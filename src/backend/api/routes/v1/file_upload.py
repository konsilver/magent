"""File upload and update endpoints."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core.auth.backend import UserContext, get_current_user
from core.db.engine import get_db
from core.db.models import Artifact, ChatSession
from core.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/file", tags=["file"])

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload", summary="上传用户文件到 OSS 持久存储")
async def upload_user_file(
    file: UploadFile = File(...),
    chat_id: Optional[str] = Form(None),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    将用户上传的文件持久化到 OSS，写入 Artifact 表，返回文件 ID 和下载路径。

    **返回**：
    - `file_id`: 文件唯一 ID
    - `name`: 文件名
    - `size`: 文件字节数
    - `mime_type`: MIME 类型
    - `download_url`: 下载路径（`/files/{file_id}`）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件内容为空")

    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，最大支持 50 MB")

    user_id = str(user.user_id)
    env = os.getenv("ENVIRONMENT", "dev")
    artifact_id = f"ua_{uuid.uuid4().hex[:16]}"
    storage_key = f"{env}/{user_id}/user_uploads/{artifact_id}/{file.filename}"

    try:
        storage = get_storage()
        storage_url = storage.upload_bytes(file_bytes, storage_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件上传失败: {e}")

    # 验证 chat_id 是否已在数据库中存在（上传时 session 可能尚未创建）
    db_chat_id: Optional[str] = None
    if chat_id:
        exists = db.query(ChatSession.chat_id).filter(
            ChatSession.chat_id == chat_id
        ).first()
        if exists:
            db_chat_id = chat_id

    # Note: summary and parsed_text are intentionally left empty here.
    # They are populated lazily from `attachment.content` during the first
    # chat request that references this file (see _backfill_artifact_cache
    # in api/routes/v1/chats.py), avoiding a redundant server-side parse
    # that the frontend/`/v1/file/parse` endpoint has already performed.
    artifact = Artifact(
        artifact_id=artifact_id,
        chat_id=db_chat_id,
        user_id=user_id,
        type="other",
        title=file.filename,
        filename=file.filename,
        size_bytes=len(file_bytes),
        mime_type=file.content_type or "application/octet-stream",
        storage_key=storage_key,
        storage_url=storage_url,
        extra_data={"source": "user_upload"},
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    return {
        "file_id": artifact_id,
        "name": file.filename,
        "size": len(file_bytes),
        "mime_type": file.content_type or "application/octet-stream",
        "download_url": f"/files/{artifact_id}",
    }


@router.put("/{file_id}", summary="覆盖已有文件内容")
async def overwrite_file(
    file_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    用新内容覆盖已有文件，保持 file_id 和 download_url 不变。
    """
    artifact = db.query(Artifact).filter(
        Artifact.artifact_id == file_id,
        Artifact.user_id == str(user.user_id),
        Artifact.deleted_at.is_(None),
    ).first()
    if not artifact:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，最大支持 50 MB")

    storage = get_storage()

    # Delete old content, upload new content to same key
    old_key = artifact.storage_key
    try:
        storage.delete(old_key)
    except Exception:
        pass  # old file may already be gone

    try:
        storage_url = storage.upload_bytes(file_bytes, old_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件上传失败: {e}")

    artifact.size_bytes = len(file_bytes)
    artifact.storage_url = storage_url
    artifact.mime_type = file.content_type or artifact.mime_type
    artifact.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "file_id": file_id,
        "name": artifact.filename,
        "size": len(file_bytes),
        "mime_type": artifact.mime_type,
        "download_url": f"/files/{file_id}",
    }
