"""Service configuration management API routes (admin-only).

Provides list / update / test / export / import for external service configs
(knowledge_base, industry, file_parser, internet_search).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import require_config
from core.infra.responses import success_response
from core.config.system_config import SystemConfigService

router = APIRouter(prefix="/v1/service-configs", tags=["ServiceConfigs"])
logger = logging.getLogger(__name__)

_GROUP_LABELS: Dict[str, str] = {
    "knowledge_base": "知识库服务",
    "industry": "产业知识中心",
    "file_parser": "文件解析服务",
    "internet_search": "互联网搜索",
}

_VALID_GROUPS = set(_GROUP_LABELS.keys())


# ── Request schemas ─────────────────────────────────────────────────────────

class ConfigUpdateItem(BaseModel):
    key: str
    value: Optional[str] = None


class BulkUpdateRequest(BaseModel):
    items: List[ConfigUpdateItem]


class ImportRequest(BaseModel):
    configs: List[Dict[str, Any]]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value or len(value) <= 8:
        return "****" if value else None
    return value[:4] + "****" + value[-4:]


def _config_to_dict(cfg: dict, mask: bool = True) -> dict:
    """Convert internal config dict to API response dict."""
    result = dict(cfg)
    if mask and cfg.get("is_secret") and cfg.get("config_value"):
        result["config_value"] = _mask_secret(cfg["config_value"])
    return result


def _svc() -> SystemConfigService:
    return SystemConfigService.get_instance()


async def _reinitialize_mcp_pool() -> None:
    """Invalidate MCP config cache and reinitialize stable connections.

    Called after system config changes so that MCP sub-processes pick up
    new env vars (e.g. updated API URLs/keys).
    """
    try:
        from core.config.mcp_service import McpServerConfigService
        mcp_svc = McpServerConfigService.get_instance()
        mcp_svc.invalidate_cache()

        from core.llm.mcp_pool import MCPConnectionPool
        pool = MCPConnectionPool.get_instance()
        if pool.is_initialized:
            new_configs = mcp_svc.get_all_servers()
            await pool.reinitialize_if_config_changed(new_configs)
            logger.info("[service_configs] MCP pool reinitialize triggered after config update")
    except Exception as exc:
        logger.warning("[service_configs] MCP pool reinitialize failed: %s", exc)


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", summary="列出所有服务配置")
async def list_all_configs(_: None = Depends(require_config)):
    configs = _svc().get_all_configs()
    grouped: Dict[str, Any] = {}
    for cfg in configs:
        gk = cfg.get("group_key", "other")
        if gk not in grouped:
            grouped[gk] = {
                "group_key": gk,
                "label": _GROUP_LABELS.get(gk, gk),
                "items": [],
            }
        grouped[gk]["items"].append(_config_to_dict(cfg))
    return success_response(data=list(grouped.values()))


# NOTE: Fixed-path routes MUST come before /{group_key} to avoid being
# swallowed by the path-parameter route.

@router.get("/export", summary="导出服务配置", name="export_service_configs")
async def export_configs(_: None = Depends(require_config)):
    configs = _svc().get_all_configs()
    # Export includes real values (no masking) for backup/restore
    return success_response(data={"configs": configs})


@router.post("/import", summary="导入服务配置", name="import_service_configs")
async def import_configs(
    body: ImportRequest,
    _: None = Depends(require_config),
):
    items = [
        {"key": c.get("config_key", ""), "value": c.get("config_value")}
        for c in body.configs
        if c.get("config_key")
    ]
    _svc().bulk_set(items)
    asyncio.ensure_future(_reinitialize_mcp_pool())
    return success_response(data={"imported": len(items)})


@router.get("/{group_key}", summary="列出某分组的服务配置")
async def list_group_configs(
    group_key: str,
    _: None = Depends(require_config),
):
    if group_key not in _VALID_GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_key}")
    configs = _svc().get_group_configs(group_key)
    return success_response(data={
        "group_key": group_key,
        "label": _GROUP_LABELS.get(group_key, group_key),
        "items": [_config_to_dict(cfg) for cfg in configs],
    })


@router.put("", summary="批量更新服务配置")
async def bulk_update_configs(
    body: BulkUpdateRequest,
    _: None = Depends(require_config),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="items cannot be empty")
    _svc().bulk_set([item.model_dump() for item in body.items])
    asyncio.ensure_future(_reinitialize_mcp_pool())
    return success_response(data={"updated": len(body.items)})


@router.post("/test/{group_key}", summary="测试服务连通性")
async def test_service_connectivity(
    group_key: str,
    _: None = Depends(require_config),
):
    if group_key not in _VALID_GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group_key}")

    svc = _svc()

    if group_key == "knowledge_base":
        url = svc.get("knowledge_base.url")
        api_key = svc.get("knowledge_base.api_key")
        if not url:
            return success_response(data={"success": False, "error": "URL 未配置", "latency_ms": 0})
        return success_response(data=await _test_dify(url, api_key or ""))

    elif group_key == "industry":
        url = svc.get("industry.url")
        if not url:
            return success_response(data={"success": False, "error": "URL 未配置", "latency_ms": 0})
        return success_response(data=await _test_http_health(url))

    elif group_key == "file_parser":
        url = svc.get("file_parser.api_url")
        if not url:
            return success_response(data={"success": False, "error": "URL 未配置", "latency_ms": 0})
        return success_response(data=await _test_http_health(url))

    elif group_key == "internet_search":
        engine = svc.get("internet_search.engine") or "tavily"
        if engine == "baidu":
            api_key = svc.get("internet_search.baidu_api_key")
            if not api_key:
                return success_response(data={"success": False, "error": "百度搜索 API Key 未配置", "latency_ms": 0})
            return success_response(data=await _test_baidu(api_key))
        else:
            api_key = svc.get("internet_search.tavily_api_key")
            if not api_key:
                return success_response(data={"success": False, "error": "Tavily API Key 未配置", "latency_ms": 0})
            return success_response(data=await _test_tavily(api_key))

    return success_response(data={"success": False, "error": "不支持的分组", "latency_ms": 0})


# ── Health-check helpers ────────────────────────────────────────────────────

async def _test_http_health(base_url: str) -> dict:
    """Simple HTTP GET connectivity check."""
    url = base_url.rstrip("/")
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Try /health first, fallback to base URL
            resp = await client.get(url)
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code < 500:
            return {"success": True, "latency_ms": latency, "error": None}
        return {"success": False, "latency_ms": latency, "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return {"success": False, "latency_ms": latency, "error": str(exc)}


TAVILY_SEARCH_API_URL = "https://api.tavily.com/search"


async def _test_tavily(api_key: str) -> dict:
    """Test Tavily API key by making a minimal search request."""
    url = TAVILY_SEARCH_API_URL
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "api_key": api_key,
                "query": "test",
                "max_results": 1,
            })
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"success": True, "latency_ms": latency, "error": None}
        return {
            "success": False,
            "latency_ms": latency,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return {"success": False, "latency_ms": latency, "error": str(exc)}


BAIDU_SEARCH_API_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"


async def _test_baidu(api_key: str) -> dict:
    """Test Baidu AI Search API key by making a minimal search request."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                BAIDU_SEARCH_API_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Appbuilder-Authorization": f"Bearer {api_key}",
                },
                json={
                    "messages": [{"content": "test", "role": "user"}],
                    "search_source": "baidu_search_v2",
                    "resource_type_filter": [{"type": "web", "top_k": 1}],
                },
            )
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"success": True, "latency_ms": latency, "error": None}
        return {
            "success": False,
            "latency_ms": latency,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return {"success": False, "latency_ms": latency, "error": str(exc)}


async def _test_dify(base_url: str, api_key: str) -> dict:
    """Test Dify KB connectivity by listing datasets."""
    url = f"{base_url.rstrip('/')}/datasets"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                params={"limit": 1},
            )
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"success": True, "latency_ms": latency, "error": None}
        return {
            "success": False,
            "latency_ms": latency,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        return {"success": False, "latency_ms": latency, "error": str(exc)}
