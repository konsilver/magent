"""Seed model providers and role assignments from my_design_doc/model_config.json

Revision ID: o6p7q8r9s0t1
Revises: n5o6p7q8r9s0
Create Date: 2026-05-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'o6p7q8r9s0t1'
down_revision: Union[str, Sequence[str], None] = 'n5o6p7q8r9s0'
branch_labels = None
depends_on = None


# Provider data
_PROVIDERS = [
    {
        "provider_id": "7e20bf3b-b503-47f1-be0f-b4c65dccd78a",
        "display_name": "GLM-5 (DeepSeekR1)",
        "provider_type": "chat",
        "base_url": "http://121.41.45.124:10025/v1",
        "api_key": "sk-GKZEn9QR5t0OZHPRB8FfF3AaA95f4aAc812b73B074E79492",
        "model_name": "deepseekr1",
        "extra_config": {"max_tokens": 8192, "temperature": 0.6},
        "is_active": True,
    },
    {
        "provider_id": "c1386b7e-27ed-43f4-949f-b09a5f8b9f2d",
        "display_name": "Qwen3-Next-80B",
        "provider_type": "chat",
        "base_url": "http://101.37.174.109:3001/v1",
        "api_key": "sk-GKZEn9QR5t0OZHPRB8FfF3AaA95f4aAc812b73B074E79492",
        "model_name": "qwen3_80b",
        "extra_config": {"max_tokens": 8192, "temperature": 0.6},
        "is_active": True,
    },
    {
        "provider_id": "e918986c-205c-4d69-8c34-a7d014b7a5f4",
        "display_name": "Qwen3-Embedding-8B",
        "provider_type": "embedding",
        "base_url": "http://101.37.174.109:3001/v1",
        "api_key": "sk-GKZEn9QR5t0OZHPRB8FfF3AaA95f4aAc812b73B074E79492",
        "model_name": "qwen3_embedding_8b",
        "extra_config": {"dimensions": 4096},
        "is_active": True,
    },
    {
        "provider_id": "340a36f2-00ad-42e4-8919-9104a3eaea13",
        "display_name": "Qwen3-Reranker-8B",
        "provider_type": "reranker",
        "base_url": "http://172.25.204.118:10054/v1",
        "api_key": "gpustack_37d24098df1fdd9d_9bc5fc446021f2a5e9b9972148fadec4",
        "model_name": "qwen3_reranker_8b",
        "extra_config": {},
        "is_active": True,
    },
    {
        "provider_id": "15a25882-f403-4dc6-8138-dca6eb365315",
        "display_name": "Qwen3.5-122B",
        "provider_type": "chat",
        "base_url": "http://47.96.14.202:1029/v1",
        "api_key": "jingxin-qwen-122b",
        "model_name": "qwen3.5-122b",
        "extra_config": {"temperature": 0.6},
        "is_active": True,
    },
]

# Role assignment strategy:
#   main_agent  → deepseekr1  (deep reasoning for tool-use ReAct loop)
#   plan_agent  → deepseekr1  (plan generation benefits from reasoning)
#   memory      → qwen3_80b   (fast structured extraction, no heavy reasoning)
#   summarizer  → qwen3_80b   (fast title/classification)
#   followup    → qwen3_80b   (follow-up generation is lightweight)
#   chart       → qwen3.5-122b (code generation benefits from large model)
#   code_exec   → qwen3.5-122b (code execution reasoning)
#   embedding   → qwen3_embedding_8b
#   reranker    → qwen3_reranker_8b
_ROLE_ASSIGNMENTS = [
    {"role_key": "main_agent",  "provider_id": "7e20bf3b-b503-47f1-be0f-b4c65dccd78a"},
    {"role_key": "plan_agent",  "provider_id": "7e20bf3b-b503-47f1-be0f-b4c65dccd78a"},
    {"role_key": "memory",      "provider_id": "c1386b7e-27ed-43f4-949f-b09a5f8b9f2d"},
    {"role_key": "summarizer",  "provider_id": "c1386b7e-27ed-43f4-949f-b09a5f8b9f2d"},
    {"role_key": "followup",    "provider_id": "c1386b7e-27ed-43f4-949f-b09a5f8b9f2d"},
    {"role_key": "chart",       "provider_id": "15a25882-f403-4dc6-8138-dca6eb365315"},
    {"role_key": "code_exec",   "provider_id": "15a25882-f403-4dc6-8138-dca6eb365315"},
    {"role_key": "embedding",   "provider_id": "e918986c-205c-4d69-8c34-a7d014b7a5f4"},
    {"role_key": "reranker",    "provider_id": "340a36f2-00ad-42e4-8919-9104a3eaea13"},
]


def upgrade() -> None:
    import json
    conn = op.get_bind()

    for p in _PROVIDERS:
        conn.execute(
            sa.text(
                """
                INSERT INTO model_providers
                    (provider_id, display_name, provider_type, base_url, api_key,
                     model_name, extra_config, is_active)
                VALUES
                    (:provider_id, :display_name, :provider_type, :base_url, :api_key,
                     :model_name, :extra_config, :is_active)
                ON CONFLICT (provider_id) DO UPDATE SET
                    display_name  = EXCLUDED.display_name,
                    base_url      = EXCLUDED.base_url,
                    api_key       = EXCLUDED.api_key,
                    model_name    = EXCLUDED.model_name,
                    extra_config  = EXCLUDED.extra_config,
                    is_active     = EXCLUDED.is_active,
                    updated_at    = now()
                """
            ),
            {
                "provider_id": p["provider_id"],
                "display_name": p["display_name"],
                "provider_type": p["provider_type"],
                "base_url": p["base_url"],
                "api_key": p["api_key"],
                "model_name": p["model_name"],
                "extra_config": json.dumps(p["extra_config"]),
                "is_active": p["is_active"],
            },
        )

    for r in _ROLE_ASSIGNMENTS:
        conn.execute(
            sa.text(
                """
                INSERT INTO model_role_assignments (role_key, provider_id, updated_by)
                VALUES (:role_key, :provider_id, 'seed_migration')
                ON CONFLICT (role_key) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    updated_by  = 'seed_migration',
                    updated_at  = now()
                """
            ),
            r,
        )


def downgrade() -> None:
    conn = op.get_bind()
    for r in _ROLE_ASSIGNMENTS:
        conn.execute(
            sa.text("DELETE FROM model_role_assignments WHERE role_key = :role_key"),
            {"role_key": r["role_key"]},
        )
    for p in _PROVIDERS:
        conn.execute(
            sa.text("DELETE FROM model_providers WHERE provider_id = :provider_id"),
            {"provider_id": p["provider_id"]},
        )
