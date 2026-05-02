"""AgentScope hooks replacing LangChain middleware.

Each hook is a factory function that returns an async callable matching
AgentScope's hook signatures (pre_reply, post_reply, etc.).

Hooks access runtime context via agent._jx_context (a ModelContext instance).
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MAX_FILE_CONTENT_CHARS = 50_000  # 单文件截断阈值


class ModelContext(BaseModel):
    """Context schema used by the agent runtime."""

    model_name: str = ""
    user_id: str | None = None
    chat_id: str | None = None
    enable_thinking: bool = True
    uploaded_files: List[Dict[str, Any]] = []
    # Files uploaded in previous turns of the same chat. Only their
    # filename+summary is injected into prompt; the agent pulls full content
    # on demand via the `read_artifact` tool.
    historical_files: List[Dict[str, Any]] = []


# ── DynamicModel hook (replaces DynamicModelMiddleware) ──────────────────

_main_model = None
_main_fast_model = None
_cached_version: int = -1
_named_model_cache: Dict[str, Any] = {}


def _check_version():
    """Invalidate cached model instances when ModelConfigService version changes."""
    global _main_model, _main_fast_model, _cached_version, _named_model_cache
    try:
        from core.config.model_config import ModelConfigService
        current = ModelConfigService.get_instance().version
    except Exception:
        return
    if current != _cached_version:
        _main_model = None
        _main_fast_model = None
        _named_model_cache = {}
        _cached_version = current


def _get_main_model(fast: bool = False):
    """Get the main agent model, resolved from DB (main_agent role)."""
    global _main_model, _main_fast_model
    from core.llm.chat_models import get_default_model
    _check_version()
    if fast:
        if _main_fast_model is None:
            _main_fast_model = get_default_model(disable_thinking=True, stream=True)
        return _main_fast_model
    if _main_model is None:
        _main_model = get_default_model(stream=True)
    return _main_model


def _get_named_model(model_name: str):
    """Resolve and cache a model by name (concrete model_name, not role key)."""
    _check_version()
    if model_name in _named_model_cache:
        return _named_model_cache[model_name]
    try:
        from core.config.model_config import ModelConfigService, ResolvedModelConfig
        from core.db.engine import SessionLocal
        from core.db.models import ModelProvider
        from core.llm.chat_models import make_chat_model
        svc = ModelConfigService.get_instance()
        cfg = svc.resolve(model_name)
        if cfg is None:
            with SessionLocal() as _db:
                prov = _db.query(ModelProvider).filter(
                    ModelProvider.model_name == model_name,
                    ModelProvider.is_active == True,
                    ModelProvider.provider_type == "chat",
                ).first()
                if prov:
                    extra = dict(prov.extra_config or {})
                    cfg = ResolvedModelConfig(
                        base_url=prov.base_url,
                        api_key=prov.api_key,
                        model_name=prov.model_name,
                        temperature=float(extra.get("temperature", 0.6)),
                        max_tokens=int(extra.get("max_tokens", 8192)),
                        timeout=int(extra.get("timeout", 120)),
                        extra={},
                    )
        if cfg:
            model = make_chat_model(
                model=cfg.model_name,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                stream=True,
            )
            _named_model_cache[model_name] = model
            return model
    except Exception as exc:
        logger.warning("[hooks] _get_named_model(%s) failed: %s", model_name, exc)
    return None


def make_dynamic_model_hook():
    """Create a pre_reply hook that swaps the agent's model based on context."""

    async def dynamic_model_pre_reply(agent, kwargs):
        ctx = getattr(agent, "_jx_context", None)
        if ctx is None:
            return
        # If a specific model was requested (e.g. minimax-m27 for simple steps), use it
        override_name = getattr(ctx, "model_name", "")
        if override_name:
            named = _get_named_model(override_name)
            if named is not None:
                agent.model = named
                return
        fast = not getattr(ctx, "enable_thinking", True)
        model = _get_main_model(fast=fast)
        agent.model = model

    return dynamic_model_pre_reply


# ── FileContext hook (replaces FileContextMiddleware) ─────────────────────

def _is_image(f: Dict[str, Any]) -> bool:
    mime = (f.get("mime_type") or "").lower()
    return mime.startswith("image/")


