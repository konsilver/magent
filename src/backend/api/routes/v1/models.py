"""Model management API routes (admin-only).

Provides CRUD for model providers and role assignments,
plus connectivity testing and export/import.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import require_config
from core.db.engine import get_db
from core.config.model_config import ModelConfigService
from core.db.model_repository import (
    ROLE_DEFINITIONS,
    assign_role,
    create_provider,
    delete_provider,
    export_all,
    get_provider,
    import_all,
    list_providers,
    list_role_assignments,
    provider_is_referenced,
    set_provider_test_result,
    unassign_role,
    update_provider,
)
from core.infra.responses import success_response

router = APIRouter(prefix="/v1/models", tags=["Models"])
logger = logging.getLogger(__name__)


# ── Request / response schemas ────────────────────────────────────────────────

class ProviderCreateRequest(BaseModel):
    display_name: str
    provider_type: str = Field(..., pattern="^(chat|embedding|reranker)$")
    base_url: str
    api_key: str
    model_name: str
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ProviderUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class TestConnectionRequest(BaseModel):
    """For testing a provider config that hasn't been saved yet."""
    provider_type: str = Field(..., pattern="^(chat|embedding|reranker)$")
    base_url: str
    api_key: str
    model_name: str


class RoleAssignRequest(BaseModel):
    provider_id: str


class ImportRequest(BaseModel):
    providers: List[Dict[str, Any]] = Field(default_factory=list)
    role_assignments: List[Dict[str, Any]] = Field(default_factory=list)
    overwrite: bool = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask_api_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _provider_to_dict(p) -> dict:
    return {
        "provider_id": p.provider_id,
        "display_name": p.display_name,
        "provider_type": p.provider_type,
        "base_url": p.base_url,
        "api_key": _mask_api_key(p.api_key),
        "model_name": p.model_name,
        "extra_config": p.extra_config or {},
        "is_active": p.is_active,
        "last_tested_at": p.last_tested_at.isoformat() if p.last_tested_at else None,
        "last_test_status": p.last_test_status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _normalize_base_url(base_url: str, provider_type: str) -> str:
    """Normalize base_url to ensure it ends with /v1 for chat/embedding providers.

    OpenAI-compatible models expect base_url like 'http://host:port/v1'.
    Users often provide 'http://host:port/' or 'http://host:port'.
    """
    url = base_url.strip().rstrip("/")
    if provider_type in ("chat", "embedding") and not url.endswith("/v1"):
        url = url + "/v1"
    return url


async def _validate_provider_config(
    base_url: str, api_key: str, model_name: str, provider_type: str,
) -> None:
    """Validate provider config at save time. Raises HTTPException on failure."""
    normalized_url = _normalize_base_url(base_url, provider_type)
    result = await _test_connection(provider_type, normalized_url, api_key, model_name)
    if not result["success"]:
        raise HTTPException(
            status_code=400,
            detail=f"模型连通性验证失败：{result['error']}。请检查 URL、令牌和模型名称是否正确。",
        )


async def _test_connection(provider_type: str, base_url: str, api_key: str, model_name: str) -> dict:
    """Test connectivity. Returns {success, latency_ms, error}."""
    base_url = base_url.rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    if provider_type == "chat":
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }
    elif provider_type == "embedding":
        url = f"{base_url}/embeddings"
        payload = {"model": model_name, "input": "test"}
    elif provider_type == "reranker":
        url = f"{base_url}/rerank"
        payload = {"model": model_name, "query": "test", "documents": ["a", "b"]}
    else:
        return {"success": False, "latency_ms": 0, "error": f"Unknown type: {provider_type}"}

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
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


# ── Provider endpoints ────────────────────────────────────────────────────────

