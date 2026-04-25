#!/usr/bin/env python3
"""stdio MCP server exposing tool: generate_chart_tool."""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from typing import Any, Dict

from mcp.server import FastMCP

mcp = FastMCP("jingxin-generate-chart-tool")


@mcp.tool()
async def generate_chart_tool(data: str, query: str) -> Dict[str, Any]:
    """根据给定数据生成可视化图表（matplotlib），将图片保存到存储并返回结果摘要。

    适用场景：
    - 用户明确要求：画图/绘图/生成图表（折线图、柱状图、饼图等）。

    调用规范（严禁跳过）：
    - **禁止凭空绘图**：必须先通过数据查询工具获取真实数据。
    - 将数据整理为 JSON 字符串传入 data；在 query 中写清：图表类型、标题、坐标轴、单位换算要求等。

    Args:
        data: 绘图数据（JSON 字符串）。例如：{"年份":[2022,2023],"增加值":[123,145]}。
        query: 绘图指令。例如："画折线图，标题为xxx，单位换算为亿元"。

    Returns:
        dict: {"ok": true, "name": "chart_xxx.png", "size": 12345, "mime_type": "image/png",
               "note": "图表已生成，下载信息由系统在附件区处理"}
              或失败时: {"ok": false, "error": "..."}
    """

    from .chart import generate_chart_tool as _tool

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = await _tool(data=data, query=query)

    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)

    if isinstance(result, dict):
        if result.get("ok"):
            payload = dict(result)
            payload.setdefault("note", "图表已生成，可在附件区查看或下载")
            return payload
        return result
    return {"result": result}


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
