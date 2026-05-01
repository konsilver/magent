"""Minimal multi-agent workflow orchestration (AgentScope backend)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import anyio

from core.llm.agent_factory import create_agent_executor
from core.llm.context_manager import ContextWindowManager
from core.llm.message_compat import load_session_into_memory, extract_text_from_chat_response, strip_thinking
from core.llm.mcp_manager import close_clients
from core.config.catalog_resolver import (
    enabled_skill_ids_from_context,
    enabled_agent_ids_from_context,
    enabled_mcp_ids_from_context,
    enabled_kb_ids_from_context,
)
from routing.streaming import StreamingAgent
from routing.citations import extract_citations
from configs.display_names import TOOL_DISPLAY_NAMES


# Tools whose "running" card should appear immediately even when the first
# streaming chunk still has empty args. Needed for tools whose args take the
# LLM a noticeable amount of time to finish writing (e.g. long JSON params),
# otherwise the UI shows only the generic typing placeholder until the tool
# result finally arrives.
_FAST_EMIT_TOOLS = frozenset({
    "execute_code",
    "run_command",
    "run_skill_script",
})


def _tool_args_ready(tool_name: str, tool_args: Any) -> bool:
    """Check if tool_call args are complete enough to emit to the frontend.

    For view_text_file we need file_path to decide if it's a skill load.
    For tools in _FAST_EMIT_TOOLS we emit immediately (even with empty args)
    so the UI shows the card right away, then update when args arrive.
    """
    if not tool_args:
        if tool_name in _FAST_EMIT_TOOLS:
            return True
        return False
    if tool_name == "view_text_file":
        return bool(isinstance(tool_args, dict) and tool_args.get("file_path"))
    return True

# Re-export public helpers for backward compatibility
from routing.message_parser import (                         # noqa: F401
    format_message_content as _format_message_content,
    looks_markdown as _looks_markdown,
    extract_text_from_stream_item as _extract_text_from_stream_item,
    extract_text_from_messages_chunk as _extract_text_from_messages_chunk,
    resolve_sources_conflict as _resolve_sources_conflict,
    normalize_source as _normalize_source,
    source_rank as _source_rank,
)
from routing.memory_integration import (                     # noqa: F401
    launch_memory_retrieval,
    inject_memories,
    save_memories_background,
)

logger = logging.getLogger(__name__)


def _build_skill_injection(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a skill-selection hint message if skill_id is present in context.

    Returns a dict {"role": "user", "content": "..."} or None.
    """
    skill_id = context.get("skill_id")
    if not skill_id:
        return None

    try:
        from agent_skills.loader import get_skill_loader
        loader = get_skill_loader()
        metadata = loader.load_all_metadata().get(skill_id)
        if not metadata:
            logger.warning("[skill_inject] skill_id=%s not found", skill_id)
            return None

        skill_dir = loader.get_skill_dir(skill_id)
        if not skill_dir:
            logger.warning("[skill_inject] skill_id=%s has no skill dir", skill_id)
            return None

        return {
            "role": "user",
            "content": (
                f"<skill_instructions skill=\"{metadata.name}\">\n"
                f"用户已显式调用「{metadata.name}」技能。\n"
                "你必须先调用以下工具加载技能文件，不能跳过这一步直接调用 run_skill_script 或其它工具：\n"
                f"view_text_file(file_path=\"{skill_dir}/SKILL.md\")\n\n"
                "读取 SKILL.md 后，再严格按照其中流程执行。\n"
                "</skill_instructions>"
            ),
        }
    except Exception as e:
        logger.error("[skill_inject] failed to load skill %s: %s", skill_id, e)
        return None


def _parse_agent_mentions(message: str, available_agents: list) -> list:
    """Parse @agent_name mentions from user message.

    Returns list of matched agent_ids.
    """
    import re
    mentioned = []
    for agent in available_agents:
        name = agent.get("name", "")
        if not name:
            continue
        pattern = f"@{re.escape(name)}"
        if pattern in message:
            mentioned.append(agent["agent_id"])
    return mentioned


