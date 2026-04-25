"""我的空间 — 用户资源管理 API

GET    /v1/artifacts            用户文件/图片列表
GET    /v1/artifacts/favorites  收藏的会话列表
DELETE /v1/artifacts/{id}       软删除资源
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from core.auth.backend import get_current_user, UserContext
from core.content.kb_processing import vectorise_document_background
from core.db.engine import get_db
from core.db.models import Artifact, ChatMessage, ChatSession, KBDocument, KBSpace
from core.db.repository import ArtifactRepository, ChatSessionRepository
from core.infra.responses import success_response, error_response
from core.services import KBService
from core.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

# Users whose historical data has already been backfilled (process-lifetime cache).
_backfilled_users: set = set()


class AddArtifactToKBRequest(BaseModel):
    kb_id: str


# ── Shared helpers (also used by chats.py) ────────────────────────────────


def infer_artifact_type(mime_type: str) -> str:
    """Map MIME type to Artifact.type enum value."""
    if mime_type.startswith("image/"):
        return "chart"
    if "wordprocessingml" in mime_type:
        return "report"
    return "document"


def resolve_artifact_storage_key(file_id: str, storage_key: Optional[str] = None) -> Optional[str]:
    """Resolve the real storage_key for an artifact.

    Tool results and historical DB rows may only store a placeholder key such as
    ``artifacts/<file_id>``. The artifact registry keeps the authoritative key,
    including the file extension required by OSS/local object lookup.
    """
    if not file_id:
        return storage_key

    try:
        from artifacts.store import get_artifact

        item = get_artifact(file_id)
        if item and item.get("storage_key"):
            return str(item["storage_key"])
    except Exception:
        logger.debug("resolve_artifact_storage_key: store lookup failed for %s", file_id, exc_info=True)

    return storage_key


def _normalize_file_ref(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("file_id"):
        return None

    file_id = str(result["file_id"]).strip()
    url = str(result.get("url", result.get("download_url", ""))).strip()
    if not file_id or not url:
        return None

    return {
        "file_id": file_id,
        "name": str(result.get("name", "")).strip() or file_id,
        "mime_type": str(result.get("mime_type", "application/octet-stream")),
        "size": int(result.get("size", 0) or 0),
        "url": url,
        "storage_key": resolve_artifact_storage_key(file_id, result.get("storage_key")),
    }


def extract_file_refs(result: Any) -> List[Dict[str, Any]]:
    """Extract one or more normalized file refs from a tool result payload."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return []

    refs: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _append(candidate: Any) -> None:
        ref = _normalize_file_ref(candidate)
        if not ref:
            return
        fid = ref["file_id"]
        if fid in seen:
            return
        seen.add(fid)
        refs.append(ref)

    if isinstance(result, list):
        for item in result:
            for ref in extract_file_refs(item):
                _append(ref)
        return refs

    if not isinstance(result, dict):
        return refs

    _append(result)

    for key in ("artifacts", "files"):
        values = result.get(key)
        if isinstance(values, list):
            for item in values:
                _append(item)

    nested = result.get("result")
    if nested is not None and nested is not result:
        for ref in extract_file_refs(nested):
            _append(ref)

    return refs


def extract_file_ref(result: Any) -> Optional[Dict[str, Any]]:
    """Backward-compatible single-file helper."""
    refs = extract_file_refs(result)
    return refs[0] if refs else None


def sanitize_chat_preview(content: Optional[str], max_len: int = 200) -> str:
    """Normalize chat preview text for list cards.

    Favorite chat previews should stay single-paragraph and avoid control
    characters or excessive whitespace from raw message content.
    """
    if not content:
        return ""

    text = str(content)
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[:max_len].rstrip() + "…"
    return text


# ── Backfill (runs once per user per process) ─────────────────────────────


