"""seed code_execution_mcp into admin_mcp_servers

Revision ID: m1n2o3p4q5r6
Revises: l0k1l2m3n4o5
Create Date: 2026-04-20

Adds the code_execution_mcp server as a stable stdio server so the
MCPConnectionPool pre-connects it at startup instead of spawning a new
subprocess on every request (which caused 1-3s latency per request).
"""
from alembic import op
import sqlalchemy as sa

revision = 'm1n2o3p4q5r6'
down_revision = 'l0k1l2m3n4o5'
branch_labels = None
depends_on = None

_COMMON_ENV_KEYS = ["PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ"]


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO admin_mcp_servers
            (server_id, display_name, description, transport, command, args,
             url, env_vars, env_inherit, headers, is_stable, is_enabled, sort_order, extra_config)
        VALUES
            ('code_execution_mcp', '代码执行沙箱',
             '在安全隔离的沙箱容器中执行 Python / JavaScript / Bash 代码片段，返回 stdout、stderr 和退出码，用于代码生成后的自动验证。',
             'stdio', 'python',
             '["-m", "mcp_servers.code_execution_mcp.server"]',
             NULL, '{}',
             '["PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "TZ", "SKILL_SCRIPT_RUNNER_URL", "SKILL_SCRIPT_ENABLED", "CODE_EXEC_TIMEOUT"]',
             '{}', true, true, 7, '{}')
        ON CONFLICT (server_id) DO UPDATE SET
            is_stable = true,
            is_enabled = true,
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            command = EXCLUDED.command,
            args = EXCLUDED.args,
            env_inherit = EXCLUDED.env_inherit,
            sort_order = EXCLUDED.sort_order
    """))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM admin_mcp_servers WHERE server_id = 'code_execution_mcp'"))
