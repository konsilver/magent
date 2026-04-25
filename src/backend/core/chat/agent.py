"""Agent session management.

Note: Agent creation logic lives in core.llm.agent_factory.
"""

from __future__ import annotations

from typing import Dict

from core.llm.agent_factory import create_agent_executor
from core.chat.session import get_session_store

# Export for backward compatibility
__all__ = ["create_agent_executor", "get_or_create_session", "sessions"]


def get_or_create_session(chat_id: str) -> dict:
    """获取或创建会话.

    Uses pluggable session store backend (see core.session).
    Default: in-memory storage (non-persistent).
    Set SESSION_STORE env var to use persistent backend.
    """
    store = get_session_store()
    return store.get_or_create(chat_id)


@property
def sessions() -> Dict[str, dict]:
    """Direct access to sessions dict (backward compatibility).

    WARNING: This only works with MemorySessionStore.
    For persistent stores, use get_session_store() API instead.
    """
    store = get_session_store()
    if hasattr(store, "sessions"):
        return store.sessions
    raise AttributeError(
        "Current session store does not support direct dict access. "
        "Use get_session_store() API instead."
    )