# ------------------------------------------------------------------
# Data containers
# ------------------------------------------------------------------

@dataclass
class WorkflowResult:
    route: str = "main"
    response: str = ""
    is_markdown: bool = False
    sources: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# Synchronous workflow (non-streaming)
# ------------------------------------------------------------------

def run_chat_workflow(
    *,
    session_messages: List[Dict[str, Any]],
    user_message: str,
    context: Dict[str, Any],
) -> WorkflowResult:
    """Run route -> target execution."""

    # ── Inject explicit skill instructions ──
    skill_msg = _build_skill_injection(context)
    if skill_msg:
        session_messages.insert(-1, skill_msg)
        logger.info("[skill_inject] injected skill instructions for '%s'", context.get("skill_id"))

    warnings: List[str] = []
    enabled_skill_ids = enabled_skill_ids_from_context(context)
    enabled_kb_ids = enabled_kb_ids_from_context(context)
    enabled_mcp_ids = enabled_mcp_ids_from_context(context)
    _workflow_user_id = str(context.get("user_id", ""))
    _workflow_model_name = str(context.get("model_name", ""))
    _workflow_mem_enabled = bool(context.get("memory_enabled", False))
    _reranker_enabled = bool(context.get("reranker_enabled", False))

    async def _run():
        agent, mcp_clients = await create_agent_executor(
            agent_spec=None,
            enabled_skill_ids=enabled_skill_ids,
            enabled_mcp_ids=enabled_mcp_ids,
            enabled_kb_ids=enabled_kb_ids,
            current_user_id=_workflow_user_id,
            reranker_enabled=_reranker_enabled,
            model_name=_workflow_model_name,
            memory_enabled=_workflow_mem_enabled,
        )
        try:
            from agentscope.message import Msg
            from core.llm.hooks import ModelContext, _build_file_context

            # Set _jx_context so hooks (file context, etc.) can access it
            uploaded_files = list(context.get("uploaded_files", []))
            historical_files = list(context.get("historical_files", []))
            ctx = ModelContext(
                model_name=str(context.get("model_name", "")),
                user_id=str(context.get("user_id", "")),
                chat_id=str(context.get("chat_id", "")),
                enable_thinking=bool(context.get("enable_thinking", True)),
                uploaded_files=uploaded_files,
                historical_files=historical_files,
            )
            agent._jx_context = ctx  # type: ignore[attr-defined]

            # Load history EXCLUDING the last user message — agent.reply()
            # will add it to memory internally, avoiding duplicates.
            history = list(session_messages)
            if history and history[-1].get("role") in ("user", "human"):
                history.pop()

            # Context window management for non-streaming path
            _ctx_mgr = ContextWindowManager.for_model(_workflow_model_name)
            history = _ctx_mgr.trim_history(history)

            if history:
                await load_session_into_memory(history, agent.memory)

            # Inject uploaded file context into memory
            if uploaded_files:
                file_context = _build_file_context(uploaded_files)
                file_msg = Msg(name="user", content=file_context, role="user")
                await agent.memory.add(file_msg)

            user_msg = Msg(name="user", content=user_message, role="user")
            result = await agent.reply(user_msg)
            return strip_thinking(result.get_text_content() or "")
        finally:
            await close_clients(mcp_clients)

    try:
        import asyncio as _asyncio
        response = _asyncio.run(_run())
    except Exception as e:
        warnings.append(f"Agent execution error: {str(e)[:200]}")
        response = ""

    return WorkflowResult(
        route="main",
        response=response,
        is_markdown=_looks_markdown(response),
        sources=_resolve_sources_conflict([]),
        artifacts=[],
        warnings=warnings,
        meta={},
    )


# ------------------------------------------------------------------
# Sub-agent direct conversation
# ------------------------------------------------------------------

