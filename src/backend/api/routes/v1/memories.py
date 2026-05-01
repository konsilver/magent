"""记忆管理 API

GET    /v1/memories           查看当前用户所有记忆
GET    /v1/memories/settings  获取用户记忆设置
PATCH  /v1/memories/settings  更新用户记忆设置（开关）
DELETE /v1/memories           清空所有记忆
DELETE /v1/memories/{id}      删除单条记忆
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from core.auth.backend import get_current_user, UserContext
from core.db.engine import get_db
from core.llm.memory import (
    MEM0_ENABLED,
    get_all_memories,
    delete_memory,
    delete_all_memories,
    delete_memories_by_type,
)
from core.infra.responses import success_response, error_response
from core.services import UserService

router = APIRouter(prefix="/v1/memories", tags=["memories"])


class MemorySettingsRequest(BaseModel):
    memory_enabled: bool | None = None
    memory_write_enabled: bool | None = None
    reranker_enabled: bool | None = None


# ── 固定路径优先注册，避免被 /{memory_id} 误匹配 ────────────────

def _is_reranker_available() -> bool:
    """Check if reranker endpoint is configured at the infra level."""
    try:
        from core.config.model_config import ModelConfigService
        cfg = ModelConfigService.get_instance().resolve("reranker")
        if cfg and cfg.base_url and cfg.model_name:
            return True
    except Exception:
        pass
    import os
    return bool(os.getenv("RERANKER_URL") and os.getenv("RERANKER_MODEL"))


@router.get("/settings")
async def get_memory_settings(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户记忆 / 重排开关设置。"""
    svc = UserService(db)
    settings = svc.get_user_settings(str(user.user_id))
    _mem_default = MEM0_ENABLED
    return success_response(data={
        "memory_enabled": settings.get("memory_enabled", _mem_default),
        "memory_write_enabled": settings.get("memory_write_enabled", _mem_default),
        "mem0_available": MEM0_ENABLED,
        "reranker_enabled": settings.get("reranker_enabled", False),
        "reranker_available": _is_reranker_available(),
    })


@router.patch("/settings")
async def update_memory_settings(
    body: MemorySettingsRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新用户记忆 / 重排开关设置（持久化到 users_shadow.metadata）。"""
    svc = UserService(db)
    patch: dict = {}
    if body.memory_enabled is not None:
        patch["memory_enabled"] = body.memory_enabled
    if body.memory_write_enabled is not None:
        patch["memory_write_enabled"] = body.memory_write_enabled
    if body.reranker_enabled is not None:
        patch["reranker_enabled"] = body.reranker_enabled
    if patch:
        svc.update_user_metadata(user_id=str(user.user_id), patch=patch)
    return success_response(data={
        **({"memory_enabled": body.memory_enabled} if body.memory_enabled is not None else {}),
        **({"memory_write_enabled": body.memory_write_enabled} if body.memory_write_enabled is not None else {}),
        **({"reranker_enabled": body.reranker_enabled} if body.reranker_enabled is not None else {}),
    })


# ── 列表 / 清空 / 单条删除 ──────────────────────────────────────

@router.get("")
async def list_memories(user: UserContext = Depends(get_current_user)):
    """获取当前用户所有记忆条目。"""
    if not MEM0_ENABLED:
        return success_response(data={"enabled": False, "items": [], "count": 0})
    items = await get_all_memories(str(user.user_id))
    return success_response(data={"enabled": True, "items": items, "count": len(items)})


@router.delete("")
async def remove_all_memories(
    user: UserContext = Depends(get_current_user),
    type: Optional[str] = Query(None, description="按记忆类型过滤删除，如 user_profile"),
):
    """清空当前用户记忆。传入 type 参数时只删除指定类型，否则删除全部。"""
    if not MEM0_ENABLED:
        return success_response(data={"message": "记忆系统未启用，无需清除"})
    if type:
        ok = await delete_memories_by_type(str(user.user_id), type)
        if not ok:
            return error_response(code=50002, message="清空失败", status_code=500)
        return success_response(data={"message": f"已清空类型为 {type} 的记忆"})
    ok = await delete_all_memories(str(user.user_id))
    if not ok:
        return error_response(code=50002, message="清空失败", status_code=500)
    return success_response(data={"message": "已清空所有记忆"})


@router.delete("/{memory_id}")
async def remove_memory(memory_id: str, user: UserContext = Depends(get_current_user)):
    """删除单条记忆。"""
    ok = await delete_memory(memory_id)
    if not ok:
        return error_response(code=50001, message="删除失败", status_code=500)
    return success_response(data={"deleted": memory_id})
