"""Implementation for tool: get_industry_news (bundled MCP).

Moved from `mcp_servers/get_industry_news_mcp/impl.py`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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


def get_industry_news(
    keyword: Optional[str] = None,
    news_type: Optional[str] = None,
    chain: Optional[str] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    writer = safe_stream_writer()
    writer(
        f"正在通过宁波产业知识中心获取最新资讯，查询参数keyword: {keyword}, news_type: {news_type}, chain: {chain}, region:{region}...\n"
    )

    industry_url, auth_token = _resolve_industry_config()

    base_url = f"{industry_url}/industry/trends"
    url = f"{base_url}/filtering"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Authorization": auth_token,
    }

    payload = {
        "type": "",
        "pageNum": 1,
        "pageSize": 10,
        "termQueries": {
            "domain_oriented": ["人工智能"],
            "news_type": [news_type] if news_type else [],
            "keyword": [keyword] if keyword else [],
            "chain": [chain] if chain else [],
            "emotional_tendency": [],
            "source_type": [],
            "region": [region] if region else [],
            "date": [],
        },
        "isSubscribe": False,
        "sort": None,
    }

    results: List[Dict[str, Any]] = []
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        resp_json = resp.json()

        data_list = resp_json.get("body", {}).get("data", [])
        for item in data_list:
            tags_list = item.get("tags", [])
            tags_str = ", ".join([t.get("name", "") for t in tags_list if t.get("name")])
            industry_list = item.get("industry", [])
            industry_str = ", ".join(industry_list) if industry_list else ""
            raw_abstract = item.get("abstract", "")

            results.append(
                {
                    "标题": item.get("title", ""),
                    "摘要": clean_html(raw_abstract),
                    "标签": tags_str,
                    "对应产业链": industry_str,
                    "地区": item.get("province", ""),
                    "国家": item.get("country", ""),
                    "城市": item.get("city", ""),
                }
            )

    except requests.exceptions.RequestException as e:
        print(f"请求发生网络错误: {e}")
    except Exception as e:
        print(f"数据处理错误: {e}")

    return results
