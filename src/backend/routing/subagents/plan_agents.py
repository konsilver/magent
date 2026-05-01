"""LLM agent helpers for plan mode: UserProfile, Planner, Intent Classification, Warmup, QA."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time as _time
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
    _agent_label: str = "LLMAgent",
) -> str:
    """Call a tool-disabled agent and return stripped text output."""
    _t0 = _time.monotonic()
    logger.info("[%s] START model=%s user=%s timeout=%ds", _agent_label, model_name, user_id, timeout)
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
        result = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        _elapsed = (_time.monotonic() - _t0) * 1000
        logger.info("[%s] DONE elapsed=%.0fms output_chars=%d", _agent_label, _elapsed, len(result))
        return result
    except asyncio.TimeoutError:
        logger.warning("[%s] TIMEOUT after %ds", _agent_label, timeout)
        raise
    except Exception as exc:
        logger.warning("[%s] ERROR after %.0fms: %s", _agent_label, (_time.monotonic() - _t0) * 1000, exc)
        raise
    finally:
        await close_clients(mcp_clients)


# ═══════════════════════════════════════════════════════════════════════════════
# UserProfile Agent
# ═══════════════════════════════════════════════════════════════════════════════

_USER_PROFILE_PROMPT_TEMPLATE = """你是 User-Profile Agent，负责从用户最新输入中识别**真正能反映用户稳定特征**的信息。

## 什么是"用户特征"（必须满足以下所有条件）
1. **稳定性**：能跨越多次对话反复成立的特征（如专业背景、表达风格、特定偏好、认知水平）
2. **个人性**：是关于"这个人是谁/如何思考/有什么偏好"，而非关于"这次任务要做什么"
3. **显式性**：必须在输入中有明确的语言线索支撑，不可凭推断或假设

## 什么不是"用户特征"（以下情况请填写 null）
- 用户只是描述了一个任务目标（例："帮我写一份报告"）→ 这是任务，不是特征
- 用户提出了一个问题（例："Python 怎么读取文件？"）→ 这是查询，不是特征
- 用户描述了一个场景或背景（例："我有个项目要明天交"）→ 这是情境，不是特征
- 输入非常短且通用，没有任何个人信息线索 → 应填 null

## 用户最新输入
{user_input}

## 从记忆中检索到的与当前任务相关的用户特征（top-4）
{memory_context}

## 你的任务
1. 严格审查输入，判断是否存在符合上述"用户特征"定义的信息
   - 如果存在：提炼为"urgent"（一句话，≤40字，聚焦特征而非任务内容）
   - 如果不存在：urgent 填 null
2. 将记忆中与本次任务确实相关的用户特征整合到"mem"字段（如果记忆为空或不相关则为 null）

