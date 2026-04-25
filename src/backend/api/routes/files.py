"""File download and preview API routes."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from artifacts.store import get_artifact
from core.auth.backend import UserContext, require_auth
from core.db.engine import get_db
from core.db.repository import AuditLogRepository
from core.infra.exceptions import StorageError
from core.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
POWERPOINT_EXTENSIONS = {".ppt", ".pptx"}
POWERPOINT_MIME_MARKERS = ("presentationml", "powerpoint")


def _load_artifact_item(file_id: str, db: Session) -> dict[str, Any]:
    """Resolve an artifact from local store first, then DB-backed storage."""
    item = get_artifact(file_id)
    if item is not None:
        return item

    from core.db.models import Artifact as ArtifactModel

    artifact_obj = db.query(ArtifactModel).filter(
        ArtifactModel.artifact_id == file_id
    ).first()
    if artifact_obj is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    return {
        "path": None,
        "name": artifact_obj.filename or file_id,
        "mime_type": artifact_obj.mime_type or "application/octet-stream",
        "size": artifact_obj.size_bytes or 0,
        "storage_key": artifact_obj.storage_key,
        "metadata": {"from_database": True},
    }


def _record_audit(
    *,
    user: Optional[UserContext],
    db: Session,
    action: str,
    file_id: str,
    item: dict[str, Any],
    status: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    if not user:
        return
    try:
        AuditLogRepository(db).create({
            "user_id": user.user_id,
            "action": action,
            "resource_type": "artifact",
            "resource_id": file_id,
            "status": status,
            "details": details or {},
        })
    except Exception as exc:
        logger.warning("Failed to create audit log for %s: %s", action, exc)


def _authorize_access(
    *,
    item: dict[str, Any],
    file_id: str,
    user: Optional[UserContext],
    db: Session,
    denied_action: str,
) -> None:
    owner_id = item.get("metadata", {}).get("user_id")
    if not user or not owner_id:
        return
    if owner_id == user.user_id:
        return

    _record_audit(
        user=user,
        db=db,
        action=denied_action,
        file_id=file_id,
        item=item,
        status="failed",
        details={"reason": "access_denied"},
    )
    raise HTTPException(status_code=403, detail="Access denied: you don't own this file")


def _cleanup_path(path: str) -> None:
    try:
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Failed to clean up temp path %s: %s", path, exc)


def _prepare_local_file(
    *,
    item: dict[str, Any],
    file_id: str,
    background_tasks: BackgroundTasks,
) -> str:
    """Ensure the artifact is available as a local file path."""
    storage_key = item.get("storage_key")
    local_path = item.get("path")
    if not storage_key:
        if not local_path:
            raise HTTPException(status_code=500, detail="File path not available")
        return str(local_path)

    storage = get_storage()
    temp_dir = tempfile.mkdtemp(prefix=f"artifact_{file_id}_")
    filename = Path(str(item.get("name", file_id))).name or file_id
    temp_path = Path(temp_dir) / filename
    try:
        storage.download(storage_key, str(temp_path))
    except StorageError as exc:
        _cleanup_path(temp_dir)
        logger.error("Failed to download file from storage: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(exc)}")

    background_tasks.add_task(_cleanup_path, temp_dir)
    return str(temp_path)


def _is_powerpoint_file(item: dict[str, Any]) -> bool:
    name = str(item.get("name", ""))
    mime_type = str(item.get("mime_type", "")).lower()
    ext = Path(name).suffix.lower()
    return ext in POWERPOINT_EXTENSIONS or any(marker in mime_type for marker in POWERPOINT_MIME_MARKERS)


def _convert_powerpoint_to_pdf(source_path: str, file_id: str) -> tuple[str, str]:
    """Render a PPT/PPTX to PDF via headless LibreOffice."""
    temp_dir = tempfile.mkdtemp(prefix=f"ppt_preview_{file_id}_")
    source = Path(source_path)
    staged_name = source.name or f"{file_id}.pptx"
    staged_source = Path(temp_dir) / staged_name
    shutil.copy2(source, staged_source)

    profile_dir = Path(temp_dir) / "lo-profile"
    command = [
        "libreoffice",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        temp_dir,
        str(staged_source),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        _cleanup_path(temp_dir)
        raise RuntimeError("LibreOffice 未安装，无法预览 PPT 文件") from exc
    except subprocess.TimeoutExpired as exc:
        _cleanup_path(temp_dir)
        raise RuntimeError("PPT 预览转换超时") from exc

    pdf_path = staged_source.with_suffix(".pdf")
    if not pdf_path.exists():
        generated = sorted(Path(temp_dir).glob("*.pdf"))
        if generated:
            pdf_path = generated[0]

    if result.returncode != 0 or not pdf_path.exists():
        stderr = (result.stderr or result.stdout or "").strip()
        _cleanup_path(temp_dir)
        raise RuntimeError(f"PPT 预览转换失败: {(stderr or 'unknown error')[:300]}")

    return str(pdf_path), temp_dir


def _build_direct_download_response(
    *,
    item: dict[str, Any],
    file_id: str,
    inline: bool,
    background_tasks: BackgroundTasks,
) -> FileResponse:
    local_path = _prepare_local_file(item=item, file_id=file_id, background_tasks=background_tasks)
    return FileResponse(
        path=local_path,
        media_type=str(item.get("mime_type", "application/octet-stream")),
        filename=str(item.get("name", file_id)),
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/{file_id}")
async def download_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    mode: str = Query("direct", description="Download mode: direct or presigned"),
    inline: bool = Query(False, description="If true, serve for inline display (Content-Disposition: inline)"),
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    """
    下载生成的文件

    下载由 Agent 生成的附件文件（如报告、图表等）。

    **参数**:
    - **file_id**: 文件ID（在聊天响应的 artifacts 列表中获取）
    - **mode**: 下载模式
      - `direct`: 直接返回文件内容（默认）
      - `presigned`: 返回 S3 签名 URL（15分钟有效期，未实现时返回直接下载URL）

    **返回**:
    - mode=direct: 文件内容（直接下载）
    - mode=presigned: JSON 格式的签名 URL

    **示例**:
    ```bash
    # 直接下载
    curl -O http://localhost:8000/files/report-20240213-123456

    # 获取签名 URL
    curl http://localhost:8000/files/report-20240213-123456?mode=presigned
    ```

    **文件来源**:
    - 由 MCP server `report_export_mcp` 生成的 docx 文件
    - 由 `generate_chart_tool` 生成的图表文件
    - 其他 Agent 生成的附件
    """
    item = _load_artifact_item(file_id, db)
    _authorize_access(
        item=item,
        file_id=file_id,
        user=user,
        db=db,
        denied_action="file.download.denied",
    )

    storage_key = item.get("storage_key")
    use_storage = bool(storage_key)

    _record_audit(
        user=user,
        db=db,
        action="file.download",
        file_id=file_id,
        item=item,
        status="success",
        details={
            "mode": mode,
            "filename": item.get("name", file_id),
            "size": item.get("size", 0),
        },
    )

    # 根据模式返回
    if mode == "presigned":
        if use_storage:
            try:
                storage = get_storage()
                presigned_url = storage.generate_presigned_url(storage_key, expires_in=900)

                storage_type = os.getenv("STORAGE_TYPE", "local").lower()

                response_data = {
                    "url": presigned_url,
                    "expires_in": 900,  # 15 minutes
                    "filename": str(item.get("name", file_id)),
                }

                if storage_type == "local":
                    response_data["note"] = "Local storage - file:// URL provided for development"

                return JSONResponse(response_data)

            except StorageError as exc:
                logger.error("Failed to generate presigned URL: %s", exc)
                return JSONResponse({
                    "url": f"/files/{file_id}",
                    "expires_in": 900,
                    "filename": str(item.get("name", file_id)),
                    "note": "Failed to generate presigned URL, using direct download URL"
                })
        else:
            # Local artifact, return direct download URL
            return JSONResponse({
                "url": f"/files/{file_id}",
                "expires_in": 900,
                "filename": str(item.get("name", file_id)),
                "note": "Local artifact, using direct download URL"
            })
    else:
        return _build_direct_download_response(
            item=item,
            file_id=file_id,
            inline=inline,
            background_tasks=background_tasks,
        )


@router.get("/{file_id}/preview")
async def preview_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    format: str = Query("pdf", description="Preview format, currently only pdf is supported"),
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    """Render PowerPoint artifacts to a browser-previewable PDF."""
    if format != "pdf":
        raise HTTPException(status_code=400, detail="Unsupported preview format")

    item = _load_artifact_item(file_id, db)
    _authorize_access(
        item=item,
        file_id=file_id,
        user=user,
        db=db,
        denied_action="file.preview.denied",
    )
    if not _is_powerpoint_file(item):
        raise HTTPException(status_code=400, detail="Only PPT/PPTX files support preview")

    source_path = _prepare_local_file(
        item=item,
        file_id=file_id,
        background_tasks=background_tasks,
    )
    try:
        pdf_path, temp_dir = _convert_powerpoint_to_pdf(source_path, file_id)
    except RuntimeError as exc:
        logger.error("Failed to render PPT preview for %s: %s", file_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    background_tasks.add_task(_cleanup_path, temp_dir)
    _record_audit(
        user=user,
        db=db,
        action="file.preview",
        file_id=file_id,
        item=item,
        status="success",
        details={
            "format": format,
            "filename": item.get("name", file_id),
            "size": item.get("size", 0),
        },
    )

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"{Path(str(item.get('name', file_id))).stem}.pdf",
        content_disposition_type="inline",
    )
