"""LLM agent helpers for plan mode: UserProfile, Planner, Intent Classification, Warmup, QA."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.llm.agent_factory import create_agent_executor
from core.llm.mcp_manager import close_clients
from routing.subagents.plan_store import (
    _role_model,
    _parse_json_output,
    _context_board_summary,
    _mem0_enabled,
)


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
# ═══════════════════════════════════════════════════════════════════════════════

_USER_PROFILE_PROMPT_TEMPLATE = """你是 User-Profile Agent，负责从用户最新输入中捕捉可能反映用户特征的信息，并从记忆中提取可能与当前任务相关的用户特征。

注意：请**只基于用户本次最新输入**提取特征，不要考虑历史上下文，以确保提取的特征反映用户当前的真实意图。

## 用户最新输入
{user_input}

## 从记忆中检索到的与当前任务相关的用户特征（top-4）
{memory_context}

## 你的任务
1. 从用户最新输入中提取"urgent"：用户在这次输入中最核心的即时需求（一句话，聚焦偏好/认知水平/风格倾向等高稳定性特征）
2. 将记忆中检索到的相关用户特征整合到"mem"字段（如果记忆为空则为 null）

## 输出要求
请严格输出以下 JSON（不要输出其他内容）：
{{
  "urgent": "用户的即时需求描述（一句话，不超过50字）",
  "mem": "从记忆中提炼的与本次任务相关的用户特征摘要（或 null）"
}}"""


async def _run_user_profile_agent(
    user_id: str,
    user_input: str,
    model_name: str,
    board: Dict[str, Any],
) -> None:
    """Extract user characteristics, write to context board's 'user' field."""
    from routing.subagents.plan_memory import _save_user_profile_background
    memory_context = "（记忆系统未启用或暂无相关记录）"
    if _mem0_enabled() and user_id:
        try:
            from core.llm.memory import retrieve_memories
            raw = await retrieve_memories(user_id, user_input, limit=4, min_score=0.4)
            if raw:
                memory_context = raw
        except Exception as exc:
            logger.debug("[UserProfileAgent] memory retrieval failed: %s", exc)

    prompt = _USER_PROFILE_PROMPT_TEMPLATE.format(
        user_input=user_input,
        memory_context=memory_context,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=30)
        data = _parse_json_output(text)
        if data:
            board["user"]["urgent"] = data.get("urgent")
            board["user"]["mem"] = data.get("mem")
            _save_user_profile_background(user_id, data, model_name=model_name)
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
      "brief_description": "步骤任务的一句话简述（10字以内）",
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


_INTENT_CLASSIFY_PROMPT = """你是意图分类助手。根据用户的回复，判断他们的意图。

## 用户回复
{user_reply}

## 判断规则
- 如果用户的意思是"同意/确认/开始执行当前计划"，输出 "confirm"
- 如果用户的意思是"不满意/想重新规划/有新的建议/修改计划"，输出 "replan"

只输出以下 JSON：
{{"intent": "confirm|replan"}}"""