## 输出要求
请严格输出以下 JSON（不要输出其他内容）：
{{
  "urgent": "用户特征描述（≤40字）或 null",
  "mem": "从记忆中提炼的相关用户特征摘要（或 null）"
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
    logger.info("[UserProfileAgent] extracting user profile for user=%s input_chars=%d", user_id, len(user_input))
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=30, _agent_label="UserProfileAgent")
        data = _parse_json_output(text)
        if data:
            board["user"]["urgent"] = data.get("urgent")
            board["user"]["mem"] = data.get("mem")
            logger.info("[UserProfileAgent] profile written to board: urgent=%r mem_present=%s",
                        str(data.get("urgent", ""))[:80], bool(data.get("mem")))
            _save_user_profile_background(user_id, data, model_name=model_name)
        else:
            logger.warning("[UserProfileAgent] JSON parse failed, board unchanged")
    except Exception as exc:
        logger.warning("[UserProfileAgent] failed (non-critical): %s", exc)


async def extract_user_profile(
    user_id: str,
    user_input: str,
    model_name: str,
) -> Optional[Dict[str, Any]]:
    """Extract user characteristics for the given input and persist them.

    Returns {"urgent": str|None, "mem": str|None}, or None on failure.
    Fires off background memory merge/save as a side effect.
    Used by both plan mode and normal chat mode.
    """
    from routing.subagents.plan_memory import _save_user_profile_background
    memory_context = "（记忆系统未启用或暂无相关记录）"
    if _mem0_enabled() and user_id:
        try:
            from core.llm.memory import retrieve_memories
            raw = await retrieve_memories(user_id, user_input, limit=4, min_score=0.4)
            if raw:
                memory_context = raw
        except Exception as exc:
            logger.debug("[UserProfile] memory retrieval failed: %s", exc)

    prompt = _USER_PROFILE_PROMPT_TEMPLATE.format(
        user_input=user_input,
        memory_context=memory_context,
    )
    logger.info("[UserProfile] extracting profile user=%s input_chars=%d", user_id, len(user_input))
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=30, _agent_label="UserProfile")
        data = _parse_json_output(text)
        if data:
            logger.info("[UserProfile] done: urgent=%r mem_present=%s",
                        str(data.get("urgent", ""))[:80], bool(data.get("mem")))
            _save_user_profile_background(user_id, data, model_name=model_name)
            return data
        logger.warning("[UserProfile] JSON parse failed")
        return None
    except Exception as exc:
        logger.warning("[UserProfile] failed (non-critical): %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Planner Agent
# ═══════════════════════════════════════════════════════════════════════════════

_PLANNER_PROMPT_TEMPLATE = """你是 Planner Agent，负责将用户任务拆解为一组线性、可执行的步骤。

你的职责只有一件事：定义「做什么」（宏观任务分解），不涉及「如何做」，不包含执行细节。

## 用户特征（来自 context 黑板）
{user_context}

【重要】优先级规则：
- context.user 字段（用户实时特征）的优先级**高于**历史记忆中任何 suggestion（包括计划建议和子任务建议）
- 若 context.user 内部存在多条相互冲突的条目，以**时间戳最新**的条目为准
- 历史记忆仅作参考，不得覆盖或替代 context.user 中的用户特征

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
- 如果有 replan_context，必须利用 failure_reason 做修正
- **步骤解耦原则**：每个 step 应尽量独立（可单独理解和执行），避免步骤之间强耦合；允许逻辑上的递进关系，但每步的输入应来自上下文而非对前一步的硬性依赖
- **步骤数量限制**：简单任务（问答、推荐、解释）2~3步；复杂任务最多不超过6步；禁止因过度拆分导致步骤冗余"""


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
    logger.info("[IntentClassify] classifying user reply (len=%d): %r", len(user_reply), user_reply[:80])
    prompt = _INTENT_CLASSIFY_PROMPT.format(user_reply=user_reply[:500])
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=20, _agent_label="IntentClassify")
        data = _parse_json_output(text)
        if data and data.get("intent") in ("confirm", "replan"):
            logger.info("[IntentClassify] intent=%s", data["intent"])
            return data["intent"]
        logger.warning("[IntentClassify] unexpected output, defaulting to confirm: %r", text[:100])
    except Exception as exc:
        logger.warning("[IntentClassify] failed (defaulting to confirm): %s", exc)
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

    is_replan = bool(replan_context)
    _label = "Planner(replan)" if is_replan else "Planner"
    logger.info("[%s] START user=%s input_chars=%d similar_tasks=%d graph_plans=%d",
                _label, user_id, len(user_input),
                len(retrieved_memory.get("similar_tasks", [])),
                len(retrieved_memory.get("graph_plans", [])))

    prompt = _PLANNER_PROMPT_TEMPLATE.format(
        user_context=user_context,
        memory_context=memory_context,
        user_input=user_input,
        replan_context=replan_section,
        tools_desc=tools_desc,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=90, _agent_label=_label)
        data = _parse_json_output(text)
        if data:
            steps = data.get("steps", [])
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
                for i, s in enumerate(steps)
            ]
            logger.info("[%s] plan generated: title=%r steps=%d goal_chars=%d",
                        _label, str(data.get("title", ""))[:60], len(steps), len(data.get("user_goal", "")))
        else:
            logger.warning("[%s] JSON parse failed, returning None", _label)
        return data
    except Exception as exc:
        logger.error("[%s] failed: %s", _label, exc)
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

