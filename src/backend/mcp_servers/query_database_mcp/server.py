#!/usr/bin/env python3
"""stdio MCP server exposing tool: query_database.

Run:
  python -m mcp_servers.query_database_mcp.server

Notes:
- Stdout is reserved for MCP protocol.
- Capture any business logs and forward them to stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from typing import Any, Dict

from mcp.server import FastMCP

mcp = FastMCP("jingxin-query-database")


@mcp.tool()
async def query_database(question: str, empNo: str = "80049875") -> Dict[str, Any]:
    """从数据仓库/数据库查询精确数值(最优先的数据来源).

    适用场景:
    - 用户在问某个行业的某个指标的具体数值(如: 规上工业增加值、增速、利润总额等).
    - 需要可核对的数, 而不是泛泛分析.

    调用规范(必须严格遵守):
    1. 禁止拆分问题: 工具内部已具备问题分解和多表联查能力. 无论用户问题涉及
       多少个指标、行业或时间段, 必须将用户的完整问题作为一个整体传入 question
       参数, 禁止在外部将问题拆分为多次调用.
       - 正确: question="查询2024年宁波规上工业增加值、利润总额及增速" (一次调用)
       - 错误: 先调用 question="查询2024年宁波规上工业增加值",
               再调用 question="查询2024年宁波规上工业利润总额" (拆成多次)
    2. 仅在单次调用明确失败后, 才可考虑缩小查询范围重试.
    3. 先把用户问题改写为数仓里存在的行业/指标名称, 不要自造别名.

    Args:
        question: 用户的完整查询问题(直接传入原始问题, 工具内部会自动分解和转
            SQL, 不要在外部拆分).
        empNo: 员工编号, 默认 "80049875".

    Returns:
        dict: 包含 "result" 键的字典(字符串通常是 JSON pretty-print, 或错误提示).

    Examples:
        - question="查询近3年我市规上工业总产值和增加值情况"
        - question="查询2024年宁波规上工业增加值及增速是多少"
        - question="查询2025年3月宁波市人工智能与机器人产业销售费用是多少"
        - question="对比2023年和2024年宁波市各区县规上工业增加值、利润总额和营业收入"
    """

    from mcp_servers.query_database_mcp.impl import query_database as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(question=question, empNo=empNo)

    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)

    if isinstance(result, dict):
        return result
    return {"result": result}


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
