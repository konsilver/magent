"""MCP server configuration for Jingxin-Agent.

This module defines how to connect to stdio MCP servers.
Tools are loaded via AgentScope's StdIOStatefulClient.

Detail descriptions are automatically extracted from each server's docstrings.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

# Re-export display-name dicts from canonical module for backwards compatibility
from configs.display_names import (  # noqa: F401
    MCP_SERVER_DESCRIPTIONS,
    MCP_SERVER_DISPLAY_NAMES,
    TOOL_DISPLAY_NAMES,
)

def _kb_mcp_http_url() -> str:
    from core.config.settings import settings
    return settings.server.kb_mcp_http_url


_COMMON_STDIO_ENV_KEYS = (
    "PATH",
    "PYTHONPATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
)

_ARTIFACT_STORAGE_ENV_KEYS = (
    "STORAGE_TYPE",
    "STORAGE_PATH",
    "OSS_ENDPOINT",
    "OSS_BUCKET",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_KEY_PREFIX",
    "S3_BUCKET",
    "S3_REGION",
    "S3_ENDPOINT",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
)


def _stdio_env(*extra_keys: str) -> dict[str, str]:
    keys = set(_COMMON_STDIO_ENV_KEYS)
    keys.update([k for k in extra_keys if isinstance(k, str) and k.strip()])
    env: dict[str, str] = {}
    for key in keys:
        value = os.getenv(key)
        if isinstance(value, str):
            env[key] = value
    return env


MCP_SERVERS: Dict[str, dict] = {
    # one-tool-per-server (stdio)
    "retrieve_dataset_content": {
        "transport": "streamable_http",
        "url": _kb_mcp_http_url(),
        # env is only used to start the background HTTP server process;
        # per-request params (user_id, allowed IDs) are sent via HTTP headers.
        "env": _stdio_env(
            "MILVUS_URL",
            "MILVUS_TOKEN",
            "DATABASE_URL",
        ),
    },
    "internet_search": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.internet_search_mcp.server"],
        "env": _stdio_env(
            # TAVILY_API_KEY and BAIDU_API_KEY are now injected dynamically
            # by factory.py via SystemConfigService.get_service_env_overlay()
            "INTERNET_SEARCH_ENGINE",
            "INTERNET_SEARCH_CN_ONLY",
            "INTERNET_SEARCH_CN_STRICT",
            "INTERNET_SEARCH_COUNTRY",
            "INTERNET_SEARCH_AUTO_PARAMETERS",
        ),
    },

    # Bundled MCP server (MCP-level pluggability): enable/disable this single entry
    # to add/remove the whole AI/chain/news tool bundle.
    "ai_chain_information_mcp": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.ai_chain_information_mcp.server"],
        # INDUSTRY_URL, INDUSTRY_AUTH_TOKEN are now injected dynamically
        # by factory.py via SystemConfigService.get_service_env_overlay()
        "env": _stdio_env(),
    },

    "web_fetch": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.web_fetch_mcp.server"],
        "env": _stdio_env(),
    },
    "code_execution_mcp": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.code_execution_mcp.server"],
        "env": _stdio_env(
            "SKILL_SCRIPT_RUNNER_URL",
            "SKILL_SCRIPT_ENABLED",
            "CODE_EXEC_TIMEOUT",
        ),
    },
}


def get_mcp_server_with_detail(server_id: str) -> Optional[dict]:
    """Get MCP server config with auto-generated detail field.

    Args:
        server_id: MCP server identifier

    Returns:
        Server config dict with 'detail' field added, or None if not found
    """
    if server_id not in MCP_SERVERS:
        return None

    config = dict(MCP_SERVERS[server_id])

    # Lazy import to avoid circular dependencies
    try:
        from configs.mcp_detail_extractor import extract_mcp_server_detail

        config["detail"] = extract_mcp_server_detail(server_id)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Failed to extract detail for {server_id}: {e}")
        config["detail"] = f"### {server_id}\n\nMCP 服务工具"

    return config


def get_all_mcp_servers_with_details() -> Dict[str, dict]:
    """Get all MCP server configs with auto-generated detail fields.

    Returns:
        Dict mapping server_id to config with 'detail' field
    """
    result = {}
    for server_id in MCP_SERVERS.keys():
        config = get_mcp_server_with_detail(server_id)
        if config:
            result[server_id] = config
    return result
