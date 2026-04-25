"""Unified capability resolution logic.

Centralises the merging of catalog.json defaults + per-user DB overrides
so that every consumer (chat endpoint, factory, workflow, subagents) uses
the same algorithm.
"""

from __future__ import annotations

import logging
from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from configs.catalog import get_catalog, get_enabled_ids, is_enabled

logger = logging.getLogger(__name__)

# ── Per-user capability cache ────────────────────────────────────────────
_CAPABILITY_CACHE_TTL = 30.0  # seconds
_capability_cache_lock = Lock()
# user_id -> (expires_at, (skills, agents, mcps))
_capability_cache: Dict[str, Tuple[float, Tuple[Optional[List[str]], Optional[List[str]], Optional[List[str]]]]] = {}


# ── Context helpers (extract typed lists from a runtime context dict) ────────

def _extract_ids_from_context(context: Dict[str, Any], key: str) -> Optional[List[str]]:
    """Extract a list of non-empty string IDs from *context[key]*.

    Returns ``None`` if the key is absent or not a list, allowing callers
    to distinguish "not provided" from "empty list".
    """
    raw = context.get(key)
    if not isinstance(raw, list):
        return None
    return [str(item).strip() for item in raw if str(item).strip()]


def enabled_skill_ids_from_context(context: Dict[str, Any]) -> Optional[List[str]]:
    return _extract_ids_from_context(context, "enabled_skills")


def enabled_agent_ids_from_context(context: Dict[str, Any]) -> Optional[List[str]]:
    return _extract_ids_from_context(context, "enabled_agents")


def enabled_mcp_ids_from_context(context: Dict[str, Any]) -> Optional[List[str]]:
    return _extract_ids_from_context(context, "enabled_mcps")


def enabled_kb_ids_from_context(context: Dict[str, Any]) -> Optional[List[str]]:
    return _extract_ids_from_context(context, "enabled_kbs")


def is_agent_route_enabled(route: str, context: Dict[str, Any]) -> bool:
    """Check whether a given agent/sub-agent route is enabled for this request."""
    ids = enabled_agent_ids_from_context(context)
    if isinstance(ids, list):
        return route in set(ids)
    return is_enabled("agents", route)


# ── Full resolution (catalog.json base + DB user overrides) ─────────────────

def _merge_kind(
    base_items: list,
    user_overrides: list,
) -> List[str]:
    """Merge base catalog items with user overrides, return sorted enabled IDs.

    Only IDs present in base_items are eligible; user_overrides can only
    flip the enabled flag for existing items, never resurrect deleted ones.

    **Admin lock rule**: if an item is disabled in the base catalog
    (catalog.json ``enabled=false``), user overrides CANNOT re-enable it.
    This ensures admin-disabled capabilities are truly unavailable.
    """
    # Build base map: id -> enabled
    base_map: Dict[str, bool] = {}
    for item in base_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "")).strip()
        if item_id:
            base_map[item_id] = bool(item.get("enabled", True))

    enabled_map = dict(base_map)
    for item in user_overrides:
        if isinstance(item, dict):
            item_id = str(item.get("id", "")).strip()
            # Only update existing IDs — do not re-add items deleted from catalog.
            # Admin-disabled items (base enabled=false) are locked — user cannot re-enable.
            if item_id and item_id in enabled_map and base_map.get(item_id, True):
                enabled_map[item_id] = bool(item.get("enabled", False))
    return sorted(k for k, v in enabled_map.items() if v)


def resolve_all_runtime_enabled(
    db: Session,
    user_id: str,
) -> Tuple[Optional[List[str]], Optional[List[str]], Optional[List[str]]]:
    """Resolve user-effective enabled skills, agents, and MCPs in one pass.

    Results are cached per user_id for 30 seconds to avoid repeated DB
    queries when the same user sends multiple messages in quick succession.

    Loads ``get_catalog()`` and ``CatalogService`` once, then merges
    base defaults with per-user overrides.

    Returns ``(enabled_skills, enabled_agents, enabled_mcps)``.
    On error, returns ``(None, None, None)`` so callers fall back to
    static catalog defaults.
    """
    # Check cache first
    now = monotonic()
    with _capability_cache_lock:
        cached = _capability_cache.get(user_id)
        if cached is not None:
            expires_at, result = cached
            if now < expires_at:
                return result
            else:
                _capability_cache.pop(user_id, None)

    try:
        # Lazy import to avoid circular dependency at module level
        from core.services import CatalogService

        base_catalog = get_catalog(include_runtime_details=False)
        svc = CatalogService(db)
        overrides = svc.get_user_overrides(user_id)

        skills = _merge_kind(
            base_catalog.get("skills") or [],
            overrides.get("skills", []),
        )
        agents = _merge_kind(
            base_catalog.get("agents") or [],
            overrides.get("agents", []),
        )
        mcps = _merge_kind(
            base_catalog.get("mcp") or [],
            overrides.get("mcps", []),
        )
        result = (skills, agents, mcps)

        # Store in cache
        with _capability_cache_lock:
            _capability_cache[user_id] = (now + _CAPABILITY_CACHE_TTL, result)

        return result
    except Exception as exc:
        logger.warning("resolve_all_runtime_enabled failed: %s (user=%s)", exc, user_id)
        return None, None, None


def invalidate_capability_cache(user_id: Optional[str] = None) -> None:
    """Invalidate capability cache. Pass user_id to clear a specific user, or None for all."""
    with _capability_cache_lock:
        if user_id is None:
            _capability_cache.clear()
        else:
            _capability_cache.pop(user_id, None)
