"""Catalog management API routes (v1)."""

import logging
from threading import Lock
from time import monotonic
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from core.db.engine import get_db
from core.auth.backend import get_current_user, UserContext
from core.db.repository import KBRepository
from core.services import CatalogService, KBService
from core.infra.responses import success_response
from core.infra.exceptions import BadRequestError
from configs.catalog import get_catalog
from utils.dify_kb import is_dify_enabled, list_datasets

# ── Dify dataset list cache (avoids 3s timeout on every page load) ──
_dify_cache_lock = Lock()
_dify_cache: Optional[tuple] = None  # (expires_at, items)
_DIFY_CACHE_TTL = 30.0


def _list_datasets_cached() -> List[Dict[str, Any]]:
    """Return Dify datasets with 30s in-memory cache."""
    global _dify_cache
    now = monotonic()
    with _dify_cache_lock:
        if _dify_cache is not None:
            expires_at, items = _dify_cache
            if now < expires_at:
                return items

    items = list_datasets(page=1, limit=100)
    with _dify_cache_lock:
        _dify_cache = (now + _DIFY_CACHE_TTL, items)
    return items

router = APIRouter(prefix="/v1/catalog", tags=["Catalog"])
logger = logging.getLogger(__name__)


# Request/Response Models
class UpdateCatalogRequest(BaseModel):
    """Request model for updating catalog configuration."""
    enabled: Optional[bool] = Field(None, description="Enable/disable the item")
    config: Optional[Dict[str, Any]] = Field(None, description="Configuration overrides")


class CatalogItemResponse(BaseModel):
    """Response model for a catalog item."""
    id: str
    name: str
    description: str
    enabled: bool
    config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


