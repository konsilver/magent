"""FastAPI application for Jingxin-Agent.

This module is the slim orchestrator: it creates the FastAPI instance,
wires up middleware / error-handlers / routers, and defines lifecycle
events.  Heavy logic lives in dedicated sub-modules:

- api.middleware.cors          – CORS setup
- api.middleware.logging       – HTTP logging & request-size limit
- api.middleware.error_handler – global exception handlers
- api.health                   – /health, /ready, /live, /metrics endpoints
"""

import os
import sys
from typing import Callable

from fastapi import FastAPI
from dotenv import load_dotenv

from api.routes import (
    files_router,
    chats_router,
    users_router,
    catalog_v1_router,
    kb_router,
    audit_router,
    summary_router,
    classify_router,
    config_router,
    file_parse_router,
    file_upload_router,
    content_router,
    memories_router,
    auth_router,
    mock_sso_router,
    models_router,
    service_configs_router,
    admin_skills_router,
    admin_prompts_router,
    admin_mcp_servers_router,
    chat_shares_router,
    agents_router,
    admin_agents_router,
    artifacts_router,
    plans_router,
    config_verify_router,
    admin_usage_logs_router,
    admin_billing_router,
    admin_chat_history_router,
    code_execute_router,
    automations_router,
    admin_logs_router,
)
from api.middleware.cors import setup_cors
from api.middleware.logging import setup_logging_middleware
from api.middleware.error_handler import setup_error_handlers
from api.health import router as health_router, is_internal_ip  # noqa: F401 – re-export for backward compat
from core.config.settings import settings
from core.infra.logging import get_logger

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Create app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Jingxin Agent API",
    description="Multi-agent system with LangChain and MCP integration",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Middleware & error handlers (order matters – last registered runs first)
# ---------------------------------------------------------------------------

setup_cors(app)
setup_logging_middleware(app)
setup_error_handlers(app)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Root endpoint
@app.get("/", tags=["root"])
async def root():
    """API 根路径 — 返回基本信息和可用端点。"""
    return {
        "service": "Jingxin Agent API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


# Health / monitoring endpoints
app.include_router(health_router)

# Non-v1 routers (file downloads keep legacy path for artifact URL stability)
app.include_router(files_router)

# V1 API routers
app.include_router(chats_router)
app.include_router(users_router)
app.include_router(catalog_v1_router)
app.include_router(kb_router)
app.include_router(audit_router)
app.include_router(summary_router)
app.include_router(classify_router)
app.include_router(config_router)
app.include_router(file_parse_router)
app.include_router(file_upload_router)
app.include_router(content_router)
app.include_router(memories_router)
app.include_router(models_router)
app.include_router(service_configs_router)
app.include_router(admin_skills_router)
app.include_router(admin_prompts_router)
app.include_router(admin_mcp_servers_router)
app.include_router(chat_shares_router)
app.include_router(agents_router)
app.include_router(admin_agents_router)
app.include_router(artifacts_router)
app.include_router(plans_router)
app.include_router(config_verify_router)
app.include_router(admin_usage_logs_router)
app.include_router(admin_billing_router)
app.include_router(admin_chat_history_router)
app.include_router(auth_router)
app.include_router(code_execute_router)
app.include_router(automations_router)
app.include_router(admin_logs_router)

# Mock SSO router (when SSO_LOGIN_MODE=mock)
_sso_login_mode = settings.sso.login_mode
_sso_mock_enabled_legacy = settings.sso.mock_enabled
if _sso_login_mode == "mock" or (not _sso_login_mode and _sso_mock_enabled_legacy):
    app.include_router(mock_sso_router)
    logger.info("mock_sso_router_registered", login_mode=_sso_login_mode or "legacy")

# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


_kb_mcp_http_process = None  # background HTTP MCP server for KB retrieval


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _startup_preload_enabled() -> bool:
    return _env_flag("STARTUP_PRELOAD_ENABLED", True)


def _mcp_warmup_enabled() -> bool:
    return _env_flag("MCP_WARMUP_ENABLED", True)


def _start_kb_mcp_http_server() -> None:
    """Start the retrieve_dataset_content MCP server as a background HTTP process."""
    import subprocess

    global _kb_mcp_http_process

    # Build env: inherit current env + MCP env from DB config service
    from core.config.mcp_service import McpServerConfigService
    kb_cfg = McpServerConfigService.get_instance().get_server("retrieve_dataset_content") or {}
    env = dict(os.environ)
    env.update(kb_cfg.get("env", {}))

    from core.config.settings import settings as _s
    kb_port = str(_s.server.kb_mcp_http_port)
    cmd = [
        sys.executable, "-m",
        "mcp_servers.retrieve_dataset_content_mcp.server",
        "--transport", "streamable-http",
        "--port", kb_port,
    ]
    _kb_mcp_http_process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
    )
    logger.info("[startup] KB MCP HTTP server started (pid=%d, port=%s)", _kb_mcp_http_process.pid, kb_port)


