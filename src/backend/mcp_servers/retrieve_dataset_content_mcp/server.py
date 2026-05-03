#!/usr/bin/env python3
"""MCP server exposing tools: retrieve_dataset_content, list_datasets.

Supports two transports:
- stdio (default): spawned per-request as a subprocess
- streamable-http: long-running HTTP server, runtime params via HTTP headers
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("jingxin-retrieve-dataset-content")
_LOGGER = logging.getLogger(__name__)

# ── Header names for runtime parameters (HTTP mode) ─────────────────────────
_HDR_ALLOWED_DATASET_IDS = "x-allowed-dataset-ids"
_HDR_ALLOWED_KB_IDS = "x-allowed-kb-ids"
_HDR_CURRENT_USER_ID = "x-current-user-id"
_HDR_RERANKER_ENABLED = "x-reranker-enabled"


def _get_header(ctx: Optional[Context], name: str) -> Optional[str]:
    """Extract an HTTP header from the MCP request context.

    Returns None if ctx is unavailable (stdio mode) or the header is absent.
    """
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
        if request is None:
            return None
        value = request.headers.get(name)
        return value if value else None
    except Exception as exc:
        _LOGGER.warning("_get_header(%s) failed: %s (ctx type=%s)", name, exc, type(ctx))
        return None


_BASE_TOOL_DESCRIPTION = """从"知识库/数据集"检索政策文件、报告、非结构化文本片段。默认自动搜索所有可用数据集。

⚠️ 【必须遵守的引用规则】
回答中引用本工具返回的任何内容时，**必须**在引用句末尾加上 `[ref:retrieve_dataset_content-N]` 标记（N 为 items 列表中的序号，从1开始）。
不带引用标记的回答视为不完整，前端将无法展示引用来源卡片。
示例：根据报告，2024年工业增加值增速为5.2%[ref:retrieve_dataset_content-1]。

适用场景（当用户问题涉及以下内容时，应**主动**调用本工具，无需等待用户显式要求）：
- 政策文件原文、解读、申报条件
- 产业分析报告、行业研究、发展规划
- 企业调研材料、项目申报书
- 工业经济运行分析、统计公报等非结构化文本

调用说明：
- **dataset_id 默认留空即可**，系统会自动搜索所有可用数据集并返回最相关的结果。
- 仅当用户明确指定要从某个特定知识库搜索时，才传入对应的 dataset_id。
- 返回的是记录列表；回答时应从每条记录的 `segment -> content` 提取要点。

Args:
    query: 检索 query。
    dataset_id: 数据集 ID（默认为空，自动搜索所有数据集；仅当用户指定特定知识库时才填写）。
    top_k: 返回片段数量。
    score_threshold: 相似度阈值。
    search_method: 检索方式（默认 hybrid_search）。
    reranking_enable: 是否启用重排。
    weights: 混合检索权重。

Returns:
    dict: {"items": [records...]}
"""


def _build_tool_description() -> str:
    return _BASE_TOOL_DESCRIPTION


@mcp.tool(description=_build_tool_description())
async def retrieve_dataset_content(
    query: str,
    dataset_id: str = "",
    top_k: int = 10,
    score_threshold: float = 0.4,
    search_method: str = "hybrid_search",
    reranking_enable: bool = False,
    weights: float = 0.6,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Execute dataset retrieval and return MCP-compatible payload."""

    from mcp_servers.retrieve_dataset_content_mcp.impl import retrieve_dataset_content as _impl

    # Read runtime params from HTTP headers (None in stdio mode → fallback to env)
    allowed_dataset_ids = _get_header(ctx, _HDR_ALLOWED_DATASET_IDS)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        items = _impl(
            query=query,
            dataset_id=dataset_id,
            top_k=top_k,
            score_threshold=score_threshold,
            search_method=search_method,
            reranking_enable=reranking_enable,
            weights=weights,
            allowed_dataset_ids=allowed_dataset_ids,
        )

    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)

    return {"items": items}


# ── List datasets tool ────────────────────────────────────────────────────────

_LIST_DATASETS_DESCRIPTION = """列出当前可用的所有知识库（公有 + 私有），包含每个知识库的名称、简介和文档列表。

适用场景：
- 用户询问"有哪些知识库"、"有什么数据集"、"知识库列表"等。
- 用户想了解可以查询哪些资料来源。
- 在不确定应该查哪个知识库时，先调用本工具查看可用列表，再用 retrieve_dataset_content 进行检索。

Returns:
    dict: {"public_datasets": [...], "private_datasets": [...], "total": N}
    每个知识库包含：id/名称/简介/文档数量/文档标题列表
"""


@mcp.tool(description=_LIST_DATASETS_DESCRIPTION)
async def list_datasets(
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """List all available public and private knowledge bases."""

    from mcp_servers.retrieve_dataset_content_mcp.impl import list_all_datasets as _impl

    allowed_dataset_ids = _get_header(ctx, _HDR_ALLOWED_DATASET_IDS)
    allowed_kb_ids = _get_header(ctx, _HDR_ALLOWED_KB_IDS)
    current_user_id = _get_header(ctx, _HDR_CURRENT_USER_ID)

    return _impl(
        allowed_dataset_ids=allowed_dataset_ids,
        allowed_kb_ids=allowed_kb_ids,
        current_user_id=current_user_id,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="KB retrieval MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9100,
        help="HTTP port (only used with streamable-http transport, default: 9100)",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.settings.port = args.port
        mcp.run("streamable-http")
    else:
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
