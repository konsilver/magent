#!/usr/bin/env python3
"""stdio MCP server exposing a grouped set of AI/产业链信息 + 企业画像工具.

This MCP server intentionally groups the following tools under *one* server so that
pluggability happens at the MCP-server level (not per-tool):
- get_chain_information
- get_industry_news
- get_latest_ai_news
- search_company
- get_company_base_info
- get_company_business_analysis
- get_company_tech_insight
- get_company_funding
- get_company_risk_warning

Implementation delegates to the existing per-tool impl modules.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from typing import Any, Dict, List, Optional

from mcp.server import FastMCP

mcp = FastMCP("jingxin-ai-chain-information")


def _flush_logs(buf: io.StringIO) -> None:
    logs = buf.getvalue().strip()
    if logs:
        print(logs, file=sys.stderr)


@mcp.tool()
async def get_chain_information(chain_id: str) -> Dict[str, Any]:
    """获取指定产业链的“深度全景分析报告 + 核心数据指标 + 图谱结构”。

    适用场景：
    - 用户需要对某个产业链做宏观分析：发展现状、企业画像、技术创新、投融资态势、上下游结构等。

    输出说明：
    - 返回结构化宏观数据、画像、趋势与图谱信息，非单条新闻或单个离散指标。

    参数约束（重要）：
    - chain_id 必须使用系统预定义的英文 ID，不可编造。
      常见映射示例：
        - 新能源汽车 -> industry_vehicle
        - 新一代人工智能 -> industry_ai
        - 人形机器人 -> industry_android
        - 先进石油化工 -> industry_api
        - 智能家电 -> industry_appliance
        - 智能座舱 -> industry_cabin
        - 智慧物流 -> industry_ils
        - 机器人 -> industry_robot

    Returns:
        dict: 包含产业链概况、企业画像、技术创新、投融资态势、产业链图谱等板块。
    """

    from mcp_servers.ai_chain_information_mcp.impl_chain import get_chain_information as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(chain_id=chain_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


@mcp.tool()
async def get_industry_news(
    keyword: Optional[str] = None,
    news_type: Optional[str] = None,
    chain: Optional[str] = None,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """按条件筛选“产业动态/新闻/政策/投融资”等资讯（多维过滤）。

    适用场景：
    - 用户询问某个产业链/领域的“最新动态/新闻/政策/融资/头部企业动作”等，需要按维度筛选。

    参数说明：
    - keyword：标题/摘要模糊关键词（实体名/细分方向）。
    - news_type：资讯类型（政策动向/融资报道/技术突破/产品发布/产业活动等）。
    - chain：产业链/行业领域（使用系统支持枚举值）。
    - region：地区（宁波/省内/国内/国外等）。

    边界：
    - 本工具输出资讯条目，不含历史统计数据或精确指标数值。

    Returns:
        dict: {"items": [ {"标题":...,"摘要":...,"标签":...,"对应产业链":...,"地区":...,"国家":...,"城市":...}, ... ]}
    """

    from mcp_servers.ai_chain_information_mcp.impl_news import get_industry_news as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        items = _impl(keyword=keyword, news_type=news_type, chain=chain, region=region)

    _flush_logs(buf)
    return {"items": items}


@mcp.tool()
async def get_latest_ai_news() -> Dict[str, Any]:
    """获取“最近一周”人工智能领域热门事件/动态（聚合）。

    适用场景：
    - 用户明确询问：“最近一周 AI 热门事件”“AI 产业周报”“本周 AI 动态”，且不指定具体细分维度时。

    边界：
    - 仅用于 AI 领域热点动态的聚合概览，不做具体产业链/类型/地区的分维度筛选。

    Returns:
        dict: {"items": [{"时间":...,"标题":...,"摘要":...}, ...]}
    """

    from mcp_servers.ai_chain_information_mcp.impl_latest import get_latest_ai_news as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        items = _impl()

    _flush_logs(buf)
    return {"items": items}



# ══════════════════════════════════════════════════════════════════════════════
# 企业画像工具（基于宁波知识中心企业画像接口）
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def search_company(keyword: str, top_num: int = 5) -> Dict[str, Any]:
    """按关键词搜索企业，返回匹配的企业列表（含企业 ID、名称、资质等摘要信息）。

    适用场景：
    - 用户想查某家企业但只知道名称关键词，需要先搜索获取企业 ID，再调用其他企业画像工具获取详情。
    - 这是使用其他企业详情工具（get_company_base_info / get_company_business_analysis 等）的前置步骤。

    参数说明：
    - keyword：企业名称关键词（如"比亚迪""宁波银行"等），支持模糊匹配。
    - top_num：返回条数，默认 5，最大建议不超过 10。

    输出说明：
    - 返回企业列表，每条包含：企业名称、企业id、法定代表人、注册资金、成立日期、企业状态、地址、所属产业节点、企业资质、官网。
    - 后续调用其他工具时，请使用返回的"企业id"字段作为 company_id 参数。

    Returns:
        dict: {"items": [{"企业名称":...,"企业id":...,"法定代表人":...,...}, ...]}
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import search_company as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        items = _impl(keyword=keyword, top_num=top_num)

    _flush_logs(buf)
    return {"items": items}


