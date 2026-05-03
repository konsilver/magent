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

# ── Bare-agent instance cache (reuse across QA / Warmup / Planner calls) ─────
# Key: model_name string. Value: asyncio.Lock + agent instance.
# We keep at most one cached agent per model_name. Each call acquires the lock,
# resets the agent's memory, runs the LLM call, then releases the lock.
# This eliminates repeated ReActAgent.__init__ overhead for sequential calls.

_BARE_AGENT_CACHE: Dict[str, Any] = {}  # model_name -> {"agent": ReActAgent, "lock": asyncio.Lock}
_BARE_AGENT_CACHE_LOCK = asyncio.Lock()


async def _get_cached_bare_agent(model_name: str) -> Any:
    """Return a cached bare agent for model_name, creating it if needed.

    The caller must hold (and release) the per-agent lock returned alongside.
    """
    from core.llm.agent_factory import _create_bare_llm_agent
    from agentscope.memory import InMemoryMemory
    from core.llm.hooks import ModelContext

    async with _BARE_AGENT_CACHE_LOCK:
        entry = _BARE_AGENT_CACHE.get(model_name)
        if entry is None:
            agent, _ = await _create_bare_llm_agent(model_name, None)
            entry = {"agent": agent, "lock": asyncio.Lock()}
            _BARE_AGENT_CACHE[model_name] = entry

    return entry


async def _call_llm_agent_cached(
    prompt: str,
    model_name: str,
    user_id: str,
    timeout: int = 120,
    _agent_label: str = "LLMAgent",
) -> str:
    """Like _call_llm_agent but reuses a cached bare-agent instance per model_name.

    Falls back to creating a fresh agent if the cached agent is busy (lock held).
    """
    _t0 = _time.monotonic()
    logger.info("[%s] START model=%s user=%s prompt_chars=%d timeout=%ds",
                _agent_label, model_name, user_id, len(prompt), timeout)

    from agentscope.memory import InMemoryMemory
    from agentscope.message import Msg
    from core.llm.hooks import ModelContext

    entry = await _get_cached_bare_agent(model_name)
    agent_lock: asyncio.Lock = entry["lock"]

    # Try to acquire the lock without waiting; if busy, fall back to a fresh agent.
    # asyncio.Lock has no try_acquire, so we use a zero-timeout wait_for.
    lock_acquired = False
    agent = None
    mcp_clients: list = []

    if not agent_lock.locked():
        try:
            await asyncio.wait_for(agent_lock.acquire(), timeout=0.01)
            lock_acquired = True
        except (asyncio.TimeoutError, Exception):
            lock_acquired = False

    if lock_acquired:
        agent = entry["agent"]
        # Reset per-call state
        agent.memory = InMemoryMemory()
        agent._jx_context = ModelContext()  # type: ignore[attr-defined]
        logger.debug("[%s] using cached bare agent for model=%s", _agent_label, model_name)
    else:
        # Cached agent is in use — create a fresh one for this call
        logger.debug("[%s] cached agent busy, creating fresh for model=%s", _agent_label, model_name)
        from core.llm.agent_factory import _create_bare_llm_agent
        agent, mcp_clients = await _create_bare_llm_agent(model_name, user_id)

    try:
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
        logger.info("[%s] DONE elapsed=%.0fms output_chars=%d",
                    _agent_label, _elapsed, len(result))
        return result
    except asyncio.TimeoutError:
        logger.warning("[%s] TIMEOUT after %ds", _agent_label, timeout)
        raise
    except Exception as exc:
        logger.warning("[%s] ERROR after %.0fms: %s", _agent_label, (_time.monotonic() - _t0) * 1000, exc)
        raise
    finally:
        if lock_acquired:
            agent_lock.release()
        await close_clients(mcp_clients)


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
    _prompt_chars = len(prompt)
    logger.info("[%s] START model=%s user=%s prompt_chars=%d timeout=%ds",
                _agent_label, model_name, user_id, _prompt_chars, timeout)
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
        logger.info("[%s] DONE elapsed=%.0fms output_chars=%d",
                    _agent_label, _elapsed, len(result))
        return result
    except asyncio.TimeoutError:
        logger.warning("[%s] TIMEOUT after %ds", _agent_label, timeout)
        raise
    except Exception as exc:
        logger.warning("[%s] ERROR after %.0fms: %s", _agent_label, (_time.monotonic() - _t0) * 1000, exc)
        raise
    finally:
        await close_clients(mcp_clients)


