"""add constraint fields to plan_steps: local_constraint and next_step_instruction

Revision ID: n5o6p7q8r9s0
Revises: m1n2o3p4q5r6
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'n5o6p7q8r9s0'
down_revision: Union[str, Sequence[str], None] = 'm1n2o3p4q5r6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'plan_steps',
        sa.Column('local_constraint', sa.JSON(), nullable=True),
    )
    op.add_column(
        'plan_steps',
        sa.Column('next_step_instruction', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('plan_steps', 'next_step_instruction')
    op.drop_column('plan_steps', 'local_constraint')
