"""Plan mode API routes — generate and execute structured plans."""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth.backend import get_current_user, UserContext
from core.db.engine import get_db
from core.infra.responses import success_response, created_response
from core.services.plan_service import PlanService
from core.services.chat_service import ChatService
from core.infra.logging import get_logger
from api.routes.v1.artifacts import infer_artifact_type, resolve_artifact_storage_key

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/plans", tags=["Plans"])


# ── Request Schemas ────────────────────────────────────────────

class HistoryMessage(BaseModel):
    role: str
    content: str


class PlanAttachment(BaseModel):
    name: str
    content: str = ""
    mime_type: str = ""
    file_id: str = ""
    download_url: str = ""


class GeneratePlanRequest(BaseModel):
    task_description: str = Field(..., min_length=1, max_length=5000)
    model_name: str = "qwen"
    enabled_mcp_ids: Optional[List[str]] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_kb_ids: Optional[List[str]] = None
    enabled_agent_ids: Optional[List[str]] = None
    chat_id: Optional[str] = None
    history_messages: Optional[List[HistoryMessage]] = None
    attachments: Optional[List[PlanAttachment]] = None


class UpdatePlanRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None


# ── SSE Helpers ────────────────────────────────────────────────

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _ensure_plan_session(db: Session, chat_id: Optional[str], user_id: str) -> Optional[str]:
    """Ensure a chat session exists for plan mode messages.

    Returns the chat_id if session was created/found, None otherwise.
    """
    if not chat_id:
        return None
    try:
        svc = ChatService(db)
        svc.ensure_session(
            chat_id=chat_id,
            user_id=user_id,
            title="计划模式",
            extra_data={"plan_chat": True},
        )
        return chat_id
    except Exception as exc:
        logger.warning("Failed to ensure plan session: %s", exc)
        return None


def _save_plan_message(
    db: Session, chat_id: Optional[str], role: str, content: str,
    model: Optional[str] = None, extra_data: Optional[Dict] = None,
    tool_calls: Optional[List[Dict]] = None,
    usage: Optional[Dict] = None,
) -> None:
    """Save a message to the chat session for plan mode persistence."""
    if not chat_id or not content:
        return
    try:
        svc = ChatService(db)
        svc.add_message(
            chat_id=chat_id, role=role, content=content,
            model=model, extra_data=extra_data,
            tool_calls=tool_calls,
            usage=usage,
        )
    except Exception as exc:
        logger.warning("Failed to save plan message: %s", exc)


def _load_chat_history(
    db: Session,
    chat_id: Optional[str],
    user_id: str,
    history_messages: Optional[List[HistoryMessage]] = None,
) -> List[Dict[str, Any]]:
    """Load chat history as [{"role": ..., "content": ...}] dicts.

    Priority: DB lookup by chat_id > frontend-provided history_messages.
    Now that plan mode persists messages to DB, DB is the primary source.
    """
    # 1. Try DB first
    if chat_id:
        try:
            svc = ChatService(db)
            messages = svc.list_all_messages(chat_id, user_id)
            if messages:
                return [{"role": msg.role, "content": msg.content} for msg in messages]
        except Exception as exc:
            logger.warning("Failed to load chat history from DB: %s", exc)

    # 2. Fallback: frontend-provided history
    if history_messages:
        return [{"role": m.role, "content": m.content} for m in history_messages if m.content]

    return []


# ── Endpoints ──────────────────────────────────────────────────

