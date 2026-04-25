"""Observability log writer — async, best-effort writer for the three
tool/sub-agent/skill log tables.

Writes are fire-and-forget so the SSE hot path never blocks on DB I/O.
Failures downgrade to structured logs instead of propagating.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy.orm import Session

from core.db.engine import SessionLocal
from core.db.models import SkillCallLog, SubAgentCallLog, ToolCallLog, UserShadow
from core.infra.data_masking import mask_sensitive_data
from core.infra.logging import chat_id_var, get_logger, trace_id_var, user_id_var

logger = get_logger(__name__)

_current_source: ContextVar[str] = ContextVar("log_current_source", default="main_agent")
_current_subagent_log_id: ContextVar[Optional[str]] = ContextVar("log_current_subagent", default=None)
_current_message_id: ContextVar[str] = ContextVar("log_current_message", default="")

TOOL_LOG_ENABLED = os.getenv("TOOL_CALL_LOG_ENABLED", "true").lower() == "true"
SUBAGENT_LOG_ENABLED = os.getenv("SUBAGENT_LOG_ENABLED", "true").lower() == "true"
SKILL_LOG_ENABLED = os.getenv("SKILL_LOG_ENABLED", "true").lower() == "true"

MAX_RESULT_BYTES = int(os.getenv("LOG_MAX_RESULT_BYTES", 64 * 1024))
MAX_STDOUT_BYTES = int(os.getenv("LOG_MAX_STDOUT_BYTES", 64 * 1024))

_REDACT_FIELDS = [
    s.strip() for s in os.getenv(
        "LOG_REDACT_FIELDS",
        "password,token,api_key,apikey,authorization,secret,access_key",
    ).split(",") if s.strip()
]

# Retain pending tasks so the event loop can't GC them mid-flight when the
# originating SSE request ends.
_pending_write_tasks: "set[asyncio.Task]" = set()


def _new_id() -> str:
    return uuid.uuid4().hex


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _truncate_text(text: Optional[str], limit: int) -> tuple[Optional[str], bool]:
    if text is None:
        return None, False
    if not isinstance(text, str):
        text = str(text)
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore") + "\n…[truncated]", True


def _truncate_json(payload: Any, limit: int) -> tuple[Any, bool]:
    if payload is None:
        return None, False
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8", errors="ignore")
    except Exception:
        return {"_repr": str(payload)[:limit]}, True
    if len(encoded) <= limit:
        return payload, False
    preview = encoded[: limit // 2].decode("utf-8", errors="ignore")
    return {"_truncated": True, "_preview": preview}, True


def _context_ids() -> Dict[str, str]:
    return {
        "trace_id": trace_id_var.get() or "",
        "user_id": user_id_var.get() or "",
        "chat_id": chat_id_var.get() or "",
    }


def _fetch_username(db: Session, user_id: str) -> Optional[str]:
    if not user_id:
        return None
    try:
        row = db.query(UserShadow.username).filter(UserShadow.user_id == user_id).one_or_none()
        return row.username if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("username_lookup_failed", user_id=user_id, error=str(exc))
        return None


@contextmanager
def subagent_scope(subagent_log_id: Optional[str], source: str = "subagent") -> Iterator[None]:
    tok_id = _current_subagent_log_id.set(subagent_log_id)
    tok_src = _current_source.set(source)
    try:
        yield
    finally:
        _current_subagent_log_id.reset(tok_id)
        _current_source.reset(tok_src)


@contextmanager
def skill_scope(source: str = "skill") -> Iterator[None]:
    tok = _current_source.set(source)
    try:
        yield
    finally:
        _current_source.reset(tok)


def set_current_message_id(message_id: str) -> None:
    _current_message_id.set(message_id or "")


def current_subagent_log_id() -> Optional[str]:
    return _current_subagent_log_id.get()


def current_source() -> str:
    return _current_source.get() or "main_agent"


def _write_tool_call_sync(record: Dict[str, Any]) -> None:
    if not TOOL_LOG_ENABLED:
        return
    db: Session = SessionLocal()
    try:
        args_safe = mask_sensitive_data(record.get("tool_args"), field_patterns=_REDACT_FIELDS)
        args_safe, _ = _truncate_json(args_safe, MAX_RESULT_BYTES)
        result_safe, result_trunc = _truncate_json(record.get("tool_result"), MAX_RESULT_BYTES)
        username = _fetch_username(db, record.get("user_id", ""))
        row = ToolCallLog(
            id=record.get("id") or _new_id(),
            trace_id=record.get("trace_id"),
            chat_id=record.get("chat_id"),
            message_id=record.get("message_id"),
            user_id=record.get("user_id"),
            user_name=username,
            tool_name=record["tool_name"],
            tool_display_name=record.get("tool_display_name"),
            tool_call_id=record.get("tool_call_id"),
            mcp_server=record.get("mcp_server"),
            tool_args=args_safe,
            tool_result=result_safe,
            result_truncated=bool(result_trunc),
            status=record.get("status") or "success",
            error_message=record.get("error_message"),
            duration_ms=record.get("duration_ms"),
            source=record.get("source") or "main_agent",
            subagent_log_id=record.get("subagent_log_id"),
            skill_log_id=record.get("skill_log_id"),
            started_at=record.get("started_at"),
            created_at=record.get("created_at") or now_utc(),
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("tool_call_log_write_failed", error=str(exc), tool=record.get("tool_name"))
    finally:
        db.close()


def _write_subagent_create_sync(record: Dict[str, Any]) -> None:
    if not SUBAGENT_LOG_ENABLED:
        return
    db: Session = SessionLocal()
    try:
        input_safe, _ = _truncate_json(record.get("input_messages"), MAX_RESULT_BYTES)
        username = _fetch_username(db, record.get("user_id", ""))
        row = SubAgentCallLog(
            id=record["id"],
            trace_id=record.get("trace_id"),
            chat_id=record.get("chat_id"),
            message_id=record.get("message_id"),
            user_id=record.get("user_id"),
            user_name=username,
            subagent_id=record.get("subagent_id"),
            subagent_name=record["subagent_name"],
            subagent_type=record.get("subagent_type"),
            plan_id=record.get("plan_id"),
            step_id=record.get("step_id"),
            step_index=record.get("step_index"),
            step_title=record.get("step_title"),
            model=record.get("model"),
            input_messages=input_safe,
            status="running",
            parent_subagent_log_id=record.get("parent_subagent_log_id"),
            started_at=record.get("started_at") or now_utc(),
            created_at=now_utc(),
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("subagent_log_create_failed", error=str(exc), name=record.get("subagent_name"))
    finally:
        db.close()


def _write_subagent_finish_sync(
    subagent_log_id: str,
    status: str,
    updates: Dict[str, Any],
) -> None:
    if not SUBAGENT_LOG_ENABLED:
        return
    db: Session = SessionLocal()
    try:
        row = db.query(SubAgentCallLog).filter(SubAgentCallLog.id == subagent_log_id).one_or_none()
        if row is None:
            return
        row.status = status
        if "output_content" in updates:
            output, _ = _truncate_text(updates.get("output_content"), MAX_RESULT_BYTES)
            row.output_content = output
        if "intermediate_steps" in updates:
            steps_safe, _ = _truncate_json(updates.get("intermediate_steps"), MAX_RESULT_BYTES)
            row.intermediate_steps = steps_safe
        if "token_usage" in updates:
            row.token_usage = updates.get("token_usage")
        if "tool_calls_count" in updates:
            row.tool_calls_count = updates.get("tool_calls_count")
        if "skill_calls_count" in updates:
            row.skill_calls_count = updates.get("skill_calls_count")
        if "error_message" in updates:
            row.error_message = updates.get("error_message")
        if "duration_ms" in updates:
            row.duration_ms = updates.get("duration_ms")
        row.completed_at = now_utc()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("subagent_log_finish_failed", error=str(exc), id=subagent_log_id)
    finally:
        db.close()


def _write_skill_call_sync(record: Dict[str, Any]) -> None:
    if not SKILL_LOG_ENABLED:
        return
    db: Session = SessionLocal()
    try:
        args_safe = mask_sensitive_data(record.get("script_args"), field_patterns=_REDACT_FIELDS)
        args_safe, _ = _truncate_json(args_safe, MAX_RESULT_BYTES)
        stdout_safe, trunc_out = _truncate_text(record.get("script_stdout"), MAX_STDOUT_BYTES)
        stderr_safe, trunc_err = _truncate_text(record.get("script_stderr"), MAX_STDOUT_BYTES)
        stdin_safe, _ = _truncate_text(record.get("script_stdin"), MAX_STDOUT_BYTES)
        username = _fetch_username(db, record.get("user_id", ""))
        row = SkillCallLog(
            id=record.get("id") or _new_id(),
            trace_id=record.get("trace_id"),
            chat_id=record.get("chat_id"),
            message_id=record.get("message_id"),
            user_id=record.get("user_id"),
            user_name=username,
            skill_id=record["skill_id"],
            skill_name=record.get("skill_name"),
            skill_version=record.get("skill_version"),
            skill_source=record.get("skill_source"),
            invocation_type=record.get("invocation_type") or "auto_load",
            script_name=record.get("script_name"),
            script_language=record.get("script_language"),
            script_args=args_safe,
            script_stdin=stdin_safe,
            script_stdout=stdout_safe,
            script_stderr=stderr_safe,
            output_truncated=bool(trunc_out or trunc_err),
            exit_code=record.get("exit_code"),
            status=record.get("status") or "success",
            error_message=record.get("error_message"),
            duration_ms=record.get("duration_ms"),
            source=record.get("source") or current_source(),
            subagent_log_id=record.get("subagent_log_id") or current_subagent_log_id(),
            started_at=record.get("started_at"),
            created_at=record.get("created_at") or now_utc(),
        )
        db.add(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("skill_call_log_write_failed", error=str(exc), skill=record.get("skill_id"))
    finally:
        db.close()


async def write_tool_call(record: Dict[str, Any]) -> None:
    if not TOOL_LOG_ENABLED:
        return
    record.setdefault("source", current_source())
    record.setdefault("subagent_log_id", current_subagent_log_id())
    ctx = _context_ids()
    record.setdefault("trace_id", ctx["trace_id"])
    record.setdefault("user_id", ctx["user_id"])
    record.setdefault("chat_id", ctx["chat_id"])
    record.setdefault("message_id", _current_message_id.get())
    try:
        await asyncio.to_thread(_write_tool_call_sync, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_call_log_task_failed", error=str(exc))


async def start_subagent_log(record: Dict[str, Any]) -> str:
    log_id = record.get("id") or _new_id()
    record["id"] = log_id
    if not SUBAGENT_LOG_ENABLED:
        return log_id
    ctx = _context_ids()
    record.setdefault("trace_id", ctx["trace_id"])
    record.setdefault("user_id", ctx["user_id"])
    record.setdefault("chat_id", ctx["chat_id"])
    record.setdefault("message_id", _current_message_id.get())
    record.setdefault("parent_subagent_log_id", current_subagent_log_id())
    record.setdefault("started_at", now_utc())
    try:
        await asyncio.to_thread(_write_subagent_create_sync, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subagent_log_start_failed", error=str(exc))
    return log_id


async def finish_subagent_log(
    subagent_log_id: str,
    *,
    status: str = "success",
    output_content: Optional[str] = None,
    intermediate_steps: Optional[List[Dict[str, Any]]] = None,
    token_usage: Optional[Dict[str, int]] = None,
    tool_calls_count: int = 0,
    skill_calls_count: int = 0,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    if not SUBAGENT_LOG_ENABLED or not subagent_log_id:
        return
    updates = {
        "output_content": output_content,
        "intermediate_steps": intermediate_steps,
        "token_usage": token_usage,
        "tool_calls_count": tool_calls_count,
        "skill_calls_count": skill_calls_count,
        "error_message": error_message,
        "duration_ms": duration_ms,
    }
    try:
        await asyncio.to_thread(_write_subagent_finish_sync, subagent_log_id, status, updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("subagent_log_finish_failed", error=str(exc))


async def write_skill_call(record: Dict[str, Any]) -> str:
    if not SKILL_LOG_ENABLED:
        return ""
    log_id = record.get("id") or _new_id()
    record["id"] = log_id
    ctx = _context_ids()
    record.setdefault("trace_id", ctx["trace_id"])
    record.setdefault("user_id", ctx["user_id"])
    record.setdefault("chat_id", ctx["chat_id"])
    record.setdefault("message_id", _current_message_id.get())
    try:
        await asyncio.to_thread(_write_skill_call_sync, record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_call_log_task_failed", error=str(exc))
    return log_id


def _fire_and_forget(coro) -> None:
    # Keep a reference until the task completes so the event loop can't GC
    # it mid-flight when the originating request ends.
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        logger.debug("log_writer: no running loop, dropping write")
        coro.close()
        return
    _pending_write_tasks.add(task)
    task.add_done_callback(_pending_write_tasks.discard)


def schedule_tool_call_write(record: Dict[str, Any]) -> None:
    _fire_and_forget(write_tool_call(record))


def schedule_skill_call_write(record: Dict[str, Any]) -> None:
    _fire_and_forget(write_skill_call(record))
