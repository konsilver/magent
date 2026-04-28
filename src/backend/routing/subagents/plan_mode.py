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
                                  global_replan > 1 → SSE interrupted, new plan
                                  sent to frontend for user confirmation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from core.infra import log_writer
from core.infra.logging import LogContext
from core.llm.agent_factory import create_agent_executor
from core.llm.mcp_manager import close_clients
from routing.streaming import StreamingAgent, _UsageTrackingModel

import time as _time

logger = logging.getLogger(__name__)

# ── Control-flow constants ────────────────────────────────────────────────────
_MAX_REDO_PER_STEP = 2      # REDO_STEP retries before escalating to REPLAN
_MAX_LOCAL_REPLAN = 1       # local REPLAN count before triggering full global reset

# ── Plan store, context board, and shared utility helpers ────────────────────
from routing.subagents.plan_store import (
    _role_model,
    _PLAN_STORE, _PLAN_STORE_LOCK,
    _store_plan, _get_stored_plan, _update_stored_plan,
    _update_stored_step, _replace_stored_steps, _make_plan_dict,
    _StepProxy,
    _make_context_board, _context_board_summary, _build_plan_context_prompt_section,
    _collect_valid_tool_names, _load_visible_agents, _load_default_plan_prompt,
    _build_tools_description,
    _prepare_history, _build_file_context, _parse_json_output,
    _extract_summary, _terminate_mcp_processes, _mem0_enabled,
)



async def _retrieve_plan_memory(user_id: str, task_description: str) -> Dict[str, Any]:
    """KV 检索相似历史任务，再用 plan_id 到 Neo4j Graph 查完整骨架。

    流程：
    1. KV (Milvus) 根据任务描述检索 top-8 相似计划，从中提取 plan_id
    2. 按 plan_id 查 Neo4j 拿到完整的骨架结构（步骤链 + 优化建议）
    3. 返回 {"similar_tasks": [...], "failure_patterns": [...], "graph_plans": [...]}

    Planner 和 warmup agent 都使用此结果；warmup 直接复用，不再重查。
    """
    if not _mem0_enabled() or not user_id:
        return {"similar_tasks": [], "failure_patterns": [], "graph_plans": []}
    try:
        from core.llm.memory import retrieve_memories
        # Step 1: KV 检索 top-8
        raw = await retrieve_memories(user_id, task_description, limit=8, min_score=0.4)
        if not raw:
            return {"similar_tasks": [], "failure_patterns": [], "graph_plans": []}

        similar_tasks: List[str] = []
        failure_patterns: List[str] = []
        plan_ids_from_kv: List[str] = []

        import re as _re
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("##"):
                continue
            text = line.lstrip("- ").strip()
            if not text:
                continue
            # 从 KV 文本中提取 plan_id（格式：plan_id=xxxxxxxx）
            _m = _re.search(r"plan_id=([a-f0-9]{8,16})", text)
            if _m:
                plan_ids_from_kv.append(_m.group(1))
            if "replan" in text.lower() or "失败" in text or "失败模式" in text:
                failure_patterns.append(text)
            else:
                similar_tasks.append(text)

        # Step 2: Graph 查询（只在 Graph 启用时，且有 plan_id）
        graph_plans: List[Dict] = []
        if plan_ids_from_kv:
            try:
                from core.llm.memory import MEM0_GRAPH_ENABLED, query_plan_graph
                if MEM0_GRAPH_ENABLED:
                    graph_plans = await query_plan_graph(user_id, plan_ids_from_kv[:8])
            except Exception as g_exc:
                logger.debug("[Memory] graph plan query failed (non-critical): %s", g_exc)

        return {
            "similar_tasks": similar_tasks[:8],
            "failure_patterns": failure_patterns[:4],
            "graph_plans": graph_plans,
        }
    except Exception as exc:
        logger.debug("[Memory] plan memory retrieval failed (non-critical): %s", exc)
        return {"similar_tasks": [], "failure_patterns": [], "graph_plans": []}