@router.post("/generate")
async def generate_plan(
    req: GeneratePlanRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream — generate a structured plan from a task description."""
    from routing.subagents.plan_mode import astream_generate_plan

    # Ensure chat session exists and save user message
    db_chat_id = _ensure_plan_session(db, req.chat_id, user.user_id)
    _save_plan_message(db, db_chat_id, "user", req.task_description, model=req.model_name)

    session_messages = _load_chat_history(db, req.chat_id, user.user_id, req.history_messages)
    logger.warning("[plan-generate] chat_id=%s, loaded %d history messages",
                   req.chat_id, len(session_messages))

    async def _gen():
        plan_title = ""
        plan_desc = ""
        try:
            # Convert attachments to dicts for plan_mode
            uploaded_files = None
            if req.attachments:
                uploaded_files = [a.model_dump() for a in req.attachments if a.content]

            async for event in astream_generate_plan(
                task_description=req.task_description,
                user_id=user.user_id,
                db=db,
                model_name=req.model_name,
                enabled_mcp_ids=req.enabled_mcp_ids,
                enabled_skill_ids=req.enabled_skill_ids,
                enabled_kb_ids=req.enabled_kb_ids,
                enabled_agent_ids=req.enabled_agent_ids,
                session_messages=session_messages,
                uploaded_files=uploaded_files,
            ):
                # Capture plan info for DB persistence
                if event.get("type") == "plan_generated":
                    plan_title = event.get("title", "")
                    plan_desc = event.get("description", "")
                    steps = event.get("steps", [])
                    step_summary = "\n".join(
                        f"{i+1}. {s.get('title', '')}" for i, s in enumerate(steps)
                    )
                    assistant_content = (
                        f"已生成执行计划：**{plan_title}**\n\n"
                        f"{plan_desc}\n\n"
                        f"**执行步骤：**\n{step_summary}"
                    )
                    plan_snapshot = {
                        "mode": "preview",
                        "title": plan_title,
                        "description": plan_desc,
                        "steps": [
                            {
                                "step_order": s.get("step_order", i + 1),
                                "title": s.get("title", ""),
                                "description": s.get("description"),
                                "expected_tools": s.get("expected_tools", []),
                                "expected_skills": s.get("expected_skills", []),
                            }
                            for i, s in enumerate(steps)
                        ],
                        "total_steps": len(steps),
                        "completed_steps": 0,
                    }
                    gen_usage = event.get("usage") or None
                    _save_plan_message(
                        db, db_chat_id, "assistant", assistant_content,
                        model=req.model_name,
                        extra_data={
                            "is_markdown": True,
                            "plan_id": event.get("plan_id"),
                            "plan_snapshot": plan_snapshot,
                        },
                        usage=gen_usage,
                    )
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("generate_plan SSE error")
            yield f"data: {json.dumps({'type': 'plan_error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("")
async def list_plans(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
):
    """List all plans for the current user."""
    svc = PlanService(db)
    plans = svc.list_plans(user.user_id, limit=limit, offset=offset)
    return success_response(data=[PlanService.plan_to_dict(p) for p in plans])


@router.get("/{plan_id}")
async def get_plan(
    plan_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get plan details including steps."""
    svc = PlanService(db)
    plan = svc.get_plan(plan_id, user.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    return success_response(data=PlanService.plan_to_dict(plan))


@router.patch("/{plan_id}")
async def update_plan(
    plan_id: str,
    req: UpdatePlanRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update plan (edit title, approve, replace steps)."""
    svc = PlanService(db)
    plan = svc.get_plan(plan_id, user.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")

    # Replace steps if provided
    if req.steps is not None:
        svc.replace_steps(plan_id, req.steps)

    # Update scalar fields
    updates = {}
    if req.status is not None:
        valid = {"approved", "cancelled"}
        if req.status not in valid:
            raise HTTPException(status_code=400, detail=f"只能设置状态为: {', '.join(valid)}")
        updates["status"] = req.status
    if req.title is not None:
        updates["title"] = req.title

    if updates:
        svc.update_plan(plan_id, **updates)

    plan = svc.get_plan(plan_id, user.user_id)
    return success_response(data=PlanService.plan_to_dict(plan))


@router.delete("/{plan_id}")
async def delete_plan(
    plan_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a plan and its steps."""
    svc = PlanService(db)
    deleted = svc.delete_plan(plan_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="计划不存在")
    return success_response(message="已删除")


class ExecutePlanRequest(BaseModel):
    enabled_mcp_ids: Optional[List[str]] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_kb_ids: Optional[List[str]] = None
    enabled_agent_ids: Optional[List[str]] = None
    chat_id: Optional[str] = None
    history_messages: Optional[List[HistoryMessage]] = None


@router.post("/{plan_id}/execute")
async def execute_plan(
    plan_id: str,
    req: ExecutePlanRequest = ExecutePlanRequest(),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream — execute an approved plan step by step."""
    from routing.subagents.plan_mode import astream_execute_plan

    svc = PlanService(db)
    plan = svc.get_plan(plan_id, user.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    if plan.status != "approved":
        raise HTTPException(status_code=400, detail=f"计划状态为 '{plan.status}'，需要先审批")

    # Ensure chat session and save "确认执行" user message
    db_chat_id = _ensure_plan_session(db, req.chat_id, user.user_id)
    _save_plan_message(db, db_chat_id, "user", "确认执行", model="qwen")

    session_messages = _load_chat_history(db, req.chat_id, user.user_id, req.history_messages)
    logger.warning("[plan-execute] chat_id=%s, loaded %d history messages",
                   req.chat_id, len(session_messages))

    from core.infra.logging import LogContext

    async def _gen():
        _log_ctx = LogContext(user_id=user.user_id or None, chat_id=db_chat_id or None)
        _log_ctx.__enter__()
        try:
            result_text = ""
            completed_steps = 0
            total_steps = 0
            exec_usage: Optional[Dict[str, Any]] = None
            collected_artifacts: List[Dict[str, Any]] = []
            tool_calls_log: List[Dict[str, Any]] = []
            async for event in astream_execute_plan(
                plan_id=plan_id,
                user_id=user.user_id,
                db=db,
                enabled_mcp_ids=req.enabled_mcp_ids,
                enabled_skill_ids=req.enabled_skill_ids,
                enabled_kb_ids=req.enabled_kb_ids,
                enabled_agent_ids=req.enabled_agent_ids,
                session_messages=session_messages,
            ):
                evt_type = event.get("type")
                # Capture execution result for DB persistence
                if evt_type == "plan_complete":
                    result_text = event.get("result_text", "")
                    completed_steps = event.get("completed_steps", 0)
                    total_steps = event.get("total_steps", 0)
                    exec_usage = event.get("usage") or None
                elif evt_type == "tool_call":
                    tool_calls_log.append({
                        "tool_name": event.get("tool_name"),
                        "tool_id": event.get("tool_id"),
                        "tool_args": event.get("tool_args", {}),
                        "step_id": event.get("step_id"),
                    })
                # Collect file artifacts from tool results
                elif evt_type == "tool_result":
                    result = event.get("result")
                    # Match result back to its tool_call entry
                    _tid = event.get("tool_id")
                    _tn = event.get("tool_name")
                    matched = False
                    for _tc in tool_calls_log:
                        if _tid and _tc.get("tool_id") == _tid and "result" not in _tc:
                            _tc["result"] = result
                            _tc["status"] = "success"
                            matched = True
                            break
                    if not matched:
                        tool_calls_log.append({
                            "tool_name": _tn, "tool_id": _tid,
                            "result": result, "status": "success",
                            "step_id": event.get("step_id"),
                        })
                    if isinstance(result, dict) and result.get("file_id") and result.get("ok"):
                        collected_artifacts.append(result)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # Build plan snapshot for history reconstruction
            plan_snapshot = None
            try:
                updated_plan = svc.get_plan(plan_id, user.user_id)
                if updated_plan:
                    plan_snapshot = PlanService.build_execution_snapshot(
                        updated_plan,
                        completed_steps=completed_steps,
                        total_steps=total_steps,
                        result_text=result_text,
                    )
            except Exception as _snap_exc:
                logger.warning("Failed to build plan snapshot: %s", _snap_exc)

            # Build artifacts list for extra_data (matches chats.py format)
            artifacts_meta: List[Dict[str, Any]] = []
            for _art in collected_artifacts:
                artifacts_meta.append({
                    "file_id": _art.get("file_id", ""),
                    "name": _art.get("name", ""),
                    "mime_type": _art.get("mime_type", "application/octet-stream"),
                    "size": _art.get("size", 0),
                    "url": _art.get("url", ""),
                })

            # Save execution result as assistant message
            if result_text:
                _save_plan_message(
                    db, db_chat_id, "assistant", result_text,
                    model="qwen",
                    extra_data={
                        "is_markdown": True,
                        "plan_id": plan_id,
                        "completed_steps": completed_steps,
                        "total_steps": total_steps,
                        "plan_snapshot": plan_snapshot,
                        "artifacts": artifacts_meta,
                    },
                    tool_calls=tool_calls_log if tool_calls_log else None,
                    usage=exec_usage,
                )
            else:
                summary = f"计划执行完成：共 {total_steps} 步，完成 {completed_steps} 步。"
                _save_plan_message(
                    db, db_chat_id, "assistant", summary,
                    model="qwen",
                    extra_data={
                        "is_markdown": False,
                        "plan_id": plan_id,
                        "plan_snapshot": plan_snapshot,
                        "artifacts": artifacts_meta,
                    },
                    tool_calls=tool_calls_log if tool_calls_log else None,
                    usage=exec_usage,
                )
            # Register generated file artifacts into Artifact table
            if collected_artifacts and db_chat_id:
                from core.db.models import Artifact as ArtifactModel
                all_fids = [a["file_id"] for a in collected_artifacts if a.get("file_id")]
                existing_ids: set = set()
                if all_fids:
                    existing_ids = set(
                        r[0] for r in db.query(ArtifactModel.artifact_id)
                        .filter(ArtifactModel.artifact_id.in_(all_fids)).all()
                    )
                for _art in collected_artifacts:
                    _art_id = _art.get("file_id", "")
                    if not _art_id or _art_id in existing_ids:
                        continue
                    _mime = _art.get("mime_type", "application/octet-stream")
                    try:
                        _storage_key = resolve_artifact_storage_key(_art_id, _art.get("storage_key")) or f"artifacts/{_art_id}"
                        db.add(ArtifactModel(
                            artifact_id=_art_id,
                            chat_id=db_chat_id,
                            user_id=user.user_id,
                            type=infer_artifact_type(_mime),
                            title=_art.get("name", ""),
                            filename=_art.get("name", ""),
                            size_bytes=max(_art.get("size", 0) or 0, 1),
                            mime_type=_mime,
                            storage_key=_storage_key,
                            storage_url=_art.get("url", ""),
                            extra_data={"source": "ai_generated", "plan_id": plan_id},
                        ))
                        existing_ids.add(_art_id)
                    except Exception as _ae:
                        logger.warning("plan artifact_db_insert_failed: %s", _ae)
                try:
                    db.commit()
                    logger.info("plan_artifacts: registered %d files for plan %s", len(collected_artifacts), plan_id)
                except Exception as _ce:
                    logger.warning("plan artifact_db_commit_failed: %s", _ce)
                    db.rollback()

        except Exception as exc:
            logger.exception("execute_plan SSE error")
            yield f"data: {json.dumps({'type': 'plan_error', 'plan_id': plan_id, 'error': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            try:
                _log_ctx.__exit__(None, None, None)
            except Exception:
                pass
        yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{plan_id}/cancel")
async def cancel_plan(
    plan_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a running plan."""
    svc = PlanService(db)
    plan = svc.get_plan(plan_id, user.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="计划不存在")
    if plan.status not in ("running", "approved", "draft"):
        raise HTTPException(status_code=400, detail=f"计划状态为 '{plan.status}'，无法取消")

    svc.update_plan(plan_id, status="cancelled")
    return success_response(message="已取消")
