"""Artifact management business logic."""

from typing import Optional, Dict, Any
import uuid
from sqlalchemy.orm import Session

from core.db.repository import ArtifactRepository


class ArtifactService:
    """Service for artifact management."""

    def __init__(self, db: Session):
        self.db = db
        self.repo = ArtifactRepository(db)

    def create_artifact(
        self,
        user_id: str,
        artifact_type: str,
        title: str,
        filename: str,
        size_bytes: int,
        mime_type: str,
        storage_key: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new artifact."""
        artifact_data = {
            "artifact_id": f"artifact_{uuid.uuid4().hex[:16]}",
            "user_id": user_id,
            "chat_id": chat_id,
            "type": artifact_type,
            "title": title,
            "filename": filename,
            "size_bytes": size_bytes,
            "mime_type": mime_type,
            "storage_key": storage_key
        }

        artifact = self.repo.create(artifact_data)

        return {
            "artifact_id": artifact.artifact_id,
            "type": artifact.type,
            "title": artifact.title,
            "filename": artifact.filename,
            "created_at": artifact.created_at.isoformat()
        }

    def get_artifact(self, artifact_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get artifact with ownership check."""
        artifact = self.repo.get_by_id(artifact_id)

        if not artifact or artifact.user_id != user_id:
            return None

        return {
            "artifact_id": artifact.artifact_id,
            "type": artifact.type,
            "title": artifact.title,
            "filename": artifact.filename,
            "storage_key": artifact.storage_key,
            "size_bytes": artifact.size_bytes,
            "mime_type": artifact.mime_type,
            "created_at": artifact.created_at.isoformat()
        }
