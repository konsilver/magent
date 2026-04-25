"""Tests for MCPConnectionPool tool function caching.

Run with:
    PYTHONPATH=src/backend python -m pytest src/backend/tests/test_mcp_pool_cache.py -v

Verifies:
  1. Tool functions are cached during initialize()
  2. build_toolkit_from_cache() registers tools without RPC
  3. Cache is cleared on shutdown / close_all_stable
  4. Cache is refreshed after _reconnect_stable()
  5. has_cached_tools property
  6. refresh_cache() for single server
  7. build_toolkit_from_cache() falls back on cache miss
  8. Per-request (non-stable) servers still spawn fresh
  9. reinitialize_if_config_changed() clears and rebuilds cache
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentscope.tool import Toolkit


# ── Fake MCP objects ──────────────────────────────────────────────────────

class FakeTool:
    """Mimics mcp.types.Tool."""
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class FakeMCPToolFunction:
    """Mimics agentscope.mcp.MCPToolFunction.

    Must satisfy both the MCPToolFunction isinstance check in Toolkit
    and the generic function path (__name__, __doc__).
    """
    def __init__(self, name: str, mcp_name: str):
        self.name = name
        self.mcp_name = mcp_name
        self.json_schema = {"name": name, "parameters": {"type": "object", "properties": {}}}
        # Needed for Toolkit.register_tool_function generic path
        self.__name__ = name
        self.__doc__ = f"Tool {name}"

    async def __call__(self, **kwargs):
        return {"result": f"{self.name} called"}


class FakeStdIOStatefulClient:
    """Mimics StdIOStatefulClient with controllable behavior."""

    def __init__(self, name: str, tools: Optional[List[str]] = None, **kwargs):
        self.name = name
        self._tools = [FakeTool(t) for t in (tools or ["tool_a", "tool_b"])]
        self.is_connected = True
        self.connect_count = 0
        self.list_tools_count = 0
        self.get_callable_count = 0

    async def connect(self):
        self.connect_count += 1
        self.is_connected = True

    async def list_tools(self) -> List[FakeTool]:
        self.list_tools_count += 1
        return list(self._tools)

    async def get_callable_function(self, func_name: str, wrap_tool_result: bool = True) -> FakeMCPToolFunction:
        self.get_callable_count += 1
        return FakeMCPToolFunction(name=func_name, mcp_name=self.name)


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_pool():
    """Create a fresh MCPConnectionPool (bypass singleton)."""
    from core.llm.mcp_pool import MCPConnectionPool
    pool = MCPConnectionPool.__new__(MCPConnectionPool)
    pool.__init__()
    return pool


def _server_configs(names: List[str], stable: bool = True) -> Dict[str, dict]:
    return {
        name: {
            "command": "python",
            "args": ["-m", f"mcp_servers.{name}"],
            "is_stable": stable,
        }
        for name in names
    }


# ── Tests ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pool():
    return _make_pool()


class TestCacheDuringInitialize:
    """Tool functions should be cached during initialize()."""

    @pytest.mark.asyncio
    async def test_tools_cached_after_initialize(self, pool):
        """After initialize(), _cached_tool_funcs should contain entries for each stable server."""
        clients = {}

        def fake_stdio_client(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["search", "fetch"])
            clients[name] = c
            return c

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["server_a", "server_b"]))

        assert pool.is_initialized
        assert pool.has_cached_tools
        assert "server_a" in pool._cached_tool_funcs
        assert "server_b" in pool._cached_tool_funcs
        assert len(pool._cached_tool_funcs["server_a"]) == 2
        assert len(pool._cached_tool_funcs["server_b"]) == 2

        # list_tools should have been called once per server (during caching)
        assert clients["server_a"].list_tools_count == 1
        assert clients["server_b"].list_tools_count == 1

    @pytest.mark.asyncio
    async def test_cache_contains_correct_func_names(self, pool):
        """Cached funcs should have the correct tool names."""
        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name, tools=["alpha", "beta", "gamma"])

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        func_names = [f.name for f in pool._cached_tool_funcs["srv"]]
        assert func_names == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_cache_failure_does_not_block_init(self, pool):
        """If caching fails for a server, init still succeeds and that server has no cache."""
        call_count = 0

        def fake_stdio_client(name, **kwargs):
            nonlocal call_count
            c = FakeStdIOStatefulClient(name=name)
            # Make list_tools fail for the second server
            if name == "bad_server":
                async def fail_list():
                    raise RuntimeError("tool listing failed")
                c.list_tools = fail_list
            return c

        cfgs = _server_configs(["good_server", "bad_server"])
        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(cfgs)

        assert pool.is_initialized
        assert "good_server" in pool._cached_tool_funcs
        assert "bad_server" not in pool._cached_tool_funcs


class TestBuildToolkitFromCache:
    """build_toolkit_from_cache() should register tools without RPC."""

    @pytest.mark.asyncio
    async def test_zero_rpc_for_cached_servers(self, pool):
        """Stable servers with cache should not call list_tools again."""
        clients = {}

        def fake_stdio_client(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["tool1"])
            clients[name] = c
            return c

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv_a", "srv_b"]))

        # Reset counts after init
        for c in clients.values():
            c.list_tools_count = 0
            c.get_callable_count = 0

        # Build toolkit from cache
        toolkit, transient = await pool.build_toolkit_from_cache(
            enabled_keys=["srv_a", "srv_b"],
        )

        # Zero RPC calls — tools came from cache
        assert clients["srv_a"].list_tools_count == 0
        assert clients["srv_b"].list_tools_count == 0
        assert clients["srv_a"].get_callable_count == 0
        assert clients["srv_b"].get_callable_count == 0

        # Toolkit should have tools registered
        schemas = toolkit.get_json_schemas()
        assert len(schemas) == 2  # 1 tool per server × 2 servers
        assert transient == []

    @pytest.mark.asyncio
    async def test_partial_enabled_keys(self, pool):
        """Only requested servers should be registered."""
        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name, tools=["tool1", "tool2"])

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv_a", "srv_b", "srv_c"]))

        toolkit, _ = await pool.build_toolkit_from_cache(
            enabled_keys=["srv_a"],  # only request srv_a
        )

        schemas = toolkit.get_json_schemas()
        assert len(schemas) == 2  # 2 tools from srv_a only

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_rpc(self, pool):
        """If cache is missing for a server, it should fall back to register_mcp_client."""
        clients = {}

        def fake_stdio_client(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["tool1"])
            clients[name] = c
            return c

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv_a"]))

        # Manually clear cache for srv_a to simulate cache miss
        pool._cached_tool_funcs.pop("srv_a")

        # Track whether register_mcp_client was called (the RPC fallback path)
        register_called = []

        async def tracking_register(*args, **kwargs):
            # args[0] is self (Toolkit), args[1] is the client
            client = args[1] if len(args) > 1 else kwargs.get("mcp_client")
            if client:
                register_called.append(client.name)

        with patch.object(Toolkit, "register_mcp_client", tracking_register):
            toolkit, transient = await pool.build_toolkit_from_cache(
                enabled_keys=["srv_a"],
            )

        # Should have called register_mcp_client (RPC fallback)
        assert "srv_a" in register_called

    @pytest.mark.asyncio
    async def test_non_stable_servers_spawn_fresh(self, pool):
        """Per-request (non-stable) servers should spawn new subprocesses."""
        spawned = []

        def fake_stdio_client(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["stable_tool"])
            return c

        # Initialize with one stable server
        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["stable_srv"]))

        # Now request with a non-stable server
        def fake_stdio_transient(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["transient_tool"])
            spawned.append(c)
            return c

        per_request_cfg = {
            "transient_srv": {
                "command": "python",
                "args": ["-m", "mcp_servers.transient"],
            }
        }

        # Patch register_mcp_client to avoid real MCP protocol on fake clients
        async def fake_register(*args, **kwargs):
            pass  # Just accept the client without real RPC

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_transient), \
             patch.object(Toolkit, "register_mcp_client", fake_register):
            toolkit, transient = await pool.build_toolkit_from_cache(
                enabled_keys=["stable_srv", "transient_srv"],
                per_request_servers_cfg=per_request_cfg,
            )

        assert len(transient) == 1
        assert spawned[0].connect_count == 1


class TestCacheLifecycle:
    """Cache should be cleared/refreshed at the right times."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_cache(self, pool):
        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name)

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        assert pool.has_cached_tools

        await pool.shutdown()

        assert not pool.has_cached_tools
        assert pool._cached_tool_funcs == {}

    @pytest.mark.asyncio
    async def test_reinitialize_rebuilds_cache(self, pool):
        """reinitialize_if_config_changed should rebuild cache when config changes."""
        call_log = []

        def fake_stdio_client(name, **kwargs):
            call_log.append(name)
            return FakeStdIOStatefulClient(name=name, tools=["t1"])

        cfgs_v1 = _server_configs(["srv_a"])
        cfgs_v2 = _server_configs(["srv_a", "srv_b"])

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(cfgs_v1)
            assert set(pool._cached_tool_funcs.keys()) == {"srv_a"}

            await pool.reinitialize_if_config_changed(cfgs_v2)
            assert set(pool._cached_tool_funcs.keys()) == {"srv_a", "srv_b"}

    @pytest.mark.asyncio
    async def test_reconnect_refreshes_cache(self, pool):
        """After _reconnect_stable, cache should be refreshed with new session's tools."""
        reconnect_tools = ["new_tool_after_reconnect"]

        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name, tools=["original_tool"])

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        old_funcs = pool._cached_tool_funcs["srv"]
        assert len(old_funcs) == 1
        assert old_funcs[0].name == "original_tool"

        # Reconnect returns client with different tools
        def fake_reconnect_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name, tools=reconnect_tools)

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_reconnect_client):
            new_client = await pool._reconnect_stable("srv")

        assert new_client is not None
        new_funcs = pool._cached_tool_funcs["srv"]
        assert len(new_funcs) == 1
        assert new_funcs[0].name == "new_tool_after_reconnect"

    @pytest.mark.asyncio
    async def test_refresh_cache_single_server(self, pool):
        """refresh_cache() should update cache for a single server."""
        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name, tools=["v1_tool"])

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        assert pool._cached_tool_funcs["srv"][0].name == "v1_tool"

        # Simulate tool list change by replacing the client's tools
        pool._stable_clients["srv"]._tools = [FakeTool("v2_tool")]

        await pool.refresh_cache("srv")

        assert pool._cached_tool_funcs["srv"][0].name == "v2_tool"