async def _call_llm_agent_with_code_exec(
    prompt: str,
    model_name: str,
    user_id: str,
    timeout: int = 120,
    _agent_label: str = "LLMAgentCodeExec",
) -> str:
    """Call an agent with execute_code tool enabled, return stripped text output.

    Uses the bare-agent fast path (no MCP connections) and registers only the
    execute_code tool, avoiding latency and failures from unrelated MCP servers.
    """
    _t0 = _time.monotonic()
    logger.info("[%s] START model=%s user=%s prompt_chars=%d timeout=%ds",
                _agent_label, model_name, user_id, len(prompt), timeout)
    from core.llm.agent_factory import _create_bare_llm_agent
    from core.llm.tool import register_execute_code_tools
    agent, mcp_clients = await _create_bare_llm_agent(model_name, user_id)
    agent.max_iters = 5
    register_execute_code_tools(agent.toolkit, user_id=user_id)
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
        logger.info("[%s] DONE elapsed=%.0fms output_chars=%d",
                    _agent_label, _elapsed, len(result))
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
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=60, _agent_label="UserProfileAgent")
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
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=60, _agent_label="UserProfile")
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

_PLANNER_FIRST_PROMPT = """你是 Planner Agent，负责将用户任务拆解为一组线性、可执行的步骤。

你的职责：定义「做什么」（宏观任务分解），不涉及「如何做」，不包含执行细节。同时结合用户特征与任务内容，生成一句话作为计划标题（user_goal）。

## 用户特征（context.user）
{user_context}

优先级规则：
- context.user 字段（用户实时特征）的优先级**高于**历史记忆中任何 suggestion

## 历史记忆参考
{memory_context}

## 对话历史（最近3轮）与最新请求
{conversation_and_query}

## 输出要求
请严格输出以下 JSON，不要包含其他字段：
{{
  "user_goal": "结合用户特征与任务内容，一句话描述用户目标（≤20字，作为计划标题）",
  "steps": [
    {{
      "step_id": 1,
      "brief_description": "步骤简述（≤10字）",
      "description": "步骤任务详细描述",
      "complexity": "simple | complex"
    }}
  ]
}}

规则：
- user_goal 要简洁精准，体现用户核心诉求，不超过20字
- **步骤解耦原则**：每个 step 应尽量独立，避免步骤之间强耦合
- **步骤数量限制**：简单任务（问答、推荐、解释）2~3步；复杂任务最多不超过6步
- **最后一个步骤的任务应该是整理总结前面步骤任务结果，用来回答用户任务**
- **如果步骤是需要代码执行的任务，则在该任务描述中不仅要包含代码的生成，还要包含代码模块的测试验证（不要专门制造一个步骤专门验证代码）**
- **complexity 分类规则**：
  - "simple"：信息检索、知识问答、内容推荐、解释概念、短文本生成（摘要/总结/翻译/格式转换）、列举/比较/选择、整合已有文本结果的汇总步骤等依赖模型自身知识就能完成的任务
  - "complex"：联网搜索、执行代码、文件操作、方案设计、架构分析等需要借助外部工具的任务"""


