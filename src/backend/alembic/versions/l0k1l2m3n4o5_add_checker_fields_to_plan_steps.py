"""add checker fields to plan_steps: step_goal and check_result

Revision ID: l0k1l2m3n4o5
Revises: k1l2m3n4o5p6
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'l0k1l2m3n4o5'
down_revision: Union[str, Sequence[str], None] = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'plan_steps',
        sa.Column('step_goal', sa.Text(), nullable=True),
    )
    op.add_column(
        'plan_steps',
        sa.Column(
            'check_result',
            sa.JSON(),  # 修改点：直接使用 sa.JSON()，去掉 .with_variant(...)
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('plan_steps', 'check_result')
    op.drop_column('plan_steps', 'step_goal')