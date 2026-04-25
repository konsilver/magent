"""User-related business logic."""

from typing import Optional, Dict, Any
from datetime import datetime
import uuid
from sqlalchemy.orm import Session

from core.db.repository import UserRepository, AuditLogRepository
from core.db.models import UserShadow


class UserService:
    """Service for user-related operations."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = UserRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def get_or_create_user_shadow(
        self,
        user_center_id: str,
        username: str,
        email: Optional[str] = None,
        avatar_url: Optional[str] = None
    ) -> UserShadow:
        """
        Lazy load user shadow from user center.
        Creates shadow if not exists, updates if exists.
        """
        user = self.repo.get_by_user_center_id(user_center_id)

        if user:
            # Update existing user
            update_data = {
                "username": username,
                "email": email,
                "avatar_url": avatar_url,
                "last_sync_at": datetime.utcnow()
            }
            return self.repo.update(user.user_id, update_data)
        else:
            # Create new user shadow
            user_data = {
                "user_id": f"user_{uuid.uuid4().hex[:16]}",
                "user_center_id": user_center_id,
                "username": username,
                "email": email,
                "avatar_url": avatar_url,
                "last_sync_at": datetime.utcnow()
            }
            user = self.repo.create(user_data)

            # Audit log
            self.audit_repo.create({
                "user_id": user.user_id,
                "action": "user.created",
                "resource_type": "user",
                "resource_id": user.user_id,
                "status": "success"
            })

            return user


    def get_user_settings(self, user_id: str) -> Dict[str, Any]:
        """Read memory/preferences from users_shadow.metadata JSONB."""
        user = self.repo.get_by_id(user_id)
        if not user:
            return {}
        return dict(user.extra_data) if user.extra_data else {}

    def update_user_metadata(self, user_id: str, patch: Dict[str, Any]) -> None:
        """Merge `patch` into users_shadow.metadata JSONB (shallow merge)."""
        user = self.repo.get_by_id(user_id)
        if not user:
            return
        current = dict(user.extra_data) if user.extra_data else {}
        current.update(patch)
        user.extra_data = current
        user.updated_at = datetime.utcnow()
        self.db.commit()
