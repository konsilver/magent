"""add admin_skills table

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-19

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'admin_skills',
        sa.Column('skill_id', sa.String(100), primary_key=True),
        sa.Column('skill_content', sa.Text(), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('version', sa.String(50), nullable=False, server_default='1.0.0'),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('allowed_tools', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
    )
    op.create_index('idx_admin_skills_is_enabled', 'admin_skills', ['is_enabled'])
    op.create_index('idx_admin_skills_updated_at', 'admin_skills', ['updated_at'])


def downgrade() -> None:
    op.drop_index('idx_admin_skills_updated_at', table_name='admin_skills')
    op.drop_index('idx_admin_skills_is_enabled', table_name='admin_skills')
    op.drop_table('admin_skills')
