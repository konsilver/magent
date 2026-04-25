"""Extract tool descriptions from MCP server modules to auto-generate detail fields.

This module dynamically extracts docstrings from @mcp.tool() decorated functions
in server.py files, avoiding duplicate maintenance of tool descriptions.
"""

from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


def _extract_tool_info_from_ast(server_path: Path) -> List[Dict[str, str | bool]]:
    """Extract tool function names and docstrings from a server.py file using AST.

    Args:
        server_path: Path to the server.py file

    Returns:
        List of dicts with keys: 'name', 'docstring', 'has_decorator_description'
    """
    try:
        source = server_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        _LOGGER.warning(f"Failed to parse {server_path}: {e}")
        return []

    tools: List[Dict[str, str | bool]] = []

    for node in ast.walk(tree):
        # Look for async function definitions
        if not isinstance(node, ast.AsyncFunctionDef):
            continue

        # Check if decorated with @mcp.tool()
        has_mcp_tool_decorator = False
        has_decorator_description = False
        for decorator in node.decorator_list:
            # Handle @mcp.tool() or @mcp.tool
            if isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr == "tool":
                        has_mcp_tool_decorator = True
                        for kw in decorator.keywords:
                            if kw.arg == "description":
                                has_decorator_description = True
                                break
                        break
            elif isinstance(decorator, ast.Attribute):
                if decorator.attr == "tool":
                    has_mcp_tool_decorator = True
                    break

        if not has_mcp_tool_decorator:
            continue

        # Extract function name and docstring
        func_name = node.name
        docstring = ast.get_docstring(node) or ""

        if docstring:
            tools.append(
                {
                    "name": func_name,
                    "docstring": docstring.strip(),
                    "has_decorator_description": has_decorator_description,
                }
            )

    return tools


def _format_tools_as_markdown(server_id: str, tools: List[Dict[str, str | bool]]) -> str:
    """Format extracted tools into a Markdown detail string.

    Args:
        server_id: MCP server identifier (e.g., 'query_database')
        tools: List of tool info dicts with 'name' and 'docstring'

    Returns:
        Formatted Markdown string
    """
    if not tools:
        return f"### {server_id}\n\nMCP 服务工具"

    # Build markdown sections
    sections = []

    # Header
    sections.append(f"### {server_id}")
    sections.append("")

    # Tools section
    if len(tools) == 1:
        # Single tool - use its docstring directly
        sections.append(tools[0]["docstring"])
    else:
        # Multiple tools - list them
        sections.append("**包含工具**")
        sections.append("")
        for tool in tools:
            sections.append(f"#### `{tool['name']}`")
            sections.append("")
            sections.append(tool["docstring"])
            sections.append("")

    return "\n".join(sections)


def _extract_tool_info_from_runtime(module_path: str) -> List[Dict[str, str | bool]]:
    """Extract tool names and descriptions from FastMCP runtime objects."""
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        _LOGGER.warning(f"Failed to import module for runtime MCP detail extraction: {module_path}: {e}")
        return []

    mcp = getattr(mod, "mcp", None)
    if mcp is None:
        return []

    tool_manager = getattr(mcp, "_tool_manager", None)
    tools_map = getattr(tool_manager, "_tools", None) if tool_manager is not None else None
    if not isinstance(tools_map, dict):
        return []

    out: List[Dict[str, str | bool]] = []
    for tool_name, tool_obj in tools_map.items():
        name = str(tool_name or "").strip()
        description = str(getattr(tool_obj, "description", "") or "").strip()
        if not name or not description:
            continue
        out.append(
            {
                "name": name,
                "docstring": description,
                "has_decorator_description": True,
            }
        )
    return out


def extract_mcp_server_detail(server_id: str, server_module_path: Optional[str] = None) -> str:
    """Extract detail description for an MCP server from its server.py file.

    Args:
        server_id: MCP server identifier (e.g., 'query_database', 'ai_chain_information_mcp')
        server_module_path: Optional module path (e.g., 'mcp_servers.query_database_mcp.server')
                           If not provided, will derive from server_id

    Returns:
        Markdown-formatted detail string describing the server and its tools
    """
    # Derive module path if not provided
    if not server_module_path:
        # Convert server_id to module path
        # e.g., query_database -> mcp_servers.query_database_mcp.server
        # e.g., ai_chain_information_mcp -> mcp_servers.ai_chain_information_mcp.server
        # Handle special cases where server_id already ends with _mcp
        if server_id.endswith("_mcp"):
            module_path = f"mcp_servers.{server_id}.server"
        else:
            module_path = f"mcp_servers.{server_id}_mcp.server"
    else:
        module_path = server_module_path

    # Convert module path to file path
    # e.g., mcp_servers.query_database_mcp.server -> mcp_servers/query_database_mcp/server.py
    try:
        parts = module_path.split(".")
        # Find project root (where mcp_servers directory exists)
        current = Path(__file__).parent.parent  # Go up from configs/
        server_file = current / "/".join(parts[:-1]) / f"{parts[-1]}.py"

        if not server_file.exists():
            _LOGGER.warning(f"Server file not found: {server_file}")
            return f"### {server_id}\n\nMCP 服务工具"

        # Extract tools (AST by default)
        tools = _extract_tool_info_from_ast(server_file)

        # If tool decorator carries explicit description, prefer runtime extraction,
        # so dynamic descriptions (e.g. runtime-injected KB IDs) are preserved.
        use_runtime = any(bool(t.get("has_decorator_description")) for t in tools)
        if use_runtime:
            runtime_tools = _extract_tool_info_from_runtime(module_path)
            if runtime_tools:
                tools = runtime_tools

        # Format as markdown
        return _format_tools_as_markdown(server_id, tools)

    except Exception as e:
        _LOGGER.warning(f"Failed to extract detail for {server_id}: {e}")
        return f"### {server_id}\n\nMCP 服务工具"


def get_all_mcp_details(mcp_servers: Dict[str, dict]) -> Dict[str, str]:
    """Extract details for all MCP servers.

    Args:
        mcp_servers: MCP_SERVERS dict from mcp_config

    Returns:
        Dict mapping server_id to detail markdown string
    """
    details = {}
    for server_id in mcp_servers.keys():
        details[server_id] = extract_mcp_server_detail(server_id)
    return details
