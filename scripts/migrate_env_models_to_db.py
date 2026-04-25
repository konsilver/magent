#!/usr/bin/env python3
"""One-time migration: read model config from .env and insert into DB.

Usage:
    cd src/backend
    PYTHONPATH=. python ../../scripts/migrate_env_models_to_db.py

Or from repo root:
    PYTHONPATH=src/backend python scripts/migrate_env_models_to_db.py

Prerequisites:
    - DATABASE_URL must be set (or .env must contain it)
    - The model_providers / model_role_assignments tables must already exist
      (run `alembic upgrade head` first)
"""

from __future__ import annotations

import os
import sys
import uuid

from dotenv import load_dotenv

# Load .env from repo root or CWD
for candidate in ("../../.env", "../.env", ".env"):
    if os.path.exists(candidate):
        load_dotenv(candidate)
        break

# Ensure src/backend is on the path
backend_dir = os.path.join(os.path.dirname(__file__), "..", "src", "backend")
if os.path.isdir(backend_dir):
    sys.path.insert(0, os.path.abspath(backend_dir))

from core.database import SessionLocal  # noqa: E402
from core.db_models import ModelProvider, ModelRoleAssignment, SystemConfig  # noqa: E402


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _first(*names: str) -> str:
    for n in names:
        v = _env(n)
        if v:
            return v
    return ""


