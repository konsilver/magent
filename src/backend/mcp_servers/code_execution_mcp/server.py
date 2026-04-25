#!/usr/bin/env python3
"""stdio MCP server: code_execution_mcp

在安全沙箱中执行代码片段，支持 Python / JavaScript / Bash。
复用 jingxin-script-runner sidecar，与 script_runner.py 使用相同的 HTTP 接口。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from mcp.server import FastMCP

mcp = FastMCP("jingxin-code-execution")

_RUNNER_URL = os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900")
_DEFAULT_TIMEOUT = int(os.getenv("CODE_EXEC_TIMEOUT", "60"))

_LANG_ALIASES: dict[str, str] = {
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "sh": "bash",
    "shell": "bash",
    "py": "python",
}


async def _call_sidecar(payload: dict) -> Dict[str, Any]:
    import httpx

    http_timeout = payload.get("timeout", _DEFAULT_TIMEOUT) + 30
    try:
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resp = await client.post(f"{_RUNNER_URL}/execute", json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "代码执行沙箱不可用，请检查 jingxin-script-runner 容器是否正在运行",
        }
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else str(e)
        return {"exit_code": -1, "stdout": "", "stderr": f"沙箱 HTTP 错误: {body}"}
    except httpx.TimeoutException:
        t = payload.get("timeout", _DEFAULT_TIMEOUT)
        return {"exit_code": -1, "stdout": "", "stderr": f"代码执行超时（{t} 秒）"}


@mcp.tool()
async def execute_code(
    code: str,
    language: str = "python",
    timeout: int = 60,
) -> Dict[str, Any]:
    """在安全沙箱中执行代码片段，返回 stdout、stderr 和 exit_code。

    适用场景：算法验证、函数正确性测试、数据处理脚本验证。
    支持语言：python、javascript、bash。

    Args:
        code: 要执行的完整代码字符串（不需要是函数，直接可运行的脚本）
        language: 编程语言，支持 python / javascript / bash，默认 python
        timeout: 执行超时秒数，默认 60，最大 120

    Returns:
        dict with keys:
            exit_code (int): 0 表示成功，非 0 表示失败
            stdout (str): 标准输出内容
            stderr (str): 标准错误内容（失败时包含错误信息）
    """
    effective_timeout = min(max(1, timeout), 120)
    lang = _LANG_ALIASES.get(language.strip().lower(), language.strip().lower())

    payload = {
        "script_content": code,
        "script_name": f"__inline__.{lang}",
        "language": lang,
        "params": {},
        "timeout": effective_timeout,
    }
    result = await _call_sidecar(payload)
    return {
        "exit_code": result.get("exit_code", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


@mcp.tool()
async def run_command(
    command: str,
    timeout: int = 30,
) -> Dict[str, Any]:
    """在沙箱中执行 shell 命令，返回 stdout、stderr 和 exit_code。

    适用场景：安装 Python 包（pip install）、文件操作、系统工具命令验证。

    Args:
        command: Shell 命令字符串（在 bash 中执行）
        timeout: 执行超时秒数，默认 30，最大 60

    Returns:
        dict with keys:
            exit_code (int): 0 表示成功，非 0 表示失败
            stdout (str): 标准输出内容
            stderr (str): 标准错误内容
    """
    effective_timeout = min(max(1, timeout), 60)
    payload = {
        "script_content": command,
        "script_name": "__inline__.sh",
        "language": "bash",
        "params": {},
        "timeout": effective_timeout,
    }
    result = await _call_sidecar(payload)
    return {
        "exit_code": result.get("exit_code", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
