"""Admin MCP server management API routes.

Provides CRUD for MCP server configurations managed via the admin backend.
Servers are stored in PostgreSQL (admin_mcp_servers table).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.db.models import AdminMcpServer
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/admin/mcp-servers", tags=["Admin MCP Servers"])
logger = logging.getLogger(__name__)

_SECRET_KEY_PATTERNS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _refresh_caches():
    """Invalidate all relevant caches after MCP server mutation."""
    try:
        from core.config.mcp_service import McpServerConfigService
        McpServerConfigService.get_instance().invalidate_cache()
    except Exception:
        pass
    try:
        from configs.catalog_loader import invalidate_catalog_cache
        invalidate_catalog_cache()
    except Exception:
        pass
    try:
        from core.config.catalog_resolver import invalidate_capability_cache
        invalidate_capability_cache()
    except Exception:
        pass
    try:
        from prompts.prompt_runtime import invalidate_prompt_cache
        invalidate_prompt_cache()
    except Exception:
        pass


def _sync_catalog_from_db(db: Session):
    """Sync catalog.json mcp section from DB state."""
    try:
        from configs.catalog import get_catalog
        from configs.catalog_loader import _write_catalog
        rows = db.query(AdminMcpServer).order_by(AdminMcpServer.sort_order).all()
        cat = get_catalog(include_runtime_details=False)
        mcp_items = []
        for row in rows:
            mcp_items.append({
                "id": row.server_id,
                "kind": "mcp_server",
                "name": row.display_name,
                "description": row.description or "",
                "enabled": row.is_enabled,
                "version": "v1",
                "config": {"server": row.server_id},
            })
        cat["mcp"] = mcp_items
        _write_catalog(cat)
    except Exception as exc:
        logger.warning("Failed to sync catalog from DB: %s", exc)


def _mask_secrets(env_vars: dict) -> dict:
    """Mask values of env vars whose keys look like secrets."""
    if not env_vars:
        return {}
    masked = {}
    for k, v in env_vars.items():
        k_upper = k.upper()
        if any(pat in k_upper for pat in _SECRET_KEY_PATTERNS):
            masked[k] = "***"
        else:
            masked[k] = v
    return masked


def _serialize_row(row: AdminMcpServer) -> dict:
    """Convert DB row to API response dict."""
    return {
        "server_id": row.server_id,
        "display_name": row.display_name,
        "description": row.description or "",
        "transport": row.transport,
        "command": row.command,
        "args": row.args or [],
        "url": row.url,
        "env_vars": _mask_secrets(row.env_vars or {}),
        "env_inherit": row.env_inherit or [],
        "headers": row.headers or {},
        "is_stable": row.is_stable,
        "is_enabled": row.is_enabled,
        "sort_order": row.sort_order,
        "extra_config": row.extra_config or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "created_by": row.created_by,
    }


# ── Pydantic schemas ────────────────────────────────────────────────────────

class McpServerCreateRequest(BaseModel):
    server_id: str = Field(..., pattern=r"^[a-z0-9_-]{1,63}$")
    display_name: str
    description: str = ""
    transport: str = Field("stdio", pattern=r"^(stdio|streamable_http|sse)$")
    command: Optional[str] = None
    args: List[str] = []
    url: Optional[str] = None
    env_vars: Dict[str, str] = {}
    env_inherit: List[str] = []
    headers: Dict[str, str] = {}
    is_stable: bool = True
    sort_order: int = 0
    extra_config: Dict[str, Any] = {}


class McpServerUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    env_inherit: Optional[List[str]] = None
    headers: Optional[Dict[str, str]] = None
    is_stable: Optional[bool] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    extra_config: Optional[Dict[str, Any]] = None


class McpServerToggleRequest(BaseModel):
    is_enabled: bool


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_config)])
def list_mcp_servers(db: Session = Depends(get_db)):
    """List all MCP servers."""
    rows = db.query(AdminMcpServer).order_by(AdminMcpServer.sort_order).all()

    # Check pool connection status for stable servers
    pool_status: Dict[str, bool] = {}
    try:
        from core.llm.mcp_pool import MCPConnectionPool
        pool = MCPConnectionPool.get_instance()
        for name, client in pool._stable_clients.items():
            pool_status[name] = getattr(client, "is_connected", False)
    except Exception:
        pass

    # Quick TCP reachability check for non-stable HTTP/SSE servers
    http_status: Dict[str, bool] = {}
    for row in rows:
        if not row.is_stable and row.transport in ("streamable_http", "sse") and row.url:
            try:
                import socket
                from urllib.parse import urlparse
                parsed = urlparse(row.url)
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                sock = socket.create_connection((host, port), timeout=2)
                sock.close()
                http_status[row.server_id] = True
            except Exception:
                http_status[row.server_id] = False

    items = []
    for row in rows:
        item = _serialize_row(row)
        if row.server_id in pool_status:
            item["pool_connected"] = pool_status[row.server_id]
        elif row.server_id in http_status:
            item["pool_connected"] = http_status[row.server_id]
        else:
            item["pool_connected"] = None
        items.append(item)

    return success_response(data=items)


@router.get("/{server_id}", dependencies=[Depends(require_config)])
def get_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """Get single MCP server detail."""
    row = db.query(AdminMcpServer).filter_by(server_id=server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")
    return success_response(data=_serialize_row(row))


@router.post("", dependencies=[Depends(require_config)])
def create_mcp_server(req: McpServerCreateRequest, db: Session = Depends(get_db)):
    """Create a new MCP server."""
    existing = db.query(AdminMcpServer).filter_by(server_id=req.server_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"MCP server '{req.server_id}' already exists")

    # Validate transport-specific fields
    if req.transport == "stdio" and not req.command:
        raise HTTPException(status_code=422, detail="stdio transport requires 'command'")
    if req.transport in ("streamable_http", "sse") and not req.url:
        raise HTTPException(status_code=422, detail=f"{req.transport} transport requires 'url'")

    row = AdminMcpServer(
        server_id=req.server_id,
        display_name=req.display_name,
        description=req.description,
        transport=req.transport,
        command=req.command,
        args=req.args,
        url=req.url,
        env_vars=req.env_vars,
        env_inherit=req.env_inherit,
        headers=req.headers,
        is_stable=req.is_stable,
        is_enabled=True,
        sort_order=req.sort_order,
        extra_config=req.extra_config,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    _sync_catalog_from_db(db)
    _refresh_caches()

    return success_response(data=_serialize_row(row), message="Created")


@router.put("/{server_id}", dependencies=[Depends(require_config)])
def update_mcp_server(
    server_id: str,
    req: McpServerUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update an existing MCP server."""
    row = db.query(AdminMcpServer).filter_by(server_id=server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")

    update_fields = req.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)

    _sync_catalog_from_db(db)
    _refresh_caches()

    return success_response(data=_serialize_row(row), message="Updated")


