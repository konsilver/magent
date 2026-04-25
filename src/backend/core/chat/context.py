"""Runtime context assembly for chat workflow.

Extracted from ``api/routes/chat.py`` — centralises the logic for
building the dict that ``routing/workflow.py`` consumes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.config.catalog_resolver import resolve_all_runtime_enabled
from core.services import UserService

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


def normalize_external_user_id(raw_user_id: Optional[str]) -> str:
    candidate = (raw_user_id or "").strip() or "anonymous"
    sanitized = "".join(ch if (ch.isalnum() or ch in {"_", "-", "."}) else "_" for ch in candidate)
    return sanitized[:48] or "anonymous"


def resolve_db_user_id(
    db: Session,
    user_id_from_auth: Optional[str],
    request_user_id: Optional[str] = None,
) -> str:
    """Resolve a DB user_id from auth context or fallback request user_id."""
    if user_id_from_auth:
        return user_id_from_auth

    try:
        db.rollback()
    except Exception:
        pass

    external_user = normalize_external_user_id(request_user_id)
    user_service = UserService(db)
    shadow_user = user_service.get_or_create_user_shadow(
        user_center_id=f"local_{external_user}",
        username=external_user,
    )
    return shadow_user.user_id


def generate_smart_title(message: str) -> str:
    message = message.strip()
    if not message:
        return "新对话"
    for delimiter in ["。", "！", "？", ".", "!", "?"]:
        if delimiter in message:
            first_sentence = message.split(delimiter)[0] + delimiter
            if len(first_sentence) <= 30:
                return first_sentence
            break
    return message if len(message) <= 20 else message[:20] + "..."


def resolve_user_facing_error(exc: Exception) -> str:
    """Map exceptions to user-friendly Chinese error strings."""
    msg = str(exc).lower()
    if "rate limit" in msg or "429" in msg:
        return "请求过于频繁，请稍后重试"
    if "timeout" in msg:
        return "请求超时，请稍后重试"
    if "connection" in msg:
        return "服务连接失败，请稍后重试"
    if "api key" in msg or "authentication" in msg or "401" in msg:
        return "模型服务认证失败，请联系管理员"
    if "502" in msg or "503" in msg or "bad gateway" in msg:
        return "模型服务暂时不可用，请稍后重试"
    return "请求处理失败，请稍后重试"


def resolve_enabled_capabilities(
    db: Session,
    user_id: str,
    request_skills: Optional[List[str]] = None,
    request_agents: Optional[List[str]] = None,
    request_mcps: Optional[List[str]] = None,
):
    """Resolve effective enabled capabilities, merging request overrides with DB state."""
    if request_skills is not None and request_agents is not None and request_mcps is not None:
        return request_skills, request_agents, request_mcps

    db_skills, db_agents, db_mcps = resolve_all_runtime_enabled(db, user_id)
    return (
        request_skills if request_skills is not None else db_skills,
        request_agents if request_agents is not None else db_agents,
        request_mcps if request_mcps is not None else db_mcps,
    )


def build_runtime_context(
    *,
    model_name: Optional[str],
    user_id: str,
    chat_id: str,
    enable_thinking: bool = False,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
    enabled_skills: Optional[List[str]] = None,
    enabled_agents: Optional[List[str]] = None,
    enabled_mcps: Optional[List[str]] = None,
    enabled_kbs: Optional[List[str]] = None,
    memory_enabled: bool = False,
    memory_write_enabled: bool = False,
    reranker_enabled: bool = False,
) -> Dict[str, Any]:
    """Build the runtime context dict consumed by workflow.py."""
    return {
        "model_name": model_name,
        "user_id": user_id,
        "chat_id": chat_id,
        "enable_thinking": enable_thinking,
        "uploaded_files": uploaded_files or [],
        "enabled_skills": enabled_skills,
        "enabled_agents": enabled_agents,
        "enabled_mcps": enabled_mcps,
        "enabled_kbs": enabled_kbs,
        "memory_enabled": memory_enabled,
        "memory_write_enabled": memory_write_enabled,
        "reranker_enabled": reranker_enabled,
    }