class TestHasCachedTools:
    """has_cached_tools property edge cases."""

    @pytest.mark.asyncio
    async def test_false_before_init(self, pool):
        assert not pool.has_cached_tools

    @pytest.mark.asyncio
    async def test_true_after_init_with_stable_servers(self, pool):
        def fake_stdio_client(name, **kwargs):
            return FakeStdIOStatefulClient(name=name)

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        assert pool.has_cached_tools

    @pytest.mark.asyncio
    async def test_false_when_no_stable_servers(self, pool):
        """If all servers are per-request, cache is empty."""
        await pool.initialize(_server_configs([], stable=True))
        assert not pool.has_cached_tools


class TestGetRequestToolkitFallback:
    """Original get_request_toolkit should still work (fallback path)."""

    @pytest.mark.asyncio
    async def test_get_request_toolkit_still_works(self, pool):
        clients = {}

        def fake_stdio_client(name, **kwargs):
            c = FakeStdIOStatefulClient(name=name, tools=["t1"])
            clients[name] = c
            return c

        with patch("core.llm.mcp_pool.StdIOStatefulClient", side_effect=fake_stdio_client):
            await pool.initialize(_server_configs(["srv"]))

        # Track whether register_mcp_client was called (the RPC path)
        register_called = []

        async def tracking_register(*args, **kwargs):
            client = args[1] if len(args) > 1 else kwargs.get("mcp_client")
            if client:
                register_called.append(client.name)

        # Use old method — should still do RPC (register_mcp_client)
        with patch.object(Toolkit, "register_mcp_client", tracking_register):
            toolkit, transient = await pool.get_request_toolkit(
                enabled_keys=["srv"],
                per_request_servers_cfg={},
            )

        # get_request_toolkit uses register_mcp_client (RPC path)
        assert "srv" in register_called
        assert transient == []
