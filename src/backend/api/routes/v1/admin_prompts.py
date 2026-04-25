"""Admin prompt management API routes.

Provides CRUD for system prompt parts managed via the admin backend.
DB records override filesystem prompt files; deleting a DB record
restores the filesystem version.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.db.models import AdminPromptPart, AdminPromptVersion
from core.infra.responses import success_response
from prompts.prompt_config import load_prompt_config
from prompts.provider import FilesystemPromptProvider, _FILE_CONTENT_CACHE

router = APIRouter(prefix="/v1/admin/prompts", tags=["Admin Prompts"])
logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _invalidate_prompt_cache():
    """Clear prompt caches so changes take effect immediately."""
    from prompts.prompt_runtime import invalidate_prompt_cache
    invalidate_prompt_cache()
    _FILE_CONTENT_CACHE.clear()


def _resolve_prompt_dir() -> Path:
    """Resolve the filesystem prompt directory from config."""
    config = load_prompt_config()
    raw = getattr(config.system_prompt, "prompt_dir", None) or "./prompts/prompt_text/v1"
    from prompts.prompt_runtime import _resolve_prompt_dir
    return _resolve_prompt_dir(raw)


def _load_fs_parts() -> Dict[str, Dict[str, Any]]:
    """Load prompt parts from the filesystem based on config."""
    config = load_prompt_config()
    parts_list = config.system_prompt.parts or []
    prompt_dir = _resolve_prompt_dir()

    fs_parts: Dict[str, Dict[str, Any]] = {}
    for idx, part_id in enumerate(parts_list):
        part_id = part_id.strip()
        if not part_id:
            continue
        # Try to read from filesystem
        provider = FilesystemPromptProvider(prompt_dir=prompt_dir, strict_vars=False)
        content = provider.get_prompt(part_id, "system", vars={})
        # Generate display name from part_id
        # e.g. "system/00_role" -> "00_role"
        display = part_id.split("/")[-1] if "/" in part_id else part_id
        fs_parts[part_id] = {
            "part_id": part_id,
            "content": content,
            "display_name": display,
            "sort_order": idx * 10,
            "is_enabled": True,
            "source": "file",
        }
    return fs_parts


# ── Request schemas ─────────────────────────────────────────────────────────

class PartUpsertRequest(BaseModel):
    content: str = Field(..., description="Markdown content")
    display_name: str = Field(..., description="Display name")
    sort_order: int = Field(0, description="Sort order")
    is_enabled: bool = Field(True, description="Enabled flag")


class OrderUpdateRequest(BaseModel):
    order: List[Dict[str, Any]] = Field(..., description="List of {part_id, sort_order}")


class PromptImportRequest(BaseModel):
    parts: List[Dict[str, Any]] = Field(..., description="Array of prompt part objects to import")
    overwrite: bool = Field(True, description="Overwrite existing parts")


class PreviewRequest(BaseModel):
    pass  # No body needed, uses current state


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/parts", dependencies=[Depends(require_config)])
async def list_parts(db: Session = Depends(get_db)):
    """List all prompt parts, merging DB overrides with filesystem."""
    # Start with filesystem parts
    merged = _load_fs_parts()

    # Overlay DB records
    db_rows = db.query(AdminPromptPart).all()
    for row in db_rows:
        merged[row.part_id] = {
            "part_id": row.part_id,
            "content": row.content,
            "display_name": row.display_name,
            "sort_order": row.sort_order,
            "is_enabled": row.is_enabled,
            "source": "database",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "created_by": row.created_by,
        }

    # Sort by sort_order
    items = sorted(merged.values(), key=lambda x: x["sort_order"])
    return success_response(data=items)


@router.get("/export", dependencies=[Depends(require_config)])
async def export_prompts(db: Session = Depends(get_db)):
    """Export all DB prompt parts as a JSON array."""
    rows = db.query(AdminPromptPart).order_by(AdminPromptPart.sort_order).all()
    items = []
    for r in rows:
        items.append({
            "part_id": r.part_id,
            "content": r.content,
            "display_name": r.display_name,
            "sort_order": r.sort_order,
            "is_enabled": r.is_enabled,
        })
    return success_response(data=items)


@router.post("/import", dependencies=[Depends(require_config)])
async def import_prompts(req: PromptImportRequest, db: Session = Depends(get_db)):
    """Import prompt parts from a JSON array. Upserts each part."""
    created = 0
    updated = 0
    now = datetime.utcnow()
    for item in req.parts:
        pid = item.get("part_id")
        if not pid:
            continue
        existing = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == pid).first()
        if existing and not req.overwrite:
            continue
        if existing:
            # Snapshot before overwrite
            version = AdminPromptVersion(
                part_id=pid,
                content=existing.content,
                display_name=existing.display_name,
                sort_order=existing.sort_order,
                is_enabled=existing.is_enabled,
                created_at=now,
                created_by=existing.created_by,
            )
            db.add(version)
            existing.content = item.get("content", existing.content)
            existing.display_name = item.get("display_name", existing.display_name)
            existing.sort_order = item.get("sort_order", existing.sort_order)
            if "is_enabled" in item:
                existing.is_enabled = item["is_enabled"]
            existing.updated_at = now
            updated += 1
        else:
            row = AdminPromptPart(
                part_id=pid,
                content=item.get("content", ""),
                display_name=item.get("display_name", pid.split("/")[-1]),
                sort_order=item.get("sort_order", 0),
                is_enabled=item.get("is_enabled", True),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created += 1
    db.commit()
    _invalidate_prompt_cache()
    logger.info("admin_prompts_imported: created=%d updated=%d", created, updated)
    return success_response(data={"created": created, "updated": updated, "message": "Import complete"})


@router.get("/parts/{part_id:path}", dependencies=[Depends(require_config)])
async def get_part(part_id: str, db: Session = Depends(get_db)):
    """Get a single prompt part with both DB and filesystem versions."""
    # DB version
    row = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == part_id).first()
    db_data = None
    if row:
        db_data = {
            "part_id": row.part_id,
            "content": row.content,
            "display_name": row.display_name,
            "sort_order": row.sort_order,
            "is_enabled": row.is_enabled,
            "source": "database",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "created_by": row.created_by,
        }

    # Filesystem version
    fs_parts = _load_fs_parts()
    fs_data = fs_parts.get(part_id)

    if not db_data and not fs_data:
        raise HTTPException(status_code=404, detail=f"Part not found: {part_id}")

    return success_response(data={
        "current": db_data or fs_data,
        "filesystem_content": fs_data["content"] if fs_data else None,
    })


@router.put("/parts/{part_id:path}", dependencies=[Depends(require_config)])
async def upsert_part(part_id: str, req: PartUpsertRequest, db: Session = Depends(get_db)):
    """Create or update a prompt part in the database."""
    row = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == part_id).first()
    now = datetime.utcnow()
    if row:
        # Snapshot the old content as a version before overwriting
        version = AdminPromptVersion(
            part_id=part_id,
            content=row.content,
            display_name=row.display_name,
            sort_order=row.sort_order,
            is_enabled=row.is_enabled,
            created_at=now,
            created_by=row.created_by,
        )
        db.add(version)
        row.content = req.content
        row.display_name = req.display_name
        row.sort_order = req.sort_order
        row.is_enabled = req.is_enabled
        row.updated_at = now
    else:
        row = AdminPromptPart(
            part_id=part_id,
            content=req.content,
            display_name=req.display_name,
            sort_order=req.sort_order,
            is_enabled=req.is_enabled,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    db.commit()

    _invalidate_prompt_cache()
    logger.info("admin_prompt_part_upserted: %s", part_id)
    return success_response(data={"part_id": part_id, "message": "Part saved"})


@router.delete("/parts/{part_id:path}", dependencies=[Depends(require_config)])
async def delete_part(part_id: str, db: Session = Depends(get_db)):
    """Delete a DB override, restoring the filesystem version."""
    row = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == part_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No DB override for part: {part_id}")
    db.delete(row)
    db.commit()

    _invalidate_prompt_cache()
    logger.info("admin_prompt_part_deleted: %s", part_id)
    return success_response(data={"part_id": part_id, "message": "DB override removed, filesystem version restored"})


@router.put("/order", dependencies=[Depends(require_config)])
async def update_order(req: OrderUpdateRequest, db: Session = Depends(get_db)):
    """Batch update sort_order for prompt parts."""
    now = datetime.utcnow()
    for item in req.order:
        pid = item.get("part_id")
        order = item.get("sort_order")
        if pid is None or order is None:
            continue
        row = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == pid).first()
        if row:
            row.sort_order = int(order)
            row.updated_at = now
    db.commit()

    _invalidate_prompt_cache()
    logger.info("admin_prompt_order_updated: %d items", len(req.order))
    return success_response(data={"message": "Order updated"})


@router.post("/preview", dependencies=[Depends(require_config)])
async def preview_prompt(db: Session = Depends(get_db)):
    """Preview the full assembled system prompt using current DB + filesystem state."""
    config = load_prompt_config()
    prompt_dir = _resolve_prompt_dir()

    # Get DB parts
    db_parts: Dict[str, AdminPromptPart] = {}
    try:
        rows = db.query(AdminPromptPart).all()
        for r in rows:
            db_parts[r.part_id] = r
    except Exception:
        pass

    # Build merged parts list
    fs_parts_list = config.system_prompt.parts or []
    # Collect all part_ids: filesystem + DB-only
    all_part_ids = list(fs_parts_list)
    for pid in db_parts:
        if pid not in all_part_ids:
            all_part_ids.append(pid)

    # Build sort key: DB sort_order if available, else filesystem index * 10
    def sort_key(pid: str) -> int:
        if pid in db_parts:
            return db_parts[pid].sort_order
        try:
            return fs_parts_list.index(pid) * 10
        except ValueError:
            return 9999

    sorted_ids = sorted(all_part_ids, key=sort_key)

    # Assemble
    fs_provider = FilesystemPromptProvider(prompt_dir=prompt_dir, strict_vars=False)
    chunks: List[str] = []
    for pid in sorted_ids:
        db_row = db_parts.get(pid)
        if db_row:
            if not db_row.is_enabled:
                continue
            chunks.append(db_row.content.strip())
        else:
            txt = fs_provider.get_prompt(pid.strip(), "system", vars={})
            if txt.strip():
                chunks.append(txt.strip())

    full_prompt = "\n\n".join(chunks)
    return success_response(data={
        "prompt": full_prompt,
        "part_count": len(chunks),
        "char_count": len(full_prompt),
    })


# ── Version history routes ──────────────────────────────────────────────

@router.get("/parts/{part_id:path}/versions", dependencies=[Depends(require_config)])
async def list_versions(part_id: str, db: Session = Depends(get_db)):
    """List version history for a prompt part, newest first."""
    rows = (
        db.query(AdminPromptVersion)
        .filter(AdminPromptVersion.part_id == part_id)
        .order_by(AdminPromptVersion.created_at.desc())
        .all()
    )
    items = [
        {
            "version_id": r.version_id,
            "part_id": r.part_id,
            "display_name": r.display_name,
            "sort_order": r.sort_order,
            "is_enabled": r.is_enabled,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "created_by": r.created_by,
            "content_length": len(r.content) if r.content else 0,
        }
        for r in rows
    ]
    return success_response(data=items)


@router.get("/parts/{part_id:path}/versions/{version_id}", dependencies=[Depends(require_config)])
async def get_version(part_id: str, version_id: int, db: Session = Depends(get_db)):
    """Get full content of a specific version."""
    row = (
        db.query(AdminPromptVersion)
        .filter(
            AdminPromptVersion.part_id == part_id,
            AdminPromptVersion.version_id == version_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Version not found: {version_id}")
    return success_response(data={
        "version_id": row.version_id,
        "part_id": row.part_id,
        "content": row.content,
        "display_name": row.display_name,
        "sort_order": row.sort_order,
        "is_enabled": row.is_enabled,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
    })


@router.post("/parts/{part_id:path}/versions/{version_id}/restore", dependencies=[Depends(require_config)])
async def restore_version(part_id: str, version_id: int, db: Session = Depends(get_db)):
    """Restore a prompt part to a previous version.

    Saves the current content as a new version first, then overwrites
    the part with the selected version's content.
    """
    ver = (
        db.query(AdminPromptVersion)
        .filter(
            AdminPromptVersion.part_id == part_id,
            AdminPromptVersion.version_id == version_id,
        )
        .first()
    )
    if ver is None:
        raise HTTPException(status_code=404, detail=f"Version not found: {version_id}")

    now = datetime.utcnow()
    row = db.query(AdminPromptPart).filter(AdminPromptPart.part_id == part_id).first()

    if row:
        # Snapshot current content before restoring
        snapshot = AdminPromptVersion(
            part_id=part_id,
            content=row.content,
            display_name=row.display_name,
            sort_order=row.sort_order,
            is_enabled=row.is_enabled,
            created_at=now,
            created_by=row.created_by,
        )
        db.add(snapshot)
        # Overwrite with version content
        row.content = ver.content
        row.display_name = ver.display_name or row.display_name
        row.sort_order = ver.sort_order if ver.sort_order is not None else row.sort_order
        row.is_enabled = ver.is_enabled if ver.is_enabled is not None else row.is_enabled
        row.updated_at = now
    else:
        # Part was deleted from DB — recreate from version
        row = AdminPromptPart(
            part_id=part_id,
            content=ver.content,
            display_name=ver.display_name or part_id.split("/")[-1],
            sort_order=ver.sort_order or 0,
            is_enabled=ver.is_enabled if ver.is_enabled is not None else True,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    db.commit()
    _invalidate_prompt_cache()
    logger.info("admin_prompt_version_restored: %s → v%d", part_id, version_id)
    return success_response(data={"part_id": part_id, "restored_version_id": version_id, "message": "Version restored"})