def main() -> None:
    db = SessionLocal()

    # Check if providers already exist
    existing = db.query(ModelProvider).count()
    if existing > 0:
        print(f"[skip] {existing} provider(s) already in DB — migration not needed.")
        db.close()
        return

    providers_created = 0
    roles_assigned = 0

    # Helper to create a provider + assign a role
    def add(role_key: str, ptype: str, display_name: str,
            base_url: str, api_key: str, model_name: str,
            extra: dict | None = None) -> None:
        nonlocal providers_created, roles_assigned
        if not base_url or not model_name:
            print(f"  [{role_key}] skipped — no base_url or model_name")
            return
        pid = str(uuid.uuid4())
        db.add(ModelProvider(
            provider_id=pid,
            display_name=display_name,
            provider_type=ptype,
            base_url=base_url,
            api_key=api_key or "sk-placeholder",
            model_name=model_name,
            extra_config=extra or {},
            is_active=True,
        ))
        db.add(ModelRoleAssignment(
            role_key=role_key,
            provider_id=pid,
            updated_by="env_migration",
        ))
        providers_created += 1
        roles_assigned += 1
        print(f"  [{role_key}] {display_name} → {model_name} @ {base_url}")

    print("Migrating .env model config to DB...")

    # ── main_agent (DeepSeek) ─────────────────────────────────────────
    main_url = _first("MODEL_URL", "DEEPSEEK_API_BASE", "OPENAI_API_BASE", "OPENAI_BASE_URL")
    main_key = _first("API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    main_model = _first("BASE_MODEL_NAME")
    add("main_agent", "chat", "主模型 (env迁移)", main_url, main_key, main_model)

    # ── alt_agent (Qwen) ─────────────────────────────────────────────
    qwen_url = _first("QWEN_API_BASE", "MODEL_URL", "OPENAI_API_BASE")
    qwen_key = _first("QWEN_API_KEY", "API_KEY", "OPENAI_API_KEY")
    qwen_model = _first("QWEN_MODEL_NAME")
    if qwen_model:
        add("alt_agent", "chat", "备选模型 Qwen (env迁移)", qwen_url, qwen_key, qwen_model)

    # ── summarizer ───────────────────────────────────────────────────
    sum_model = _first("SUMMARIZE_MODEL_NAME")
    if sum_model:
        add("summarizer", "chat", "摘要/分类模型 (env迁移)", main_url, main_key, sum_model)

    # ── followup ─────────────────────────────────────────────────────
    fu_model = _first("FOLLOWUP_MODEL_NAME", "SUMMARIZE_MODEL_NAME")
    if fu_model:
        add("followup", "chat", "追问模型 (env迁移)", main_url, main_key, fu_model)

    # ── memory (mem0 LLM) ────────────────────────────────────────────
    mem_url = _first("MEMORY_MODEL_URL", "MODEL_URL")
    mem_key = _first("MEMORY_API_KEY", "API_KEY")
    mem_model = _first("MEMORY_MODEL_NAME", "BASE_MODEL_NAME")
    if mem_model:
        add("memory", "chat", "记忆模型 (env迁移)", mem_url, mem_key, mem_model)

    # ── embedding ────────────────────────────────────────────────────
    embed_url = _first("MEM0_EMBED_URL")
    embed_key = _first("MEM0_EMBED_API_KEY")
    embed_model = _first("MEM0_EMBED_MODEL")
    embed_dims = _first("MEM0_EMBED_DIMS")
    if embed_url and embed_model:
        extra = {}
        if embed_dims:
            extra["dimensions"] = int(embed_dims)
        add("embedding", "embedding", "向量模型 (env迁移)", embed_url, embed_key, embed_model, extra)

    # ── reranker ─────────────────────────────────────────────────────
    rr_url = _first("RERANKER_URL")
    rr_key = _first("RERANKER_API_KEY")
    rr_model = _first("RERANKER_MODEL")
    if rr_url and rr_model:
        add("reranker", "reranker", "重排序模型 (env迁移)", rr_url, rr_key, rr_model)

    # ── chart (use main model) ───────────────────────────────────────
    if main_model:
        add("chart", "chat", "图表模型 (env迁移)", main_url, main_key, main_model)

    db.commit()
    db.close()

    print(f"\nDone: {providers_created} provider(s) created, {roles_assigned} role(s) assigned.")
    print("You can now remove model-related env vars from .env.")


# ── Service config migration ───────────────────────────────────────────────

# Maps: config_key → list of env var names to try (first non-empty wins)
_SERVICE_ENV_MAP = {
    "query_database.url": ["QUERY_DATABASE_URL", "DATABASE_API_URL", "DB_QUERY_API_URL"],
    "query_database.timeout": ["QUERY_DATABASE_TIMEOUT_SECONDS"],
    "query_database.retry_times": ["QUERY_DATABASE_RETRY_TIMES"],
    "query_database.max_output_tokens": ["QUERY_DATABASE_MAX_OUTPUT_TOKENS"],
    "knowledge_base.provider": ["KNOWLEDGE_BASE"],
    "knowledge_base.url": ["DIFY_URL", "DIFY_BASE_URL"],
    "knowledge_base.api_key": ["DIFY_API_KEY", "DIFY_AUTH_TOKEN"],
    "knowledge_base.allowed_dataset_ids": ["DIFY_ALLOWED_DATASET_IDS"],
    "knowledge_base.detail_max_chars": ["KB_DETAIL_CONTENT_MAX_CHARS"],
    "industry.url": ["INDUSTRY_URL"],
    "industry.auth_token": ["INDUSTRY_AUTH_TOKEN"],
    "file_parser.api_url": ["FILE_PARSER_API_URL"],
    "file_parser.timeout": ["FILE_PARSER_TIMEOUT"],
    "file_parser.lang_list": ["FILE_PARSER_LANG_LIST"],
    "file_parser.backend": ["FILE_PARSER_BACKEND"],
    "file_parser.parse_method": ["FILE_PARSER_PARSE_METHOD"],
    "file_parser.formula_enable": ["FILE_PARSER_FORMULA_ENABLE"],
    "file_parser.table_enable": ["FILE_PARSER_TABLE_ENABLE"],
    "internet_search.tavily_api_key": ["TAVILY_API_KEY"],
}


def migrate_service_configs() -> None:
    """Read .env service config values and update system_configs rows that have NULL values."""
    db = SessionLocal()

    updated = 0
    for config_key, env_names in _SERVICE_ENV_MAP.items():
        env_val = ""
        for name in env_names:
            env_val = _env(name)
            if env_val:
                break
        if not env_val:
            continue

        row = db.query(SystemConfig).filter(SystemConfig.config_key == config_key).first()
        if row is None:
            print(f"  [{config_key}] not found in DB — skipped")
            continue
        if row.config_value is not None and row.config_value.strip():
            print(f"  [{config_key}] already has DB value — skipped")
            continue

        row.config_value = env_val
        row.updated_by = "env_migration"
        updated += 1
        print(f"  [{config_key}] ← {env_names[0]}={env_val[:40]}{'...' if len(env_val) > 40 else ''}")

    db.commit()
    db.close()
    print(f"\nService configs: {updated} value(s) migrated from .env.")


if __name__ == "__main__":
    main()
    print("\n--- Migrating service configs ---")
    migrate_service_configs()
