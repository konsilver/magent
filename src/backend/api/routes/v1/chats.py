"""Chat session management + streaming chat API routes (v1)."""

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from api.schemas import AttachmentItem, ChatRequest, ChatResponse
from core.auth.backend import get_current_user, require_auth, UserContext
from core.chat.context import (
    build_runtime_context,
    generate_smart_title,
    now_iso,
    resolve_db_user_id,
    resolve_enabled_capabilities,
    resolve_user_facing_error,
)
from core.db.engine import get_db
from core.db.models import Artifact as ArtifactModel, MessageFeedback
from core.config.model_config import ModelConfigService
from core.services import ChatService, UserService
from core.infra.responses import success_response, paginated_response, created_response
from core.infra.exceptions import ResourceNotFoundError, ResourceOwnershipError
from routing.followups import get_followup_generator
from routing.workflow import WorkflowResult, run_chat_workflow, astream_chat_workflow
from routing.subagents.plan_mode import astream_generate_plan, astream_execute_plan
from core.services.plan_service import PlanService
from core.llm.message_compat import strip_thinking
from core.infra.logging import get_logger
from api.routes.v1.artifacts import extract_file_refs, infer_artifact_type, resolve_artifact_storage_key

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/chats", tags=["Sessions"])


# ── Shared helpers for streaming SSE event processing ────────────────────

def _upsert_tool_call(tool_calls_log: list, tc: dict) -> None:
    """Merge a tool_call into the log, updating an existing entry by tool_id."""
    tid = tc.get("tool_id")
    if tid:
        for existing in tool_calls_log:
            if existing.get("tool_id") == tid:
                if tc.get("tool_args"):
                    existing["tool_args"] = tc["tool_args"]
                if tc.get("tool_display_name"):
                    existing["tool_display_name"] = tc["tool_display_name"]
                return
    tool_calls_log.append(tc)


def _attach_tool_result(tool_calls_log: list, tid: str, tn: str, res: Any) -> None:
    """Attach a tool_result to the matching tool_call entry in the log."""
    for tc in tool_calls_log:
        if tid and tc.get("tool_id") == tid:
            tc["result"], tc["status"] = res, "success"
            return
        if tn and tc.get("tool_name") == tn and "result" not in tc:
            tc["result"], tc["status"] = res, "success"
            return
    if tid or tn:
        tool_calls_log.append({"tool_name": tn, "tool_id": tid, "result": res, "status": "success"})


def _persist_artifacts(
    db: Session, user_id: str, chat_id: str, collected: list,
) -> None:
    """Batch-insert collected file artifacts into the Artifact DB table."""
    if not collected:
        return
    all_fids = [a["file_id"] for a in collected if a.get("file_id")]
    existing_ids = set(
        r[0] for r in db.query(ArtifactModel.artifact_id)
        .filter(ArtifactModel.artifact_id.in_(all_fids)).all()
    ) if all_fids else set()
    for art in collected:
        art_id = art.get("file_id", "")
        if not art_id or art_id in existing_ids:
            continue
        mime = art.get("mime_type", "application/octet-stream")
        try:
            storage_key = resolve_artifact_storage_key(art_id, art.get("storage_key")) or f"artifacts/{art_id}"
            db.add(ArtifactModel(
                artifact_id=art_id, chat_id=chat_id, user_id=user_id,
                type=infer_artifact_type(mime),
                title=art.get("name", ""), filename=art.get("name", ""),
                size_bytes=max(art.get("size", 0) or 0, 1),
                mime_type=mime, storage_key=storage_key,
                storage_url=art.get("url", ""),
                extra_data={"source": "ai_generated", "tool_name": art.get("tool_name", "")},
            ))
        except Exception as e:
            logger.warning("artifact_db_insert_failed: %s", e)
    try:
        db.commit()
    except Exception as e:
        logger.warning("artifact_db_commit_failed: %s", e)
        db.rollback()


def _extend_collected_artifacts(collected: list, refs: list[dict]) -> None:
    existing_ids = {item.get("file_id") for item in collected if item.get("file_id")}
    for ref in refs:
        file_id = ref.get("file_id")
        if not file_id or file_id in existing_ids:
            continue
        collected.append(ref)
        existing_ids.add(file_id)


def _ensure_main_model_configured() -> None:
    """Fail fast with a user-facing error when the main chat model is missing."""
    resolved = ModelConfigService.get_instance().resolve("main_agent")
    if resolved is not None:
        return
    raise HTTPException(
        status_code=503,
        detail="当前未配置主对话模型，请先在管理后台配置模型供应商并绑定 main_agent 角色。",
    )


# Request/Response Models
class CreateChatRequest(BaseModel):
    """Request model for creating a chat session."""
    title: Optional[str] = Field("新对话", description="Chat session title")
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata")


class UpdateChatRequest(BaseModel):
    """Request model for updating a chat session."""
    title: Optional[str] = Field(None, description="Chat session title")
    pinned: Optional[bool] = Field(None, description="Pin status")
    favorite: Optional[bool] = Field(None, description="Favorite status")
    metadata: Optional[dict] = Field(None, description="Additional metadata")


class ChatSessionResponse(BaseModel):
    """Response model for chat session."""
    chat_id: str
    title: str
    user_id: str
    message_count: int
    pinned: bool
    favorite: bool
    metadata: dict
    created_at: str
    updated_at: str


class ChatMessageResponse(BaseModel):
    """Response model for chat message."""
    message_id: str
    chat_id: str
    role: str
    content: str
    model: Optional[str] = None
    tool_calls: Optional[list] = None
    metadata: Optional[dict] = None
    created_at: str


