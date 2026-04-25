"""Implementation for tool: get_chain_information (bundled MCP).

Moved from `mcp_servers/get_chain_information_mcp/impl.py` to support deleting the old MCP server.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

import requests
from dotenv import load_dotenv

# Import safe stream writer from common utilities
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _common import safe_stream_writer

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


def get_chain_information(chain_id: str):
    writer = safe_stream_writer()
    writer(f"正在通过宁波产业知识中心获取产业链发展最新情况，查询产业链: {chain_id}...\n")

    industry_url, auth_token = _resolve_industry_config()
    base_url = industry_url
    headers = {
        "Authorization": auth_token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    def fetch_data(url_suffix, params=None):
        try:
            full_url = f"{base_url}{url_suffix}"
            response = requests.get(full_url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("header", {}).get("code") == 200:
                return data.get("body")
            return None
        except Exception as e:
            print(f"接口请求失败 {url_suffix}: {e}")
            return None

    def clean_chain_tree(node):
        if not node:
            return None
        simple_node = {"名称": node.get("name")}
        children = node.get("children", [])
        if children:
            cleaned_children = []
            for child in children:
                cleaned_child = clean_chain_tree(child)
                if cleaned_child:
                    cleaned_children.append(cleaned_child)
            if cleaned_children:
                simple_node["下级环节"] = cleaned_children
        return simple_node

    def map_list_fields(data_list, field_mapping):
        if not data_list:
            return []
        new_list = []
        for item in data_list:
            new_item = {}
            for old_key, new_key in field_mapping.items():
                if old_key in item:
                    val = item[old_key]
                    if val is None:
                        val = 0
                    new_item[new_key] = val
            new_list.append(new_item)
        return new_list

    print(f"正在获取产业链基础架构: {chain_id}...")
    base_info = fetch_data("/company/analysis/info", {"chainId": chain_id})
    if not base_info:
        return {"error": "无法获取产业链基础信息"}

    tasks = {
        "overview": ("/industry/overview/IndustryOverview", {"chainId": chain_id}),
        "key_enterprises": ("/company/analysis/highQualityEnterprises", {"chainId": chain_id}),
        "capital_dist": ("/company/analysis/registCapiValue", {"chainId": chain_id}),
        "time_dist": ("/company/analysis/establishmentYears", {"chainId": chain_id}),
        "region_dist": ("/industry/overview/OverviewRegion", {"chainId": chain_id}),
        "node_dist": ("/industry/overview/getCompanyNode", {"chainId": chain_id}),
        "patent_trend": ("/industry/overview/getOveriewPatentApply", {"chainId": chain_id}),
        "tech_lifecycle": ("/technologyTrends/getLifeCycle", {"chainId": chain_id}),
        "tech_domain": ("/industry/overview/getPatentDomain", {"chainId": chain_id}),
        "financing_trend": ("/industry/overview/getFinancingTrend", {"chainId": chain_id}),
        "financing_city": ("/metrics/getFinancingOrientation", {"chainId": chain_id, "type": 2}),
        "financing_node": ("/metrics/getFinancingOrientation", {"chainId": chain_id, "type": 1}),
    }

    results: Dict[str, Any] = {}
    print("正在并行获取多维数据...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_key = {executor.submit(fetch_data, url, params): key for key, (url, params) in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception:
                results[key] = None

    overview_data = results.get("overview", {}) or {}

    key_ent = map_list_fields(results.get("key_enterprises", []), {"name": "类型", "count": "数量"})
    cap_dist = map_list_fields((results.get("capital_dist", {}) or {}).get("records", []), {"name": "注册资本", "count": "数量"})

    region_raw = results.get("region_dist", {}) or {}
    prov_top5 = map_list_fields((region_raw.get("省份TOP5排行", []) or [])[:5], {"region": "省份", "num": "企业数"})
    city_top5 = map_list_fields((region_raw.get("城市TOP5排行", []) or [])[:5], {"region": "城市", "num": "企业数"})

    patent_raw = results.get("patent_trend", [])
    patent_trend_clean = []
    if patent_raw:
        for p_type in patent_raw:
            if p_type.get("patentType") == "发明授权":
                recent_years = (p_type.get("list", []) or [])[-5:]
                patent_trend_clean = map_list_fields(recent_years, {"year": "年份", "count": "授权数量"})

    tech_life = map_list_fields((results.get("tech_lifecycle", []) or [])[-5:], {"year": "年份", "num": "专利申请数", "people": "申请人数"})
    tech_dom = map_list_fields((results.get("tech_domain", []) or [])[:8], {"domain": "技术领域", "num": "专利数"})

    fin_trend = map_list_fields((results.get("financing_trend", []) or [])[-5:], {"year": "年份", "num": "融资事件数", "money": "融资金额(亿元)"})
    fin_city = map_list_fields((results.get("financing_city", []) or [])[:5], {"name": "城市", "firstValue": "涉及金额"})

    raw_tree = base_info.get("nodeData", {})
    cleaned_tree = clean_chain_tree(raw_tree)

    final_output = {
        "产业链概况": {
            "名称": base_info.get("name"),
            "描述": base_info.get("description"),
            "关键指标": {
                "节点数": base_info.get("nodeCount"),
                "企业总数": base_info.get("companyCount"),
                "专利总量": overview_data.get("专利总量"),
                "近5年融资金额(亿元)": overview_data.get("近5年融资金额"),
                "上市企业数": overview_data.get("上市企业数量"),
            },
        },
        "企业画像": {
            "重点企业": key_ent,
            "注册资本分布": cap_dist,
            "区域分布": {"省份TOP5": prov_top5, "城市TOP5": city_top5},
            "环节分布": map_list_fields(results.get("node_dist", []) or [], {"node": "环节", "num": "企业数", "rate": "占比(%)"}),
        },
        "技术创新": {
            "近5年专利授权趋势": patent_trend_clean,
            "近5年技术生命周期": tech_life,
            "核心技术领域(TOP8)": tech_dom,
        },
        "投融资态势": {"近5年融资趋势": fin_trend, "热门投向城市": fin_city},
        "产业链图谱": cleaned_tree,
    }

    return final_output
