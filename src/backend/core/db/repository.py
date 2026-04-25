"""Data access layer - Repository pattern for database operations."""

from typing import Optional, List, Dict, Any
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func, select
from core.db.models import (
    UserShadow, ChatSession, ChatMessage, CatalogOverride,
    KBSpace, KBDocument, Artifact, AuditLog, UserAgent
)


class UserRepository:
    """Repository for user operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: str) -> Optional[UserShadow]:
        """Get user by ID."""
        return self.db.query(UserShadow).filter(UserShadow.user_id == user_id).first()

    def get_by_user_center_id(self, user_center_id: str) -> Optional[UserShadow]:
        """Get user by user center ID."""
        return self.db.query(UserShadow).filter(
            UserShadow.user_center_id == user_center_id
        ).first()

    def create(self, user_data: Dict[str, Any]) -> UserShadow:
        """Create a new user shadow."""
        user = UserShadow(**user_data)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(self, user_id: str, update_data: Dict[str, Any]) -> Optional[UserShadow]:
        """Update user information."""
        user = self.get_by_id(user_id)
        if not user:
            return None

        for key, value in update_data.items():
            setattr(user, key, value)

        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return user


class ChatSessionRepository:
    """Repository for chat session operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, chat_id: str) -> Optional[ChatSession]:
        """Get chat session by ID."""
        return self.db.query(ChatSession).filter(
            ChatSession.chat_id == chat_id,
            ChatSession.deleted_at.is_(None)
        ).first()

    def list_by_user(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        pinned_only: bool = False,
        favorite_only: bool = False,
        exclude_automation: bool = False,
    ) -> tuple[List[ChatSession], int]:
        """List chat sessions for a user with pagination."""
        query = self.db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None)
        )

        if pinned_only:
            query = query.filter(ChatSession.pinned == True)
        if favorite_only:
            query = query.filter(ChatSession.favorite == True)
        if exclude_automation:
            # Exclude sessions created by automation scheduler.
            # extra_data is mapped to the "metadata" JSON column.
            # Use dialect-portable cast: check the JSON text doesn't contain the marker key.
            query = query.filter(
                or_(
                    ChatSession.extra_data.is_(None),
                    ~func.cast(ChatSession.extra_data, sa.Text).contains('"automation_run"'),
                )
            )

        # Get total count
        total = query.count()

        # Apply pagination and ordering
        sessions = query.order_by(desc(ChatSession.updated_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return sessions, total

    def create(self, session_data: Dict[str, Any]) -> ChatSession:
        """Create a new chat session."""
        session = ChatSession(**session_data)
        session.created_at = datetime.utcnow()
        session.updated_at = datetime.utcnow()
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def update(self, chat_id: str, update_data: Dict[str, Any]) -> Optional[ChatSession]:
        """Update chat session."""
        session = self.get_by_id(chat_id)
        if not session:
            return None

        for key, value in update_data.items():
            setattr(session, key, value)

        session.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(session)
        return session

    def soft_delete(self, chat_id: str) -> bool:
        """Soft delete a chat session."""
        session = self.get_by_id(chat_id)
        if not session:
            return False

        session.deleted_at = datetime.utcnow()
        self.db.commit()
        return True

    def search(
        self, user_id: str, query: str, page: int = 1, page_size: int = 20, scope: str = "title"
    ) -> tuple[List[Dict[str, Any]], int]:
        """Search chat sessions by title and optionally message content.

        Args:
            scope: "title" (default) searches title only;
                   "all" searches both title and message content.

        Returns:
            A list of dicts with ChatSession + match_type + matched_snippet, and total count.
            Results are ordered: title matches first (by updated_at desc),
            then content-only matches (by updated_at desc).
        """
        like_pattern = f"%{query}%"

        base_filter = and_(
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None),
        )

        # Title-matching chat_ids (always needed)
        title_id_set: set[str] = {
            row[0]
            for row in self.db.query(ChatSession.chat_id)
            .filter(base_filter, ChatSession.title.ilike(like_pattern))
            .all()
        }

        if scope == "all":
            # Content-only matching chat_ids (exclude ones already matched by title)
            content_id_set: set[str] = set()
            content_rows = (
                self.db.query(ChatMessage.chat_id)
                .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
                .filter(
                    base_filter,
                    ChatMessage.role.in_(["user", "assistant"]),
                    ChatMessage.content.ilike(like_pattern),
                )
                .distinct()
                .all()
            )
            content_id_set = {row[0] for row in content_rows} - title_id_set
            all_ids = title_id_set | content_id_set
        else:
            content_id_set = set()
            all_ids = title_id_set

        total = len(all_ids)

        # Fetch title-matched sessions first, then content-matched sessions
        title_sessions = (
            self.db.query(ChatSession)
            .filter(ChatSession.chat_id.in_(title_id_set))
            .order_by(desc(ChatSession.updated_at))
            .all()
        ) if title_id_set else []

        content_sessions = (
            self.db.query(ChatSession)
            .filter(ChatSession.chat_id.in_(content_id_set))
            .order_by(desc(ChatSession.updated_at))
            .all()
        ) if content_id_set else []

        # Merge: title matches first, then content matches
        ordered = title_sessions + content_sessions

        # Apply pagination on the merged list
        start = (page - 1) * page_size
        page_sessions = ordered[start : start + page_size]

        results: List[Dict[str, Any]] = []
        for s in page_sessions:
            match_type = "title" if s.chat_id in title_id_set else "content"
            matched_snippet: Optional[str] = None

            if match_type == "content":
                msg = (
                    self.db.query(ChatMessage)
                    .filter(
                        ChatMessage.chat_id == s.chat_id,
                        ChatMessage.role.in_(["user", "assistant"]),
                        ChatMessage.content.ilike(like_pattern),
                    )
                    .order_by(ChatMessage.created_at)
                    .first()
                )
                if msg and msg.content:
                    # Center the snippet around the keyword
                    content = msg.content.replace("\n", " ")
                    lower_content = content.lower()
                    idx = lower_content.find(query.lower())
                    if idx == -1:
                        matched_snippet = content[:30]
                    else:
                        snippet_len = 30
                        half = snippet_len // 2
                        start_pos = max(0, idx - half)
                        end_pos = min(len(content), start_pos + snippet_len)
                        snippet = content[start_pos:end_pos]
                        if start_pos > 0:
                            snippet = "..." + snippet
                        if end_pos < len(content):
                            snippet = snippet + "..."
                        matched_snippet = snippet

            results.append({
                "session": s,
                "match_type": match_type,
                "matched_snippet": matched_snippet,
            })

        return results, total


class ChatMessageRepository:
    """Repository for chat message operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, message_id: str) -> Optional[ChatMessage]:
        """Get message by ID."""
        return self.db.query(ChatMessage).filter(
            ChatMessage.message_id == message_id
        ).first()

    def list_by_chat(
        self,
        chat_id: str,
        page: int = 1,
        page_size: int = 50
    ) -> tuple[List[ChatMessage], int]:
        """List messages for a chat session with pagination."""
        query = self.db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id
        )

        total = query.count()
        messages = query.order_by(ChatMessage.created_at).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return messages, total

    def create(self, message_data: Dict[str, Any]) -> ChatMessage:
        """Create a new chat message."""
        message = ChatMessage(**message_data)
        message.created_at = datetime.utcnow()
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def update_extra_data(self, message_id: str, patch: Dict[str, Any]) -> Optional[ChatMessage]:
        """Merge *patch* into the message's extra_data JSONB field."""
        message = self.get_by_id(message_id)
        if not message:
            return None
        merged = dict(message.extra_data or {})
        merged.update(patch)
        message.extra_data = merged
        self.db.commit()
        self.db.refresh(message)
        return message

    def bulk_create(self, messages_data: List[Dict[str, Any]]) -> List[ChatMessage]:
        """Bulk create chat messages."""
        messages = [ChatMessage(**data) for data in messages_data]
        for msg in messages:
            msg.created_at = datetime.utcnow()

        self.db.bulk_save_objects(messages)
        self.db.commit()
        return messages


class CatalogRepository:
    """Repository for catalog override operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_override(self, user_id: str, kind: str, item_id: str) -> Optional[CatalogOverride]:
        """Get catalog override for a specific item."""
        return self.db.query(CatalogOverride).filter(
            CatalogOverride.user_id == user_id,
            CatalogOverride.kind == kind,
            CatalogOverride.item_id == item_id
        ).first()

    def list_overrides(self, user_id: str, kind: Optional[str] = None) -> List[CatalogOverride]:
        """List all catalog overrides for a user."""
        query = self.db.query(CatalogOverride).filter(
            CatalogOverride.user_id == user_id
        )

        if kind:
            query = query.filter(CatalogOverride.kind == kind)

        return query.all()

    def upsert_override(self, user_id: str, kind: str, item_id: str, enabled: bool, config: Dict = None) -> CatalogOverride:
        """Create or update catalog override."""
        override = self.get_override(user_id, kind, item_id)

        if override:
            override.enabled = enabled
            if config is not None:
                override.config_data = config
            override.updated_at = datetime.utcnow()
        else:
            override = CatalogOverride(
                user_id=user_id,
                kind=kind,
                item_id=item_id,
                enabled=enabled,
                config_data=config or {}
            )
            self.db.add(override)

        self.db.commit()
        self.db.refresh(override)
        return override


class KBRepository:
    """Repository for knowledge base operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_space(self, kb_id: str) -> Optional[KBSpace]:
        """Get KB space by ID."""
        return self.db.query(KBSpace).filter(
            KBSpace.kb_id == kb_id,
            KBSpace.deleted_at.is_(None)
        ).first()

    def list_spaces(self, user_id: str) -> List[KBSpace]:
        """List all KB spaces for a user."""
        return self.db.query(KBSpace).filter(
            KBSpace.user_id == user_id,
            KBSpace.deleted_at.is_(None)
        ).all()

    def create_space(self, space_data: Dict[str, Any]) -> KBSpace:
        """Create a new KB space."""
        space = KBSpace(**space_data)
        self.db.add(space)
        self.db.commit()
        self.db.refresh(space)
        return space

    def update_space(self, kb_id: str, update_data: Dict[str, Any]) -> Optional[KBSpace]:
        """Update a KB space."""
        space = self.get_space(kb_id)
        if not space:
            return None

        for key, value in update_data.items():
            setattr(space, key, value)

        space.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(space)
        return space

    def get_document(self, document_id: str) -> Optional[KBDocument]:
        """Get KB document by ID."""
        return self.db.query(KBDocument).filter(
            KBDocument.document_id == document_id,
            KBDocument.deleted_at.is_(None)
        ).first()

    def list_documents(
        self,
        kb_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[List[KBDocument], int]:
        """List documents in a KB space."""
        query = self.db.query(KBDocument).filter(
            KBDocument.kb_id == kb_id,
            KBDocument.deleted_at.is_(None)
        )

        total = query.count()
        documents = query.order_by(desc(KBDocument.uploaded_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return documents, total

    def create_document(self, document_data: Dict[str, Any]) -> KBDocument:
        """Create a new KB document."""
        document = KBDocument(**document_data)
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document


class ArtifactRepository:
    """Repository for artifact operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, artifact_id: str) -> Optional[Artifact]:
        """Get artifact by ID."""
        return self.db.query(Artifact).filter(
            Artifact.artifact_id == artifact_id,
            Artifact.deleted_at.is_(None)
        ).first()

    def list_by_user(
        self,
        user_id: str,
        artifact_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[List[Artifact], int]:
        """List artifacts for a user."""
        query = self.db.query(Artifact).filter(
            Artifact.user_id == user_id,
            Artifact.deleted_at.is_(None)
        )

        if artifact_type:
            query = query.filter(Artifact.type == artifact_type)

        total = query.count()
        artifacts = query.order_by(desc(Artifact.created_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return artifacts, total

    def list_by_user_with_chat(
        self,
        user_id: str,
        mime_prefix: Optional[str] = None,
        keyword: Optional[str] = None,
        source_kind: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Dict[str, Any]], int]:
        """List artifacts for a user with chat session title (JOIN).

        Args:
            mime_prefix: e.g. "image/" to filter images, or use negation via caller logic.
            keyword: fuzzy match on filename or title.
            source_kind: "user_upload" | "ai_generated"; filters on
                ``extra_data.source`` using a dialect-aware JSON accessor.
        """
        query = self.db.query(Artifact, ChatSession.title.label("chat_title")).outerjoin(
            ChatSession, Artifact.chat_id == ChatSession.chat_id
        ).filter(
            Artifact.user_id == user_id,
            Artifact.deleted_at.is_(None),
        )

        if mime_prefix == "image/":
            query = query.filter(Artifact.mime_type.like("image/%"))
        elif mime_prefix == "document":
            query = query.filter(~Artifact.mime_type.like("image/%"))

        if source_kind in ("user_upload", "ai_generated"):
            # Dialect-portable JSON path extraction on the Artifact.extra_data
            # (DB column name "metadata"): JSONB on PostgreSQL, JSON on SQLite.
            dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
            if dialect == "postgresql":
                json_source = func.jsonb_extract_path_text(Artifact.extra_data, "source")
            else:
                json_source = func.json_extract(Artifact.extra_data, "$.source")

            if source_kind == "user_upload":
                query = query.filter(json_source == "user_upload")
            else:
                # ai_generated = anything that is NOT explicitly user_upload,
                # including NULL / missing source metadata (e.g. backfill).
                query = query.filter(
                    or_(json_source.is_(None), json_source != "user_upload")
                )

        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.filter(
                or_(
                    Artifact.filename.ilike(like_pattern),
                    Artifact.title.ilike(like_pattern),
                )
            )

        total = query.count()
        rows = query.order_by(desc(Artifact.created_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        items = []
        for artifact, chat_title in rows:
            items.append({
                "artifact": artifact,
                "chat_title": chat_title,
            })
        return items, total

    def soft_delete(self, artifact_id: str, user_id: str) -> bool:
        """Soft delete an artifact (set deleted_at)."""
        artifact = self.db.query(Artifact).filter(
            Artifact.artifact_id == artifact_id,
            Artifact.user_id == user_id,
            Artifact.deleted_at.is_(None),
        ).first()
        if not artifact:
            return False
        artifact.deleted_at = datetime.utcnow()
        self.db.commit()
        return True

    def create(self, artifact_data: Dict[str, Any]) -> Artifact:
        """Create a new artifact."""
        artifact = Artifact(**artifact_data)
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        return artifact


class AuditLogRepository:
    """Repository for audit log operations."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, log_data: Dict[str, Any]) -> AuditLog:
        """Create a new audit log entry."""
        log = AuditLog(**log_data)
        self.db.add(log)
        try:
            self.db.commit()
            self.db.refresh(log)
        except Exception:
            # Audit should not block the main business flow in local/dev setups.
            self.db.rollback()
        return log

    def list_by_user(
        self,
        user_id: str,
        action: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> tuple[List[AuditLog], int]:
        """List audit logs for a user."""
        query = self.db.query(AuditLog).filter(
            AuditLog.user_id == user_id
        )

        if action:
            query = query.filter(AuditLog.action == action)

        total = query.count()
        logs = query.order_by(desc(AuditLog.created_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return logs, total

    def list_by_trace_id(self, trace_id: str) -> List[AuditLog]:
        """Get all logs for a specific trace ID."""
        return self.db.query(AuditLog).filter(
            AuditLog.trace_id == trace_id
        ).order_by(AuditLog.created_at).all()

    def get_by_id(self, log_id: int) -> Optional[AuditLog]:
        """Get audit log by ID."""
        return self.db.query(AuditLog).filter(AuditLog.log_id == log_id).first()

    def list_with_filters(
        self,
        user_id: str,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50
    ) -> tuple[List[AuditLog], int]:
        """List audit logs with multiple filters."""
        query = self.db.query(AuditLog).filter(AuditLog.user_id == user_id)

        if action:
            query = query.filter(AuditLog.action == action)
        if resource_type:
            query = query.filter(AuditLog.resource_type == resource_type)
        if start_date:
            query = query.filter(AuditLog.created_at >= start_date)
        if end_date:
            query = query.filter(AuditLog.created_at <= end_date)

        total = query.count()
        logs = query.order_by(desc(AuditLog.created_at)).offset(
            (page - 1) * page_size
        ).limit(page_size).all()

        return logs, total

    def get_user_stats(self, user_id: str, days: int = 7) -> Dict[str, Any]:
        """Get audit statistics for a user."""
        from datetime import timedelta

        start_date = datetime.utcnow() - timedelta(days=days)

        query = self.db.query(AuditLog).filter(
            AuditLog.user_id == user_id,
            AuditLog.created_at >= start_date
        )

        # Total actions
        total = query.count()

        # Failed actions
        failed = query.filter(AuditLog.status == "failed").count()

        # Actions by type
        actions_by_type = {}
        action_groups = self.db.query(
            AuditLog.action, func.count(AuditLog.log_id)
        ).filter(
            AuditLog.user_id == user_id,
            AuditLog.created_at >= start_date
        ).group_by(AuditLog.action).all()

        for action, count in action_groups:
            actions_by_type[action] = count

        # Most active day
        daily_counts = self.db.query(
            func.date(AuditLog.created_at).label('date'),
            func.count(AuditLog.log_id).label('count')
        ).filter(
            AuditLog.user_id == user_id,
            AuditLog.created_at >= start_date
        ).group_by(func.date(AuditLog.created_at)).order_by(desc('count')).first()

        most_active_day = None
        if daily_counts:
            most_active_day = {
                'date': daily_counts.date.isoformat() if hasattr(daily_counts.date, 'isoformat') else str(daily_counts.date),
                'count': daily_counts.count
            }

        return {
            'period_days': days,
            'total_actions': total,
            'failed_actions': failed,
            'success_rate': round((total - failed) / total * 100, 2) if total > 0 else 0,
            'actions_by_type': actions_by_type,
            'most_active_day': most_active_day
        }


class UserAgentRepository:
    """Repository for user agent (sub-agent) operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, agent_id: str) -> Optional[UserAgent]:
        return self.db.query(UserAgent).filter(UserAgent.agent_id == agent_id).first()

    def list_for_user(self, user_id: str) -> List[UserAgent]:
        """Return all agents visible to a user: enabled admin agents + user's own agents."""
        return self.db.query(UserAgent).filter(
            or_(
                and_(UserAgent.owner_type == "admin", UserAgent.is_enabled == True),
                and_(UserAgent.owner_type == "user", UserAgent.user_id == user_id),
            )
        ).order_by(UserAgent.owner_type.desc(), UserAgent.sort_order, UserAgent.created_at).all()

    def list_admin(self) -> List[UserAgent]:
        """Return all admin-owned agents."""
        return self.db.query(UserAgent).filter(
            UserAgent.owner_type == "admin"
        ).order_by(UserAgent.sort_order, UserAgent.created_at).all()

    def count_user_agents(self, user_id: str) -> int:
        """Count agents owned by a specific user."""
        return self.db.query(func.count(UserAgent.agent_id)).filter(
            UserAgent.owner_type == "user",
            UserAgent.user_id == user_id,
        ).scalar() or 0

    def create(self, data: Dict[str, Any]) -> UserAgent:
        agent = UserAgent(**data)
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def update(self, agent_id: str, data: Dict[str, Any]) -> Optional[UserAgent]:
        agent = self.get_by_id(agent_id)
        if not agent:
            return None
        for key, value in data.items():
            setattr(agent, key, value)
        agent.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete(self, agent_id: str) -> bool:
        agent = self.get_by_id(agent_id)
        if not agent:
            return False
        self.db.delete(agent)
        self.db.commit()
        return True
