"""Add system_configs table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-13 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Pre-defined service configuration items to seed on first migration.
_SEED_CONFIGS = [
    # ── knowledge_base ──
    ("knowledge_base.provider", "dify", "知识库后端", "知识库服务提供方", "knowledge_base", False),
    ("knowledge_base.url", None, "知识库 API URL", "Dify 或其他知识库服务地址", "knowledge_base", False),
    ("knowledge_base.api_key", None, "知识库 API Key", "知识库服务鉴权密钥", "knowledge_base", True),
    ("knowledge_base.allowed_dataset_ids", None, "允许的数据集 ID", "逗号分隔的数据集 ID 白名单，为空则全部允许", "knowledge_base", False),
    ("knowledge_base.detail_max_chars", "50000", "详情最大字符数", "知识库文档详情最大字符数", "knowledge_base", False),
    # ── industry ──
    ("industry.url", None, "产业知识中心 URL", "产业链信息接口地址", "industry", False),
    ("industry.auth_token", None, "产业知识中心 Token", "产业链信息接口鉴权令牌", "industry", True),
    # ── file_parser ──
    ("file_parser.api_url", None, "文件解析 API URL", "PDF/文档解析服务地址", "file_parser", False),
    ("file_parser.timeout", "60", "超时时间(秒)", "文件解析请求超时", "file_parser", False),
    ("file_parser.lang_list", "ch", "语言列表", "OCR 语言列表", "file_parser", False),
    ("file_parser.backend", "pipeline", "解析后端", "解析后端引擎", "file_parser", False),
    ("file_parser.parse_method", "auto", "解析方法", "auto / ocr / txt", "file_parser", False),
    ("file_parser.formula_enable", "true", "启用公式识别", "是否启用公式识别", "file_parser", False),
    ("file_parser.table_enable", "true", "启用表格识别", "是否启用表格识别", "file_parser", False),
    # ── internet_search ──
    ("internet_search.tavily_api_key", None, "Tavily API Key", "Tavily 互联网搜索服务密钥", "internet_search", True),
]


def upgrade() -> None:
    op.create_table(
        'system_configs',
        sa.Column('config_key', sa.String(100), primary_key=True),
        sa.Column('config_value', sa.Text()),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('group_key', sa.String(50), nullable=False),
        sa.Column('is_secret', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_by', sa.String(64)),
    )
    op.create_index('idx_system_configs_group_key', 'system_configs', ['group_key'])

    # Seed rows (config_value NULL means "use .env fallback")
    rows = [
        {
            "config_key": key,
            "config_value": val,
            "display_name": display,
            "description": desc,
            "group_key": group,
            "is_secret": secret,
        }
        for key, val, display, desc, group, secret in _SEED_CONFIGS
    ]
    op.bulk_insert(sa.table(
        'system_configs',
        sa.column('config_key', sa.String),
        sa.column('config_value', sa.Text),
        sa.column('display_name', sa.String),
        sa.column('description', sa.Text),
        sa.column('group_key', sa.String),
        sa.column('is_secret', sa.Boolean),
    ), rows)


def downgrade() -> None:
    op.drop_table('system_configs')
