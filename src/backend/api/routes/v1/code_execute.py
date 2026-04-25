"""Standalone code execution endpoint (for re-execute from Artifacts panel)."""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth.backend import UserContext, get_current_user
from core.db.engine import get_db
from core.db.models import Artifact
from core.infra.responses import success_response
from core.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/code", tags=["code-execution"])

RUNNER_URL = os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900")
_EXT_MAP = {"python": "py", "javascript": "js", "bash": "sh"}


class ExecuteRequest(BaseModel):
    language: str = "python"
    code: str
    timeout: int = 60


class FileRef(BaseModel):
    file_id: str
    name: str
    url: str
    mime_type: str
    size: int


class ExecuteResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: int
    files: List[FileRef] = []


@router.post("/execute", summary="直接执行代码（用于面板重执行）")
async def execute_code_direct(
    req: ExecuteRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Execute code in the sandbox directly, returning structured results."""
    if req.language not in _EXT_MAP:
        raise HTTPException(400, f"不支持的语言: {req.language}，可选: {', '.join(_EXT_MAP)}")

    if not req.code.strip():
        raise HTTPException(400, "代码内容不能为空")

    effective_timeout = min(req.timeout, 120)
    script_name = f"exec.{_EXT_MAP[req.language]}"

    try:
        async with httpx.AsyncClient(timeout=effective_timeout + 10) as client:
            resp = await client.post(
                f"{RUNNER_URL}/execute",
                json={
                    "script_content": req.code,
                    "script_name": script_name,
                    "language": req.language,
                    "params": {},
                    "timeout": effective_timeout,
                },
            )
            result = resp.json()
    except Exception as e:
        raise HTTPException(502, f"脚本执行器连接失败: {e}")

    # Process file outputs
    file_refs: List[Dict[str, Any]] = []
    raw_files = result.get("files", [])
    if raw_files:
        user_id = str(user.user_id)
        env = os.getenv("ENVIRONMENT", "dev")
        storage = get_storage()

        for fd in raw_files:
            name = fd.get("name", "output")
            content_b64 = fd.get("content_b64", "")
            if not content_b64:
                continue
            try:
                file_bytes = base64.b64decode(content_b64)
                artifact_id = f"ce_{uuid.uuid4().hex[:16]}"
                storage_key = f"{env}/{user_id}/code_exec/{artifact_id}/{name}"
                storage_url = storage.upload_bytes(file_bytes, storage_key)
                mime = fd.get("mime_type", "application/octet-stream")
                size = fd.get("size", len(file_bytes))

                db.add(Artifact(
                    artifact_id=artifact_id,
                    chat_id=None,
                    user_id=user_id,
                    type="chart" if mime.startswith("image/") else "document",
                    title=name,
                    filename=name,
                    size_bytes=size,
                    mime_type=mime,
                    storage_key=storage_key,
                    storage_url=storage_url,
                    extra_data={"source": "code_exec_direct"},
                ))
                file_refs.append({
                    "file_id": artifact_id,
                    "name": name,
                    "url": f"/files/{artifact_id}",
                    "mime_type": mime,
                    "size": size,
                })
            except Exception as exc:
                logger.warning("failed to store exec file %s: %s", name, exc)

        if file_refs:
            try:
                db.commit()
            except Exception:
                db.rollback()
                file_refs = []

    return success_response(data={
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
        "execution_time_ms": result.get("execution_time_ms", 0),
        "files": file_refs,
    })
