"""Artifact store with local / OSS dual-mode support.

Storage mode is controlled by the STORAGE_TYPE environment variable:
- 'local' (default): files are written under
  ``${STORAGE_PATH:-result}/artifacts/`` on the local filesystem and served
  directly via FileResponse.
- 'oss': files are uploaded to Aliyun OSS via OSSStorageBackend.  A local
  JSON index is still maintained for fast look-ups, and it is also backed up
  to OSS so that the index survives container restarts.

Artifacts are downloaded via ``GET /files/{file_id}``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# ── 本地路径（两种模式都用于存放索引文件） ────────────────────────
# 优先使用 STORAGE_PATH（容器内通常为 /app/storage），避免在 /app 下创建
# 无权限目录；未配置时回退到项目根目录 result/。
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STORAGE_BASE = os.getenv("STORAGE_PATH", "").strip()
_BASE_DIR = Path(_STORAGE_BASE).expanduser() if _STORAGE_BASE else (_PROJECT_ROOT / "result")
_STORE_DIR = (_BASE_DIR / "artifacts").resolve()
_INDEX_PATH = _STORE_DIR / "index.json"
_LOCK = threading.Lock()

# OSS 中索引文件的 key（OSSStorageBackend 会自动加前缀）
_OSS_INDEX_KEY = "artifacts/_index.json"


# ── 辅助：获取当前 STORAGE_TYPE ──────────────────────────────────
def _storage_type() -> str:
    return os.getenv("STORAGE_TYPE", "local").lower()


# ── 辅助：获取 OSS 后端（延迟导入，避免循环依赖） ────────────────
def _get_oss_storage():
    from core.storage import get_storage
    return get_storage()


# ── 索引管理 ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat()


def _ensure_store() -> None:
    """确保本地目录和索引文件存在；OSS 模式下尝试从 OSS 恢复索引。"""
    _STORE_DIR.mkdir(parents=True, exist_ok=True)

    if not _INDEX_PATH.exists():
        # OSS 模式：尝试从 OSS 恢复索引（容器重启后恢复）
        if _storage_type() == "oss":
            try:
                storage = _get_oss_storage()
                content = storage.download_bytes(_OSS_INDEX_KEY)
                _INDEX_PATH.write_bytes(content)
                logger.info("Artifact index restored from OSS.")
                return
            except Exception:
                pass  # 第一次启动，OSS 上也没有，正常创建空索引

        _INDEX_PATH.write_text(
            json.dumps({"files": {}}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _load_index() -> Dict[str, Any]:
    _ensure_store()
    try:
        raw = _INDEX_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except Exception:
        pass
    return {"files": {}}


def _save_index(data: Dict[str, Any]) -> None:
    _ensure_store()
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _INDEX_PATH.write_text(text, encoding="utf-8")

    # OSS 模式：同步备份索引，保证容器重启后可恢复
    if _storage_type() == "oss":
        try:
            storage = _get_oss_storage()
            storage.upload_bytes(text.encode("utf-8"), _OSS_INDEX_KEY)
        except Exception as e:
            logger.warning(f"Failed to backup artifact index to OSS: {e}")


# ── 公开接口 ──────────────────────────────────────────────────────

def save_artifact_bytes(
    *,
    content: bytes,
    name: str,
    mime_type: str = "application/octet-stream",
    extension: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """持久化字节内容，返回包含 file_id / storage_key 的元数据字典。

    local 模式：写入本地 ${STORAGE_PATH:-result}/artifacts/
    oss   模式：上传到 OSS，本地仅保存索引条目
    """
    ext = extension.strip().lstrip(".")
    file_id = uuid4().hex
    filename = f"{file_id}.{ext}" if ext else file_id

    mode = _storage_type()

    if mode == "oss":
        # ── OSS 存储 ──────────────────────────────────────────────
        storage_key = f"artifacts/{filename}"
        try:
            storage = _get_oss_storage()
            storage.upload_bytes(content, storage_key)
            logger.info(f"Artifact uploaded to OSS: {storage_key}")
        except Exception as e:
            logger.error(f"Failed to upload artifact to OSS: {e}")
            raise

        item: Dict[str, Any] = {
            "file_id": file_id,
            "name": name,
            "mime_type": mime_type,
            "size": len(content),
            "path": None,           # OSS 模式无本地路径
            "storage_key": storage_key,
            "created_at": _now_iso(),
            "metadata": metadata or {},
        }
    else:
        # ── 本地存储（原有逻辑） ───────────────────────────────────
        abs_path = (_STORE_DIR / filename).resolve()
        _ensure_store()
        abs_path.write_bytes(content)
        logger.info(f"Artifact saved locally: {abs_path}")

        item = {
            "file_id": file_id,
            "name": name,
            "mime_type": mime_type,
            "size": len(content),
            "path": str(abs_path),
            "storage_key": None,
            "created_at": _now_iso(),
            "metadata": metadata or {},
        }

    with _LOCK:
        index = _load_index()
        files = index.get("files")
        if not isinstance(files, dict):
            files = {}
            index["files"] = files
        files[file_id] = item
        _save_index(index)

    return item


def get_artifact(file_id: str) -> Optional[Dict[str, Any]]:
    """通过 file_id 查询 artifact 元数据。"""
    with _LOCK:
        data = _load_index()
        files = data.get("files")
        if not isinstance(files, dict):
            return None
        item = files.get(file_id)
        if not isinstance(item, dict):
            return None

    # OSS 模式：只要 storage_key 存在即视为有效
    storage_key = item.get("storage_key")
    if storage_key:
        return item

    # 本地模式：确认文件实际存在
    path = Path(str(item.get("path", "")))
    if not path.exists() or not path.is_file():
        return None
    return item
