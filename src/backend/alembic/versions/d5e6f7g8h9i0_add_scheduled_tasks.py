"""add scheduled_tasks and scheduled_task_runs tables

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-04-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd5e6f7g8h9i0'
down_revision: Union[str, Sequence[str]] = 'c4d5e6f7g8h9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduled_tasks',
        sa.Column('task_id', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.String(64), sa.ForeignKey('users_shadow.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_type', sa.String(20), nullable=False),
        sa.Column('prompt', sa.Text()),
        sa.Column('plan_id', sa.String(64), sa.ForeignKey('plans.plan_id', ondelete='SET NULL')),
        sa.Column('cron_expression', sa.String(100), nullable=False),
        sa.Column('recurring', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('timezone', sa.String(50), nullable=False, server_default='Asia/Shanghai'),
        sa.Column('enabled_mcp_ids', postgresql.JSONB(), server_default='[]'),
        sa.Column('enabled_skill_ids', postgresql.JSONB(), server_default='[]'),
        sa.Column('enabled_kb_ids', postgresql.JSONB(), server_default='[]'),
        sa.Column('enabled_agent_ids', postgresql.JSONB(), server_default='[]'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('next_run_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('last_run_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('run_count', sa.Integer(), server_default='0'),
        sa.Column('max_runs', sa.Integer()),
        sa.Column('consecutive_failures', sa.Integer(), server_default='0'),
        sa.Column('max_failures', sa.Integer(), server_default='3'),
        sa.Column('last_error', sa.Text()),
        sa.Column('name', sa.String(200)),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('metadata', postgresql.JSONB(), server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("task_type IN ('prompt', 'plan')", name='scheduled_tasks_type_check'),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'disabled', 'completed', 'expired')",
            name='scheduled_tasks_status_check',
        ),
    )
    op.create_index('idx_scheduled_tasks_user_id', 'scheduled_tasks', ['user_id'])
    op.create_index('idx_scheduled_tasks_status', 'scheduled_tasks', ['status'])
    op.create_index('idx_scheduled_tasks_user_status', 'scheduled_tasks', ['user_id', 'status'])

    op.create_table(
        'scheduled_task_runs',
        sa.Column('run_id', sa.String(64), primary_key=True),
        sa.Column('task_id', sa.String(64), sa.ForeignKey('scheduled_tasks.task_id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='running'),
        sa.Column('chat_id', sa.String(64)),
        sa.Column('result_summary', sa.Text()),
        sa.Column('error_message', sa.Text()),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('duration_ms', sa.Integer()),
        sa.Column('usage', postgresql.JSONB(), server_default='{}'),
        sa.CheckConstraint("status IN ('running', 'success', 'failed')", name='scheduled_task_runs_status_check'),
    )
    op.create_index('idx_scheduled_task_runs_task_id', 'scheduled_task_runs', ['task_id'])
    op.create_index('idx_scheduled_task_runs_started_at', 'scheduled_task_runs', ['started_at'])


def downgrade() -> None:
    op.drop_table('scheduled_task_runs')
    op.drop_table('scheduled_tasks')
