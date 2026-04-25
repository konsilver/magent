"""Implementation for MCP tool: query_database."""

from __future__ import annotations

import json
import os
import re

import requests
from dotenv import load_dotenv

# Import safe stream writer from common utilities
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import safe_stream_writer

load_dotenv()


def _read_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


REQUEST_TIMEOUT_SECONDS = _read_int_env("QUERY_DATABASE_TIMEOUT_SECONDS", 40)
RETRY_TIMES = _read_int_env("QUERY_DATABASE_RETRY_TIMES", 1)  # retry once by default
MAX_OUTPUT_TOKENS = _read_int_env("QUERY_DATABASE_MAX_OUTPUT_TOKENS", 45_000)
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _resolve_query_database_base_url() -> str:
    # Dedicated tool endpoint (preferred)
    for env_name in ("QUERY_DATABASE_URL", "DATABASE_API_URL", "DB_QUERY_API_URL"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value.rstrip("/")

    # Backward compatibility: allow old DATABASE_URL only if it points to an HTTP API.
    legacy = (os.getenv("DATABASE_URL") or "").strip()
    if legacy.startswith("http://") or legacy.startswith("https://"):
        return legacy.rstrip("/")

    return ""


def _estimate_tokens(text: str) -> int:
    """Estimate tokens without external tokenizer dependency."""
    if not text:
        return 0
    cjk_chars = len(CJK_PATTERN.findall(text))
    other_chars = len(text) - cjk_chars
    return cjk_chars + ((other_chars + 3) // 4)


def _truncate_by_token_limit(text: str, token_limit: int) -> tuple[str, bool, int]:
    original_tokens = _estimate_tokens(text)
    if original_tokens <= token_limit:
        return text, False, original_tokens

    suffix = "\n\n... [输出过长，已截断]"
    suffix_tokens = _estimate_tokens(suffix)
    available_tokens = max(token_limit - suffix_tokens, 1)

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _estimate_tokens(text[:mid]) <= available_tokens:
            low = mid
        else:
            high = mid - 1

    truncated = text[:low] + suffix
    while low > 0 and _estimate_tokens(truncated) > token_limit:
        low -= 1
        truncated = text[:low] + suffix
    return truncated, True, original_tokens


def _guard_output(text: str, writer) -> str:
    guarded, was_truncated, original_tokens = _truncate_by_token_limit(text, MAX_OUTPUT_TOKENS)
    if was_truncated:
        writer(
            f"⚠️ query_database 返回内容约 {original_tokens} tokens，"
            f"超过上限 {MAX_OUTPUT_TOKENS}，已截断\n"
        )
    return guarded


def query_database(question: str, empNo: str = "80049875") -> str:
    base_url = _resolve_query_database_base_url()
    if not base_url:
        return (
            "❌ 未配置 query_database 工具服务地址。"
            "请在 .env 或容器环境中设置 QUERY_DATABASE_URL（示例: http://your-database-service:6200）。"
        )

    url = f"{base_url}/query_database"
    writer = safe_stream_writer()
    writer(f"正在通过数据库搜索{question}的结果...\n")

    headers = {"Content-Type": "application/json"}
    payload = {"empNo": empNo, "question": question}
    max_attempts = RETRY_TIMES + 1

    for attempt in range(1, max_attempts + 1):
        resp = None
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 200:
                if data.get("data", []) != []:
                    result_data = data["data"]
                    writer("✅ 数据库查询成功，找到相关数据\n")
                    return _guard_output(
                        f"✅ 查询成功:\n\n{json.dumps(result_data, ensure_ascii=False, indent=2)}",
                        writer,
                    )
                writer("⚠️ 数据库查询完成，但未找到相关数据\n")
                return _guard_output(
                    f"❌ 数据库中暂时没有存在相关数据:{json.dumps(data, ensure_ascii=False, indent=2)}",
                    writer,
                )

            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次调用返回业务异常，准备重试...\n")
                continue

            writer("❌ 数据库返回异常状态码\n")
            return _guard_output(
                f"❌ 数据库返回异常:{json.dumps(data, ensure_ascii=False, indent=2)}",
                writer,
            )

        except requests.exceptions.HTTPError as errh:
            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次数据库HTTP错误，准备重试...\n")
                continue
            detail = ""
            try:
                detail = (resp.text or "")[:500] if resp is not None else ""
            except Exception:
                pass
            writer("❌ 数据库HTTP错误\n")
            return _guard_output(f"❌ HTTP错误: {errh}\n响应内容: {detail}", writer)

        except requests.exceptions.ConnectionError as errc:
            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次连接数据库失败，准备重试...\n")
                continue
            writer("❌ 无法连接到数据库服务\n")
            return _guard_output(
                f"❌ 连接错误: 无法连接到数据库服务 ({url})\n详情: {errc}",
                writer,
            )

        except requests.exceptions.Timeout as errt:
            if attempt < max_attempts:
                writer(
                    f"⚠️ 第{attempt}次数据库查询超时（超过{REQUEST_TIMEOUT_SECONDS}秒），准备重试...\n"
                )
                continue
            writer(f"❌ 数据库查询超时（超过{REQUEST_TIMEOUT_SECONDS}秒）\n")
            return _guard_output(
                f"❌ 请求超时: 数据库查询时间过长（超过{REQUEST_TIMEOUT_SECONDS}秒）\n详情: {errt}",
                writer,
            )

        except requests.exceptions.RequestException as err:
            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次请求失败，准备重试...\n")
                continue
            return _guard_output(f"❌ 请求失败: {err}", writer)

        except json.JSONDecodeError:
            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次响应不是合法JSON，准备重试...\n")
                continue
            try:
                raw = (resp.text or "")[:200] if resp is not None else ""
            except Exception:
                raw = ""
            return _guard_output(f"❌ 响应解析失败: 返回的数据不是有效的JSON格式\n原始响应: {raw}", writer)

        except Exception as err:
            if attempt < max_attempts:
                writer(f"⚠️ 第{attempt}次调用出现未知错误，准备重试...\n")
                continue
            return _guard_output(f"❌ 未知错误: {err}", writer)

    return _guard_output("❌ 请求失败: 重试后仍未成功。", writer)
