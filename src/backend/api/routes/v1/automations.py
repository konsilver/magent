"""Automation API routes — CRUD for scheduled tasks + notifications."""

import json
from typing import Any, Dict, List, Optional

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth.backend import get_current_user, UserContext
from core.db.engine import get_db
from core.infra.responses import success_response, created_response
from core.services.automation_service import AutomationService
from core.infra.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/automations", tags=["Automations"])


# ── Request Schemas ────────────────────────────────────────────

class CreateAutomationRequest(BaseModel):
    task_type: str = Field(..., pattern=r"^(prompt|plan)$")
    prompt: Optional[str] = Field(None, max_length=5000)
    plan_id: Optional[str] = None
    cron_expression: str = Field(..., min_length=9, max_length=100)
    recurring: bool = True
    schedule_type: Optional[str] = Field(None, pattern=r"^(recurring|once|manual)$")
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    timezone: str = "Asia/Shanghai"
    enabled_mcp_ids: Optional[List[str]] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_kb_ids: Optional[List[str]] = None
    enabled_agent_ids: Optional[List[str]] = None
    max_runs: Optional[int] = None


class UpdateAutomationRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    cron_expression: Optional[str] = Field(None, min_length=9, max_length=100)
    recurring: Optional[bool] = None
    schedule_type: Optional[str] = Field(None, pattern=r"^(recurring|once|manual)$")
    prompt: Optional[str] = Field(None, max_length=5000)
    enabled_mcp_ids: Optional[List[str]] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_kb_ids: Optional[List[str]] = None
    enabled_agent_ids: Optional[List[str]] = None


class NotificationIdsRequest(BaseModel):
    ids: List[str]


# ── Endpoints ──────────────────────────────────────────────────

