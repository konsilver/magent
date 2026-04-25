"""Admin sub-agent management API routes.

Provides CRUD for admin-owned agents (visible to all users).
Protected by ADMIN_TOKEN.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from api.deps import require_admin
from core.db.engine import get_db
from core.db.models import UserAgent
from core.services.user_agent_service import UserAgentService
from core.infra.responses import success_response, error_response

router = APIRouter(
    prefix="/v1/admin/agents",
    tags=["Admin Agents"],
    dependencies=[Depends(require_admin)],
)
logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class AdminAgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    avatar: Optional[str] = None
    description: Optional[str] = ""
    system_prompt: str = ""
    welcome_message: Optional[str] = ""
    suggested_questions: Optional[List[str]] = Field(default_factory=list)
    mcp_server_ids: Optional[List[str]] = Field(default_factory=list)
    skill_ids: Optional[List[str]] = Field(default_factory=list)
    kb_ids: Optional[List[str]] = Field(default_factory=list)
    model_provider_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_iters: Optional[int] = 10
    timeout: Optional[int] = 120
    is_enabled: Optional[bool] = True
    sort_order: Optional[int] = 0
    extra_config: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AgentImportRequest(BaseModel):
    agents: List[Dict[str, Any]] = Field(..., description="Array of agent objects to import")
    overwrite: bool = Field(True, description="Overwrite existing agents with same name")


class AdminAgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    suggested_questions: Optional[List[str]] = None
    mcp_server_ids: Optional[List[str]] = None
    skill_ids: Optional[List[str]] = None
    kb_ids: Optional[List[str]] = None
    model_provider_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_iters: Optional[int] = None
    timeout: Optional[int] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    extra_config: Optional[Dict[str, Any]] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", summary="列出所有 Admin 子智能体")
async def list_admin_agents(db: Session = Depends(get_db)):
    svc = UserAgentService(db)
    agents = svc.list_admin()
    return success_response(data=agents)


@router.post("", summary="创建 Admin 子智能体")
async def create_admin_agent(
    body: AdminAgentCreateRequest,
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    data = body.model_dump(exclude_none=True)
    try:
        agent = svc.create(user_id=None, operator_name="管理员", owner_type="admin", data=data)
    except ValueError as exc:
        return error_response(code=400, message=str(exc))
    return success_response(data=agent)


_EXPORT_FIELDS = [
    "name", "description", "system_prompt", "welcome_message",
    "suggested_questions", "mcp_server_ids", "skill_ids", "kb_ids",
    "model_provider_id", "temperature", "max_tokens", "max_iters",
    "timeout", "is_enabled", "sort_order", "extra_config", "avatar",
]


@router.get("/export", summary="导出所有 Admin 子智能体")
async def export_agents(db: Session = Depends(get_db)):
    """Export all admin agents as a JSON array."""
    rows = db.query(UserAgent).filter(UserAgent.owner_type == "admin").order_by(UserAgent.sort_order).all()
    items = []
    for r in rows:
        item = {}
        for f in _EXPORT_FIELDS:
            val = getattr(r, f, None)
            # Convert Decimal to float for JSON serialization
            if isinstance(val, Decimal):
                val = float(val)
            item[f] = val
        items.append(item)
    return success_response(data=items)


@router.post("/import", summary="导入 Admin 子智能体")
async def import_agents(req: AgentImportRequest, db: Session = Depends(get_db)):
    """Import agents from a JSON array. Upserts by name."""
    created = 0
    updated = 0
    now = datetime.utcnow()
    for item in req.agents:
        name = item.get("name")
        if not name:
            continue
        existing = db.query(UserAgent).filter(
            UserAgent.owner_type == "admin", UserAgent.name == name
        ).first()
        if existing and not req.overwrite:
            continue
        if existing:
            for f in _EXPORT_FIELDS:
                if f == "name":
                    continue
                if f in item:
                    setattr(existing, f, item[f])
            existing.updated_at = now
            for jf in ("suggested_questions", "mcp_server_ids", "skill_ids", "kb_ids", "extra_config"):
                if jf in item:
                    flag_modified(existing, jf)
            updated += 1
        else:
            row = UserAgent(
                agent_id=str(uuid.uuid4()),
                owner_type="admin",
                name=name,
                description=item.get("description", ""),
                system_prompt=item.get("system_prompt", ""),
                welcome_message=item.get("welcome_message", ""),
                suggested_questions=item.get("suggested_questions", []),
                mcp_server_ids=item.get("mcp_server_ids", []),
                skill_ids=item.get("skill_ids", []),
                kb_ids=item.get("kb_ids", []),
                model_provider_id=item.get("model_provider_id"),
                temperature=item.get("temperature"),
                max_tokens=item.get("max_tokens"),
                max_iters=item.get("max_iters", 10),
                timeout=item.get("timeout", 120),
                is_enabled=item.get("is_enabled", True),
                sort_order=item.get("sort_order", 0),
                extra_config=item.get("extra_config", {}),
                avatar=item.get("avatar"),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created += 1
    db.commit()
    logger.info("admin_agents_imported: created=%d updated=%d", created, updated)
    return success_response(data={"created": created, "updated": updated, "message": "Import complete"})


@router.get("/{agent_id}", summary="Admin 子智能体详情")
async def get_admin_agent(
    agent_id: str,
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    try:
        agent = svc.get_by_id(agent_id)
    except LookupError:
        return error_response(code=404, message="Agent not found")
    return success_response(data=agent)


@router.put("/{agent_id}", summary="更新 Admin 子智能体")
async def update_admin_agent(
    agent_id: str,
    body: AdminAgentUpdateRequest,
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    data = body.model_dump(exclude_none=True)
    try:
        agent = svc.update(agent_id, user_id=None, operator_name="管理员", owner_type="admin", data=data)
    except LookupError:
        return error_response(code=404, message="Agent not found")
    except PermissionError:
        return error_response(code=403, message="Access denied")
    return success_response(data=agent)


@router.put("/{agent_id}/toggle", summary="切换 Admin 子智能体启用状态")
async def toggle_admin_agent(
    agent_id: str,
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    try:
        agent = svc.toggle_enabled(agent_id)
    except LookupError:
        return error_response(code=404, message="Agent not found")
    return success_response(data=agent)


@router.delete("/{agent_id}", summary="删除 Admin 子智能体")
async def delete_admin_agent(
    agent_id: str,
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    try:
        svc.delete(agent_id, user_id=None, owner_type="admin")
    except LookupError:
        return error_response(code=404, message="Agent not found")
    except PermissionError:
        return error_response(code=403, message="Access denied")
    return success_response(data={"deleted": True})