@app.on_event("startup")
async def _startup_ensure_tables():
    """Ensure database tables exist for SQLite environments.

    Alembic migration files use PostgreSQL-specific DDL that cannot run on
    SQLite.  When the entrypoint script is bypassed (e.g. direct ``uvicorn``
    invocation during local development), this handler guarantees that all
    ORM tables are created via ``Base.metadata.create_all()`` which respects
    the dialect-aware type variants.  For PostgreSQL this is a no-op because
    tables are already managed by alembic.
    """
    from core.db.engine import DATABASE_URL, init_db
    if DATABASE_URL.startswith("sqlite://"):
        logger.info("[startup] SQLite detected – ensuring tables via create_all()")
        init_db()


@app.on_event("startup")
async def _startup_preload():
    """Pre-load caches and initialize MCP connection pool at startup."""
    import asyncio

    if not _startup_preload_enabled():
        logger.info("[startup] Preload disabled for current environment")
        return

    # Start the KB HTTP MCP server as a background process
    try:
        _start_kb_mcp_http_server()
    except Exception as exc:
        logger.warning("[startup] KB MCP HTTP server start failed: %s", exc)

    async def _run():
        import time
        start = time.monotonic()

        async def _run_sync(label: str, func: Callable[[], None]) -> None:
            try:
                await asyncio.to_thread(func)
                logger.info("[startup] %s loaded", label)
            except Exception as exc:
                logger.warning("[startup] %s preload failed: %s", label, exc)

        # 1. Pre-load prompt config (mtime-cached, ~0ms after first call)
        from prompts.prompt_config import load_prompt_config
        await _run_sync("Prompt config", load_prompt_config)

        # 2. Pre-load skill metadata
        def _load_skill_metadata() -> None:
            from agent_skills.loader import get_skill_loader
            loader = get_skill_loader()
            meta = loader.load_all_metadata()
            logger.info("[startup] Skill metadata loaded: %d skills", len(meta))
        await _run_sync("Skill metadata", _load_skill_metadata)

        # 3. Initialize MCP connection pool (the big one — 1-7s savings)
        if _mcp_warmup_enabled():
            try:
                from core.llm.agent_factory import warmup_mcp_tools
                await warmup_mcp_tools()
            except Exception as exc:
                logger.warning("[startup] MCP pool initialization failed: %s", exc)
        else:
            logger.info("[startup] MCP pool warmup skipped for current environment")

        # 4. Pre-load DB prompt parts so first chat skips DB query
        from prompts.prompt_runtime import warmup_prompt_cache
        await _run_sync("Prompt cache", warmup_prompt_cache)

        # 5. Build Agent Pool (depends on MCP pool being ready)
        try:
            from core.llm.agent_pool import AgentPool
            await AgentPool.get_instance().initialize()
        except Exception as exc:
            logger.warning("[startup] Agent pool initialization failed: %s", exc)

        elapsed = time.monotonic() - start
        logger.info("[startup] Preload complete in %.2fs", elapsed)

    asyncio.create_task(_run())


@app.on_event("startup")
async def _startup_automation_scheduler():
    """Start the automation scheduler for timed task execution."""
    if not _env_flag("AUTOMATION_ENABLED", True):
        logger.info("[startup] Automation scheduler disabled")
        return
    try:
        from routing.automation_scheduler import AutomationScheduler
        global _automation_scheduler
        _automation_scheduler = AutomationScheduler()
        await _automation_scheduler.start()
    except Exception as exc:
        logger.warning("[startup] Automation scheduler failed to start: %s", exc)


_automation_scheduler = None


@app.on_event("shutdown")
async def _shutdown_pools():
    """Close MCP connection pool, KB HTTP server, automation scheduler, and Redis on shutdown."""
    # Stop automation scheduler
    global _automation_scheduler
    if _automation_scheduler is not None:
        try:
            await _automation_scheduler.stop()
        except Exception as e:
            logger.warning("automation_scheduler_shutdown_error", error=str(e))
    # Terminate KB MCP HTTP background server
    global _kb_mcp_http_process
    if _kb_mcp_http_process is not None:
        try:
            _kb_mcp_http_process.terminate()
            _kb_mcp_http_process.wait(timeout=5)
            logger.info("KB MCP HTTP server terminated (pid=%d)", _kb_mcp_http_process.pid)
        except Exception as e:
            logger.warning("kb_mcp_http_shutdown_error", error=str(e))
            try:
                _kb_mcp_http_process.kill()
            except Exception:
                pass
        _kb_mcp_http_process = None

    try:
        from core.llm.agent_pool import AgentPool
        await AgentPool.get_instance().shutdown()
    except Exception as e:
        logger.warning("agent_pool_shutdown_error", error=str(e))

    try:
        from core.llm.mcp_pool import MCPConnectionPool
        await MCPConnectionPool.get_instance().shutdown()
    except Exception as e:
        logger.warning("mcp_pool_shutdown_error", error=str(e))

    try:
        from core.infra.redis import close_redis
        await close_redis()
    except Exception as e:
        logger.warning("redis_shutdown_error", error=str(e))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    """Main entry point for running the server."""
    import uvicorn

    port = settings.server.port
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
