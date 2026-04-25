"""Global MCP connection pool with tool function caching.

Keeps stable MCP servers connected across requests to eliminate the 1-7s
subprocess spawn overhead. Per-request servers (e.g. retrieve_dataset_content
with runtime KB env vars) are spawned fresh each time.

Tool functions from stable servers are cached at initialization time so that
per-request toolkit building requires zero MCP RPCs — just fast in-process
``register_tool_function()`` calls.

Usage:
    pool = MCPConnectionPool.get_instance()
    await pool.initialize(env_overlay)   # called once at startup

    # Per request (fast — uses cached tool funcs, no RPC):
    toolkit, transient = await pool.build_toolkit_from_cache(
        enabled_keys=[...],
        per_request_servers_cfg={...},
    )
    # ... use toolkit ...
    await pool.close_transient(transient)  # only closes per-request clients

    # Fallback if cache not ready:
    toolkit, transient = await pool.get_request_toolkit(enabled_keys, per_request_cfg)

    # At shutdown:
    await pool.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from threading import Lock
from typing import Any, Dict, List, Optional, Set, Tuple

from agentscope.mcp import StdIOStatefulClient
from agentscope.tool import Toolkit

logger = logging.getLogger(__name__)


class MCPConnectionPool:
    """Singleton MCP connection pool with tool function caching."""

    _instance: Optional[MCPConnectionPool] = None
    _instance_lock = Lock()

    def __init__(self) -> None:
        # key -> connected StdIOStatefulClient
        self._stable_clients: Dict[str, StdIOStatefulClient] = {}
        self._stable_configs: Dict[str, dict] = {}
        self._stable_server_ids: Set[str] = set()
        self._initialized = False
        self._config_version: int = 0
        self._lock = asyncio.Lock()
        # key -> list of cached MCPToolFunction objects (from get_callable_function)
        self._cached_tool_funcs: Dict[str, list] = {}

    @classmethod
    def get_instance(cls) -> MCPConnectionPool:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def initialize(self, server_configs: Optional[Dict[str, dict]] = None) -> None:
        """Connect to all stable MCP servers.

        Args:
            server_configs: Full server config dict (from McpServerConfigService
                or legacy MCP_SERVERS). Each value may contain an ``is_stable``
                flag; servers without the flag are treated as per-request.
                If None, loads from McpServerConfigService.
        """
        async with self._lock:
            if server_configs is None:
                from core.config.mcp_service import McpServerConfigService
                server_configs = McpServerConfigService.get_instance().get_all_servers()

            # Derive stable server set from config
            self._stable_server_ids = {
                name for name, cfg in server_configs.items()
                if cfg.get("is_stable", False)
            }

            # Close any existing connections first
            await self._close_all_stable()

            connected = 0
            for name, cfg in server_configs.items():
                if name not in self._stable_server_ids:
                    continue
                try:
                    client = StdIOStatefulClient(
                        name=name,
                        command=cfg.get("command", "python"),
                        args=cfg.get("args", []),
                        env=cfg.get("env") or None,
                    )
                    await client.connect()
                    self._stable_clients[name] = client
                    self._stable_configs[name] = cfg
                    connected += 1
                    logger.info("[mcp_pool] Connected stable server: %s", name)

                    # Pre-cache tool functions (eliminates per-request list_tools RPC)
                    await self._cache_tools_for_server(name, client)
                except Exception as exc:
                    logger.warning("[mcp_pool] Failed to connect stable server '%s': %s", name, exc)

            self._initialized = True
            self._config_version += 1
            logger.info("[mcp_pool] Initialized with %d/%d stable servers, %d cached tool funcs",
                        connected, len(self._stable_server_ids),
                        sum(len(v) for v in self._cached_tool_funcs.values()))

    async def reinitialize_if_config_changed(
        self,
        new_server_configs: Dict[str, dict],
    ) -> None:
        """Reinitialize stable connections if server configs have changed."""
        new_stable_ids = {
            name for name, cfg in new_server_configs.items()
            if cfg.get("is_stable", False)
        }
        changed = new_stable_ids != self._stable_server_ids
        if not changed:
            for name in new_stable_ids:
                if self._stable_configs.get(name) != new_server_configs.get(name):
                    changed = True
                    break

        if changed:
            logger.info("[mcp_pool] Config change detected, reinitializing stable connections")
            await self.initialize(new_server_configs)

    # ── Tool function caching ─────────────────────────────────────────────

    async def _cache_tools_for_server(
        self, name: str, client: StdIOStatefulClient,
    ) -> None:
        """Cache MCPToolFunction objects for a connected stable server."""
        import time as _time
        t0 = _time.monotonic()
        try:
            tools = await client.list_tools()
            funcs = []
            for tool in tools:
                func_obj = await client.get_callable_function(
                    func_name=tool.name,
                    wrap_tool_result=True,
                )
                funcs.append(func_obj)
            self._cached_tool_funcs[name] = funcs
            elapsed = (_time.monotonic() - t0) * 1000
            logger.info(
                "[mcp_pool] Cached %d tool functions for server '%s' in %.0fms",
                len(funcs), name, elapsed,
            )
        except Exception as exc:
            logger.warning("[mcp_pool] Failed to cache tools for '%s': %s", name, exc)
            self._cached_tool_funcs.pop(name, None)

    @property
    def has_cached_tools(self) -> bool:
        """True if at least one stable server has cached tool functions."""
        return bool(self._cached_tool_funcs)

    async def refresh_cache(self, server_name: str) -> None:
        """Re-cache tool functions for a single server (e.g. after tool list change)."""
        client = self._stable_clients.get(server_name)
        if client is None or not getattr(client, "is_connected", False):
            client = await self._reconnect_stable(server_name)
        if client is not None:
            await self._cache_tools_for_server(server_name, client)

    async def build_toolkit_from_cache(
        self,
        enabled_keys: List[str],
        per_request_servers_cfg: Optional[Dict[str, dict]] = None,
    ) -> Tuple[Toolkit, List[StdIOStatefulClient]]:
        """Build a per-request Toolkit using cached tool functions (zero RPC).

        - Stable servers: registers cached MCPToolFunction objects directly.
        - Per-request servers (stdio): spawns fresh subprocesses.

        Args:
            enabled_keys: MCP server keys needed for this request
                (should NOT include HTTP transport servers — those are handled
                separately in agent_factory).
            per_request_servers_cfg: Config for non-stable stdio servers.

        Returns:
            (toolkit, transient_clients) — caller must close transient_clients.
        """
        import time as _time

        toolkit = Toolkit()
        transient_clients: List[StdIOStatefulClient] = []

        # Phase 1: kick off per-request server spawns in background
        spawn_tasks: Dict[str, asyncio.Task] = {}
        for key in enabled_keys:
            if key not in self._stable_server_ids:
                cfg = (per_request_servers_cfg or {}).get(key)
                if cfg is not None:
                    spawn_tasks[key] = asyncio.create_task(
                        self._spawn_transient(key, cfg)
                    )

        # Phase 2: register stable servers from cache (fast — no RPC)
        for key in enabled_keys:
            if key not in self._stable_server_ids:
                continue
            _key_start = _time.monotonic()
            cached_funcs = self._cached_tool_funcs.get(key)
            if cached_funcs is None:
                # Cache miss — fall back to full registration via RPC
                client = self._stable_clients.get(key)
                if client is None or not getattr(client, "is_connected", False):
                    client = await self._reconnect_stable(key)
                if client is not None:
                    try:
                        await toolkit.register_mcp_client(
                            client, namesake_strategy="rename",
                        )
                    except Exception as exc:
                        logger.warning("[mcp_pool] Cache-miss register failed for '%s': %s", key, exc)
                logger.info("[mcp_pool] registered stable '%s' via RPC fallback in %.0fms",
                            key, (_time.monotonic() - _key_start) * 1000)
                continue

            # Fast path: register cached MCPToolFunction objects directly
            registered = 0
            for func_obj in cached_funcs:
                try:
                    toolkit.register_tool_function(
                        func_obj, namesake_strategy="rename",
                    )
                    registered += 1
                except Exception as exc:
                    logger.warning("[mcp_pool] Failed to register cached func '%s/%s': %s",
                                   key, getattr(func_obj, "name", "?"), exc)
            logger.info("[mcp_pool] registered stable '%s' from cache (%d funcs) in %.0fms",
                        key, registered, (_time.monotonic() - _key_start) * 1000)

        # Phase 3: await per-request servers and register them
        for key, task in spawn_tasks.items():
            _key_start = _time.monotonic()
            try:
                client = await task
                if client is not None:
                    await toolkit.register_mcp_client(
                        client, namesake_strategy="rename",
                    )
                    transient_clients.append(client)
                    logger.info("[mcp_pool] spawned per-request '%s' in %.0fms",
                                key, (_time.monotonic() - _key_start) * 1000)
            except Exception as exc:
                logger.warning("[mcp_pool] Failed per-request server '%s': %s (%.0fms)",
                               key, exc, (_time.monotonic() - _key_start) * 1000)

        return toolkit, transient_clients

    async def get_request_toolkit(
        self,
        enabled_keys: List[str],
        per_request_servers_cfg: Dict[str, dict],
    ) -> Tuple[Toolkit, List[StdIOStatefulClient]]:
        """Build a per-request Toolkit.

        - Stable servers: re-register cached clients (fast, no subprocess spawn)
        - Per-request servers: spawn concurrently in background while stable
          servers are being registered, then register once connected.

        Args:
            enabled_keys: List of MCP server keys needed for this request.
            per_request_servers_cfg: Full config for per-request servers
                (e.g. retrieve_dataset_content with runtime env).

        Returns:
            (toolkit, transient_clients) — caller must close transient_clients.
        """
        import asyncio
        import time as _time

        toolkit = Toolkit()
        transient_clients: List[StdIOStatefulClient] = []

        # Phase 1: kick off per-request server spawns in background FIRST
        # (they take seconds), then register stable servers (fast, ~15ms each).
        spawn_tasks: Dict[str, asyncio.Task] = {}
        for key in enabled_keys:
            if key not in self._stable_server_ids:
                cfg = per_request_servers_cfg.get(key)
                if cfg is not None:
                    spawn_tasks[key] = asyncio.create_task(
                        self._spawn_transient(key, cfg)
                    )

        # Phase 2: register stable servers (fast — clients already connected)
        for key in enabled_keys:
            if key not in self._stable_server_ids:
                continue
            _key_start = _time.monotonic()
            client = self._stable_clients.get(key)
            if client is None or not getattr(client, "is_connected", False):
                client = await self._reconnect_stable(key)
            if client is not None:
                try:
                    await toolkit.register_mcp_client(
                        client, namesake_strategy="rename",
                    )
                except Exception as exc:
                    logger.warning("[mcp_pool] Failed to register stable client '%s': %s", key, exc)
                    client = await self._reconnect_stable(key)
                    if client is not None:
                        try:
                            await toolkit.register_mcp_client(
                                client, namesake_strategy="rename",
                            )
                        except Exception as exc2:
                            logger.warning("[mcp_pool] Retry register failed for '%s': %s", key, exc2)
            logger.info("[mcp_pool] registered stable '%s' in %.0fms", key, (_time.monotonic() - _key_start) * 1000)

        # Phase 3: await per-request servers and register them
        for key, task in spawn_tasks.items():
            _key_start = _time.monotonic()
            try:
                client = await task
                if client is not None:
                    await toolkit.register_mcp_client(
                        client, namesake_strategy="rename",
                    )
                    transient_clients.append(client)
                    logger.info("[mcp_pool] spawned per-request '%s' in %.0fms", key, (_time.monotonic() - _key_start) * 1000)
            except Exception as exc:
                logger.warning("[mcp_pool] Failed per-request server '%s': %s (%.0fms)", key, exc, (_time.monotonic() - _key_start) * 1000)

        return toolkit, transient_clients

    async def _spawn_transient(self, key: str, cfg: dict) -> Optional[StdIOStatefulClient]:
        """Spawn a per-request MCP server subprocess."""
        client = StdIOStatefulClient(
            name=key,
            command=cfg.get("command", "python"),
            args=cfg.get("args", []),
            env=cfg.get("env") or None,
        )
        try:
            await client.connect()
            return client
        except Exception as exc:
            logger.warning("[mcp_pool] spawn_transient '%s' connect failed: %s", key, exc)
            try:
                proc = getattr(client, "_process", None) or getattr(client, "process", None)
                if proc is not None and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass
            raise

    async def _reconnect_stable(self, name: str) -> Optional[StdIOStatefulClient]:
        """Reconnect a stable server that has died."""
        cfg = self._stable_configs.get(name)
        if cfg is None:
            return None

        # Close old client if exists
        old = self._stable_clients.pop(name, None)
        if old is not None:
            try:
                proc = getattr(old, "_process", None) or getattr(old, "process", None)
                if proc is not None and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass

        try:
            client = StdIOStatefulClient(
                name=name,
                command=cfg.get("command", "python"),
                args=cfg.get("args", []),
                env=cfg.get("env") or None,
            )
            await client.connect()
            self._stable_clients[name] = client
            logger.info("[mcp_pool] Reconnected stable server: %s", name)
            # Refresh tool cache (session changed after reconnect)
            await self._cache_tools_for_server(name, client)
            return client
        except Exception as exc:
            logger.warning("[mcp_pool] Reconnection failed for '%s': %s", name, exc)
            self._cached_tool_funcs.pop(name, None)
            return None

    async def _close_all_stable(self) -> None:
        """Close all stable connections."""
        for name, client in list(self._stable_clients.items()):
            try:
                proc = getattr(client, "_process", None) or getattr(client, "process", None)
                if proc is not None and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass
        self._stable_clients.clear()
        self._stable_configs.clear()
        self._cached_tool_funcs.clear()

    async def shutdown(self) -> None:
        """Close all connections. Call at app shutdown."""
        async with self._lock:
            await self._close_all_stable()
            self._initialized = False
            logger.info("[mcp_pool] Shut down")

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def stable_client_count(self) -> int:
        return len(self._stable_clients)