@mcp.tool()
async def get_company_base_info(company_id: str) -> Dict[str, Any]:
    """获取企业基本信息（工商注册、对外投资、联系方式、行业分类等）。

    适用场景：
    - 用户需要了解一家企业的基本工商信息、注册资本、行业分类、对外投资等。
    - 作为企业画像的基础信息模块。

    参数说明：
    - company_id：企业唯一标识符，必须通过 search_company 工具获取，格式为 "instance_entity_company-xxxxx"。

    输出说明：
    - 返回结构化数据，包含：公司名称、注册资本、注册地址、国民经济行业、对外投资信息（总数 + 部分列表）、对外投资地区分布、电话等。

    Returns:
        dict: 企业基本信息结构化数据。
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import get_company_base_info as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(company_id=company_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


@mcp.tool()
async def get_company_business_analysis(company_id: str) -> Dict[str, Any]:
    """获取企业经营分析数据（客户信息、供应商信息、招投标、经营状况等）。

    适用场景：
    - 用户需要分析一家企业的经营状况：主要客户、供应商关系、招投标记录等。
    - 适合做企业尽调、商业分析、竞争对手分析等场景。

    参数说明：
    - company_id：企业唯一标识符，必须通过 search_company 工具获取。

    输出说明：
    - 返回经营分析数据，可能包含：客户信息（客户列表、销售金额、关联关系）、供应商信息、招投标记录等维度。

    Returns:
        dict: 企业经营分析结构化数据。
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import get_company_business_analysis as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(company_id=company_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


@mcp.tool()
async def get_company_tech_insight(company_id: str) -> Dict[str, Any]:
    """获取企业技术洞察数据（专利分析、核心技术领域、技术趋势等）。

    适用场景：
    - 用户需要了解一家企业的技术实力：专利布局、核心技术领域、被引用最多的专利、技术领域趋势变化等。
    - 适合技术竞争力评估、知识产权分析、招商引资技术维度评估等。

    参数说明：
    - company_id：企业唯一标识符，必须通过 search_company 工具获取。

    输出说明：
    - 返回技术洞察数据，包含：被引用次数最多专利 TOP5（专利名称、被引次数、到期日期）、重点技术领域趋势（按年份统计）等。

    Returns:
        dict: 企业技术洞察结构化数据。
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import get_company_tech_insight as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(company_id=company_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


@mcp.tool()
async def get_company_funding(company_id: str) -> Dict[str, Any]:
    """获取企业资金穿透信息（对外投资扩张、投资历史、投资金额、股权结构等）。

    适用场景：
    - 用户需要了解企业的投资布局：对外投资企业数量、投资总金额、投资历史时间线、各投资企业的持股比例等。
    - 适合做股权穿透分析、资本运作分析、关联企业排查等。

    参数说明：
    - company_id：企业唯一标识符，必须通过 search_company 工具获取。

    输出说明：
    - 返回资金穿透数据，包含：投资扩张分析（总投资企业数量、对外投资总金额）、投资历史（按时间排列，每条含公司名称、投资比例、国标行业等）。

    Returns:
        dict: 企业资金穿透结构化数据。
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import get_company_funding as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(company_id=company_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


@mcp.tool()
async def get_company_risk_warning(company_id: str) -> Dict[str, Any]:
    """获取企业风险预警信息（即将到期专利、法律风险、经营异常等）。

    适用场景：
    - 用户需要评估一家企业的潜在风险：即将到期的专利、法律纠纷、行政处罚、经营异常等。
    - 适合做投资风险评估、合作伙伴风险排查、招商引资风险预警等。

    参数说明：
    - company_id：企业唯一标识符，必须通过 search_company 工具获取。

    输出说明：
    - 返回风险预警数据，包含：即将到期专利列表（专利名称、类型、公开日期、预计到期日期）等风险维度。

    Returns:
        dict: 企业风险预警结构化数据。
    """

    from mcp_servers.ai_chain_information_mcp.impl_company import get_company_risk_warning as _impl

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = _impl(company_id=company_id)

    _flush_logs(buf)
    if isinstance(result, dict):
        return result
    return {"result": result}


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