async def _astream_subagent_direct(
    *,
    agent_id: str,
    session_messages: List[Dict[str, Any]],
    user_message: str,
    context: Dict[str, Any],
) -> AsyncIterator[Dict[str, Any]]:
    """Stream a direct conversation with a user-created sub-agent.

    Loads the UserAgent config from DB and uses it to build the agent
    with custom system_prompt, MCP tools, skills, KB, and model params.
    Shares the same streaming/memory/citation infrastructure as the main route.
    """
    import time as _time

    _wf_start = _time.monotonic()

    # Load UserAgent ORM object
    from core.db.engine import SessionLocal
    from core.services.user_agent_service import UserAgentService

    with SessionLocal() as _db:
        svc = UserAgentService(_db)
        user_agent = svc.get_raw_by_id(
            agent_id,
            user_id=str(context.get("user_id", "")),
        )
        # Eagerly load fields before session closes
        _ = user_agent.mcp_server_ids, user_agent.skill_ids, user_agent.kb_ids
        _ = user_agent.system_prompt, user_agent.model_provider_id
        _ = user_agent.max_iters, user_agent.temperature, user_agent.max_tokens, user_agent.timeout

    # ── [mem0] memory retrieval ───
    _mem0_user_id = str(context.get("user_id", ""))
    _mem0_enabled = bool(context.get("memory_enabled", False))
    _mem0_write_enabled = bool(context.get("memory_write_enabled", False))
    logger.info("[subagent] user_id=%s, agent_id=%s, memory_enabled=%s", _mem0_user_id, agent_id, _mem0_enabled)

    _memory_task = await launch_memory_retrieval(
        _mem0_user_id, user_message, _mem0_enabled,
    )

    _native_ltm_active = False
    warnings: List[str] = []
    full_response = ""
    displayed_tools: set[str] = set()
    all_citations: List[Dict[str, Any]] = []
    citation_offsets: Dict[str, int] = {}

    try:
        yield {"type": "thinking", "message": "正在连接子智能体..."}

        _stream_user_id = str(context.get("user_id", ""))
        _stream_model_name = str(context.get("model_name", ""))
        _stream_reranker = bool(context.get("reranker_enabled", False))

        # Create agent with sub-agent config overrides
        agent, mcp_clients = await create_agent_executor(
            agent_spec=None,
            enabled_mcp_ids=None,   # overridden by user_agent inside factory
            enabled_skill_ids=None, # overridden by user_agent inside factory
            enabled_kb_ids=None,    # overridden by user_agent inside factory
            current_user_id=_stream_user_id,
            reranker_enabled=_stream_reranker,
            model_name=_stream_model_name,
            memory_enabled=_mem0_enabled,
            user_agent=user_agent,
        )

        _native_ltm_active = getattr(agent, 'long_term_memory', None) is not None
        logger.info("[subagent] agent created in %.0fms", (_time.monotonic() - _wf_start) * 1000)

        # ── [mem0] inject memories ───
        if not _native_ltm_active:
            session_messages = await inject_memories(_memory_task, session_messages)
        elif _memory_task is not None:
            _memory_task.cancel()
            logger.info("[subagent] native LTM active, skipping manual memory injection")

        # ── Context window management ─────────────────────────────
        _actual_model = getattr(agent.model, 'model_name', _stream_model_name)
        ctx_manager = ContextWindowManager.for_model(_actual_model)
        trimmed = ctx_manager.trim_history(session_messages)
        dropped_count = len(session_messages) - len(trimmed)
        if dropped_count > 0:
            try:
                from core.llm.history_summarizer import summarize_history
                dropped_messages = session_messages[:dropped_count]
                summary = await summarize_history(dropped_messages)
                if summary:
                    session_messages = [
                        {"role": "user", "content": f"<conversation_summary>\n{summary}\n</conversation_summary>\n（以上为早期对话的结构化摘要）"},
                        *trimmed,
                    ]
                else:
                    session_messages = trimmed
            except Exception as exc:
                logger.warning("[subagent] history summarize failed: %s", exc)
                session_messages = trimmed

        streaming_agent = StreamingAgent(agent, mcp_clients)
        skill_load_ids: set = set()

        try:
            async for event_type, payload in streaming_agent.stream(
                session_messages, context
            ):
                if event_type == "text_delta":
                    full_response += payload
                    yield {"type": "content", "event": "ai_message", "delta": payload}

                elif event_type == "tool_call":
                    tool_name = payload.get("name", "unknown")
                    tool_id = payload.get("id", "")
                    tool_args = payload.get("args", {})

                    _is_fast_emit = tool_name in _FAST_EMIT_TOOLS
                    if tool_id and tool_id in displayed_tools:
                        if _is_fast_emit and tool_args:
                            pass  # re-emit with updated args
                        else:
                            continue
                    if not _tool_args_ready(tool_name, tool_args):
                        continue
                    if tool_id:
                        displayed_tools.add(tool_id)

                    is_skill_load = (
                        tool_name == "view_text_file"
                        and isinstance(tool_args, dict)
                        and "SKILL.md" in str(tool_args.get("file_path", ""))
                    )
                    if is_skill_load and tool_id:
                        skill_load_ids.add(tool_id)
                    emit_name = "load_skill" if is_skill_load else tool_name
                    display_name = (
                        "加载技能"
                        if is_skill_load
                        else TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
                    )
                    safe_args = tool_args if isinstance(tool_args, dict) else {}

                    # Resolve sub-agent name for call_subagent tool card
                    _subagent_name = ""
                    if tool_name == "call_subagent" and _visible_subagents:
                        # Try from args first
                        _sa_id = tool_args.get("agent_id", "") if isinstance(tool_args, dict) else ""
                        if _sa_id:
                            for _sa in _visible_subagents:
                                if _sa.get("agent_id") == _sa_id:
                                    _subagent_name = _sa.get("name", "")
                                    break
                        # Fallback: if @mention was detected, use the first mentioned agent
                        if not _subagent_name and _mentioned_ids:
                            for _sa in _visible_subagents:
                                if _sa.get("agent_id") in _mentioned_ids:
                                    _subagent_name = _sa.get("name", "")
                                    break
                        # Last fallback: if only one agent visible
                        if not _subagent_name and len(_visible_subagents) == 1:
                            _subagent_name = _visible_subagents[0].get("name", "")

                    yield {
                        "type": "tool_call",
                        "tool_name": emit_name,
                        "tool_display_name": display_name,
                        "tool_args": safe_args,
                        "input": safe_args,
                        "tool_id": tool_id,
                        **({"subagent_name": _subagent_name} if _subagent_name else {}),
                    }

                elif event_type == "tool_result":
                    tool_name = payload.get("name", "unknown")
                    tool_id = payload.get("id", "")
                    is_skill_result = (
                        (tool_id and tool_id in skill_load_ids)
                        or (tool_name == "view_text_file"
                            and "SKILL.md" in str(payload.get("content", "")))
                    )
                    if is_skill_result:
                        tool_name = "load_skill"
                    tool_content = payload.get("content", "")

                    try:
                        tool_result_json = json.loads(tool_content) if tool_content else {}
                    except json.JSONDecodeError:
                        tool_result_json = {"result": tool_content}

                    extracted_query = ""
                    if isinstance(tool_result_json, dict) and "result" in tool_result_json:
                        result_data = tool_result_json["result"]
                        if isinstance(result_data, dict):
                            extracted_query = result_data.get("query", result_data.get("question", ""))

                    cit_items = extract_citations(tool_name, tool_id, tool_result_json)
                    offset = citation_offsets.get(tool_name, 0)
                    if offset > 0:
                        for cit in cit_items:
                            old_idx = int(cit.id.rsplit("-", 1)[-1])
                            cit.id = f"{tool_name}-{old_idx + offset}"
                    citation_offsets[tool_name] = offset + len(cit_items)
                    cit_dicts = [c.to_dict() for c in cit_items]
                    all_citations.extend(cit_dicts)

                    yield {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_args": {"query": extracted_query} if extracted_query else {},
                        "result": tool_result_json,
                        "tool_id": tool_id,
                        "citations": cit_dicts,
                    }

                elif event_type == "heartbeat":
                    yield {"type": "heartbeat"}

                elif event_type == "tool_pending":
                    yield {"type": "tool_pending", **(payload or {})}

                elif event_type == "error":
                    raise payload

        finally:
            await streaming_agent.shutdown()

    except Exception as e:
        import traceback
        logger.error("subagent_stream_error: %s\n%s", e, traceback.format_exc())
        warnings.append(f"Streaming error: {str(e)[:200]}")

        if displayed_tools and not full_response:
            fallback_msg = "抱歉，我在整理工具调用的结果时遇到了问题。以上是已获取的工具执行结果，请参考。"
            full_response = fallback_msg
            yield {"type": "content", "event": "ai_message", "delta": fallback_msg}
        elif not full_response:
            raise

    yield {
        "type": "meta",
        "route": f"subagent:{agent_id}",
        "is_markdown": _looks_markdown(full_response),
        "sources": _resolve_sources_conflict([]),
        "artifacts": [],
        "warnings": warnings,
        "citations": all_citations,
        "usage": streaming_agent.get_usage(),
    }

    # ── [mem0] background save ────────────────────────────────────
    if not _native_ltm_active:
        save_memories_background(_mem0_user_id, user_message, full_response, _mem0_write_enabled)


