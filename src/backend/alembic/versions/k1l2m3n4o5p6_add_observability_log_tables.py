"""add tool_call_logs / subagent_call_logs / skill_call_logs

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'k1l2m3n4o5p6'
down_revision: Union[str, Sequence[str], None] = 'j0k1l2m3n4o5'
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # ─── tool_call_logs ──────────────────────────────────────────
    op.create_table(
        "tool_call_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("chat_id", sa.String(64)),
        sa.Column("message_id", sa.String(64)),
        sa.Column("user_id", sa.String(64)),
        sa.Column("user_name", sa.String(255)),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_display_name", sa.String(255)),
        sa.Column("tool_call_id", sa.String(64)),
        sa.Column("mcp_server", sa.String(64)),
        sa.Column("tool_args", _json_type()),
        sa.Column("tool_result", _json_type()),
        sa.Column("result_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("source", sa.String(20), nullable=False, server_default="main_agent"),
        sa.Column("subagent_log_id", sa.String(64)),
        sa.Column("skill_log_id", sa.String(64)),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('success', 'failed', 'timeout')", name="tool_call_logs_status_check"),
        sa.CheckConstraint(
            "source IN ('main_agent', 'subagent', 'skill', 'automation')",
            name="tool_call_logs_source_check",
        ),
    )
    op.create_index("idx_tool_call_logs_created_at", "tool_call_logs", ["created_at"])
    op.create_index("idx_tool_call_logs_user_created", "tool_call_logs", ["user_id", "created_at"])
    op.create_index("idx_tool_call_logs_chat_created", "tool_call_logs", ["chat_id", "created_at"])
    op.create_index("idx_tool_call_logs_tool_name", "tool_call_logs", ["tool_name", "created_at"])
    op.create_index("idx_tool_call_logs_status", "tool_call_logs", ["status", "created_at"])
    op.create_index("idx_tool_call_logs_trace_id", "tool_call_logs", ["trace_id"])
    op.create_index("idx_tool_call_logs_subagent", "tool_call_logs", ["subagent_log_id"])
    op.create_index("idx_tool_call_logs_skill", "tool_call_logs", ["skill_log_id"])

    # ─── subagent_call_logs ──────────────────────────────────────
    op.create_table(
        "subagent_call_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("chat_id", sa.String(64)),
        sa.Column("message_id", sa.String(64)),
        sa.Column("user_id", sa.String(64)),
        sa.Column("user_name", sa.String(255)),
        sa.Column("subagent_id", sa.String(64)),
        sa.Column("subagent_name", sa.String(128), nullable=False),
        sa.Column("subagent_type", sa.String(32)),
        sa.Column("plan_id", sa.String(64)),
        sa.Column("step_id", sa.String(64)),
        sa.Column("step_index", sa.Integer()),
        sa.Column("step_title", sa.String(500)),
        sa.Column("model", sa.String(128)),
        sa.Column("input_messages", _json_type()),
        sa.Column("output_content", sa.Text()),
        sa.Column("intermediate_steps", _json_type()),
        sa.Column("token_usage", _json_type()),
        sa.Column("tool_calls_count", sa.Integer(), server_default="0"),
        sa.Column("skill_calls_count", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("parent_subagent_log_id", sa.String(64)),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'cancelled')",
            name="subagent_call_logs_status_check",
        ),
    )
    op.create_index("idx_subagent_logs_created_at", "subagent_call_logs", ["created_at"])
    op.create_index("idx_subagent_logs_user_created", "subagent_call_logs", ["user_id", "created_at"])
    op.create_index("idx_subagent_logs_chat_created", "subagent_call_logs", ["chat_id", "created_at"])
    op.create_index("idx_subagent_logs_subagent_name", "subagent_call_logs", ["subagent_name", "created_at"])
    op.create_index("idx_subagent_logs_status", "subagent_call_logs", ["status", "created_at"])
    op.create_index("idx_subagent_logs_plan_id", "subagent_call_logs", ["plan_id"])
    op.create_index("idx_subagent_logs_trace_id", "subagent_call_logs", ["trace_id"])
    op.create_index("idx_subagent_logs_parent", "subagent_call_logs", ["parent_subagent_log_id"])

    # ─── skill_call_logs ─────────────────────────────────────────
    op.create_table(
        "skill_call_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("chat_id", sa.String(64)),
        sa.Column("message_id", sa.String(64)),
        sa.Column("user_id", sa.String(64)),
        sa.Column("user_name", sa.String(255)),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("skill_name", sa.String(255)),
        sa.Column("skill_version", sa.String(50)),
        sa.Column("skill_source", sa.String(20)),
        sa.Column("invocation_type", sa.String(20), nullable=False, server_default="auto_load"),
        sa.Column("script_name", sa.String(255)),
        sa.Column("script_language", sa.String(32)),
        sa.Column("script_args", _json_type()),
        sa.Column("script_stdin", sa.Text()),
        sa.Column("script_stdout", sa.Text()),
        sa.Column("script_stderr", sa.Text()),
        sa.Column("output_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("source", sa.String(20), nullable=False, server_default="main_agent"),
        sa.Column("subagent_log_id", sa.String(64)),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "invocation_type IN ('view', 'run_script', 'auto_load')",
            name="skill_call_logs_invocation_check",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed', 'timeout')",
            name="skill_call_logs_status_check",
        ),
        sa.CheckConstraint(
            "source IN ('main_agent', 'subagent', 'automation')",
            name="skill_call_logs_source_check",
        ),
    )
    op.create_index("idx_skill_call_logs_created_at", "skill_call_logs", ["created_at"])
    op.create_index("idx_skill_call_logs_user_created", "skill_call_logs", ["user_id", "created_at"])
    op.create_index("idx_skill_call_logs_chat_created", "skill_call_logs", ["chat_id", "created_at"])
    op.create_index("idx_skill_call_logs_skill_name", "skill_call_logs", ["skill_name", "created_at"])
    op.create_index("idx_skill_call_logs_invocation", "skill_call_logs", ["invocation_type", "created_at"])
    op.create_index("idx_skill_call_logs_status", "skill_call_logs", ["status", "created_at"])
    op.create_index("idx_skill_call_logs_trace_id", "skill_call_logs", ["trace_id"])
    op.create_index("idx_skill_call_logs_subagent", "skill_call_logs", ["subagent_log_id"])


def downgrade() -> None:
    op.drop_table("skill_call_logs")
    op.drop_table("subagent_call_logs")
    op.drop_table("tool_call_logs")