## 步骤总数（来自 Planner）
{step_count}

## 你需要为之制定局部约束的第一个 SubAgent 的任务
**步骤 1**：{first_step_title}
{first_step_description}

请在下方"第三步"中，**结合上述第一步的具体任务内容**制定针对性局部约束，而非泛泛描述。

## 第一步：风险评估（必须先做，决定约束强弱）
评估本次任务执行过程中可能出现的**错误风险等级**：

- **低风险任务**：推荐书籍、回答问题、解释概念、给出建议等——输出即使有偏差，用户可以轻易识别和纠正
  → 约束宽松：global_constraints ≤ 2 条（仅内容质量类），local_constraint priority = "soft"，validation_rules = []
- **中风险任务**：数据分析、报告撰写、方案设计等——输出错误有一定代价但可补救
  → 约束适中：global_constraints ≤ 4 条，priority 可以有部分 "hard"
- **高风险任务**：代码执行、系统设计方案（用户已明确定义的软件架构/接口规范等）、精确计算、需调用外部工具——输出错误代价高或难以发现
  → 约束严格：global_constraints 不限，priority = "hard"，schema 详细

**约束设置原则**：
- 约束要真正可验证，避免空泛描述（如"内容准确"不如"必须给出3条以上具体建议"）
- 禁止为低风险任务添加结构性约束（字段/格式要求）

## 第二步：步骤数-输出长度约束（必须设置）
根据步骤总数，在 global_constraints 中**必须添加一条输出长度约束**：
- 1~2步：每步输出无字数硬限制
- 3步：每步输出建议 ≤ 400 字
- 4步：每步输出建议 ≤ 300 字
- 5~6步：每步输出建议 ≤ 200 字
（步骤越多，每步越应聚焦核心，避免堆砌）

## 第三步：制定结构化语义
1. 结合 context 黑板中的用户特征，在 Planner 定义的 user_goal 基础上进一步细化用户目标
2. 按风险评估结果制定全局约束（含步骤输出长度约束）
3. 列出显式假设（仅列出真正重要的，避免臆造）
4. 结合**本提示"你需要为之制定局部约束的第一个 SubAgent 的任务"**中的步骤 1 内容，制定针对该步骤的局部约束和输出结构

