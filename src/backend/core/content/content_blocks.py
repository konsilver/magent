"""Helpers for exporting and importing editable docs content blocks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from core.db.models import ContentBlock

DOCS_BLOCK_MAP = {
    "updates": "docs_updates",
    "capabilities": "docs_capabilities",
    "prompt_hub": "prompt_hub",
}
SNAPSHOT_SCHEMA_VERSION = 1


class ContentSnapshotError(ValueError):
    """Raised when a docs content snapshot is invalid."""


def _serialize_block(row: ContentBlock | None) -> dict[str, Any]:
    return {
        "payload": row.payload if row else [],
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "updated_by": row.updated_by if row else None,
    }


def build_docs_snapshot(db: Session) -> dict[str, Any]:
    """Build a portable JSON snapshot for docs content blocks."""
    rows = (
        db.query(ContentBlock)
        .filter(ContentBlock.id.in_(list(DOCS_BLOCK_MAP.values())))
        .all()
    )
    row_map = {row.id: row for row in rows}

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "blocks": {
            alias: _serialize_block(row_map.get(db_id))
            for alias, db_id in DOCS_BLOCK_MAP.items()
        },
    }


def _parse_snapshot_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ContentSnapshotError("updated_at must be an ISO datetime string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContentSnapshotError(f"Invalid datetime: {value}") from exc


def normalize_docs_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize an incoming docs snapshot."""
    if not isinstance(snapshot, Mapping):
        raise ContentSnapshotError("Snapshot body must be a JSON object")

    schema_version = snapshot.get("schema_version", SNAPSHOT_SCHEMA_VERSION)
    if schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ContentSnapshotError(
            f"Unsupported schema_version: {schema_version}. Expected {SNAPSHOT_SCHEMA_VERSION}"
        )

    raw_blocks = snapshot.get("blocks")
    if not isinstance(raw_blocks, Mapping):
        raise ContentSnapshotError("Snapshot.blocks must be an object")

    unknown_blocks = set(raw_blocks.keys()) - set(DOCS_BLOCK_MAP.keys())
    if unknown_blocks:
        raise ContentSnapshotError(
            f"Unknown blocks in snapshot: {', '.join(sorted(unknown_blocks))}"
        )

    normalized_blocks: dict[str, dict[str, Any]] = {}
    for alias in DOCS_BLOCK_MAP:
        raw_block = raw_blocks.get(alias)
        if raw_block is None:
            continue
        if not isinstance(raw_block, Mapping):
            raise ContentSnapshotError(f"Snapshot block '{alias}' must be an object")

        payload = raw_block.get("payload", [])
        if not isinstance(payload, list):
            raise ContentSnapshotError(f"Snapshot block '{alias}'.payload must be a list")

        normalized_blocks[alias] = {
            "payload": payload,
            "updated_at": _parse_snapshot_datetime(raw_block.get("updated_at")),
            "updated_by": raw_block.get("updated_by"),
        }

    if not normalized_blocks:
        raise ContentSnapshotError("Snapshot does not contain any importable docs blocks")

    exported_at = snapshot.get("exported_at")
    if exported_at not in (None, "") and not isinstance(exported_at, str):
        raise ContentSnapshotError("Snapshot.exported_at must be a string")

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "blocks": normalized_blocks,
    }


def import_docs_snapshot(
    db: Session,
    snapshot: Mapping[str, Any],
    *,
    overwrite: bool = True,
    default_updated_by: str | None = None,
) -> dict[str, Any]:
    """Import a docs content snapshot into the current database."""
    normalized = normalize_docs_snapshot(snapshot)
    imported: list[str] = []
    skipped: list[str] = []

    for alias, block in normalized["blocks"].items():
        db_id = DOCS_BLOCK_MAP[alias]
        row = db.query(ContentBlock).filter(ContentBlock.id == db_id).first()
        if row and not overwrite:
            skipped.append(alias)
            continue

        updated_at = block["updated_at"] or datetime.now(timezone.utc)
        updated_by = block["updated_by"] or default_updated_by

        if row:
            row.payload = block["payload"]
            row.updated_at = updated_at
            row.updated_by = updated_by
        else:
            row = ContentBlock(
                id=db_id,
                payload=block["payload"],
                updated_at=updated_at,
                updated_by=updated_by,
            )
            db.add(row)
        imported.append(alias)

    db.commit()

    return {
        "schema_version": normalized["schema_version"],
        "imported": imported,
        "skipped": skipped,
        "count": len(imported),
        "overwrite": overwrite,
    }
