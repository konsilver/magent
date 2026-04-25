"""Plan mode orchestration — Planner → Warmup → SubAgent+QA pipeline.

Phase 1 (generate): UserProfile + Planner run in parallel, memory queried,
                    structured step list produced and persisted.
Phase 2 (execute):  Warmup Agent reads context board (user + plan), sets
                    global constraints / success_criteria / first-step
                    local constraint.  Steps are then executed sequentially
                    by SubAgents; each writes its output to the context board
                    and prepares the next step's local constraint.  A QA
                    Agent validates every step result.
                    Control flow: PASS → next step,
                                  REDO_STEP (up to 2×) → retry current,
                                  REPLAN → Planner re-plans from failure point,
                                  global_replan > 1 → full reset,
                                  full_reset > 1 → Forced mode (QA disabled).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.db.models import Plan
from core.infra import log_writer
from core.infra.logging import LogContext
from core.llm.agent_factory import create_agent_executor
from core.llm.mcp_manager import close_clients
from core.services.plan_service import PlanService
from routing.streaming import StreamingAgent, _UsageTrackingModel

import time as _time

logger = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "prompts", "prompt_text", "v4", "system", "90_plan_mode.system.md",
)

# ── Control-flow constants ────────────────────────────────────────────────────
_MAX_REDO_PER_STEP = 2      # REDO_STEP retries before escalating to REPLAN
_MAX_LOCAL_REPLAN = 1       # local REPLAN count before triggering full global reset
_MAX_GLOBAL_RESET = 1       # full global reset count before entering Forced mode


# ═══════════════════════════════════════════════════════════════════════════════
# Context Board  (shared in-memory blackboard for all agents)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_context_board() -> Dict[str, Any]:
    """Return a fresh context blackboard as per data_structure/context.md."""
    return {
        "user": {
            "urgent": None,   # extracted from latest query by user_profile_agent
            "mem": None,      # retrieved from memory (disabled for now)
        },
        "plan": {
            "user_goal": None,
            "steps": [],      # list of {step_id, description, output}
        },
        "check": {
            "global_constraints": [],
            "assumptions": [],
        },
        "only_qa": {
            "success_criteria": [],
        },
    }


def _context_board_summary(board: Dict[str, Any]) -> str:
    """Serialize the public parts of the board for agent prompts."""
    public = {
        "user": board.get("user", {}),
        "plan": board.get("plan", {}),
        "check": board.get("check", {}),
    }
    return json.dumps(public, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _collect_valid_tool_names(enabled_mcp_ids: Optional[List[str]] = None) -> Optional[set]:
    if enabled_mcp_ids is None:
        return None
    valid = set(enabled_mcp_ids)
    try:
        from core.config.mcp_service import McpServerConfigService
        svc = McpServerConfigService.get_instance()
        all_servers = svc.get_all_servers()
        for sid in enabled_mcp_ids:
            cfg = all_servers.get(sid, {})
            for tool in cfg.get("tools_json", []) or []:
                if isinstance(tool, dict) and tool.get("name"):
                    valid.add(tool["name"])
    except Exception:
        pass
    return valid


def _load_visible_agents(
    db: Session,
    user_id: str,
    enabled_agent_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    try:
        from core.services.user_agent_service import UserAgentService
        svc = UserAgentService(db)
        agents = svc.list_for_user(user_id)
        agents = [a for a in agents if a.get("is_enabled", True)]
        if enabled_agent_ids is not None:
            id_set = set(enabled_agent_ids)
            agents = [a for a in agents if a.get("agent_id") in id_set]
        return agents
    except Exception as exc:
        logger.warning("Failed to load visible agents: %s", exc)
        return []


def _load_default_plan_prompt(available_tools_desc: str = "（暂无工具信息）") -> str:
    try:
        path = os.path.normpath(_PROMPT_PATH)
        with open(path, "r", encoding="utf-8") as f:
            template = f.read()
        return template.replace("{available_tools}", available_tools_desc)
    except Exception as exc:
        logger.warning("Failed to load default plan prompt: %s", exc)
        return (
            "你是一个通用任务规划助手。请将用户任务分解为详细可执行的步骤，输出严格JSON，"
            "包含 title、description、steps 字段。"
        )


def _build_tools_description(
    enabled_mcp_ids: Optional[List[str]] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    visible_agents: Optional[List[Dict[str, Any]]] = None,
) -> str:
    tool_lines: List[str] = []
    skill_lines: List[str] = []
    agent_lines: List[str] = []

    try:
        from core.config.mcp_service import McpServerConfigService
        svc = McpServerConfigService.get_instance()
        all_servers = svc.get_all_servers()
        mcp_filter = set(enabled_mcp_ids) if enabled_mcp_ids is not None else None
        for sid, s in all_servers.items():
            if mcp_filter is not None and sid not in mcp_filter:
                continue
            name = s.get("display_name", sid)
            desc = s.get("description", "")
            tools = s.get("tools_json", [])
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)] if tools else []
            tool_lines.append(f"- **{name}** ({sid}): {desc}")
            if tool_names:
                tool_lines.append(f"  具体工具函数: {', '.join(tool_names)}")
    except Exception as exc:
        logger.warning("Failed to load MCP tools: %s", exc)

    try:
        from agent_skills.loader import get_skill_loader
        loader = get_skill_loader()
        all_skills = loader.load_all_metadata()
        skill_filter = set(enabled_skill_ids) if enabled_skill_ids is not None else None
        for skill_id, meta in all_skills.items():
            if skill_filter is not None and skill_id not in skill_filter:
                continue
            name = getattr(meta, "name", skill_id) if not isinstance(meta, dict) else meta.get("name", skill_id)
            desc = getattr(meta, "description", "") if not isinstance(meta, dict) else meta.get("description", "")
            skill_lines.append(f"- **{name}** (id: {skill_id}): {desc}")
    except Exception as exc:
        logger.warning("Failed to load skills: %s", exc)

    if visible_agents:
        from core.llm.subagent_tool import _get_tools_desc
        for a in visible_agents:
            agent_id = a.get("agent_id", "")
            name = a.get("name", agent_id)
            desc = a.get("description", "")
            agent_lines.append(f"- **{name}** (id: {agent_id}): {desc}（可用工具: {_get_tools_desc(a)}）")

    sections = []
    if tool_lines:
        sections.append("### MCP 工具（填入 expected_tools 字段）\n" + "\n".join(tool_lines))
    if skill_lines:
        sections.append("### 技能（填入 expected_skills 字段）\n" + "\n".join(skill_lines))
    if agent_lines:
        sections.append("### 子智能体（填入 expected_agents 字段）\n" + "\n".join(agent_lines))

    return "\n\n".join(sections) if sections else "（当前无可用工具或技能）"


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
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
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


# ═══════════════════════════════════════════════════════════════════════════════
# Memory helpers  (all disabled — fields left empty)
# ═══════════════════════════════════════════════════════════════════════════════

async def _retrieve_plan_memory(user_id: str, task_description: str) -> Dict[str, Any]:
    return {"similar_tasks": [], "failure_patterns": []}


async def _retrieve_step_memory(user_id: str, step_description: str) -> Dict[str, Any]:
    return {"relevant_patterns": []}


def _save_task_memory_background(
    user_id: str,
    user_goal: str,
    plan_steps: List[str],
    success: bool,
    quality_score: float,
    failure_reason: str,
    final_solution_summary: str,
    forced: bool,
    key_constraints: List[str],
) -> None:
    return


def _save_step_memory_background(
    user_id: str,
    step_description: str,
    input_context: str,
    local_constraint: Dict,
    output_schema: Dict,
    result_quality: str,
    error_pattern: str,
    improvement_hint: str,
) -> None:
    return


def _save_user_profile_background(user_id: str, profile_update: Dict) -> None:
    return


# ═══════════════════════════════════════════════════════════════════════════════
# Low-level: call an LLM agent with no tools, return full text
# ═══════════════════════════════════════════════════════════════════════════════

async def _call_llm_agent(
    prompt: str,
    model_name: str,
    user_id: str,
    timeout: int = 120,
) -> str:
    """Call a tool-disabled agent and return stripped text output."""
    agent, mcp_clients = await create_agent_executor(
        disable_tools=True,
        model_name=model_name,
        current_user_id=user_id,
        isolated=True,
    )
    try:
        from agentscope.message import Msg
        user_msg = Msg(name="user", content=prompt, role="user")
        reply = await asyncio.wait_for(agent.reply(user_msg), timeout=timeout)
        text = ""
        if hasattr(reply, "content"):
            if isinstance(reply.content, str):
                text = reply.content
            elif isinstance(reply.content, list):
                parts = []
                for block in reply.content:
                    if hasattr(block, "text"):
                        parts.append(block.text)
                    elif isinstance(block, dict) and "text" in block:
                        parts.append(block["text"])
                    elif isinstance(block, str):
                        parts.append(block)
                text = "\n".join(parts)
            else:
                text = str(reply.content)
        else:
            text = str(reply)
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    finally:
        await close_clients(mcp_clients)


# ═══════════════════════════════════════════════════════════════════════════════
# UserProfile Agent
# Runs in parallel with Planner; writes to context board's "user" field.
# After the full plan finishes, also persists profile to memory (async).
# ═══════════════════════════════════════════════════════════════════════════════

_USER_PROFILE_PROMPT_TEMPLATE = """你是 User-Profile Agent，负责从用户输入中捕捉可能反映用户特征的信息，并从记忆中提取可能与当前任务相关的用户特征。

