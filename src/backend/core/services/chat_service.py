"""Chat session and message business logic."""

from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import uuid
from sqlalchemy.orm import Session

from core.db.repository import ChatSessionRepository, ChatMessageRepository, AuditLogRepository
from core.db.models import ChatSession, ChatMessage


class ChatService:
    """Service for chat-related operations."""

    def __init__(self, db: Session):
        self.db = db
        self.session_repo = ChatSessionRepository(db)
        self.message_repo = ChatMessageRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def create_session(
        self,
        user_id: str,
        title: str = "新对话",
        extra_data: Dict = None,
        chat_id: Optional[str] = None
    ) -> ChatSession:
        """Create a new chat session.

        If `chat_id` is provided and belongs to the same user, reuse it.
        If it belongs to another user, generate a new chat_id.
        """
        if chat_id:
            existing = self.session_repo.get_by_id(chat_id)
            if existing:
                if existing.user_id == user_id:
                    return existing
                chat_id = None

        session_data = {
            "chat_id": chat_id or f"chat_{uuid.uuid4().hex[:16]}",
            "user_id": user_id,
            "title": title,
            "extra_data": extra_data or {}
        }
        session = self.session_repo.create(session_data)

        # Audit log
        self.audit_repo.create({
            "user_id": user_id,
            "action": "chat.session.created",
            "resource_type": "chat_session",
            "resource_id": session.chat_id,
            "status": "success"
        })

        return session

    def ensure_session(
        self,
        chat_id: str,
        user_id: str,
        title: str = "新对话",
        extra_data: Optional[Dict] = None
    ) -> Optional[ChatSession]:
        """Ensure a chat session exists for user and chat_id."""
        existing = self.session_repo.get_by_id(chat_id)
        if existing:
            if existing.user_id != user_id:
                return None
            # Merge any missing metadata flags into existing session
            if extra_data:
                merged = dict(existing.extra_data or {})
                changed = False
                for k, v in extra_data.items():
                    if k not in merged:
                        merged[k] = v
                        changed = True
                if changed:
                    existing.extra_data = merged
                    self.db.commit()
            return existing
        return self.create_session(
            user_id=user_id,
            title=title,
            extra_data=extra_data or {},
            chat_id=chat_id
        )

    def list_sessions(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        pinned_only: bool = False,
        favorite_only: bool = False,
        exclude_automation: bool = False,
    ) -> Tuple[List[ChatSession], int, int]:
        """List chat sessions with pagination."""
        sessions, total = self.session_repo.list_by_user(
            user_id, page, page_size, pinned_only, favorite_only,
            exclude_automation=exclude_automation,
        )

        total_pages = (total + page_size - 1) // page_size

        return sessions, total, total_pages

    def get_session(self, chat_id: str, user_id: str) -> Optional[ChatSession]:
        """Get chat session with ownership check."""
        session = self.session_repo.get_by_id(chat_id)

        if session and session.user_id != user_id:
            # Access denied - user doesn't own this session
            return None

        return session

    def update_session(
        self,
        chat_id: str,
        user_id: str,
        update_data: Dict[str, Any]
    ) -> Optional[ChatSession]:
        """Update chat session."""
        session = self.get_session(chat_id, user_id)
        if not session:
            return None

        normalized_update_data = dict(update_data)
        extra_data_patch = normalized_update_data.get("extra_data")
        if isinstance(extra_data_patch, dict):
            merged_extra_data = dict(session.extra_data or {})
            merged_extra_data.update(extra_data_patch)
            normalized_update_data["extra_data"] = merged_extra_data

        updated_session = self.session_repo.update(chat_id, normalized_update_data)

        # Audit log
        self.audit_repo.create({
            "user_id": user_id,
            "action": "chat.session.updated",
            "resource_type": "chat_session",
            "resource_id": chat_id,
            "details": normalized_update_data,
            "status": "success"
        })

        return updated_session

    def delete_session(self, chat_id: str, user_id: str) -> bool:
        """Delete chat session (soft delete)."""
        session = self.get_session(chat_id, user_id)
        if not session:
            return False

        result = self.session_repo.soft_delete(chat_id)

        if result:
            # Audit log
            self.audit_repo.create({
                "user_id": user_id,
                "action": "chat.session.deleted",
                "resource_type": "chat_session",
                "resource_id": chat_id,
                "status": "success"
            })

        return result

    def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        tool_calls: Optional[List[Dict]] = None,
        usage: Optional[Dict] = None,
        error: Optional[Dict] = None,
        extra_data: Dict = None,
        message_id: Optional[str] = None,
    ) -> ChatMessage:
        """Add a message to a chat session."""
        message_data = {
            "message_id": message_id or f"msg_{uuid.uuid4().hex[:16]}",
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "model": model,
            "tool_calls": tool_calls,
            "usage": usage,
            "error": error,
            "extra_data": extra_data or {}
        }

        message = self.message_repo.create(message_data)

        # Keep session metadata in sync for list APIs.
        session = self.session_repo.get_by_id(chat_id)
        if session:
            session.message_count = (session.message_count or 0) + 1
            session.updated_at = datetime.utcnow()
            self.db.commit()

        return message

    def list_all_messages(self, chat_id: str, user_id: str) -> Optional[List[ChatMessage]]:
        """List all messages in chronological order with ownership check."""
        session = self.get_session(chat_id, user_id)
        if not session:
            return None

        return self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id
        ).order_by(ChatMessage.created_at).all()

    def list_messages(
        self,
        chat_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 50
    ) -> Optional[Tuple[List[ChatMessage], int, int]]:
        """List messages in a chat session."""
        # Check ownership
        session = self.get_session(chat_id, user_id)
        if not session:
            return None

        messages, total = self.message_repo.list_by_chat(chat_id, page, page_size)
        total_pages = (total + page_size - 1) // page_size

        return messages, total, total_pages

    def delete_messages_from(self, chat_id: str, message_id: str) -> int:
        """Delete a message and all subsequent messages in the chat.

        Returns the number of messages deleted.
        """
        target = self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.message_id == message_id,
        ).first()
        if not target:
            return 0

        deleted = self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.created_at >= target.created_at,
        ).delete(synchronize_session="fetch")

        # Update session message count
        session = self.session_repo.get_by_id(chat_id)
        if session:
            remaining = self.db.query(ChatMessage).filter(
                ChatMessage.chat_id == chat_id,
            ).count()
            session.message_count = remaining
            session.updated_at = datetime.utcnow()

        self.db.commit()
        return deleted

    def get_message_by_id(self, message_id: str) -> Optional[ChatMessage]:
        """Get a single message by its ID."""
        return self.db.query(ChatMessage).filter(
            ChatMessage.message_id == message_id,
        ).first()

    def get_message_by_index(self, chat_id: str, index: int) -> Optional[ChatMessage]:
        """Get a message by its position (0-based) in the chat, ordered by created_at."""
        return self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
        ).order_by(ChatMessage.created_at).offset(index).limit(1).first()

    def get_user_message_before(self, chat_id: str, message_id: str) -> Optional[ChatMessage]:
        """Get the user message immediately before the given message."""
        target = self.get_message_by_id(message_id)
        if not target:
            return None
        return self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.role == "user",
            ChatMessage.created_at < target.created_at,
        ).order_by(ChatMessage.created_at.desc()).first()

    def update_message_extra_data(
        self,
        message_id: str,
        patch: Dict[str, Any],
    ) -> bool:
        """Merge *patch* into a message's extra_data. Returns True on success."""
        return self.message_repo.update_extra_data(message_id, patch) is not None

    def search_sessions(
        self,
        user_id: str,
        query: str,
        page: int = 1,
        page_size: int = 20,
        scope: str = "title",
    ) -> Tuple[list, int]:
        """Search chat sessions by title (and optionally message content)."""
        results, total = self.session_repo.search(user_id, query, page, page_size, scope=scope)
        return results, total
