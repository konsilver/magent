"""Centralized application settings.

All environment variables are read here once at import time.
Other modules should ``from core.config.settings import settings`` instead
of calling ``os.getenv()`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values


_REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_env_files() -> None:
    """Load repo-level env files without overriding real process env vars.

    Precedence:
    1. Existing process environment
    2. Env-specific file such as .env.dev
    3. Base .env
    """
    base_env_path = _REPO_ROOT / ".env"
    base_values = dotenv_values(base_env_path) if base_env_path.exists() else {}

    resolved_env = (
        os.getenv("ENV")
        or os.getenv("ENVIRONMENT")
        or str(base_values.get("ENV") or "")
        or str(base_values.get("ENVIRONMENT") or "")
    ).strip().lower()

    candidate_paths = [base_env_path]
    if resolved_env:
        candidate_paths.append(_REPO_ROOT / f".env.{resolved_env}")
    elif (_REPO_ROOT / ".env.dev").exists():
        candidate_paths.append(_REPO_ROOT / ".env.dev")

    merged_values: dict[str, str] = {}
    for path in candidate_paths:
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                merged_values[key] = value

    for key, value in merged_values.items():
        os.environ.setdefault(key, value)


_load_env_files()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes")


def _int(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class AuthSettings:
    mode: str = field(default_factory=lambda: _env("AUTH_MODE", "mock"))
    api_url: str = field(default_factory=lambda: _env("AUTH_API_URL", ""))
    api_timeout: int = field(default_factory=lambda: _int(_env("AUTH_API_TIMEOUT", "5"), 5))
    retry_count: int = field(default_factory=lambda: _int(_env("AUTH_RETRY_COUNT", "2"), 2))
    mock_user_id: str = field(default_factory=lambda: _env("AUTH_MOCK_USER_ID", "dev_user_001"))
    mock_username: str = field(default_factory=lambda: _env("AUTH_MOCK_USERNAME", "Developer"))
    admin_token: str = field(default_factory=lambda: _env("ADMIN_TOKEN", ""))
    config_token: str = field(default_factory=lambda: _env("CONFIG_TOKEN", ""))


@dataclass(frozen=True)
class SSOSettings:
    login_url: str = field(default_factory=lambda: _env("SSO_LOGIN_URL", ""))
    login_mode: str = field(default_factory=lambda: _env("SSO_LOGIN_MODE", "").lower())
    mock_enabled: bool = field(default_factory=lambda: _bool(_env("SSO_MOCK_ENABLED", "false")))
    exchange_mode: str = field(default_factory=lambda: _env("SSO_EXCHANGE_MODE", "").lower())
    ticket_exchange_url: str = field(default_factory=lambda: _env("SSO_TICKET_EXCHANGE_URL", ""))
    timeout: int = field(default_factory=lambda: _int(_env("SSO_TIMEOUT_SECONDS", "5"), 5))


@dataclass(frozen=True)
class SessionSettings:
    cookie_name: str = field(default_factory=lambda: _env("SESSION_COOKIE_NAME", "jx_session"))
    cookie_secure: bool = field(default_factory=lambda: _bool(_env("SESSION_COOKIE_SECURE", "false")))
    cookie_samesite: str = field(default_factory=lambda: _env("SESSION_COOKIE_SAMESITE", "lax"))
    cookie_domain: Optional[str] = field(default_factory=lambda: _env("SESSION_COOKIE_DOMAIN", "") or None)
    cookie_httponly: bool = field(default_factory=lambda: _bool(_env("SESSION_COOKIE_HTTPONLY", "false")))
    ttl_hours: float = field(default_factory=lambda: float(_env("SESSION_TTL_HOURS", "8")))
    store_type: str = field(default_factory=lambda: _env("SESSION_STORE", "memory").lower().strip())


@dataclass(frozen=True)
class DatabaseSettings:
    url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./jingxin.db"))
    sqlite_fallback_url: str = field(default_factory=lambda: _env("SQLITE_FALLBACK_URL", "sqlite:///./jingxin_dev.db"))
    echo: bool = field(default_factory=lambda: _bool(_env("DB_ECHO", "false")))
    pool_size: int = field(default_factory=lambda: _int(_env("DB_POOL_SIZE", "20"), 20))
    pool_max_overflow: int = field(default_factory=lambda: _int(_env("DB_POOL_MAX_OVERFLOW", "10"), 10))
    pool_timeout: int = field(default_factory=lambda: _int(_env("DB_POOL_TIMEOUT", "30"), 30))


@dataclass(frozen=True)
class RoleModelSettings:
    """Per-role LLM model name overrides.

    Each field corresponds to one agent role.  When the env var is absent the
    role falls back to ``BASE_MODEL_NAME``.  Set ``ROLE_<ROLE>_MODEL`` in your
    ``.env`` file to override a specific role without touching the others.

    Role env vars:
        ROLE_USER_PROFILE_MODEL         — UserProfile agent
        ROLE_PLAN_MODEL                 — Planner agent
        ROLE_WARMUP_MODEL               — Warmup agent
        ROLE_SUBAGENT_MODEL             — SubAgent (step executor, complex steps)
        ROLE_SUBAGENT_SIMPLE_MODEL      — SubAgent (simple steps, fast model)
        ROLE_QA_MODEL                   — QA agent (complex steps)
        ROLE_QA_SIMPLE_MODEL            — QA agent (simple steps, fast model)
        ROLE_INTENT_MODEL               — Intent classifier (confirm/replan)
        ROLE_SUMMARY_MODEL              — Summary agent (final plan result)
    """

    user_profile: str = field(default_factory=lambda: _env("ROLE_USER_PROFILE_MODEL", _env("BASE_MODEL_NAME", "")))
    plan: str = field(default_factory=lambda: _env("ROLE_PLAN_MODEL", _env("BASE_MODEL_NAME", "")))
    warmup: str = field(default_factory=lambda: _env("ROLE_WARMUP_MODEL", _env("BASE_MODEL_NAME", "")))
    subagent: str = field(default_factory=lambda: _env("ROLE_SUBAGENT_MODEL", _env("BASE_MODEL_NAME", "")))
    subagent_simple: str = field(default_factory=lambda: _env("ROLE_SUBAGENT_SIMPLE_MODEL", _env("ROLE_SUBAGENT_MODEL", _env("BASE_MODEL_NAME", ""))))
    qa: str = field(default_factory=lambda: _env("ROLE_QA_MODEL", _env("BASE_MODEL_NAME", "")))
    qa_simple: str = field(default_factory=lambda: _env("ROLE_QA_SIMPLE_MODEL", _env("ROLE_QA_MODEL", _env("BASE_MODEL_NAME", ""))))
    intent: str = field(default_factory=lambda: _env("ROLE_INTENT_MODEL", _env("BASE_MODEL_NAME", "")))
    summary: str = field(default_factory=lambda: _env("ROLE_SUMMARY_MODEL", _env("ROLE_QA_MODEL", _env("BASE_MODEL_NAME", ""))))


@dataclass(frozen=True)
class LLMSettings:
    model_url: str = field(default_factory=lambda: _env("MODEL_URL", ""))
    api_key: str = field(default_factory=lambda: _env("API_KEY", ""))
    base_model_name: str = field(default_factory=lambda: _env("BASE_MODEL_NAME", ""))
    enable_summary: bool = field(default_factory=lambda: _bool(_env("ENABLE_SUMMARY", "true")))
    summary_max_rounds: int = field(default_factory=lambda: _int(_env("SUMMARY_MAX_ROUNDS", "3"), 3))
    roles: RoleModelSettings = field(default_factory=RoleModelSettings)


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool = field(default_factory=lambda: _bool(_env("MEM0_ENABLED", "false")))
    graph_enabled: bool = field(default_factory=lambda: _bool(_env("MEM0_GRAPH_ENABLED", "false")))
    embed_url: str = field(default_factory=lambda: _env("MEM0_EMBED_URL", ""))
    embed_model: str = field(default_factory=lambda: _env("MEM0_EMBED_MODEL", "qwen3_embedding_8b"))
    embed_api_key: str = field(default_factory=lambda: _env("MEM0_EMBED_API_KEY", "sk-placeholder"))
    embed_dims: int = field(default_factory=lambda: _int(_env("MEM0_EMBED_DIMS", "1024"), 1024))
    model_name: str = field(default_factory=lambda: _env("MEMORY_MODEL_NAME", _env("BASE_MODEL_NAME", "deepseek-chat")))
    model_url: str = field(default_factory=lambda: _env("MEMORY_MODEL_URL", _env("MODEL_URL", "")))
    api_key: str = field(default_factory=lambda: _env("MEMORY_API_KEY", _env("API_KEY", "sk-placeholder")))
    milvus_url: str = field(default_factory=lambda: _env("MILVUS_URL", "http://milvus:19530"))
    milvus_token: str = field(default_factory=lambda: _env("MILVUS_TOKEN", ""))
    neo4j_url: str = field(default_factory=lambda: _env("NEO4J_URL", "bolt://neo4j:7687"))
    neo4j_username: str = field(default_factory=lambda: _env("NEO4J_USERNAME", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "jingxin_neo4j_2026"))


@dataclass(frozen=True)
class StorageSettings:
    type: str = field(default_factory=lambda: _env("STORAGE_TYPE", "local").lower())
    path: str = field(default_factory=lambda: _env("STORAGE_PATH", "").strip())


@dataclass(frozen=True)
class KnowledgeBaseSettings:
    backend: str = field(default_factory=lambda: (_env("KNOWLEDGE_BASE") or "").strip().lower())
    dify_url: str = field(default_factory=lambda: _env("DIFY_URL") or _env("DIFY_BASE_URL") or "")
    dify_api_key: str = field(default_factory=lambda: _env("DIFY_API_KEY") or _env("DIFY_AUTH_TOKEN") or "")
    dify_allowed_dataset_ids: str = field(default_factory=lambda: (_env("DIFY_ALLOWED_DATASET_IDS") or "").strip())
    detail_content_max_chars: int = field(default_factory=lambda: _int(_env("KB_DETAIL_CONTENT_MAX_CHARS", "50000"), 50000))
    reranker_url: str = field(default_factory=lambda: _env("RERANKER_URL", "").rstrip("/"))
    reranker_model: str = field(default_factory=lambda: _env("RERANKER_MODEL", ""))
    reranker_api_key: str = field(default_factory=lambda: _env("RERANKER_API_KEY", ""))


@dataclass(frozen=True)
class RedisSettings:
    url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))


@dataclass(frozen=True)
class ServerSettings:
    env: str = field(default_factory=lambda: _env("ENV", "dev").lower())
    port: int = field(default_factory=lambda: _int(_env("PORT", _env("BACKEND_PORT", "3001")), 3001))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", ""))
    max_request_size: int = field(default_factory=lambda: _int(_env("MAX_REQUEST_SIZE", str(10 * 1024 * 1024)), 10 * 1024 * 1024))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    log_file_path: str = field(default_factory=lambda: (_env("LOG_FILE_PATH") or "/app/logs/backend.log").strip())
    log_file_max_bytes: int = field(default_factory=lambda: _int((_env("LOG_FILE_MAX_BYTES") or "10485760").strip(), 10485760))
    log_file_backup_count: int = field(default_factory=lambda: _int((_env("LOG_FILE_BACKUP_COUNT") or "5").strip(), 5))
    kb_mcp_http_port: int = _int(os.getenv("KB_MCP_HTTP_PORT", "9100"), 9100)
    kb_mcp_http_port: int = field(default_factory=lambda: _int(_env("KB_MCP_HTTP_PORT", "9100"), 9100))

    @property
    def kb_mcp_http_url(self) -> str:
        return f"http://127.0.0.1:{self.kb_mcp_http_port}/mcp"

    @property
    def is_prod(self) -> bool:
        return self.env in ("prod", "production")


@dataclass(frozen=True)
class TracingSettings:
    enabled: bool = field(default_factory=lambda: _bool(_env("TRACING_ENABLED", "false")))
    service_name: str = field(default_factory=lambda: _env("SERVICE_NAME", "jingxin-agent"))
    jaeger_host: str = field(default_factory=lambda: _env("JAEGER_HOST", "localhost"))
    jaeger_port: int = field(default_factory=lambda: _int(_env("JAEGER_PORT", "6831"), 6831))


@dataclass(frozen=True)
class RateLimitSettings:
    enabled: bool = field(default_factory=lambda: _bool(_env("RATE_LIMIT_ENABLED", "true")))
    storage: str = field(default_factory=lambda: _env("RATE_LIMIT_STORAGE", "memory://"))
    cb_user_center_threshold: int = field(default_factory=lambda: _int(_env("CB_USER_CENTER_THRESHOLD", "5"), 5))
    cb_user_center_timeout: int = field(default_factory=lambda: _int(_env("CB_USER_CENTER_TIMEOUT", "60"), 60))
    cb_model_api_threshold: int = field(default_factory=lambda: _int(_env("CB_MODEL_API_THRESHOLD", "10"), 10))
    cb_model_api_timeout: int = field(default_factory=lambda: _int(_env("CB_MODEL_API_TIMEOUT", "30"), 30))
    cb_storage_threshold: int = field(default_factory=lambda: _int(_env("CB_STORAGE_THRESHOLD", "5"), 5))
    cb_storage_timeout: int = field(default_factory=lambda: _int(_env("CB_STORAGE_TIMEOUT", "60"), 60))


@dataclass(frozen=True)
class RoutingSettings:
    strategy: str = field(default_factory=lambda: (_env("ROUTER_STRATEGY") or "main_only").strip().lower())
    followup_enabled: bool = field(default_factory=lambda: _bool(_env("FOLLOWUP_ENABLED", "true")))


@dataclass(frozen=True)
class PromptSettings:
    provider: str = field(default_factory=lambda: (_env("PROMPT_PROVIDER") or "filesystem").strip().lower())
    dir: str = field(default_factory=lambda: _env("PROMPT_DIR", ""))
    inline_template: str = field(default_factory=lambda: _env("PROMPT_INLINE_TEMPLATE", ""))
    config_path: str = field(default_factory=lambda: _env("JX_PROMPT_CONFIG", ""))


@dataclass(frozen=True)
class IndustrySettings:
    url: str = field(default_factory=lambda: _env("INDUSTRY_URL", ""))
    auth_token: str = field(default_factory=lambda: _env("INDUSTRY_AUTH_TOKEN", ""))


@dataclass(frozen=True)
class AppSettings:
    """Top-level settings container — one read from env at startup."""
    auth: AuthSettings = field(default_factory=AuthSettings)
    sso: SSOSettings = field(default_factory=SSOSettings)
    session: SessionSettings = field(default_factory=SessionSettings)
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    kb: KnowledgeBaseSettings = field(default_factory=KnowledgeBaseSettings)
    redis: RedisSettings = field(default_factory=RedisSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    tracing: TracingSettings = field(default_factory=TracingSettings)
    rate_limit: RateLimitSettings = field(default_factory=RateLimitSettings)
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    prompt: PromptSettings = field(default_factory=PromptSettings)
    industry: IndustrySettings = field(default_factory=IndustrySettings)


# Singleton — import this everywhere
settings = AppSettings()