async def _classify_user_intent(user_reply: str, model_name: str, user_id: str) -> str:
    """Classify user reply as 'confirm' (execute plan) or 'replan' (redo planning).

    Returns 'confirm' or 'replan'.
    """
    prompt = _INTENT_CLASSIFY_PROMPT.format(user_reply=user_reply[:500])
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=20)
        data = _parse_json_output(text)
        if data and data.get("intent") in ("confirm", "replan"):
            return data["intent"]
    except Exception as exc:
        logger.debug("[IntentClassify] failed: %s", exc)
    return "confirm"


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

    graph_plans = retrieved_memory.get("graph_plans", [])
    if graph_plans:
        memory_lines.append("\n### 历史计划骨架（来自 Graph，含步骤依赖链）")
        for gp in graph_plans[:5]:
            pid = gp.get("plan_id", "?")
            desc = gp.get("description", "")
            status = gp.get("status", "")
            steps = gp.get("steps", [])
            suggestion = gp.get("suggestion", "")
            step_chain = " → ".join(s.get("title", "") for s in steps) or "（无步骤）"
            memory_lines.append(f"- [plan_id={pid}, {status}] {desc}")
            memory_lines.append(f"  步骤链: {step_chain}")
            if suggestion:
                memory_lines.append(f"  优化建议: {suggestion[:150]}")

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
            board["plan"]["user_goal"] = data.get("user_goal", "")
            board["plan"]["steps"] = [
                {
                    "step_id": s.get("step_id", f"step_{i+1}"),
                    "brief_description": s.get("brief_description", ""),
                    "description": s.get("description", s.get("title", "")),
                    "output": None,
                    "suggestion": None,
                    "tool_use_trace": [],
                }
                for i, s in enumerate(data.get("steps", []))
            ]
        return data
    except Exception as exc:
        logger.error("[Planner] failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Warmup Agent
# ═══════════════════════════════════════════════════════════════════════════════

_WARMUP_PROMPT_TEMPLATE = """你是 Warmup Agent，负责将任务意图从模糊语言转化为结构化执行语义空间。

## context 黑板（当前状态）
{context_board}

## 用户原始输入
{user_input}

## 可复用的记忆
{memory_context}

## 你的任务
1. 结合 context 黑板中的用户特征（user.urgent / user.mem），在 Planner 定义的 user_goal 基础上进一步细化用户目标
2. 制定全局约束（hard 约束，后续所有 step 必须遵守；软约束也放这里，priority 标为 soft）
3. 列出显式假设（避免隐式错误）
4. 为第一个 SubAgent 制定局部约束和输出结构

## 输出要求
请严格输出以下 JSON，不要包含其他字段：
{{
  "refined_user_goal": "细化后的具体目标",
  "global_constraints": [
    {{
      "constraint": "约束描述",
      "type": "semantic|logic|format",
      "priority": "hard|soft"
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
    """Run Warmup Agent; write global constraints and assumptions to board."""
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
            if data.get("refined_user_goal"):
                board["plan"]["user_goal"] = data["refined_user_goal"]
            board["check"]["global_constraints"] = data.get("global_constraints", [])
            board["check"]["assumptions"] = data.get("assumptions", [])
        return data
    except Exception as exc:
        logger.error("[Warmup] failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# QA Agent
# ═══════════════════════════════════════════════════════════════════════════════

_QA_PROMPT_TEMPLATE = """你是 QA Agent，负责验证 SubAgent 的执行结果。

## context 黑板（公共部分）
{context_board}

## 当前步骤信息
{step_info}

## SubAgent 执行结果
{result}

## 本步骤局部约束（上一个 agent 定义）
- local_constraint: {local_constraint}
- expected_output_schema: {expected_schema}

## 验证流程（按顺序执行，发现错误后继续检查直到遍历完所有条目）

Step 1: 检查 expected_output_schema — 结果是否满足输出结构要求（hard，不满足 → REDO）
Step 2: 检查 context.check.global_constraints（priority=hard）+ local_constraint（priority=hard）— 任意一条不满足 → REDO
Step 3: 检查 context.check.assumptions 一致性 — output 是否与显式假设一致，不满足 → REDO
Step 4: 检查 local_constraint 中 priority=soft 的部分，使用 LLM judge，confidence < 0.6 视为失败 → REDO
Step 5: 对 context 中 user 和 plan 整体进行 LLM judge，判断是否偏离整体目标，confidence < 0.8 → REPLAN

注意：
- 先完成所有检查再汇总 verdict，不要提前终止
- 遍历发现所有错误，写入 failure_reason 列表

## 输出要求
请严格输出以下 JSON：
{{
  "verdict": "PASS|REDO|REPLAN",
  "failure_reason": [
    {{
      "local_constraint_satisfied": true,
      "global_constraint_satisfied": true,
      "description": "失败原因描述",
      "confidence": 0.0,
      "suggestion": "针对此失败给出的优化建议，供重做的 agent 或 planner 参考"
    }}
  ]
}}

注意：Step 5（goal_alignment_confidence）请将其作为一条 failure_reason 条目的 confidence 字段反映，local_constraint_satisfied 和 global_constraint_satisfied 均填 true，description 写明目标偏离情况。

verdict 规则：
- Step 1/2/3/4 任意失败 → REDO
- Step 5 失败（goal_alignment_confidence < 0.8）→ REPLAN
- 全部通过 → PASS
- REPLAN 优先级高于 REDO"""


_QA_FINAL_PROMPT = """你是 QA Agent，对整个计划的执行结果进行最终检查。

## 用户目标
{user_goal}

## context 黑板（含全局约束和假设）
{context_board}

## 最终输出
{final_output}

根据 context.check 中的 global_constraints 和 assumptions，结合最终结果，给出针对整个计划的优化建议。
无论计划执行得好还是差，都要给出切实可行的改进方向。

请严格输出以下 JSON：
{{
  "plan_suggestion": "针对整个计划的优化建议"
}}"""


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
        step_info=json.dumps({"step_id": step.step_id, "title": step.title, "description": step.description}, ensure_ascii=False),
        result=result[:3000],
        local_constraint=json.dumps(local_constraint, ensure_ascii=False),
        expected_schema=json.dumps(expected_schema, ensure_ascii=False),
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60)
        data = _parse_json_output(text)
        if data and "verdict" in data:
            if isinstance(data.get("failure_reason"), dict):
                data["failure_reason"] = [data["failure_reason"]]
            elif not isinstance(data.get("failure_reason"), list):
                data["failure_reason"] = []
            return data
    except Exception as exc:
        logger.warning("[QA] failed: %s", exc)
    return {"verdict": "PASS", "failure_reason": []}


async def _run_qa_final(
    board: Dict[str, Any],
    final_output: str,
    model_name: str,
    user_id: str,
) -> Dict[str, Any]:
    """QA final check at end of plan execution — returns only plan_suggestion."""
    prompt = _QA_FINAL_PROMPT.format(
        user_goal=board["plan"].get("user_goal", ""),
        context_board=_context_board_summary(board),
        final_output=final_output[:2000],
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60)
        data = _parse_json_output(text)
        if data and "plan_suggestion" in data:
            return data
    except Exception as exc:
        logger.warning("[QA-final] failed: %s", exc)
    return {"plan_suggestion": ""}