@router.put("/{server_id}/toggle", dependencies=[Depends(require_config)])
def toggle_mcp_server(
    server_id: str,
    req: McpServerToggleRequest,
    db: Session = Depends(get_db),
):
    """Toggle MCP server enabled/disabled."""
    row = db.query(AdminMcpServer).filter_by(server_id=server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")

    row.is_enabled = req.is_enabled
    row.updated_at = datetime.utcnow()
    db.commit()

    _sync_catalog_from_db(db)
    _refresh_caches()

    return success_response(
        data={"server_id": server_id, "is_enabled": req.is_enabled},
        message="Toggled",
    )


@router.delete("/{server_id}", dependencies=[Depends(require_config)])
def delete_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """Delete an MCP server."""
    row = db.query(AdminMcpServer).filter_by(server_id=server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")

    db.delete(row)
    db.commit()

    _sync_catalog_from_db(db)
    _refresh_caches()

    return success_response(message="Deleted")


@router.post("/{server_id}/test", dependencies=[Depends(require_config)])
async def test_mcp_server(server_id: str, db: Session = Depends(get_db)):
    """Test connectivity to an MCP server (temporary connection, does not affect pool)."""
    import time as _time

    row = db.query(AdminMcpServer).filter_by(server_id=server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")

    from core.config.mcp_service import McpServerConfigService
    cfg = McpServerConfigService.get_instance()._row_to_config(row)

    start = _time.monotonic()
    try:
        if row.transport == "stdio":
            from agentscope.mcp import StdIOStatefulClient
            client = StdIOStatefulClient(
                name=row.server_id,
                command=cfg.get("command", "python"),
                args=cfg.get("args", []),
                env=cfg.get("env") or None,
            )
            await client.connect()
            discovered = await client.list_tools()
            tools = [t.name for t in discovered] if discovered else []
            tools_meta = [
                {"name": t.name, "description": getattr(t, "description", "") or ""}
                for t in (discovered or [])
            ]
            elapsed = (_time.monotonic() - start) * 1000
            # Terminate after test
            try:
                proc = getattr(client, "_process", None) or getattr(client, "process", None)
                if proc is not None and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass
        elif row.transport in ("streamable_http", "sse"):
            from agentscope.mcp import HttpStatefulClient
            client = HttpStatefulClient(
                name=row.server_id,
                transport=row.transport,
                url=cfg.get("url", ""),
                headers=cfg.get("headers"),
            )
            await client.connect()
            discovered = await client.list_tools()
            tools = [t.name for t in discovered] if discovered else []
            tools_meta = [
                {"name": t.name, "description": getattr(t, "description", "") or ""}
                for t in (discovered or [])
            ]
            elapsed = (_time.monotonic() - start) * 1000
        else:
            raise HTTPException(status_code=422, detail=f"Unknown transport: {row.transport}")

        # Persist discovered tools to DB for catalog detail display
        if tools_meta:
            row.tools_json = tools_meta
            db.commit()
            _sync_catalog_from_db(db)

        return success_response(data={
            "server_id": server_id,
            "status": "ok",
            "latency_ms": round(elapsed, 1),
            "tools_discovered": tools,
        })
    except HTTPException:
        raise
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        return success_response(
            data={
                "server_id": server_id,
                "status": "error",
                "latency_ms": round(elapsed, 1),
                "error": str(exc),
            },
            code=10001,
            message="Connection failed",
        )


@router.post("/reload-pool", dependencies=[Depends(require_config)])
async def reload_pool():
    """Force reinitialize the MCP connection pool with latest DB config."""
    import time as _time

    start = _time.monotonic()
    try:
        from core.config.mcp_service import McpServerConfigService
        from core.llm.mcp_pool import MCPConnectionPool

        svc = McpServerConfigService.get_instance()
        svc.invalidate_cache()
        servers = svc.get_all_servers(enabled_only=True)

        pool = MCPConnectionPool.get_instance()
        await pool.initialize(servers)

        elapsed = (_time.monotonic() - start) * 1000
        return success_response(data={
            "stable_connections": pool.stable_client_count,
            "latency_ms": round(elapsed, 1),
        }, message="Pool reloaded")
    except Exception as exc:
        elapsed = (_time.monotonic() - start) * 1000
        raise HTTPException(
            status_code=500,
            detail=f"Pool reload failed after {elapsed:.0f}ms: {exc}",
        )
