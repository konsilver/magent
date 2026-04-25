"""add user_agents table

Revision ID: g7h8i9j0k1l2
Revises: c19b17bb0e6c
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = 'g7h8i9j0k1l2'
down_revision = 'c19b17bb0e6c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_agents',
        sa.Column('agent_id', sa.String(64), primary_key=True),
        sa.Column('owner_type', sa.String(10), nullable=False),
        sa.Column('user_id', sa.String(64), sa.ForeignKey('users_shadow.user_id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('avatar', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('system_prompt', sa.Text(), nullable=False, server_default=''),
        sa.Column('welcome_message', sa.Text(), server_default=''),
        sa.Column('suggested_questions', JSONB(), server_default='[]'),
        sa.Column('mcp_server_ids', JSONB(), server_default='[]'),
        sa.Column('skill_ids', JSONB(), server_default='[]'),
        sa.Column('kb_ids', JSONB(), server_default='[]'),
        sa.Column('model_provider_id', sa.String(64), sa.ForeignKey('model_providers.provider_id', ondelete='SET NULL'), nullable=True),
        sa.Column('temperature', sa.Numeric(3, 2), nullable=True),
        sa.Column('max_tokens', sa.Integer(), nullable=True),
        sa.Column('max_iters', sa.Integer(), server_default='10'),
        sa.Column('timeout', sa.Integer(), server_default='120'),
        sa.Column('is_enabled', sa.Boolean(), server_default='true'),
        sa.Column('sort_order', sa.Integer(), server_default='0'),
        sa.Column('extra_config', JSONB(), server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.CheckConstraint("owner_type IN ('admin', 'user')", name='user_agents_owner_type_check'),
    )

    op.create_index('idx_user_agents_owner_type', 'user_agents', ['owner_type'])
    op.create_index('idx_user_agents_user_id', 'user_agents', ['user_id'])
    op.create_index('idx_user_agents_is_enabled', 'user_agents', ['is_enabled'])
    op.create_index('idx_user_agents_sort_order', 'user_agents', ['sort_order'])
    op.create_index('idx_user_agents_updated_at', 'user_agents', ['updated_at'])


def downgrade() -> None:
    op.drop_index('idx_user_agents_updated_at', table_name='user_agents')
    op.drop_index('idx_user_agents_sort_order', table_name='user_agents')
    op.drop_index('idx_user_agents_is_enabled', table_name='user_agents')
    op.drop_index('idx_user_agents_user_id', table_name='user_agents')
    op.drop_index('idx_user_agents_owner_type', table_name='user_agents')
    op.drop_table('user_agents')
