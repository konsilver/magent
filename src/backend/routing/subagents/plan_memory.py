"""Memory helper functions for plan mode: retrieval and background saving."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from routing.subagents.plan_store import _mem0_enabled, _parse_json_output


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
        import re as _re
        # Step 1: KV 检索 top-8
        raw = await retrieve_memories(user_id, task_description, limit=8, min_score=0.55)
        if not raw:
            return {"similar_tasks": [], "failure_patterns": [], "graph_plans": []}

        similar_tasks: List[str] = []
        failure_patterns: List[str] = []
        plan_ids_from_kv: List[str] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("##"):
                continue
            text = line.lstrip("- ").strip()
            if not text:
                continue
            _m = _re.search(r"plan_id=(plan_[a-f0-9]{16})", text)
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
        raw = await retrieve_memories(user_id, step_description, limit=4, min_score=0.60)
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
    plan_id: Optional[str] = None,
    step_details: Optional[List[Dict[str, Any]]] = None,
    plan_suggestion: str = "",
    model_name: str = "",
) -> None:
    """Fire-and-forget: LLM distill → KV + Graph memory after plan execution."""
    if not _mem0_enabled() or not user_id:
        return

    async def _save() -> None:
        try:
            from core.llm.memory import save_conversation, MEM0_GRAPH_ENABLED
            from routing.subagents.plan_agents import _call_llm_agent
            from core.config import settings
            status_str = "success" if success else "replan"
            steps_desc = "\n".join(
                f"{i+1}. {s.get('title', s) if isinstance(s, dict) else s}"
                for i, s in enumerate(plan_steps)
            )

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

            if not abstract_steps:
                raw_nodes = step_details or [{"title": s, "step_id": f"s{i+1}"} for i, s in enumerate(plan_steps)]
                abstract_steps = [
                    {"step_id": n.get("step_id", f"step_{i+1}"), "abstract_title": n.get("title", f"步骤{i+1}")}
                    for i, n in enumerate(raw_nodes)
                ]

            pid = plan_id or ""

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

            if MEM0_GRAPH_ENABLED and pid:
                from core.llm.memory import write_plan_graph
                # 成功计划不写 Suggestion 节点；失败/replan 才写
                suggestion_for_graph = (plan_suggestion or failure_reason or "") if not success else ""
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
    """Fire-and-forget: LLM judge then save successful step execution insight to KV memory."""
    if not _mem0_enabled() or not user_id:
        return

    async def _save() -> None:
        try:
            from core.llm.memory import save_conversation
            from routing.subagents.plan_agents import _call_llm_agent
            from core.config import settings
            constraint_desc = local_constraint.get("constraint", "") if local_constraint else ""
            tools_desc = ", ".join(t for t in tool_use_trace if t) or "无"
            redo_desc = f"曾经 REDO，优化建议：{qa_suggestion}" if had_redo and qa_suggestion else (
                "曾经 REDO" if had_redo else "无"
            )

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
    """Fire-and-forget: 用 urgent 检索相似的 user_profile 条目，LLM 合并后只替换检索到的部分。"""
    if not _mem0_enabled() or not user_id:
        return

    urgent = profile_update.get("urgent")
    if not urgent:
        return

    _USER_PROFILE_METADATA = {"type": "user_profile"}

    async def _save() -> None:
        try:
            from core.llm.memory import save_facts_direct, retrieve_memories_with_ids, delete_memory
            from routing.subagents.plan_agents import _call_llm_agent
            from core.config import settings

            # Step 1: 用 urgent 作为 query 检索语义相似的 user_profile 条目（含 id）
            retrieved_items = await retrieve_memories_with_ids(
                user_id, urgent, limit=4, min_score=0.4, memory_type="user_profile"
            )
            retrieved_texts = [item.get("memory", "") for item in retrieved_items if item.get("memory")]
            retrieved_mem_str = "\n".join(f"- {t}" for t in retrieved_texts) if retrieved_texts else "（暂无相关历史特征）"

            # Step 2: LLM 合并 urgent 与检索到的条目（冲突以 urgent 为准）
            merge_prompt = _USER_PROFILE_MERGE_PROMPT.format(
                mem=retrieved_mem_str,
                urgent=urgent,
            )
            _model = model_name or settings.llm.roles.user_profile or settings.llm.base_model_name
            merged_text = await _call_llm_agent(merge_prompt, _model, user_id, timeout=20)
            merged_data = _parse_json_output(merged_text)
            facts: List[str] = merged_data.get("facts", []) if merged_data else []
            if not facts:
                facts = [urgent]

            # Step 3: 只删除检索到的 top-k 条目（它们已被合并进 facts，保留其余条目）
            for entry in retrieved_items:
                mid = entry.get("id") or entry.get("memory_id")
                if mid:
                    try:
                        await delete_memory(mid)
                    except Exception as del_exc:
                        logger.debug("[Memory] user profile delete entry %s failed: %s", mid, del_exc)

            # Step 4: 逐条直接写入，跳过 mem0 的二次 LLM 提取，写入条数 = len(facts)
            written = await save_facts_direct(user_id, facts, metadata=_USER_PROFILE_METADATA)

            logger.info("[Memory] user profile merged & saved for user=%s, retrieved=%d, facts=%d, written=%d",
                        user_id, len(retrieved_items), len(facts), written)
        except Exception as exc:
            logger.debug("[Memory] user profile save failed (non-critical): %s", exc)

    try:
        asyncio.create_task(_save())
    except Exception:
        pass
