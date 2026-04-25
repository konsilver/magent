"""Pre-built Agent Pool for zero-latency request handling.

At startup (after MCPConnectionPool is initialized), AgentPool builds N
ReActAgent instances with the full Toolkit already registered and the static
system prompt already compiled.  Each request acquires one agent, resets its
per-session state, and releases it when done.

Per-request state that changes every turn:
  - agent.memory        — replaced with a fresh InMemoryMemory()
  - agent._jx_context   — updated with user_id / chat_id / files
  - agent.sys_prompt    — {now} placeholder substituted with current time

Everything else (Toolkit, model, compression_config, hooks) is stable across
requests and shared without copying.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Lock
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_POOL_SIZE_DEFAULT = 3


class _PooledAgent:
    """A single slot in the pool."""

    def __init__(self, agent, base_system_prompt: str) -> None:
        self.agent = agent
        self._base_system_prompt = base_system_prompt  # contains {now} placeholder
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Reset per-session state so this agent is clean for the next request."""
        from agentscope.memory import InMemoryMemory
        from core.llm.hooks import ModelContext
        self.agent.memory = InMemoryMemory()
        self.agent._jx_context = ModelContext()  # type: ignore[attr-defined]
        now_str = datetime.now().isoformat(timespec="seconds")
        self.agent.sys_prompt = self._base_system_prompt.replace("{now}", now_str)