def _backfill_artifacts_from_messages(user_id: str, db: Session) -> int:
    """Scan historical messages for file references not yet in the Artifact table.

    Covers three sources:
      1. tool_calls[].result  (AI-generated files)
      2. extra_data.artifacts (AI-generated files)
      3. extra_data.attachments (user-uploaded files)
    """
    existing_ids = set(
        row[0] for row in db.query(Artifact.artifact_id)
        .filter(Artifact.user_id == user_id).all()
    )

    # Scan both assistant messages (tool_calls) and user messages (attachments)
    rows = (
        db.query(ChatMessage.chat_id, ChatMessage.role, ChatMessage.tool_calls, ChatMessage.extra_data)
        .join(ChatSession, ChatMessage.chat_id == ChatSession.chat_id)
        .filter(
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None),
            ChatMessage.role.in_(["assistant", "user"]),
        )
        .all()
    )

    created = 0
    for chat_id, role, tool_calls_col, extra_data in rows:
        file_refs: List[Dict[str, Any]] = []

        # Source 1: tool_calls[].result (assistant messages)
        if role == "assistant":
            for tc in (tool_calls_col or []):
                file_refs.extend(extract_file_refs(tc.get("result")))

        if isinstance(extra_data, dict):
            # Source 2: extra_data.artifacts (assistant messages)
            for art in (extra_data.get("artifacts") or []):
                file_refs.extend(extract_file_refs(art))

            # Source 3: extra_data.attachments (user messages)
            for att in (extra_data.get("attachments") or []):
                file_refs.extend(extract_file_refs(att))

        for ref in file_refs:
            fid = ref["file_id"]
            if fid in existing_ids:
                continue
            try:
                db.add(Artifact(
                    artifact_id=fid,
                    chat_id=chat_id,
                    user_id=user_id,
                    type=infer_artifact_type(ref["mime_type"]),
                    title=ref["name"],
                    filename=ref["name"],
                    size_bytes=max(ref.get("size", 0) or 0, 1),
                    mime_type=ref["mime_type"],
                    storage_key=ref.get("storage_key") or f"artifacts/{fid}",
                    storage_url=ref.get("url", ""),
                    extra_data={"source": "backfill"},
                ))
                existing_ids.add(fid)
                created += 1
            except Exception:
                logger.debug("backfill skip %s", fid, exc_info=True)

    if created:
        try:
            db.commit()
            logger.info("backfill_artifacts: created %d for user %s", created, user_id)
        except Exception:
            logger.warning("backfill_artifacts commit failed", exc_info=True)
            db.rollback()
            created = 0
    return created