_PLANNER_REPLAN_PROMPT = """你是 Planner Agent，当前计划执行过程中某步骤失败，需要从指定步骤开始重新规划。

你的职责：定义「做什么」（宏观任务分解），不涉及「如何做」，不包含执行细节。

## 用户特征（context.user）
{user_context}

优先级规则：
- context.user 字段（用户实时特征）的优先级**高于**历史记忆中任何 suggestion

## 历史记忆参考
{memory_context}

## 当前计划状态（context.plan）
{plan_context}

## 【重要】重新规划指令
- 从 step_id = {redo_id} 开始重新规划（该步及之后的步骤全部重新制定，之前已完成的步骤保持不变）
- QA 给出的优化建议：{suggestion}
- 尽量根据建议修正导致失败的问题

## 对话历史（最近3轮）与最新请求
{conversation_and_query}

## 输出要求
请严格输出以下 JSON，不要包含其他字段：
{{
  "steps": [
    {{
      "step_id": 1,
      "brief_description": "步骤简述（≤10字）",
      "description": "步骤任务详细描述（只描述做什么，不包含约束、格式、实现方式）",
      "complexity": "simple | complex"
    }}
  ]
}}

注意：输出的 steps 只包含从 step_id={redo_id} 开始的新步骤，step_id 从 {redo_id} 开始编号

规则：
- **步骤解耦原则**：每个 step 应尽量独立，避免步骤之间强耦合
- **步骤数量限制**：简单任务（问答、推荐、解释）2~3步；复杂任务最多不超过6步
- **最后一个步骤的任务应该是整理总结前面步骤任务结果，用来回答用户任务**
- **如果步骤是需要代码执行的任务，则在该任务描述中不仅要包含代码的生成，还要包含代码模块的测试验证（不要专门制造一个步骤专门验证代码）**
- **complexity 分类规则**：
  - "simple"：信息检索、知识问答、内容推荐、解释概念、短文本生成（摘要/总结/翻译/格式转换）、列举/比较/选择、整合已有文本结果的汇总步骤等依赖模型自身知识就能完成的任务
  - "complex"：联网搜索、执行代码、文件操作、方案设计、架构分析等需要借助外部工具的任务"""



_INTENT_CLASSIFY_PROMPT = """你是意图分类助手。你的唯一任务是判断用户对当前计划的态度，并输出一个 JSON。

## 用户回复内容
{user_reply}

## 判断规则
- "confirm"：用户明确同意、确认、接受当前计划，想开始执行
  确认的典型表达：好的、确认、开始吧、没问题、可以、执行、同意、就按这个、确认执行、开始执行、行、好
- "replan"：用户对计划不满意、提出修改建议、要求重新规划、或在回复中包含新的方向性意见
  重新规划的典型表达：重新规划、我想换个方向、能不能改成...、我特别想...、不对、不好、再想想

## 重要
- 如果用户回复非常简短且只表达同意（如"好"、"好的"、"确认"、"可以"、"执行"），必须判断为 "confirm"
- 只要用户回复中含有实质性的修改建议或方向性意见，才判断为 "replan"
- 不得输出任何分析过程或解释，只输出下面格式的 JSON，不要有任何其他文字

输出格式（严格遵守）：
{{"intent": "confirm"}}
或
{{"intent": "replan"}}"""

# 快速关键词匹配：用户输入中明确表示确认的词汇（在 LLM 解析失败时使用）
_CONFIRM_KEYWORDS = {"确认", "执行", "好的", "好", "可以", "开始", "同意", "没问题", "行", "继续", "ok", "yes", "确定"}
_REPLAN_KEYWORDS = {"重新", "重规划", "修改", "不对", "不行", "不好", "换个", "改成", "建议"}


