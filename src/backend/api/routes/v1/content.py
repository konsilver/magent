"""Content block management API routes (v1).

Provides read/write access to editable frontend content sections:
  - docs_updates      → 功能更新时间轴
  - docs_capabilities → 能力中心列表
  - manual upload     → 操作手册 PDF 上传

Write endpoints require a valid ADMIN_TOKEN in the Authorization header.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from api.deps import require_admin
from core.content.content_blocks import (
    ContentSnapshotError,
    DOCS_BLOCK_MAP,
    SNAPSHOT_SCHEMA_VERSION,
    build_docs_snapshot,
    import_docs_snapshot,
)
from core.db.engine import get_db
from core.db.models import ContentBlock
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/content", tags=["Content"])
logger = logging.getLogger(__name__)


# ── Request models ───────────────────────────────────────────────────────────

class UpdateBlockRequest(BaseModel):
    payload: List[Any]


class DocsSnapshotBlock(BaseModel):
    payload: List[Any] = Field(default_factory=list)
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


class DocsContentSnapshot(BaseModel):
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    exported_at: Optional[str] = None
    blocks: Dict[str, DocsSnapshotBlock]

    @field_validator("blocks")
    @classmethod
    def validate_blocks(cls, value: Dict[str, DocsSnapshotBlock]) -> Dict[str, DocsSnapshotBlock]:
        unknown = set(value.keys()) - set(DOCS_BLOCK_MAP.keys())
        if unknown:
            raise ValueError(f"Unknown blocks: {', '.join(sorted(unknown))}")
        if not value:
            raise ValueError("blocks cannot be empty")
        return value


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/docs", summary="获取文档内容块（前台读取）")
async def get_docs_content(db: Session = Depends(get_db)):
    updates_row = db.query(ContentBlock).filter(ContentBlock.id == "docs_updates").first()
    caps_row = db.query(ContentBlock).filter(ContentBlock.id == "docs_capabilities").first()
    prompt_hub_row = db.query(ContentBlock).filter(ContentBlock.id == "prompt_hub").first()

    return success_response(data={
        "updates": updates_row.payload if updates_row else [],
        "capabilities": caps_row.payload if caps_row else [],
        "prompt_hub": prompt_hub_row.payload if prompt_hub_row else [],
        "updates_updated_at": updates_row.updated_at.isoformat() if updates_row and updates_row.updated_at else None,
        "capabilities_updated_at": caps_row.updated_at.isoformat() if caps_row and caps_row.updated_at else None,
    })


@router.get("/docs/export", summary="导出文档内容快照")
async def export_docs_content(
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return success_response(data=build_docs_snapshot(db))


@router.post("/docs/import", summary="导入文档内容快照")
async def import_docs_content_endpoint(
    body: DocsContentSnapshot,
    overwrite: bool = True,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        result = import_docs_snapshot(
            db,
            body.model_dump(),
            overwrite=overwrite,
            default_updated_by="admin_import",
        )
    except ContentSnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "Docs content snapshot imported",
        extra={"imported": result["imported"], "skipped": result["skipped"]},
    )
    return success_response(data=result)


@router.put("/docs/{block_id}", summary="更新内容块（后管写入）")
async def update_docs_block(
    block_id: str,
    body: UpdateBlockRequest,
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if block_id not in ("updates", "capabilities", "prompt_hub"):
        raise HTTPException(status_code=404, detail=f"Unknown block: {block_id}")

    db_id = DOCS_BLOCK_MAP.get(block_id, block_id)
    row = db.query(ContentBlock).filter(ContentBlock.id == db_id).first()
    if row:
        row.payload = body.payload
        row.updated_at = datetime.utcnow()
    else:
        row = ContentBlock(id=db_id, payload=body.payload, updated_at=datetime.utcnow())
        db.add(row)

    db.commit()
    logger.info("Content block %s updated (%d items)", db_id, len(body.payload))
    return success_response(data={"block_id": block_id, "count": len(body.payload)})


# ── Manual (操作手册) upload ──────────────────────────────────────────────────

MANUAL_DIR = Path("/app/storage/manual")
MANUAL_FILENAME = "操作手册.pdf"
MAX_MANUAL_SIZE = 50 * 1024 * 1024  # 50 MB


@router.get("/manual", summary="获取操作手册信息")
async def get_manual_info():
    filepath = MANUAL_DIR / MANUAL_FILENAME
    if not filepath.exists():
        return success_response(data={"exists": False})

    stat = filepath.stat()
    return success_response(data={
        "exists": True,
        "filename": MANUAL_FILENAME,
        "size": stat.st_size,
        "uploaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "url": f"/docs/manual/{MANUAL_FILENAME}",
    })


@router.post("/manual/upload", summary="上传操作手册 PDF（管理员）")
async def upload_manual(
    file: UploadFile = File(...),
    _: None = Depends(require_admin),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    content = await file.read()
    if len(content) > MAX_MANUAL_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 50MB")

    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = MANUAL_DIR / MANUAL_FILENAME
    filepath.write_bytes(content)

    logger.info("Manual uploaded: %s (%d bytes)", MANUAL_FILENAME, len(content))
    return success_response(data={
        "filename": MANUAL_FILENAME,
        "size": len(content),
        "url": f"/docs/manual/{MANUAL_FILENAME}",
    })
