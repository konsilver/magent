"""Plan mode service — reads/writes the in-memory plan store.

DB persistence for Plan/PlanStep has been removed.  Plans live only in
the process-level _PLAN_STORE dict managed by plan_mode.py.  This service
is kept as a thin shim so the REST API routes (plans.py) and chats.py
require minimal changes.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime


class PlanService:
    """Thin shim over the in-memory plan store."""

    def __init__(self, db=None):
        # db is accepted but ignored — plans are no longer persisted to DB
        pass

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _store():
        from routing.subagents.plan_mode import (
            _get_stored_plan,
            _update_stored_plan,
            _replace_stored_steps,
            _PLAN_STORE,
            _PLAN_STORE_LOCK,
        )
        return {
            "get": _get_stored_plan,
            "update": _update_stored_plan,
            "replace_steps": _replace_stored_steps,
            "store": _PLAN_STORE,
            "lock": _PLAN_STORE_LOCK,
        }

    # ── Plan CRUD ──────────────────────────────────────────────────

    def get_plan(self, plan_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Return the plan dict from memory, or None if not found / wrong owner."""
        plan = self._store()["get"](plan_id)
        if plan and plan.get("user_id") != user_id:
            return None
        return plan

    def list_plans(self, user_id: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """List plans for a user, newest first."""
        import threading
        store = self._store()
        with store["lock"]:
            all_plans = [
                p for p in store["store"].values()
                if p.get("user_id") == user_id
            ]
        all_plans.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return all_plans[offset: offset + limit]

    def update_plan(self, plan_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Update plan fields in memory."""
        return self._store()["update"](plan_id, **kwargs)

    def replace_steps(self, plan_id: str, steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Replace all steps of a plan."""
        return self._store()["replace_steps"](plan_id, steps)

    def delete_plan(self, plan_id: str, user_id: str) -> bool:
        """Remove a plan from memory."""
        store = self._store()
        with store["lock"]:
            plan = store["store"].get(plan_id)
            if not plan or plan.get("user_id") != user_id:
                return False
            del store["store"][plan_id]
        return True

    # ── Serialization helpers ──────────────────────────────────────

    @staticmethod
    def plan_to_dict(plan: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize a plan dict (identity, since it's already a dict)."""
        if plan is None:
            return {}
        result = {
            "plan_id": plan.get("plan_id", ""),
            "title": plan.get("title", ""),
            "description": plan.get("description", ""),
            "task_input": plan.get("task_input", ""),
            "status": plan.get("status", "draft"),
            "total_steps": plan.get("total_steps", 0),
            "completed_steps": plan.get("completed_steps", 0),
            "result_summary": plan.get("result_summary"),
            "steps": [PlanService.step_to_dict(s) for s in plan.get("steps", [])],
            "created_at": plan.get("created_at"),
            "updated_at": plan.get("updated_at"),
        }
        extra = plan.get("extra_data") or {}
        if isinstance(extra, dict) and extra.get("agent_name_map"):
            result["agent_name_map"] = extra["agent_name_map"]
        return result

    @staticmethod
    def step_to_dict(step: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize a step dict."""
        return {
            "step_id": step.get("step_id", ""),
            "step_order": step.get("step_order", 0),
            "title": step.get("title", ""),
            "brief_description": step.get("brief_description", ""),
            "description": step.get("description", ""),
            "expected_tools": step.get("expected_tools", []),
            "expected_skills": step.get("expected_skills", []),
            "expected_agents": step.get("expected_agents", []),
            "status": step.get("status", "pending"),
            "result_summary": step.get("result_summary"),
            "ai_output": step.get("ai_output"),
            "error_message": step.get("error_message"),
            "started_at": step.get("started_at"),
            "completed_at": step.get("completed_at"),
        }

    @staticmethod
    def build_execution_snapshot(
        plan: Dict[str, Any],
        *,
        completed_steps: int,
        total_steps: int,
        result_text: str,
    ) -> Dict[str, Any]:
        """Snapshot of plan execution for persistence in assistant message."""
        return {
            "mode": "complete",
            "title": plan.get("title", ""),
            "description": plan.get("description", ""),
            "steps": [
                {
                    "step_order": s.get("step_order", 0),
                    "title": s.get("title", ""),
                    "brief_description": s.get("brief_description", ""),
                    "description": s.get("description", ""),
                    "expected_tools": s.get("expected_tools") or [],
                    "expected_skills": s.get("expected_skills") or [],
                    "status": s.get("status", "pending"),
                    "summary": s.get("result_summary"),
                    "ai_output": s.get("ai_output"),
                    "tool_calls_log": s.get("tool_calls_log") or [],
                }
                for s in plan.get("steps", [])
            ],
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "result_text": result_text,
        }