async def _classify_user_intent(user_reply: str, model_name: str, user_id: str) -> str:
    """Classify user reply as 'confirm' (execute plan) or 'replan' (redo planning).

    Returns 'confirm' or 'replan'.
    """
    logger.info("[IntentClassify] classifying user reply (len=%d): %r", len(user_reply), user_reply[:80])

    # 快速路径：对极短的纯确认输入直接返回 confirm，跳过 LLM 调用
    reply_stripped = user_reply.strip()
    if len(reply_stripped) <= 10:
        reply_lower = reply_stripped.lower()
        if any(kw in reply_stripped or kw in reply_lower for kw in _CONFIRM_KEYWORDS):
            logger.info("[IntentClassify] fast-path confirm (short keyword match): %r", reply_stripped)
            return "confirm"

    prompt = _INTENT_CLASSIFY_PROMPT.format(user_reply=user_reply[:500])
    try:
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=20, _agent_label="IntentClassify")
        data = _parse_json_output(text)
        if data and data.get("intent") in ("confirm", "replan"):
            logger.info("[IntentClassify] intent=%s", data["intent"])
            return data["intent"]

        # JSON 解析失败：先匹配 confirm 关键词，再匹配 replan 关键词，最后才 fallback
        raw = (text or "").strip()
        raw_lower = raw.lower()
        # LLM 输出中若含有 "confirm" 字符串视为 confirm
        if '"confirm"' in raw_lower or "'confirm'" in raw_lower:
            logger.warning("[IntentClassify] parse failed but found confirm in raw output: %r", raw[:100])
            return "confirm"
        if '"replan"' in raw_lower or "'replan'" in raw_lower:
            logger.warning("[IntentClassify] parse failed but found replan in raw output: %r", raw[:100])
            return "replan"

        # 再从用户原始输入做关键词兜底（而非从 LLM 输出中找）
        reply_lower = user_reply.lower()
        if any(kw in user_reply or kw in reply_lower for kw in _CONFIRM_KEYWORDS):
            logger.warning("[IntentClassify] parse failed, user input matched confirm keyword, raw=%r", raw[:100])
            return "confirm"
        if any(kw in user_reply for kw in _REPLAN_KEYWORDS):
            logger.warning("[IntentClassify] parse failed, user input matched replan keyword, raw=%r", raw[:100])
            return "replan"

        logger.warning("[IntentClassify] parse failed and no keyword matched, defaulting to replan: %r", raw[:100])
    except Exception as exc:
        logger.warning("[IntentClassify] failed (defaulting to replan): %s", exc)
    return "replan"


def _format_conversation_and_query(session_messages: List[Dict[str, Any]], latest_query: str) -> str:
    """Take last 6 messages (= 3 rounds) from session_messages and append the latest query."""
    recent = session_messages[-6:] if len(session_messages) > 6 else session_messages
    lines = []
    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        role_label = "用户" if role == "user" else "助手"
        lines.append(f"[{role_label}]: {content}")
    lines.append(f"[用户（最新）]: {latest_query}")
    return "\n".join(lines)