def _collect_artifact_kb_usage(db: Session, user_id: str, artifact_ids: List[str]) -> Dict[str, List[Dict[str, str]]]:
    """Collect private KB memberships for a batch of artifact IDs."""
    if not artifact_ids:
        return {}

    usage: Dict[str, List[Dict[str, str]]] = {artifact_id: [] for artifact_id in artifact_ids}
    rows = (
        db.query(KBDocument, KBSpace)
        .join(KBSpace, KBDocument.kb_id == KBSpace.kb_id)
        .filter(
            KBSpace.user_id == user_id,
            KBSpace.deleted_at.is_(None),
            KBDocument.deleted_at.is_(None),
        )
        .all()
    )

    artifact_id_set = set(artifact_ids)
    for document, space in rows:
        meta = document.extra_data if isinstance(document.extra_data, dict) else {}
        source_artifact_id = meta.get("source_artifact_id")
        if not source_artifact_id or source_artifact_id not in artifact_id_set:
            continue
        usage.setdefault(source_artifact_id, []).append({
            "kb_id": space.kb_id,
            "name": space.name,
        })

    return usage


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("/favorites")
async def list_favorite_chats(
    keyword: Optional[str] = Query(None, description="搜索关键字"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户收藏的会话列表（含最后消息预览，单次查询）。"""
    uid = str(user.user_id)

    # Build base query with keyword filter pushed to SQL
    q = db.query(ChatSession).filter(
        ChatSession.user_id == uid,
        ChatSession.deleted_at.is_(None),
        ChatSession.favorite == True,  # noqa: E712
    )
    if keyword:
        q = q.filter(ChatSession.title.ilike(f"%{keyword}%"))

    total = q.count()
    sessions = q.order_by(desc(ChatSession.updated_at)).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    # Batch-fetch last message preview for all sessions in one query
    chat_ids = [s.chat_id for s in sessions]
    previews: Dict[str, str] = {}
    if chat_ids:
        # Window function: row_number per chat_id ordered by created_at desc
        rn = func.row_number().over(
            partition_by=ChatMessage.chat_id,
            order_by=desc(ChatMessage.created_at),
        ).label("rn")
        subq = (
            db.query(ChatMessage.chat_id, ChatMessage.content, rn)
            .filter(
                ChatMessage.chat_id.in_(chat_ids),
                ChatMessage.role.in_(["user", "assistant"]),
            )
            .subquery()
        )
        rows = db.query(subq.c.chat_id, subq.c.content).filter(subq.c.rn == 1).all()
        for cid, content in rows:
            previews[cid] = sanitize_chat_preview(content, max_len=200)

    items = []
    for s in sessions:
        items.append({
            "id": s.chat_id,
            "type": "favorite",
            "name": s.title or "对话",
            "source_chat_id": s.chat_id,
            "source_chat_title": s.title,
            "content_preview": previews.get(s.chat_id, ""),
            "created_at": (s.updated_at or s.created_at).isoformat() if (s.updated_at or s.created_at) else None,
        })

    return success_response(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    })


@router.get("")
async def list_user_artifacts(
    type: Optional[str] = Query(None, description="document | image"),
    source_kind: Optional[str] = Query(None, description="user_upload | ai_generated"),
    keyword: Optional[str] = Query(None, description="文件名搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户文件/图片列表（从 Artifact 表）。"""
    uid = str(user.user_id)

    # One-time backfill for historical data (skipped after first run per process)
    if uid not in _backfilled_users:
        _backfill_artifacts_from_messages(uid, db)
        _backfilled_users.add(uid)

    repo = ArtifactRepository(db)
    mime_prefix = None
    if type == "image":
        mime_prefix = "image/"
    elif type == "document":
        mime_prefix = "document"

    normalized_source_kind = source_kind if source_kind in ("user_upload", "ai_generated") else None

    rows, total = repo.list_by_user_with_chat(
        user_id=uid, mime_prefix=mime_prefix, keyword=keyword,
        source_kind=normalized_source_kind,
        page=page, page_size=page_size,
    )

    artifact_ids = [row["artifact"].artifact_id for row in rows]
    artifact_kb_usage = _collect_artifact_kb_usage(db, uid, artifact_ids)

    items = []
    for row in rows:
        artifact = row["artifact"]
        is_image = artifact.mime_type and artifact.mime_type.startswith("image/")
        linked_kbs = artifact_kb_usage.get(artifact.artifact_id, [])
        extra_data = artifact.extra_data if isinstance(artifact.extra_data, dict) else {}
        source_kind = "user_upload" if extra_data.get("source") == "user_upload" else "ai_generated"
        items.append({
            "id": artifact.artifact_id,
            "type": "image" if is_image else "document",
            "name": artifact.filename or artifact.title,
            "mime_type": artifact.mime_type,
            "file_id": artifact.artifact_id,
            "size": artifact.size_bytes,
            "source_kind": source_kind,
            "knowledge_base_count": len(linked_kbs),
            "knowledge_bases": linked_kbs,
            "source_chat_id": artifact.chat_id,
            "source_chat_title": row["chat_title"] or "对话",
            "created_at": (artifact.updated_at or artifact.created_at).isoformat() if (artifact.updated_at or artifact.created_at) else None,
        })

    return success_response(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    })


@router.post("/{artifact_id}/knowledge-base")
async def add_artifact_to_knowledge_base(
    artifact_id: str,
    payload: AddArtifactToKBRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uid = str(user.user_id)
    kb_service = KBService(db)

    try:
        document = kb_service.add_artifact_to_space(
            artifact_id=artifact_id,
            user_id=uid,
            kb_id=payload.kb_id,
        )
    except ValueError as exc:
        return error_response(message=str(exc), code=404, status_code=404)
    except PermissionError as exc:
        return error_response(message=str(exc), code=403, status_code=403)

    if document.get("already_exists"):
        return success_response(data=document, message="该文件已在目标知识库中")

    try:
        artifact = ArtifactRepository(db).get_by_id(artifact_id)
        if artifact is None or artifact.user_id != uid:
            return error_response(message="资源不存在或无权限", code=404, status_code=404)
        file_bytes = get_storage().download_bytes(artifact.storage_key)
        background_tasks.add_task(
            vectorise_document_background,
            document_id=document["document_id"],
            kb_id=payload.kb_id,
            user_id=uid,
            title=document["title"],
            file_bytes=file_bytes,
            mime_type=artifact.mime_type or "application/octet-stream",
            chunk_method=document["chunk_method"],
            db_url=os.getenv("DATABASE_URL", ""),
            indexing_config=document.get("indexing_config"),
        )
    except Exception:
        logger.warning("failed to queue indexing for artifact %s", artifact_id, exc_info=True)

    return success_response(data=document, message="文件已加入知识库，正在索引")


@router.delete("/{artifact_id}")
async def delete_artifact(
    artifact_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """软删除资源。"""
    repo = ArtifactRepository(db)
    uid = str(user.user_id)
    deleted = repo.soft_delete(artifact_id, uid)
    if not deleted:
        return error_response(message="资源不存在或无权限", code=404, status_code=404)
    return success_response(message="删除成功")
