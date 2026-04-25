"""add schedule_type column to scheduled_tasks

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-04-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i9j0k1l2m3n4'
down_revision: Union[str, Sequence[str], None] = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add column with default 'recurring' so the NOT NULL constraint is satisfied for existing rows
    op.add_column(
        'scheduled_tasks',
        sa.Column(
            'schedule_type',
            sa.String(20),
            nullable=False,
            server_default='recurring',
        ),
    )
    # 2) Backfill: recurring=false rows become 'once' (their historical semantics)
    op.execute(
        "UPDATE scheduled_tasks SET schedule_type = 'once' WHERE recurring = FALSE"
    )
    # 3) Check constraint
    op.create_check_constraint(
        'scheduled_tasks_schedule_type_check',
        'scheduled_tasks',
        "schedule_type IN ('recurring', 'once', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint('scheduled_tasks_schedule_type_check', 'scheduled_tasks', type_='check')
    op.drop_column('scheduled_tasks', 'schedule_type')
