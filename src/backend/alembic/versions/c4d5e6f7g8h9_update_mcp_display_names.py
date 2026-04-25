"""update mcp server display names

Revision ID: c4d5e6f7g8h9
Revises: b2c3d4e5f6g8
Create Date: 2026-04-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4d5e6f7g8h9'
down_revision: Union[str, Sequence[str]] = 'b2c3d4e5f6g8'
branch_labels = None
depends_on = None

# (server_id, old_display_name, new_display_name)
_RENAMES = [
    ("ai_chain_information_mcp", "产业链信息", "产业知识中心查询"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for server_id, _, new_name in _RENAMES:
        conn.execute(
            sa.text("UPDATE admin_mcp_servers SET display_name = :name WHERE server_id = :sid"),
            {"name": new_name, "sid": server_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for server_id, old_name, _ in _RENAMES:
        conn.execute(
            sa.text("UPDATE admin_mcp_servers SET display_name = :name WHERE server_id = :sid"),
            {"name": old_name, "sid": server_id},
        )