@router.get("", summary="获取能力目录")
async def get_catalog_items(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the complete capability catalog.

    Returns all available capabilities including:
    - skills: Tool bundles and skill packages
    - agents: Subagents and specialized agents
    - mcp_servers: MCP (Model Context Protocol) servers

    The catalog includes both system defaults and user-specific overrides.
    For each item, the enabled status and configuration reflect the user's
    customizations if any, otherwise the default values.
    """
    # Get base catalog from configs
    base_catalog = get_catalog()

    # Get user overrides (graceful degradation if table doesn't exist yet)
    try:
        catalog_service = CatalogService(db)
        user_overrides = catalog_service.get_user_overrides(user.user_id)
    except Exception as exc:
        logger.warning("Failed to load user catalog overrides: %s", exc)
        user_overrides = {"skills": [], "agents": [], "mcps": []}

    # Merge base catalog with user overrides
    def merge_items(base_items: List[Dict], override_items: List[Dict]) -> List[Dict]:
        """Merge base catalog items with user overrides.

        Admin lock rule: if an item is disabled in the base catalog
        (catalog.json enabled=false), user overrides cannot re-enable it.
        """
        # Create a map of overrides by id
        override_map = {item["id"]: item for item in override_items}

        result = []
        for base_item in base_items:
            item_id = base_item.get("id")
            base_enabled = bool(base_item.get("enabled", True))

            # Admin-disabled items are completely hidden from user frontend
            if not base_enabled:
                continue

            # Start with base item
            merged = dict(base_item)

            # Apply user override if exists
            if item_id in override_map:
                override = override_map[item_id]
                merged["enabled"] = override["enabled"]
                if override.get("config"):
                    # Merge configs
                    base_config = merged.get("config", {})
                    override_config = override.get("config", {})
                    merged["config"] = {**base_config, **override_config}

            config = merged.get("config", {})
            if isinstance(config, dict):
                tags = config.get("tags")
                if isinstance(tags, list):
                    merged["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
                server = config.get("server")
                if isinstance(server, str) and server.strip():
                    merged["server"] = server.strip()

            result.append(merged)

        return result

    # Merge each category
    mcp_items = merge_items(
        base_catalog.get("mcp", []),
        user_overrides.get("mcps", [])
    )

    # ── Public KB (Dify) ──────────────────────────────────────────────────────
    public_kb_items: List[Dict[str, Any]] = []
    if is_dify_enabled():
        try:
            dify_items = _list_datasets_cached()
            for item in dify_items:
                item["visibility"] = "public"
                item["is_public"] = True
            public_kb_items = dify_items
        except Exception as exc:
            logger.warning("Failed to load Dify KB datasets: %s", exc)

    # ── Private KB (local Milvus) ─────────────────────────────────────────────
    try:
        kb_repo = KBRepository(db)
        spaces = kb_repo.list_spaces(user.user_id)
    except Exception as exc:
        logger.warning("Failed to load private KB spaces: %s", exc)
        spaces = []
    private_kb_items: List[Dict[str, Any]] = []
    for space in spaces:
        extra = space.extra_data if isinstance(space.extra_data, dict) else {}
        is_system_managed = bool(extra.get("system_managed"))
        if is_system_managed:
            continue
        tag = str(extra.get("tag") or "").strip()
        tags = [tag] if tag else []
        private_kb_items.append({
            "id": space.kb_id,
            "kind": "knowledge_base",
            "name": space.name,
            "description": space.description or "无简介",
            "desc": space.description or "无简介",
            "enabled": True,
            "version": "local",
            "provider": "Jingxin-KB",
            "visibility": "private",
            "is_public": False,
            "chunk_method": space.chunk_method or "semantic",
            "document_count": space.document_count or 0,
            "total_size_bytes": space.total_size_bytes or 0,
            "detail": (
                f"### {space.name}\n\n"
                f"{space.description or '暂无简介'}\n"
            ),
            "tags": tags,
            "system_managed": is_system_managed,
            "pinned": bool(extra.get("pinned")),
            "editable": not is_system_managed and extra.get("editable", True) is not False,
            "deletable": not is_system_managed and extra.get("deletable", True) is not False,
            "uploadable": not is_system_managed and extra.get("uploadable", True) is not False,
        })
    private_kb_items.sort(key=lambda item: (not bool(item.get("pinned")), item.get("name", "")))

    kb_items: List[Dict[str, Any]] = public_kb_items + private_kb_items

    data = {
        "skills": merge_items(
            base_catalog.get("skills", []),
            user_overrides.get("skills", [])
        ),
        "agents": merge_items(
            base_catalog.get("agents", []),
            user_overrides.get("agents", [])
        ),
        "mcp": mcp_items,
        "mcp_servers": mcp_items,
        "kb": kb_items,
    }

    return success_response(
        data=data,
        message="Catalog retrieved successfully"
    )


@router.patch("/{kind}/{id}", summary="更新能力配置")
async def update_catalog_item(
    kind: str = Path(..., description="Item kind: skill, agent, mcp, or kb"),
    id: str = Path(..., description="Item ID"),
    request: UpdateCatalogRequest = ...,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update catalog item configuration for the current user.

    Users can customize:
    - enabled: Whether the capability is enabled
    - config: Configuration overrides specific to this user

    These overrides are stored per-user and do not affect other users
    or the system-wide catalog defaults.

    Valid kinds:
    - skill: Tool bundles and skill packages
    - agent: Subagents and specialized agents
    - mcp: MCP servers
    - kb: Knowledge bases (runtime-only toggle)
    """
    # Normalize kind
    kind_map = {
        "skill": "skill",
        "skills": "skill",
        "agent": "agent",
        "agents": "agent",
        "mcp": "mcp",
        "mcp_server": "mcp",
        "mcp_servers": "mcp",
        "kb": "kb",
        "knowledge_base": "kb",
        "knowledge_bases": "kb",
    }

    normalized_kind = kind_map.get(kind.lower())
    if not normalized_kind:
        raise BadRequestError(
            message="Invalid kind",
            data={
                "allowed_kinds": ["skill", "agent", "mcp", "kb"],
                "provided_kind": kind
            }
        )

    # Validate that at least one field is provided
    if request.enabled is None and request.config is None:
        raise BadRequestError(
            message="At least one field must be provided",
            data={
                "allowed_fields": ["enabled", "config"]
            }
        )

    # KB toggles are not persisted in DB catalog_overrides (no kb enum in schema).
    # Frontend persists UI preference locally; backend accepts request for API uniformity.
    if normalized_kind == "kb":
        return success_response(
            data={
                "kind": normalized_kind,
                "id": id,
                "enabled": True if request.enabled is None else request.enabled,
                "config": request.config or {}
            },
            message="Knowledge base toggle accepted (runtime-only)"
        )

    # Get base catalog to verify item exists
    base_catalog = get_catalog()
    kind_bucket = "skills" if normalized_kind == "skill" else "agents" if normalized_kind == "agent" else "mcp"
    base_items = base_catalog.get(kind_bucket, [])

    item_exists = any(item.get("id") == id for item in base_items)
    if not item_exists:
        raise BadRequestError(
            message=f"Item not found in catalog",
            data={
                "kind": normalized_kind,
                "item_id": id,
                "hint": f"The item may not exist in the {kind_bucket} catalog"
            }
        )

    # Get current override or default values
    catalog_service = CatalogService(db)
    user_overrides = catalog_service.get_user_overrides(user.user_id, normalized_kind)

    # Find current item config
    current_enabled = True
    current_config = {}
    admin_disabled = False

    # Get from base catalog
    for item in base_items:
        if item.get("id") == id:
            current_enabled = item.get("enabled", True)
            current_config = item.get("config", {})
            admin_disabled = not bool(item.get("enabled", True))
            break

    # Admin lock: if disabled at the catalog level, user cannot re-enable
    if admin_disabled and request.enabled is True:
        raise BadRequestError(
            message="此功能已被管理员禁用，无法启用",
            data={"kind": normalized_kind, "item_id": id}
        )

    # Override with user settings if exists
    override_key = "skills" if normalized_kind == "skill" else "agents" if normalized_kind == "agent" else "mcps"
    for override_item in user_overrides.get(override_key, []):
        if override_item.get("id") == id:
            current_enabled = override_item.get("enabled", current_enabled)
            current_config = override_item.get("config", current_config)
            break

    # Apply updates
    new_enabled = request.enabled if request.enabled is not None else current_enabled
    new_config = current_config.copy()
    if request.config is not None:
        new_config.update(request.config)

    # Save override
    catalog_service.update_override(
        user_id=user.user_id,
        kind=normalized_kind,
        item_id=id,
        enabled=new_enabled,
        config=new_config if new_config else None
    )

    # Invalidate prompt cache: tool/skill changes affect the system prompt
    try:
        from prompts.prompt_runtime import invalidate_prompt_cache
        invalidate_prompt_cache()
    except Exception:
        pass

    return success_response(
        data={
            "kind": normalized_kind,
            "id": id,
            "enabled": new_enabled,
            "config": new_config
        },
        message="Catalog item updated successfully"
    )
