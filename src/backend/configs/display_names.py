"""Unified display-name mappings for tools and MCP servers.

Single source of truth — every module that needs a Chinese display name
for a tool or server should import from here.
"""

from __future__ import annotations

from typing import Dict

# ── MCP 服务器级别名称 ────────────────────────────────────────────────────────

# MCP 服务器 ID → 中文名称（用于能力中心面板标题）
MCP_SERVER_DISPLAY_NAMES: Dict[str, str] = {
    "retrieve_dataset_content":  "知识库检索",
    "internet_search":           "互联网搜索",
    "web_fetch":                 "网站信息抓取",
}

# MCP 服务器 ID → 一句话功能描述（用于能力中心面板描述文字）
MCP_SERVER_DESCRIPTIONS: Dict[str, str] = {
    "retrieve_dataset_content":  "从公有/私有知识库中语义检索政策文件、产业报告及用户上传文档，支持混合检索与重排序。",
    "internet_search":           "通过互联网实时搜索公开网页、新闻及财经资讯，作为数据库与知识库之外的信息兜底。",
    "web_fetch":                 "抓取指定网页 URL 的内容，提取正文文本或 Markdown，支持搜索引擎结果页解析。",
}

# ── 工具函数级别名称 ──────────────────────────────────────────────────────────

# 工具函数名 → 中文显示名（用于对话框工具卡片 + 流式事件）
TOOL_DISPLAY_NAMES: Dict[str, str] = {
    # MCP 工具
    "retrieve_dataset_content":   "公有知识库检索",
    "list_datasets":              "查看知识库列表",
    "internet_search":            "互联网搜索",
    # 内置工具
    "get_skills":                 "查询可用技能",
    "get_agents":                 "查询可用智能体",
    "get_mcp_tools":              "查询 MCP 工具列表",
    "search_knowledge_base":      "知识库搜索",
    # 子智能体调度
    "call_subagent":              "调用子智能体",
    # 技能系统
    "view_text_file":             "读取文件",
    "web_fetch":                  "网页抓取",
    # 跨轮文件访问
    "read_artifact":              "读取文件内容",
    # 代码执行 Lab 工具
    "execute_code":               "代码执行",
    "run_command":                "执行命令",
}
