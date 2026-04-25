"""Central model configuration service (DB-driven, cached).

Replaces all os.getenv() calls for model URLs / API keys / model names.
Thread-safe singleton with a short TTL cache so admin changes take effect
within seconds without requiring a restart.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.db.engine import SessionLocal
from core.db.models import ModelProvider, ModelRoleAssignment

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class ResolvedModelConfig:
    """All info needed to call one model endpoint."""
    base_url: str
    api_key: str
    model_name: str
    temperature: float = 0.6
    max_tokens: int = 8192
    timeout: int = 120
    extra: dict = field(default_factory=dict)


class ModelConfigService:
    """Thread-safe singleton that resolves role → model config from DB."""

    _instance: Optional["ModelConfigService"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._cache: dict[str, Optional[ResolvedModelConfig]] = {}
        self._cache_ts: float = 0.0
        self._cache_lock = threading.Lock()
        self._version: int = 0  # bumped on invalidate

    @classmethod
    def get_instance(cls) -> "ModelConfigService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def version(self) -> int:
        return self._version

    # ── resolve ────────────────────────────────────────────────────────

    def resolve(self, role_key: str) -> Optional[ResolvedModelConfig]:
        """Return config for *role_key*, or None if not assigned."""
        self._maybe_refresh()
        return self._cache.get(role_key)

    def resolve_required(self, role_key: str) -> ResolvedModelConfig:
        """Like resolve() but raises if not configured."""
        cfg = self.resolve(role_key)
        if cfg is None:
            raise RuntimeError(f"模型角色 '{role_key}' 未配置，请在管理后台分配模型")
        return cfg

    # ── cache management ──────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_ts = 0.0
            self._version += 1

    def _maybe_refresh(self) -> None:
        now = time.monotonic()
        if now - self._cache_ts < _CACHE_TTL_SECONDS and self._cache:
            return
        with self._cache_lock:
            # double-check
            if now - self._cache_ts < _CACHE_TTL_SECONDS and self._cache:
                return
            self._load_from_db()
            self._cache_ts = time.monotonic()

    def _load_from_db(self) -> None:
        new_cache: dict[str, Optional[ResolvedModelConfig]] = {}
        try:
            db = SessionLocal()
            try:
                rows = (
                    db.query(ModelRoleAssignment, ModelProvider)
                    .join(ModelProvider, ModelRoleAssignment.provider_id == ModelProvider.provider_id)
                    .filter(ModelProvider.is_active == True)  # noqa: E712
                    .all()
                )
                for assignment, provider in rows:
                    extra = dict(provider.extra_config or {})
                    new_cache[assignment.role_key] = ResolvedModelConfig(
                        base_url=provider.base_url,
                        api_key=provider.api_key,
                        model_name=provider.model_name,
                        temperature=float(extra.pop("temperature", 0.6)),
                        max_tokens=int(extra.pop("max_tokens", 8192)),
                        timeout=int(extra.pop("timeout", 120)),
                        extra=extra,
                    )
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[ModelConfigService] DB load failed, keeping stale cache: %s", exc)
            return  # keep whatever was there before

        self._cache = new_cache

    # ── MCP env overlay ───────────────────────────────────────────────

    def get_mcp_env_overlay(self) -> dict[str, str]:
        """Return env-var style dict for injecting into MCP sub-processes.

        Maps role configs to the legacy env var names that MCP servers expect.
        """
        overlay: dict[str, str] = {}

        main = self.resolve("main_agent")
        if main:
            overlay["MODEL_URL"] = main.base_url
            overlay["API_KEY"] = main.api_key
            overlay["BASE_MODEL_NAME"] = main.model_name
            overlay["OPENAI_API_BASE"] = main.base_url
            overlay["OPENAI_BASE_URL"] = main.base_url
            overlay["OPENAI_API_KEY"] = main.api_key

        chart = self.resolve("chart") or main
        if chart:
            overlay.setdefault("MODEL_URL", chart.base_url)
            overlay.setdefault("API_KEY", chart.api_key)
            overlay.setdefault("BASE_MODEL_NAME", chart.model_name)

        embed = self.resolve("embedding")
        if embed:
            overlay["MEM0_EMBED_URL"] = embed.base_url
            overlay["MEM0_EMBED_MODEL"] = embed.model_name
            overlay["MEM0_EMBED_API_KEY"] = embed.api_key
            dims = embed.extra.get("dimensions")
            if dims:
                overlay["MEM0_EMBED_DIMS"] = str(dims)

        reranker = self.resolve("reranker")
        if reranker:
            overlay["RERANKER_URL"] = reranker.base_url
            overlay["RERANKER_MODEL"] = reranker.model_name
            overlay["RERANKER_API_KEY"] = reranker.api_key

        return overlay
