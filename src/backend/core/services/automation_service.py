"""Automation service — CRUD for scheduled tasks and run history."""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid

from croniter import croniter
import pytz
from sqlalchemy.orm import Session

from core.db.models import ScheduledTask, ScheduledTaskRun
from core.infra.logging import get_logger

logger = get_logger(__name__)


def compute_next_run(
    cron_expression: str,
    timezone: str = "Asia/Shanghai",
    base_time: Optional[datetime] = None,
) -> datetime:
    """Compute the next fire time from a cron expression in the given timezone.

    Returns a timezone-aware UTC datetime.
    """
    tz = pytz.timezone(timezone)
    base = base_time or datetime.now(tz)
    if base.tzinfo is None:
        base = tz.localize(base)
    cron = croniter(cron_expression, base)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(pytz.utc)


class AutomationService:
    """Service for scheduled-task operations."""

    def __init__(self, db: Session):
        self.db = db

    # ── Task CRUD ──────────────────────────────────────────────────

    def create_task(
        self,
        *,
        user_id: str,
        task_type: str,
        prompt: Optional[str] = None,
        plan_id: Optional[str] = None,
        cron_expression: str,
        recurring: bool = True,
        schedule_type: str = "recurring",
        name: Optional[str] = None,
        description: str = "",
        timezone: str = "Asia/Shanghai",
        enabled_mcp_ids: Optional[List[str]] = None,
        enabled_skill_ids: Optional[List[str]] = None,
        enabled_kb_ids: Optional[List[str]] = None,
        enabled_agent_ids: Optional[List[str]] = None,
        max_runs: Optional[int] = None,
    ) -> ScheduledTask:
        task_id = f"auto_{uuid.uuid4().hex[:16]}"

        # Manual tasks are never auto-scheduled; next_run_at stays NULL so scheduler skips them.
        if schedule_type == "manual":
            next_run = None
        else:
            next_run = compute_next_run(cron_expression, timezone)

        if schedule_type == "once" or not recurring:
            max_runs = 1

        task = ScheduledTask(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            prompt=prompt,
            plan_id=plan_id,
            cron_expression=cron_expression,
            recurring=recurring,
            schedule_type=schedule_type,
            timezone=timezone,
            enabled_mcp_ids=enabled_mcp_ids or [],
            enabled_skill_ids=enabled_skill_ids or [],
            enabled_kb_ids=enabled_kb_ids or [],
            enabled_agent_ids=enabled_agent_ids or [],
            status="active",
            next_run_at=next_run,
            max_runs=max_runs,
            name=name,
            description=description,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: str, user_id: str) -> Optional[ScheduledTask]:
        task = self.db.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
        if task and task.user_id != user_id:
            return None
        return task

    def get_task_by_id(self, task_id: str) -> Optional[ScheduledTask]:
        """Get task without ownership check (for scheduler)."""
        return self.db.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()

    def list_tasks(
        self,
        user_id: str,
        status_filter: Optional[str] = None,
        sidebar_activated: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScheduledTask]:
        q = self.db.query(ScheduledTask).filter(ScheduledTask.user_id == user_id)
        if status_filter:
            q = q.filter(ScheduledTask.status == status_filter)
        if sidebar_activated is not None:
            q = q.filter(ScheduledTask.sidebar_activated == sidebar_activated)
        return q.order_by(ScheduledTask.created_at.desc()).offset(offset).limit(limit).all()

    def activate_sidebar(self, task_id: str, user_id: str) -> Optional[ScheduledTask]:
        """Mark a task as sidebar-activated (idempotent)."""
        task = self.get_task(task_id, user_id)
        if not task:
            return None
        if not task.sidebar_activated:
            task.sidebar_activated = True
            task.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(task)
        return task

    def update_task(self, task_id: str, user_id: str, **kwargs: Any) -> Optional[ScheduledTask]:
        task = self.get_task(task_id, user_id)
        if not task:
            return None
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        # Manual tasks never fire automatically — clear next_run_at regardless of cron.
        # For recurring/once, recompute if cron or schedule_type changed.
        if task.schedule_type == "manual":
            task.next_run_at = None
        elif "cron_expression" in kwargs or "schedule_type" in kwargs:
            task.next_run_at = compute_next_run(
                task.cron_expression, task.timezone
            )
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def update_task_system(self, task_id: str, **kwargs: Any) -> Optional[ScheduledTask]:
        """Update task fields without ownership check (for scheduler)."""
        task = self.get_task_by_id(task_id)
        if not task:
            return None
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete_task(self, task_id: str, user_id: str) -> bool:
        task = self.db.query(ScheduledTask).filter(
            ScheduledTask.task_id == task_id,
            ScheduledTask.user_id == user_id,
        ).first()
        if not task:
            return False
        self.db.delete(task)
        self.db.commit()
        return True

    def pause_task(self, task_id: str, user_id: str) -> Optional[ScheduledTask]:
        task = self.get_task(task_id, user_id)
        if not task or task.status != "active":
            return None
        task.status = "paused"
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    def resume_task(self, task_id: str, user_id: str) -> Optional[ScheduledTask]:
        task = self.get_task(task_id, user_id)
        if not task or task.status != "paused":
            return None
        task.status = "active"
        task.next_run_at = compute_next_run(task.cron_expression, task.timezone)
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task

    # ── Due tasks (for scheduler) ──────────────────────────────────

    def get_due_tasks(self, now: datetime) -> List[ScheduledTask]:
        # Manual-type tasks are never auto-fired; they can only be triggered via /trigger.
        return (
            self.db.query(ScheduledTask)
            .filter(
                ScheduledTask.status == "active",
                ScheduledTask.schedule_type != "manual",
                ScheduledTask.next_run_at <= now,
            )
            .all()
        )

    def advance_next_run(self, task_id: str) -> None:
        task = self.get_task_by_id(task_id)
        if not task:
            return
        task.run_count = (task.run_count or 0) + 1

        # Manual tasks: stay active, no next_run_at, run_count just grows.
        if task.schedule_type == "manual":
            task.next_run_at = None
        # One-shot (once) or max_runs reached → completed
        elif (task.schedule_type == "once") or (not task.recurring) or (task.max_runs and task.run_count >= task.max_runs):
            task.status = "completed"
            task.next_run_at = None
        else:
            task.next_run_at = compute_next_run(task.cron_expression, task.timezone)

        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    # ── Run history ───────────────────────────────────────────────

    def record_run_start(self, task_id: str) -> ScheduledTaskRun:
        run = ScheduledTaskRun(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def record_run_complete(
        self,
        run_id: str,
        *,
        status: str,
        chat_id: Optional[str] = None,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        duration_ms: int = 0,
        usage: Optional[Dict] = None,
    ) -> Optional[ScheduledTaskRun]:
        run = self.db.query(ScheduledTaskRun).filter(ScheduledTaskRun.run_id == run_id).first()
        if not run:
            return None
        run.status = status
        run.chat_id = chat_id
        run.result_summary = result_summary
        run.error_message = error_message
        run.completed_at = datetime.now(timezone.utc)
        run.duration_ms = duration_ms
        run.usage = usage or {}
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_task_runs(
        self, task_id: str, user_id: str, limit: int = 10
    ) -> List[ScheduledTaskRun]:
        task = self.get_task(task_id, user_id)
        if not task:
            return []
        return (
            self.db.query(ScheduledTaskRun)
            .filter(ScheduledTaskRun.task_id == task_id)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(limit)
            .all()
        )

    # ── Serialization ─────────────────────────────────────────────

    @staticmethod
    def task_to_dict(task: ScheduledTask) -> Dict[str, Any]:
        plan_title = None
        if task.plan_id and task.plan:
            plan_title = task.plan.title
        return {
            "task_id": task.task_id,
            "user_id": task.user_id,
            "task_type": task.task_type,
            "prompt": task.prompt,
            "plan_id": task.plan_id,
            "plan_title": plan_title,
            "cron_expression": task.cron_expression,
            "recurring": task.recurring,
            "schedule_type": task.schedule_type or ("recurring" if task.recurring else "once"),
            "timezone": task.timezone,
            "enabled_mcp_ids": task.enabled_mcp_ids or [],
            "enabled_skill_ids": task.enabled_skill_ids or [],
            "enabled_kb_ids": task.enabled_kb_ids or [],
            "enabled_agent_ids": task.enabled_agent_ids or [],
            "status": task.status,
            "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
            "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
            "run_count": task.run_count or 0,
            "max_runs": task.max_runs,
            "consecutive_failures": task.consecutive_failures or 0,
            "max_failures": task.max_failures or 3,
            "last_error": task.last_error,
            "name": task.name,
            "description": task.description,
            "sidebar_activated": bool(task.sidebar_activated),
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        }

    @staticmethod
    def run_to_dict(run: ScheduledTaskRun) -> Dict[str, Any]:
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status,
            "chat_id": run.chat_id,
            "result_summary": run.result_summary,
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_ms": run.duration_ms,
            "usage": run.usage or {},
        }
