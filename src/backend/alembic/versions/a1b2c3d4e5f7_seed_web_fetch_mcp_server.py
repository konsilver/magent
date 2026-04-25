"""seed web_fetch mcp server

Revision ID: a1b2c3d4e5f7
Revises: eea1fd495093
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'eea1fd495093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMON_ENV_KEYS = ["PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ"]


def upgrade() -> None:
    env_inherit = '["PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ"]'
    op.execute(sa.text(f"""
        INSERT INTO admin_mcp_servers
            (server_id, display_name, description, transport, command, args,
             env_vars, env_inherit, headers, is_stable, is_enabled, sort_order, extra_config)
        VALUES
            ('web_fetch', '网站信息抓取',
             '抓取指定网页 URL 的内容，提取正文文本或 Markdown，支持搜索引擎结果页解析。',
             'stdio', 'python', '["-m", "mcp_servers.web_fetch_mcp.server"]',
             '{{}}'::jsonb, '{env_inherit}'::jsonb, '{{}}'::jsonb, true, true, 6, '{{}}'::jsonb)
        ON CONFLICT (server_id) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM admin_mcp_servers WHERE server_id = 'web_fetch'")
    )
