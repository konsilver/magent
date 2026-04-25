"""Database backend for admin-managed skills stored in PostgreSQL."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .protocol import SkillFileInfo

# Sentinel path used when skill content comes from DB (never actually read)
_DB_SENTINEL = Path("/db/admin_skills")


class DatabaseBackend:
    """Loads admin skills from PostgreSQL instead of the filesystem.

    Session lifecycle: each method opens and closes its own session to avoid
    holding connections across the global loader's lifetime (which is not
    compatible with FastAPI's dependency-injected sessions).
    """

    def __init__(self, priority: int = 75):
        self._priority = priority

    @property
    def source_name(self) -> str:
        return "admin"

    @property
    def priority(self) -> int:
        return self._priority

    def _get_session_and_model(self):
        """Lazily import DB session and model to avoid startup-time DB connection."""
        from core.db.engine import SessionLocal
        from core.db.models import AdminSkill
        return SessionLocal, AdminSkill

    def list_skill_files(self) -> List[SkillFileInfo]:
        """List all enabled admin skills from the database."""
        SessionLocal, AdminSkill = self._get_session_and_model()
        db = SessionLocal()
        try:
            rows = db.query(AdminSkill).filter(AdminSkill.is_enabled == True).all()
            result = []
            for row in rows:
                result.append(SkillFileInfo(
                    skill_id=row.skill_id,
                    file_path=_DB_SENTINEL / row.skill_id / "SKILL.md",
                    source_name=self.source_name,
                    priority=self._priority,
                    content=row.skill_content,
                ))
            return result
        finally:
            db.close()

    def read_skill_file(self, skill_id: str) -> str:
        """Read raw SKILL.md content from DB."""
        SessionLocal, AdminSkill = self._get_session_and_model()
        db = SessionLocal()
        try:
            row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
            if row is None:
                raise FileNotFoundError(f"Admin skill not found in DB: {skill_id}")
            return row.skill_content
        finally:
            db.close()

    def get_extra_files(self, skill_id: str) -> dict:
        """Return {filename: content} or empty dict for a skill's extra files."""
        SessionLocal, AdminSkill = self._get_session_and_model()
        db = SessionLocal()
        try:
            row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
            if row is None or not row.extra_files:
                return {}
            return dict(row.extra_files)
        finally:
            db.close()

    def exists(self, skill_id: str) -> bool:
        """Check if an admin skill exists in the database."""
        SessionLocal, AdminSkill = self._get_session_and_model()
        db = SessionLocal()
        try:
            count = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).count()
            return count > 0
        finally:
            db.close()