def _fetch_image_base64(f: Dict[str, Any]) -> Optional[tuple[str, str]]:
    """Fetch image data for a file attachment and return (base64_data, mime_type)."""
    from core.content.artifact_reader import resolve_artifact_storage

    file_id = f.get("file_id") or ""
    mime_type = (f.get("mime_type") or "image/png").lower()
    if not file_id:
        return None

    storage_key, _ = resolve_artifact_storage(file_id, f.get("name") or "image")
    if not storage_key:
        return None

    try:
        from core.storage import get_storage
        from core.infra.exceptions import StorageError

        data = base64.b64encode(get_storage().download_bytes(storage_key)).decode("utf-8")
        return data, mime_type
    except StorageError as e:
        logger.warning(f"Image hook: storage download failed for {file_id}: {e}")
        return None


def _build_file_context(uploaded_files: List[Dict[str, Any]]) -> str:
    """将文本附件列表拼成注入模型的上下文文本（仅非图片文件）。

    当 content 为空但有 file_id 时（如从我的空间导入），自动从存储下载并解析文档内容。
    """
    text_files = [f for f in uploaded_files if not _is_image(f)]
    if not text_files:
        return ""

    file_descriptions = []
    for f in text_files:
        name = f.get('name', '未知文件')
        fid = f.get('file_id', '')
        desc = f"- {name}"
        if fid:
            desc += f"  (file_id: {fid})"
        file_descriptions.append(desc)
    from core.content.artifact_reader import fetch_parsed_text

    file_content_parts: List[str] = []
    for f in text_files:
        content = (f.get("content") or "").strip()
        # 如果没有文本内容但有 file_id（从我的空间导入），从存储拉取并解析
        fid = f.get("file_id")
        if not content and fid:
            logger.info(f"Document hook: fetching content from storage for {f.get('name')} ({fid})")
            content = fetch_parsed_text(fid)
        if content:
            if len(content) > _MAX_FILE_CONTENT_CHARS:
                content = content[:_MAX_FILE_CONTENT_CHARS] + "\n... (内容过长，已截断)"
        file_content_parts.append(content)
    file_content = "\n\n---\n\n".join(file_content_parts)

    return (
        f"[file name]: {chr(10).join(file_descriptions)}\n"
        f"[file content begin]\n"
        f"{file_content}\n"
        f"[file content end]\n"
        f"这是用户所上传的文件内容，请你根据文件内容，结合用户的问题进行回答"
    )


def _source_label(source: str) -> str:
    from core.content.artifact_reader import SOURCE_AI_GENERATED, SOURCE_USER_UPLOAD
    return {SOURCE_USER_UPLOAD: "用户上传", SOURCE_AI_GENERATED: "AI 生成"}.get(source, "")


def _build_historical_files_context(historical_files: List[Dict[str, Any]]) -> str:
    """Build a compact summary block for files from previous turns.

    Includes both user-uploaded files and AI-generated files (reports,
    charts, code output, etc.) produced by tools in earlier turns.
    Each entry is labeled by provenance so the agent knows what kind of
    file it's referencing.

    Agent gets only `{filename, file_id, source, summary}`; full content
    is fetched on-demand via the `read_artifact` tool. This keeps prompt
    size bounded as the conversation grows.
    """
    if not historical_files:
        return ""

    lines: List[str] = []
    for f in historical_files:
        file_id = f.get("file_id") or ""
        name = f.get("name") or f.get("filename") or "未命名文件"
        mime = f.get("mime_type") or ""
        summary = (f.get("summary") or "").strip()
        source = f.get("source") or ""
        deleted = bool(f.get("deleted"))

        source_label = _source_label(source)
        header_parts = [f"file_id: {file_id}"]
        if source_label:
            header_parts.append(source_label)
        if mime:
            header_parts.append(mime)
        header = f"- {name}  ({', '.join(header_parts)})"

        if deleted:
            lines.append(header + "  [文件已删除，无法读取]")
            continue

        lines.append(header)
        if summary:
            # Indent summary so block structure is clear to the model.
            indented = "\n".join(f"  {ln}" for ln in summary.splitlines())
            lines.append(f"  摘要：\n{indented}")
        else:
            lines.append("  摘要：（尚未生成，可调用 read_artifact 读取完整内容）")

    header = (
        "[历史文件清单]（本会话中之前轮次涉及的文件，包含用户上传和 AI 生成的两类，以下仅为摘要）\n"
        "如需完整内容、具体章节或更多细节，请调用 `read_artifact(file_id, offset, limit)` 工具按需读取。"
    )
    return header + "\n\n" + "\n".join(lines)


