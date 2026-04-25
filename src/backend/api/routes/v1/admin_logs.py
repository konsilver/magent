"""Admin observability logs API (tool / sub-agent / skill call logs + trace view)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.db.models import (
    ChatSession,
    SkillCallLog,
    SubAgentCallLog,
    ToolCallLog,
)
from core.infra.responses import paginated_response, success_response

router = APIRouter(prefix="/v1/admin/logs", tags=["Admin Observability Logs"])
logger = logging.getLogger(__name__)


_DATETIME_FIELDS = ("started_at", "completed_at", "created_at")


def _row_to_dict(row: Any, *, session_title: Optional[str] = None) -> Dict[str, Any]:
    """Convert a SQLAlchemy row to a JSON-serializable dict.  Datetime
    columns are rendered via ``isoformat``; an optional ``session_title``
    is injected (when present) as an extra field."""
    data: Dict[str, Any] = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if col.name in _DATETIME_FIELDS and val is not None:
            val = val.isoformat()
        data[col.name] = val
    if session_title is not None or "chat_id" in data:
        data["session_title"] = session_title
    return data


def _session_title_map(db: Session, chat_ids: List[str]) -> Dict[str, str]:
    if not chat_ids:
        return {}
    rows = (
        db.query(ChatSession.chat_id, ChatSession.title)
        .filter(ChatSession.chat_id.in_(list(set(chat_ids))))
        .all()
    )
    return {r.chat_id: r.title for r in rows}


def _apply_filters(query, mapping: Dict[Any, Any]):
    """Apply each (column → value) pair as an equality filter, skipping
    ``None`` entries.  Replaces a long chain of ``if x: query = query.filter(...)``."""
    for col, value in mapping.items():
        if value is None:
            continue
        query = query.filter(col == value)
    return query


def _apply_date_range(query, col, date_from, date_to):
    if date_from:
        query = query.filter(col >= date_from)
    if date_to:
        query = query.filter(col <= date_to)
    return query


def _paginate(query, page: int, page_size: int, order_col):
    total = query.count()
    rows = (
        query.order_by(desc(order_col))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def _serialize(row, titles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    title = (titles or {}).get(getattr(row, "chat_id", None)) if titles is not None else None
    return _row_to_dict(row, session_title=title)


@router.get("/tools", dependencies=[Depends(require_config)])
def list_tool_logs(
    user_id: Optional[str] = Query(None),
    chat_id: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    subagent_log_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = _apply_filters(db.query(ToolCallLog), {
        ToolCallLog.user_id: user_id,
        ToolCallLog.chat_id: chat_id,
        ToolCallLog.tool_name: tool_name,
        ToolCallLog.status: status,
        ToolCallLog.source: source,
        ToolCallLog.trace_id: trace_id,
        ToolCallLog.subagent_log_id: subagent_log_id,
    })
    query = _apply_date_range(query, ToolCallLog.created_at, date_from, date_to)
    rows, total = _paginate(query, page, page_size, ToolCallLog.created_at)
    titles = _session_title_map(db, [r.chat_id for r in rows if r.chat_id])
    items = [_serialize(r, titles) for r in rows]
    return paginated_response(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/tools/filters", dependencies=[Depends(require_config)])
def tool_log_filters(db: Session = Depends(get_db)):
    tool_rows = (
        db.query(ToolCallLog.tool_name)
        .filter(ToolCallLog.tool_name.isnot(None))
        .distinct()
        .order_by(ToolCallLog.tool_name)
        .all()
    )
    return success_response(data={
        "tool_names": [r.tool_name for r in tool_rows],
        "statuses": ["success", "failed", "timeout"],
        "sources": ["main_agent", "subagent", "skill", "automation"],
    })


@router.get("/tools/summary", dependencies=[Depends(require_config)])
def tool_log_summary(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    success_expr = func.sum(case((ToolCallLog.status == "success", 1), else_=0))
    q = db.query(
        ToolCallLog.tool_name,
        func.count().label("cnt"),
        success_expr.label("success_cnt"),
        func.avg(ToolCallLog.duration_ms).label("avg_duration_ms"),
    )
    q = _apply_date_range(q, ToolCallLog.created_at, date_from, date_to)
    rows = q.group_by(ToolCallLog.tool_name).order_by(desc("cnt")).all()
    return success_response(data=[
        {
            "tool_name": r.tool_name,
            "total": int(r.cnt or 0),
            "success": int(r.success_cnt or 0),
            "success_rate": (float(r.success_cnt or 0) / float(r.cnt)) if r.cnt else 0.0,
            "avg_duration_ms": int(r.avg_duration_ms) if r.avg_duration_ms is not None else None,
        }
        for r in rows
    ])


@router.get("/tools/{log_id}", dependencies=[Depends(require_config)])
def get_tool_log(log_id: str, db: Session = Depends(get_db)):
    row = db.query(ToolCallLog).filter(ToolCallLog.id == log_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Tool call log not found")
    titles = _session_title_map(db, [row.chat_id] if row.chat_id else [])
    return success_response(data=_serialize(row, titles))


@router.get("/subagents", dependencies=[Depends(require_config)])
def list_subagent_logs(
    user_id: Optional[str] = Query(None),
    chat_id: Optional[str] = Query(None),
    subagent_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    plan_id: Optional[str] = Query(None),
    only_parents: bool = Query(False),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = _apply_filters(db.query(SubAgentCallLog), {
        SubAgentCallLog.user_id: user_id,
        SubAgentCallLog.chat_id: chat_id,
        SubAgentCallLog.subagent_name: subagent_name,
        SubAgentCallLog.status: status,
        SubAgentCallLog.trace_id: trace_id,
        SubAgentCallLog.plan_id: plan_id,
    })
    if only_parents:
        query = query.filter(SubAgentCallLog.parent_subagent_log_id.is_(None))
    query = _apply_date_range(query, SubAgentCallLog.created_at, date_from, date_to)
    rows, total = _paginate(query, page, page_size, SubAgentCallLog.created_at)
    titles = _session_title_map(db, [r.chat_id for r in rows if r.chat_id])
    items = [_serialize(r, titles) for r in rows]
    return paginated_response(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/subagents/filters", dependencies=[Depends(require_config)])
def subagent_filters(db: Session = Depends(get_db)):
    rows = (
        db.query(SubAgentCallLog.subagent_name)
        .filter(SubAgentCallLog.subagent_name.isnot(None))
        .distinct()
        .order_by(SubAgentCallLog.subagent_name)
        .all()
    )
    return success_response(data={
        "subagent_names": [r.subagent_name for r in rows],
        "statuses": ["running", "success", "failed", "cancelled"],
    })


def _collect_subagent_subtree_ids(db: Session, root_id: str) -> List[str]:
    """BFS over parent_subagent_log_id — plan depth is ≤ 2 in practice so a
    CTE is overkill."""
    all_ids = [root_id]
    frontier = [root_id]
    while frontier:
        rows = (
            db.query(SubAgentCallLog.id)
            .filter(SubAgentCallLog.parent_subagent_log_id.in_(frontier))
            .all()
        )
        next_ids = [r.id for r in rows]
        if not next_ids:
            break
        all_ids.extend(next_ids)
        frontier = next_ids
    return all_ids


@router.get("/subagents/{log_id}", dependencies=[Depends(require_config)])
def get_subagent_log(log_id: str, db: Session = Depends(get_db)):
    row = db.query(SubAgentCallLog).filter(SubAgentCallLog.id == log_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Sub-agent log not found")

    titles = _session_title_map(db, [row.chat_id] if row.chat_id else [])
    detail = _serialize(row, titles)

    child_steps = (
        db.query(SubAgentCallLog)
        .filter(SubAgentCallLog.parent_subagent_log_id == log_id)
        .order_by(SubAgentCallLog.step_index)
        .all()
    )
    detail["child_steps"] = [_serialize(s) for s in child_steps]

    # Include tool / skill calls from descendants: rows are attached to the
    # leaf (step) subagent, so querying by parent_id alone returns nothing.
    subtree_ids = _collect_subagent_subtree_ids(db, log_id)
    tool_logs = (
        db.query(ToolCallLog)
        .filter(ToolCallLog.subagent_log_id.in_(subtree_ids))
        .order_by(ToolCallLog.created_at)
        .all()
    )
    detail["tool_calls"] = [_serialize(t) for t in tool_logs]

    skill_logs = (
        db.query(SkillCallLog)
        .filter(SkillCallLog.subagent_log_id.in_(subtree_ids))
        .order_by(SkillCallLog.created_at)
        .all()
    )
    detail["skill_calls"] = [_serialize(s) for s in skill_logs]
    return success_response(data=detail)


@router.get("/skills", dependencies=[Depends(require_config)])
def list_skill_logs(
    user_id: Optional[str] = Query(None),
    chat_id: Optional[str] = Query(None),
    skill_name: Optional[str] = Query(None),
    invocation_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = _apply_filters(db.query(SkillCallLog), {
        SkillCallLog.user_id: user_id,
        SkillCallLog.chat_id: chat_id,
        SkillCallLog.skill_name: skill_name,
        SkillCallLog.invocation_type: invocation_type,
        SkillCallLog.status: status,
        SkillCallLog.trace_id: trace_id,
        SkillCallLog.source: source,
    })
    query = _apply_date_range(query, SkillCallLog.created_at, date_from, date_to)
    rows, total = _paginate(query, page, page_size, SkillCallLog.created_at)
    titles = _session_title_map(db, [r.chat_id for r in rows if r.chat_id])
    items = [_serialize(r, titles) for r in rows]
    return paginated_response(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/skills/filters", dependencies=[Depends(require_config)])
def skill_filters(db: Session = Depends(get_db)):
    name_rows = (
        db.query(SkillCallLog.skill_name)
        .filter(SkillCallLog.skill_name.isnot(None))
        .distinct()
        .order_by(SkillCallLog.skill_name)
        .all()
    )
    return success_response(data={
        "skill_names": [r.skill_name for r in name_rows],
        "invocation_types": ["view", "run_script", "auto_load"],
        "statuses": ["success", "failed", "timeout"],
    })


@router.get("/skills/{log_id}", dependencies=[Depends(require_config)])
def get_skill_log(log_id: str, db: Session = Depends(get_db)):
    row = db.query(SkillCallLog).filter(SkillCallLog.id == log_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill call log not found")
    titles = _session_title_map(db, [row.chat_id] if row.chat_id else [])
    return success_response(data=_serialize(row, titles))


@router.get("/trace/{trace_id}", dependencies=[Depends(require_config)])
def trace_detail(trace_id: str, db: Session = Depends(get_db)):
    def _by_trace(model):
        return (
            db.query(model)
            .filter(model.trace_id == trace_id)
            .order_by(model.created_at)
            .all()
        )
    return success_response(data={
        "trace_id": trace_id,
        "tool_calls": [_serialize(r) for r in _by_trace(ToolCallLog)],
        "subagent_calls": [_serialize(r) for r in _by_trace(SubAgentCallLog)],
        "skill_calls": [_serialize(r) for r in _by_trace(SkillCallLog)],
    })
