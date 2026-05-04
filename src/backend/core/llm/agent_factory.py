"""Agent factory - creates AgentScope agents with pluggable configuration.

This module is separated from core.chat.agent to avoid circular dependencies:
- routing modules can import from this factory
- this factory can import routing.registry without creating cycles
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.mcp import HttpStatefulClient, StdIOStatefulClient
from agentscope.memory import InMemoryMemory
from agentscope.token import CharTokenCounter
from agentscope.tool import Toolkit

from agent_skills.loader import get_skill_loader
from configs.catalog import get_enabled_ids
from core.config.mcp_service import McpServerConfigService
from core.llm.chat_models import get_default_model, get_summarize_model, make_chat_model
from core.llm.hooks import (
    ModelContext,
    make_dynamic_model_hook,
    make_file_context_hook,
)
from core.llm.tool import (
    register_execute_code_tools,
    register_read_artifact,
    register_run_skill_script,
    register_sandboxed_view_text_file,
)
from core.llm.mcp_manager import (
    load_mcp_tools,
    close_clients,
)
from core.llm.mcp_pool import MCPConnectionPool
from prompts.prompt_config import load_prompt_config
from prompts.prompt_runtime import build_system_prompt, build_subagent_system_prompt, select_tools
from routing.registry import AgentSpec

load_dotenv()

def _effective_mcp_server_keys(
    cfg,
    agent_spec: Optional[AgentSpec],
    enabled_mcp_ids: Optional[list[str]] = None,
    enabled_kb_ids: Optional[list[str]] = None,
) -> list[str]:
    all_servers = McpServerConfigService.get_instance().get_all_servers(enabled_only=True)
    all_keys = list(all_servers.keys())

    # enabled_mcp_ids=[] means explicitly disable all MCP (e.g. plan-mode simple subagents)
    # enabled_mcp_ids=None means no restriction — load all (main agent default)
    if enabled_mcp_ids is not None:
        if len(enabled_mcp_ids) == 0:
            return []
        return [k for k in all_keys if k in enabled_mcp_ids]

    return all_keys


def _filter_mcp_servers_by_keys(enabled_keys: list[str]) -> dict:
    enabled_set = set(enabled_keys)
    all_servers = McpServerConfigService.get_instance().get_all_servers(enabled_only=True)
    return {k: v for k, v in all_servers.items() if k in enabled_set}


from core.config.settings import settings as _settings

KB_MCP_HTTP_URL = _settings.server.kb_mcp_http_url


def _apply_runtime_kb_constraints(
    enabled_servers: dict,
    enabled_kb_ids: Optional[list[str]],
    current_user_id: Optional[str] = None,
    reranker_enabled: bool = False,
) -> dict:
    """Prepare KB server config with per-request HTTP headers.

    For streamable_http transport, runtime params are sent as HTTP headers
    instead of subprocess env vars. The server config gets a ``headers``
    dict that will be passed to HttpStatefulClient at connection time.

    When enabled_kb_ids is None (frontend didn't specify), we still set
    headers (with empty allowed lists) so the MCP impl can auto-resolve
    available KBs at tool-call time. This avoids adding latency here.
    """
    if "retrieve_dataset_content" not in enabled_servers:
        return enabled_servers

    if not isinstance(enabled_kb_ids, list):
        enabled_kb_ids = []

    normalized = [str(x).strip() for x in enabled_kb_ids if str(x).strip()]

    dify_ids = [x for x in normalized if not x.startswith("kb_")]
    local_ids = [x for x in normalized if x.startswith("kb_")]

    out = dict(enabled_servers)
    server_cfg = dict(out["retrieve_dataset_content"])

    # For HTTP transport: attach per-request headers
    if server_cfg.get("transport") == "streamable_http":
        server_cfg["headers"] = {
            "X-Allowed-Dataset-Ids": ",".join(dify_ids),
            "X-Allowed-Kb-Ids": ",".join(local_ids),
            "X-Current-User-Id": current_user_id or "",
            "X-Reranker-Enabled": "true" if reranker_enabled else "false",
        }
    else:
        # Legacy stdio path (backward compat)
        env_cfg = dict(server_cfg.get("env", {}) or {})
        env_cfg["DIFY_ALLOWED_DATASET_IDS"] = ",".join(dify_ids)
        env_cfg["LOCAL_KB_ALLOWED_IDS"] = ",".join(local_ids)
        env_cfg["CURRENT_USER_ID"] = current_user_id or ""
        env_cfg["RERANKER_ENABLED"] = "true" if reranker_enabled else "false"
        server_cfg["env"] = env_cfg

    out["retrieve_dataset_content"] = server_cfg
    return out


async def warmup_mcp_tools() -> None:
    """Initialize the MCP connection pool at startup.

    Reads MCP server configs from DB (via McpServerConfigService) and
    connects to all stable servers. Per-request servers (e.g.
    retrieve_dataset_content) are spawned on demand.
    """
    import logging
    import time

    log = logging.getLogger(__name__)

    # DB overlays (model config, system config) are already applied inside
    # McpServerConfigService._build_env(), so no manual overlay needed here.
    svc = McpServerConfigService.get_instance()
    servers = svc.get_all_servers(enabled_only=True)

    if not servers:
        log.info("[warmup] No MCP servers configured – skipping warmup")
        return

    log.info("[warmup] Initializing MCP connection pool for %d server(s)…", len(servers))
    start = time.monotonic()

    try:
        pool = MCPConnectionPool.get_instance()
        await pool.initialize(servers)
        elapsed = time.monotonic() - start
        log.info("[warmup] MCP pool initialized: %d stable connections in %.2fs",
                 pool.stable_client_count, elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.warning("[warmup] MCP pool initialization failed after %.2fs: %s", elapsed, exc)


def _effective_main_available_skills() -> list[str]:
    """Return all discovered skills — no per-user filtering."""
    try:
        loader = get_skill_loader()
        return sorted(loader.load_all_metadata().keys())
    except Exception:
        return []


async def _create_bare_llm_agent(
    model_name: Optional[str],
    current_user_id: Optional[str],
) -> Tuple[ReActAgent, list]:
    """Fast path for disable_tools=True isolated agents.

    Skips all skill/MCP/prompt-build overhead — just resolves the model
    and creates a minimal ReActAgent with an empty toolkit.
    Used by plan-mode LLM helpers (Warmup, QA, Summary, etc.).
    """
    import logging
    import time
    _log = logging.getLogger(__name__)
    _t0 = time.monotonic()

    from core.config.model_config import ModelConfigService
    from core.llm.chat_models import resolve_provider_by_model_name
    from core.llm.context_manager import resolve_model_context_window

    svc = ModelConfigService.get_instance()
    cfg = None
    if model_name:
        cfg = resolve_provider_by_model_name(model_name)
    if cfg is None:
        cfg = svc.resolve("main_agent")

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
        effective_model_name = cfg.model_name
    else:
        model = get_default_model(stream=True)
        effective_model_name = model_name or ""

    ctx_window = resolve_model_context_window(effective_model_name)
    compression_config = ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=CharTokenCounter(),
        trigger_threshold=int(ctx_window * 0.75),
        keep_recent=6,
        compression_prompt=(
            "<system-hint>你一直在处理上述任务但尚未完成。"
            "请生成一份续写摘要，使你能在新的上下文窗口中高效恢复工作。"
            "对话历史将被替换为此摘要。"
            "摘要应结构化、简洁且可操作，使用中文输出。"
            "</system-hint>"
        ),
    )

    agent = ReActAgent(
        name="llm_agent",
        sys_prompt="你是一个专注的助手，请严格按照用户要求完成任务。",
        model=model,
        formatter=OpenAIChatFormatter(),
        toolkit=Toolkit(),
        memory=InMemoryMemory(),
        compression_config=compression_config,
        max_iters=1,
        parallel_tool_calls=False,
    )
    agent._disable_console_output = True
    agent._jx_context = ModelContext()  # type: ignore[attr-defined]

    _log.info("[factory-bare] +%.0fms bare agent created (model=%s)", (time.monotonic() - _t0) * 1000, effective_model_name)
    return agent, []


async def create_agent_executor(
    agent_spec: Optional[AgentSpec] = None,
    user_query: Optional[str] = None,
    disable_tools: bool = False,
    enabled_skill_ids: Optional[list[str]] = None,
    enabled_mcp_ids: Optional[list[str]] = None,
    enabled_kb_ids: Optional[list[str]] = None,
    current_user_id: Optional[str] = None,
    reranker_enabled: bool = False,
    model_name: Optional[str] = None,
    memory_enabled: bool = False,
    user_agent: Optional[Any] = None,
    visible_subagents: Optional[List[Dict[str, Any]]] = None,
    mentioned_agent_ids: Optional[List[str]] = None,
    isolated: bool = False,
    max_iters: Optional[int] = None,
    code_exec_enabled: bool = False,
    plan_mode: bool = False,
) -> Tuple[ReActAgent, List[StdIOStatefulClient]]:
    """创建并返回一个 AgentScope ReActAgent 和其 MCP 客户端列表.

    Returns:
        Tuple of (agent, mcp_clients). Caller is responsible for closing
        mcp_clients after use via close_clients().
    """
    import logging
    import time
    _log = logging.getLogger(__name__)
    _t0 = time.monotonic()
    def _elapsed():
        return f"{(time.monotonic() - _t0)*1000:.0f}ms"

    import asyncio

    # Fast path: tool-disabled isolated agents (plan-mode LLM helpers) skip
    # all skill/MCP/prompt-build overhead and go straight to a bare agent.
    if disable_tools and isolated and user_agent is None and agent_spec is None:
        return await _create_bare_llm_agent(model_name, current_user_id)

    cfg = load_prompt_config()
    _log.info("[factory] +%s config loaded", _elapsed())
    if agent_spec is not None and agent_spec.prompt_parts:
        cfg = replace(
            cfg,
            system_prompt=replace(cfg.system_prompt, parts=list(agent_spec.prompt_parts)),
        )

    # ── Sub-agent overrides ──────────────────────────────────────────
    if user_agent is not None:
        # Override capability bindings from user_agent config
        enabled_mcp_ids = user_agent.mcp_server_ids or []
        enabled_skill_ids = user_agent.skill_ids or []
        enabled_kb_ids = user_agent.kb_ids or []

    # Determine which MCP servers to connect
    enabled_mcp_keys = _effective_mcp_server_keys(
        cfg,
        agent_spec,
        enabled_mcp_ids=enabled_mcp_ids,
        enabled_kb_ids=enabled_kb_ids,
    )
    enabled_servers = _filter_mcp_servers_by_keys(enabled_mcp_keys)
    enabled_servers = _apply_runtime_kb_constraints(
        enabled_servers, enabled_kb_ids, current_user_id, reranker_enabled=reranker_enabled
    )

    # ── Phase 1: Concurrent pre-loading ────────────────────────────────
    # DB overlays, skill metadata, and prompt DB parts are independent —
    # run them in parallel via thread pool to cut first-token latency.

    def _preload_skill_metadata():
        """Pre-warm skill metadata cache so registration is fast."""
        loader = get_skill_loader()
        loader.load_all_metadata()
        return loader

    # DB prompt parts are now pre-loaded at startup via warmup_prompt_cache(),
    # so no need to fetch them per-request.
    # DB-driven env overlays are already applied inside McpServerConfigService,
    # so no manual overlay step is needed here.

    # Fast path: if skill metadata cache is already warm, skip thread dispatch overhead (~7ms)
    _skill_loader_instance = get_skill_loader()
    if _skill_loader_instance._metadata_cache is not None:
        loader = _skill_loader_instance
    else:
        loader = await asyncio.to_thread(_preload_skill_metadata)
    _log.info("[factory] +%s skill metadata pre-loaded", _elapsed())

    # ── Phase 2: MCP toolkit (async, may spawn per-request subprocesses) ──
    mcp_clients: List[StdIOStatefulClient] = []
    http_clients: List[HttpStatefulClient] = []
    toolkit = Toolkit(
        agent_skill_instruction=(
            "# 技能（Agent Skills）\n"
            "匹配技能时，先用 view_text_file 读取 SKILL.md，再按指令执行。"
        ),
        agent_skill_template=(
            "## {name}\n"
            "{description}\n"
            "→ view_text_file(\"{dir}/SKILL.md\")"
        ),
    )

    if not disable_tools and enabled_servers:
        # Separate HTTP/SSE transport servers from stdio servers
        _HTTP_TRANSPORTS = {"streamable_http", "sse"}
        http_server_cfgs = {
            k: v for k, v in enabled_servers.items()
            if v.get("transport") in _HTTP_TRANSPORTS
        }
        stdio_servers = {
            k: v for k, v in enabled_servers.items()
            if v.get("transport") not in _HTTP_TRANSPORTS
        }

        if isolated:
            # Isolated mode (sub-agent in worker thread): skip shared pool
            # and HTTP clients to avoid anyio cancel-scope cross-task errors.
            all_stdio = {**stdio_servers, **http_server_cfgs}
            mcp_clients_loaded, toolkit = await load_mcp_tools(
                {k: v for k, v in all_stdio.items()
                 if v.get("transport") not in _HTTP_TRANSPORTS},
            )
            mcp_clients = mcp_clients_loaded
        else:
            pool = MCPConnectionPool.get_instance()
            stdio_enabled_keys = [k for k in enabled_mcp_keys if k not in http_server_cfgs]
            if pool.is_initialized:
                per_request_cfg = {
                    k: v for k, v in stdio_servers.items()
                    if k not in pool._stable_server_ids
                }
                if pool.has_cached_tools:
                    # Fast path: register cached tool funcs directly (zero RPC)
                    toolkit, mcp_clients = await pool.build_toolkit_from_cache(
                        enabled_keys=stdio_enabled_keys,
                        per_request_servers_cfg=per_request_cfg,
                    )
                else:
                    # Fallback: pool initialized but cache not ready
                    toolkit, mcp_clients = await pool.get_request_toolkit(
                        enabled_keys=stdio_enabled_keys,
                        per_request_servers_cfg=per_request_cfg,
                    )
            else:
                mcp_clients_loaded, toolkit = await load_mcp_tools(stdio_servers)
                mcp_clients = mcp_clients_loaded

            # Connect HTTP transport servers (fast — no subprocess spawn)
            import time as _time_mod
            _HTTP_MCP_CONNECT_TIMEOUT = 5.0  # seconds; fail fast if KB server is down
            for key, srv_cfg in http_server_cfgs.items():
                _http_start = _time_mod.monotonic()
                try:
                    client = HttpStatefulClient(
                        name=key,
                        transport=srv_cfg.get("transport", "streamable_http"),
                        url=srv_cfg.get("url", KB_MCP_HTTP_URL),
                        headers=srv_cfg.get("headers"),
                    )
                    await asyncio.wait_for(client.connect(), timeout=_HTTP_MCP_CONNECT_TIMEOUT)
                    await toolkit.register_mcp_client(client, namesake_strategy="rename")
                    http_clients.append(client)
                    _log.info("[factory] HTTP MCP '%s' connected in %.0fms",
                              key, (_time_mod.monotonic() - _http_start) * 1000)
                except asyncio.TimeoutError:
                    _log.warning("[factory] HTTP MCP '%s' connect timed out (%.0fs), skipping",
                                 key, _HTTP_MCP_CONNECT_TIMEOUT)
                except Exception as exc:
                    _log.warning("[factory] HTTP MCP '%s' connect failed: %s", key, exc)

        _log.info("[factory] +%s MCP tools loaded (transient_stdio=%d, http=%d)",
                  _elapsed(), len(mcp_clients), len(http_clients))

    # ── Phase 3: Skill registration (fast — metadata already cached) ──
    # enabled_skill_ids=None → load all (main agent default)
    # enabled_skill_ids=[]  → disable all (plan-mode subagents)
    skill_ids_to_register = enabled_skill_ids
    if skill_ids_to_register is None:
        skill_ids_to_register = _effective_main_available_skills()

    allowed_skill_dirs: list[str] = []
    if skill_ids_to_register:
        n = loader.register_skills_to_toolkit(toolkit, skill_ids_to_register)
        if n > 0:
            _log.info("Registered %d agent skills to toolkit", n)
        for sid in skill_ids_to_register:
            d = loader.get_skill_dir(sid)
            if d:
                allowed_skill_dirs.append(d)

    from agent_skills.config import get_enabled_skill_sources
    for src in get_enabled_skill_sources():
        root = str(src.root_dir)
        if os.path.isdir(root) and root not in allowed_skill_dirs:
            allowed_skill_dirs.append(root)
    _cache_root = os.path.join(os.path.expanduser("~"), ".cache", "jingxin-agent", "skills")
    if _cache_root not in allowed_skill_dirs:
        allowed_skill_dirs.append(_cache_root)

    _log.info("[factory] +%s skills registered", _elapsed())

    loaded_skill_ids: set[str] = set()
    register_sandboxed_view_text_file(
        toolkit,
        allowed_skill_dirs,
        loader,
        loaded_skill_ids=loaded_skill_ids,
    )

    # ── Phase 3.5: Register run_skill_script tool (if any skills have executable scripts) ──
    register_run_skill_script(
        toolkit,
        skill_ids_to_register or [],
        loader,
        user_id=current_user_id,
        loaded_skill_ids=loaded_skill_ids,
    )

    # ── Phase 3.6: Register execute_code tool (Lab code execution sessions only) ──
    if code_exec_enabled:
        register_execute_code_tools(toolkit, user_id=current_user_id)

    # ── Phase 3.7: Register read_artifact for cross-turn file access ──
    # Unconditional: any user may have uploaded files in prior turns of this chat,
    # and the hook injects historical-file summaries referencing this tool.
    register_read_artifact(toolkit, user_id=current_user_id)

    # ── Phase 4: Build system prompt (DB parts pre-fetched) ──
    _agent_ref: Optional[Dict] = None
    tool_schemas = toolkit.get_json_schemas()
    if user_agent is not None:
        system_prompt = build_subagent_system_prompt(
            user_agent,
            tool_schemas,
            enabled_mcp_keys,
            enabled_kb_ids=enabled_kb_ids,
        )
        _log.info("[factory] +%s subagent system prompt built (%d chars)", _elapsed(), len(system_prompt))
        if code_exec_enabled:
            _code_exec_dir = os.path.join(
                os.path.dirname(__file__), '..', '..', 'prompts', 'prompt_text', 'code_exec', 'system',
            )
            if os.path.isdir(_code_exec_dir):
                _prompt_files = sorted(
                    f for f in os.listdir(_code_exec_dir)
                    if f.endswith('.system.md')
                )
                for _pf in _prompt_files:
                    _pf_path = os.path.join(_code_exec_dir, _pf)
                    with open(_pf_path, 'r', encoding='utf-8') as _f:
                        system_prompt += "\n\n" + _f.read()
                _log.info("[factory] +%s subagent code execution prompts injected (%d files)",
                          _elapsed(), len(_prompt_files))
    elif plan_mode:
        # Plan-mode SubAgent: use a minimal system prompt instead of the full v5 prompt pack.
        # The v5 files describe a generic code-generation assistant with skills/MCP routing
        # that are all irrelevant or misleading in a structured step-execution context.
        from datetime import datetime as _dt
        _now = _dt.now().isoformat()
        _plan_sys_parts = [
            f"## 当前时间\n{_now}",
            (
                "## 输出格式\n"
                "- 代码必须放在带语言标识的 Markdown 代码块中（` ```python `、` ```bash ` 等）\n"
                "- 执行成功：输出 `执行成功（exit_code: 0）` 及关键 stdout\n"
                "- 执行失败：输出 `执行失败（exit_code: N）` 及完整 stderr\n"
                "- 语言：中文输出，技术术语保留英文原文"
            ),
        ]
        if enabled_mcp_keys:
            _plan_sys_parts.append(
                "## MCP 工具\n"
                "你被授权调用以下 MCP 工具，其他 Agent 无权调用：\n"
                + "\n".join(f"- {k}" for k in enabled_mcp_keys)
            )
        system_prompt = "\n\n".join(_plan_sys_parts)
        _log.info("[factory] +%s plan-mode subagent system prompt built (%d chars)", _elapsed(), len(system_prompt))
    else:
        system_prompt = build_system_prompt(
            cfg, ctx={
                "tools": tool_schemas,
                "mcp_servers": enabled_mcp_keys,
                "enabled_kbs": enabled_kb_ids,
            }
        )
        _log.info("[factory] +%s system prompt built (%d chars)", _elapsed(), len(system_prompt))

        # ── Inject code execution prompt (Lab sessions only) ──
        if code_exec_enabled:
            _code_exec_dir = os.path.join(
                os.path.dirname(__file__), '..', '..', 'prompts', 'prompt_text', 'code_exec', 'system',
            )
            if os.path.isdir(_code_exec_dir):
                _prompt_files = sorted(
                    f for f in os.listdir(_code_exec_dir)
                    if f.endswith('.system.md')
                )
                for _pf in _prompt_files:
                    _pf_path = os.path.join(_code_exec_dir, _pf)
                    with open(_pf_path, 'r', encoding='utf-8') as _f:
                        system_prompt += "\n\n" + _f.read()
                _log.info("[factory] +%s code execution prompts injected (%d files)",
                          _elapsed(), len(_prompt_files))

        # ── Register call_subagent tool for main agent ──
        if visible_subagents:
            from core.llm.subagent_tool import register_subagent_tool, build_subagent_prompt_section
            _agent_ref = {"agent": None}  # 创建后设置
            register_subagent_tool(
                toolkit, visible_subagents, current_user_id or "",
                agent_ref=_agent_ref,
            )
            subagent_section = build_subagent_prompt_section(visible_subagents, mentioned_agent_ids)
            if subagent_section:
                system_prompt = system_prompt + "\n\n" + subagent_section
            _log.info("[factory] +%s subagent tool registered (%d agents)", _elapsed(), len(visible_subagents))

    # Create model (streaming enabled for SSE)
    # Priority: explicit model_name > code_exec/plan_agent role > main_agent fallback
    # model_name is set by complexity-based selection (_subagent_model); it must win over
    # the plan_agent role default so simple steps use minimax and complex steps use glm-5.
    default_model = None
    _mode_role = "code_exec" if code_exec_enabled else ("plan_agent" if (plan_mode and not model_name) else None)
    if _mode_role:
        try:
            from core.config.model_config import ModelConfigService
            _mode_cfg = ModelConfigService.get_instance().resolve(_mode_role)
            if _mode_cfg:
                default_model = make_chat_model(
                    model=_mode_cfg.model_name,
                    temperature=_mode_cfg.temperature,
                    max_tokens=_mode_cfg.max_tokens,
                    timeout=_mode_cfg.timeout,
                    base_url=_mode_cfg.base_url,
                    api_key=_mode_cfg.api_key,
                    stream=True,
                )
                _log.info("[factory] using %s model: %s", _mode_role, _mode_cfg.model_name)
        except Exception as exc:
            _log.warning("[factory] %s model resolve failed: %s, falling back to main_agent", _mode_role, exc)
    if default_model is None and model_name:
        # Caller specified a concrete model name (e.g. from ROLE_SUBAGENT_MODEL env var)
        # Use cached lookup to avoid a DB query on every subagent creation.
        try:
            from core.llm.chat_models import resolve_provider_by_model_name
            _named_cfg = resolve_provider_by_model_name(model_name)
            if _named_cfg:
                default_model = make_chat_model(
                    model=_named_cfg.model_name,
                    temperature=_named_cfg.temperature,
                    max_tokens=_named_cfg.max_tokens,
                    timeout=_named_cfg.timeout,
                    base_url=_named_cfg.base_url,
                    api_key=_named_cfg.api_key,
                    stream=True,
                )
                _log.info("[factory] using model_name=%s resolved to %s", model_name, _named_cfg.model_name)
        except Exception as exc:
            _log.warning("[factory] model_name=%s resolve failed: %s", model_name, exc)
    if default_model is None:
        default_model = get_default_model(cfg.model, stream=True)

    # ── Sub-agent config override (model / temperature / max_tokens) ──
    # Triggers when user_agent specifies a custom model provider, a non-null
    # temperature, or a non-null max_tokens. Non-overridden fields fall back to
    # the main_agent model config so temperature-only overrides still work.
    if user_agent is not None:
        _user_temp = float(user_agent.temperature) if user_agent.temperature is not None else None
        _user_max_tokens = user_agent.max_tokens or None
        _user_timeout = user_agent.timeout or None
        _user_provider_id = user_agent.model_provider_id

        if _user_provider_id or _user_temp is not None or _user_max_tokens:
            try:
                from core.db.engine import SessionLocal
                from core.db.models import ModelProvider
                from core.config.model_config import ModelConfigService

                provider = None
                if _user_provider_id:
                    with SessionLocal() as _db:
                        provider = _db.query(ModelProvider).filter(
                            ModelProvider.provider_id == _user_provider_id,
                            ModelProvider.is_active == True,
                        ).first()

                # Fallback model config (main_agent) for params the user didn't override
                _fallback_cfg = ModelConfigService.get_instance().resolve("main_agent")

                _final_model = provider.model_name if provider else (_fallback_cfg.model_name if _fallback_cfg else None)
                _final_base_url = provider.base_url if provider else (_fallback_cfg.base_url if _fallback_cfg else None)
                _final_api_key = provider.api_key if provider else (_fallback_cfg.api_key if _fallback_cfg else None)
                _final_temp = _user_temp if _user_temp is not None else (
                    _fallback_cfg.temperature if _fallback_cfg else 0.6
                )
                _final_max_tokens = _user_max_tokens or (_fallback_cfg.max_tokens if _fallback_cfg else 8192)
                _final_timeout = _user_timeout or (_fallback_cfg.timeout if _fallback_cfg else 120)

                if _final_model and _final_base_url and _final_api_key:
                    default_model = make_chat_model(
                        model=_final_model,
                        temperature=_final_temp,
                        max_tokens=_final_max_tokens,
                        timeout=_final_timeout,
                        base_url=_final_base_url,
                        api_key=_final_api_key,
                        stream=True,
                    )
                    _log.info(
                        "[factory] subagent config override: model=%s, temp=%s, max_tokens=%s",
                        _final_model, _final_temp, _final_max_tokens,
                    )
                else:
                    _log.warning("[factory] subagent override skipped: missing model/base_url/api_key")
            except Exception as exc:
                _log.warning("[factory] subagent config override failed: %s, using default", exc)

    _log.info("[factory] +%s model created", _elapsed())

    # ── Resolve model context window for compression threshold ──
    # 优先使用 DB 中的实际模型名（如 deepseekr1, qwen3.5-122b），
    # 而不是前端传的别名（如 qwen, deepseek）
    from core.llm.context_manager import resolve_model_context_window

    _effective_model_name = ""
    try:
        from core.config.model_config import ModelConfigService
        _svc = ModelConfigService.get_instance()
        # Try mode-specific role first, then main_agent
        _resolve_role = _mode_role or "main_agent"
        _resolved_cfg = _svc.resolve(_resolve_role) or _svc.resolve("main_agent")
        if _resolved_cfg:
            _effective_model_name = _resolved_cfg.model_name
    except Exception:
        pass
    if not _effective_model_name:
        _effective_model_name = model_name or ""

    _ctx_window = resolve_model_context_window(_effective_model_name)
    # Trigger compression at 75% of context window
    _compression_threshold = int(_ctx_window * 0.75)
    _log.info(
        "[factory] CompressionConfig: model=%s, ctx_window=%d, threshold=%d",
        _effective_model_name or "(unknown)", _ctx_window, _compression_threshold,
    )

    compression_config = ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=CharTokenCounter(),
        trigger_threshold=_compression_threshold,
        keep_recent=6,  # 保留最近 3 轮 (user+assistant)
        compression_prompt=(
            "<system-hint>你一直在处理上述任务但尚未完成。"
            "请生成一份续写摘要，使你能在新的上下文窗口中高效恢复工作。"
            "对话历史将被替换为此摘要。"
            "摘要应结构化、简洁且可操作，使用中文输出。"
            "</system-hint>"
        ),
    )

    # ── Phase 5: Long-term memory (optional, AgentScope native) ──
    _long_term_memory = None
    _ltm_mode = "both"  # AgentScope 默认值，不能为 None
    if memory_enabled and current_user_id:
        try:
            from core.llm.memory import build_long_term_memory
            _long_term_memory = build_long_term_memory(
                user_id=current_user_id,
            )
            if _long_term_memory is not None:
                # static_control: 框架在每轮推理前自动检索，推理后自动保存
                _ltm_mode = "static_control"
                _log.info("[factory] +%s long-term memory enabled (mode=%s)", _elapsed(), _ltm_mode)
        except Exception as exc:
            _log.warning("[factory] long-term memory setup failed: %s", exc)

    # Skills are now registered via toolkit.register_agent_skill() above.
    # AgentScope's ReActAgent.sys_prompt automatically appends
    # toolkit.get_agent_skill_prompt(), so no separate hook is needed.

    # ── Resolve agent name and max_iters ──
    _DEFAULT_MAIN_ITERS = 50
    _DEFAULT_SUBAGENT_ITERS = 10
    _agent_name = "jingxin_agent"
    _max_iters = _DEFAULT_MAIN_ITERS
    if max_iters is not None:
        _max_iters = max_iters
    elif user_agent is not None:
        _agent_name = f"subagent_{user_agent.agent_id}" if isolated else f"agent_{user_agent.agent_id}"
        _max_iters = user_agent.max_iters or (
            _DEFAULT_SUBAGENT_ITERS if isolated else _DEFAULT_MAIN_ITERS
        )
    elif isolated:
        _max_iters = _DEFAULT_SUBAGENT_ITERS

    # Create the ReActAgent
    agent = ReActAgent(
        name=_agent_name,
        sys_prompt=system_prompt,
        model=default_model,
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
        compression_config=compression_config,
        long_term_memory=_long_term_memory,
        long_term_memory_mode=_ltm_mode,
        max_iters=_max_iters,
        parallel_tool_calls=True,
    )
    agent._disable_console_output = True

    # 设置 agent 引用，让 call_subagent 闭包能提取共享上下文
    if _agent_ref is not None:
        _agent_ref["agent"] = agent

    # Attach context placeholder
    agent._jx_context = ModelContext()  # type: ignore[attr-defined]

    # Register hooks (replacing middleware chain)
    # 1. Dynamic model switching
    dynamic_hook = make_dynamic_model_hook()
    agent._instance_pre_reply_hooks["dynamic_model"] = dynamic_hook

    # 2. File context injection
    file_hook = make_file_context_hook()
    agent._instance_pre_reply_hooks["file_context"] = file_hook

    _log.info("[factory] +%s agent created, TOTAL setup done", _elapsed())

    # Include HTTP clients in the transient list so callers close them
    all_transient = [*mcp_clients, *http_clients]
    return agent, all_transient


def create_agent_executor_sync(
    **kwargs,
) -> Tuple[ReActAgent, List[StdIOStatefulClient]]:
    """Synchronous wrapper around create_agent_executor."""
    import asyncio
    return asyncio.run(create_agent_executor(**kwargs))
