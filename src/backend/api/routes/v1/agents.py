"""User-facing sub-agent API routes.

Provides CRUD for user-owned agents and read access to admin agents.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth.backend import get_current_user, UserContext
from core.db.engine import get_db
from core.services.user_agent_service import UserAgentService
from core.infra.responses import success_response, error_response

router = APIRouter(prefix="/v1/agents", tags=["User Agents"])
logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    avatar: Optional[str] = None
    description: Optional[str] = Field("", max_length=20)
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
    code_exec_enabled: Optional[bool] = False
    extra_config: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar: Optional[str] = None
    description: Optional[str] = Field(None, max_length=20)
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
    code_exec_enabled: Optional[bool] = None
    is_enabled: Optional[bool] = None
    extra_config: Optional[Dict[str, Any]] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", summary="列出当前用户可见的所有子智能体")
async def list_agents(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    agents = svc.list_for_user(user.user_id)
    return success_response(data=agents)


@router.get("/available-resources", summary="可绑定到子智能体的资源列表")
async def available_resources(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    resources = svc.list_available_resources()
    return success_response(data=resources)


@router.get("/{agent_id}", summary="子智能体详情")
async def get_agent(
    agent_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    try:
        agent = svc.get_by_id(agent_id, user_id=user.user_id)
    except LookupError:
        return error_response(code=404, message="Agent not found")
    except PermissionError:
        return error_response(code=403, message="Access denied")
    return success_response(data=agent)


@router.post("", summary="创建用户子智能体")
async def create_agent(
    body: AgentCreateRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    data = body.model_dump(exclude_none=True)
    try:
        agent = svc.create(user_id=user.user_id, operator_name=user.username, owner_type="user", data=data)
    except ValueError as exc:
        return error_response(code=400, message=str(exc))
    return success_response(data=agent)


@router.put("/{agent_id}", summary="更新用户子智能体")
async def update_agent(
    agent_id: str,
    body: AgentUpdateRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    data = body.model_dump(exclude_none=True)
    try:
        agent = svc.update(agent_id, user_id=user.user_id, operator_name=user.username, owner_type="user", data=data)
    except LookupError:
        return error_response(code=404, message="Agent not found")
    except PermissionError:
        return error_response(code=403, message="Access denied")
    return success_response(data=agent)


@router.delete("/{agent_id}", summary="删除用户子智能体")
async def delete_agent(
    agent_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = UserAgentService(db)
    try:
        svc.delete(agent_id, user_id=user.user_id, owner_type="user")
    except LookupError:
        return error_response(code=404, message="Agent not found")
    except PermissionError:
        return error_response(code=403, message="Access denied")
    return success_response(data={"deleted": True})