def make_file_context_hook():
    """Create a pre_reply hook that injects uploaded file context into memory.

    - Text/document files: injected as a text Msg with extracted content.
    - Image files: injected as a multimodal Msg containing ImageBlock(s) so
      vision-capable models (e.g. Qwen-VL, GPT-4o) can see them directly.
    """
    from agentscope.message import Msg

    async def file_context_pre_reply(agent, kwargs):
        ctx = getattr(agent, "_jx_context", None)
        if ctx is None:
            return
        uploaded_files = list(getattr(ctx, "uploaded_files", None) or [])
        historical_files = list(getattr(ctx, "historical_files", None) or [])
        if not uploaded_files and not historical_files:
            return

        # Only inject file context once (first reply iteration)
        if getattr(agent, "_jx_file_context_injected", False):
            return
        agent._jx_file_context_injected = True  # type: ignore[attr-defined]

        logger.info(
            f"File context hook: {len(uploaded_files)} current, "
            f"{len(historical_files)} historical; "
            f"current={[f.get('name') for f in uploaded_files]}"
        )

        # ── 0. Historical files: inject summaries only (no full content) ──
        if historical_files:
            hist_context = _build_historical_files_context(historical_files)
            if hist_context:
                logger.info(f"File context hook: injecting {len(historical_files)} historical summaries ({len(hist_context)} chars)")
                hist_msg = Msg(name="user", content=hist_context, role="user")
                await agent.memory.add(hist_msg)

        # ── 1. Inject text-file context (non-images) for CURRENT turn ──
        text_context = _build_file_context(uploaded_files) if uploaded_files else ""
        if text_context:
            logger.info(f"File context hook: injecting text context ({len(text_context)} chars)")
            file_msg = Msg(name="user", content=text_context, role="user")
            await agent.memory.add(file_msg)

        # ── 2. Merge image files into the last user message in memory ──
        # Combining images with the user's question in a single message
        # ensures vision models see both together (separate messages cause
        # the model to ignore images in complex ReAct pipelines).
        image_files = [f for f in uploaded_files if _is_image(f)]
        if not image_files:
            return

        try:
            from agentscope.message import TextBlock, ImageBlock
        except ImportError:
            logger.warning("ImageBlock not available in this agentscope version; skipping image injection")
            return

        image_blocks: list = []
        image_names: list[str] = []
        for f in image_files:
            result = _fetch_image_base64(f)
            if result:
                b64_data, mime_type = result
                image_blocks.append(ImageBlock(
                    type="image",
                    source={
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64_data,
                    },
                ))
                image_names.append(f.get("name", "图片"))
            else:
                logger.warning(f"Image hook: could not load image {f.get('name')} ({f.get('file_id')}), skipping")

        if not image_blocks:
            logger.warning(f"File context hook: {len(image_files)} image(s) found but none could be loaded")
            return

        logger.info(f"File context hook: merging {len(image_names)} image(s) into user message: {image_names}")

        names_str = "、".join(image_names)
        prefix_block = TextBlock(
            type="text",
            text=f"[用户上传了 {len(image_blocks)} 张图片：{names_str}]",
        )

        # Merge images into the last user message so vision models see
        # both image and question together in one message.
        mem = await agent.memory.get_memory()
        last_user_msg = None
        for i in range(len(mem) - 1, -1, -1):
            if getattr(mem[i], "role", None) == "user":
                last_user_msg = mem[i]
                break

        if last_user_msg is not None:
            original_content = last_user_msg.content
            merged_blocks: list = [prefix_block, *image_blocks]
            if isinstance(original_content, str):
                merged_blocks.append(TextBlock(type="text", text=original_content))
            elif isinstance(original_content, list):
                merged_blocks.extend(original_content)
            last_user_msg.content = merged_blocks
        else:
            img_msg = Msg(name="user", content=[prefix_block, *image_blocks], role="user")
            await agent.memory.add(img_msg)

    return file_context_pre_reply
