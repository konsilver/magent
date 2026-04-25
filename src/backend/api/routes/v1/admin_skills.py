"""Admin skill management API routes.

Provides CRUD for agent skills managed via the admin backend.
Skills are stored in PostgreSQL (admin_skills table).
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from api.deps import require_admin
from core.db.engine import get_db
from agent_skills.loader import get_skill_loader
from agent_skills.registry import (
    AgentSkillMetadata,
    _load_skill_metadata_from_str,
    _split_frontmatter,
    SkillSpecError,
)
from configs.catalog import get_enabled_ids, set_enabled as catalog_set_enabled
from configs.catalog_loader import invalidate_catalog_cache
from core.db.models import AdminSkill
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/admin/skills", tags=["Admin Skills"])
logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _refresh_caches():
    """Refresh skill loader and catalog caches after mutation."""
    get_skill_loader(reset=True)
    invalidate_catalog_cache()
    # Also invalidate per-user capability cache so name/description changes
    # are visible immediately for all users.
    try:
        from core.config.catalog_resolver import invalidate_capability_cache
        invalidate_capability_cache()
    except Exception:
        pass
    # Invalidate prompt cache because skill changes affect the tool routing
    # table injected into the system prompt.
    try:
        from prompts.prompt_runtime import invalidate_prompt_cache
        invalidate_prompt_cache()
    except Exception:
        pass
    # Eagerly re-sync catalog.json so the file is up-to-date before the next
    # frontend request, avoiding a stale-catalog window.
    try:
        from configs.catalog_loader import load_catalog
        load_catalog(include_runtime_details=False)
    except Exception as _e:
        logger.warning("Failed to eagerly sync catalog after skill mutation: %s", _e)


def _serialize_metadata(meta: AgentSkillMetadata) -> Dict[str, Any]:
    """Convert AgentSkillMetadata to API response dict."""
    source = "unknown"
    if meta.skill_path and ":" in meta.skill_path:
        source = meta.skill_path.split(":", 1)[0]
    return {
        "id": meta.id,
        "name": meta.name,
        "description": meta.description,
        "version": meta.version,
        "tags": meta.tags,
        "allowed_tools": meta.allowed_tools,
        "source": source,
    }


def _sanitize_fm_value(value: str) -> str:
    """Sanitize a frontmatter value to prevent format corruption."""
    # Replace newlines with spaces to keep frontmatter single-line per field
    return (value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()


def _build_skill_content(
    skill_id: str,
    display_name: str,
    description: str,
    version: str,
    tags: List[str],
    allowed_tools: List[str],
    instructions: str,
) -> str:
    """Build SKILL.md content string from fields."""
    fm_lines = [
        "---",
        f"name: {skill_id}",
        f"display_name: {_sanitize_fm_value(display_name)}",
        f"description: {_sanitize_fm_value(description)}",
        f"version: {_sanitize_fm_value(version)}",
    ]
    if tags:
        fm_lines.append(f"tags: {', '.join(tags)}")
    if allowed_tools:
        fm_lines.append(f"allowed_tools: {' '.join(allowed_tools)}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(instructions)
    fm_lines.append("")
    return "\n".join(fm_lines)


# ── Request schemas ─────────────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9_-]{1,63}$", description="Skill ID (lowercase, digits, hyphens, underscores)")
    display_name: str = Field(..., description="Display name")
    description: str = Field(..., description="Skill description")
    version: str = Field(default="1.0.0", description="Version string")
    tags: List[str] = Field(default_factory=list, description="Tags")
    allowed_tools: List[str] = Field(default_factory=list, description="Allowed tools")
    instructions: str = Field(..., description="Instructions (markdown)")


class SkillImportRequest(BaseModel):
    skills: List[Dict[str, Any]] = Field(..., description="Array of skill objects to import")
    overwrite: bool = Field(True, description="Overwrite existing skills")


class SkillToggleRequest(BaseModel):
    is_enabled: bool = Field(..., description="Whether to enable the skill globally")


class SkillUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    tags: Optional[List[str]] = None
    allowed_tools: Optional[List[str]] = None
    instructions: Optional[str] = None
    is_enabled: Optional[bool] = None


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_admin)])
async def list_skills():
    """List all skills from all sources with source info and enabled status."""
    loader = get_skill_loader()
    all_metadata = loader.load_all_metadata()
    enabled_ids = set(get_enabled_ids("skills"))
    items = []
    for m in all_metadata.values():
        d = _serialize_metadata(m)
        d["is_enabled"] = d["id"] in enabled_ids
        items.append(d)
    # Sort: admin first, then built-in, then others
    source_order = {"admin": 0, "built-in": 1, "user": 2, "project": 3}
    items.sort(key=lambda x: (source_order.get(x["source"], 99), x["id"]))
    return success_response(data=items)


@router.get("/export", dependencies=[Depends(require_admin)])
async def export_skills(db: Session = Depends(get_db)):
    """Export all admin skills as a JSON array."""
    rows = db.query(AdminSkill).order_by(AdminSkill.skill_id).all()
    items = []
    for r in rows:
        items.append({
            "skill_id": r.skill_id,
            "skill_content": r.skill_content,
            "display_name": r.display_name,
            "description": r.description,
            "version": r.version,
            "tags": r.tags or [],
            "allowed_tools": r.allowed_tools or [],
            "extra_files": r.extra_files or {},
            "is_enabled": r.is_enabled,
        })
    return success_response(data=items)


@router.post("/import", dependencies=[Depends(require_admin)])
async def import_skills(req: SkillImportRequest, db: Session = Depends(get_db)):
    """Import skills from a JSON array. Upserts each skill."""
    created = 0
    updated = 0
    now = datetime.utcnow()
    for item in req.skills:
        sid = item.get("skill_id")
        if not sid:
            continue
        existing = db.query(AdminSkill).filter(AdminSkill.skill_id == sid).first()
        if existing and not req.overwrite:
            continue
        if existing:
            existing.skill_content = item.get("skill_content", existing.skill_content)
            existing.display_name = item.get("display_name", existing.display_name)
            existing.description = item.get("description", existing.description)
            existing.version = item.get("version", existing.version)
            existing.tags = item.get("tags", existing.tags)
            existing.allowed_tools = item.get("allowed_tools", existing.allowed_tools)
            existing.extra_files = item.get("extra_files", existing.extra_files)
            if "is_enabled" in item:
                existing.is_enabled = item["is_enabled"]
            existing.updated_at = now
            flag_modified(existing, "tags")
            flag_modified(existing, "allowed_tools")
            flag_modified(existing, "extra_files")
            updated += 1
        else:
            row = AdminSkill(
                skill_id=sid,
                skill_content=item.get("skill_content", ""),
                display_name=item.get("display_name", sid),
                description=item.get("description", ""),
                version=item.get("version", "1.0.0"),
                tags=item.get("tags", []),
                allowed_tools=item.get("allowed_tools", []),
                extra_files=item.get("extra_files", {}),
                is_enabled=item.get("is_enabled", True),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created += 1
    db.commit()
    _refresh_caches()
    logger.info("admin_skills_imported: created=%d updated=%d", created, updated)
    return success_response(data={"created": created, "updated": updated, "message": "Import complete"})


@router.get("/{skill_id}", dependencies=[Depends(require_admin)])
async def get_skill(skill_id: str, db: Session = Depends(get_db)):
    """Get skill detail including instructions."""
    loader = get_skill_loader()
    spec = loader.load_skill_full(skill_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    source = "unknown"
    if spec.skill_path and ":" in spec.skill_path:
        source = spec.skill_path.split(":", 1)[0]
    # Fetch extra_files metadata (works for all backends: admin DB, filesystem, etc.)
    extra_files_list: List[Dict[str, Any]] = []
    try:
        ef = loader.get_extra_files(skill_id)
        extra_files_list = [
            {"filename": fn, "size": len(content)}
            for fn, content in ef.items()
        ]
    except Exception:
        pass

    # For admin skills, extract the full body text from skill_content
    # so that rich content (code blocks, multiple sections) is preserved.
    instructions_raw = ""
    if source == "admin":
        try:
            row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
            if row and row.skill_content:
                _, body = _split_frontmatter(row.skill_content)
                instructions_raw = body.strip()
        except Exception:
            pass

    return success_response(data={
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "version": spec.version,
        "tags": spec.tags,
        "allowed_tools": spec.allowed_tools,
        "instructions": spec.instructions,
        "instructions_raw": instructions_raw,
        "inputs": spec.inputs,
        "outputs": spec.outputs,
        "source": source,
        "extra_files": extra_files_list,
    })


@router.post("", dependencies=[Depends(require_admin)])
async def create_skill(req: SkillCreateRequest, db: Session = Depends(get_db)):
    """Create a skill from form data. Stores to DB."""
    # 409 conflict check
    existing = db.query(AdminSkill).filter(AdminSkill.skill_id == req.name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Skill already exists: {req.name}")

    content = _build_skill_content(
        skill_id=req.name,
        display_name=req.display_name,
        description=req.description,
        version=req.version,
        tags=req.tags,
        allowed_tools=req.allowed_tools,
        instructions=req.instructions,
    )

    row = AdminSkill(
        skill_id=req.name,
        skill_content=content,
        display_name=req.display_name,
        description=req.description,
        version=req.version,
        tags=req.tags,
        allowed_tools=req.allowed_tools,
        is_enabled=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()

    _refresh_caches()
    logger.info("admin_skill_created: %s", req.name)
    return success_response(data={"id": req.name, "message": "Skill created"})


@router.put("/{skill_id}", dependencies=[Depends(require_admin)])
async def update_skill(skill_id: str, req: SkillUpdateRequest, db: Session = Depends(get_db)):
    """Partial update of an admin skill."""
    row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    # Apply updates to mutable fields
    if req.display_name is not None:
        row.display_name = req.display_name
    if req.description is not None:
        row.description = req.description
    if req.version is not None:
        row.version = req.version
    if req.tags is not None:
        row.tags = req.tags
    if req.allowed_tools is not None:
        row.allowed_tools = req.allowed_tools
    if req.is_enabled is not None:
        row.is_enabled = req.is_enabled

    # Rebuild skill_content to keep it in sync
    # Extract current instructions from existing content if not updated
    if req.instructions is not None:
        instructions = req.instructions
    else:
        try:
            _, body = _split_frontmatter(row.skill_content)
            instructions = body.strip()
        except Exception:
            instructions = ""

    row.skill_content = _build_skill_content(
        skill_id=skill_id,
        display_name=row.display_name,
        description=row.description,
        version=row.version,
        tags=row.tags or [],
        allowed_tools=row.allowed_tools or [],
        instructions=instructions,
    )
    row.updated_at = datetime.utcnow()
    db.commit()

    _refresh_caches()
    logger.info("admin_skill_updated: %s", skill_id)
    return success_response(data={"id": skill_id, "message": "Skill updated"})


@router.post("/upload", dependencies=[Depends(require_admin)])
async def upload_skill(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a skill folder as a zip file. Upserts into DB."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

    # Security: check for zip-slip
    for name in zf.namelist():
        if name.startswith("/") or ".." in name:
            raise HTTPException(status_code=400, detail=f"Unsafe path in zip: {name}")

    # Find SKILL.md in zip
    skill_md_paths = [n for n in zf.namelist() if n.endswith("SKILL.md")]
    if not skill_md_paths:
        raise HTTPException(status_code=400, detail="No SKILL.md found in zip")

    skill_md_path = skill_md_paths[0]
    parts = skill_md_path.split("/")
    if len(parts) == 1:
        prefix = ""
    elif len(parts) == 2:
        prefix = parts[0] + "/"
    else:
        prefix = "/".join(parts[:-1]) + "/"

    # Parse SKILL.md to get skill_id and validate
    try:
        raw = zf.read(skill_md_path).decode("utf-8")
        fm, _ = _split_frontmatter(raw)
        skill_id = fm.get("name", "").strip()
        if not skill_id:
            if prefix:
                skill_id = prefix.rstrip("/").split("/")[-1]
            else:
                raise HTTPException(status_code=400, detail="SKILL.md missing 'name' in frontmatter")
    except SkillSpecError as e:
        raise HTTPException(status_code=400, detail=f"Invalid SKILL.md: {e}")

    # Validate by parsing metadata
    try:
        meta = _load_skill_metadata_from_str(raw, skill_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid skill: {e}")

    # ── Extract extra text files from zip ──────────────────────────────
    TEXT_EXTENSIONS = {
        ".md", ".txt", ".json", ".py", ".yaml", ".yml", ".toml", ".cfg",
        ".ini", ".csv", ".xml", ".html", ".css", ".js", ".ts", ".sh", ".conf",
    }
    MAX_SINGLE_FILE = 1 * 1024 * 1024   # 1 MB
    MAX_TOTAL = 10 * 1024 * 1024         # 10 MB
    MAX_FILES = 50

    extra_files: Dict[str, str] = {}
    total_size = 0
    for entry in zf.namelist():
        # Skip SKILL.md itself, directories, and hidden files
        if entry == skill_md_path or entry.endswith("/") or "/__" in entry:
            continue
        # Must be under the same prefix
        if prefix and not entry.startswith(prefix):
            continue
        # Check extension whitelist
        _, ext = os.path.splitext(entry)
        if ext.lower() not in TEXT_EXTENSIONS:
            continue
        info = zf.getinfo(entry)
        if info.file_size > MAX_SINGLE_FILE:
            continue
        total_size += info.file_size
        if total_size > MAX_TOTAL:
            break
        if len(extra_files) >= MAX_FILES:
            break
        try:
            content = zf.read(entry).decode("utf-8")
        except (UnicodeDecodeError, KeyError):
            continue
        # Store relative to prefix
        rel_name = entry[len(prefix):] if prefix else entry
        extra_files[rel_name] = content

    # Upsert into DB
    now = datetime.utcnow()
    existing = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if existing is not None:
        existing.skill_content = raw
        existing.display_name = meta.name
        existing.description = meta.description
        existing.version = meta.version
        existing.tags = meta.tags
        existing.allowed_tools = meta.allowed_tools
        existing.extra_files = extra_files
        existing.is_enabled = True
        existing.updated_at = now
        flag_modified(existing, "extra_files")
    else:
        row = AdminSkill(
            skill_id=skill_id,
            skill_content=raw,
            display_name=meta.name,
            description=meta.description,
            version=meta.version,
            tags=meta.tags,
            allowed_tools=meta.allowed_tools,
            extra_files=extra_files,
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    db.commit()

    _refresh_caches()
    logger.info("admin_skill_uploaded: %s", skill_id)
    return success_response(data={"id": skill_id, "message": "Skill uploaded"})


# ── Toggle enabled/disabled ────────────────────────────────────────────────

@router.put("/{skill_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_skill(skill_id: str, req: SkillToggleRequest):
    """Globally enable or disable a skill (any source).

    Only updates catalog.json enabled flag.  AdminSkill.is_enabled in DB
    is NOT touched — that field controls whether the skill appears in
    load_all_metadata(); setting it to false would make the skill vanish
    entirely instead of just being disabled in the catalog.
    """
    # Verify skill exists
    loader = get_skill_loader()
    meta = loader.load_all_metadata().get(skill_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    # Update catalog.json only
    catalog_set_enabled("skills", skill_id, req.is_enabled)

    _refresh_caches()
    logger.info("admin_skill_toggled: %s → %s", skill_id, req.is_enabled)
    return success_response(data={
        "id": skill_id,
        "is_enabled": req.is_enabled,
        "message": f"Skill {'enabled' if req.is_enabled else 'disabled'}",
    })


# ── File CRUD ─────────────────────────────────────────────────────────────

class FileUpdateRequest(BaseModel):
    content: str = Field(..., description="File content")


@router.get("/{skill_id}/files/{filename:path}", dependencies=[Depends(require_admin)])
async def get_skill_file(skill_id: str, filename: str, db: Session = Depends(get_db)):
    """Read a single extra file from an admin skill."""
    row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    extra = row.extra_files or {}
    if filename not in extra:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return success_response(data={"filename": filename, "content": extra[filename]})


@router.put("/{skill_id}/files/{filename:path}", dependencies=[Depends(require_admin)])
async def update_skill_file(
    skill_id: str, filename: str, req: FileUpdateRequest, db: Session = Depends(get_db)
):
    """Create or update an extra file in an admin skill."""
    row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    extra = dict(row.extra_files or {})
    extra[filename] = req.content
    row.extra_files = extra
    row.updated_at = datetime.utcnow()
    flag_modified(row, "extra_files")
    db.commit()
    _refresh_caches()
    return success_response(data={"filename": filename, "message": "File saved"})


@router.delete("/{skill_id}/files/{filename:path}", dependencies=[Depends(require_admin)])
async def delete_skill_file(skill_id: str, filename: str, db: Session = Depends(get_db)):
    """Delete an extra file from an admin skill."""
    row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    extra = dict(row.extra_files or {})
    if filename not in extra:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    del extra[filename]
    row.extra_files = extra
    row.updated_at = datetime.utcnow()
    flag_modified(row, "extra_files")
    db.commit()
    _refresh_caches()
    return success_response(data={"filename": filename, "message": "File deleted"})


@router.post("/{skill_id}/fork", dependencies=[Depends(require_admin)])
async def fork_skill(skill_id: str, db: Session = Depends(get_db)):
    """Fork a built-in skill into the admin DB for editing.

    Copies the full SKILL.md content and extra files from the filesystem
    backend into an AdminSkill record. Returns 409 if an admin version
    already exists.
    """
    loader = get_skill_loader()
    source = loader.get_skill_source(skill_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    if source == "admin":
        raise HTTPException(status_code=409, detail=f"Admin version already exists: {skill_id}")

    # Check DB doesn't already have it
    existing = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Admin version already exists: {skill_id}")

    # Load full spec from filesystem
    spec = loader.load_skill_full(skill_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Could not load skill: {skill_id}")

    # Read raw SKILL.md content from filesystem
    skill_info = loader._backend.get_skill_info(skill_id)
    if skill_info is None:
        raise HTTPException(status_code=404, detail=f"Could not find skill info: {skill_id}")

    if skill_info.content is not None:
        raw_content = skill_info.content
    else:
        try:
            raw_content = skill_info.file_path.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read skill file: {e}")

    # Get extra files
    extra_files: Dict[str, str] = {}
    try:
        extra_files = loader.get_extra_files(skill_id)
    except Exception:
        pass

    # Parse metadata from content
    try:
        meta = _load_skill_metadata_from_str(raw_content, skill_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse skill metadata: {e}")

    # Create admin skill record
    now = datetime.utcnow()
    row = AdminSkill(
        skill_id=skill_id,
        skill_content=raw_content,
        display_name=meta.name,
        description=meta.description,
        version=meta.version,
        tags=meta.tags,
        allowed_tools=meta.allowed_tools,
        extra_files=extra_files,
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()

    _refresh_caches()
    logger.info("admin_skill_forked: %s (from %s)", skill_id, source)
    return success_response(data={"id": skill_id, "source": source, "message": "Skill forked to admin"})


@router.delete("/{skill_id}", dependencies=[Depends(require_admin)])
async def delete_skill(skill_id: str, db: Session = Depends(get_db)):
    """Delete an admin skill from DB."""
    # Verify skill exists and is from admin source
    loader = get_skill_loader()
    source = loader.get_skill_source(skill_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    if source != "admin":
        raise HTTPException(status_code=403, detail=f"Cannot delete {source} skill, only admin skills can be deleted")

    row = db.query(AdminSkill).filter(AdminSkill.skill_id == skill_id).first()
    if row is not None:
        db.delete(row)
        db.commit()

    _refresh_caches()
    logger.info("admin_skill_deleted: %s", skill_id)
    return success_response(data={"id": skill_id, "message": "Skill deleted"})
