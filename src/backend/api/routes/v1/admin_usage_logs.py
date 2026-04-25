"""Admin usage logs API routes.

Provides query endpoints for viewing per-user agent call logs,
including token usage, model info, and error status.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case, cast, Integer, desc
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.db.models import ChatMessage, ChatSession, UserShadow
from core.infra.responses import success_response, paginated_response

router = APIRouter(prefix="/v1/admin/usage-logs", tags=["Admin Usage Logs"])
logger = logging.getLogger(__name__)


def _extract_usage_int(usage_col, key: str):
    """Extract integer token count from usage JSONB, defaulting to 0."""
    return func.coalesce(cast(usage_col[key].as_string(), Integer), 0)


@router.get("", dependencies=[Depends(require_config)])
def list_usage_logs(
    user_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    has_error: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List per-request usage logs with token details."""
    query = (
        db.query(
            ChatMessage.message_id,
            ChatMessage.chat_id,
            ChatMessage.model,
            ChatMessage.usage,
            ChatMessage.error,
            ChatMessage.created_at,
            ChatSession.title.label("session_title"),
            ChatSession.user_id,
            UserShadow.username,
        )
        .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
        .join(UserShadow, ChatSession.user_id == UserShadow.user_id)
        .filter(ChatMessage.role == "assistant")
    )

    if user_id:
        query = query.filter(ChatSession.user_id == user_id)
    if model:
        query = query.filter(ChatMessage.model == model)
    if date_from:
        query = query.filter(ChatMessage.created_at >= date_from)
    if date_to:
        query = query.filter(ChatMessage.created_at <= date_to)
    if has_error is True:
        query = query.filter(ChatMessage.error.isnot(None))
    elif has_error is False:
        query = query.filter(ChatMessage.error.is_(None))

    total = query.count()
    rows = (
        query.order_by(desc(ChatMessage.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        usage = r.usage or {}
        pt = usage.get("prompt_tokens", 0) or 0
        ct = usage.get("completion_tokens", 0) or 0
        items.append({
            "message_id": r.message_id,
            "chat_id": r.chat_id,
            "user_id": r.user_id,
            "username": r.username,
            "session_title": r.session_title,
            "model": r.model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "has_error": r.error is not None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return paginated_response(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/summary", dependencies=[Depends(require_config)])
def usage_summary(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    group_by: str = Query("day", regex="^(day|model|user)$"),
    db: Session = Depends(get_db),
):
    """Aggregate usage statistics grouped by day, model, or user."""
    prompt_tokens = _extract_usage_int(ChatMessage.usage, "prompt_tokens")
    completion_tokens = _extract_usage_int(ChatMessage.usage, "completion_tokens")

    if group_by == "day":
        group_col = func.date(ChatMessage.created_at)
    elif group_by == "model":
        group_col = ChatMessage.model
    else:
        group_col = ChatSession.user_id

    query = (
        db.query(
            group_col.label("group_key"),
            func.count().label("total_requests"),
            func.sum(prompt_tokens).label("prompt_tokens"),
            func.sum(completion_tokens).label("completion_tokens"),
            func.sum(prompt_tokens + completion_tokens).label("total_tokens"),
        )
        .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
        .filter(ChatMessage.role == "assistant")
        .group_by(group_col)
    )

    if group_by == "user":
        query = query.join(UserShadow, ChatSession.user_id == UserShadow.user_id)
        query = query.add_columns(UserShadow.username.label("display_name"))
        query = query.group_by(UserShadow.username)

    if date_from:
        query = query.filter(ChatMessage.created_at >= date_from)
    if date_to:
        query = query.filter(ChatMessage.created_at <= date_to)

    rows = query.order_by(group_col).all()

    items = []
    for r in rows:
        item = {
            "group_key": str(r.group_key) if r.group_key else "unknown",
            "total_requests": r.total_requests or 0,
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
        }
        if group_by == "user" and hasattr(r, "display_name"):
            item["display_name"] = r.display_name
        items.append(item)

    return success_response(data=items)


@router.get("/models", dependencies=[Depends(require_config)])
def list_distinct_models(db: Session = Depends(get_db)):
    """List all distinct model names that appear in chat messages."""
    rows = (
        db.query(ChatMessage.model)
        .filter(ChatMessage.model.isnot(None))
        .distinct()
        .order_by(ChatMessage.model)
        .all()
    )
    return success_response(data=[r.model for r in rows])
