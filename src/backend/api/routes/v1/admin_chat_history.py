"""Admin chat history API routes.

Provides endpoints for super-admins to browse all users' chat sessions
and view full conversation content including tool call results.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.db.models import ChatMessage, ChatSession, UserShadow
from core.infra.responses import success_response, paginated_response

router = APIRouter(prefix="/v1/admin/chat-history", tags=["Admin Chat History"])
logger = logging.getLogger(__name__)


@router.get("/sessions", dependencies=[Depends(require_config)])
def list_sessions(
    user_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all chat sessions across users (admin view, includes deleted)."""
    query = (
        db.query(
            ChatSession.chat_id,
            ChatSession.user_id,
            ChatSession.title,
            ChatSession.message_count,
            ChatSession.created_at,
            ChatSession.updated_at,
            ChatSession.deleted_at,
            UserShadow.username,
        )
        .join(UserShadow, ChatSession.user_id == UserShadow.user_id)
    )

    if user_id:
        query = query.filter(ChatSession.user_id == user_id)
    if search:
        query = query.filter(ChatSession.title.ilike(f"%{search}%"))
    if date_from:
        query = query.filter(ChatSession.created_at >= date_from)
    if date_to:
        query = query.filter(ChatSession.created_at <= date_to)

    total = query.count()
    rows = (
        query.order_by(desc(ChatSession.updated_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        {
            "chat_id": r.chat_id,
            "user_id": r.user_id,
            "username": r.username,
            "title": r.title,
            "message_count": r.message_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
        }
        for r in rows
    ]

    return paginated_response(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/sessions/{chat_id}/messages", dependencies=[Depends(require_config)])
def get_session_messages(chat_id: str, db: Session = Depends(get_db)):
    """Get all messages for a specific chat session."""
    session = db.query(ChatSession).filter(ChatSession.chat_id == chat_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.created_at)
        .all()
    )

    items = [
        {
            "message_id": m.message_id,
            "role": m.role,
            "content": m.content or "",
            "model": m.model,
            "tool_calls": m.tool_calls,
            "usage": m.usage,
            "error": m.error,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]

    return success_response(data=items)


@router.get("/users", dependencies=[Depends(require_config)])
def list_users(db: Session = Depends(get_db)):
    """List all users for filter dropdowns."""
    rows = (
        db.query(UserShadow.user_id, UserShadow.username)
        .order_by(UserShadow.username)
        .all()
    )
    return success_response(data=[{"user_id": r.user_id, "username": r.username} for r in rows])
