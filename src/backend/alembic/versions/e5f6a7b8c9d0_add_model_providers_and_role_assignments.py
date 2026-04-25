"""Add model_providers and model_role_assignments tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'model_providers',
        sa.Column('provider_id', sa.String(64), primary_key=True),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('provider_type', sa.String(20), nullable=False),
        sa.Column('base_url', sa.Text(), nullable=False),
        sa.Column('api_key', sa.Text(), nullable=False),
        sa.Column('model_name', sa.String(255), nullable=False),
        sa.Column('extra_config', postgresql.JSONB(), server_default='{}'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_tested_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('last_test_status', sa.String(20)),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()')),
        sa.CheckConstraint(
            "provider_type IN ('chat', 'embedding', 'reranker')",
            name='model_providers_type_check',
        ),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN ('success', 'failure')",
            name='model_providers_test_status_check',
        ),
    )
    op.create_index('idx_model_providers_type', 'model_providers', ['provider_type'])
    op.create_index('idx_model_providers_active', 'model_providers', ['is_active'])

    op.create_table(
        'model_role_assignments',
        sa.Column('role_key', sa.String(50), primary_key=True),
        sa.Column(
            'provider_id',
            sa.String(64),
            sa.ForeignKey('model_providers.provider_id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_by', sa.String(64)),
    )
    op.create_index('idx_model_role_assignments_provider', 'model_role_assignments', ['provider_id'])


def downgrade() -> None:
    op.drop_table('model_role_assignments')
    op.drop_table('model_providers')
