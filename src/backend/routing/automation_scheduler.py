"""Automation scheduler — polls the DB for due tasks and fires them.

Inspired by claude-code's CronScheduler:
- Polls every 15s for due tasks
- Uses Redis distributed lock to prevent double-firing across instances
- Handles missed tasks on startup
- Auto-disables tasks after consecutive failure threshold
- Writes notifications to Redis for frontend polling
"""

import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

from core.infra.logging import get_logger

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 15
REDIS_LOCK_PREFIX = "jx:auto:lock:"
REDIS_LOCK_TTL = 300  # 5 minutes max lock hold

_scheduler_instance: Optional["AutomationScheduler"] = None


def get_scheduler() -> Optional["AutomationScheduler"]:
    return _scheduler_instance


class AutomationScheduler:
    """Async scheduler that polls the DB for due tasks and fires them."""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        global _scheduler_instance
        _scheduler_instance = self
        logger.info("[scheduler] started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        global _scheduler_instance
        _scheduler_instance = None
        logger.info("[scheduler] stopped")

    async def _poll_loop(self):
        # Small initial delay to let the app finish startup
        await asyncio.sleep(5)

        # Recover missed one-shot tasks on startup
        await self._recover_missed_tasks()

        while self._running:
            try:
                await self._check_and_fire()
            except Exception as e:
                logger.error("[scheduler] poll error: %s", e, exc_info=True)
            jitter = random.uniform(0, 5)
            await asyncio.sleep(POLL_INTERVAL_SECONDS + jitter)

    async def _check_and_fire(self):
        from core.db.engine import SessionLocal
        from core.services.automation_service import AutomationService

        now = datetime.utcnow().replace(tzinfo=pytz.utc)
        with SessionLocal() as db:
            svc = AutomationService(db)
            due_tasks = svc.get_due_tasks(now)

        if not due_tasks:
            return

        logger.info("[scheduler] found %d due tasks", len(due_tasks))
        for task in due_tasks:
            # Try to acquire Redis distributed lock
            acquired = await self._acquire_lock(task.task_id)
            if not acquired:
                continue
            # Fire in background
            asyncio.create_task(self.execute_task(task.task_id, task.user_id))

    async def execute_task(self, task_id: str, user_id: str):
        """Execute a single scheduled task."""
        from core.db.engine import SessionLocal
        from core.services.automation_service import AutomationService

        start = time.monotonic()

        with SessionLocal() as db:
            svc = AutomationService(db)
            task = svc.get_task_by_id(task_id)
            if not task or task.status not in ("active", "paused"):
                await self._release_lock(task_id)
                return

            run = svc.record_run_start(task_id)
            task_type = task.task_type
            task_name = task.name or "定时任务"
            task_prompt = task.prompt
            task_plan_id = task.plan_id
            task_consecutive_failures = task.consecutive_failures or 0
            task_max_failures = task.max_failures or 3
            enabled_mcp_ids = task.enabled_mcp_ids or []
            enabled_skill_ids = task.enabled_skill_ids or []
            enabled_kb_ids = task.enabled_kb_ids or []
            enabled_agent_ids = task.enabled_agent_ids or []

        try:
            if task_type == "prompt":
                chat_id, result_summary, usage = await self._execute_prompt_task(
                    user_id=user_id,
                    task_name=task_name,
                    prompt=task_prompt,
                    task_id=task_id,
                    enabled_mcp_ids=enabled_mcp_ids,
                    enabled_skill_ids=enabled_skill_ids,
                    enabled_kb_ids=enabled_kb_ids,
                )
            elif task_type == "plan":
                chat_id, result_summary, usage = await self._execute_plan_task(
                    user_id=user_id,
                    task_name=task_name,
                    plan_id=task_plan_id,
                    task_id=task_id,
                    enabled_mcp_ids=enabled_mcp_ids,
                    enabled_skill_ids=enabled_skill_ids,
                    enabled_kb_ids=enabled_kb_ids,
                    enabled_agent_ids=enabled_agent_ids,
                )
            else:
                raise ValueError(f"Unknown task type: {task_type}")

            duration_ms = int((time.monotonic() - start) * 1000)

            with SessionLocal() as db:
                svc = AutomationService(db)
                svc.record_run_complete(
                    run.run_id,
                    status="success",
                    chat_id=chat_id,
                    result_summary=result_summary,
                    duration_ms=duration_ms,
                    usage=usage,
                )
                svc.update_task_system(
                    task_id,
                    consecutive_failures=0,
                    last_run_at=datetime.utcnow(),
                )
                svc.advance_next_run(task_id)

            await self._send_notification(user_id, task_id, task_name, "success", result_summary or "执行完成", chat_id)
            logger.info("[scheduler] task %s completed in %dms", task_id, duration_ms)

        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            error_msg = str(e)[:2000]
            logger.error("[scheduler] task %s failed: %s", task_id, error_msg, exc_info=True)

            with SessionLocal() as db:
                svc = AutomationService(db)
                svc.record_run_complete(
                    run.run_id,
                    status="failed",
                    error_message=error_msg,
                    duration_ms=duration_ms,
                )
                new_failures = task_consecutive_failures + 1
                updates: Dict[str, Any] = {
                    "consecutive_failures": new_failures,
                    "last_error": error_msg,
                    "last_run_at": datetime.utcnow(),
                }
                if new_failures >= task_max_failures:
                    updates["status"] = "disabled"
                    logger.warning("[scheduler] task %s auto-disabled after %d failures", task_id, new_failures)
                svc.update_task_system(task_id, **updates)
                if new_failures < task_max_failures:
                    svc.advance_next_run(task_id)

            await self._send_notification(user_id, task_id, task_name, "failed", error_msg[:200])

        finally:
            await self._release_lock(task_id)

    async def _execute_prompt_task(
        self,
        *,
        user_id: str,
        task_name: str,
        prompt: str,
        task_id: str,
        enabled_mcp_ids: List[str],
        enabled_skill_ids: List[str],
        enabled_kb_ids: List[str],
    ) -> Tuple[str, str, Dict]:
        """Execute a prompt-type task.

        Mirrors the stream-consumption behaviour of the normal chat endpoint
        (api/routes/v1/chats.py:chat_stream) so that tool_calls, artifacts,
        citations, sources and warnings are all persisted to the chat message,
        and generated files are written into the Artifact table. Without this,
        automation chats show only bare text without any file attachments or
        tool-call history.
        """
        from core.db.engine import SessionLocal
        from core.services.chat_service import ChatService
        from routing.workflow import astream_chat_workflow
        from api.routes.v1.chats import (
            _upsert_tool_call,
            _attach_tool_result,
            _persist_artifacts,
            _extend_collected_artifacts,
        )
        from api.routes.v1.artifacts import extract_file_refs

        chat_id = f"chat_{uuid.uuid4().hex[:16]}"
        message_id = f"msg_{uuid.uuid4().hex[:16]}"

        with SessionLocal() as db:
            chat_svc = ChatService(db)
            chat_svc.ensure_session(
                chat_id=chat_id,
                user_id=user_id,
                title=f"[自动化] {task_name}",
                extra_data={"automation_task_id": task_id, "automation_run": True},
            )
            chat_svc.add_message(chat_id=chat_id, role="user", content=prompt)

        context = {
            "user_id": user_id,
            "chat_id": chat_id,
            "model_name": "qwen",
            "enable_thinking": False,
            "memory_enabled": False,
            "enabled_mcp_ids": enabled_mcp_ids,
            "enabled_skill_ids": enabled_skill_ids,
            "enabled_kb_ids": enabled_kb_ids,
        }
        session_messages = [{"role": "user", "content": prompt}]

        full_response = ""
        usage: Dict = {}
        tool_calls_log: List[Dict[str, Any]] = []
        collected_artifacts: List[Dict[str, Any]] = []
        meta_fields: Dict[str, Any] = {}

        async for chunk in astream_chat_workflow(
            session_messages=session_messages,
            user_message=prompt,
            context=context,
        ):
            chunk_type = chunk.get("type")

            if chunk_type in {"content", "ai_message"}:
                full_response += chunk.get("delta", "")

            elif chunk_type == "tool_call":
                tc: Dict[str, Any] = {
                    "tool_name": chunk.get("tool_name"),
                    "tool_display_name": chunk.get("tool_display_name"),
                    "tool_args": chunk.get("tool_args", {}),
                    "tool_id": chunk.get("tool_id"),
                }
                if chunk.get("subagent_name"):
                    tc["subagent_name"] = chunk["subagent_name"]
                _upsert_tool_call(tool_calls_log, tc)

            elif chunk_type == "tool_result":
                _tid = chunk.get("tool_id")
                _tn = chunk.get("tool_name")
                _res = chunk.get("result", {})
                _attach_tool_result(tool_calls_log, _tid, _tn, _res)
                _file_refs = extract_file_refs(_res)
                for _ref in _file_refs:
                    _ref["tool_name"] = _tn or ""
                _extend_collected_artifacts(collected_artifacts, _file_refs)

            elif chunk_type == "meta":
                usage = chunk.get("usage", {}) or {}
                meta_fields = {
                    "route": chunk.get("route", "main"),
                    "sources": chunk.get("sources", []),
                    "artifacts": chunk.get("artifacts", []),
                    "warnings": chunk.get("warnings", []),
                    "is_markdown": chunk.get("is_markdown", True),
                    "citations": chunk.get("citations", []),
                }

        # Persist assistant message with the full run context.
        with SessionLocal() as db:
            chat_svc = ChatService(db)
            chat_svc.add_message(
                chat_id=chat_id,
                role="assistant",
                content=full_response,
                message_id=message_id,
                tool_calls=tool_calls_log if tool_calls_log else None,
                usage=usage,
                extra_data={
                    "timestamp": datetime.utcnow().isoformat(),
                    "route": meta_fields.get("route", "main"),
                    "is_markdown": meta_fields.get("is_markdown", True),
                    "sources": meta_fields.get("sources", []),
                    "artifacts": meta_fields.get("artifacts", []),
                    "warnings": meta_fields.get("warnings", []),
                    "citations": meta_fields.get("citations", []),
                    "message_id": message_id,
                    "automation_task_id": task_id,
                    "automation_run": True,
                },
            )
            _persist_artifacts(db, user_id, chat_id, collected_artifacts)

        summary = full_response[:500] if full_response else "执行完成"
        return chat_id, summary, usage

    async def _execute_plan_task(
        self,
        *,
        user_id: str,
        task_name: str,
        plan_id: str,
        task_id: str,
        enabled_mcp_ids: List[str],
        enabled_skill_ids: List[str],
        enabled_kb_ids: List[str],
        enabled_agent_ids: List[str],
    ) -> Tuple[str, str, Dict]:
        """Execute a plan-type task.

        Produces a real chat session with a plan_snapshot assistant message so
        "查看对话" in the run history resolves to a loadable conversation.
        """
        from core.db.engine import SessionLocal
        from core.services.chat_service import ChatService
        from core.services.plan_service import PlanService
        from routing.subagents.plan_mode import astream_execute_plan

        chat_id = f"chat_{uuid.uuid4().hex[:16]}"

        with SessionLocal() as db:
            plan_svc = PlanService(db)
            plan = plan_svc.get_plan(plan_id, user_id)
            if not plan:
                raise ValueError(f"Plan {plan_id} not found")

            plan_title = plan.title

            if plan.status in ("completed", "failed", "cancelled"):
                plan_svc.update_plan(plan_id, status="approved", completed_steps=0)
                for step in plan.steps:
                    plan_svc.update_step(
                        step.step_id,
                        status="pending",
                        result_summary=None,
                        ai_output=None,
                        error_message=None,
                    )
            elif plan.status == "draft":
                plan_svc.update_plan(plan_id, status="approved")

            chat_svc = ChatService(db)
            chat_svc.ensure_session(
                chat_id=chat_id,
                user_id=user_id,
                title=f"[自动化] {task_name}",
                extra_data={
                    "automation_task_id": task_id,
                    "automation_run": True,
                    "plan_chat": True,
                    "plan_id": plan_id,
                },
            )
            chat_svc.add_message(
                chat_id=chat_id,
                role="user",
                content=f"自动化执行计划：{plan_title}",
            )

        result_text = ""
        usage: Dict = {}
        completed_steps = 0
        total_steps = 0
        tool_calls_log: List[Dict[str, Any]] = []
        collected_artifacts: List[Dict[str, Any]] = []

        with SessionLocal() as db:
            async for event in astream_execute_plan(
                plan_id=plan_id,
                user_id=user_id,
                db=db,
                enabled_mcp_ids=enabled_mcp_ids,
                enabled_skill_ids=enabled_skill_ids,
                enabled_kb_ids=enabled_kb_ids,
                enabled_agent_ids=enabled_agent_ids,
            ):
                evt_type = event.get("type")
                if evt_type == "plan_complete":
                    result_text = event.get("result_text", "")
                    completed_steps = event.get("completed_steps", 0)
                    total_steps = event.get("total_steps", 0)
                    usage = event.get("usage", {}) or {}
                elif evt_type == "tool_call":
                    tool_calls_log.append({
                        "tool_name": event.get("tool_name"),
                        "tool_id": event.get("tool_id"),
                        "tool_args": event.get("tool_args", {}),
                        "step_id": event.get("step_id"),
                    })
                elif evt_type == "tool_result":
                    _tid = event.get("tool_id")
                    _tn = event.get("tool_name")
                    result = event.get("result")
                    matched = False
                    for _tc in tool_calls_log:
                        if _tid and _tc.get("tool_id") == _tid and "result" not in _tc:
                            _tc["result"] = result
                            _tc["status"] = "success"
                            matched = True
                            break
                    if not matched:
                        tool_calls_log.append({
                            "tool_name": _tn,
                            "tool_id": _tid,
                            "result": result,
                            "status": "success",
                            "step_id": event.get("step_id"),
                        })
                    if isinstance(result, dict) and result.get("file_id") and result.get("ok"):
                        collected_artifacts.append(result)

        with SessionLocal() as db:
            plan_svc = PlanService(db)
            updated_plan = plan_svc.get_plan(plan_id, user_id)
            plan_snapshot: Optional[Dict[str, Any]] = None
            if updated_plan:
                plan_snapshot = PlanService.build_execution_snapshot(
                    updated_plan,
                    completed_steps=completed_steps,
                    total_steps=total_steps,
                    result_text=result_text,
                )

            artifacts_meta: List[Dict[str, Any]] = [
                {
                    "file_id": a.get("file_id", ""),
                    "name": a.get("name", ""),
                    "mime_type": a.get("mime_type", "application/octet-stream"),
                    "size": a.get("size", 0),
                    "url": a.get("url", ""),
                }
                for a in collected_artifacts
            ]

            assistant_content = result_text or (
                f"计划执行完成：共 {total_steps} 步，完成 {completed_steps} 步。"
            )
            chat_svc = ChatService(db)
            chat_svc.add_message(
                chat_id=chat_id,
                role="assistant",
                content=assistant_content,
                model="qwen",
                extra_data={
                    "is_markdown": bool(result_text),
                    "plan_id": plan_id,
                    "plan_snapshot": plan_snapshot,
                    "artifacts": artifacts_meta,
                    "completed_steps": completed_steps,
                    "total_steps": total_steps,
                    "automation_task_id": task_id,
                    "automation_run": True,
                },
                tool_calls=tool_calls_log if tool_calls_log else None,
                usage=usage,
            )

            # Register generated file artifacts into Artifact table (best-effort)
            if collected_artifacts:
                try:
                    from core.db.models import Artifact as ArtifactModel
                    all_fids = [a["file_id"] for a in collected_artifacts if a.get("file_id")]
                    existing_ids: set = set()
                    if all_fids:
                        existing_ids = set(
                            r[0] for r in db.query(ArtifactModel.artifact_id)
                            .filter(ArtifactModel.artifact_id.in_(all_fids)).all()
                        )
                    for a in collected_artifacts:
                        fid = a.get("file_id")
                        if not fid or fid in existing_ids:
                            continue
                        db.add(ArtifactModel(
                            artifact_id=fid,
                            chat_id=chat_id,
                            user_id=user_id,
                            type=a.get("type", "file"),
                            title=a.get("name", ""),
                            filename=a.get("name", ""),
                            size_bytes=a.get("size", 0),
                            mime_type=a.get("mime_type", "application/octet-stream"),
                            storage_key=a.get("storage_key", ""),
                            storage_url=a.get("url", ""),
                            extra_data={"source": "ai_generated", "plan_id": plan_id},
                        ))
                    db.commit()
                except Exception as _art_exc:
                    logger.warning("[plan-automation] failed to persist artifacts: %s", _art_exc)
                    db.rollback()

        summary = (result_text or assistant_content)[:500]
        return chat_id, summary, usage

    async def _recover_missed_tasks(self):
        """On startup, check for one-shot tasks whose next_run_at is in the past."""
        from core.db.engine import SessionLocal
        from core.services.automation_service import AutomationService

        now = datetime.utcnow().replace(tzinfo=pytz.utc)
        try:
            with SessionLocal() as db:
                svc = AutomationService(db)
                missed = svc.get_due_tasks(now)
                one_shot_count = 0
                for task in missed:
                    if not task.recurring:
                        one_shot_count += 1
                        asyncio.create_task(self.execute_task(task.task_id, task.user_id))
                if one_shot_count:
                    logger.info("[scheduler] recovering %d missed one-shot tasks", one_shot_count)
        except Exception as e:
            logger.error("[scheduler] recovery error: %s", e)

    # ── Redis lock helpers ─────────────────────────────────────────

    async def _acquire_lock(self, task_id: str) -> bool:
        try:
            from core.infra.redis import get_redis
            redis = get_redis()
            key = f"{REDIS_LOCK_PREFIX}{task_id}"
            result = await redis.set(key, "1", ex=REDIS_LOCK_TTL, nx=True)
            return bool(result)
        except Exception as e:
            logger.warning("[scheduler] lock acquire failed for %s: %s", task_id, e)
            return False

    async def _release_lock(self, task_id: str):
        try:
            from core.infra.redis import get_redis
            redis = get_redis()
            await redis.delete(f"{REDIS_LOCK_PREFIX}{task_id}")
        except Exception as e:
            logger.warning("[scheduler] lock release failed for %s: %s", task_id, e)

    # ── Notification ───────────────────────────────────────────────

    async def _send_notification(
        self,
        user_id: str,
        task_id: str,
        task_name: str,
        status: str,
        summary: str,
        chat_id: Optional[str] = None,
    ):
        try:
            from core.infra.redis import get_redis
            redis = get_redis()
            notification = {
                "id": f"notif_{uuid.uuid4().hex[:12]}",
                "task_id": task_id,
                "task_name": task_name,
                "status": status,
                "summary": summary[:200],
                "chat_id": chat_id,
                "timestamp": int(datetime.utcnow().timestamp() * 1000),
                "read": False,
            }
            key = f"jx:notifications:{user_id}"
            await redis.lpush(key, json.dumps(notification, ensure_ascii=False))
            await redis.ltrim(key, 0, 49)  # Keep latest 50
            await redis.expire(key, 7 * 24 * 3600)  # 7 day TTL
        except Exception as e:
            logger.warning("[scheduler] notification failed: %s", e)
