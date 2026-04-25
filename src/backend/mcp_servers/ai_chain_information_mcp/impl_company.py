"""Implementation for enterprise profile tools (bundled MCP).

Provides 6 tools based on the Ningbo Knowledge Center Enterprise Profile API:
1. search_company - search companies by keyword
2. get_company_base_info - basic company information
3. get_company_business_analysis - business analysis
4. get_company_tech_insight - technology insight
5. get_company_funding - funding transparency
6. get_company_risk_warning - risk warning
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import safe_stream_writer

load_dotenv()

def _resolve_company_config() -> tuple[str, str]:
    """Resolve company API config from environment variables.

    Env vars are injected by the admin platform via SystemConfigService
    (industry.url → INDUSTRY_URL, industry.auth_token → INDUSTRY_AUTH_TOKEN).
    """
    api_url = (os.getenv("COMPANY_API_URL") or os.getenv("INDUSTRY_URL") or "").strip().rstrip("/")
    auth_token = (os.getenv("COMPANY_AUTH_TOKEN") or os.getenv("INDUSTRY_AUTH_TOKEN") or "").strip()
    if not api_url:
        raise RuntimeError("COMPANY_API_URL or INDUSTRY_URL must be configured (via admin panel or .env)")
    if not auth_token:
        raise RuntimeError("COMPANY_AUTH_TOKEN or INDUSTRY_AUTH_TOKEN must be configured (via admin panel or .env)")
    return api_url, auth_token


def _company_request(path: str, params: dict, timeout: int = 15) -> Optional[Any]:
    api_url, auth_token = _resolve_company_config()
    headers = {
        "Authorization": auth_token,
        "User-Agent": "Mozilla/5.0",
    }
    try:
        resp = requests.get(f"{api_url}{path}", params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("header", {}).get("code") == 200:
            return data.get("body")
        return None
    except Exception as e:
        print(f"企业画像接口请求失败 {path}: {e}")
        return None


# ── Tool 1: 企业搜索 ──────────────────────────────────────────────────────────

def search_company(keyword: str, top_num: int = 5) -> List[Dict[str, Any]]:
    writer = safe_stream_writer()
    writer(f"正在搜索企业，关键词: {keyword}，返回条数: {top_num}...\n")

    body = _company_request("/toModel/company/search", {"keyword": keyword, "topNum": top_num})
    if not body or not isinstance(body, list):
        return []

    results = []
    for item in body:
        results.append({
            "企业名称": item.get("企业名称", ""),
            "企业id": item.get("企业id", ""),
            "法定代表人": item.get("法定代表人", ""),
            "注册资金": item.get("注册资金", ""),
            "成立日期": item.get("成立日期", ""),
            "企业状态": item.get("企业状态", ""),
            "地址": item.get("地址", ""),
            "所属产业节点": item.get("所属产业节点", []),
            "企业资质": item.get("企业资质", []),
            "官网": item.get("官网", ""),
        })
    return results


# ── Tool 2: 企业基本信息 ──────────────────────────────────────────────────────

def get_company_base_info(company_id: str) -> Dict[str, Any]:
    writer = safe_stream_writer()
    writer(f"正在获取企业基本信息，企业ID: {company_id}...\n")

    body = _company_request("/toModel/company/baseInfo", {"id": company_id})
    if not body:
        return {"error": "无法获取企业基本信息"}
    return body


# ── Tool 3: 企业经营分析 ──────────────────────────────────────────────────────

def get_company_business_analysis(company_id: str) -> Dict[str, Any]:
    writer = safe_stream_writer()
    writer(f"正在获取企业经营分析，企业ID: {company_id}...\n")

    body = _company_request("/toModel/company/businessAnalysis", {"id": company_id})
    if not body:
        return {"error": "无法获取企业经营分析数据"}
    return body


# ── Tool 4: 企业技术洞察 ──────────────────────────────────────────────────────

def get_company_tech_insight(company_id: str) -> Dict[str, Any]:
    writer = safe_stream_writer()
    writer(f"正在获取企业技术洞察，企业ID: {company_id}...\n")

    body = _company_request("/toModel/company/technicalInsight", {"id": company_id})
    if not body:
        return {"error": "无法获取企业技术洞察数据"}
    return body


# ── Tool 5: 企业资金穿透 ──────────────────────────────────────────────────────

def get_company_funding(company_id: str) -> Dict[str, Any]:
    writer = safe_stream_writer()
    writer(f"正在获取企业资金穿透信息，企业ID: {company_id}...\n")

    body = _company_request("/toModel/company/fundingTransparency", {"id": company_id})
    if not body:
        return {"error": "无法获取企业资金穿透数据"}
    return body


# ── Tool 6: 企业风险预警 ──────────────────────────────────────────────────────

def get_company_risk_warning(company_id: str) -> Dict[str, Any]:
    writer = safe_stream_writer()
    writer(f"正在获取企业风险预警信息，企业ID: {company_id}...\n")

    body = _company_request("/toModel/company/riskWarning", {"id": company_id})
    if not body:
        return {"error": "无法获取企业风险预警数据"}
    return body