@router.get("/providers", summary="列出所有模型供应商")
async def list_providers_endpoint(
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    providers = list_providers(db)
    return success_response(data=[_provider_to_dict(p) for p in providers])


@router.post("/providers", summary="新增模型供应商")
async def create_provider_endpoint(
    body: ProviderCreateRequest,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    normalized_url = _normalize_base_url(body.base_url, body.provider_type)
    await _validate_provider_config(normalized_url, body.api_key, body.model_name, body.provider_type)

    provider = create_provider(
        db,
        display_name=body.display_name,
        provider_type=body.provider_type,
        base_url=normalized_url,
        api_key=body.api_key,
        model_name=body.model_name,
        extra_config=body.extra_config,
        is_active=body.is_active,
    )
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data=_provider_to_dict(provider))


@router.put("/providers/{provider_id}", summary="更新模型供应商")
async def update_provider_endpoint(
    provider_id: str,
    body: ProviderUpdateRequest,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}

    # If URL or credentials changed, validate the new config
    existing = get_provider(db, provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    new_url = fields.get("base_url", existing.base_url)
    new_key = fields.get("api_key", existing.api_key)
    new_model = fields.get("model_name", existing.model_name)
    new_type = fields.get("provider_type", existing.provider_type)

    if "base_url" in fields or "api_key" in fields or "model_name" in fields:
        normalized_url = _normalize_base_url(new_url, new_type)
        await _validate_provider_config(normalized_url, new_key, new_model, new_type)
        fields["base_url"] = normalized_url
    elif "base_url" not in fields:
        # Even on non-connectivity updates, ensure stored URL is normalized
        pass

    provider = update_provider(db, provider_id, **fields)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data=_provider_to_dict(provider))


@router.delete("/providers/{provider_id}", summary="删除模型供应商")
async def delete_provider_endpoint(
    provider_id: str,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    refs = provider_is_referenced(db, provider_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail=f"该供应商正被以下角色引用，请先取消分配：{', '.join(refs)}",
        )
    if not delete_provider(db, provider_id):
        raise HTTPException(status_code=404, detail="Provider not found")
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data={"deleted": provider_id})


# ── Connectivity testing ──────────────────────────────────────────────────────

@router.post("/providers/{provider_id}/test", summary="测试已保存供应商连通性")
async def test_saved_provider(
    provider_id: str,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    provider = get_provider(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    test_url = _normalize_base_url(provider.base_url, provider.provider_type)
    result = await _test_connection(
        provider.provider_type, test_url, provider.api_key, provider.model_name,
    )
    set_provider_test_result(db, provider_id, result["success"])
    return success_response(data=result)


@router.post("/providers/test", summary="测试未保存配置连通性（预检）")
async def test_unsaved_provider(
    body: TestConnectionRequest,
    _: None = Depends(require_config),
):
    test_url = _normalize_base_url(body.base_url, body.provider_type)
    result = await _test_connection(
        body.provider_type, test_url, body.api_key, body.model_name,
    )
    return success_response(data=result)


# ── Role assignment endpoints ─────────────────────────────────────────────────

@router.get("/roles", summary="列出所有角色及当前分配")
async def list_roles_endpoint(
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    return success_response(data=list_role_assignments(db))


@router.put("/roles/{role_key}", summary="为角色分配供应商")
async def assign_role_endpoint(
    role_key: str,
    body: RoleAssignRequest,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    if role_key not in ROLE_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role_key}")

    # Type check: ensure provider_type matches role's required type
    provider = get_provider(db, body.provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    required_type = ROLE_DEFINITIONS[role_key]["type"]
    if provider.provider_type != required_type:
        raise HTTPException(
            status_code=400,
            detail=f"角色 '{role_key}' 需要 {required_type} 类型的供应商，但所选供应商是 {provider.provider_type} 类型",
        )

    if not assign_role(db, role_key, body.provider_id):
        raise HTTPException(status_code=400, detail="Assignment failed")
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data={"role_key": role_key, "provider_id": body.provider_id})


@router.delete("/roles/{role_key}", summary="取消角色分配")
async def unassign_role_endpoint(
    role_key: str,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    if role_key not in ROLE_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role_key}")
    unassign_role(db, role_key)
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data={"role_key": role_key, "provider_id": None})


# ── Export / Import ───────────────────────────────────────────────────────────

@router.get("/export", summary="导出模型配置")
async def export_endpoint(
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    return success_response(data=export_all(db))


@router.post("/import", summary="导入模型配置")
async def import_endpoint(
    body: ImportRequest,
    _: None = Depends(require_config),
    db: Session = Depends(get_db),
):
    result = import_all(db, body.model_dump(), overwrite=body.overwrite)
    ModelConfigService.get_instance().invalidate_cache()
    return success_response(data=result)