## 用户输入（最近3轮历史+最新query）
{user_input}

## 从记忆中检索到的用户特征（当前记忆未启用，此处为空）
{memory_context}

## 你的任务
1. 从用户输入中提取"urgent"：用户在这次输入中的第一时间需求（简短描述）
2. 整合记忆中检索到的与当前任务相关的用户特征到"mem"字段

## 输出要求
请严格输出以下 JSON：
{{
  "urgent": "用户的即时需求描述（一句话）",
  "mem": null
}}

只输出 JSON，不要解释。"""


async def _run_user_profile_agent(
    user_id: str,
    user_input: str,
    model_name: str,
    board: Dict[str, Any],
) -> None:
    """Extract user characteristics, write to context board's 'user' field."""
    prompt = _USER_PROFILE_PROMPT_TEMPLATE.format(
        user_input=user_input,
        memory_context="（记忆系统未启用）",
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=30)
        data = _parse_json_output(text)
        if data:
            board["user"]["urgent"] = data.get("urgent")
            board["user"]["mem"] = data.get("mem")
            _save_user_profile_background(user_id, data)
    except Exception as exc:
        logger.debug("[UserProfileAgent] failed (non-critical): %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Planner Agent
# ═══════════════════════════════════════════════════════════════════════════════

_PLANNER_PROMPT_TEMPLATE = """你是 Planner Agent，负责将用户任务拆解为一组线性、可执行的步骤。

你的职责只有一件事：定义「做什么」（宏观任务分解），不涉及「如何做」，不包含执行细节。

## 用户特征（来自 context 黑板）
{user_context}

## 历史记忆参考
{memory_context}

## 用户任务（最近3轮历史+最新query）
{user_input}

{replan_context}

## 可用工具/技能
{tools_desc}

## 输出要求
请严格输出以下 JSON，不要包含任何其他文字：
{{
  "user_goal": "高层任务目标（一句话，不涉及执行细节）",
  "title": "计划标题",
  "description": "计划简要描述",
  "steps": [
    {{
      "step_id": "step_1",
      "title": "步骤标题",
      "description": "只描述任务目标，不包含约束、格式、实现方式",
      "expected_tools": [],
      "expected_skills": [],
      "expected_agents": []
    }}
  ]
}}

规则：
- 每个 step 只描述「做什么」，不写「如何做」
- 不生成局部约束或输出格式
- 如果有 replan_context，必须利用 failure_reason 做修正"""


async def _run_planner(
    user_input: str,
    user_id: str,
    model_name: str,
    tools_desc: str,
    retrieved_memory: Dict,
    board: Dict[str, Any],
    replan_context: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Run Planner Agent, write user_goal + steps to context board, return plan dict."""
    memory_lines = []
    for st in retrieved_memory.get("similar_tasks", []):
        if st:
            memory_lines.append(f"- {st}")
    for fp in retrieved_memory.get("failure_patterns", []):
        if fp:
            memory_lines.append(f"- [失败模式] {fp}")
    memory_context = "\n".join(memory_lines) if memory_lines else "（暂无历史记忆）"

    user_context = json.dumps(board.get("user", {}), ensure_ascii=False)

    replan_section = ""
    if replan_context:
        replan_section = f"""## 重新规划上下文（Replan）
- 是否彻底重新规划: {replan_context.get('complete', False)}
- 失败步骤: Step {replan_context.get('failed_step', '?')}
- 失败原因: {json.dumps(replan_context.get('failure_reason', {}), ensure_ascii=False)}
请从失败步骤开始重新规划，修正导致失败的问题。"""

    prompt = _PLANNER_PROMPT_TEMPLATE.format(
        user_context=user_context,
        memory_context=memory_context,
        user_input=user_input,
        replan_context=replan_section,
        tools_desc=tools_desc,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=90)
        data = _parse_json_output(text)
        if data:
            # Write to context board
            board["plan"]["user_goal"] = data.get("user_goal", "")
            board["plan"]["steps"] = [
                {
                    "step_id": s.get("step_id", f"step_{i+1}"),
                    "description": s.get("description", s.get("title", "")),
                    "output": None,
                }
                for i, s in enumerate(data.get("steps", []))
            ]
        return data
    except Exception as exc:
        logger.error("[Planner] failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Warmup Agent
# Reads context board (user + plan), writes global constraints /
# success_criteria / assumptions back to the board, returns first-step
# local_constraint + expected_output_schema.
# ═══════════════════════════════════════════════════════════════════════════════

_WARMUP_PROMPT_TEMPLATE = """你是 Warmup Agent，负责将任务意图从模糊语言转化为结构化执行语义空间。

## context 黑板（当前状态）
{context_board}

## 用户原始输入（最近3轮历史+最新query）
{user_input}

## 可复用的记忆
{memory_context}

## 你的任务
1. 结合 context 黑板中的用户特征（user.urgent / user.mem），在 Planner 定义的 user_goal 基础上进一步细化用户目标
2. 制定全局约束（hard 约束，后续所有 step 必须遵守）
3. 制定成功标准（QA 用于最终判断任务完成情况，软约束放这里）
4. 列出显式假设（避免隐式错误）
5. 为第一个 SubAgent 制定局部约束和输出结构

## 输出要求
请严格输出以下 JSON：
{{
  "refined_user_goal": "细化后的具体目标",
  "global_constraints": [
    {{
      "constraint": "约束描述",
      "type": "semantic|logic|format",
      "priority": "hard"
    }}
  ],
  "success_criteria": [
    {{
      "criterion": "成功标准描述",
      "check_method": "rule_match|schema_validation|constraint_check|llm_judge"
    }}
  ],
  "assumptions": ["显式假设1", "显式假设2"],
  "next_step_instruction": {{
    "local_constraint": {{
      "constraint": "对第一步的约束",
      "type": "format|logic|semantic",
      "check_method": "rule_match|schema_validation|constraint_check",
      "priority": "hard|soft"
    }},
    "expected_output_schema": {{
      "fields": [],
      "types": {{}},
      "required": [],
      "validation_rules": []
    }}
  }}
}}"""


async def _run_warmup(
    user_input: str,
    user_id: str,
    model_name: str,
    retrieved_memory: Dict,
    board: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Run Warmup Agent; write global constraints / success_criteria / assumptions to board."""
    memory_lines = []
    for st in retrieved_memory.get("similar_tasks", []):
        if st:
            memory_lines.append(f"- [相似任务] {st}")
    memory_context = "\n".join(memory_lines) if memory_lines else "（暂无记忆）"

    prompt = _WARMUP_PROMPT_TEMPLATE.format(
        context_board=_context_board_summary(board),
        user_input=user_input,
        memory_context=memory_context,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=90)
        data = _parse_json_output(text)
        if data:
            # Write to context board
            if data.get("refined_user_goal"):
                board["plan"]["user_goal"] = data["refined_user_goal"]
            board["check"]["global_constraints"] = data.get("global_constraints", [])
            board["check"]["assumptions"] = data.get("assumptions", [])
            board["only_qa"]["success_criteria"] = data.get("success_criteria", [])
        return data
    except Exception as exc:
        logger.error("[Warmup] failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# QA Agent
# 5-step validation in a single LLM call; no constraint-validity check.
# ═══════════════════════════════════════════════════════════════════════════════

_QA_PROMPT_TEMPLATE = """你是 QA Agent，负责验证 SubAgent 的执行结果。在 Forced 模式下你不会被调用。

## context 黑板（公共部分）
{context_board}

## 仅 QA 可见
- 成功标准（success_criteria）: {success_criteria}

## 当前步骤信息
{step_info}

## SubAgent 执行结果
{result}

## 本步骤局部约束（上一个 agent 定义）
- local_constraint: {local_constraint}
- expected_output_schema: {expected_schema}

## 验证流程（按顺序执行，发现错误后继续检查直到遍历完所有条目）

Step 1: 检查 expected_output_schema — 结果是否满足输出结构要求（hard，不满足 → REDO_STEP）
Step 2: 检查 global_constraints（priority=hard）+ local_constraint（priority=hard）— 任意一条不满足 → REDO_STEP
Step 3: 检查 assumptions 一致性 — output 是否与显式假设一致，不满足 → REDO_STEP
Step 4: 检查 local_constraint 中 priority=soft 的部分，使用 LLM judge，confidence < 0.6 视为失败 → REDO_STEP
Step 5: 对 context 中 user 和 plan 整体进行 LLM judge，判断是否偏离整体目标，confidence < 0.8 → REPLAN

注意：
- 先完成所有检查再汇总 verdict，不要提前终止
- 遍历发现所有错误，写入 failure_reason 列表

## 输出要求
请严格输出以下 JSON：
{{
  "verdict": "PASS|REDO_STEP|REPLAN",
  "checks": {{
    "schema_satisfied": true,
    "local_constraint_satisfied": true,
    "global_constraint_satisfied": true,
    "assumptions_consistent": true,
    "soft_constraint_passed": true,
    "goal_alignment_confidence": 1.0
  }},
  "failure_reason": [
    {{
      "type": "execution_error|goal_misalignment",
      "description": "失败原因描述",
      "violated": "被违反的约束或标准",
      "evidence": "证据",
      "confidence": 0.0
    }}
  ]
}}

verdict 规则：
- Step 1/2/3/4 任意失败 → REDO_STEP
- Step 5 失败（goal_alignment_confidence < 0.8）→ REPLAN
- 全部通过 → PASS
- REPLAN 优先级高于 REDO_STEP"""


_QA_FINAL_PROMPT = """你是 QA Agent，对整个计划的执行结果进行最终检查。

## 用户目标
{user_goal}

## 成功标准（success_criteria）
{success_criteria}

## context 黑板（各步骤输出）
{context_board}

## 最终输出
{final_output}

根据 success_criteria 对最终结果进行检查：
- rule_match / schema_validation 类为硬约束，违反则判定失败
- constraint_check / llm_judge 类用 LLM judge

请输出以下 JSON：
{{
  "quality_score": 0.0,
  "success": true,
  "assessment": "简短评价"
}}

quality_score 在 0.0-1.0 之间。"""


async def _run_qa(
    step: Any,
    result: str,
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    model_name: str,
    user_id: str,
) -> Dict[str, Any]:
    """Run QA Agent, return verdict dict."""
    prompt = _QA_PROMPT_TEMPLATE.format(
        context_board=_context_board_summary(board),
        success_criteria=json.dumps(board.get("only_qa", {}).get("success_criteria", []), ensure_ascii=False),
        step_info=json.dumps({"step_id": step.step_id, "title": step.title, "description": step.description}, ensure_ascii=False),
        result=result[:3000],
        local_constraint=json.dumps(local_constraint, ensure_ascii=False),
        expected_schema=json.dumps(expected_schema, ensure_ascii=False),
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60)
        data = _parse_json_output(text)
        if data and "verdict" in data:
            # Normalize failure_reason to always be a list
            if isinstance(data.get("failure_reason"), dict):
                data["failure_reason"] = [data["failure_reason"]]
            elif not isinstance(data.get("failure_reason"), list):
                data["failure_reason"] = []
            return data
    except Exception as exc:
        logger.warning("[QA] failed: %s", exc)
    return {"verdict": "PASS", "checks": {}, "failure_reason": []}


async def _run_qa_final(
    board: Dict[str, Any],
    final_output: str,
    model_name: str,
    user_id: str,
) -> Dict[str, Any]:
    """QA final check at end of plan execution."""
    prompt = _QA_FINAL_PROMPT.format(
        user_goal=board["plan"].get("user_goal", ""),
        success_criteria=json.dumps(board.get("only_qa", {}).get("success_criteria", []), ensure_ascii=False),
        context_board=_context_board_summary(board),
        final_output=final_output[:2000],
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60)
        data = _parse_json_output(text)
        if data and "quality_score" in data:
            return data
    except Exception as exc:
        logger.warning("[QA-final] failed: %s", exc)
    return {"quality_score": 0.8, "success": True, "assessment": "完成"}


# ═══════════════════════════════════════════════════════════════════════════════
# SubAgent step execution
# ═══════════════════════════════════════════════════════════════════════════════

def _build_subagent_instruction(
    step: Any,
    next_step: Optional[Any],
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    retrieved_memory: Dict,
    failure_reason: Optional[List[Dict]] = None,
) -> str:
    """Build full instruction string for SubAgent."""
    parts = []

    # Context board (public view)
    parts.append(f"## context 黑板（共享状态）\n{_context_board_summary(board)}")

    # My step
    parts.append(f"## 我的当前任务\n**步骤 {step.step_order}**: {step.title}\n{step.description or ''}")

    # Local constraint I must follow
    if local_constraint:
        parts.append(f"## 我需要遵守的局部约束\n{json.dumps(local_constraint, ensure_ascii=False, indent=2)}")

    if expected_schema:
        parts.append(f"## 我的输出格式要求\n{json.dumps(expected_schema, ensure_ascii=False, indent=2)}")

    # Failure reason (REDO)
    if failure_reason:
        parts.append(f"## QA 失败原因（请针对性修正）\n{json.dumps(failure_reason, ensure_ascii=False, indent=2)}")

    # Memory reference
    patterns = retrieved_memory.get("relevant_patterns", [])
    if any(p for p in patterns):
        parts.append("## 历史相似执行经验（参考）\n" + "\n".join(f"- {p}" for p in patterns if p))

    # Next step info (for generating next constraint)
    next_step_desc = ""
    if next_step:
        next_step_desc = f"\n下一步任务: {next_step.title} — {next_step.description or ''}"

    parts.append(f"""## 执行要求
1. 聚焦当前步骤目标，不执行其他步骤的任务
2. 必须遵守上述局部约束（如有）和 context 黑板中的 global_constraints
3. 完成执行后，**必须**在输出末尾附加 JSON 块，格式如下：

```json
{{
  "result": "当前步骤执行结果摘要",
  "next_step_instruction": {{
    "local_constraint": {{
      "constraint": "对下一步的约束描述（考虑下一步任务: {next_step_desc}）",
      "type": "format|logic|semantic",
      "check_method": "rule_match|schema_validation|constraint_check",
      "priority": "hard|soft"
    }},
    "expected_output_schema": {{
      "fields": [],
      "types": {{}},
      "required": [],
      "validation_rules": []
    }}
  }}
}}
```

如果这是最后一步，next_step_instruction 字段填 null。""")

    return "\n\n".join(parts)


async def _run_subagent_step(
    step: Any,
    next_step: Optional[Any],
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    retrieved_memory: Dict,
    prepared_history: List[Dict],
    uploaded_files: Optional[List[Dict]],
    model_name: str,
    user_id: str,
    enabled_kb_ids: Optional[List[str]],
    failure_reason: Optional[List[Dict]],
    _cumulative_usage: "_UsageTrackingModel",
    _plan_subagent_log_id: str,
    _all_mcp_clients: List,
) -> AsyncIterator[Dict[str, Any]]:
    """Execute a single SubAgent step, yield SSE events."""
    instruction = _build_subagent_instruction(
        step, next_step, board, local_constraint, expected_schema,
        retrieved_memory, failure_reason,
    )

    step_text = ""
    step_tool_calls: List[Dict] = []
    _step_start = _time.monotonic()

    _step_log_id = await log_writer.start_subagent_log({
        "subagent_name": f"plan_mode:step_{step.step_order}",
        "subagent_type": "plan_step",
        "subagent_id": step.step_id,
        "step_id": step.step_id,
        "step_index": step.step_order,
        "step_title": step.title,
        "model": model_name,
        "parent_subagent_log_id": _plan_subagent_log_id,
        "input_messages": {"instruction": instruction},
    })
    _step_outcome = "success"
    _step_error_msg: Optional[str] = None
    _pool_slot = None
    mcp_clients = []

    try:
        _step_max_iters = int(os.environ.get("PLAN_STEP_MAX_ITERS", "5"))
        from core.llm.agent_pool import AgentPool as _AgentPool
        _pool = _AgentPool.get_instance()
        _use_pool = _pool.is_ready
        if _use_pool:
            try:
                _pooled = await _pool._acquire_direct()
                _pooled.reset()
                agent = _pooled.agent
                agent.max_iters = _step_max_iters
                _pool_slot = _pooled
            except Exception as _pe:
                logger.warning("[plan-exec] pool acquire failed (%s), falling back", _pe)
                _use_pool = False

        if not _use_pool:
            agent, mcp_clients = await create_agent_executor(
                enabled_mcp_ids=None,
                enabled_skill_ids=None,
                enabled_kb_ids=enabled_kb_ids,
                current_user_id=user_id,
                model_name=model_name,
                isolated=True,
                max_iters=_step_max_iters,
            )

        _orig_hook = agent._instance_pre_reply_hooks.get("dynamic_model")
        if _orig_hook:
            async def _patched_hook(ag, kwargs, _oh=_orig_hook, _proxy=_cumulative_usage):
                result = await _oh(ag, kwargs)
                real = ag.model
                if not isinstance(real, _UsageTrackingModel):
                    _proxy._real = real
                    ag.model = _proxy
                return result
            agent._instance_pre_reply_hooks["dynamic_model"] = _patched_hook

        from agentscope.message import Msg
        from core.llm.message_compat import load_session_into_memory

        await load_session_into_memory(prepared_history, agent.memory)

        file_context = _build_file_context(uploaded_files or [])
        if file_context:
            await agent.memory.add(Msg(name="user", content=file_context, role="user"))

        user_msg = Msg(name="user", content=instruction, role="user")

        yield {"type": "plan_step_progress", "step_id": step.step_id, "delta": "正在执行...\n"}

        try:
            _collected_calls: List[Dict] = []
            _pending_log: Dict[str, Dict] = {}

            with log_writer.subagent_scope(_step_log_id, source="subagent"):
                reply_task = asyncio.create_task(agent.reply(user_msg))
            while not reply_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(reply_task), timeout=15)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
                except asyncio.CancelledError:
                    raise

            reply = reply_task.result()

            # Extract tool calls from memory
            try:
                mem_msgs = agent.memory.get_memory()
                if asyncio.iscoroutine(mem_msgs):
                    mem_msgs = await mem_msgs
                for mem_msg in (mem_msgs or []):
                    if hasattr(mem_msg, "has_content_blocks") and mem_msg.has_content_blocks("tool_use"):
                        for block in mem_msg.get_content_blocks("tool_use"):
                            tool_name = block.get("name", "unknown")
                            tool_id = block.get("id", "")
                            tool_args = block.get("input", {})
                            _collected_calls.append(block)
                            _pending_log[tool_id] = {"tool_name": tool_name, "tool_args": tool_args}
                            yield {
                                "type": "tool_call",
                                "step_id": step.step_id,
                                "tool_name": tool_name,
                                "tool_id": tool_id,
                                "tool_args": tool_args,
                            }
                    if hasattr(mem_msg, "has_content_blocks") and mem_msg.has_content_blocks("tool_result"):
                        for block in mem_msg.get_content_blocks("tool_result"):
                            tool_name = block.get("name", "unknown")
                            tool_id = block.get("id", "")
                            output = block.get("output", [])
                            content: Any = output
                            if isinstance(output, list):
                                text_parts = []
                                has_only_text = True
                                for item in output:
                                    if isinstance(item, dict):
                                        tv = item.get("text")
                                        if tv is not None:
                                            text_parts.append(str(tv))
                                        else:
                                            has_only_text = False
                                            break
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                    else:
                                        has_only_text = False
                                        break
                                if has_only_text and text_parts:
                                    joined = "\n".join(text_parts)
                                    try:
                                        content = json.loads(joined)
                                    except (json.JSONDecodeError, ValueError):
                                        content = joined
                            elif isinstance(output, str):
                                try:
                                    content = json.loads(output)
                                except (json.JSONDecodeError, ValueError):
                                    content = output
                            _call = _pending_log.pop(tool_id, {})
                            log_writer.schedule_tool_call_write({
                                "tool_name": _call.get("tool_name") or tool_name,
                                "tool_call_id": tool_id,
                                "tool_args": _call.get("tool_args"),
                                "tool_result": content,
                                "status": "success",
                                "source": "subagent",
                                "subagent_log_id": _step_log_id,
                            })
                            yield {
                                "type": "tool_result",
                                "step_id": step.step_id,
                                "tool_name": tool_name,
                                "tool_id": tool_id,
                                "result": content,
                            }
            except Exception as _mem_exc:
                logger.warning("[plan-exec] Failed to extract tool calls from memory: %s", _mem_exc)

            # Extract text from reply
            if hasattr(reply, "content"):
                if isinstance(reply.content, str):
                    step_text = reply.content
                elif isinstance(reply.content, list):
                    parts = []
                    for block in reply.content:
                        if hasattr(block, "text"):
                            parts.append(block.text)
                        elif isinstance(block, dict) and "text" in block:
                            parts.append(block["text"])
                        elif isinstance(block, str):
                            parts.append(block)
                    step_text = "\n".join(parts)
                else:
                    step_text = str(reply.content)
            else:
                step_text = str(reply)

            step_text = re.sub(r"<think>.*?</think>", "", step_text, flags=re.DOTALL).strip()
            step_tool_calls = _collected_calls

            yield {"type": "plan_step_progress", "step_id": step.step_id, "delta": step_text}

        except asyncio.TimeoutError:
            step_text = "步骤执行被取消"
            _step_outcome = "failed"
            _step_error_msg = "timeout"
        except Exception as _reply_exc:
            _err = f"{type(_reply_exc).__name__}: {_reply_exc}".strip(": ")
            step_text = f"执行出错: {_err or type(_reply_exc).__name__}"
            _step_outcome = "failed"
            _step_error_msg = _err

        _all_mcp_clients.extend(mcp_clients)

    except Exception as step_exc:
        logger.exception("Step %s agent setup failed", step.step_id)
        step_text = f"步骤初始化失败: {step_exc}"
        _step_outcome = "failed"
        _step_error_msg = str(step_exc)

    finally:
        if _pool_slot is not None:
            try:
                _pool_slot._lock.release()
            except Exception:
                pass
        await log_writer.finish_subagent_log(
            _step_log_id,
            status=_step_outcome,
            output_content=step_text,
            intermediate_steps=step_tool_calls[:100] if step_tool_calls else None,
            tool_calls_count=len(step_tool_calls),
            duration_ms=int((_time.monotonic() - _step_start) * 1000),
            error_message=_step_error_msg,
        )

    yield {
        "type": "_step_result",
        "step_id": step.step_id,
        "step_text": step_text,
        "step_tool_calls": step_tool_calls,
        "outcome": _step_outcome,
        "error_msg": _step_error_msg,
    }


def _extract_next_step_instruction(step_text: str) -> Tuple[str, Dict]:
    """Split step_text into (narrative_text, next_step_instruction dict)."""
    match = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", step_text, re.DOTALL)
    if not match:
        last_brace = step_text.rfind("}")
        first_brace = step_text.rfind("{", 0, last_brace)
        if first_brace != -1 and last_brace != -1:
            candidate = step_text[first_brace:last_brace + 1]
            try:
                data = json.loads(candidate)
                narrative = step_text[:first_brace].strip()
                return narrative, data
            except Exception:
                pass
        return step_text, {}

    candidate = match.group(1)
    try:
        data = json.loads(candidate)
        narrative = step_text[:match.start()].strip()
        return narrative, data
    except Exception:
        return step_text, {}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Generate Plan  (UserProfile + Planner run in parallel)
# ═══════════════════════════════════════════════════════════════════════════════

async def astream_generate_plan(
    task_description: str,
    user_id: str,
    db: Session,
    model_name: str = "qwen",
    enabled_mcp_ids: Optional[List[str]] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_kb_ids: Optional[List[str]] = None,
    enabled_agent_ids: Optional[List[str]] = None,
    session_messages: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Phase 1: UserProfile + Planner in parallel, then persist plan.

    Yields SSE events:
    - plan_generating  {delta: str}
    - plan_generated   {plan_id, title, description, steps: [...]}
    - plan_error       {error: str}
    """
    visible_agents = _load_visible_agents(db, user_id, enabled_agent_ids)
    tools_desc = _build_tools_description(enabled_mcp_ids, enabled_skill_ids, visible_agents)

    # Build the context board for this generation phase
    # (will be re-built fresh at execution time; here just for planner context)
    board = _make_context_board()

    yield {"type": "plan_generating", "delta": "正在分析用户特征并查询历史记忆...\n"}

    # Run UserProfile Agent and memory retrieval in parallel
    retrieved_memory, _ = await asyncio.gather(
        _retrieve_plan_memory(user_id, task_description),
        _run_user_profile_agent(user_id, task_description, model_name, board),
    )

    yield {"type": "plan_generating", "delta": "正在制定执行计划...\n"}

    try:
        plan_data = await _run_planner(
            user_input=task_description,
            user_id=user_id,
            model_name=model_name,
            tools_desc=tools_desc,
            retrieved_memory=retrieved_memory,
            board=board,
        )

        if not plan_data:
            yield {"type": "plan_error", "error": "Planner 输出格式解析失败，请重试"}
            return

        # Validate tool/skill/agent references
        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
        _valid_agents = {a.get("agent_id") for a in visible_agents}
        for step_data in plan_data.get("steps", []):
            if _valid_tools is not None:
                step_data["expected_tools"] = [
                    t for t in (step_data.get("expected_tools") or [])
                    if t in _valid_tools
                ]
            if _valid_skills is not None:
                step_data["expected_skills"] = [
                    s for s in (step_data.get("expected_skills") or [])
                    if s in _valid_skills
                ]
            step_data["expected_agents"] = [
                a for a in (step_data.get("expected_agents") or [])
                if a in _valid_agents
            ]

        # Persist to DB
        svc = PlanService(db)
        plan = svc.create_plan(
            user_id=user_id,
            title=plan_data.get("title", "未命名计划"),
            description=plan_data.get("description", ""),
            task_input=task_description,
            steps=plan_data.get("steps", []),
        )

        agent_name_map = {a.get("agent_id"): a.get("name", a.get("agent_id", "")) for a in visible_agents} if visible_agents else {}

        extra: Dict[str, Any] = {
            "user_goal": plan_data.get("user_goal", task_description),
            "retrieved_memory": retrieved_memory,
            # Persist user profile extracted during generation for reuse at execution
            "user_profile": board.get("user", {}),
        }
        if uploaded_files:
            extra["uploaded_files"] = uploaded_files
        if agent_name_map:
            extra["agent_name_map"] = agent_name_map
        svc.update_plan(plan.plan_id, extra_data=extra)

        event = {"type": "plan_generated", **PlanService.plan_to_dict(plan)}
        if agent_name_map:
            event["agent_name_map"] = agent_name_map
        yield event

    except Exception as exc:
        logger.exception("Plan generation failed")
        yield {"type": "plan_error", "error": f"计划生成失败: {exc}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Execute Plan  (Warmup → SubAgent + QA loop)
# ═══════════════════════════════════════════════════════════════════════════════

async def astream_execute_plan(
    plan_id: str,
    user_id: str,
    db: Session,
    model_name: str = "qwen",
    enabled_mcp_ids: Optional[List[str]] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_kb_ids: Optional[List[str]] = None,
    enabled_agent_ids: Optional[List[str]] = None,
    session_messages: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Phase 2: Warmup → SubAgent+QA pipeline.

    Yields SSE events:
    - plan_step_start     {step_id, step_order, title}
    - plan_step_progress  {step_id, delta: str}
    - tool_call / tool_result  {step_id, ...}
    - plan_step_qa        {step_id, verdict, checks}
    - plan_step_complete  {step_id, status, summary}
    - plan_error          {plan_id, step_id?, error}
    - plan_complete       {plan_id, status, summary, ...}
    """
    logger.warning("[plan-exec] astream_execute_plan called for plan_id=%s", plan_id)

    svc = PlanService(db)
    plan = svc.get_plan(plan_id, user_id)
    if not plan:
        yield {"type": "plan_error", "plan_id": plan_id, "error": "计划不存在"}
        return

    if plan.status not in ("approved", "running"):
        yield {"type": "plan_error", "plan_id": plan_id, "error": f"计划状态 '{plan.status}' 不可执行"}
        return

    plan_meta = plan.extra_data or {}
    if not uploaded_files and isinstance(plan_meta, dict):
        uploaded_files = plan_meta.get("uploaded_files")

    user_goal = plan_meta.get("user_goal", plan.task_input)
    plan_retrieved_memory = plan_meta.get("retrieved_memory", {})
    saved_user_profile = plan_meta.get("user_profile", {})

    svc.update_plan(plan_id, status="running")
    completed_count = 0
    cancelled = False

    _chat_hint = plan_meta.get("chat_id") if isinstance(plan_meta, dict) else None
    _log_ctx = LogContext(user_id=user_id or None, chat_id=_chat_hint)
    _log_ctx.__enter__()

    _plan_run_start = _time.monotonic()
    _plan_subagent_log_id = await log_writer.start_subagent_log({
        "subagent_name": "plan_mode",
        "subagent_type": "plan_mode",
        "subagent_id": plan_id,
        "plan_id": plan_id,
        "model": model_name,
        "step_title": plan.title,
        "input_messages": {
            "task_input": plan.task_input,
            "total_steps": plan.total_steps,
        },
    })
    _plan_tool_count = 0
    _all_mcp_clients: List = []
    _cumulative_usage = _UsageTrackingModel(None)

    prepared_history = await _prepare_history(session_messages or [], model_name)

    # ── Build fresh context board ─────────────────────────────────────────────
    board = _make_context_board()

    # Restore user profile from generation phase
    if saved_user_profile:
        board["user"].update(saved_user_profile)

    # Seed board with planner's output (from DB)
    board["plan"]["user_goal"] = user_goal
    board["plan"]["steps"] = [
        {
            "step_id": s.step_id,
            "description": s.description or s.title,
            "output": None,
        }
        for s in plan.steps
    ]

    # ── Warmup Phase ──────────────────────────────────────────────────────────
    yield {"type": "plan_step_progress", "step_id": None, "delta": "Warmup Agent 正在初始化执行语义空间...\n"}

    warmup_memory = {
        "similar_tasks": plan_retrieved_memory.get("similar_tasks", []),
    }

    warmup_result = await _run_warmup(
        user_input=plan.task_input,
        user_id=user_id,
        model_name=model_name,
        retrieved_memory=warmup_memory,
        board=board,
    )

    if warmup_result is None:
        # Degrade gracefully
        warmup_result = {
            "refined_user_goal": user_goal,
            "global_constraints": [],
            "success_criteria": [],
            "assumptions": [],
            "next_step_instruction": {"local_constraint": {}, "expected_output_schema": {}},
        }
        board["plan"]["user_goal"] = user_goal

    # First step's local constraint comes from Warmup
    first_instr = warmup_result.get("next_step_instruction") or {}
    current_local_constraint = first_instr.get("local_constraint", {})
    current_expected_schema = first_instr.get("expected_output_schema", {})

    # ── Step execution state ──────────────────────────────────────────────────
    step_summaries: List[str] = []
    last_step_text: str = ""

    # Control flow counters
    local_replan_count = 0   # replans from current failure point
    global_reset_count = 0   # full plan resets
    forced_mode = False

    try:
        step_idx = 0
        steps = list(plan.steps)

        while step_idx < len(steps):
            step = steps[step_idx]

            logger.warning("[plan-exec] === Step %d/%d: %s (forced=%s) ===",
                           step_idx + 1, len(steps), step.title, forced_mode)

            if cancelled:
                svc.update_step(step.step_id, status="skipped")
                step_idx += 1
                continue

            db.refresh(plan)
            if plan.status == "cancelled":
                cancelled = True
                svc.update_step(step.step_id, status="skipped")
                step_idx += 1
                continue

            yield {
                "type": "plan_step_start",
                "step_id": step.step_id,
                "step_order": step.step_order,
                "title": step.title,
            }
            yield {"type": "heartbeat"}
            svc.update_step(step.step_id, status="running", started_at=datetime.utcnow())

            step_memory = await _retrieve_step_memory(user_id, step.description or step.title)
            next_step = steps[step_idx + 1] if step_idx + 1 < len(steps) else None

            # ── REDO loop ──────────────────────────────────────────────────────
            redo_count = 0
            redo_failure_reason: Optional[List[Dict]] = None
            step_text = ""
            step_tool_calls: List[Dict] = []
            qa_verdict = "PASS"
            qa_data: Dict = {}

            while True:
                step_text = ""
                step_tool_calls = []

                async for event in _run_subagent_step(
                    step=step,
                    next_step=next_step,
                    board=board,
                    local_constraint=current_local_constraint,
                    expected_schema=current_expected_schema,
                    retrieved_memory=step_memory,
                    prepared_history=prepared_history,
                    uploaded_files=uploaded_files,
                    model_name=model_name,
                    user_id=user_id,
                    enabled_kb_ids=enabled_kb_ids,
                    failure_reason=redo_failure_reason,
                    _cumulative_usage=_cumulative_usage,
                    _plan_subagent_log_id=_plan_subagent_log_id,
                    _all_mcp_clients=_all_mcp_clients,
                ):
                    if event["type"] == "_step_result":
                        step_text = event["step_text"]
                        step_tool_calls = event["step_tool_calls"]
                    else:
                        yield event

                _plan_tool_count += len(step_tool_calls)

                narrative_text, subagent_json = _extract_next_step_instruction(step_text)

                if forced_mode:
                    qa_verdict = "PASS"
                    qa_data = {"verdict": "PASS", "forced": True}
                else:
                    qa_data = await _run_qa(
                        step=step,
                        result=narrative_text or step_text,
                        board=board,
                        local_constraint=current_local_constraint,
                        expected_schema=current_expected_schema,
                        model_name=model_name,
                        user_id=user_id,
                    )
                    qa_verdict = qa_data.get("verdict", "PASS")

                yield {
                    "type": "plan_step_qa",
                    "step_id": step.step_id,
                    "verdict": qa_verdict,
                    "checks": qa_data.get("checks", {}),
                    "forced": forced_mode,
                }

                if qa_verdict == "PASS":
                    break

                if qa_verdict == "REDO_STEP":
                    redo_count += 1
                    redo_failure_reason = qa_data.get("failure_reason", [])
                    logger.warning("[QA] REDO_STEP step=%d redo=%d", step.step_order, redo_count)
                    if redo_count >= _MAX_REDO_PER_STEP:
                        # Escalate to local REPLAN
                        qa_verdict = "REPLAN"
                        logger.warning("[QA] REDO limit reached → escalate to REPLAN")
                        break
                    yield {"type": "plan_step_progress", "step_id": step.step_id,
                           "delta": f"\nQA 验证未通过，正在重试 ({redo_count}/{_MAX_REDO_PER_STEP})...\n"}
                    continue

                if qa_verdict == "REPLAN":
                    break

                break  # unknown verdict → treat as PASS

            # ── Handle REPLAN ─────────────────────────────────────────────────
            if qa_verdict == "REPLAN" and not forced_mode:
                local_replan_count += 1
                logger.warning("[plan-exec] REPLAN triggered at step %d (local_count=%d, global_reset=%d)",
                               step.step_order, local_replan_count, global_reset_count)

                if local_replan_count > _MAX_LOCAL_REPLAN:
                    # Trigger full global reset
                    global_reset_count += 1
                    if global_reset_count > _MAX_GLOBAL_RESET:
                        # Enter Forced mode
                        logger.warning("[plan-exec] Global reset limit exceeded → Forced mode")
                        forced_mode = True
                        local_replan_count = 0
                        yield {"type": "plan_step_progress", "step_id": step.step_id,
                               "delta": "\n已进入 Forced 模式，QA 验证将跳过，继续执行至结束。\n"}
                        # Fall through to finalize current step
                    else:
                        # Full global reset: replan from scratch
                        logger.warning("[plan-exec] Full global reset #%d", global_reset_count)
                        local_replan_count = 0
                        yield {"type": "plan_step_progress", "step_id": step.step_id,
                               "delta": f"\n触发全局重新规划（第 {global_reset_count} 次整体重置）...\n"}

                        replan_memory = await _retrieve_plan_memory(user_id, plan.task_input)
                        replan_ctx = {
                            "complete": True,
                            "failed_step": step.step_order,
                            "failure_reason": qa_data.get("failure_reason", []),
                        }
                        new_plan_data = await _run_planner(
                            user_input=plan.task_input,
                            user_id=user_id,
                            model_name=model_name,
                            tools_desc=_build_tools_description(enabled_mcp_ids, enabled_skill_ids),
                            retrieved_memory=replan_memory,
                            board=board,
                            replan_context=replan_ctx,
                        )

                        if new_plan_data and new_plan_data.get("steps"):
                            remaining_steps = new_plan_data["steps"]
                            _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
                            _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
                            _valid_agents = {a.get("agent_id") for a in _load_visible_agents(db, user_id, enabled_agent_ids)}
                            for sd in remaining_steps:
                                if _valid_tools is not None:
                                    sd["expected_tools"] = [t for t in (sd.get("expected_tools") or []) if t in _valid_tools]
                                if _valid_skills is not None:
                                    sd["expected_skills"] = [s for s in (sd.get("expected_skills") or []) if s in _valid_skills]
                                sd["expected_agents"] = [a for a in (sd.get("expected_agents") or []) if a in _valid_agents]

                            all_new_steps = [
                                {"title": s.get("title", f"步骤{i+1}"), "description": s.get("description", ""),
                                 "expected_tools": s.get("expected_tools", []),
                                 "expected_skills": s.get("expected_skills", []),
                                 "expected_agents": s.get("expected_agents", [])}
                                for i, s in enumerate(remaining_steps)
                            ]
                            updated_plan = svc.replace_steps(plan_id, all_new_steps)
                            if updated_plan:
                                db.refresh(updated_plan)
                                steps = list(updated_plan.steps)
                                step_idx = 0
                                # Reset board steps
                                board["plan"]["steps"] = [
                                    {"step_id": s.step_id, "description": s.description or s.title, "output": None}
                                    for s in steps
                                ]
                                svc.update_step(steps[step_idx].step_id, status="running", started_at=datetime.utcnow())
                                continue
                else:
                    # Local replan: re-plan from current failure point
                    yield {"type": "plan_step_progress", "step_id": step.step_id,
                           "delta": f"\nQA 触发局部重新规划（从当前步骤重做）...\n"}

                    replan_memory = await _retrieve_plan_memory(user_id, plan.task_input)
                    replan_ctx = {
                        "complete": False,
                        "failed_step": step.step_order,
                        "failure_reason": qa_data.get("failure_reason", []),
                    }
                    new_plan_data = await _run_planner(
                        user_input=plan.task_input,
                        user_id=user_id,
                        model_name=model_name,
                        tools_desc=_build_tools_description(enabled_mcp_ids, enabled_skill_ids),
                        retrieved_memory=replan_memory,
                        board=board,
                        replan_context=replan_ctx,
                    )

                    if new_plan_data and new_plan_data.get("steps"):
                        remaining_steps = new_plan_data["steps"]
                        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
                        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
                        _valid_agents = {a.get("agent_id") for a in _load_visible_agents(db, user_id, enabled_agent_ids)}
                        for sd in remaining_steps:
                            if _valid_tools is not None:
                                sd["expected_tools"] = [t for t in (sd.get("expected_tools") or []) if t in _valid_tools]
                            if _valid_skills is not None:
                                sd["expected_skills"] = [s for s in (sd.get("expected_skills") or []) if s in _valid_skills]
                            sd["expected_agents"] = [a for a in (sd.get("expected_agents") or []) if a in _valid_agents]

                        completed_steps_so_far = steps[:step_idx]
                        all_new_steps = [
                            {"title": s.get("title", f"步骤{i+1}"), "description": s.get("description", ""),
                             "expected_tools": s.get("expected_tools", []),
                             "expected_skills": s.get("expected_skills", []),
                             "expected_agents": s.get("expected_agents", [])}
                            for i, s in enumerate(remaining_steps)
                        ]
                        updated_plan = svc.replace_steps(plan_id, [
                            {"title": s.title, "description": s.description,
                             "expected_tools": s.expected_tools or [],
                             "expected_skills": s.expected_skills or [],
                             "expected_agents": s.expected_agents or []}
                            for s in completed_steps_so_far
                        ] + all_new_steps)
                        if updated_plan:
                            db.refresh(updated_plan)
                            steps = list(updated_plan.steps)
                            # Reset board steps for remaining
                            board["plan"]["steps"] = [
                                {"step_id": s.step_id, "description": s.description or s.title,
                                 "output": board["plan"]["steps"][i]["output"] if i < len(board["plan"]["steps"]) else None}
                                for i, s in enumerate(steps)
                            ]
                            svc.update_step(steps[step_idx].step_id, status="running", started_at=datetime.utcnow())
                            continue

            # ── Finalize step ─────────────────────────────────────────────────
            narrative_text, subagent_json = _extract_next_step_instruction(step_text)
            display_text = narrative_text if narrative_text else step_text

            # Write step output to context board (only after QA PASS or Forced)
            step_result_summary = subagent_json.get("result", "") if subagent_json else ""
            for board_step in board["plan"]["steps"]:
                if board_step["step_id"] == step.step_id:
                    board_step["output"] = step_result_summary or _extract_summary(display_text)
                    break

            if display_text:
                last_step_text = display_text

            summary = _extract_summary(display_text, max_len=200)
            step_summaries.append(f"步骤{step.step_order}({step.title}): {summary}")

            # Extract next step instruction
            next_instr = subagent_json.get("next_step_instruction") if subagent_json else None
            if next_instr and isinstance(next_instr, dict):
                current_local_constraint = next_instr.get("local_constraint", {})
                current_expected_schema = next_instr.get("expected_output_schema", {})
            else:
                current_local_constraint = {}
                current_expected_schema = {}

            _final_step_status = (
                "success"
                if not step_text.startswith("执行出错") and not step_text.startswith("步骤初始化失败")
                else "failed"
            )

            svc.update_step(
                step.step_id,
                status=_final_step_status,
                result_summary=summary,
                ai_output=display_text[:5000],
                tool_calls_log=step_tool_calls,
                completed_at=datetime.utcnow(),
                local_constraint=current_local_constraint or None,
                next_step_instruction=next_instr or None,
            )

            if _final_step_status == "success":
                completed_count += 1

            _save_step_memory_background(
                user_id=user_id,
                step_description=step.description or step.title,
                input_context="",
                local_constraint=current_local_constraint,
                output_schema=current_expected_schema,
                result_quality="high" if _final_step_status == "success" and qa_verdict == "PASS" else "low",
                error_pattern=str(qa_data.get("failure_reason", [{}])[0].get("type", "")) if qa_verdict != "PASS" else "",
                improvement_hint=str(qa_data.get("failure_reason", [{}])[0].get("description", "")) if qa_verdict != "PASS" else "",
            )

            yield {
                "type": "plan_step_complete",
                "step_id": step.step_id,
                "status": _final_step_status,
                "summary": summary,
            }

            if _final_step_status == "failed":
                yield {
                    "type": "plan_error",
                    "plan_id": plan_id,
                    "step_id": step.step_id,
                    "error": display_text,
                }

            step_idx += 1

        # ── Plan complete ─────────────────────────────────────────────────────
        logger.warning("[plan-exec] === All steps done. completed=%d/%d forced=%s ===",
                       completed_count, len(steps), forced_mode)

        final_status = "completed" if completed_count == len(steps) else "failed"
        if cancelled:
            final_status = "cancelled"

        overall_summary = f"共 {len(steps)} 个步骤，完成 {completed_count} 个"
        result_text = last_step_text

        # QA final check (always runs, even in forced mode — just doesn't trigger replans)
        qa_final = await _run_qa_final(
            board=board,
            final_output=result_text,
            model_name=model_name,
            user_id=user_id,
        )
        quality_score = qa_final.get("quality_score", 0.8)
        task_success = qa_final.get("success", True) and final_status == "completed"
        if forced_mode:
            task_success = None

        svc.update_plan(
            plan_id,
            status=final_status,
            completed_steps=completed_count,
            result_summary=result_text[:2000] if result_text else overall_summary,
        )

        _save_task_memory_background(
            user_id=user_id,
            user_goal=board["plan"].get("user_goal", ""),
            plan_steps=[s.title for s in steps],
            success=bool(task_success) if task_success is not None else False,
            quality_score=quality_score,
            failure_reason="",
            final_solution_summary=result_text[:500],
            forced=forced_mode,
            key_constraints=[
                c.get("constraint", "")
                for c in board["check"].get("global_constraints", [])
            ],
        )

        records = _cumulative_usage.usage_records
        total_prompt = sum(r.get("prompt_tokens", 0) for r in records)
        total_completion = sum(r.get("completion_tokens", 0) for r in records)
        exec_usage = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "llm_call_count": len(records),
        }

        yield {
            "type": "plan_complete",
            "plan_id": plan_id,
            "status": final_status,
            "summary": overall_summary,
            "result_text": result_text,
            "completed_steps": completed_count,
            "total_steps": plan.total_steps,
            "usage": exec_usage,
            "quality_score": quality_score,
            "forced_mode": forced_mode,
        }

        await log_writer.finish_subagent_log(
            _plan_subagent_log_id,
            status="success" if final_status == "completed" else final_status,
            output_content=result_text or overall_summary,
            intermediate_steps=step_summaries,
            token_usage=exec_usage,
            tool_calls_count=_plan_tool_count,
            duration_ms=int((_time.monotonic() - _plan_run_start) * 1000),
        )

    except Exception as exc:
        logger.exception("Plan execution failed")
        svc.update_plan(plan_id, status="failed", result_summary=str(exc))
        await log_writer.finish_subagent_log(
            _plan_subagent_log_id,
            status="failed",
            error_message=str(exc),
            duration_ms=int((_time.monotonic() - _plan_run_start) * 1000),
        )
        yield {"type": "plan_error", "plan_id": plan_id, "error": str(exc)}

    finally:
        _terminate_mcp_processes(_all_mcp_clients)
        _all_mcp_clients.clear()
        try:
            _log_ctx.__exit__(None, None, None)
        except Exception:
            pass