async def _retrieve_step_memory(user_id: str, step_description: str) -> Dict[str, Any]:
    """Search KV memory for relevant execution patterns for a single step.

    SubAgent reads this before executing its step.
    Returns {"relevant_patterns": [...]}.
    """
    if not _mem0_enabled() or not user_id:
        return {"relevant_patterns": []}
    try:
        from core.llm.memory import retrieve_memories
        # top k=4 as per read.md spec (Task Execution module)
        raw = await retrieve_memories(user_id, step_description, limit=4, min_score=0.45)
        if not raw:
            return {"relevant_patterns": []}

        patterns: List[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("##"):
                continue
            text = line.lstrip("- ").strip()
            if text:
                patterns.append(text)

        return {"relevant_patterns": patterns[:4]}
    except Exception as exc:
        logger.debug("[Memory] step memory retrieval failed (non-critical): %s", exc)
        return {"relevant_patterns": []}


# ── LLM prompts for memory distillation ──────────────────────────────────────

_PLAN_MEMORY_DISTILL_PROMPT = """你是记忆管理助手，负责从一次计划执行的完整记录中提取可复用的规划经验。

## 任务目标
{user_goal}

## 计划步骤（含原始描述，按序）
{steps_desc}

## 执行结果
状态: {status}
质量评分: {quality_score}

## 你的任务
1. 用一句话（不超过100字）总结这个计划的"分解策略"，聚焦于任务是如何被拆解的，不涉及执行细节
2. 将每个步骤抽象化（去除实现细节、工具名称、具体数值），保留步骤的功能语义
3. 输出步骤之间的依赖顺序（线性执行则依次写 next 关系）

输出以下 JSON（不要输出其他内容）：
{{
  "skeleton_description": "一句话描述分解策略",
  "task_type": "研究分析|代码开发|数据处理|问题解答|其他",
  "abstract_steps": [
    {{"step_id": "step_1", "abstract_title": "抽象步骤名称（不含实现细节）"}},
    ...
  ]
}}"""

_STEP_MEMORY_JUDGE_PROMPT = """你是记忆管理助手，判断某次子任务执行经验是否值得存入记忆供未来参考。

## 子任务描述
{step_description}

## 执行结果摘要
{result_summary}

## 失败风险记录（如有）
{risk}

判断标准：这条经验在"不同任务但相似 step"中能否被复用？
如果能，请用一句话（不超过80字）提炼出可复用的执行洞见（insight），包括遇到了什么问题、怎么解决的。
如果不能，直接输出 {{"reusable": false}}。

只输出 JSON：
{{"reusable": true, "insight": "..."}} 或 {{"reusable": false}}"""

_USER_PROFILE_MERGE_PROMPT = """你是用户特征管理助手，负责合并两份用户特征数据。

## 从记忆中检索到的历史特征（mem）
{mem}

## 从最新 query 提取的即时特征（urgent）
{urgent}

## 合并规则
1. 如果 urgent 与 mem 有冲突（同类信息不一致），以 urgent 为准覆盖
2. 如果两者不重叠，把 urgent 的新特征补充进来
3. 不要重复存储相同含义的信息
4. 最终输出合并后的特征列表，每条是一个独立事实

只输出 JSON：
{{"facts": ["特征1", "特征2", ...]}}"""


def _save_task_memory_background(
    user_id: str,
    user_goal: str,
    plan_steps: List[str],
    success: bool,
    quality_score: float,
    failure_reason: str,
    final_solution_summary: str,
    forced: bool,  # kept for call-site compat; always False now
    key_constraints: List[str],
    plan_id: Optional[str] = None,
    step_details: Optional[List[Dict[str, Any]]] = None,
    plan_suggestion: str = "",
    model_name: str = "",
) -> None:
    """Fire-and-forget: LLM distill → KV + Graph memory after plan execution.

    Step 1: LLM extracts skeleton_description, task_type, abstract_steps from context.
    Step 2: Write KV with distilled info (plan_id links KV to Graph).
    Step 3 (Graph only): Write PlanSkeleton --has--> StepNode --next--> StepNode chain;
            on replan also write PlanSkeleton --refers_to--> Suggestion.
    """
    if not _mem0_enabled() or not user_id:
        return

    async def _save() -> None:
        try:
            from core.llm.memory import save_conversation, MEM0_GRAPH_ENABLED
            status_str = "success" if success else "replan"
            steps_desc = "\n".join(
                f"{i+1}. {s.get('title', s) if isinstance(s, dict) else s}"
                for i, s in enumerate(plan_steps)
            )

            # ── Step 1: LLM distillation ─────────────────────────────────────
            distill_prompt = _PLAN_MEMORY_DISTILL_PROMPT.format(
                user_goal=user_goal,
                steps_desc=steps_desc,
                status=status_str,
                quality_score=f"{quality_score:.2f}",
            )
            _model = model_name or settings.llm.roles.plan or settings.llm.base_model_name
            distill_text = await _call_llm_agent(distill_prompt, _model, user_id, timeout=30)
            distill_data = _parse_json_output(distill_text) or {}

            skeleton_desc = distill_data.get("skeleton_description", user_goal[:100])
            task_type = distill_data.get("task_type", "其他")
            abstract_steps: List[Dict] = distill_data.get("abstract_steps", [])

            # Fall back to raw titles if LLM didn't return abstract steps
            if not abstract_steps:
                raw_nodes = step_details or [{"title": s, "step_id": f"s{i+1}"} for i, s in enumerate(plan_steps)]
                abstract_steps = [
                    {"step_id": n.get("step_id", f"step_{i+1}"), "abstract_title": n.get("title", f"步骤{i+1}")}
                    for i, n in enumerate(raw_nodes)
                ]

            pid = (plan_id or "")[:16]

            # ── Step 2: KV storage ───────────────────────────────────────────
            # Encode distilled skeleton as conversation pair.
            # plan_id is embedded so Graph lookup can match KV entries.
            _suggestion_part = f"优化建议：{plan_suggestion[:150]}" if plan_suggestion else (
                f"失败摘要：{failure_reason[:150]}" if failure_reason else ""
            )
            kv_user_msg = (
                f"计划任务（plan_id={pid}）：{user_goal}\n"
                f"分解策略：{skeleton_desc}\n"
                f"任务类型：{task_type}"
            )
            kv_assistant_msg = (
                f"计划执行{status_str}，质量评分 {quality_score:.2f}。"
                + (f"{_suggestion_part}。" if _suggestion_part else "")
                + f"关键约束：{'; '.join(key_constraints[:3]) if key_constraints else '无'}。"
                + f"结果摘要：{final_solution_summary[:200] if final_solution_summary else '无'}"
            )
            await save_conversation(user_id, kv_user_msg, kv_assistant_msg)
            logger.info("[Memory] plan KV memory saved for user=%s, plan_id=%s, status=%s",
                        user_id, pid, status_str)

            # ── Step 3: Graph storage (direct Neo4j write) ───────────────────
            # Precise schema:
            #   PlanSkeleton(plan_id) --HAS--> StepNode (order by index)
            #   StepNode_n            --NEXT--> StepNode_n+1
            #   PlanSkeleton(plan_id) --REFERS_TO--> Suggestion (replan only)
            if MEM0_GRAPH_ENABLED and pid:
                from core.llm.memory import write_plan_graph
                suggestion_for_graph = (plan_suggestion or failure_reason or "") if not success else plan_suggestion
                await write_plan_graph(
                    user_id=user_id,
                    plan_id=pid,
                    skeleton_description=skeleton_desc,
                    task_type=task_type,
                    status=status_str,
                    abstract_steps=abstract_steps,
                    plan_suggestion=suggestion_for_graph,
                )

        except Exception as exc:
            logger.debug("[Memory] plan memory save failed (non-critical): %s", exc)

    try:
        asyncio.create_task(_save())
    except Exception:
        pass


def _save_step_memory_background(
    user_id: str,
    step_description: str,
    tool_use_trace: List[str],
    local_constraint: Dict,
    had_redo: bool,
    qa_suggestion: str,
    model_name: str = "",
) -> None:
    """Fire-and-forget: LLM judge then save successful step execution insight to KV memory.

    Called after the entire plan completes (not per-step), so the full board context
    is available.  Only writes when the LLM judges the experience is reusable.
    """
    if not _mem0_enabled() or not user_id:
        return

    async def _save() -> None:
        try:
            from core.llm.memory import save_conversation
            constraint_desc = local_constraint.get("constraint", "") if local_constraint else ""
            tools_desc = ", ".join(t for t in tool_use_trace if t) or "无"
            redo_desc = f"曾经 REDO，优化建议：{qa_suggestion}" if had_redo and qa_suggestion else (
                "曾经 REDO" if had_redo else "无"
            )

            # LLM judge: is this experience reusable across different tasks with similar steps?
            judge_prompt = _STEP_MEMORY_JUDGE_PROMPT.format(
                step_description=step_description,
                result_summary=f"约束：{constraint_desc or '无'}；调用工具：{tools_desc}",
                risk=redo_desc,
            )
            _model = model_name or settings.llm.base_model_name
            judge_text = await _call_llm_agent(judge_prompt, _model, user_id, timeout=20)
            judge_data = _parse_json_output(judge_text)
            if not judge_data or not judge_data.get("reusable"):
                logger.debug("[Memory] step memory skipped by LLM judge: %s", step_description[:40])
                return

            insight = judge_data.get("insight", "")
            user_msg = f"执行子任务：{step_description}"
            assistant_msg = f"执行洞见：{insight}" if insight else f"成功完成。工具：{tools_desc}"
            await save_conversation(user_id, user_msg, assistant_msg)
            logger.info("[Memory] step memory saved for user=%s, step=%s", user_id, step_description[:40])
        except Exception as exc:
            logger.debug("[Memory] step memory save failed (non-critical): %s", exc)

    try:
        asyncio.create_task(_save())
    except Exception:
        pass


def _save_user_profile_background(user_id: str, profile_update: Dict, model_name: str = "") -> None:
    """Fire-and-forget: merge urgent + mem via LLM, delete old entries, write merged result."""
    if not _mem0_enabled() or not user_id:
        return

    urgent = profile_update.get("urgent")
    if not urgent:
        return

    mem = profile_update.get("mem") or ""

    _USER_PROFILE_METADATA = {"type": "user_profile"}

    async def _save() -> None:
        try:
            from core.llm.memory import save_conversation, get_memories_by_metadata, delete_memory

            # Step 1: LLM merge urgent + mem into a single facts list
            merge_prompt = _USER_PROFILE_MERGE_PROMPT.format(
                mem=mem or "（暂无历史特征）",
                urgent=urgent,
            )
            _model = model_name or settings.llm.roles.user_profile or settings.llm.base_model_name
            merged_text = await _call_llm_agent(merge_prompt, _model, user_id, timeout=20)
            merged_data = _parse_json_output(merged_text)
            facts: List[str] = merged_data.get("facts", []) if merged_data else []
            if not facts:
                facts = [urgent]

            # Step 2: 精确删除旧的 user_profile 条目（按 metadata type 过滤，无误删风险）
            try:
                old_entries = await get_memories_by_metadata(user_id, _USER_PROFILE_METADATA)
                for entry in old_entries:
                    mid = entry.get("id") or entry.get("memory_id")
                    if mid:
                        await delete_memory(mid)
            except Exception as del_exc:
                logger.debug("[Memory] user profile delete old failed (non-critical): %s", del_exc)

            # Step 3: 写入合并后的用户特征，带 metadata 标签方便下次精确删除
            facts_text = "\n".join(f"- {f}" for f in facts)
            user_msg = "用户特征更新"
            assistant_msg = f"用户特征（合并后）：\n{facts_text}"
            await save_conversation(user_id, user_msg, assistant_msg, metadata=_USER_PROFILE_METADATA)
            logger.info("[Memory] user profile merged & saved for user=%s, facts=%d", user_id, len(facts))
        except Exception as exc:
            logger.debug("[Memory] user profile save failed (non-critical): %s", exc)

    try:
        asyncio.create_task(_save())
    except Exception:
        pass


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

## 从记忆中检索到的与当前任务相关的用户特征（top-4）
{memory_context}

## 你的任务
1. 从用户输入中提取"urgent"：用户在这次输入中最核心的即时需求（一句话，聚焦偏好/认知水平/风格倾向等高稳定性特征）
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
    """Extract user characteristics, write to context board's 'user' field.

    Retrieves top-4 user profile memories (read.md: User-profile module, top k=4),
    then writes merged urgent+mem to board. Async background write to memory after.
    """
    # Retrieve relevant user profile memories (top k=4 per spec)
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
            # Async background: merge urgent+mem via LLM then save to memory
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
    # Default: treat ambiguous reply as confirm to avoid blocking execution
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

    # Append Graph-based plan skeletons (step chains + suggestions from Neo4j)
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
            # Write to context board
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

根据 context 中的 success_criteria 和最终结果，给出针对整个计划的优化建议。
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
        success_criteria=json.dumps(board.get("only_qa", {}).get("success_criteria", []), ensure_ascii=False),
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

        # Inject plan-mode context (goal, completed step outputs, constraints)
        # into sys_prompt at system level so the agent has stable background
        # awareness. For pool agents, reset() already restored the base prompt
        # before this point, so _base_system_prompt is never polluted.
        _plan_ctx_section = _build_plan_context_prompt_section(
            board, step, len(board.get("plan", {}).get("steps", []))
        )
        agent.sys_prompt = agent.sys_prompt + "\n\n" + _plan_ctx_section

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
    previous_plan_id: Optional[str] = None,
    user_reply: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Phase 1: UserProfile + Planner in parallel, then persist plan.

    When previous_plan_id and user_reply are provided:
    - Classify user intent: 'confirm' → caller should execute; 'replan' → save rejected
      plan to memory then re-generate plan with user suggestions.

    Yields SSE events:
    - plan_intent      {intent: 'confirm'|'replan'}  (only when user_reply provided)
    - plan_generating  {delta: str}
    - plan_generated   {plan_id, title, description, steps: [...]}
    - plan_error       {error: str}
    """
    # ── Intent classification (when responding to a shown plan) ──────────────
    if user_reply and previous_plan_id:
        intent = await _classify_user_intent(user_reply, _role_model("intent", model_name), user_id)
        yield {"type": "plan_intent", "intent": intent, "plan_id": previous_plan_id}

        if intent == "confirm":
            # Signal frontend to execute; no further action here
            yield {"type": "plan_confirm", "plan_id": previous_plan_id}
            return

        # intent == "replan": save rejected plan to memory, then fall through to re-plan
        prev_plan = _get_stored_plan(previous_plan_id)
        if prev_plan:
            _save_task_memory_background(
                user_id=user_id,
                user_goal=prev_plan.get("extra_data", {}).get("user_goal", prev_plan.get("title", "")),
                plan_steps=[s.get("title", "") for s in prev_plan.get("steps", [])],
                success=False,
                quality_score=0.0,
                failure_reason="",
                final_solution_summary="",
                forced=False,
                key_constraints=[],
                plan_id=previous_plan_id,
                step_details=[{"step_id": s["step_id"], "title": s.get("title", "")} for s in prev_plan.get("steps", [])],
                plan_suggestion=f"用户拒绝该方案并给出建议：{user_reply[:200]}",
            )
        yield {"type": "plan_generating", "delta": "已记录您的建议，正在重新制定计划...\n"}

    visible_agents = _load_visible_agents(db, user_id, enabled_agent_ids)
    tools_desc = _build_tools_description(enabled_mcp_ids, enabled_skill_ids, visible_agents)

    # Build the context board for this generation phase
    # (will be re-built fresh at execution time; here just for planner context)
    board = _make_context_board()

    if not (user_reply and previous_plan_id):
        yield {"type": "plan_generating", "delta": "正在分析用户特征并查询历史记忆...\n"}

    # Run UserProfile Agent and memory retrieval in parallel
    retrieved_memory, _ = await asyncio.gather(
        _retrieve_plan_memory(user_id, task_description),
        _run_user_profile_agent(user_id, task_description, _role_model("user_profile", model_name), board),
    )

    yield {"type": "plan_generating", "delta": "正在制定执行计划...\n"}

    try:
        plan_data = await _run_planner(
            user_input=task_description,
            user_id=user_id,
            model_name=_role_model("plan", model_name),
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

        # Store plan in memory (no DB persistence for plan content)
        plan_id = f"plan_{uuid.uuid4().hex[:16]}"
        agent_name_map = {a.get("agent_id"): a.get("name", a.get("agent_id", "")) for a in visible_agents} if visible_agents else {}

        extra: Dict[str, Any] = {
            "user_goal": plan_data.get("user_goal", task_description),
            "retrieved_memory": retrieved_memory,
            "user_profile": board.get("user", {}),
        }
        if uploaded_files:
            extra["uploaded_files"] = uploaded_files
        if agent_name_map:
            extra["agent_name_map"] = agent_name_map

        plan_dict = _make_plan_dict(
            plan_id=plan_id,
            user_id=user_id,
            title=plan_data.get("title", "未命名计划"),
            description=plan_data.get("description", ""),
            task_input=task_description,
            steps=plan_data.get("steps", []),
            extra_data=extra,
        )
        _store_plan(plan_dict)

        event: Dict[str, Any] = {
            "type": "plan_generated",
            "plan_id": plan_id,
            "title": plan_dict["title"],
            "description": plan_dict["description"],
            "task_input": plan_dict["task_input"],
            "status": plan_dict["status"],
            "total_steps": plan_dict["total_steps"],
            "completed_steps": 0,
            "result_summary": None,
            "steps": [
                {
                    "step_id": s["step_id"],
                    "step_order": s["step_order"],
                    "title": s["title"],
                    "brief_description": s.get("brief_description", ""),
                    "description": s["description"],
                    "expected_tools": s["expected_tools"],
                    "expected_skills": s["expected_skills"],
                    "expected_agents": s["expected_agents"],
                    "status": s["status"],
                    "result_summary": None,
                }
                for s in plan_dict["steps"]
            ],
        }
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
    - plan_step_qa        {step_id, verdict}
    - plan_step_complete  {step_id, status, summary}
    - plan_error          {plan_id, step_id?, error}
    - plan_complete       {plan_id, status, summary, ...}
    """
    logger.warning("[plan-exec] astream_execute_plan called for plan_id=%s", plan_id)

    plan_dict = _get_stored_plan(plan_id)
    if not plan_dict or plan_dict.get("user_id") != user_id:
        yield {"type": "plan_error", "plan_id": plan_id, "error": "计划不存在"}
        return

    if plan_dict["status"] not in ("approved", "running"):
        yield {"type": "plan_error", "plan_id": plan_id, "error": f"计划状态 '{plan_dict['status']}' 不可执行"}
        return

    plan_meta = plan_dict.get("extra_data") or {}
    if not uploaded_files:
        uploaded_files = plan_meta.get("uploaded_files")

    user_goal = plan_meta.get("user_goal", plan_dict["task_input"])
    plan_retrieved_memory = plan_meta.get("retrieved_memory", {})
    saved_user_profile = plan_meta.get("user_profile", {})

    _update_stored_plan(plan_id, status="running")
    completed_count = 0
    cancelled = False

    _chat_hint = plan_meta.get("chat_id")
    _log_ctx = LogContext(user_id=user_id or None, chat_id=_chat_hint)
    _log_ctx.__enter__()

    _plan_run_start = _time.monotonic()
    _plan_subagent_log_id = await log_writer.start_subagent_log({
        "subagent_name": "plan_mode",
        "subagent_type": "plan_mode",
        "subagent_id": plan_id,
        "plan_id": plan_id,
        "model": model_name,
        "step_title": plan_dict["title"],
        "input_messages": {
            "task_input": plan_dict["task_input"],
            "total_steps": plan_dict["total_steps"],
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

    # Seed board with planner's output (from memory store)
    board["plan"]["user_goal"] = user_goal
    board["plan"]["steps"] = [
        {
            "step_id": s["step_id"],
            "brief_description": s.get("brief_description", ""),
            "description": s.get("description") or s.get("title", ""),
            "output": None,
            "suggestion": None,
            "tool_use_trace": [],
        }
        for s in plan_dict["steps"]
    ]

    # ── Warmup Phase ──────────────────────────────────────────────────────────
    yield {"type": "plan_step_progress", "step_id": None, "delta": "Warmup Agent 正在初始化执行语义空间...\n"}

    warmup_memory = {
        "similar_tasks": plan_retrieved_memory.get("similar_tasks", []),
    }

    warmup_result = await _run_warmup(
        user_input=plan_dict["task_input"],
        user_id=user_id,
        model_name=_role_model("warmup", model_name),
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
    global_reset_count = 0   # full plan resets (each triggers SSE interrupt + new plan)

    try:
        step_idx = 0
        steps = [_StepProxy(s) for s in plan_dict["steps"]]

        while step_idx < len(steps):
            step = steps[step_idx]

            logger.warning("[plan-exec] === Step %d/%d: %s ===",
                           step_idx + 1, len(steps), step.title)

            if cancelled:
                _update_stored_step(plan_id, step.step_id, status="skipped")
                step_idx += 1
                continue

            current_status = (_get_stored_plan(plan_id) or {}).get("status", "running")
            if current_status == "cancelled":
                cancelled = True
                _update_stored_step(plan_id, step.step_id, status="skipped")
                step_idx += 1
                continue

            yield {
                "type": "plan_step_start",
                "step_id": step.step_id,
                "step_order": step.step_order,
                "title": step.title,
            }
            yield {"type": "heartbeat"}
            _update_stored_step(plan_id, step.step_id, status="running", started_at=datetime.utcnow().isoformat())

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
                    model_name=_role_model("subagent", model_name),
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

                qa_data = await _run_qa(
                    step=step,
                    result=narrative_text or step_text,
                    board=board,
                    local_constraint=current_local_constraint,
                    expected_schema=current_expected_schema,
                    model_name=_role_model("qa", model_name),
                    user_id=user_id,
                )
                qa_verdict = qa_data.get("verdict", "PASS")

                yield {
                    "type": "plan_step_qa",
                    "step_id": step.step_id,
                    "verdict": qa_verdict,
                }

                if qa_verdict == "PASS":
                    break

                if qa_verdict == "REDO_STEP":
                    redo_count += 1
                    redo_failure_reason = qa_data.get("failure_reason", [])
                    logger.warning("[QA] REDO_STEP step=%d redo=%d", step.step_order, redo_count)
                    # Accumulate QA suggestions into context board step suggestion field
                    _new_suggestions = "; ".join(
                        r.get("suggestion", "") for r in redo_failure_reason if r.get("suggestion")
                    )
                    for board_step in board["plan"]["steps"]:
                        if board_step["step_id"] == step.step_id:
                            existing = board_step.get("suggestion") or ""
                            board_step["suggestion"] = (existing + "; " + _new_suggestions).strip("; ") if _new_suggestions else existing
                            break
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
            if qa_verdict == "REPLAN":
                local_replan_count += 1
                logger.warning("[plan-exec] REPLAN triggered at step %d (local_count=%d, global_reset=%d)",
                               step.step_order, local_replan_count, global_reset_count)

                if local_replan_count >= _MAX_LOCAL_REPLAN:
                    # Trigger full global reset: interrupt execution, replan from scratch,
                    # send new plan to frontend for user confirmation (no Forced mode).
                    global_reset_count += 1
                    logger.warning("[plan-exec] Global reset #%d triggered at step %d",
                                   global_reset_count, step.step_order)

                    failure_summary = "; ".join(
                        r.get("description", "") for r in qa_data.get("failure_reason", []) if r.get("description")
                    ) or "执行方案无法达到预期效果"

                    # Async: save failed plan to memory with risk annotation
                    _failed_plan_suggestion = f"该方案在第{step.step_order}步触发全局重置，原因：{failure_summary}"
                    _save_task_memory_background(
                        user_id=user_id,
                        user_goal=board["plan"].get("user_goal", ""),
                        plan_steps=[s.title for s in steps],
                        success=False,
                        quality_score=0.0,
                        failure_reason=_failed_plan_suggestion,
                        final_solution_summary="",
                        forced=False,
                        key_constraints=[
                            c.get("constraint", "")
                            for c in board["check"].get("global_constraints", [])
                        ],
                        plan_id=plan_id,
                        step_details=[{"step_id": s.step_id, "title": s.title} for s in steps],
                    )

                    replan_ctx = {
                        "complete": True,
                        "failed_step": step.step_order,
                        "failure_reason": qa_data.get("failure_reason", []),
                    }
                    # 全局重置：重建空 board，user_profile_agent 与 memory 检索并行
                    # 确保新 board 的 context.user 字段有用户特征（不能省略 user_profile_agent）
                    _reset_board = _make_context_board()
                    replan_memory, _ = await asyncio.gather(
                        _retrieve_plan_memory(user_id, plan_dict["task_input"]),
                        _run_user_profile_agent(
                            user_id=user_id,
                            user_input=plan_dict["task_input"],
                            model_name=_role_model("user_profile", model_name),
                            board=_reset_board,
                        ),
                    )
                    new_plan_data = await _run_planner(
                        user_input=plan_dict["task_input"],
                        user_id=user_id,
                        model_name=_role_model("plan", model_name),
                        tools_desc=_build_tools_description(enabled_mcp_ids, enabled_skill_ids),
                        retrieved_memory=replan_memory,
                        board=_reset_board,
                        replan_context=replan_ctx,
                    )

                    new_steps = []
                    if new_plan_data and new_plan_data.get("steps"):
                        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
                        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
                        _valid_agents = {a.get("agent_id") for a in _load_visible_agents(db, user_id, enabled_agent_ids)}
                        for sd in new_plan_data["steps"]:
                            if _valid_tools is not None:
                                sd["expected_tools"] = [t for t in (sd.get("expected_tools") or []) if t in _valid_tools]
                            if _valid_skills is not None:
                                sd["expected_skills"] = [s for s in (sd.get("expected_skills") or []) if s in _valid_skills]
                            sd["expected_agents"] = [a for a in (sd.get("expected_agents") or []) if a in _valid_agents]
                        new_steps = new_plan_data["steps"]

                    # Yield global-reset event and interrupt this execution SSE stream.
                    # Frontend will display the new plan for user confirmation.
                    yield {
                        "type": "plan_global_reset",
                        "plan_id": plan_id,
                        "failure_reason": failure_summary,
                        "reset_count": global_reset_count,
                        "new_plan": {
                            "user_goal": new_plan_data.get("user_goal", "") if new_plan_data else "",
                            "steps": [
                                {
                                    "step_order": i + 1,
                                    "title": s.get("title", f"步骤{i+1}"),
                                    "brief_description": s.get("brief_description", ""),
                                    "description": s.get("description", ""),
                                    "expected_tools": s.get("expected_tools", []),
                                    "expected_skills": s.get("expected_skills", []),
                                }
                                for i, s in enumerate(new_steps)
                            ],
                            "total_steps": len(new_steps),
                        },
                    }
                    return  # interrupt execution stream
                else:
                    # Local replan: re-plan from current failure point
                    yield {"type": "plan_step_progress", "step_id": step.step_id,
                           "delta": f"\nQA 触发局部重新规划（从当前步骤重做）...\n"}

                    replan_memory = await _retrieve_plan_memory(user_id, plan_dict["task_input"])
                    replan_ctx = {
                        "complete": False,
                        "failed_step": step.step_order,
                        "failure_reason": qa_data.get("failure_reason", []),
                    }
                    new_plan_data = await _run_planner(
                        user_input=plan_dict["task_input"],
                        user_id=user_id,
                        model_name=_role_model("plan", model_name),
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
                            {"title": s.get("title", f"步骤{i+1}"), "brief_description": s.get("brief_description", ""),
                             "description": s.get("description", ""),
                             "expected_tools": s.get("expected_tools", []),
                             "expected_skills": s.get("expected_skills", []),
                             "expected_agents": s.get("expected_agents", [])}
                            for i, s in enumerate(remaining_steps)
                        ]
                        merged_steps = [
                            {"title": s.title, "brief_description": s._d.get("brief_description", ""),
                             "description": s._d.get("description", ""),
                             "expected_tools": s._d.get("expected_tools") or [],
                             "expected_skills": s._d.get("expected_skills") or [],
                             "expected_agents": s._d.get("expected_agents") or []}
                            for s in completed_steps_so_far
                        ] + all_new_steps
                        updated = _replace_stored_steps(plan_id, merged_steps)
                        if updated:
                            old_board_steps = board["plan"]["steps"]
                            steps = [_StepProxy(s) for s in updated["steps"]]
                            board["plan"]["steps"] = [
                                {
                                    "step_id": s["step_id"],
                                    "brief_description": s.get("brief_description", ""),
                                    "description": s.get("description") or s.get("title", ""),
                                    "output": old_board_steps[i]["output"] if i < len(old_board_steps) else None,
                                    "suggestion": old_board_steps[i].get("suggestion") if i < len(old_board_steps) else None,
                                    "tool_use_trace": old_board_steps[i].get("tool_use_trace", []) if i < len(old_board_steps) else [],
                                }
                                for i, s in enumerate(updated["steps"])
                            ]
                            _update_stored_step(plan_id, steps[step_idx].step_id, status="running", started_at=datetime.utcnow().isoformat())
                            # Notify frontend: local replan happened, steps from step_idx replaced
                            _replan_reason = "; ".join(
                                r.get("suggestion", "") or r.get("description", "")
                                for r in qa_data.get("failure_reason", []) if r.get("suggestion") or r.get("description")
                            )
                            yield {
                                "type": "plan_replan",
                                "plan_id": plan_id,
                                "replaced_from_order": step.step_order,
                                "reason": _replan_reason or "为了保证执行成功率，已自动优化后续步骤",
                                "new_steps": [
                                    {
                                        "step_id": s["step_id"],
                                        "step_order": s["step_order"],
                                        "title": s.get("title", ""),
                                        "brief_description": s.get("brief_description", ""),
                                    }
                                    for s in updated["steps"][step_idx:]
                                ],
                            }
                            continue

            # ── Finalize step ─────────────────────────────────────────────────
            narrative_text, subagent_json = _extract_next_step_instruction(step_text)
            display_text = narrative_text if narrative_text else step_text

            # Write step output + tool_use_trace to context board (only after QA PASS)
            step_result_summary = subagent_json.get("result", "") if subagent_json else ""
            for board_step in board["plan"]["steps"]:
                if board_step["step_id"] == step.step_id:
                    board_step["output"] = step_result_summary or _extract_summary(display_text)
                    # Collect tool call names as trace for batch memory write after plan completes
                    board_step["tool_use_trace"] = [
                        tc.get("name") or tc.get("tool_name") or tc.get("function", {}).get("name", "")
                        for tc in (step_tool_calls or [])
                        if isinstance(tc, dict)
                    ]
                    # Record per-step QA result for batch memory write
                    board_step["_qa_passed"] = (qa_verdict == "PASS")
                    board_step["_had_redo"] = (redo_count > 0)
                    board_step["_qa_suggestion"] = (
                        str(qa_data.get("failure_reason", [{}])[0].get("suggestion", ""))
                        if qa_verdict != "PASS" else ""
                    )
                    board_step["_step_description"] = step.description or step.title
                    board_step["_local_constraint"] = current_local_constraint
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

            _update_stored_step(
                plan_id,
                step.step_id,
                status=_final_step_status,
                result_summary=summary,
                ai_output=display_text[:5000],
                tool_calls_log=step_tool_calls,
                completed_at=datetime.utcnow().isoformat(),
            )

            if _final_step_status == "success":
                completed_count += 1

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
        logger.warning("[plan-exec] === All steps done. completed=%d/%d ===",
                       completed_count, len(steps))

        final_status = "completed" if completed_count == len(steps) else "failed"
        if cancelled:
            final_status = "cancelled"

        overall_summary = f"共 {len(steps)} 个步骤，完成 {completed_count} 个"
        result_text = last_step_text

        qa_final = await _run_qa_final(
            board=board,
            final_output=result_text,
            model_name=_role_model("qa", model_name),
            user_id=user_id,
        )
        task_success = final_status == "completed"

        _update_stored_plan(
            plan_id,
            status=final_status,
            completed_steps=completed_count,
            result_summary=result_text[:2000] if result_text else overall_summary,
        )

        _final_plan_suggestion = qa_final.get("plan_suggestion", "")
        # Write QA plan suggestion to context board
        if _final_plan_suggestion:
            board["plan"]["plan_suggestion"] = _final_plan_suggestion
        _save_task_memory_background(
            user_id=user_id,
            user_goal=board["plan"].get("user_goal", ""),
            plan_steps=[s.title for s in steps],
            success=bool(task_success),
            quality_score=1.0 if task_success else 0.5,
            failure_reason="",
            final_solution_summary=result_text[:500],
            forced=False,
            key_constraints=[
                c.get("constraint", "")
                for c in board["check"].get("global_constraints", [])
            ],
            plan_id=plan_id,
            step_details=[{"step_id": s.step_id, "title": s.title} for s in steps],
            plan_suggestion=_final_plan_suggestion,
            model_name=model_name,
        )

        # Batch write step memories — runs after all steps complete so we can use
        # the full board context.  Each step is isolated (no cross-step dependency).
        for board_step in board["plan"]["steps"]:
            if not board_step.get("_qa_passed"):
                continue
            _save_step_memory_background(
                user_id=user_id,
                step_description=board_step.get("_step_description") or board_step.get("description", ""),
                tool_use_trace=board_step.get("tool_use_trace", []),
                local_constraint=board_step.get("_local_constraint", {}),
                had_redo=board_step.get("_had_redo", False),
                qa_suggestion=board_step.get("_qa_suggestion", ""),
                model_name=_role_model("qa", model_name),
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
            "total_steps": plan_dict["total_steps"],
            "usage": exec_usage,
            "plan_suggestion": _final_plan_suggestion,
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
        _update_stored_plan(plan_id, status="failed", result_summary=str(exc))
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
