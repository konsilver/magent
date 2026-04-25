"""Implementation for tool: get_latest_ai_news (bundled MCP).

Moved from `mcp_servers/get_latest_ai_news_mcp/impl.py`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

# Import safe stream writer from common utilities
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import safe_stream_writer

from utils.helpers import clean_html

load_dotenv()

def _resolve_industry_config() -> tuple[str, str]:
    """Resolve industry API config from environment variables.

    Env vars are injected by the admin platform via SystemConfigService
    (industry.url → INDUSTRY_URL, industry.auth_token → INDUSTRY_AUTH_TOKEN).
    """
    industry_url = (os.getenv("INDUSTRY_URL") or "").strip().rstrip("/")
    auth_token = (os.getenv("INDUSTRY_AUTH_TOKEN") or "").strip()
    if not industry_url:
        raise RuntimeError("INDUSTRY_URL must be configured (via admin panel or .env)")
    if not auth_token:
        raise RuntimeError("INDUSTRY_AUTH_TOKEN must be configured (via admin panel or .env)")
    return industry_url, auth_token


def get_latest_ai_news() -> List[Dict[str, Any]]:
    industry_url, auth_token = _resolve_industry_config()
    base_url = f"{industry_url}/industry/trends"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    headers["Authorization"] = auth_token

    writer = safe_stream_writer()
    writer("正在获取最近一周的人工智能产业热门动态...\n")

    result_list: List[Dict[str, Any]] = []
    try:
        list_url = f"{base_url}/weekHot"
        list_resp = requests.get(list_url, headers=headers, timeout=10)
        list_resp.raise_for_status()
        list_data = list_resp.json()

        if list_data.get("header", {}).get("code") != 200:
            print(f"列表接口返回错误: {list_data}")
            return []

        items = list_data.get("body", [])
        for item in items:
            news_id = item.get("id")
            title = item.get("title")
            update_time = item.get("update_time")
            if not news_id:
                continue

            detail_url = f"{base_url}/detail"
            params = {"id": news_id}
            try:
                detail_resp = requests.get(detail_url, headers=headers, params=params, timeout=5)
                if detail_resp.status_code == 200:
                    detail_json = detail_resp.json()
                    detail_body = detail_json.get("body", {})
                    raw_abstract = detail_body.get("abstract", "")
                    result_list.append({"时间": update_time, "标题": title, "摘要": clean_html(raw_abstract)})
                else:
                    print(f"详情获取失败 ID {news_id}: {detail_resp.status_code}")
            except Exception as e:
                print(f"获取详情 ID {news_id} 时发生异常: {e}")
                continue

    except requests.exceptions.RequestException as e:
        print(f"请求发生网络错误: {e}")
        return []
    except Exception as e:
        print(f"发生未知错误: {e}")
        return []

    return result_list
