"""Runtime hooks for pluggable prompt/tools.

This file defines the main integration boundaries so later we can swap in
alternative prompt builders or tool routers.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Dict, Iterable, List, Optional, Tuple

from prompts.prompt_config import PromptConfig
from prompts.provider import (
    FilesystemPromptProvider,
    InlinePromptProvider,
    hardcoded_minimal_system_prompt,
)

# ── System prompt TTL cache ──────────────────────────────────────────────
_PROMPT_CACHE_TTL = 300.0  # seconds
_prompt_cache_lock = Lock()
# key -> (expires_at, prompt_template_without_now)
_prompt_cache: Dict[tuple, Tuple[float, str]] = {}


_db_version_cache_lock = Lock()
_db_version_cache: Optional[Tuple[float, str]] = None
_DB_VERSION_CACHE_TTL = 30.0  # seconds

# ── Pre-loaded DB prompt parts (populated by warmup, invalidated on change) ──
_db_parts_preloaded_lock = Lock()
_db_parts_preloaded: Optional[Dict[str, Dict[str, Any]]] = None


def _get_db_prompt_version() -> str:
    """Return MAX(updated_at) from admin_prompt_parts as a cache-busting version string.

    Cached for 30s to avoid hitting DB on every build_system_prompt call.
    Invalidated alongside the prompt cache by _invalidate_prompt_cache().
    """
    global _db_version_cache
    now = monotonic()
    with _db_version_cache_lock:
        if _db_version_cache is not None:
            expires_at, val = _db_version_cache
            if now < expires_at:
                return val

    try:
        from sqlalchemy import func
        from core.db.engine import SessionLocal
        from core.db.models import AdminPromptPart
        db = SessionLocal()
        try:
            result = db.query(func.max(AdminPromptPart.updated_at)).scalar()
            val = result.isoformat() if result else ""
        finally:
            db.close()
    except Exception:
        val = ""

    with _db_version_cache_lock:
        _db_version_cache = (now + _DB_VERSION_CACHE_TTL, val)
    return val


def _load_db_prompt_parts() -> Dict[str, Dict[str, Any]]:
    """Load prompt part overrides from DB.

    Returns pre-loaded cache if available (populated by warmup_prompt_cache),
    otherwise falls back to a live DB query. Returns empty dict on failure.
    """
    with _db_parts_preloaded_lock:
        if _db_parts_preloaded is not None:
            return _db_parts_preloaded

    return _fetch_db_prompt_parts()


def _fetch_db_prompt_parts() -> Dict[str, Dict[str, Any]]:
    """Direct DB query for prompt parts. Always hits the database."""
    try:
        from core.db.engine import SessionLocal
        from core.db.models import AdminPromptPart
        db = SessionLocal()
        try:
            rows = db.query(AdminPromptPart).all()
            return {
                r.part_id: {
                    "content": r.content,
                    "sort_order": r.sort_order,
                    "is_enabled": r.is_enabled,
                }
                for r in rows
            }
        finally:
            db.close()
    except Exception:
        return {}


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def warmup_prompt_cache() -> None:
    """Pre-load DB prompt parts and version at startup.

    Call this during application startup so that the first chat request
    does not need to query the database for prompt parts.
    """
    global _db_parts_preloaded
    import logging
    log = logging.getLogger(__name__)

    parts = _fetch_db_prompt_parts()
    with _db_parts_preloaded_lock:
        _db_parts_preloaded = parts

    # Also warm the version cache
    _get_db_prompt_version()
    log.info("[prompt_cache] Warmed up: %d DB prompt parts loaded", len(parts))


def invalidate_prompt_cache() -> None:
    """Clear all prompt caches so changes take effect on next request.

    Call this after admin prompt edits, skill toggles, or catalog changes.
    """
    global _db_parts_preloaded, _db_version_cache
    import logging
    log = logging.getLogger(__name__)

    with _db_parts_preloaded_lock:
        _db_parts_preloaded = None
    with _db_version_cache_lock:
        _db_version_cache = None
    with _prompt_cache_lock:
        _prompt_cache.clear()
    with _kb_lite_cache_lock:
        _kb_lite_cache.clear()

    # Re-populate the preloaded cache immediately so the next request is fast
    parts = _fetch_db_prompt_parts()
    with _db_parts_preloaded_lock:
        _db_parts_preloaded = parts
    _get_db_prompt_version()

    log.info("[prompt_cache] Invalidated and re-warmed: %d DB prompt parts", len(parts))


_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Lightweight KB catalog — name + description only (no document lists).
# Injected into system prompt per user's enabled_kbs, from cached data.
# ---------------------------------------------------------------------------
_kb_lite_cache_lock = Lock()
# key: frozenset of enabled_kb_ids -> (expires_at, section_text)
_kb_lite_cache: Dict[frozenset, Tuple[float, str]] = {}
_KB_LITE_CACHE_TTL = 300.0  # 5 minutes


def invalidate_kb_lite_cache() -> None:
    """Clear lightweight KB catalog cache."""
    with _kb_lite_cache_lock:
        _kb_lite_cache.clear()


def _build_kb_lite_section(enabled_kb_ids: Optional[List[str]]) -> str:
    """Build a minimal KB catalog (name + description) for system prompt injection.

    Only uses cached Dify dataset list (no extra API calls) and fast DB queries.
    Typical output: 3-10 lines, 300-800 chars.
    """
    if not enabled_kb_ids:
        return ""

    cache_key = frozenset(enabled_kb_ids)
    now = monotonic()

    with _kb_lite_cache_lock:
        cached = _kb_lite_cache.get(cache_key)
        if cached is not None:
            expires_at, text = cached
            if now < expires_at:
                return text

    import logging
    _log = logging.getLogger(__name__)

    dify_ids = [kid for kid in enabled_kb_ids if not kid.startswith("kb_")]
    local_ids = [kid for kid in enabled_kb_ids if kid.startswith("kb_")]

    lines: List[str] = []

    # ── Public datasets (Dify) — from cached list, no extra HTTP calls ────
    if dify_ids:
        try:
            from utils.dify_kb import is_dify_enabled, list_datasets
            if is_dify_enabled():
                dify_set = set(dify_ids)
                datasets = list_datasets(page=1, limit=100, timeout=(1, 2))
                for ds in datasets:
                    ds_id = str(ds.get("id", "")).strip()
                    if ds_id and ds_id in dify_set:
                        name = ds.get("name", ds_id)
                        desc = ds.get("description") or ds.get("desc") or ""
                        desc_part = f"：{desc[:120]}" if desc else ""
                        lines.append(f"- {name}（公有，dataset_id: `{ds_id}`）{desc_part}")
        except Exception as exc:
            _log.debug("[kb_lite] Dify list failed: %s", exc)

    # ── Private KBs — fast DB query ───────────────────────────────────────
    if local_ids:
        try:
            from core.db.engine import SessionLocal
            from core.db.models import KBSpace
            with SessionLocal() as db:
                spaces = db.query(KBSpace).filter(
                    KBSpace.kb_id.in_(local_ids),
                    KBSpace.deleted_at.is_(None),
                ).all()
                for s in spaces:
                    desc_part = f"：{s.description[:120]}" if s.description else ""
                    lines.append(f"- {s.name}（私有，kb_id: `{s.kb_id}`）{desc_part}")
        except Exception as exc:
            _log.debug("[kb_lite] DB query failed: %s", exc)

    if not lines:
        return ""

    result = (
        "## 当前启用的知识库\n"
        "当用户提问涉及以下知识库名称或简介中的关键词时，应**主动**调用对应检索工具，无需等待用户显式要求。\n"
        "调用 `list_datasets` 可获取更详细的文档列表。\n\n"
        + "\n".join(lines)
    )

    with _kb_lite_cache_lock:
        _kb_lite_cache[cache_key] = (monotonic() + _KB_LITE_CACHE_TTL, result)

    return result


# ---------------------------------------------------------------------------
# Tool routing hints — only rendered for tools that are actually enabled.
# ---------------------------------------------------------------------------
TOOL_ROUTING_HINTS: dict[str, dict[str, str]] = {
    "retrieve_dataset_content": {
        "priority": "2-高",
        "when": "检索政策文件、产业报告、文档原文",
    },
    "get_chain_information": {
        "priority": "2-高",
        "when": "产业链全景分析、上下游结构、技术路线图谱",
    },
    "get_industry_news": {
        "priority": "3-中",
        "when": "产业动态、新闻、政策发布、融资报道",
    },
    "get_latest_ai_news": {
        "priority": "3-中",
        "when": "近一周AI领域热点聚合",
    },
    "internet_search": {
        "priority": "4-兜底",
        "when": "内部工具和技能均无结果时的最后兜底；若有搜索类技能匹配，应优先走技能",
    },
    "web_fetch": {
        "priority": "-",
        "when": "由搜索类技能（如'中文网页搜索'）指定调用，或由用户要求抓取/爬取对应网页信息时使用，其他情况下不要自行直接使用",
    },
}


def build_subagent_system_prompt(
    user_agent: Any,
    tool_schemas: list,
    enabled_mcp_keys: list[str],
    enabled_kb_ids: Optional[list[str]] = None,
) -> str:
    """为子智能体构建 system prompt。

    结构：
    1. 时间/环境信息 (从 00_time_role 提取通用部分)
    2. 用户自定义 system_prompt (核心角色设定)
    3. 工具使用规范 (20_tools_policy)
    4. 输出格式 (60_format)
    5. 工具路由表 (动态生成)
    6. 轻量 KB 目录 (如有)
    """
    now = datetime.now().isoformat()

    # Read core prompt segments from filesystem
    prompt_dir_cfg = os.getenv("PROMPT_DIR") or "./prompts/prompt_text/v1"
    prompt_dir = _resolve_prompt_dir(prompt_dir_cfg)
    fs = FilesystemPromptProvider(prompt_dir=prompt_dir, strict_vars=False)

    segments: List[str] = []

    # 1. Time info
    segments.append(f"## 当前时间\n{now}")

    # 2. User-defined system prompt (core role)
    custom_prompt = (user_agent.system_prompt or "").strip()
    if custom_prompt:
        segments.append(f"## 角色设定\n{custom_prompt}")

    # 3. Tools policy
    tools_policy = fs.get_prompt("20_tools_policy", "system", vars={"now": now})
    if tools_policy.strip():
        segments.append(tools_policy.strip())

    # 4. Output format
    fmt = fs.get_prompt("60_format", "system", vars={"now": now})
    if fmt.strip():
        segments.append(fmt.strip())

    # 6. Dynamic tool routing table
    if tool_schemas:
        table_rows: List[str] = []
        for tool in tool_schemas:
            name = getattr(tool, "name", None)
            if not name and isinstance(tool, dict):
                func_info = tool.get("function", {})
                name = func_info.get("name") if isinstance(func_info, dict) else None
            if not name:
                continue
            hint = TOOL_ROUTING_HINTS.get(name)
            priority = hint["priority"] if hint else "-"
            when = hint["when"] if hint else ""
            table_rows.append(f"| {name} | {priority} | {when} |")

        if table_rows:
            table_header = "| 工具 | 优先级 | 适用场景 |\n|------|--------|---------|"
            table = table_header + "\n" + "\n".join(table_rows)
            segments.append(
                "## 当前可用 MCP 工具（运行时注入）\n\n"
                + table
                + "\n\n各工具的详细参数与调用规范请参考工具自身的描述信息。"
                "\n\n注意：除上述 MCP 工具外，系统还提供 **Agent Skills**（技能），"
                "列在下方。当 MCP 工具无法满足用户需求时，请检查技能列表。"
            )

    # 7. Lightweight KB catalog
    if enabled_kb_ids:
        kb_section = _build_kb_lite_section(enabled_kb_ids)
        if kb_section:
            segments.append(kb_section)

    return "\n\n".join(segments)


def _resolve_prompt_dir(config_prompt_dir: str) -> Path:
    raw_prompt_dir = os.getenv("PROMPT_DIR") or config_prompt_dir
    path = Path(raw_prompt_dir)
    if path.is_absolute():
        return path

    # Preserve existing behavior first: resolve relative to current working directory.
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path

    # Also support launching from repo root while config uses backend-relative paths.
    backend_path = _BACKEND_ROOT / path
    if backend_path.exists():
        return backend_path

    return cwd_path


def _extract_tool_names(tools) -> Tuple[str, ...]:
    """Extract sorted tool names for cache key construction."""
    names = []
    for tool in (tools or []):
        name = getattr(tool, "name", None)
        if not name and isinstance(tool, dict):
            func_info = tool.get("function", {})
            name = func_info.get("name") if isinstance(func_info, dict) else None
        if name:
            names.append(name)
    return tuple(sorted(names))


def build_system_prompt(config: PromptConfig, ctx: Dict[str, Any] | None = None) -> str:
    """Build the system prompt from config + runtime context.

    Results are cached with a 300s TTL. The {now} placeholder is replaced
    at render time so the cache isn't invalidated every second.

    Adds a *dynamic* appendix describing currently-available tools (name + short description)
    so tools remain pluggable without hardcoding tool names in the static prompt.

    Fallback order:
      1) Filesystem prompt (config.system_prompt.prompt_dir / env PROMPT_DIR)
      2) Inline template (config.system_prompt.inline_template or env PROMPT_INLINE_TEMPLATE)
      3) Minimal hardcoded fallback (guarantee non-empty)

    Args:
        config: Loaded PromptConfig.
        ctx: Runtime context (optional). Recognized keys:
            - now: override timestamp
            - tools: optional iterable of tool objects (each with `.name` and `.description`)
            - mcp_servers: list of enabled MCP server keys
    Returns:
        A non-empty system prompt string.
    """

    ctx = ctx or {}
    now = ctx.get("now") or datetime.now().isoformat()

    # Build cache key from stable inputs (excluding {now})
    prompt_dir_cfg = getattr(config.system_prompt, "prompt_dir", None) or "./prompts/prompt_text/v1"
    parts_key = tuple(config.system_prompt.parts) if config.system_prompt.parts else ()
    tool_names = _extract_tool_names(ctx.get("tools"))
    mcp_keys = tuple(sorted(ctx.get("mcp_servers") or []))
    provider_key = (getattr(config.system_prompt, "provider", None) or "filesystem").strip().lower()
    enabled_kbs_key = tuple(sorted(ctx.get("enabled_kbs") or []))

    # Include DB prompt parts version in cache key for invalidation
    db_version = _get_db_prompt_version()
    cache_key = (provider_key, str(prompt_dir_cfg), parts_key, tool_names, mcp_keys, db_version, enabled_kbs_key)

    # Check cache
    with _prompt_cache_lock:
        cached = _prompt_cache.get(cache_key)
        if cached is not None:
            expires_at, template = cached
            if monotonic() < expires_at:
                return template.replace("{now}", now)
            else:
                _prompt_cache.pop(cache_key, None)

    # Cache miss — build the prompt
    strict_vars = _env_bool("PROMPT_STRICT_VARS", True)

    # Use a placeholder for {now} so we can cache the template
    _NOW_PLACEHOLDER = "__PROMPT_NOW_PLACEHOLDER__"

    provider = provider_key
    base = ""

    # ── Try DB-backed prompt parts first ──────────────────────────────
    db_parts = _load_db_prompt_parts()

    # 1) Filesystem prompt: config-driven prompt pack.
    if provider == "filesystem":
        prompt_dir = _resolve_prompt_dir(str(prompt_dir_cfg))
        fs_provider = FilesystemPromptProvider(prompt_dir=prompt_dir, strict_vars=strict_vars)

        parts = getattr(config.system_prompt, "parts", None)
        if isinstance(parts, list) and parts:
            # Build merged parts list: filesystem + DB-only parts
            all_part_ids = list(parts)
            for pid in db_parts:
                if pid not in all_part_ids:
                    all_part_ids.append(pid)

            # Sort by DB sort_order if available, else filesystem index * 10
            def _sort_key(pid: str) -> int:
                if pid in db_parts:
                    return db_parts[pid]["sort_order"]
                try:
                    return parts.index(pid) * 10
                except ValueError:
                    return 9999

            sorted_ids = sorted(all_part_ids, key=_sort_key) if db_parts else parts

            chunks: List[str] = []
            for part_id in sorted_ids:
                part_id_str = part_id.strip() if isinstance(part_id, str) else ""
                if not part_id_str:
                    continue

                db_row = db_parts.get(part_id_str)
                if db_row:
                    # DB override: check is_enabled
                    if not db_row["is_enabled"]:
                        continue
                    txt = db_row["content"]
                    # Apply variable substitution
                    from prompts.provider import render_template
                    txt = render_template(txt, vars={"now": _NOW_PLACEHOLDER, **ctx}, strict=False)
                else:
                    txt = fs_provider.get_prompt(part_id_str, "system", vars={"now": _NOW_PLACEHOLDER, **ctx})

                if txt.strip():
                    chunks.append(txt.strip())
            base = "\n\n".join(chunks).strip()
        else:
            # Backward compatible single-file convention: system.system.md
            base = fs_provider.get_prompt("system", "system", vars={"now": _NOW_PLACEHOLDER, **ctx})

    # 2) Inline prompt.
    if (not base.strip()) and provider == "inline":
        inline_provider = InlinePromptProvider(
            template=(getattr(config.system_prompt, "inline_template", "") or os.getenv("PROMPT_INLINE_TEMPLATE", "")),
            strict_vars=strict_vars,
        )
        base = inline_provider.get_prompt("system", "system", vars={"now": _NOW_PLACEHOLDER, **ctx})

    # 3) Absolute minimal fallback (guarantee non-empty).
    if not base.strip():
        base = hardcoded_minimal_system_prompt().strip()

    tools = ctx.get("tools")

    if tools:
        # Build a tool catalog appendix with routing hints table.
        table_rows: List[str] = []
        for tool in tools:
            name = getattr(tool, "name", None)
            # Support AgentScope JSON schemas (dict with function.name)
            if not name and isinstance(tool, dict):
                func_info = tool.get("function", {})
                name = func_info.get("name") if isinstance(func_info, dict) else None
            if not name:
                continue
            hint = TOOL_ROUTING_HINTS.get(name)
            priority = hint["priority"] if hint else "-"
            when = hint["when"] if hint else ""
            table_rows.append(f"| {name} | {priority} | {when} |")

        if table_rows:
            table_header = "| 工具 | 优先级 | 适用场景 |\n|------|--------|---------|"
            table = table_header + "\n" + "\n".join(table_rows)

            appendix_parts = [
                "## 当前可用 MCP 工具（运行时注入）",
                table,
                "各工具的详细参数与调用规范请参考工具自身的描述信息。\n\n"
                "注意：除上述 MCP 工具外，系统还提供 **Agent Skills**（技能），"
                "列在下方。当 MCP 工具无法满足用户需求时，请检查技能列表。",
            ]

            base = (base + "\n\n" + "\n\n".join(appendix_parts)).strip()

    # ── Lightweight KB catalog (name + description only) ──
    enabled_kbs = ctx.get("enabled_kbs")
    if enabled_kbs:
        kb_section = _build_kb_lite_section(enabled_kbs)
        if kb_section:
            base = (base + "\n\n" + kb_section).strip()

    # Store template in cache (with placeholder instead of real time)
    template = base.replace(now, "{now}") if now in base else base
    # Also replace the placeholder back to {now} for storage
    template = template.replace(_NOW_PLACEHOLDER, "{now}")

    with _prompt_cache_lock:
        _prompt_cache[cache_key] = (monotonic() + _PROMPT_CACHE_TTL, template)

    # Return with real time
    return template.replace("{now}", now)


def select_tools(
    config: PromptConfig,
    ctx: Dict[str, Any] | None,
    all_tools: Iterable[Any],
) -> List[Any]:
    """Select tools according to allowlist/routing config.

    Note: tool objects are expected to have a stable `.name` attribute.
    """

    allowed = set(config.tools.allowed or [])
    if not allowed:
        return list(all_tools)

    selected: List[Any] = []
    for tool in all_tools:
        name = getattr(tool, "name", None)
        # Support AgentScope JSON schemas (dict with function.name)
        if not name and isinstance(tool, dict):
            func_info = tool.get("function", {})
            name = func_info.get("name") if isinstance(func_info, dict) else None
        if not name:
            continue
        if name in allowed:
            selected.append(tool)

    # If allowlist accidentally filters everything, fail open.
    if not selected and not config.tools.routing.strict_allowlist:
        return list(all_tools)

    return selected
