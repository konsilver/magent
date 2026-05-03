"""add admin_mcp_servers table with seed data

Revision ID: f6g7h8i9j0k1
Revises: d4e5f6g7h8i9
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f6g7h8i9j0k1'
down_revision = 'd4e5f6g7h8i9'
branch_labels = None
depends_on = None

# Seed data: existing 7 MCP servers migrated from mcp_config.py
_COMMON_ENV_KEYS = ["PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ"]
_ARTIFACT_STORAGE_KEYS = [
    "STORAGE_TYPE", "STORAGE_PATH",
    "OSS_ENDPOINT", "OSS_BUCKET", "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_KEY_PREFIX",
    "S3_BUCKET", "S3_REGION", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY",
]

SEED_SERVERS = [
    {
        "server_id": "retrieve_dataset_content",
        "display_name": "知识库检索",
        "description": "从公有/私有知识库中语义检索政策文件、产业报告及用户上传文档，支持混合检索与重排序。",
        "transport": "streamable_http",
        "command": None,
        "args": [],
        "url": "http://127.0.0.1:9100/mcp",
        "env_vars": {},
        "env_inherit": _COMMON_ENV_KEYS + ["MILVUS_URL", "MILVUS_TOKEN", "DATABASE_URL"],
        "headers": {},
        "is_stable": False,
        "is_enabled": True,
        "sort_order": 1,
        "extra_config": {},
    },
    {
        "server_id": "internet_search",
        "display_name": "互联网搜索",
        "description": "通过互联网实时搜索公开网页、新闻及财经资讯，作为数据库与知识库之外的信息兜底。",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.internet_search_mcp.server"],
        "url": None,
        "env_vars": {},
        "env_inherit": _COMMON_ENV_KEYS + [
            "INTERNET_SEARCH_CN_ONLY", "INTERNET_SEARCH_CN_STRICT",
            "INTERNET_SEARCH_COUNTRY", "INTERNET_SEARCH_AUTO_PARAMETERS",
        ],
        "headers": {},
        "is_stable": True,
        "is_enabled": True,
        "sort_order": 2,
        "extra_config": {},
    },
    {
        "server_id": "ai_chain_information_mcp",
        "display_name": "产业知识中心查询",
        "description": "获取产业链全景分析报告、核心数据指标、产业动态资讯、AI 领域热点聚合及企业画像查询。",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.ai_chain_information_mcp.server"],
        "url": None,
        "env_vars": {},
        "env_inherit": _COMMON_ENV_KEYS,
        "headers": {},
        "is_stable": True,
        "is_enabled": True,
        "sort_order": 3,
        "extra_config": {},
    },
    {
        "server_id": "web_fetch",
        "display_name": "网站信息抓取",
        "description": "抓取指定网页 URL 的内容，提取正文文本或 Markdown，支持搜索引擎结果页解析。",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.web_fetch_mcp.server"],
        "url": None,
        "env_vars": {},
        "env_inherit": _COMMON_ENV_KEYS,
        "headers": {},
        "is_stable": True,
        "is_enabled": True,
        "sort_order": 6,
        "extra_config": {},
    },
]


def upgrade() -> None:
    admin_mcp_servers = op.create_table(
        'admin_mcp_servers',
        sa.Column('server_id', sa.String(100), primary_key=True),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('transport', sa.String(20), nullable=False, server_default='stdio'),
        sa.Column('command', sa.String(500), nullable=True),
        sa.Column('args', sa.JSON, nullable=True, server_default='[]'),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('env_vars', sa.JSON, nullable=True, server_default='{}'),
        sa.Column('env_inherit', sa.JSON, nullable=True, server_default='[]'),
        sa.Column('headers', sa.JSON, nullable=True, server_default='{}'),
        sa.Column('is_stable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('extra_config', sa.JSON, nullable=True, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.CheckConstraint(
            "transport IN ('stdio', 'streamable_http', 'sse')",
            name='admin_mcp_servers_transport_check',
        ),
    )
    op.create_index('idx_admin_mcp_servers_is_enabled', 'admin_mcp_servers', ['is_enabled'])
    op.create_index('idx_admin_mcp_servers_sort_order', 'admin_mcp_servers', ['sort_order'])

    # Seed existing MCP servers
    op.bulk_insert(admin_mcp_servers, SEED_SERVERS)


def downgrade() -> None:
    op.drop_index('idx_admin_mcp_servers_sort_order', table_name='admin_mcp_servers')
    op.drop_index('idx_admin_mcp_servers_is_enabled', table_name='admin_mcp_servers')
    op.drop_table('admin_mcp_servers')
