"""Plan mode — in-memory plan store, context board, and shared utility helpers.

All stateless helpers and the in-process plan store live here so the main
plan_mode.py stays focused on orchestration logic.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.config.settings import settings

logger = logging.getLogger(__name__)


# ── Role-based model selection ────────────────────────────────────────────────

def _role_model(role: str, fallback: str) -> str:
    """Return role-specific model name, falling back to request-level model."""
    role_val = getattr(settings.llm.roles, role, "")
    return role_val if role_val else fallback


def _subagent_model(complexity: str, fallback: str) -> str:
    """Return model identifier for a subagent step based on its complexity.

    Returns either a concrete model name or a role key string — both are
    accepted by create_agent_executor (it tries role-key lookup first, then
    provider lookup by model_name).

    simple  → env ROLE_SUBAGENT_SIMPLE_MODEL if set, else "subagent_simple"
              (agent_factory resolves the role key from DB)
    complex → env ROLE_SUBAGENT_MODEL if set, else fallback
    """
    if complexity == "simple":
        from_env = _role_model("subagent_simple", "")
        return from_env if from_env else "subagent_simple"
    return _role_model("subagent", fallback)


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory Plan Store
# ═══════════════════════════════════════════════════════════════════════════════

_PLAN_STORE: Dict[str, Dict[str, Any]] = {}
_PLAN_STORE_LOCK = threading.Lock()


def _store_plan(plan_dict: Dict[str, Any]) -> None:
    with _PLAN_STORE_LOCK:
        _PLAN_STORE[plan_dict["plan_id"]] = plan_dict


def _get_stored_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    with _PLAN_STORE_LOCK:
        return _PLAN_STORE.get(plan_id)


def _update_stored_plan(plan_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    with _PLAN_STORE_LOCK:
        plan = _PLAN_STORE.get(plan_id)
        if plan is None:
            return None
        plan.update(kwargs)
        plan["updated_at"] = datetime.utcnow().isoformat()
        return plan


def _update_stored_step(plan_id: str, step_id: str, **kwargs: Any) -> bool:
    with _PLAN_STORE_LOCK:
        plan = _PLAN_STORE.get(plan_id)
        if plan is None:
            return False
        for step in plan.get("steps", []):
            if step["step_id"] == step_id:
                step.update(kwargs)
                return True
        return False


def _replace_stored_steps(plan_id: str, new_steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Replace all steps of a plan (for replan). Generates new step_ids."""
    with _PLAN_STORE_LOCK:
        plan = _PLAN_STORE.get(plan_id)
        if plan is None:
            return None
        plan["steps"] = [
            {
                "step_id": f"{plan_id}_step_{i + 1}",
                "step_order": i + 1,
                "title": s.get("title", f"步骤{i+1}"),
                "brief_description": s.get("brief_description", ""),
                "description": s.get("description", ""),
                "expected_tools": s.get("expected_tools", []),
                "expected_skills": s.get("expected_skills", []),
                "expected_agents": s.get("expected_agents", []),
                "status": "pending",
                "result_summary": None,
                "ai_output": None,
                "error_message": None,
                "started_at": None,
                "completed_at": None,
                "tool_calls_log": [],
            }
            for i, s in enumerate(new_steps)
        ]
        plan["total_steps"] = len(plan["steps"])
        plan["updated_at"] = datetime.utcnow().isoformat()
        return plan


