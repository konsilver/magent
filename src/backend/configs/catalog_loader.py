"""Catalog loading, caching, syncing, and persistence.

Handles reading catalog.json from disk, building the default catalog
from dynamic sources (skills, MCP servers, subagents), TTL-based
in-memory caching, and writing changes back to disk.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List

from configs.catalog_common import _CATALOG_PATH, _item, _read_raw_catalog

_LOGGER = logging.getLogger(__name__)

# ── In-memory catalog cache (TTL-based) ────────────────────────────────────
_CATALOG_CACHE: Dict[bool, Dict[str, Any]] = {}   # key = include_runtime_details
_CATALOG_CACHE_TIME: Dict[bool, float] = {}
_CATALOG_CACHE_TTL: float = 10.0  # seconds


def invalidate_catalog_cache() -> None:
    """Clear the in-memory catalog cache (call after writes)."""
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE_TIME.clear()


def _write_catalog(data: Dict[str, Any]) -> None:
    _CATALOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    invalidate_catalog_cache()


# ── Default catalog construction ───────────────────────────────────────────

def _default_catalog() -> Dict[str, Any]:
    # Import lazily to avoid any startup surprises.
    try:
        from configs.mcp_config import MCP_SERVERS
        mcp_servers = MCP_SERVERS
    except Exception as e:
        _LOGGER.warning(f"Failed to load MCP servers: {e}")
        mcp_servers = {}

    # Build MCP items from mcp_config.py with auto-extracted detail field
    try:
        from configs.mcp_config import MCP_SERVER_DISPLAY_NAMES as _MCP_ZH_NAMES
        from configs.mcp_config import MCP_SERVER_DESCRIPTIONS as _MCP_ZH_DESC
    except Exception:
        _MCP_ZH_NAMES = {}
        _MCP_ZH_DESC = {}

    mcp_items = [
        _item(
            item_id=k,
            kind="mcp_server",
            name=_MCP_ZH_NAMES.get(k, k),
            description=_MCP_ZH_DESC.get(k, f"MCP 服务：{_MCP_ZH_NAMES.get(k, k)}"),
            enabled=True,
            config={"server": k},
        )
        for k, v in mcp_servers.items()
    ]

    try:
        from agent_skills.loader import get_skill_loader

        # Use metadata loading (fast, no instructions parsing)
        loader = get_skill_loader()
        skill_metadata = list(loader.load_all_metadata().values())
    except Exception:
        skill_metadata = []

    skill_items: List[Dict[str, Any]] = []
    for metadata in skill_metadata:
        skill_items.append(
            _item(
                item_id=metadata.id,
                kind="tool_bundle",
                name=metadata.name,
                description=metadata.description,
                enabled=True,
                version=metadata.version,
                config={"tags": metadata.tags},
            )
        )
    if not skill_items:
        skill_items = [
            _item(
                item_id="report_generation_bundle",
                kind="tool_bundle",
                name="Report Generation Bundle",
                description="Builtin report generation capability bundle.",
                enabled=True,
                config={"bundle": "reporting"},
            )
        ]

    agent_items: List[Dict[str, Any]] = []

    return {
        "skills": skill_items,
        "agents": agent_items,
        "mcp": mcp_items,
        "kb": [],
    }


# ── Dynamic spec loading ──────────────────────────────────────────────────

def _extract_skill_file_path(skill_path: str) -> Path:
    raw = str(skill_path or "")
    actual_path = raw.split(":", 1)[1] if ":" in raw else raw
    return Path(actual_path)


def _load_dynamic_skill_specs() -> Dict[str, Dict[str, Any]]:
    """Load dynamic skill metadata + SKILL.md detail from skill directories."""
    try:
        from agent_skills.loader import get_skill_loader
        loader = get_skill_loader()
        # Refresh metadata cache to support hot-reload for bind-mounted skill files.
        loader.clear_cache()
        metadata_map = loader.load_all_metadata()
    except Exception as e:
        _LOGGER.warning(f"Failed to load dynamic skill specs: {e}")
        return {}

    # Pre-build a map of skill_id → raw content for DB-backed skills
    _db_content_map: Dict[str, str] = {}
    try:
        for skill_info in loader._backend.list_skill_files():
            if skill_info.content is not None:
                _db_content_map[skill_info.skill_id] = skill_info.content
    except Exception:
        pass

    result: Dict[str, Dict[str, Any]] = {}
    for sid, metadata in metadata_map.items():
        detail = ""
        try:
            # DB-backed skills: use in-memory content directly
            if sid in _db_content_map:
                detail = _db_content_map[sid]
            else:
                skill_file = _extract_skill_file_path(metadata.skill_path)
                if skill_file.exists():
                    detail = skill_file.read_text(encoding="utf-8")
        except Exception as e:
            _LOGGER.warning(f"Failed to read SKILL.md for {sid}: {e}")

        result[sid] = {
            "id": sid,
            "name": metadata.name,
            "description": metadata.description,
            "version": metadata.version,
            "tags": metadata.tags,
            "detail": detail,
        }
    return result


def _load_dynamic_mcp_specs() -> Dict[str, Dict[str, str]]:
    """Load dynamic MCP details from mcp_servers definitions AND admin DB."""
    try:
        from configs.mcp_config import (
            MCP_SERVERS,
            MCP_SERVER_DESCRIPTIONS,
            MCP_SERVER_DISPLAY_NAMES,
            get_mcp_server_with_detail,
        )
    except Exception as e:
        _LOGGER.warning(f"Failed to load MCP configs: {e}")
        return {}

    result: Dict[str, Dict[str, str]] = {}
    for sid in MCP_SERVERS.keys():
        detail = ""
        try:
            cfg = get_mcp_server_with_detail(sid)
            if isinstance(cfg, dict):
                detail = str(cfg.get("detail", "") or "")
        except Exception as e:
            _LOGGER.warning(f"Failed to extract dynamic MCP detail for {sid}: {e}")

        result[sid] = {
            "id": sid,
            "name": MCP_SERVER_DISPLAY_NAMES.get(sid, sid),
            "description": MCP_SERVER_DESCRIPTIONS.get(sid, f"MCP 服务：{sid}"),
            "detail": detail,
        }

    # Also include admin-created MCP servers from DB (enabled only —
    # disabled servers must not appear in specs so the stale-removal
    # logic in _sync_catalog_items_from_sources() drops them from
    # catalog.json, preventing leakage to the frontend.)
    try:
        from core.db.engine import SessionLocal
        from core.db.models import AdminMcpServer

        with SessionLocal() as db:
            for row in db.query(AdminMcpServer).filter(AdminMcpServer.is_enabled == True).all():
                sid = row.server_id
                if sid not in result:
                    # Build detail from cached tools_json
                    detail = ""
                    tools_json = getattr(row, "tools_json", None) or []
                    if tools_json:
                        lines = [f"### {row.display_name or sid}\n"]
                        for tool in tools_json:
                            name = tool.get("name", "")
                            desc = tool.get("description", "").strip()
                            lines.append(f"- **{name}**：{desc}" if desc else f"- **{name}**")
                        detail = "\n".join(lines)
                    result[sid] = {
                        "id": sid,
                        "name": row.display_name or sid,
                        "description": row.description or f"MCP 服务：{sid}",
                        "detail": detail,
                    }
    except Exception as e:
        _LOGGER.debug("Could not load admin MCP servers from DB: %s", e)

    return result


# ── Sync & attach ─────────────────────────────────────────────────────────

def _sync_catalog_items_from_sources(data: Dict[str, Any]) -> bool:
    """Ensure catalog has all skills/MCP ids discovered from dynamic sources."""
    changed = False

    skills_node = data.get("skills")
    if not isinstance(skills_node, list):
        skills_node = []
        data["skills"] = skills_node
        changed = True
    mcp_node = data.get("mcp")
    if not isinstance(mcp_node, list):
        mcp_node = []
        data["mcp"] = mcp_node
        changed = True

    skill_specs = _load_dynamic_skill_specs()
    skill_index = {
        str(item.get("id", "")).strip(): item
        for item in skills_node
        if isinstance(item, dict)
    }
    for sid, spec in skill_specs.items():
        if sid in skill_index:
            item = skill_index[sid]
            # Always sync name/description/version from dynamic source so that
            # admin edits (e.g. display_name changes) are reflected immediately.
            if str(item.get("name", "")).strip() != spec["name"]:
                item["name"] = spec["name"]
                changed = True
            if str(item.get("description", "")).strip() != spec["description"]:
                item["description"] = spec["description"]
                item["desc"] = spec["description"]
                changed = True
            if str(item.get("version", "")).strip() != spec["version"]:
                item["version"] = spec["version"]
                changed = True
            if not isinstance(item.get("config"), dict):
                item["config"] = {}
                changed = True
            if spec["tags"] and not item["config"].get("tags"):
                item["config"]["tags"] = spec["tags"]
                changed = True
            continue

        skills_node.append(
            _item(
                item_id=sid,
                kind="tool_bundle",
                name=spec["name"],
                description=spec["description"],
                enabled=True,
                version=spec["version"],
                config={"tags": spec["tags"]},
            )
        )
        changed = True

    # Remove stale skill entries whose id no longer exists in any dynamic source
    before = len(skills_node)
    skills_node[:] = [
        item for item in skills_node
        if isinstance(item, dict) and str(item.get("id", "")).strip() in skill_specs
    ]
    if len(skills_node) != before:
        changed = True

    mcp_specs = _load_dynamic_mcp_specs()
    mcp_index = {
        str(item.get("id", "")).strip(): item
        for item in mcp_node
        if isinstance(item, dict)
    }
    for sid, spec in mcp_specs.items():
        if sid in mcp_index:
            item = mcp_index[sid]
            if not str(item.get("name", "")).strip():
                item["name"] = spec["name"]
                changed = True
            if not str(item.get("description", "")).strip():
                item["description"] = spec["description"]
                item["desc"] = spec["description"]
                changed = True
            cfg = item.get("config")
            if not isinstance(cfg, dict):
                item["config"] = {"server": sid}
                changed = True
            elif not str(cfg.get("server", "")).strip():
                cfg["server"] = sid
                changed = True
            continue

        mcp_node.append(
            _item(
                item_id=sid,
                kind="mcp_server",
                name=spec["name"],
                description=spec["description"],
                enabled=True,
                config={"server": sid},
            )
        )
        changed = True

    # Remove stale MCP entries whose id no longer exists in any dynamic source
    before = len(mcp_node)
    mcp_node[:] = [
        item for item in mcp_node
        if isinstance(item, dict) and str(item.get("id", "")).strip() in mcp_specs
    ]
    if len(mcp_node) != before:
        changed = True

    return changed


def _strip_static_detail_fields(data: Dict[str, Any]) -> bool:
    """Remove persisted detail fields for dynamic-detail kinds (skills/mcp)."""
    changed = False
    for key in ("skills", "mcp"):
        node = data.get(key)
        if not isinstance(node, list):
            continue
        for item in node:
            if isinstance(item, dict) and "detail" in item:
                item.pop("detail", None)
                changed = True
    return changed


def _attach_runtime_details(data: Dict[str, Any]) -> None:
    """Attach dynamic runtime details for skills and mcp without persisting."""
    skill_specs = _load_dynamic_skill_specs()
    skills_node = data.get("skills")
    if isinstance(skills_node, list):
        for item in skills_node:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id", "")).strip()
            spec = skill_specs.get(sid)
            if not spec:
                continue
            # Always sync name/description/version from dynamic source
            item["name"] = spec["name"]
            if spec["detail"]:
                item["detail"] = spec["detail"]
            item["description"] = spec["description"]
            item["desc"] = spec["description"]
            item["version"] = spec["version"]

    mcp_specs = _load_dynamic_mcp_specs()
    mcp_node = data.get("mcp")
    if isinstance(mcp_node, list):
        for item in mcp_node:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id", "")).strip()
            spec = mcp_specs.get(sid)
            if not spec:
                continue
            # Preserve explicit catalog.json labels/descriptions so manual edits
            # remain visible in the frontend. Runtime MCP metadata still fills
            # blanks and provides the dynamic detail block below.
            if not str(item.get("name", "")).strip():
                item["name"] = spec["name"]
            if not str(item.get("description", "")).strip():
                item["description"] = spec["description"]
            if not str(item.get("desc", "")).strip():
                item["desc"] = str(item.get("description") or spec["description"])
            if spec["detail"]:
                item["detail"] = spec["detail"]
            cfg = item.get("config")
            if not isinstance(cfg, dict):
                item["config"] = {"server": sid}
            elif not str(cfg.get("server", "")).strip():
                cfg["server"] = sid


# ── Full catalog load (with cache) ────────────────────────────────────────

def ensure_default_catalog() -> Dict[str, Any]:
    """Create catalog.json with defaults if missing; return the loaded catalog."""
    from configs.catalog import get_catalog

    if not _CATALOG_PATH.exists():
        cat = _default_catalog()
        _CATALOG_PATH.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return cat

    return get_catalog()


def load_catalog(*, include_runtime_details: bool = True) -> Dict[str, Any]:
    """Load catalog.json; if missing or invalid, recreate defaults.

    Results are cached in-memory for up to ``_CATALOG_CACHE_TTL`` seconds to
    avoid repeated disk I/O and dynamic source loading on every request.
    """
    from configs.catalog_migration import _migrate_legacy_shape

    now = monotonic()
    cached_time = _CATALOG_CACHE_TIME.get(include_runtime_details, 0.0)
    if include_runtime_details in _CATALOG_CACHE and (now - cached_time) < _CATALOG_CACHE_TTL:
        return copy.deepcopy(_CATALOG_CACHE[include_runtime_details])

    if not _CATALOG_PATH.exists():
        result = ensure_default_catalog()
        _CATALOG_CACHE[include_runtime_details] = copy.deepcopy(result)
        _CATALOG_CACHE_TIME[include_runtime_details] = monotonic()
        return result

    try:
        raw = _read_raw_catalog()
        data = _migrate_legacy_shape(raw)
    except Exception:
        # Reset to defaults on any parse/shape error (safe-by-default).
        data = _default_catalog()
        _write_catalog(data)
        return data

    # Ensure required top-level keys exist and keep arrays.
    defaults = _default_catalog()
    changed = False
    for key in ("skills", "agents", "mcp", "kb"):
        if key not in data:
            data[key] = defaults[key]
            changed = True
        if not isinstance(data.get(key), list):
            data[key] = []
            changed = True

    # Keep catalog ids in sync with dynamic sources.
    if _sync_catalog_items_from_sources(data):
        changed = True

    # Do not persist static detail fields for skills/mcp.
    if _strip_static_detail_fields(data):
        changed = True

    if changed:
        _write_catalog(data)

    if include_runtime_details:
        _attach_runtime_details(data)

    _CATALOG_CACHE[include_runtime_details] = copy.deepcopy(data)
    _CATALOG_CACHE_TIME[include_runtime_details] = monotonic()
    return data
