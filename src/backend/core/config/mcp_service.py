"""MCP server configuration service (DB-driven, cached).

Reads MCP server configs from the admin_mcp_servers table and provides
them in the same dict format that MCPConnectionPool and agent_factory
expect (compatible with the old MCP_SERVERS dict from mcp_config.py).

Thread-safe singleton with a 30s TTL cache.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set

from core.db.engine import SessionLocal
from core.db.models import AdminMcpServer

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0


class McpServerConfigService:
    """Reads MCP server configs from DB with in-memory caching."""

    _instance: Optional[McpServerConfigService] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: Optional[Dict[str, dict]] = None
        self._cache_all: Optional[Dict[str, dict]] = None  # includes disabled
        self._cache_ts: float = 0.0
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> McpServerConfigService:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_all_servers(self, enabled_only: bool = True) -> Dict[str, dict]:
        """Return {server_id: config_dict} from DB, cached for 30s.

        The config_dict format is compatible with the old MCP_SERVERS dict:
        {
            "transport": "stdio",
            "command": "python",
            "args": [...],
            "env": {...},       # merged: env_inherit from OS + env_vars
            "url": "...",       # for HTTP/SSE
            "headers": {...},
            "is_stable": True,
        }
        """
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < _CACHE_TTL:
            return dict(self._cache) if enabled_only else dict(self._cache_all or self._cache)

        with self._lock:
            # Double-check after acquiring lock
            if self._cache is not None and (time.monotonic() - self._cache_ts) < _CACHE_TTL:
                return dict(self._cache) if enabled_only else dict(self._cache_all or self._cache)

            return self._load_from_db(enabled_only)

    def _load_from_db(self, enabled_only: bool) -> Dict[str, dict]:
        """Load all servers from DB and rebuild cache."""
        enabled_map: Dict[str, dict] = {}
        all_map: Dict[str, dict] = {}

        try:
            with SessionLocal() as session:
                rows = session.query(AdminMcpServer).order_by(AdminMcpServer.sort_order).all()
                for row in rows:
                    cfg = self._row_to_config(row)
                    all_map[row.server_id] = cfg
                    if row.is_enabled:
                        enabled_map[row.server_id] = cfg
        except Exception as exc:
            logger.warning("[mcp_service] Failed to load from DB: %s", exc)
            # Return stale cache if available
            if self._cache is not None:
                return dict(self._cache) if enabled_only else dict(self._cache_all or self._cache)
            return {}

        self._cache = enabled_map
        self._cache_all = all_map
        self._cache_ts = time.monotonic()

        return dict(enabled_map) if enabled_only else dict(all_map)

    def _row_to_config(self, row: AdminMcpServer) -> dict:
        """Convert a DB row to the config dict format."""
        cfg: Dict[str, Any] = {
            "transport": row.transport,
            "is_stable": row.is_stable,
        }

        # stdio fields
        if row.transport == "stdio":
            cfg["command"] = row.command or "python"
            cfg["args"] = list(row.args or [])

        # HTTP/SSE fields
        if row.transport in ("streamable_http", "sse"):
            cfg["url"] = row.url or ""

        # Build env: inherit from OS + explicit env_vars
        env = self._build_env(row)
        if env:
            cfg["env"] = env

        # Headers
        if row.headers:
            cfg["headers"] = dict(row.headers)

        return cfg

    def _build_env(self, row: AdminMcpServer) -> Dict[str, str]:
        """Merge env_inherit (from OS) + env_vars (from DB)."""
        env: Dict[str, str] = {}

        # Phase 1: inherit from OS environment
        for key in (row.env_inherit or []):
            val = os.getenv(key)
            if val is not None:
                env[key] = val

        # Phase 2: overlay admin-set explicit values
        for key, val in (row.env_vars or {}).items():
            if isinstance(val, str):
                env[key] = val

        # Phase 3: apply DB-driven overlays (model config, system config)
        try:
            from core.config.model_config import ModelConfigService
            overlay = ModelConfigService.get_instance().get_mcp_env_overlay()
            if overlay:
                env.update(overlay)
        except Exception:
            pass

        try:
            from core.config.system_config import SystemConfigService
            svc_overlay = SystemConfigService.get_instance().get_service_env_overlay()
            if svc_overlay:
                env.update(svc_overlay)
        except Exception:
            pass

        return env

    def get_server(self, server_id: str) -> Optional[dict]:
        """Get a single server config by ID."""
        servers = self.get_all_servers(enabled_only=False)
        return servers.get(server_id)

    def get_stable_server_ids(self) -> Set[str]:
        """Return set of server_ids where is_stable=True."""
        servers = self.get_all_servers(enabled_only=True)
        return {k for k, v in servers.items() if v.get("is_stable")}

    def invalidate_cache(self) -> None:
        """Clear cache so next call re-reads from DB."""
        with self._lock:
            self._cache = None
            self._cache_all = None
            self._cache_ts = 0.0

    def get_all_rows(self) -> List[AdminMcpServer]:
        """Return all DB rows (for admin API). Not cached."""
        with SessionLocal() as session:
            return session.query(AdminMcpServer).order_by(
                AdminMcpServer.sort_order
            ).all()