def _make_plan_dict(
    plan_id: str,
    user_id: str,
    title: str,
    description: str,
    task_input: str,
    steps: List[Dict[str, Any]],
    extra_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct a fresh plan dict for the in-memory store."""
    now = datetime.utcnow().isoformat()
    step_list = [
        {
            "step_id": f"{plan_id}_step_{i + 1}",
            "step_order": i + 1,
            "title": s.get("title", f"步骤{i+1}"),
            "brief_description": s.get("brief_description", ""),
            "description": s.get("description", ""),
            "expected_tools": s.get("expected_tools", []),
            "expected_skills": s.get("expected_skills", []),
            "expected_agents": s.get("expected_agents", []),
            "status": "pending",
            "result_summary": None,
            "ai_output": None,
            "error_message": None,
            "started_at": None,
            "completed_at": None,
            "tool_calls_log": [],
        }
        for i, s in enumerate(steps)
    ]
    return {
        "plan_id": plan_id,
        "user_id": user_id,
        "title": title,
        "description": description,
        "task_input": task_input,
        "status": "draft",
        "total_steps": len(step_list),
        "completed_steps": 0,
        "result_summary": None,
        "steps": step_list,
        "extra_data": extra_data or {},
        "created_at": now,
        "updated_at": now,
    }


# ── Lightweight step proxy (replaces ORM PlanStep) ───────────────────────────

class _StepProxy:
    """Wraps a step dict so existing code can use attribute access."""
    __slots__ = ("_d",)

    def __init__(self, d: Dict[str, Any]):
        object.__setattr__(self, "_d", d)

    def __getattr__(self, name: str) -> Any:
        try:
            return object.__getattribute__(self, "_d")[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_d")[name] = value


# ═══════════════════════════════════════════════════════════════════════════════
# Context Board  (shared in-memory blackboard for all agents)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_context_board() -> Dict[str, Any]:
    """Return a fresh context blackboard."""
    return {
        "user": {
            "urgent": None,
            "mem": None,
        },
        "plan": {
            "user_goal": None,
            "steps": [],
            "redo_id": -1,
            "suggestion": None,
            "plan_suggestion": None,
        },
        "check": {
            "global_constraints": [],
        },
    }


def _context_board_summary(board: Dict[str, Any]) -> str:
    """Serialize the public parts of the board for agent prompts.

    Only exposes fields defined in data_structure/context.md — internal
    runtime fields (suggestion, tool_use_trace, _*) are intentionally excluded.
    """
    _PUBLIC_STEP_KEYS = {"step_id", "title", "brief_description", "description", "output"}
    steps = [
        {k: v for k, v in s.items() if k in _PUBLIC_STEP_KEYS}
        for s in board.get("plan", {}).get("steps", [])
    ]
    public = {
        "user": board.get("user", {}),
        "plan": {
            "user_goal": board.get("plan", {}).get("user_goal"),
            "steps": steps,
        },
        "check": board.get("check", {}),
    }
    return json.dumps(public, ensure_ascii=False, indent=2)


# ── Tool/agent discovery helpers ──────────────────────────────────────────────

def _collect_valid_tool_names(enabled_mcp_ids: Optional[List[str]] = None) -> Optional[set]:
    """Return the set of tool names available from enabled MCP servers, or None if no filter."""
    if not enabled_mcp_ids:
        return None
    try:
        from core.config.mcp_service import McpServerConfigService
        from core.db.engine import SessionLocal
        with SessionLocal() as _db:
            svc = McpServerConfigService(_db)
            tool_names: set = set()
            for mcp_id in enabled_mcp_ids:
                try:
                    config = svc.get_by_id(mcp_id)
                    if config and config.get("tools"):
                        for tool in config["tools"]:
                            name = tool.get("name") or tool.get("tool_name")
                            if name:
                                tool_names.add(name)
                except Exception:
                    pass
            return tool_names if tool_names else None
    except Exception:
        return None


def _load_visible_agents(
    db: Session,
    user_id: str,
    enabled_agent_ids: Optional[List[str]],
) -> List[Dict[str, Any]]:
    """Load user-visible agents filtered by enabled_agent_ids."""
    try:
        from core.services.user_agent_service import UserAgentService
        svc = UserAgentService(db)
        agents = svc.list_agents(user_id=user_id)
        if enabled_agent_ids is not None:
            agents = [a for a in agents if a.get("agent_id") in enabled_agent_ids]
        return agents
    except Exception as exc:
        logger.debug("[plan_store] _load_visible_agents failed: %s", exc)
        return []


# ── Text/JSON utilities ───────────────────────────────────────────────────────

async def _prepare_history(
    session_messages: List[Dict[str, Any]],
    model_name: str,
) -> List[Dict[str, Any]]:
    if not session_messages:
        return []
    from core.llm.context_manager import ContextWindowManager
    ctx_mgr = ContextWindowManager.for_model(model_name)
    trimmed = ctx_mgr.trim_history(session_messages)
    dropped_count = len(session_messages) - len(trimmed)
    if dropped_count > 0:
        try:
            from core.llm.history_summarizer import summarize_history
            summary = await summarize_history(session_messages[:dropped_count])
            if summary:
                return [
                    {"role": "user", "content": f"<conversation_summary>\n{summary}\n</conversation_summary>\n（以上为早期对话的结构化摘要）"},
                    *trimmed,
                ]
        except Exception:
            pass
    return trimmed


def _build_file_context(uploaded_files: List[Dict[str, Any]], max_chars: int = 50000) -> str:
    if not uploaded_files:
        return ""
    file_names = [f"- {f.get('name', '未知文件')}" for f in uploaded_files]
    content_parts: List[str] = []
    for f in uploaded_files:
        content = (f.get("content") or "").strip()
        if content:
            name = f.get("name", "未知文件")
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... (内容过长，已截断)"
            content_parts.append(f"### {name}\n{content}")
    if not content_parts:
        return ""
    return (
        f"[附件文件]: {chr(10).join(file_names)}\n\n"
        f"[附件内容]\n" + "\n\n---\n\n".join(content_parts) + "\n[附件内容结束]"
    )


def _parse_json_output(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from AI output, handling markdown fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _extract_summary(text: str, max_len: int = 200) -> str:
    if not text:
        return "已完成"
    _noise = {"---", "***", "___", "```", "---\n"}
    lines = [l.strip() for l in text.strip().split("\n") if l.strip() and l.strip() not in _noise and not l.strip().startswith("```")]
    if not lines:
        return "已完成"
    summary = lines[-1]
    if len(summary) > max_len:
        summary = summary[:max_len] + "..."
    return summary


def _terminate_mcp_processes(mcp_clients: list) -> None:
    for client in mcp_clients:
        try:
            proc = getattr(client, "_process", None) or getattr(client, "process", None)
            if proc is not None and getattr(proc, "returncode", None) is None:
                proc.terminate()
        except Exception:
            pass
        try:
            stack = getattr(client, "stack", None)
            if stack is not None and hasattr(stack, "_exit_callbacks"):
                stack._exit_callbacks.clear()
            client.stack = None
            client.session = None
            client.client = None
            client.is_connected = False
        except Exception:
            pass


def _mem0_enabled() -> bool:
    try:
        from core.llm.memory import MEM0_ENABLED
        return MEM0_ENABLED
    except Exception:
        return False
