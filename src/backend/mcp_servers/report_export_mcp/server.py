#!/usr/bin/env python3
"""stdio MCP server exposing tools: export_report_to_docx, export_table_to_excel."""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from typing import Any, Dict, Optional

from mcp.server import FastMCP

mcp = FastMCP("jingxin-report-export")


@mcp.tool()
async def export_report_to_docx(
    markdown: str,
    title: str = "报告",
    filename: Optional[str] = None,
    language: str = "zh",
) -> Dict[str, Any]:
    """
    ⚡ Lightweight export: convert an EXISTING Markdown string into a .docx download artifact.

    USE WHEN: the user wants to download a Markdown report (already generated in this chat)
              as a Word file. Headings use 方正小标宋简体, body uses 方正仿宋简体 (公文字体).
              Typical requests: "把刚才的分析导出为 Word"、"生成这份报告的 docx 下载"。

    DO NOT USE WHEN: the user needs custom styles, multi-section layout, headers/footers,
                    TOC, image insertion, template fill, or editing of an existing .docx.
                    → Use the skill instead (more powerful, template-aware).

    Args:
        markdown: The Markdown source text (required).
        title:    Document title shown as the top heading. Default "报告".
        filename: Optional output filename. Auto-generated if omitted.
        language: "zh" (default) or "en" — affects font selection.

    Returns: {"ok": true, "file_id": "...", "url": "/files/...", "name": "xxx.docx",
              "size": 12345, "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    """
    from mcp_servers.report_export_mcp.impl import export_report_to_docx as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(markdown=markdown, title=title, filename=filename, language=language)

    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)

    if isinstance(result, dict):
        return result
    return {"ok": False, "error": "unexpected export result"}


@mcp.tool()
async def export_table_to_excel(
    markdown: str,
    title: str = "表格",
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    ⚡ Lightweight export: parse Markdown table(s) and convert into an Excel (.xlsx) download.

    USE WHEN: the user has Markdown tables (standard `| col | col |` format, already generated
              in this chat) and wants a quick Excel download. Headers auto-detected from the
              row preceding `|---|---|`. Basic styling applied (header row, alternating rows,
              borders). Each Markdown table becomes one sheet.
              Typical requests: "把这张表下载为 Excel"、"导出上面的表格为 xlsx"。

    DO NOT USE WHEN: the user needs formulas, cross-sheet references, multi-sheet financial
                    models, pivot tables, role-based styling (input/formula/header coloring),
                    formula validation/repair, or editing an existing .xlsx.
                    → Use the skill instead (Formula-First, full pipeline support).

    Args:
        markdown: Markdown text containing one or more tables (required).
        title:    Default sheet title if a table has no heading. Default "表格".
        filename: Optional output filename. Auto-generated if omitted.

    Returns: {"ok": true, "name": "xxx.xlsx",
              "size": 12345, "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "sheet_count": 1, "note": "表格已生成，下载信息由系统在附件区处理"}

    Example input (markdown):
        | 月份 | 销量 | 利润 |
        |------|------|------|
        | 1月  | 100  | 30   |
        | 2月  | 150  | 45   |
    """
    from mcp_servers.report_export_mcp.impl import export_table_to_excel as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(markdown=markdown, title=title, filename=filename)

    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)

    if isinstance(result, dict):
        if result.get("ok"):
            payload = dict(result)
            payload.setdefault("note", "表格已生成，可在附件区查看或下载")
            return payload
        return result
    return {"ok": False, "error": "unexpected export result"}


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
