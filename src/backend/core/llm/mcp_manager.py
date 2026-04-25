"""MCP client pool manager for AgentScope.

Manages StdIOStatefulClient instances with TTL caching.
Replaces langchain-mcp-adapters' MultiServerMCPClient.
"""

from __future__ import annotations

import logging
from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

from agentscope.mcp import StdIOStatefulClient
from agentscope.tool import Toolkit

logger = logging.getLogger(__name__)


async def load_mcp_tools(
    mcp_servers: Dict[str, dict],
) -> Tuple[List[StdIOStatefulClient], Toolkit]:
    """Connect to MCP servers and load their tools into a Toolkit.

    Args:
        mcp_servers: Server configs, keyed by server name.
            Each value must have: command, args, env (matching configs/mcp_config.py format).

    Returns:
        Tuple of (list of connected clients, Toolkit with registered tools).
    """
    clients: List[StdIOStatefulClient] = []
    toolkit = Toolkit()

    for server_name, server_cfg in mcp_servers.items():
        command = server_cfg.get("command", "python")
        args = server_cfg.get("args", [])
        env = server_cfg.get("env") or None

        try:
            client = StdIOStatefulClient(
                name=server_name,
                command=command,
                args=args,
                env=env,
            )
            await client.connect()
            await toolkit.register_mcp_client(
                client,
                namesake_strategy="rename",
            )
            clients.append(client)
            logger.debug("MCP client '%s' connected and registered", server_name)
        except Exception as exc:
            logger.warning("Failed to connect MCP server '%s': %s", server_name, exc)
            # Ensure failed client is closed
            try:
                await client.close(ignore_errors=True)
            except Exception:
                pass

    return clients, toolkit


async def close_clients(clients: List[StdIOStatefulClient]) -> None:
    """Safely close a list of MCP clients."""
    for client in clients:
        try:
            await client.close(ignore_errors=True)
        except Exception as exc:
            logger.debug("Error closing MCP client: %s", exc)


# ── Tool schema cache (reuse across requests) ────────────────────────────

_CACHE_LOCK = Lock()
_SCHEMA_CACHE: Dict[tuple, Tuple[float, List[dict]]] = {}


def get_cached_schemas(
    cache_key: tuple,
    ttl_seconds: float,
) -> Optional[List[dict]]:
    """Return cached tool JSON schemas if fresh, else None."""
    if ttl_seconds <= 0:
        return None
    now = monotonic()
    with _CACHE_LOCK:
        entry = _SCHEMA_CACHE.get(cache_key)
        if entry is None:
            return None
        expires_at, schemas = entry
        if now >= expires_at:
            _SCHEMA_CACHE.pop(cache_key, None)
            return None
        return list(schemas)


def set_cached_schemas(
    cache_key: tuple,
    ttl_seconds: float,
    schemas: List[dict],
) -> None:
    """Store tool JSON schemas in cache."""
    if ttl_seconds <= 0:
        return
    with _CACHE_LOCK:
        _SCHEMA_CACHE[cache_key] = (monotonic() + ttl_seconds, list(schemas))


def clear_cache() -> None:
    """Clear the schema cache."""
    with _CACHE_LOCK:
        _SCHEMA_CACHE.clear()