class AgentPool:
    """Singleton pool of pre-built ReActAgents."""

    _instance: Optional[AgentPool] = None
    _instance_lock = Lock()

    def __init__(self) -> None:
        self._agents: list[_PooledAgent] = []
        self._ready = False
        self._init_lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> AgentPool:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def is_ready(self) -> bool:
        return self._ready and bool(self._agents)

    async def initialize(self, pool_size: int = _POOL_SIZE_DEFAULT) -> None:
        """Build pool agents at startup. Called after MCPConnectionPool.initialize()."""
        async with self._init_lock:
            if self._ready:
                return
            t0 = time.monotonic()
            logger.info("[agent_pool] Building %d agents…", pool_size)
            try:
                agents = await asyncio.gather(
                    *[self._build_one() for _ in range(pool_size)],
                    return_exceptions=True,
                )
                for result in agents:
                    if isinstance(result, Exception):
                        logger.warning("[agent_pool] Failed to build one agent: %s", result)
                    else:
                        self._agents.append(result)

                self._ready = bool(self._agents)
                elapsed = (time.monotonic() - t0) * 1000
                logger.info(
                    "[agent_pool] Pool ready: %d/%d agents in %.0fms",
                    len(self._agents), pool_size, elapsed,
                )
            except Exception as exc:
                logger.warning("[agent_pool] Pool initialization failed: %s", exc)

    async def _build_one(self) -> _PooledAgent:
        """Build a single pooled agent from cached MCP tools + cached system prompt."""
        from agentscope.agent import ReActAgent
        from agentscope.formatter import OpenAIChatFormatter
        from agentscope.memory import InMemoryMemory
        from agentscope.token import CharTokenCounter

        from core.llm.mcp_pool import MCPConnectionPool
        from core.llm.chat_models import get_default_model
        from core.llm.hooks import ModelContext, make_dynamic_model_hook, make_file_context_hook
        from core.llm.tool import (
            register_sandboxed_view_text_file,
            register_run_skill_script,
            register_read_artifact,
        )
        from agent_skills.loader import get_skill_loader
        from agentscope.tool import Toolkit
        from prompts.prompt_config import load_prompt_config
        from prompts.prompt_runtime import build_system_prompt

        # ── Build Toolkit from cached MCP pool (zero RPC) ─────────────────
        pool = MCPConnectionPool.get_instance()
        all_keys = list(pool._stable_clients.keys()) + list(
            k for k, cfg in pool._stable_configs.items()
            if k not in pool._stable_clients
        )
        # Use all stable server keys from the pool
        stable_keys = list(pool._stable_server_ids)

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

        if pool.is_initialized and pool.has_cached_tools:
            _, _ = await pool.build_toolkit_from_cache(
                enabled_keys=stable_keys,
                per_request_servers_cfg={},
            )
            # Re-build toolkit properly with all stable cached tools
            for key in stable_keys:
                cached_funcs = pool._cached_tool_funcs.get(key, [])
                for func_obj in cached_funcs:
                    try:
                        toolkit.register_tool_function(func_obj, namesake_strategy="rename")
                    except Exception:
                        pass

        # ── Register skills ────────────────────────────────────────────────
        loader = get_skill_loader()
        skill_ids = sorted(loader.load_all_metadata().keys())
        allowed_skill_dirs: list[str] = []
        if skill_ids:
            loader.register_skills_to_toolkit(toolkit, skill_ids)
            for sid in skill_ids:
                d = loader.get_skill_dir(sid)
                if d:
                    allowed_skill_dirs.append(d)

        loaded_skill_ids: set[str] = set()
        register_sandboxed_view_text_file(toolkit, allowed_skill_dirs, loader, loaded_skill_ids=loaded_skill_ids)
        register_run_skill_script(toolkit, skill_ids, loader, loaded_skill_ids=loaded_skill_ids)
        register_read_artifact(toolkit)

        # ── Build system prompt ────────────────────────────────────────────
        cfg = load_prompt_config()
        tool_schemas = toolkit.get_json_schemas()
        # Use a placeholder for {now}; will be substituted per-request in reset()
        base_prompt = build_system_prompt(cfg, ctx={"tools": tool_schemas, "mcp_servers": stable_keys})

        # ── Model ──────────────────────────────────────────────────────────
        model = get_default_model(cfg.model, stream=True)

        # ── CompressionConfig ──────────────────────────────────────────────
        from core.llm.context_manager import resolve_model_context_window
        try:
            from core.config.model_config import ModelConfigService
            _cfg = ModelConfigService.get_instance().resolve("main_agent")
            _model_name = _cfg.model_name if _cfg else ""
        except Exception:
            _model_name = ""
        ctx_window = resolve_model_context_window(_model_name)
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

        # ── Create ReActAgent ──────────────────────────────────────────────
        now_str = datetime.now().isoformat(timespec="seconds")
        agent = ReActAgent(
            name="jingxin_agent",
            sys_prompt=base_prompt.replace("{now}", now_str),
            model=model,
            formatter=OpenAIChatFormatter(),
            toolkit=toolkit,
            memory=InMemoryMemory(),
            compression_config=compression_config,
            max_iters=50,
            parallel_tool_calls=True,
        )
        agent._disable_console_output = True  # type: ignore[attr-defined]
        agent._jx_context = ModelContext()     # type: ignore[attr-defined]

        dynamic_hook = make_dynamic_model_hook()
        agent._instance_pre_reply_hooks["dynamic_model"] = dynamic_hook
        file_hook = make_file_context_hook()
        agent._instance_pre_reply_hooks["file_context"] = file_hook

        return _PooledAgent(agent, base_prompt)

    async def _acquire_direct(self) -> _PooledAgent:
        """Acquire a pool slot directly (caller must call slot._lock.release() when done).

        Tries each slot non-blockingly; if all are busy, polls with a short
        sleep until one is free or the timeout is reached.
        asyncio.Lock has no acquire_nowait() — we use locked() as a fast-path
        guard and then attempt a zero-timeout acquire via wait_for.
        """
        deadline = asyncio.get_event_loop().time() + 10.0

        while True:
            for slot in self._agents:
                if not slot._lock.locked():
                    try:
                        await asyncio.wait_for(slot._lock.acquire(), timeout=0)
                        logger.debug("[agent_pool] acquired slot (direct)")
                        return slot
                    except asyncio.TimeoutError:
                        pass  # another coroutine beat us to it

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("[agent_pool] Timed out waiting for a free agent slot")
            await asyncio.sleep(min(0.05, remaining))

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_PooledAgent]:
        """Acquire a free agent from the pool, yield it, then release.

        On timeout falls back to a fresh create_agent_executor() agent.
        """
        slot: Optional[_PooledAgent] = None
        fallback_clients: list = []
        try:
            try:
                slot = await self._acquire_direct()
                slot.reset()
                logger.debug("[agent_pool] acquired agent")
                yield slot
            except TimeoutError:
                logger.warning("[agent_pool] acquire timeout, falling back to create_agent_executor")
                slot = None
                from core.llm.agent_factory import create_agent_executor
                agent, fallback_clients = await create_agent_executor()
                yield _PooledAgent(agent, agent.sys_prompt)
        finally:
            if slot is not None:
                try:
                    slot._lock.release()
                except Exception:
                    pass
            if fallback_clients:
                from core.llm.mcp_manager import close_clients
                await close_clients(fallback_clients)

    async def shutdown(self) -> None:
        """Discard all pooled agents."""
        self._agents.clear()
        self._ready = False
        logger.info("[agent_pool] Shut down")