# ------------------------------------------------------------------
# Streaming workflow
# ------------------------------------------------------------------

async def astream_chat_workflow(
    *,
    session_messages: List[Dict[str, Any]],
    user_message: str,
    context: Dict[str, Any],
):
    """Stream route -> handoff -> target execution.

    Yields chunks in the format:
    - {"type": "content", "delta": "text chunk"}
    - {"type": "tool_call", ...}
    - {"type": "tool_result", ...}
    - {"type": "meta", "route": "...", "sources": [...], ...}
    """

    # ── 子智能体直接对话模式 ──
    _agent_id = context.get("agent_id")
    if _agent_id:
        async for chunk in _astream_subagent_direct(
            agent_id=_agent_id,
            session_messages=session_messages,
            user_message=user_message,
            context=context,
        ):
            yield chunk
        return

    # ── Inject explicit skill instructions ──
    skill_msg = _build_skill_injection(context)
    if skill_msg:
        session_messages.insert(-1, skill_msg)
        logger.info("[skill_inject] injected skill instructions for '%s'", context.get("skill_id"))

    # ── [mem0] memory retrieval ───
    _mem0_user_id = str(context.get("user_id", ""))
    _mem0_enabled = bool(context.get("memory_enabled", False))
    _mem0_write_enabled = bool(context.get("memory_write_enabled", False))
    logger.info("[mem0] user_id=%s, memory_enabled=%s, memory_write_enabled=%s", _mem0_user_id, _mem0_enabled, _mem0_write_enabled)

    _memory_task = await launch_memory_retrieval(
        _mem0_user_id, user_message, _mem0_enabled,
    )

    # ── Main-route streaming ──────────────────────────────────────
    _native_ltm_active = False
    warnings: List[str] = []
    full_response = ""
    displayed_tools: set[str] = set()
    all_citations: List[Dict[str, Any]] = []
    citation_offsets: Dict[str, int] = {}

    try:
        import time as _time
        _wf_start = _time.monotonic()

        yield {"type": "thinking", "message": "正在分析您的问题..."}

        _stream_user_id = str(context.get("user_id", ""))
        _stream_model_name = str(context.get("model_name", ""))
        _stream_reranker = bool(context.get("reranker_enabled", False))
        enabled_skill_ids = enabled_skill_ids_from_context(context)
        enabled_kb_ids = enabled_kb_ids_from_context(context)
        enabled_mcp_ids = enabled_mcp_ids_from_context(context)

        # Create agent — use pool if ready, fall back to create_agent_executor
        _code_exec = bool(context.get("code_exec", False))
        _plan_chat = bool(context.get("plan_chat", False))
        _pool_slot = None

        # 与 agent pool 并行提前启动 user profile 提取，节省串行等待时间
        from routing.subagents.plan_agents import extract_user_profile
        from routing.subagents.plan_store import _role_model as _get_role_model
        _up_model = _get_role_model("user_profile", _stream_model_name)
        _profile_task = asyncio.create_task(
            extract_user_profile(_stream_user_id, user_message, _up_model)
        )

        from core.llm.agent_pool import AgentPool as _AgentPool
        _agent_pool = _AgentPool.get_instance()
        _use_pool = _agent_pool.is_ready and not _code_exec
        if _use_pool:
            try:
                _pool_slot = await _agent_pool._acquire_direct()
                _pool_slot.reset()
                agent = _pool_slot.agent
                mcp_clients = []
                logger.info("[workflow] agent acquired from pool in %.0fms",
                            (_time.monotonic() - _wf_start) * 1000)
            except Exception as _pool_exc:
                logger.warning("[workflow] pool acquire failed (%s), falling back to create_agent_executor",
                               _pool_exc)
                _pool_slot = None
                _use_pool = False

        if not _use_pool:
            agent, mcp_clients = await create_agent_executor(
                agent_spec=None,
                enabled_skill_ids=enabled_skill_ids,
                enabled_mcp_ids=enabled_mcp_ids,
                enabled_kb_ids=enabled_kb_ids,
                current_user_id=_stream_user_id,
                reranker_enabled=_stream_reranker,
                model_name=_stream_model_name,
                memory_enabled=_mem0_enabled,
                code_exec_enabled=_code_exec,
                plan_mode=_plan_chat,
            )
            logger.info("[workflow] agent created (fallback) in %.0fms",
                        (_time.monotonic() - _wf_start) * 1000)

        # Check if native long-term memory is active (skip manual injection if so)
        _native_ltm_active = getattr(agent, 'long_term_memory', None) is not None

        # ── [mem0] inject memories into session ───────────────────
        # Skip manual injection if native long-term memory is active
        # (AgentScope will handle retrieval automatically via static_control)
        if not _native_ltm_active:
            session_messages = await inject_memories(_memory_task, session_messages)
        elif _memory_task is not None:
            # Cancel the manual retrieval task since native LTM handles it
            _memory_task.cancel()
            logger.info("[mem0] native LTM active, skipping manual memory injection")

        # ── Context window management ─────────────────────────────
        # Trim history before loading into agent memory to prevent overflow.
        # CompressionConfig handles in-session compression, but pre-loading
        # too many messages can still exceed the model's context window.
        # 使用 agent 实际模型名（DB 解析后的），而非前端别名
        _actual_model = getattr(agent.model, 'model_name', _stream_model_name)
        ctx_manager = ContextWindowManager.for_model(_actual_model)
        trimmed = ctx_manager.trim_history(session_messages)
        dropped_count = len(session_messages) - len(trimmed)
        if dropped_count > 0:
            # Try to summarize dropped messages instead of just discarding
            try:
                from core.llm.history_summarizer import summarize_history
                dropped_messages = session_messages[:dropped_count]
                summary = await summarize_history(dropped_messages)
                if summary:
                    session_messages = [
                        {"role": "user", "content": f"<conversation_summary>\n{summary}\n</conversation_summary>\n（以上为早期对话的结构化摘要）"},
                        *trimmed,
                    ]
                    logger.info(
                        "[workflow] 历史摘要: %d 条消息摘要为 %d 字符, 保留 %d 条最近消息",
                        dropped_count, len(summary), len(trimmed),
                    )
                else:
                    session_messages = trimmed
            except Exception as exc:
                logger.warning("[workflow] 历史摘要失败，降级为裁剪: %s", exc)
                session_messages = trimmed

        # ── User profile injection ────────────────────────────────
        # _profile_task runs in parallel with agent creation.
        # We only inject if the task finished already (non-blocking check).
        # This ensures user-profile never delays the start of streaming.
        _profile = None
        if _profile_task.done():
            try:
                _profile = _profile_task.result()
                logger.info("[workflow] user profile ready before stream (elapsed=%.0fms)",
                            (_time.monotonic() - _wf_start) * 1000)
            except Exception as _up_exc:
                logger.warning("[workflow] user profile extraction failed (non-critical): %s", _up_exc)

        if _profile and (_profile.get("urgent") or _profile.get("mem")):
            _urgent = _profile.get("urgent") or ""
            _mem = _profile.get("mem") or ""
            _profile_section = "\n\n## 用户特征\n"
            if _urgent:
                _profile_section += f"- 即时特征：{_urgent}\n"
            if _mem:
                _profile_section += f"- 历史特征：{_mem}\n"
            _new_sys = agent.sys_prompt + _profile_section
            try:
                agent.sys_prompt = _new_sys
            except AttributeError:
                object.__setattr__(agent, "_sys_prompt", _new_sys)
            logger.info("[workflow] user profile injected into sys_prompt (urgent=%s mem=%s)",
                        bool(_urgent), bool(_mem))

        streaming_agent = StreamingAgent(agent, mcp_clients)

        skill_load_ids: set = set()  # track tool_ids that are skill loads

        try:
            async for event_type, payload in streaming_agent.stream(
                session_messages, context
            ):
                if event_type == "text_delta":
                    full_response += payload
                    yield {"type": "content", "event": "ai_message", "delta": payload}

                elif event_type == "tool_call":
                    tool_name = payload.get("name", "unknown")
                    tool_id = payload.get("id", "")
                    tool_args = payload.get("args", {})

                    # In streaming mode, the first chunk for a tool_call may
                    # arrive with empty args.  For view_text_file we need args
                    # to decide if this is a skill load, so skip empty-arg
                    # duplicates until we get the complete args.
                    _is_fast_emit = tool_name in _FAST_EMIT_TOOLS
                    if tool_id and tool_id in displayed_tools:
                        # Fast-emit tools: re-emit when args arrive so the
                        # frontend can update input display immediately.
                        if _is_fast_emit and tool_args:
                            pass  # fall through to emit update
                        else:
                            continue
                    if not _tool_args_ready(tool_name, tool_args):
                        continue
                    if tool_id:
                        displayed_tools.add(tool_id)

                    # Detect skill loading: view_text_file reading a SKILL.md
                    is_skill_load = (
                        tool_name == "view_text_file"
                        and isinstance(tool_args, dict)
                        and "SKILL.md" in str(tool_args.get("file_path", ""))
                    )
                    if is_skill_load and tool_id:
                        skill_load_ids.add(tool_id)
                    emit_name = "load_skill" if is_skill_load else tool_name
                    display_name = (
                        "加载技能"
                        if is_skill_load
                        else TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
                    )
                    safe_args = tool_args if isinstance(tool_args, dict) else {}

                    # Resolve sub-agent name for call_subagent tool card
                    _tc_sa_name = ""
                    if tool_name == "call_subagent" and _visible_subagents:
                        _tc_sa_id = safe_args.get("agent_id", "") if safe_args else ""
                        if _tc_sa_id:
                            for _sa in _visible_subagents:
                                if _sa.get("agent_id") == _tc_sa_id:
                                    _tc_sa_name = _sa.get("name", "")
                                    break
                        if not _tc_sa_name and _mentioned_ids:
                            for _sa in _visible_subagents:
                                if _sa.get("agent_id") in _mentioned_ids:
                                    _tc_sa_name = _sa.get("name", "")
                                    break

                    yield {
                        "type": "tool_call",
                        "tool_name": emit_name,
                        "tool_display_name": display_name,
                        "tool_args": safe_args,
                        "input": safe_args,
                        "tool_id": tool_id,
                        **({"subagent_name": _tc_sa_name} if _tc_sa_name else {}),
                    }

                elif event_type == "tool_result":
                    tool_name = payload.get("name", "unknown")
                    tool_id = payload.get("id", "")
                    # Also override tool_name for skill load results
                    is_skill_result = (
                        (tool_id and tool_id in skill_load_ids)
                        or (tool_name == "view_text_file"
                            and "SKILL.md" in str(payload.get("content", "")))
                    )
                    if is_skill_result:
                        tool_name = "load_skill"
                    tool_content = payload.get("content", "")

                    # Parse tool result
                    try:
                        tool_result_json = json.loads(tool_content) if tool_content else {}
                    except json.JSONDecodeError:
                        tool_result_json = {"result": tool_content}

                    # Extract query if present
                    extracted_query = ""
                    if isinstance(tool_result_json, dict) and "result" in tool_result_json:
                        result_data = tool_result_json["result"]
                        if isinstance(result_data, dict):
                            extracted_query = result_data.get("query", result_data.get("question", ""))

                    # Citations
                    cit_items = extract_citations(tool_name, tool_id, tool_result_json)
                    offset = citation_offsets.get(tool_name, 0)
                    if offset > 0:
                        for cit in cit_items:
                            old_idx = int(cit.id.rsplit("-", 1)[-1])
                            cit.id = f"{tool_name}-{old_idx + offset}"
                    citation_offsets[tool_name] = offset + len(cit_items)
                    cit_dicts = [c.to_dict() for c in cit_items]
                    all_citations.extend(cit_dicts)

                    # Resolve sub-agent name from call_subagent result text
                    _tr_sa_name = ""
                    if tool_name == "call_subagent":
                        _res_str = str(tool_result_json.get("result", "")) if isinstance(tool_result_json, dict) else str(tool_result_json)
                        if "【" in _res_str and "】" in _res_str:
                            _tr_sa_name = _res_str.split("【", 1)[1].split("】", 1)[0]

                    yield {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_args": {"query": extracted_query} if extracted_query else {},
                        "result": tool_result_json,
                        "tool_id": tool_id,
                        "citations": cit_dicts,
                        **({"subagent_name": _tr_sa_name} if _tr_sa_name else {}),
                    }

                elif event_type == "heartbeat":
                    yield {"type": "heartbeat"}

                elif event_type == "tool_pending":
                    yield {"type": "tool_pending", **(payload or {})}

                elif event_type == "error":
                    raise payload

        finally:
            await streaming_agent.shutdown()
            if _pool_slot is not None:
                try:
                    _pool_slot._lock.release()
                except Exception:
                    pass
            # Cancel background profile task if it's still running
            if not _profile_task.done():
                _profile_task.cancel()

    except Exception as e:
        import traceback
        logger.error("stream_workflow_error: %s\n%s", e, traceback.format_exc())
        warnings.append(f"Streaming error: {str(e)[:200]}")

        if displayed_tools and not full_response:
            fallback_msg = "抱歉，我在整理工具调用的结果时遇到了问题。以上是已获取的工具执行结果，请参考。"
            full_response = fallback_msg
            yield {"type": "content", "event": "ai_message", "delta": fallback_msg}
        elif not full_response:
            raise

    yield {
        "type": "meta",
        "route": "main",
        "is_markdown": _looks_markdown(full_response),
        "sources": _resolve_sources_conflict([]),
        "artifacts": [],
        "warnings": warnings,
        "citations": all_citations,
        "usage": streaming_agent.get_usage(),
    }

    # ── [mem0] background save ────────────────────────────────────
    # Skip manual save if native long-term memory handled it
    if not _native_ltm_active:
        logger.info(
            "[mem0] save check: write_enabled=%s, full_response_len=%s, user_id=%s",
            _mem0_write_enabled, len(full_response) if full_response else 0, _mem0_user_id,
        )
        save_memories_background(_mem0_user_id, user_message, full_response, _mem0_write_enabled)
    else:
        logger.info("[mem0] native LTM active, skipping manual memory save")
