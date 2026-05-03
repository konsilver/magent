"""Central service configuration service (DB-driven, cached).

Manages external service configs (DB query, KB, industry, file parser) stored
in the system_configs table. Thread-safe singleton with a short TTL cache so
admin changes take effect within seconds without a restart.

Falls back to os.getenv() when DB has no value for a given key.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from core.db.engine import SessionLocal
from core.db.models import SystemConfig

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0

# ── Seed definitions (single source of truth) ────────────────────────────────
# (config_key, default_value, display_name, description, group_key, is_secret)
SEED_CONFIGS: list[tuple[str, str | None, str, str, str, bool]] = [
    # knowledge_base
    ("knowledge_base.provider", "dify", "知识库后端", "知识库服务提供方", "knowledge_base", False),
    ("knowledge_base.url", None, "知识库 API URL", "Dify 或其他知识库服务地址", "knowledge_base", False),
    ("knowledge_base.api_key", None, "知识库 API Key", "知识库服务鉴权密钥", "knowledge_base", True),
    ("knowledge_base.allowed_dataset_ids", None, "允许的数据集 ID", "逗号分隔的数据集 ID 白名单，为空则全部允许", "knowledge_base", False),
    ("knowledge_base.detail_max_chars", "50000", "详情最大字符数", "知识库文档详情最大字符数", "knowledge_base", False),
    # industry
    ("industry.url", None, "产业知识中心 URL", "产业链信息接口地址", "industry", False),
    ("industry.auth_token", None, "产业知识中心 Token", "产业链信息接口鉴权令牌", "industry", True),
    # file_parser
    ("file_parser.api_url", None, "文件解析 API URL", "PDF/文档解析服务地址", "file_parser", False),
    ("file_parser.timeout", "60", "超时时间(秒)", "文件解析请求超时", "file_parser", False),
    ("file_parser.lang_list", "ch", "语言列表", "OCR 语言列表", "file_parser", False),
    ("file_parser.backend", "pipeline", "解析后端", "解析后端引擎", "file_parser", False),
    ("file_parser.parse_method", "auto", "解析方法", "auto / ocr / txt", "file_parser", False),
    ("file_parser.formula_enable", "true", "启用公式识别", "是否启用公式识别", "file_parser", False),
    ("file_parser.table_enable", "true", "启用表格识别", "是否启用表格识别", "file_parser", False),
    # internet_search
    ("internet_search.engine", "tavily", "搜索引擎", "互联网搜索引擎 (tavily / baidu)", "internet_search", False),
    ("internet_search.tavily_api_key", None, "Tavily API Key", "Tavily 互联网搜索服务密钥", "internet_search", True),
    ("internet_search.baidu_api_key", None, "百度搜索 API Key", "百度千帆 AppBuilder 搜索服务密钥", "internet_search", True),
]

# config_key → env var name mapping
_CONFIG_KEY_TO_ENV: dict[str, str] = {
    "knowledge_base.provider": "KNOWLEDGE_BASE",
    "knowledge_base.url": "DIFY_URL",
    "knowledge_base.api_key": "DIFY_API_KEY",
    "knowledge_base.allowed_dataset_ids": "DIFY_ALLOWED_DATASET_IDS",
    "knowledge_base.detail_max_chars": "KB_DETAIL_CONTENT_MAX_CHARS",
    "industry.url": "INDUSTRY_URL",
    "industry.auth_token": "INDUSTRY_AUTH_TOKEN",
    "file_parser.api_url": "FILE_PARSER_API_URL",
    "file_parser.timeout": "FILE_PARSER_TIMEOUT",
    "file_parser.lang_list": "FILE_PARSER_LANG_LIST",
    "file_parser.backend": "FILE_PARSER_BACKEND",
    "file_parser.parse_method": "FILE_PARSER_PARSE_METHOD",
    "file_parser.formula_enable": "FILE_PARSER_FORMULA_ENABLE",
    "file_parser.table_enable": "FILE_PARSER_TABLE_ENABLE",
    "internet_search.engine": "INTERNET_SEARCH_ENGINE",
    "internet_search.tavily_api_key": "TAVILY_API_KEY",
    "internet_search.baidu_api_key": "BAIDU_API_KEY",
}

# Reverse mapping for env-fallback lookups
_ENV_TO_CONFIG_KEY: dict[str, str] = {v: k for k, v in _CONFIG_KEY_TO_ENV.items()}


class SystemConfigService:
    """Thread-safe singleton that resolves service configs from DB with env fallback."""

    _instance: Optional["SystemConfigService"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: dict[str, Optional[str]] = {}
        self._cache_meta: dict[str, dict] = {}  # full row metadata
        self._cache_ts: float = 0.0
        self._cache_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SystemConfigService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── get / get_group ─────────────────────────────────────────────

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a config value. DB first, then env fallback, then default."""
        self._maybe_refresh()
        if key in self._cache and self._cache[key] is not None:
            return self._cache[key]
        # env fallback
        env_key = _CONFIG_KEY_TO_ENV.get(key)
        if env_key:
            env_val = os.getenv(env_key)
            if env_val is not None:
                return env_val.strip()
        return default

    def get_group(self, group_key: str) -> dict[str, str]:
        """Return all config key→value pairs for a group."""
        self._maybe_refresh()
        result: dict[str, str] = {}
        for key, meta in self._cache_meta.items():
            if meta.get("group_key") == group_key:
                val = self.get(key)
                if val is not None:
                    result[key] = val
        return result

    def get_all_configs(self) -> list[dict]:
        """Return all config rows as dicts (for API responses)."""
        self._maybe_refresh()
        return list(self._cache_meta.values())

    def get_group_configs(self, group_key: str) -> list[dict]:
        """Return config rows for a specific group."""
        self._maybe_refresh()
        return [m for m in self._cache_meta.values() if m.get("group_key") == group_key]

    # ── set ─────────────────────────────────────────────────────────

    def set(self, key: str, value: str | None, updated_by: str = "admin") -> None:
        """Update a config value in DB."""
        try:
            db = SessionLocal()
            try:
                row = db.query(SystemConfig).filter(SystemConfig.config_key == key).first()
                if row is None:
                    logger.warning("[SystemConfigService] key %s not found, skipping set", key)
                    return
                row.config_value = value
                row.updated_by = updated_by
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            logger.error("[SystemConfigService] set(%s) failed: %s", key, exc)
            raise
        self.invalidate_cache()

    def bulk_set(self, items: list[dict], updated_by: str = "admin") -> None:
        """Batch update multiple config values. Each item: {key, value}.

        For secret fields, masked values (containing '****') are skipped to
        prevent the frontend from accidentally overwriting real secrets with
        their masked representation.
        """
        try:
            db = SessionLocal()
            try:
                for item in items:
                    key = item.get("key", "").strip()
                    value = item.get("value")
                    if not key:
                        continue
                    row = db.query(SystemConfig).filter(SystemConfig.config_key == key).first()
                    if row is None:
                        continue
                    # Skip masked values for secret fields
                    if row.is_secret and isinstance(value, str) and "****" in value:
                        continue
                    row.config_value = value if value != "" else None
                    row.updated_by = updated_by
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            logger.error("[SystemConfigService] bulk_set failed: %s", exc)
            raise
        self.invalidate_cache()

    # ── cache management ──────────────────────────────────────────

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_meta.clear()
            self._cache_ts = 0.0

    def _maybe_refresh(self) -> None:
        now = time.monotonic()
        if now - self._cache_ts < _CACHE_TTL_SECONDS and self._cache_meta:
            return
        with self._cache_lock:
            if now - self._cache_ts < _CACHE_TTL_SECONDS and self._cache_meta:
                return
            self._load_from_db()
            self._cache_ts = time.monotonic()

    def _ensure_seed_rows(self, db) -> None:
        """Insert any missing seed config rows. Idempotent."""
        existing_keys = {r[0] for r in db.query(SystemConfig.config_key).all()}
        inserted = 0
        for key, val, display, desc, group, secret in SEED_CONFIGS:
            if key not in existing_keys:
                db.add(SystemConfig(
                    config_key=key,
                    config_value=val,
                    display_name=display,
                    description=desc,
                    group_key=group,
                    is_secret=secret,
                ))
                inserted += 1
        if inserted:
            db.commit()
            logger.info("[SystemConfigService] Inserted %d missing seed row(s)", inserted)

    def _load_from_db(self) -> None:
        new_cache: dict[str, Optional[str]] = {}
        new_meta: dict[str, dict] = {}
        try:
            db = SessionLocal()
            try:
                self._ensure_seed_rows(db)
                rows = db.query(SystemConfig).all()
                for row in rows:
                    new_cache[row.config_key] = row.config_value
                    new_meta[row.config_key] = {
                        "config_key": row.config_key,
                        "config_value": row.config_value,
                        "display_name": row.display_name,
                        "description": row.description,
                        "group_key": row.group_key,
                        "is_secret": row.is_secret,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                        "updated_by": row.updated_by,
                    }
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[SystemConfigService] DB load failed, keeping stale cache: %s", exc)
            return

        self._cache = new_cache
        self._cache_meta = new_meta

    # ── env overlay for MCP sub-processes ─────────────────────────

    def get_service_env_overlay(self) -> dict[str, str]:
        """Return env-var style dict for injecting into MCP sub-processes.

        Maps DB configs to the env var names that MCP servers already read via os.getenv().
        Only includes keys that have a non-None value (from DB or env fallback).
        """
        overlay: dict[str, str] = {}
        for config_key, env_key in _CONFIG_KEY_TO_ENV.items():
            val = self.get(config_key)
            if val is not None:
                overlay[env_key] = val
        return overlay
