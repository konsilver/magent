"""API v1 routes."""

from .chats import router as chats_router
from .users import router as users_router
from .catalog import router as catalog_v1_router
from .kb import router as kb_router
from .audit import router as audit_router
from .summary import router as summary_router
from .classify import router as classify_router
from .config import router as config_router
from .file_parse import router as file_parse_router
from .file_upload import router as file_upload_router
from .content import router as content_router
from .memories import router as memories_router
from .auth import router as auth_router
from .mock_sso import router as mock_sso_router
from .models import router as models_router
from .service_configs import router as service_configs_router
from .admin_skills import router as admin_skills_router
from .admin_prompts import router as admin_prompts_router
from .admin_mcp_servers import router as admin_mcp_servers_router
from .chat_shares import router as chat_shares_router
from .agents import router as agents_router
from .admin_agents import router as admin_agents_router
from .artifacts import router as artifacts_router
from .plans import router as plans_router
from .config_verify import router as config_verify_router
from .admin_usage_logs import router as admin_usage_logs_router
from .admin_billing import router as admin_billing_router
from .admin_chat_history import router as admin_chat_history_router
from .code_execute import router as code_execute_router
from .automations import router as automations_router
from .admin_logs import router as admin_logs_router

__all__ = ["chats_router", "users_router", "catalog_v1_router", "kb_router", "audit_router", "summary_router", "classify_router", "config_router", "file_parse_router", "file_upload_router", "content_router", "memories_router", "auth_router", "mock_sso_router", "models_router", "service_configs_router", "admin_skills_router", "admin_prompts_router", "admin_mcp_servers_router", "chat_shares_router", "agents_router", "admin_agents_router", "artifacts_router", "plans_router", "config_verify_router", "admin_usage_logs_router", "admin_billing_router", "admin_chat_history_router", "code_execute_router", "automations_router", "admin_logs_router"]
