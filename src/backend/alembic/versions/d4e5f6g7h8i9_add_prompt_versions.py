"""add admin_prompt_versions table

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-03-20

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e5f6g7h8i9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'admin_prompt_versions',
        sa.Column('version_id', sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column('part_id', sa.String(100), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
    )
    op.create_index('idx_admin_prompt_versions_part_id', 'admin_prompt_versions', ['part_id'])
    op.create_index('idx_admin_prompt_versions_created_at', 'admin_prompt_versions', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_admin_prompt_versions_created_at', table_name='admin_prompt_versions')
    op.drop_index('idx_admin_prompt_versions_part_id', table_name='admin_prompt_versions')
    op.drop_table('admin_prompt_versions')