def _session_to_dict(s) -> dict:
    """Convert a ChatSession ORM object to API response dict."""
    return {
        "chat_id": s.chat_id,
        "title": s.title,
        "user_id": s.user_id,
        "message_count": s.message_count,
        "pinned": s.pinned,
        "favorite": s.favorite,
        "metadata": s.extra_data or {},
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _message_to_dict(m) -> dict:
    """Convert a ChatMessage ORM object to API response dict."""
    return {
        "message_id": m.message_id,
        "chat_id": m.chat_id,
        "role": m.role,
        "content": m.content,
        "model": m.model,
        "tool_calls": m.tool_calls,
        "metadata": m.extra_data or {},
        "created_at": m.created_at.isoformat(),
    }


def _clean_id_list(raw: Optional[list]) -> List[str]:
    """Normalize a list of capability IDs: strip whitespace, remove empties."""
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


@router.get("", summary="获取会话列表")
async def list_chats(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("-updated_at", description="Sort field"),
    filter: Optional[str] = Query(None, description="Filter conditions"),
    exclude_automation: bool = Query(False, description="Exclude automation-generated chats"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get paginated list of chat sessions for the current user.

    Supports filtering by:
    - pinned=true - Only pinned sessions
    - favorite=true - Only favorite sessions
    - exclude_automation=true - Hide automation-generated sessions

    Supports sorting by:
    - -updated_at (default) - Most recently updated first
    - updated_at - Oldest updated first
    - -created_at - Most recently created first
    - created_at - Oldest created first
    """
    chat_service = ChatService(db)

    # Parse filters
    pinned_only = filter == "pinned=true" if filter else False
    favorite_only = filter == "favorite=true" if filter else False

    # Get sessions
    sessions, total, total_pages = chat_service.list_sessions(
        user_id=user.user_id,
        page=page,
        page_size=page_size,
        pinned_only=pinned_only,
        favorite_only=favorite_only,
        exclude_automation=exclude_automation,
    )

    items = [_session_to_dict(s) for s in sessions]

    return paginated_response(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        message="Chat sessions retrieved successfully"
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建新会话")
async def create_chat(
    request: CreateChatRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new chat session.

    The session is automatically associated with the current authenticated user.
    """
    chat_service = ChatService(db)

    session = chat_service.create_session(
        user_id=user.user_id,
        title=request.title,
        extra_data=request.metadata
    )

    return created_response(
        data=_session_to_dict(session),
        message="Chat session created successfully"
    )


@router.get("/search", summary="搜索会话")
async def search_chats(
    q: str = Query(..., description="Search keyword"),
    scope: str = Query("title", description="Search scope: 'title' or 'all' (title + message content)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Search chat sessions by title and optionally message content.

    - scope=title (default): search title only
    - scope=all: search both title and message content

    Returns sessions with match_type ("title" or "content") and matched_snippet for content matches.
    """
    chat_service = ChatService(db)

    results, total = chat_service.search_sessions(
        user_id=user.user_id,
        query=q,
        page=page,
        page_size=page_size,
        scope=scope,
    )

    items = []
    for r in results:
        item = _session_to_dict(r["session"])
        item["match_type"] = r["match_type"]
        item["matched_snippet"] = r["matched_snippet"]
        items.append(item)

    return success_response(
        data={
            "items": items,
            "total": total
        },
        message="Search completed successfully"
    )


@router.get("/{chat_id}", summary="获取会话详情")
async def get_chat(
    chat_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get chat session details.

    Only the session owner can access the session details.
    """
    chat_service = ChatService(db)

    session = chat_service.get_session(chat_id, user.user_id)
    if not session:
        raise ResourceNotFoundError(
            resource_type="chat_session",
            resource_id=chat_id
        )

    return success_response(
        data=_session_to_dict(session),
        message="Chat session retrieved successfully"
    )


@router.patch("/{chat_id}", summary="更新会话")
async def update_chat(
    chat_id: str,
    request: UpdateChatRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update chat session metadata.

    Only the session owner can update the session.
    Fields not provided in the request will remain unchanged.
    """
    chat_service = ChatService(db)

    # Build update data from request
    update_data = {}
    if request.title is not None:
        update_data["title"] = request.title
    if request.pinned is not None:
        update_data["pinned"] = request.pinned
    if request.favorite is not None:
        update_data["favorite"] = request.favorite
    if request.metadata is not None:
        update_data["extra_data"] = request.metadata

    # Update session
    session = chat_service.update_session(chat_id, user.user_id, update_data)
    if not session:
        raise ResourceNotFoundError(
            resource_type="chat_session",
            resource_id=chat_id
        )

    return success_response(
        data=_session_to_dict(session),
        message="Chat session updated successfully"
    )


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除会话")
async def delete_chat(
    chat_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a chat session (soft delete).

    Only the session owner can delete the session.
    The session is not permanently deleted but marked as deleted.
    """
    chat_service = ChatService(db)

    result = chat_service.delete_session(chat_id, user.user_id)
    if not result:
        raise ResourceNotFoundError(
            resource_type="chat_session",
            resource_id=chat_id
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{chat_id}/messages", summary="获取会话消息列表")
async def list_messages(
    chat_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get paginated list of messages in a chat session.

    Only the session owner can access the messages.
    Messages are returned in chronological order (oldest first).
    """
    chat_service = ChatService(db)

    result = chat_service.list_messages(chat_id, user.user_id, page, page_size)
    if result is None:
        raise ResourceNotFoundError(
            resource_type="chat_session",
            resource_id=chat_id
        )

    messages, total, total_pages = result

    items = [_message_to_dict(m) for m in messages]

    return paginated_response(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        message="Messages retrieved successfully"
    )


@router.get("/{chat_id}/messages/{message_id}/followups", summary="获取追问问题")
async def get_followups(
    chat_id: str,
    message_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return follow-up questions stored in a message's extra_data."""
    chat_service = ChatService(db)

    session = chat_service.get_session(chat_id, user.user_id)
    if not session:
        raise ResourceNotFoundError(resource_type="chat_session", resource_id=chat_id)

    msg = chat_service.message_repo.get_by_id(message_id)
    if not msg or msg.chat_id != chat_id:
        raise ResourceNotFoundError(resource_type="chat_message", resource_id=message_id)

    questions = (msg.extra_data or {}).get("follow_up_questions", [])
    return success_response(data={"follow_up_questions": questions})


# ── Streaming / Non-streaming chat ────────────────────────────────────────

def _authenticated_user_id(user: Optional[UserContext]) -> Optional[str]:
    if isinstance(user, UserContext):
        return user.user_id
    return None


def _build_user_extra_data(request: ChatRequest) -> Dict[str, Any]:
    extra: Dict[str, Any] = {"timestamp": now_iso()}
    if request.attachments:
        upload_meta = [
            {"name": a.name, "mime_type": a.mime_type, "file_id": a.file_id, "download_url": a.download_url}
            for a in request.attachments if a.file_id
        ]
        if upload_meta:
            extra["attachments"] = upload_meta
    if request.quoted_follow_up:
        extra["quoted_follow_up"] = request.quoted_follow_up.model_dump()
    if request.skill_id:
        extra["skill_id"] = request.skill_id
    return extra


def _build_effective_user_message(message: str, quoted_follow_up: Optional[Any]) -> str:
    quote_text = getattr(quoted_follow_up, "text", None) if quoted_follow_up is not None else None
    if not quote_text:
        return message

    quote = str(quote_text).strip()
    if not quote:
        return message

    return (
        "你正在回答同一会话中的一条追问消息。请优先结合当前会话上下文，并重点参考下面的引用原文来理解代词、省略和上下文指向。\n"
        "要求：\n"
        "1. 将【引用原文】视为这次追问直接关联的内容。\n"
        "2. 若用户问题中出现“这个/这个点/它/上述/刚才提到的”等指代，优先从【引用原文】和最近几轮会话补全语义。\n"
        "3. 直接回答用户当前追问，不要重复无关背景，也不要提及本提示词或“根据引用原文”。\n\n"
        f"【引用原文】\n{quote}\n\n"
        f"【用户追问】\n{message}"
    )


_MAX_HISTORY_SUMMARY_CHARS = 4000  # soft cap on total historical summary injection
_MAX_CHAT_MESSAGES_SCANNED = 500   # hard cap on message lookback when aggregating file refs


def _backfill_artifact_cache(
    attachments: List[Dict[str, Any]],
    user_id: str,
) -> None:
    """Populate Artifact.parsed_text and .summary from attachment.content.

    The frontend (or /v1/file/parse) has already parsed uploaded files;
    the parsed text arrives in `attachment.content`. We write it back to
    the Artifact cache so subsequent turns can reference it without any
    re-parsing — this is what the user referred to as "不需要再调用一次解析
    文件的工具".

    Silent on failure — this is a best-effort backfill, not a hard
    requirement for the current turn.
    """
    if not attachments or not user_id:
        return
    try:
        from core.db.engine import SessionLocal
        from core.db.models import Artifact as _ArtifactModel
        from core.content.artifact_summary import build_summary_from_text
    except Exception:
        return

    to_update: List[tuple[str, str]] = []
    for att in attachments:
        fid = (att.get("file_id") or "").strip()
        content = (att.get("content") or "").strip()
        if fid and content:
            to_update.append((fid, content))
    if not to_update:
        return

    fids = [fid for fid, _ in to_update]
    content_by_id = {fid: content for fid, content in to_update}

    try:
        with SessionLocal() as db:
            rows = db.query(_ArtifactModel).filter(
                _ArtifactModel.artifact_id.in_(fids),
                _ArtifactModel.user_id == user_id,
                _ArtifactModel.deleted_at.is_(None),
            ).all()
            changed = False
            for art in rows:
                content = content_by_id.get(art.artifact_id)
                if not content:
                    continue
                if not art.parsed_text:
                    art.parsed_text = content
                    art.parsed_at = datetime.utcnow()
                    changed = True
                if not art.summary:
                    try:
                        art.summary = build_summary_from_text(
                            content, art.filename or "file", art.mime_type or "",
                        )
                        art.parse_error = None
                        changed = True
                    except Exception as e:
                        logger.debug("backfill: summary derivation failed for %s: %s", art.artifact_id, e)
            if changed:
                db.commit()
    except Exception as e:
        logger.warning("backfill_artifact_cache failed: %s", e)


def _extract_message_file_ids(msg) -> List[str]:
    """Pull all file_ids referenced by a ChatMessage, regardless of role.

    User messages carry attachments in `extra_data["attachments"]`
    (written by `_build_user_extra_data`). Assistant messages carry
    AI-generated file refs in `extra_data["artifacts"]` (written by the
    streaming flow via the workflow meta chunk). Both are flat lists of
    `{file_id, name, ...}` dicts.
    """
    extra = msg.extra_data or {}
    out: List[str] = []
    for key in ("attachments", "artifacts"):
        items = extra.get(key) or []
        for item in items:
            fid = (item.get("file_id") or "").strip() if isinstance(item, dict) else ""
            if fid:
                out.append(fid)
    return out


def _collect_historical_attachments(
    chat_id: Optional[str],
    user_id: str,
    exclude_file_ids: set,
) -> List[Dict[str, Any]]:
    """Collect all prior file references in this chat, regardless of provenance.

    Approach:
      1. Scan every `ChatMessage` in the chat — user messages contribute
         `extra_data["attachments"]`, assistant messages contribute
         `extra_data["artifacts"]` (AI-generated files from tools).
      2. Join the resulting file_ids into `Artifact` rows by primary key.
         We do NOT filter Artifact by `chat_id`, because an artifact
         imported from "My Space" keeps the chat_id of its origin chat
         but is legitimately referenced by messages in this chat.
      3. Enforce ownership (Artifact.user_id must match the requester) so
         a user can only see metadata for their own files.

    Returns entries ordered oldest-first (by message timestamp). Each has:
        {file_id, name, mime_type, summary, source, deleted?}

    Soft-caps the total summary text at `_MAX_HISTORY_SUMMARY_CHARS`,
    preserving the most recent items when the cap is exceeded.
    """
    if not chat_id or not user_id:
        return []

    try:
        from core.db.engine import SessionLocal
        from core.db.models import ChatMessage
        from core.content.artifact_reader import SOURCE_AI_GENERATED, SOURCE_USER_UPLOAD, infer_source
    except Exception:
        return []

    excluded = set(exclude_file_ids or set())

    # Step 1: pull file_ids in order from the most recent messages.
    # Cap lookback to avoid unbounded scans on very long conversations;
    # the final summary cap (_MAX_HISTORY_SUMMARY_CHARS) trims further.
    file_ids_in_order: List[str] = []
    seen_fids: set = set()
    with SessionLocal() as db:
        recent_desc = db.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
        ).order_by(ChatMessage.created_at.desc()).limit(_MAX_CHAT_MESSAGES_SCANNED).all()
        msgs = list(reversed(recent_desc))

        for m in msgs:
            for fid in _extract_message_file_ids(m):
                if fid in seen_fids or fid in excluded:
                    continue
                seen_fids.add(fid)
                file_ids_in_order.append(fid)

        if not file_ids_in_order:
            return []

        rows = db.query(ArtifactModel).filter(
            ArtifactModel.artifact_id.in_(file_ids_in_order),
        ).all()
        row_by_id = {r.artifact_id: r for r in rows}

    items: List[Dict[str, Any]] = []
    for fid in file_ids_in_order:
        art = row_by_id.get(fid)
        if art is None:
            items.append({
                "file_id": fid,
                "name": "（文件元信息丢失）",
                "deleted": True,
                "source": SOURCE_USER_UPLOAD if fid.startswith("ua_") else SOURCE_AI_GENERATED,
            })
            continue
        if art.user_id != user_id or art.deleted_at is not None:
            items.append({
                "file_id": fid,
                "name": art.filename or fid,
                "deleted": True,
                "source": infer_source(art),
            })
            continue
        items.append({
            "file_id": fid,
            "name": art.filename or art.title or fid,
            "mime_type": art.mime_type,
            "summary": art.summary or "",
            "source": infer_source(art),
            "deleted": False,
        })

    # Soft cap: keep most recent items whose cumulative summary fits budget.
    result: List[Dict[str, Any]] = []
    total_chars = 0
    for it in reversed(items):
        piece = len(it.get("summary") or "") + len(it.get("name") or "") + 80
        if result and total_chars + piece > _MAX_HISTORY_SUMMARY_CHARS:
            break
        result.append(it)
        total_chars += piece
    result.reverse()
    return result


def _build_ctx(request: ChatRequest, db_user_id: str, enabled_skills, enabled_agents, enabled_mcps, memory_enabled=False, memory_write_enabled=False, reranker_enabled=False):
    current_attachments = [a.model_dump() for a in request.attachments] if request.attachments else []
    current_file_ids = {a.get("file_id") for a in current_attachments if a.get("file_id")}

    # Backfill parsed_text + summary into Artifact rows from the frontend's
    # already-parsed `content`, so future turns can inject summaries and
    # `read_artifact` can serve full content without any re-parse.
    _backfill_artifact_cache(current_attachments, db_user_id)

    historical_files = _collect_historical_attachments(
        chat_id=request.chat_id,
        user_id=db_user_id,
        exclude_file_ids=current_file_ids,
    )

    ctx: Dict[str, Any] = {
        "model_name": request.model_name,
        "user_id": db_user_id,
        "chat_id": request.chat_id,
        "enable_thinking": request.enable_thinking,
        "uploaded_files": current_attachments,
        "historical_files": historical_files,
        "memory_enabled": memory_enabled,
        "memory_write_enabled": memory_write_enabled,
        "reranker_enabled": reranker_enabled,
        # Preserve None so downstream (SkillsMiddleware) falls back to catalog defaults.
        # Only call _clean_id_list when there's an actual list to normalize.
        "enabled_skills": _clean_id_list(enabled_skills) if enabled_skills is not None else None,
        "enabled_agents": _clean_id_list(enabled_agents) if enabled_agents is not None else None,
        "enabled_mcps": _clean_id_list(enabled_mcps) if enabled_mcps is not None else None,
        "enabled_kbs": _clean_id_list(request.enabled_kbs) if request.enabled_kbs is not None else None,
        "agent_id": request.agent_id,
        "skill_id": request.skill_id,
        "code_exec": request.code_exec,
        "plan_chat": request.plan_chat,
    }
    return ctx


def _ensure_chat_session(
    chat_service: ChatService,
    chat_id: str,
    user_id: str,
    first_message: str,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    code_exec: bool = False,
    plan_chat: bool = False,
):
    extra_data: Dict[str, Any] = {"chat_id": chat_id}
    if agent_id:
        extra_data["agent_id"] = agent_id
        if agent_name:
            extra_data["agent_name"] = agent_name
    if code_exec:
        extra_data["code_exec_chat"] = True
    if plan_chat:
        extra_data["plan_chat"] = True
    session = chat_service.ensure_session(
        chat_id=chat_id, user_id=user_id,
        title=generate_smart_title(first_message),
        extra_data=extra_data,
    )
    if session is None:
        raise HTTPException(status_code=403, detail="会话归属校验失败，无法访问该会话。")
    # Merge missing metadata flags into existing session
    existing_meta = session.extra_data or {}
    merged = dict(existing_meta)
    dirty = False
    if agent_id and not existing_meta.get("agent_id"):
        merged["agent_id"] = agent_id
        if agent_name:
            merged["agent_name"] = agent_name
        dirty = True
    if code_exec and not existing_meta.get("code_exec_chat"):
        merged["code_exec_chat"] = True
        dirty = True
    if plan_chat and not existing_meta.get("plan_chat"):
        merged["plan_chat"] = True
        dirty = True
    if dirty:
        chat_service.update_session(chat_id, user_id, {"extra_data": merged})
    return session


def _load_session_messages(chat_service: ChatService, chat_id: str, user_id: str) -> List[Dict[str, Any]]:
    messages = chat_service.list_all_messages(chat_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail=f"Session {chat_id} not found")
    normalized: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.content
        if msg.role == "user":
            quoted = (msg.extra_data or {}).get("quoted_follow_up")
            content = _build_effective_user_message(content, quoted)
        normalized.append({"role": msg.role, "content": content})
    return normalized


@router.post("/send", response_model=ChatResponse, summary="非流式聊天")
async def chat_send(
    request: ChatRequest,
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    _ensure_main_model_configured()
    chat_service = ChatService(db)
    effective_user_message = _build_effective_user_message(request.message, request.quoted_follow_up)
    db_user_id = resolve_db_user_id(db, _authenticated_user_id(user), request.user_id)
    enabled_skills, enabled_agents, enabled_mcps = None, None, None

    # ── Validate agent_id if present ──
    _agent_name: Optional[str] = None
    if request.agent_id:
        from core.services.user_agent_service import UserAgentService
        agent_svc = UserAgentService(db)
        try:
            agent_info = agent_svc.get_by_id(request.agent_id, user_id=db_user_id)
            _agent_name = agent_info["name"]
        except (LookupError, PermissionError):
            raise HTTPException(status_code=403, detail="无法访问该子智能体")

    try:
        _ensure_chat_session(
            chat_service, request.chat_id, db_user_id, request.message,
            agent_id=request.agent_id, agent_name=_agent_name,
            code_exec=request.code_exec, plan_chat=request.plan_chat,
        )
        # Link orphan artifacts (uploaded before session existed) to this chat
        if request.attachments:
            _att_ids = [a.file_id for a in request.attachments if a.file_id]
            if _att_ids:
                from core.db.models import Artifact as _ArtModel
                db.query(_ArtModel).filter(
                    _ArtModel.artifact_id.in_(_att_ids),
                    _ArtModel.user_id == db_user_id,
                    _ArtModel.chat_id.is_(None),
                ).update({"chat_id": request.chat_id}, synchronize_session="fetch")
                db.commit()
        session_messages = _load_session_messages(chat_service, request.chat_id, db_user_id)
        session_messages.append({"role": "user", "content": effective_user_message})
        chat_service.add_message(
            chat_id=request.chat_id, role="user", content=request.message,
            model=request.model_name, extra_data=_build_user_extra_data(request),
        )

        ctx = _build_ctx(request, db_user_id, enabled_skills, enabled_agents, enabled_mcps)

        def _run():
            return run_chat_workflow(session_messages=session_messages, user_message=effective_user_message, context=ctx)

        result = await anyio.to_thread.run_sync(_run)

        follow_up_questions = await get_followup_generator().generate(request.message, result.response)

        chat_service.add_message(
            chat_id=request.chat_id, role="assistant", content=result.response,
            model=request.model_name,
            extra_data={
                "timestamp": now_iso(), "route": result.route, "is_markdown": result.is_markdown,
                "sources": result.sources, "artifacts": result.artifacts, "warnings": result.warnings,
                "citations": list(result.meta.get("citations", [])) if isinstance(result.meta, dict) else [],
                "follow_up_questions": follow_up_questions,
            },
        )

        return ChatResponse(
            chat_id=request.chat_id, response=result.response, timestamp=now_iso(),
            is_markdown=result.is_markdown, route=result.route,
            sources=result.sources, artifacts=result.artifacts, warnings=result.warnings,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("chat_send_failed", chat_id=request.chat_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=resolve_user_facing_error(e))


@router.post("/stream", summary="流式聊天 (SSE)")
async def chat_stream(
    request: ChatRequest,
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    _ensure_main_model_configured()
    chat_service = ChatService(db)
    effective_user_message = _build_effective_user_message(request.message, request.quoted_follow_up)
    db_user_id = resolve_db_user_id(db, _authenticated_user_id(user), request.user_id)
    enabled_skills, enabled_agents, enabled_mcps = None, None, None

    _user_svc = UserService(db)
    _user_settings = _user_svc.get_user_settings(db_user_id)
    _memory_enabled = bool(_user_settings.get("memory_enabled", False))
    _memory_write_enabled = bool(_user_settings.get("memory_write_enabled", False))
    _reranker_enabled = bool(_user_settings.get("reranker_enabled", False))

    # ── Validate agent_id if present ──
    _agent_name_stream: Optional[str] = None
    if request.agent_id:
        from core.services.user_agent_service import UserAgentService
        agent_svc = UserAgentService(db)
        try:
            agent_info = agent_svc.get_by_id(request.agent_id, user_id=db_user_id)
            _agent_name_stream = agent_info["name"]
        except (LookupError, PermissionError):
            raise HTTPException(status_code=403, detail="无法访问该子智能体")

    _ensure_chat_session(
        chat_service, request.chat_id, db_user_id, request.message,
        agent_id=request.agent_id, agent_name=_agent_name_stream,
        code_exec=request.code_exec, plan_chat=request.plan_chat,
    )

    # Link orphan artifacts (uploaded before session existed) to this chat
    if request.attachments:
        _att_ids = [a.file_id for a in request.attachments if a.file_id]
        if _att_ids:
            from core.db.models import Artifact as _ArtModel
            db.query(_ArtModel).filter(
                _ArtModel.artifact_id.in_(_att_ids),
                _ArtModel.user_id == db_user_id,
                _ArtModel.chat_id.is_(None),
            ).update({"chat_id": request.chat_id}, synchronize_session="fetch")
            db.commit()

    async def generate():
        try:
            session_messages = _load_session_messages(chat_service, request.chat_id, db_user_id)
            session_messages.append({"role": "user", "content": effective_user_message})
            chat_service.add_message(
                chat_id=request.chat_id, role="user", content=request.message,
                model=request.model_name, extra_data=_build_user_extra_data(request),
            )

            context = _build_ctx(
                request, db_user_id, enabled_skills, enabled_agents, enabled_mcps,
                memory_enabled=_memory_enabled, memory_write_enabled=_memory_write_enabled, reranker_enabled=_reranker_enabled,
            )

            # If the session was created as a plan_chat session, ensure the context
            # reflects that even when the frontend doesn't resend plan_chat=true.
            if not context.get("plan_chat"):
                _sess_for_plan = chat_service.get_session(request.chat_id, db_user_id)
                if _sess_for_plan and (_sess_for_plan.extra_data or {}).get("plan_chat"):
                    context["plan_chat"] = True

            # ── Check if user is confirming a pending plan ────────────────────
            _CONFIRM_RE = r"^(确认执行|确认|执行|开始执行|yes|ok|确定)$"
            _pending_plan_id: Optional[str] = None
            if re.match(_CONFIRM_RE, request.message.strip(), re.IGNORECASE):
                _session = chat_service.get_session(request.chat_id, db_user_id)
                if _session:
                    _pending_plan_id = (_session.extra_data or {}).get("pending_plan_id")

            if _pending_plan_id:
                # User confirmed — execute the pending plan
                plan_svc = PlanService(db)
                plan_svc.update_plan(_pending_plan_id, status="approved")
                # Clear pending_plan_id from session
                _session = chat_service.get_session(request.chat_id, db_user_id)
                if _session:
                    _ed = dict(_session.extra_data or {})
                    _ed.pop("pending_plan_id", None)
                    _session.extra_data = _ed
                    db.commit()

                # Re-emit plan info so frontend can restore plan card context
                _plan_obj = plan_svc.get_plan(_pending_plan_id, db_user_id)
                if _plan_obj:
                    _plan_evt = PlanService.plan_to_dict(_plan_obj)
                    _plan_evt["type"] = "plan_generated"
                    _plan_evt["executing"] = True  # signal frontend to show executing mode
                    yield f"data: {json.dumps({**_plan_evt, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"

                result_text = ""
                async for evt in astream_execute_plan(
                    plan_id=_pending_plan_id,
                    user_id=db_user_id,
                    db=db,
                    model_name=request.model_name,
                    enabled_mcp_ids=enabled_mcps,
                    enabled_skill_ids=enabled_skills,
                    enabled_agent_ids=enabled_agents,
                    session_messages=session_messages[:-1],
                ):
                    yield f"data: {json.dumps({**evt, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"
                    if evt.get("type") == "plan_complete":
                        result_text = evt.get("result_text") or ""
                chat_service.add_message(
                    chat_id=request.chat_id, role="assistant", content=result_text,
                    model=request.model_name,
                )
                yield "data: [DONE]\n\n"
                return

            # ── Plan mode: generate plan ─────────────────────────────────────
            if context.get("plan_chat"):
                plan_id: Optional[str] = None
                _plan_evt_data: Optional[dict] = None

                async for evt in astream_generate_plan(
                    task_description=request.message,
                    user_id=db_user_id,
                    db=db,
                    model_name=request.model_name,
                    enabled_mcp_ids=enabled_mcps,
                    enabled_skill_ids=enabled_skills,
                    enabled_agent_ids=enabled_agents,
                    session_messages=session_messages[:-1],
                ):
                    yield f"data: {json.dumps({**evt, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"
                    if evt.get("type") == "plan_generated":
                        plan_id = evt.get("plan_id")
                        _plan_evt_data = evt

                if plan_id and _plan_evt_data:
                    _session = chat_service.get_session(request.chat_id, db_user_id)
                    if _session:
                        _ed = dict(_session.extra_data or {})
                        _ed["pending_plan_id"] = plan_id
                        _session.extra_data = _ed
                        db.commit()
                    # Persist the plan as an assistant message so it appears in history
                    _plan_title = _plan_evt_data.get("title", "执行计划")
                    _plan_desc = _plan_evt_data.get("description", "")
                    _plan_steps = _plan_evt_data.get("steps", [])
                    _step_summary = "\n".join(
                        f"{i+1}. {s.get('title', '')}" for i, s in enumerate(_plan_steps)
                    )
                    _plan_content = f"已生成执行计划：**{_plan_title}**\n\n{_plan_desc}\n\n**执行步骤：**\n{_step_summary}"
                    chat_service.add_message(
                        chat_id=request.chat_id, role="assistant", content=_plan_content,
                        model=request.model_name,
                        extra_data={
                            "is_markdown": True,
                            "plan_id": plan_id,
                            "plan_snapshot": {
                                "mode": "preview",
                                "title": _plan_title,
                                "description": _plan_desc,
                                "steps": [
                                    {
                                        "step_order": s.get("step_order", i + 1),
                                        "title": s.get("title", ""),
                                        "description": s.get("description"),
                                        "expected_tools": s.get("expected_tools", []),
                                        "expected_skills": s.get("expected_skills", []),
                                    }
                                    for i, s in enumerate(_plan_steps)
                                ],
                                "total_steps": len(_plan_steps),
                                "completed_steps": 0,
                            },
                        },
                    )
                    yield f"data: {json.dumps({'type': 'plan_needs_confirmation', 'plan_id': plan_id, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # ── Normal mode: call LLM directly (with tools) ──────────────────
            full_response = ""
            pending_message_id = f"msg_{uuid.uuid4().hex[:16]}"
            metadata: dict = {}
            tool_calls_log: list = []
            collected_artifacts: list = []  # file references from tool_result

            async for chunk in astream_chat_workflow(
                session_messages=session_messages, user_message=effective_user_message, context=context,
            ):
                chunk_type = chunk.get("type")

                if chunk_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'message': chunk.get('message', '正在思考...'), 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"

                elif chunk_type in {"ai_message", "content"}:
                    delta = chunk.get("delta", "")
                    if delta:
                        full_response += delta
                        yield f"data: {json.dumps({'type': 'content', 'event': 'ai_message', 'delta': delta, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"

                elif chunk_type == "tool_call":
                    tc: dict = {
                        "tool_name": chunk.get("tool_name"), "tool_display_name": chunk.get("tool_display_name"),
                        "tool_args": chunk.get("tool_args", {}), "tool_id": chunk.get("tool_id"),
                    }
                    if chunk.get("subagent_name"):
                        tc["subagent_name"] = chunk["subagent_name"]
                    _upsert_tool_call(tool_calls_log, tc)
                    yield f"data: {json.dumps({'type': 'tool_call', **tc, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"

                elif chunk_type == "tool_result":
                    _tid, _tn, _res = chunk.get("tool_id"), chunk.get("tool_name"), chunk.get("result", {})
                    _tr_evt: dict = {'type': 'tool_result', 'tool_name': _tn, 'result': _res, 'tool_id': _tid, 'chat_id': request.chat_id, 'citations': chunk.get('citations', [])}
                    if chunk.get("subagent_name"):
                        _tr_evt["subagent_name"] = chunk["subagent_name"]
                    yield f"data: {json.dumps(_tr_evt, ensure_ascii=False)}\n\n"
                    _attach_tool_result(tool_calls_log, _tid, _tn, _res)
                    _file_refs = extract_file_refs(_res)
                    for _file_ref in _file_refs:
                        _file_ref["tool_name"] = _tn or ""
                    _extend_collected_artifacts(collected_artifacts, _file_refs)

                elif chunk_type == "heartbeat":
                    # SSE comment keeps Nginx/client connection alive
                    yield ": heartbeat\n\n"

                elif chunk_type == "tool_pending":
                    _tp_evt = {"type": "tool_pending", "chat_id": request.chat_id,
                               "reason": chunk.get("reason", "llm_buffering")}
                    yield f"data: {json.dumps(_tp_evt, ensure_ascii=False)}\n\n"

                elif chunk_type == "meta":
                    pending_message_id = f"msg_{uuid.uuid4().hex[:16]}"
                    metadata = {
                        "type": "meta", "route": chunk.get("route", "main"),
                        "sources": chunk.get("sources", []), "artifacts": chunk.get("artifacts", []),
                        "warnings": chunk.get("warnings", []), "is_markdown": chunk.get("is_markdown", False),
                        "chat_id": request.chat_id, "message_id": pending_message_id,
                        "citations": chunk.get("citations", []),
                    }
                    yield f"data: {json.dumps(metadata, ensure_ascii=False)}\n\n"

                    # Save message immediately (without follow_up_questions)
                    _usage = chunk.get("usage") or None
                    chat_service.add_message(
                        chat_id=request.chat_id, role="assistant", content=full_response,
                        model=request.model_name, tool_calls=tool_calls_log if tool_calls_log else None,
                        usage=_usage,
                        message_id=pending_message_id,
                        extra_data={
                            "timestamp": now_iso(), "route": metadata.get("route"),
                            "is_markdown": metadata.get("is_markdown", False),
                            "sources": metadata.get("sources", []), "artifacts": metadata.get("artifacts", []),
                            "warnings": metadata.get("warnings", []), "citations": metadata.get("citations", []),
                            "message_id": pending_message_id,
                        },
                    )

                    _persist_artifacts(db, db_user_id, request.chat_id, collected_artifacts)


                    yield "data: [DONE]\n\n"

                    # Generate follow-up questions in a background task
                    _fup_user_msg = request.message
                    _fup_response = full_response
                    _fup_msg_id = pending_message_id

                    async def _generate_followups_bg(user_msg: str, response: str, msg_id: str):
                        try:
                            clean_resp = strip_thinking(response)
                            questions = await asyncio.wait_for(
                                get_followup_generator().generate(user_msg, clean_resp),
                                timeout=10,
                            )
                            if questions:
                                from core.db.engine import SessionLocal
                                with SessionLocal() as bg_db:
                                    bg_svc = ChatService(bg_db)
                                    bg_svc.update_message_extra_data(msg_id, {"follow_up_questions": questions})
                        except Exception as exc:
                            logger.warning("background follow_up generation failed: %r", exc)

                    asyncio.create_task(_generate_followups_bg(_fup_user_msg, _fup_response, _fup_msg_id))

        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else "请求处理失败，请稍后重试"
            yield f"data: {json.dumps({'type': 'error', 'error': detail, 'delta': detail, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error("chat_stream_failed", chat_id=request.chat_id, error=str(e), exc_info=True)
            user_facing = resolve_user_facing_error(e)
            try:
                _ensure_chat_session(chat_service, request.chat_id, db_user_id, request.message)
                chat_service.add_message(
                    chat_id=request.chat_id, role="assistant", content="",
                    model=request.model_name, error={"error": str(e), "timestamp": now_iso()},
                )
            except Exception:
                pass
            yield f"data: {json.dumps({'type': 'error', 'error': user_facing, 'delta': user_facing, 'chat_id': request.chat_id}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ── Regenerate / Edit ────────────────────────────────────────────────────

def _restore_attachments(saved: List[Dict]) -> List[AttachmentItem]:
    """Reconstruct AttachmentItem list from extra_data['attachments'] metadata."""
    return [
        AttachmentItem(
            name=a.get("name", ""), content="",
            mime_type=a.get("mime_type", ""), file_id=a.get("file_id", ""),
            download_url=a.get("download_url", ""),
        )
        for a in saved if a.get("file_id")
    ]


async def _stream_sse_response(
    *,
    chat_service: ChatService,
    chat_id: str,
    model_name: str,
    session_messages: List[Dict[str, Any]],
    user_message: str,
    context: Dict[str, Any],
    user_content_for_followup: Optional[str] = None,
    error_label: str = "stream_failed",
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
):
    """Shared SSE generator for chat_stream, regenerate, and edit endpoints.

    Yields SSE-formatted strings. Handles tool_call, tool_result, thinking,
    content, heartbeat, and meta events. Persists the assistant message and
    optionally generates follow-up questions in the background.
    """
    try:
        full_response = ""
        pending_message_id = f"msg_{uuid.uuid4().hex[:16]}"
        metadata: dict = {}
        tool_calls_log: list = []
        collected_artifacts: list = []

        async for chunk in astream_chat_workflow(
            session_messages=session_messages, user_message=user_message, context=context,
        ):
            chunk_type = chunk.get("type")
            if chunk_type == "thinking":
                yield f"data: {json.dumps({'type': 'thinking', 'message': chunk.get('message', '正在思考...'), 'chat_id': chat_id}, ensure_ascii=False)}\n\n"
            elif chunk_type in {"ai_message", "content"}:
                delta = chunk.get("delta", "")
                if delta:
                    full_response += delta
                    yield f"data: {json.dumps({'type': 'content', 'event': 'ai_message', 'delta': delta, 'chat_id': chat_id}, ensure_ascii=False)}\n\n"
            elif chunk_type == "tool_call":
                tc: dict = {
                    "tool_name": chunk.get("tool_name"), "tool_display_name": chunk.get("tool_display_name"),
                    "tool_args": chunk.get("tool_args", {}), "tool_id": chunk.get("tool_id"),
                }
                if chunk.get("subagent_name"):
                    tc["subagent_name"] = chunk["subagent_name"]
                _upsert_tool_call(tool_calls_log, tc)
                yield f"data: {json.dumps({'type': 'tool_call', **tc, 'chat_id': chat_id}, ensure_ascii=False)}\n\n"
            elif chunk_type == "tool_result":
                _tid, _tn, _res = chunk.get("tool_id"), chunk.get("tool_name"), chunk.get("result", {})
                _tr_evt: dict = {'type': 'tool_result', 'tool_name': _tn, 'result': _res, 'tool_id': _tid, 'chat_id': chat_id, 'citations': chunk.get('citations', [])}
                if chunk.get("subagent_name"):
                    _tr_evt["subagent_name"] = chunk["subagent_name"]
                yield f"data: {json.dumps(_tr_evt, ensure_ascii=False)}\n\n"
                _attach_tool_result(tool_calls_log, _tid, _tn, _res)
                _file_refs = extract_file_refs(_res)
                for _file_ref in _file_refs:
                    _file_ref["tool_name"] = _tn or ""
                _extend_collected_artifacts(collected_artifacts, _file_refs)
            elif chunk_type == "heartbeat":
                yield ": heartbeat\n\n"
            elif chunk_type == "tool_pending":
                _tp_evt = {"type": "tool_pending", "chat_id": chat_id,
                           "reason": chunk.get("reason", "llm_buffering")}
                yield f"data: {json.dumps(_tp_evt, ensure_ascii=False)}\n\n"
            elif chunk_type == "meta":
                pending_message_id = f"msg_{uuid.uuid4().hex[:16]}"
                metadata = {
                    "type": "meta", "route": chunk.get("route", "main"),
                    "sources": chunk.get("sources", []), "artifacts": chunk.get("artifacts", []),
                    "warnings": chunk.get("warnings", []), "is_markdown": chunk.get("is_markdown", False),
                    "chat_id": chat_id, "message_id": pending_message_id,
                    "citations": chunk.get("citations", []),
                }
                yield f"data: {json.dumps(metadata, ensure_ascii=False)}\n\n"

                _usage = chunk.get("usage") or None
                chat_service.add_message(
                    chat_id=chat_id, role="assistant", content=full_response,
                    model=model_name, tool_calls=tool_calls_log if tool_calls_log else None,
                    usage=_usage, message_id=pending_message_id,
                    extra_data={
                        "timestamp": now_iso(), "route": metadata.get("route"),
                        "is_markdown": metadata.get("is_markdown", False),
                        "sources": metadata.get("sources", []), "artifacts": metadata.get("artifacts", []),
                        "warnings": metadata.get("warnings", []), "citations": metadata.get("citations", []),
                        "message_id": pending_message_id,
                    },
                )
                if db and user_id:
                    _persist_artifacts(db, user_id, chat_id, collected_artifacts)

                yield "data: [DONE]\n\n"

                # Background follow-up generation
                _fup_user = user_content_for_followup or user_message
                _fup_resp = full_response
                _fup_id = pending_message_id

                async def _generate_followups_bg(u: str, r: str, mid: str):
                    try:
                        clean = strip_thinking(r)
                        questions = await asyncio.wait_for(
                            get_followup_generator().generate(u, clean), timeout=10,
                        )
                        if questions:
                            from core.db.engine import SessionLocal
                            with SessionLocal() as bg_db:
                                ChatService(bg_db).update_message_extra_data(mid, {"follow_up_questions": questions})
                    except Exception as exc:
                        logger.warning("background follow_up generation failed: %r", exc)

                asyncio.create_task(_generate_followups_bg(_fup_user, _fup_resp, _fup_id))

    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else "请求处理失败，请稍后重试"
        yield f"data: {json.dumps({'type': 'error', 'error': detail, 'chat_id': chat_id}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(error_label, chat_id=chat_id, error=str(e), exc_info=True)
        user_facing = resolve_user_facing_error(e)
        yield f"data: {json.dumps({'type': 'error', 'error': user_facing, 'chat_id': chat_id}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


def _make_sse_streaming_response(generator):
    """Wrap an SSE async generator in a StreamingResponse with standard headers."""
    return StreamingResponse(
        generator, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class RegenerateRequest(BaseModel):
    """Request body for regenerating an assistant response."""
    message_index: int = Field(..., description="0-based index of the assistant message in the chat")


class EditAndResendRequest(BaseModel):
    """Request body for editing a user message and regenerating."""
    message_index: int = Field(..., description="0-based index of the user message in the chat")
    new_content: str = Field(..., min_length=1, max_length=10000, description="New content for the user message")


@router.post("/{chat_id}/regenerate", summary="重新生成助手回复 (SSE)")
async def regenerate_message(
    chat_id: str,
    body: RegenerateRequest,
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    """Delete the target assistant message and all subsequent, then re-stream."""
    _ensure_main_model_configured()
    chat_service = ChatService(db)
    db_user_id = resolve_db_user_id(db, _authenticated_user_id(user))

    target_msg = chat_service.get_message_by_index(chat_id, body.message_index)
    if not target_msg or target_msg.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="消息不存在")

    user_msg = chat_service.get_user_message_before(chat_id, target_msg.message_id)
    if not user_msg:
        raise HTTPException(status_code=400, detail="找不到对应的用户消息")

    user_content = user_msg.content
    user_extra = user_msg.extra_data or {}
    attachment_items = _restore_attachments(user_extra.get("attachments", []))

    chat_service.delete_messages_from(chat_id, target_msg.message_id)

    regen_request = ChatRequest(
        chat_id=chat_id, message=user_content,
        model_name=target_msg.model or "qwen",
        enable_thinking=user_extra.get("enable_thinking", False),
        quoted_follow_up=user_extra.get("quoted_follow_up"),
        attachments=attachment_items,
    )
    enabled_skills, enabled_agents, enabled_mcps = None, None, None
    _user_settings = UserService(db).get_user_settings(db_user_id)
    effective_msg = _build_effective_user_message(regen_request.message, regen_request.quoted_follow_up)

    session_messages = _load_session_messages(chat_service, chat_id, db_user_id)
    session_messages.append({"role": "user", "content": effective_msg})
    context = _build_ctx(
        regen_request, db_user_id, enabled_skills, enabled_agents, enabled_mcps,
        memory_enabled=bool(_user_settings.get("memory_enabled", False)),
        memory_write_enabled=bool(_user_settings.get("memory_write_enabled", False)),
        reranker_enabled=bool(_user_settings.get("reranker_enabled", False)),
    )

    return _make_sse_streaming_response(_stream_sse_response(
        chat_service=chat_service, chat_id=chat_id, model_name=regen_request.model_name or "qwen",
        session_messages=session_messages, user_message=effective_msg, context=context,
        user_content_for_followup=user_content, error_label="regenerate_failed",
        db=db, user_id=db_user_id,
    ))


@router.post("/{chat_id}/edit", summary="编辑消息并重新生成 (SSE)")
async def edit_and_resend(
    chat_id: str,
    body: EditAndResendRequest,
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    """Delete the target user message and all subsequent, then re-stream with new content."""
    _ensure_main_model_configured()
    chat_service = ChatService(db)
    db_user_id = resolve_db_user_id(db, _authenticated_user_id(user))

    target_msg = chat_service.get_message_by_index(chat_id, body.message_index)
    if not target_msg or target_msg.chat_id != chat_id or target_msg.role != "user":
        raise HTTPException(status_code=404, detail="用户消息不存在")

    target_extra = target_msg.extra_data or {}
    saved_attachments = target_extra.get("attachments", [])
    attachment_items = _restore_attachments(saved_attachments)

    chat_service.delete_messages_from(chat_id, target_msg.message_id)

    edit_request = ChatRequest(
        chat_id=chat_id, message=body.new_content,
        model_name=target_msg.model or "qwen",
        attachments=attachment_items,
    )
    enabled_skills, enabled_agents, enabled_mcps = None, None, None
    _user_settings = UserService(db).get_user_settings(db_user_id)

    # Persist the edited user message
    _edit_extra: Dict[str, Any] = {"timestamp": now_iso()}
    if saved_attachments:
        _edit_extra["attachments"] = saved_attachments

    session_messages = _load_session_messages(chat_service, chat_id, db_user_id)
    session_messages.append({"role": "user", "content": body.new_content})
    chat_service.add_message(
        chat_id=chat_id, role="user", content=body.new_content,
        model=edit_request.model_name, extra_data=_edit_extra,
    )
    context = _build_ctx(
        edit_request, db_user_id, enabled_skills, enabled_agents, enabled_mcps,
        memory_enabled=bool(_user_settings.get("memory_enabled", False)),
        memory_write_enabled=bool(_user_settings.get("memory_write_enabled", False)),
        reranker_enabled=bool(_user_settings.get("reranker_enabled", False)),
    )

    return _make_sse_streaming_response(_stream_sse_response(
        chat_service=chat_service, chat_id=chat_id, model_name=edit_request.model_name or "qwen",
        session_messages=session_messages, user_message=body.new_content, context=context,
        error_label="edit_resend_failed",
        db=db, user_id=db_user_id,
    ))


# ── Feedback ──────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    rating: str
    comment: Optional[str] = None
    chat_id: Optional[str] = None


@router.post("/messages/{message_id}/feedback", summary="消息反馈")
async def submit_feedback(
    message_id: str,
    body: FeedbackRequest,
    user: Optional[UserContext] = Depends(require_auth(required=False)),
    db: Session = Depends(get_db),
):
    if body.rating not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="rating must be 'like' or 'dislike'")

    db_user_id = _authenticated_user_id(user)

    existing = db.query(MessageFeedback).filter(
        MessageFeedback.message_id == message_id, MessageFeedback.user_id == db_user_id,
    ).first()
    if existing:
        existing.rating = body.rating
        existing.comment = body.comment
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        record = existing
    else:
        record = MessageFeedback(
            message_id=message_id, chat_id=body.chat_id or "",
            user_id=db_user_id, rating=body.rating, comment=body.comment,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

    return {"ok": True, "feedback_id": record.feedback_id, "rating": record.rating}
