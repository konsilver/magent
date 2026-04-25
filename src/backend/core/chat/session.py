"""Session storage abstraction with pluggable backends.

This module provides:
- SessionStore protocol: interface for session storage
- MemorySessionStore: in-memory implementation (default, non-persistent)
- get_session_store(): factory function that returns configured store

For production, implement PersistentSessionStore using Redis/PostgreSQL/etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Protocol

from core.config.settings import settings


class SessionStore(Protocol):
    """Protocol for session storage backends."""

    def get_or_create(self, chat_id: str) -> dict:
        """Get existing session or create new one."""
        ...

    def get(self, chat_id: str) -> Optional[dict]:
        """Get session by chat_id, return None if not found."""
        ...

    def save(self, chat_id: str, session: dict) -> None:
        """Save session data."""
        ...

    def delete(self, chat_id: str) -> bool:
        """Delete session, return True if existed."""
        ...

    def list_all(self) -> List[str]:
        """Return list of all chat_ids."""
        ...


class MemorySessionStore:
    """In-memory session storage (non-persistent).

    WARNING: All sessions will be lost on server restart.
    Use this for development/testing only.
    """

    def __init__(self):
        self._sessions: Dict[str, dict] = {}

    def get_or_create(self, chat_id: str) -> dict:
        """获取或创建会话."""
        if chat_id not in self._sessions:
            self._sessions[chat_id] = {
                "messages": [],
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
            }
        return self._sessions[chat_id]

    def get(self, chat_id: str) -> Optional[dict]:
        """Get session by chat_id."""
        return self._sessions.get(chat_id)

    def save(self, chat_id: str, session: dict) -> None:
        """Save session data."""
        session["last_updated"] = datetime.now().isoformat()
        self._sessions[chat_id] = session

    def delete(self, chat_id: str) -> bool:
        """Delete session."""
        if chat_id in self._sessions:
            del self._sessions[chat_id]
            return True
        return False

    def list_all(self) -> List[str]:
        """Return list of all chat_ids."""
        return list(self._sessions.keys())

    @property
    def sessions(self) -> Dict[str, dict]:
        """Direct access to underlying dict (backward compatibility)."""
        return self._sessions


# Placeholder for future persistent implementation
class PersistentSessionStore(ABC):
    """Base class for persistent session storage.

    Example implementations:
    - RedisSessionStore: using Redis as backend
    - PostgreSQLSessionStore: using PostgreSQL as backend
    - FileSessionStore: using local JSON files
    """

    @abstractmethod
    def get_or_create(self, chat_id: str) -> dict:
        pass

    @abstractmethod
    def get(self, chat_id: str) -> Optional[dict]:
        pass

    @abstractmethod
    def save(self, chat_id: str, session: dict) -> None:
        pass

    @abstractmethod
    def delete(self, chat_id: str) -> bool:
        pass

    @abstractmethod
    def list_all(self) -> List[str]:
        pass


class PostgreSQLSessionStore:
    """PostgreSQL-backed session storage (persistent).

    Uses ChatSessionRepository and ChatMessageRepository for persistence.
    Note: This implementation requires a user_id, which may not be available
    for anonymous sessions. In such cases, falls back to memory storage.
    """

    def __init__(self):
        from core.db.engine import SessionLocal
        from core.db.repository import ChatSessionRepository, ChatMessageRepository

        self.db_factory = SessionLocal
        self._memory_fallback = MemorySessionStore()

    def _get_db(self):
        """Get a new database session."""
        return self.db_factory()

    def get_or_create(self, chat_id: str) -> dict:
        """Get or create session from PostgreSQL."""
        db = self._get_db()
        try:
            from core.db.repository import ChatSessionRepository, ChatMessageRepository

            session_repo = ChatSessionRepository(db)
            message_repo = ChatMessageRepository(db)

            # Try to find session by chat_id (stored in extra_data)
            session_model = session_repo.get_by_id(chat_id)

            if session_model:
                # Load messages
                messages, _ = message_repo.list_by_chat(chat_id, page=1, page_size=1000)

                return {
                    "messages": [
                        {
                            "role": msg.role,
                            "content": msg.content,
                        }
                        for msg in messages
                    ],
                    "created_at": session_model.created_at.isoformat(),
                    "last_updated": session_model.updated_at.isoformat(),
                }
            else:
                # Session doesn't exist in DB - use memory fallback
                # (will be created in DB when first message is sent with auth)
                return self._memory_fallback.get_or_create(chat_id)

        except Exception as e:
            print(f"Warning: PostgreSQL session store failed, using memory fallback: {e}")
            return self._memory_fallback.get_or_create(chat_id)
        finally:
            db.close()

    def get(self, chat_id: str) -> Optional[dict]:
        """Get session from PostgreSQL."""
        db = self._get_db()
        try:
            from core.db.repository import ChatSessionRepository, ChatMessageRepository

            session_repo = ChatSessionRepository(db)
            message_repo = ChatMessageRepository(db)

            session_model = session_repo.get_by_id(chat_id)
            if not session_model:
                # Try memory fallback
                return self._memory_fallback.get(chat_id)

            # Load messages
            messages, _ = message_repo.list_by_chat(chat_id, page=1, page_size=1000)

            return {
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                    }
                    for msg in messages
                ],
                "created_at": session_model.created_at.isoformat(),
                "last_updated": session_model.updated_at.isoformat(),
            }

        except Exception as e:
            print(f"Warning: PostgreSQL session get failed: {e}")
            return self._memory_fallback.get(chat_id)
        finally:
            db.close()

    def save(self, chat_id: str, session: dict) -> None:
        """Save session to PostgreSQL.

        Note: This is complex because we need user_id to create/update sessions.
        For now, we delegate to ChatService which is called during chat operations.
        This method is kept for interface compatibility.
        """
        # Session saving is handled by ChatService during chat operations
        # Here we just update the memory fallback for immediate consistency
        self._memory_fallback.save(chat_id, session)

    def delete(self, chat_id: str) -> bool:
        """Delete session from PostgreSQL (soft delete)."""
        db = self._get_db()
        try:
            from core.db.repository import ChatSessionRepository

            session_repo = ChatSessionRepository(db)
            result = session_repo.soft_delete(chat_id)

            # Also delete from memory fallback
            self._memory_fallback.delete(chat_id)

            return result

        except Exception as e:
            print(f"Warning: PostgreSQL session delete failed: {e}")
            return self._memory_fallback.delete(chat_id)
        finally:
            db.close()

    def list_all(self) -> List[str]:
        """List all session IDs.

        Note: This returns sessions from both PostgreSQL and memory fallback.
        """
        db = self._get_db()
        try:
            from core.db.repository import ChatSessionRepository

            session_repo = ChatSessionRepository(db)

            # Get all sessions (this might be slow for large datasets)
            # TODO: Add pagination or limit
            db_sessions = db.query(session_repo.model).filter_by(deleted=False).all()
            db_chat_ids = [s.chat_id for s in db_sessions]

            # Merge with memory fallback
            memory_chat_ids = self._memory_fallback.list_all()

            # Return unique IDs
            return list(set(db_chat_ids + memory_chat_ids))

        except Exception as e:
            print(f"Warning: PostgreSQL session list failed: {e}")
            return self._memory_fallback.list_all()
        finally:
            db.close()


# Global session store instance
_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Get the configured session store instance.

    Selection based on SESSION_STORE env var:
    - "memory" (default): MemorySessionStore - Non-persistent, for development
    - "postgresql": PostgreSQLSessionStore - Persistent, for production
    - "redis": RedisSessionStore (TODO: not yet implemented)

    Returns:
        SessionStore instance (singleton)
    """
    global _store
    if _store is not None:
        return _store

    store_type = settings.session.store_type

    if store_type == "memory":
        _store = MemorySessionStore()
    elif store_type == "postgresql":
        _store = PostgreSQLSessionStore()
    elif store_type == "redis":
        raise NotImplementedError(
            "RedisSessionStore not yet implemented. "
            "To use Redis, implement RedisSessionStore and update get_session_store()."
        )
    else:
        raise ValueError(
            f"Unknown session store type: {store_type!r}. "
            f"Supported: memory, postgresql, redis"
        )

    return _store


def reset_session_store() -> None:
    """Reset global store instance (for testing)."""
    global _store
    _store = None
