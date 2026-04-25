"""add admin_prompt_parts table

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-03-20

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'admin_prompt_parts',
        sa.Column('part_id', sa.String(100), primary_key=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
    )
    op.create_index('idx_admin_prompt_parts_sort_order', 'admin_prompt_parts', ['sort_order'])
    op.create_index('idx_admin_prompt_parts_is_enabled', 'admin_prompt_parts', ['is_enabled'])


def downgrade() -> None:
    op.drop_index('idx_admin_prompt_parts_is_enabled', table_name='admin_prompt_parts')
    op.drop_index('idx_admin_prompt_parts_sort_order', table_name='admin_prompt_parts')
    op.drop_table('admin_prompt_parts')