## 输出要求
请严格输出以下 JSON，不要包含其他字段：
{{
  "refined_user_goal": "细化后的具体目标",
  "task_risk": "low|medium|high",
  "task_complexity": "simple|complex",
  "global_constraints": [
    {{
      "constraint": "约束描述",
      "type": "semantic|logic|format",
      "priority": "hard|soft"
    }}
  ],
  "assumptions": ["显式假设1"],
  "next_step_instruction": {{
    "local_constraint": {{
      "constraint": "针对步骤1（{first_step_title}）的具体约束，不要泛泛描述",
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

注意：task_complexity 字段仍需输出（"simple" 对应低风险，"complex" 对应中/高风险）"""


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

    steps = board.get("plan", {}).get("steps", [])
    step_count = len(steps)
    _first_step = steps[0] if steps else {}
    first_step_title = _first_step.get("title") or _first_step.get("brief_description") or "（待定）"
    first_step_description = _first_step.get("description") or ""
    logger.info("[Warmup] START user=%s similar_tasks=%d step_count=%d first_step=%r",
                user_id, len(retrieved_memory.get("similar_tasks", [])), step_count, first_step_title)
    prompt = _WARMUP_PROMPT_TEMPLATE.format(
        context_board=_context_board_summary(board),
        user_input=user_input,
        memory_context=memory_context,
        step_count=step_count,
        first_step_title=first_step_title,
        first_step_description=first_step_description,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=90, _agent_label="Warmup")
        data = _parse_json_output(text)
        if data:
            if data.get("refined_user_goal"):
                board["plan"]["user_goal"] = data["refined_user_goal"]
            board["check"]["global_constraints"] = data.get("global_constraints", [])
            board["check"]["assumptions"] = data.get("assumptions", [])
            # 写入任务风险等级和复杂度，供 QA Agent 调整验证阈值
            if data.get("task_risk"):
                board["check"]["task_risk"] = data["task_risk"]
            if data.get("task_complexity"):
                board["check"]["task_complexity"] = data["task_complexity"]
            logger.info("[Warmup] refined_goal_chars=%d global_constraints=%d assumptions=%d task_risk=%s task_complexity=%s",
                        len(data.get("refined_user_goal", "")),
                        len(data.get("global_constraints", [])),
                        len(data.get("assumptions", [])),
                        data.get("task_risk", "unknown"),
                        data.get("task_complexity", "unknown"))
        else:
            logger.warning("[Warmup] JSON parse failed, returning None")
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

## 任务复杂度判断（影响验证阈值）
首先判断本次任务是否为**简单信息类任务**（推荐、介绍、解释、列举、总结等，不需要精确计算或外部系统交互）。
- 若 context 黑板中 task_complexity 字段为 "simple"，或根据任务描述判断为简单任务：使用**宽松阈值**
- 否则：使用**严格阈值**

## 验证流程（按顺序执行，发现错误后继续检查直到遍历完所有条目）

Step 1: 检查 expected_output_schema
- 若 validation_rules 为空数组 [] 或 schema 极简，跳过此步直接 PASS
- 否则检查结果是否满足输出结构要求（hard，不满足 → REDO）

Step 2: 检查 context.check.global_constraints（priority=hard）+ local_constraint（priority=hard）
- 简单任务：若 local_constraint.priority 为 "soft"，本步骤不因 local_constraint 失败而 REDO
- 复杂任务：任意一条不满足 → REDO

Step 3: 检查 context.check.assumptions 一致性
- 简单任务：assumptions 条数 ≤ 2 时，只有明显矛盾才判失败
- 复杂任务：output 是否与显式假设一致，不满足 → REDO

Step 4: 检查 local_constraint 中 priority=soft 的部分，使用 LLM judge
- **简单任务**：confidence < 0.4 视为失败 → REDO（宽松）
- **复杂任务**：confidence < 0.6 视为失败 → REDO（严格）

Step 5: 对 context 中 user 和 plan 字段整体进行 LLM judge，判断是否偏离整体任务目标
- **简单任务**：confidence < 0.6 → REPLAN（宽松）
- **复杂任务**：confidence < 0.8 → REPLAN（严格）

注意：
- 先完成所有检查再汇总 verdict，不要提前终止
- 遍历发现所有错误，写入 failure_reason 列表
- **内容基本达意、质量合理即视为合格，不要因格式细节或非关键字段缺失判定失败**

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
- Step 5 失败 → REPLAN
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
    logger.info("[QA] START step=%s(%r) result_chars=%d has_local_constraint=%s",
                step.step_id, step.title, len(result), bool(local_constraint))
    prompt = _QA_PROMPT_TEMPLATE.format(
        context_board=_context_board_summary(board),
        step_info=json.dumps({"step_id": step.step_id, "title": step.title, "description": step.description}, ensure_ascii=False),
        result=result[:3000],
        local_constraint=json.dumps(local_constraint, ensure_ascii=False),
        expected_schema=json.dumps(expected_schema, ensure_ascii=False),
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60, _agent_label="QA")
        data = _parse_json_output(text)
        if data and "verdict" in data:
            if isinstance(data.get("failure_reason"), dict):
                data["failure_reason"] = [data["failure_reason"]]
            elif not isinstance(data.get("failure_reason"), list):
                data["failure_reason"] = []
            verdict = data["verdict"]
            failure_count = len(data.get("failure_reason", []))
            logger.info("[QA] VERDICT=%s step=%s failure_reasons=%d", verdict, step.step_id, failure_count)
            if verdict != "PASS" and failure_count:
                for fr in data["failure_reason"][:3]:
                    logger.info("[QA]   reason: %s (suggestion: %s)",
                                str(fr.get("description", ""))[:100],
                                str(fr.get("suggestion", ""))[:80])
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
    logger.info("[QA-final] START user=%s final_output_chars=%d", user_id, len(final_output))
    prompt = _QA_FINAL_PROMPT.format(
        user_goal=board["plan"].get("user_goal", ""),
        context_board=_context_board_summary(board),
        final_output=final_output[:2000],
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60, _agent_label="QA-final")
        data = _parse_json_output(text)
        if data and "plan_suggestion" in data:
            logger.info("[QA-final] suggestion_chars=%d", len(data.get("plan_suggestion", "")))
            return data
    except Exception as exc:
        logger.warning("[QA-final] failed: %s", exc)
    return {"plan_suggestion": ""}


# ═══════════════════════════════════════════════════════════════════════════════
# Summary Agent
# ═══════════════════════════════════════════════════════════════════════════════

_SUMMARY_PROMPT_TEMPLATE = """你是一名助手，请根据以下信息直接给出最终回答。

## 用户的原始目标
{user_goal}

## 各步骤执行摘要
{step_summaries}

## 最后一步的详细输出（参考）
{last_step_output}

## 任务类型
{task_complexity_hint}

## 严格要求
- 直接输出最终回答，不得包含任何分析过程、自我推理或中间思考（例如"让我分析一下"、"我注意到"、"重新理解"等）
- 不得提及"步骤"、"计划"、"agent"、"摘要"等系统内部概念
- 语言自然流畅，像助手直接回答用户
- 内容完整，整合所有关键信息
- {format_instruction}
- 不要在回答末尾附加 JSON 块或其他结构化数据
- 直接以回答内容开头，不要有任何前置说明或过渡语

最终回答："""


async def _run_summary(
    board: Dict[str, Any],
    step_summaries: List[str],
    last_step_output: str,
    model_name: str,
    user_id: str,
) -> str:
    """Generate a user-facing summary of all plan steps. Returns markdown text."""
    logger.info("[Summary] START user=%s steps=%d last_output_chars=%d",
                user_id, len(step_summaries), len(last_step_output))
    summaries_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(step_summaries)) or "（无步骤摘要）"

    task_complexity = board.get("check", {}).get("task_complexity", "complex")
    if task_complexity == "simple":
        task_complexity_hint = "简单信息类任务（确认、记录、推荐等）"
        format_instruction = "使用自然语言纯文本回答，不要使用 Markdown 标题、加粗或列表等格式标记"
    else:
        task_complexity_hint = "复杂执行类任务（数据分析、多步计算、报告生成等）"
        format_instruction = "可使用 Markdown 格式排版（标题、列表等），让内容清晰易读"

    prompt = _SUMMARY_PROMPT_TEMPLATE.format(
        user_goal=board["plan"].get("user_goal", ""),
        step_summaries=summaries_text,
        last_step_output=last_step_output[:3000],
        task_complexity_hint=task_complexity_hint,
        format_instruction=format_instruction,
    )
    try:
        text = await _call_llm_agent(prompt, model_name, user_id, timeout=60, _agent_label="Summary")
        logger.info("[Summary] DONE output_chars=%d", len(text))
        return text.strip()
    except Exception as exc:
        logger.warning("[Summary] failed, falling back to last step output: %s", exc)
        return last_step_output