@router.post("")
async def create_automation(
    req: CreateAutomationRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a scheduled automation task."""
    # Validate cron expression
    if not croniter.is_valid(req.cron_expression):
        raise HTTPException(status_code=400, detail="无效的 cron 表达式")

    # Validate task content
    if req.task_type == "prompt" and not req.prompt:
        raise HTTPException(status_code=400, detail="提示词类型任务必须提供 prompt")
    if req.task_type == "plan":
        if not req.plan_id:
            raise HTTPException(status_code=400, detail="计划类型任务必须提供 plan_id")
        from core.services.plan_service import PlanService
        plan_svc = PlanService(db)
        plan = plan_svc.get_plan(req.plan_id, user.user_id)
        if not plan:
            raise HTTPException(status_code=404, detail="计划不存在或无权访问")

    # Infer schedule_type from recurring if not provided (backward compat)
    schedule_type = req.schedule_type or ("recurring" if req.recurring else "once")

    svc = AutomationService(db)
    task = svc.create_task(
        user_id=user.user_id,
        task_type=req.task_type,
        prompt=req.prompt,
        plan_id=req.plan_id,
        cron_expression=req.cron_expression,
        recurring=req.recurring,
        schedule_type=schedule_type,
        name=req.name,
        description=req.description or "",
        timezone=req.timezone,
        enabled_mcp_ids=req.enabled_mcp_ids,
        enabled_skill_ids=req.enabled_skill_ids,
        enabled_kb_ids=req.enabled_kb_ids,
        enabled_agent_ids=req.enabled_agent_ids,
        max_runs=req.max_runs,
    )
    return created_response(data=AutomationService.task_to_dict(task))


@router.get("")
async def list_automations(
    status: Optional[str] = None,
    sidebar_activated: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all automation tasks for the current user."""
    svc = AutomationService(db)
    tasks = svc.list_tasks(
        user.user_id,
        status_filter=status,
        sidebar_activated=sidebar_activated,
        limit=limit,
        offset=offset,
    )
    return success_response(data=[AutomationService.task_to_dict(t) for t in tasks])


@router.get("/{task_id}")
async def get_automation(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get automation task details."""
    svc = AutomationService(db)
    task = svc.get_task(task_id, user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success_response(data=AutomationService.task_to_dict(task))


@router.patch("/{task_id}")
async def update_automation(
    task_id: str,
    req: UpdateAutomationRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an automation task."""
    if req.cron_expression and not croniter.is_valid(req.cron_expression):
        raise HTTPException(status_code=400, detail="无效的 cron 表达式")

    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="无更新字段")

    svc = AutomationService(db)
    task = svc.update_task(task_id, user.user_id, **updates)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success_response(data=AutomationService.task_to_dict(task))


@router.delete("/{task_id}")
async def delete_automation(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an automation task and its run history."""
    svc = AutomationService(db)
    deleted = svc.delete_task(task_id, user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success_response(message="已删除")


@router.post("/{task_id}/pause")
async def pause_automation(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pause an active automation task."""
    svc = AutomationService(db)
    task = svc.pause_task(task_id, user.user_id)
    if not task:
        raise HTTPException(status_code=400, detail="任务不存在或不可暂停")
    return success_response(data=AutomationService.task_to_dict(task))


@router.post("/{task_id}/resume")
async def resume_automation(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Resume a paused automation task."""
    svc = AutomationService(db)
    task = svc.resume_task(task_id, user.user_id)
    if not task:
        raise HTTPException(status_code=400, detail="任务不存在或不可恢复")
    return success_response(data=AutomationService.task_to_dict(task))


@router.post("/{task_id}/trigger")
async def trigger_automation(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger an automation task immediately."""
    svc = AutomationService(db)
    task = svc.get_task(task_id, user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in ("active", "paused"):
        raise HTTPException(status_code=400, detail=f"任务状态为 '{task.status}'，无法手动触发")

    from routing.automation_scheduler import get_scheduler
    scheduler = get_scheduler()
    if scheduler:
        import asyncio
        asyncio.create_task(scheduler.execute_task(task.task_id, task.user_id))
    return success_response(message="已触发执行")


@router.post("/{task_id}/activate-sidebar")
async def activate_sidebar(
    task_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark automation task as sidebar-activated (idempotent)."""
    svc = AutomationService(db)
    task = svc.activate_sidebar(task_id, user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success_response(data=AutomationService.task_to_dict(task))


@router.get("/{task_id}/runs")
async def get_automation_runs(
    task_id: str,
    limit: int = 10,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get run history for an automation task."""
    svc = AutomationService(db)
    runs = svc.get_task_runs(task_id, user.user_id, limit=limit)
    return success_response(data=[AutomationService.run_to_dict(r) for r in runs])


# ── Notifications (Redis-backed) ──────────────────────────────

_NOTIF_TTL = 7 * 24 * 3600


async def _modify_notification_list(user_id: str, ids: set, transform):
    """Read notification list from Redis, apply *transform* to each matched
    item, and rewrite the list.  *transform(item)* returns the modified item
    dict to keep, or ``None`` to drop it."""
    from core.infra.redis import get_redis
    redis = get_redis()
    if not redis:
        return
    key = f"jx:notifications:{user_id}"
    raw_items = await redis.lrange(key, 0, -1)
    kept = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
            if item.get("id") in ids:
                item = transform(item)
                if item is None:
                    continue
            kept.append(json.dumps(item, ensure_ascii=False))
        except (json.JSONDecodeError, TypeError):
            kept.append(raw if isinstance(raw, str) else raw.decode())
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.delete(key)
        if kept:
            await pipe.rpush(key, *kept)
        await pipe.expire(key, _NOTIF_TTL)
        await pipe.execute()


@router.get("/notifications/list")
async def get_notifications(
    user: UserContext = Depends(get_current_user),
):
    """Get automation notifications for the current user."""
    try:
        from core.infra.redis import get_redis
        redis = get_redis()
        if not redis:
            return success_response(data=[])
        key = f"jx:notifications:{user.user_id}"
        raw_items = await redis.lrange(key, 0, 49)
        notifications = []
        for raw in raw_items:
            try:
                notifications.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue
        return success_response(data=notifications)
    except Exception as exc:
        logger.warning("Failed to fetch notifications: %s", exc)
        return success_response(data=[])


@router.post("/notifications/read")
async def mark_notifications_read(
    req: NotificationIdsRequest,
    user: UserContext = Depends(get_current_user),
):
    """Mark notifications as read."""
    try:
        def _mark_read(item):
            item["read"] = True
            return item
        await _modify_notification_list(user.user_id, set(req.ids), _mark_read)
        return success_response(message="ok")
    except Exception as exc:
        logger.warning("Failed to mark notifications read: %s", exc)
        return success_response(message="ok")


@router.post("/notifications/delete")
async def delete_notifications(
    req: NotificationIdsRequest,
    user: UserContext = Depends(get_current_user),
):
    """Delete notifications by IDs."""
    try:
        await _modify_notification_list(user.user_id, set(req.ids), lambda _: None)
        return success_response(message="ok")
    except Exception as exc:
        logger.warning("Failed to delete notifications: %s", exc)
        return success_response(message="ok")