async def _run_planner(
    user_input: str,
    user_id: str,
    model_name: str,
    retrieved_memory: Dict,
    board: Dict[str, Any],
    session_messages: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Run Planner Agent, write steps to context board, return plan dict.

    Automatically selects first-time or replan prompt based on board["plan"]["redo_id"].
    """
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
    conversation_and_query = _format_conversation_and_query(session_messages or [], user_input)

    redo_id = board.get("plan", {}).get("redo_id", -1)
    is_replan = redo_id != -1
    _label = "Planner(replan)" if is_replan else "Planner"

    logger.info("[%s] START user=%s input_chars=%d similar_tasks=%d graph_plans=%d redo_id=%s",
                _label, user_id, len(user_input),
                len(retrieved_memory.get("similar_tasks", [])),
                len(retrieved_memory.get("graph_plans", [])),
                redo_id)

    if is_replan:
        suggestion = board.get("plan", {}).get("suggestion", "（无建议）") or "（无建议）"
        plan_context = json.dumps(board.get("plan", {}), ensure_ascii=False)
        prompt = _PLANNER_REPLAN_PROMPT.format(
            user_context=user_context,
            memory_context=memory_context,
            plan_context=plan_context,
            redo_id=redo_id,
            suggestion=suggestion,
            conversation_and_query=conversation_and_query,
        )
    else:
        prompt = _PLANNER_FIRST_PROMPT.format(
            user_context=user_context,
            memory_context=memory_context,
            conversation_and_query=conversation_and_query,
        )

    try:
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=90, _agent_label=_label)
        data = _parse_json_output(text)
        if data:
            steps = data.get("steps", [])
            if is_replan:
                # 局部重规划：只替换 redo_id 及之后的步骤，保留已完成步骤
                existing_steps = board["plan"].get("steps", [])
                kept_steps = [s for s in existing_steps if s.get("step_id", 0) < redo_id]
                new_steps = [
                    {
                        "step_id": s.get("step_id", redo_id + i),
                        "brief_description": s.get("brief_description", ""),
                        "description": s.get("description", s.get("title", "")),
                        "complexity": s.get("complexity", "complex"),
                        "output": None,
                        "suggestion": None,
                        "tool_use_trace": [],
                    }
                    for i, s in enumerate(steps)
                ]
                board["plan"]["steps"] = kept_steps + new_steps
                board["plan"]["redo_id"] = -1
                board["plan"]["suggestion"] = None
            else:
                board["plan"]["steps"] = [
                    {
                        "step_id": s.get("step_id", i + 1),
                        "brief_description": s.get("brief_description", ""),
                        "description": s.get("description", s.get("title", "")),
                        "complexity": s.get("complexity", "complex"),
                        "output": None,
                        "suggestion": None,
                        "tool_use_trace": [],
                    }
                    for i, s in enumerate(steps)
                ]
            logger.info("[%s] plan generated: steps=%d complexities=%s", _label, len(steps),
                        [s.get("complexity", "missing") for s in steps])
        else:
            logger.warning("[%s] JSON parse failed, returning None", _label)
        return data
    except Exception as exc:
        logger.error("[%s] failed: %s", _label, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Warmup Agent
# ═══════════════════════════════════════════════════════════════════════════════

_WARMUP_PROMPT_TEMPLATE = """你是 Warmup Agent，你的任务是：
1. 为整个计划制定全局约束（global_constraints）
2. 为计划的第一个步骤制定局部约束（local_constraint）和输出格式（expected_output_schema）

## 用户特征（context.user）
{user_context}

## 已制定的计划步骤（context.plan.steps）
{plan_steps}

## 历史相似任务记忆（含失败建议）
{memory_context}

## 对话历史（最近3轮）与最新请求
{conversation_and_query}

---

## 约束生成规则

### Step 1：判断任务风险等级
根据任务整体性质判断：
- **低风险**：推荐书籍、回答问题、解释概念、给出建议等——输出即使有偏差用户可轻易识别和纠正
  → global_constraints ≤ 2 条（仅内容质量类），local_constraint 中约束以 priority="soft" 为主，expected_output_schema.fields 可为空
- **中风险**：数据分析、报告撰写、方案设计等——输出错误有一定代价但可补救
  → global_constraints ≤ 4 条，可有部分 priority="hard" 的约束
- **高风险**：代码执行、系统设计（用户已明确定义的架构/接口规范）、精确计算、需调用外部工具——输出错误代价高或难以发现
  → global_constraints 不限，约束 priority="hard"，schema 详细

### Step 2：生成 expected_output_schema（先于 constraint 生成）
- fields：列出第一步输出应包含的所有字段
- required：必须是 fields 的子集

### Step 3：生成 local_constraint
规则：
- 每个 required 字段必须有对应的 field_presence constraint
- 禁止引用 fields 中未定义的字段
- 不允许生成模糊约束（如：合理、尽量、适当）
- 每条 constraint 必须有自己的 priority（"hard" 或 "soft"），软硬约束比例：hard ≥ 60%，soft ≤ 40%
- 低风险任务禁止添加结构性约束
- 如果这个步骤是需要书写代码的任务，则构造对代码模块的预期测试效果的软约束（如任务是写一个双线程打印1~20，则你可以约束“代码执行结果是两个线程轮替打印1~20”）
- 如果这个步骤只是“总结/整理”代码，则不要代码验证效果等约束

### Step 4：生成 global_constraints
规则：
- 约束要真正可验证，避免空泛描述（如"内容准确"不如"必须给出3条以上具体建议"）
- 每条 global_constraint 必须有自己的 priority（"hard" 或 "soft"），软硬约束比例：hard ≥ 60%，soft ≤ 40%
- 不要生成代码约束
- **必须包含一条输出长度约束**（根据步骤总数 {step_count} 条）：
  - 1~2 步：每步输出无字数硬限制
  - 3 步：每步输出建议 ≤ 400 字
  - 4 步：每步输出建议 ≤ 300 字
  - 5~6 步：每步输出建议 ≤ 200 字

---

## 输出要求
请严格输出以下 JSON，不要包含其他字段：
{{
  "global_constraints": [
    {{
      "constraint_type": "field_presence | value_range | format | dependency",
      "target": "字段名",
      "rule": "字段规则",
      "priority": "hard | soft"
    }}
  ],
  "next_step_instruction": {{
    "local_constraint": {{
      "constraint": [
        {{
          "constraint_type": "field_presence | value_range | format | dependency",
          "target": "字段名",
          "rule": "字段规则",
          "priority": "hard | soft"
        }}
      ]
    }},
    "expected_output_schema": {{
      "fields": ["字段1", "字段2"],
      "required": ["字段1"]
    }}
  }}
}}"""


async def _run_warmup(
    user_input: str,
    user_id: str,
    model_name: str,
    retrieved_memory: Dict,
    board: Dict[str, Any],
    session_messages: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Run Warmup Agent; write user_goal and global_constraints to board, return full data."""
    memory_lines = []
    for st in retrieved_memory.get("similar_tasks", []):
        if st:
            memory_lines.append(f"- [相似任务] {st}")
    # 写入历史相似任务的失败建议（来自 graph_plans）
    for gp in retrieved_memory.get("graph_plans", [])[:3]:
        suggestion = gp.get("suggestion", "")
        if suggestion:
            memory_lines.append(f"- [历史建议] {suggestion[:150]}")
    memory_context = "\n".join(memory_lines) if memory_lines else "（暂无记忆）"

    steps = board.get("plan", {}).get("steps", [])
    step_count = len(steps)
    plan_steps_text = "\n".join(
        f"Step {s.get('step_id', i+1)}: {s.get('brief_description', '')} — {s.get('description', '')}"
        for i, s in enumerate(steps)
    ) or "（暂无步骤）"

    user_context = json.dumps(board.get("user", {}), ensure_ascii=False)
    conversation_and_query = _format_conversation_and_query(session_messages or [], user_input)

    logger.info("[Warmup] START user=%s similar_tasks=%d step_count=%d",
                user_id, len(retrieved_memory.get("similar_tasks", [])), step_count)

    prompt = _WARMUP_PROMPT_TEMPLATE.format(
        user_context=user_context,
        plan_steps=plan_steps_text,
        memory_context=memory_context,
        conversation_and_query=conversation_and_query,
        step_count=step_count,
    )
    try:
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=60, _agent_label="Warmup")
        data = _parse_json_output(text)
        if data:
            board["check"]["global_constraints"] = data.get("global_constraints", [])
            logger.info("[Warmup] DONE global_constraints=%d",
                        len(data.get("global_constraints", [])))
        else:
            logger.warning("[Warmup] JSON parse failed, returning None")
        return data
    except Exception as exc:
        logger.error("[Warmup] failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# QA Agent
# ═══════════════════════════════════════════════════════════════════════════════

def _qa_context_board_summary(board: Dict[str, Any]) -> str:
    """QA-specific board summary: only user + global_constraints + user_goal."""
    public = {
        "user": board.get("user", {}),
        "user_goal": board.get("plan", {}).get("user_goal"),
        "global_constraints": board.get("check", {}).get("global_constraints", []),
    }
    return json.dumps(public, ensure_ascii=False, indent=2)


_QA_PROMPT_TEMPLATE = """你是 QA Agent，负责验证其他agent的执行结果。

## context 黑板
{context_board}

## 当前检查的步骤
- step_id: {step_id}
- 标题: {step_title}
- 描述: {step_description}

## SubAgent 执行结果
{result}

## 本步骤约束（上一个 agent 定义，预设结构如下）
约束结构说明（每条 constraint 有独立的 priority）：
- constraint_type: field_presence | value_range | format | dependency
- target: 字段名
- rule: 字段规则
- priority: hard | soft

local_constraint:
{local_constraint}

expected_output_schema:
{expected_schema}

## verdict字段验证流程（按顺序执行，发现错误后继续检查直到遍历完所有条目，不提前终止）

Step 1: 检查 expected_output_schema
- 若 schema 为空或 fields 为空列表，跳过此步
- 否则检查结果是否包含 required 字段→ REDO

Step 2: 检查 global_constraints 中 priority="hard" 的条目 + local_constraint 中 priority="hard" 的条目
- 任意一条 priority="hard" 的约束不满足→ REDO

Step 3: 检查 local_constraint 中 priority="soft" 的条目
- confidence < 0.6 视为失败 → REDO

Step 4: 对 context 中 user_goal 和 user 字段，结合当前步骤描述与执行结果，判断是否偏离整体任务目标
- confidence < 0.8 → REPLAN

注意：
- 若以上步骤全部通过则verdict为PASS
- 遍历发现所有错误，将发现的错误原因或修正建议汇总写入输出结构的suggestion字段
- 如果任务是调用工具类的任务（如设计代码、搜索文档），你只需要根据约束本身判定verdict，而不需要你自己去调用工具

{last_step_hint}

## 输出格式规范

### 代码块

- 所有代码必须放在带语言标识的 Markdown 代码块中：` ```python `、` ```javascript `、` ```bash ` 等
- 多文件代码用注释分隔：`# === filename.py ===`
- 代码中禁止省略关键部分（不写 `# ...其余代码` 这类占位符）

### 执行结果展示

成功（exit_code == 0）：
```
执行成功（exit_code: 0）
输出：
<stdout 关键内容>
```

失败（exit_code != 0）：
```
执行失败（exit_code: N）
stderr：
<完整 stderr 内容>
```

### 数学公式

- 行内公式：$...$
- 独立公式块：$$...$$
- 使用标准 LaTeX 语法

### 通用规范

- 语言：中文输出，技术术语保留英文原文（如 async/await、REST API、HTTP 状态码）
- 结构：复杂步骤用编号列表，并列项用无序列表
- 依赖安装：明确给出命令，如 `pip install requests` 或 `npm install axios`
- 文件说明：生成文件时注明文件名和格式

## 输出要求
请严格输出以下 JSON：
{{
  "verdict": "PASS|REDO|REPLAN",
  "suggestion": "优化建议",
  "plan_suggestion": "针对整个计划的优化建议（仅最后一步填写，其余步骤填空字符串）"
}}"""


async def _run_qa(
    step: Any,
    result: str,
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    model_name: str,
    user_id: str,
    is_last_step: bool = False,
) -> Dict[str, Any]:
    """Run QA Agent, return verdict dict."""
    logger.info("[QA] START step=%s(%r) result_chars=%d is_last=%s",
                step.step_id, step.title, len(result), is_last_step)

    last_step_hint = (
        "## 【最后一步特殊说明】\n"
        "这是计划的最后一步（总结性步骤）。\n"
        "- 若 REPLAN：请在 plan_suggestion 而不是suggestion填写失败原因和优化建议"
    ) if is_last_step else ""

    prompt = _QA_PROMPT_TEMPLATE.format(
        context_board=_qa_context_board_summary(board),
        step_id=step.step_id,
        step_title=step.title,
        step_description=step.description or "",
        result=result[:3000],
        local_constraint=json.dumps(local_constraint, ensure_ascii=False, indent=2),
        expected_schema=json.dumps(expected_schema, ensure_ascii=False, indent=2),
        last_step_hint=last_step_hint,
    )
    try:
        text = await _call_llm_agent_cached(prompt, model_name, user_id, timeout=90, _agent_label="QA")
        data = _parse_json_output(text)
        if data and "verdict" in data:
            verdict = data["verdict"]
            suggestion = data.get("suggestion", "")
            logger.info("[QA] VERDICT=%s step=%s suggestion=%r is_last=%s",
                        verdict, step.step_id, str(suggestion)[:100], is_last_step)
            return data
    except Exception as exc:
        logger.warning("[QA] failed: %s", exc)
    return {"verdict": "PASS", "suggestion": "", "plan_suggestion": ""}


