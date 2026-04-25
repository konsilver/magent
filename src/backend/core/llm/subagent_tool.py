"""call_subagent tool — allows the main agent to dispatch tasks to sub-agents.

Each sub-agent runs in an isolated thread with its own event loop to avoid
anyio cancel-scope cross-task errors from MCP clients.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

logger = logging.getLogger(__name__)

# Thread pool for sub-agent execution.
# Each thread gets its own event loop so anyio cancel scopes stay within
# a single task — avoiding the cross-task RuntimeError.
_subagent_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="subagent")


def _run_subagent_in_thread(
    agent_id: str,
    agent_name: str,
    task: str,
    context_summary: str,
    current_user_id: str,
    shared_messages: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str]:
    """Run a single sub-agent inside a *new* event loop on a worker thread.

    Returns (True, response_text) on success, (False, error_message) on failure.
    """
    async def _inner() -> str:
        from core.db.engine import SessionLocal
        from core.services.user_agent_service import UserAgentService
        from core.llm.agent_factory import create_agent_executor
        from core.llm.mcp_manager import close_clients
        from agentscope.message import Msg
        from core.llm.message_compat import strip_thinking, load_session_into_memory

        with SessionLocal() as db:
            svc = UserAgentService(db)
            user_agent = svc.get_raw_by_id(agent_id, user_id=current_user_id)
            _ = user_agent.mcp_server_ids, user_agent.skill_ids, user_agent.kb_ids
            _ = user_agent.system_prompt, user_agent.model_provider_id
            _ = user_agent.max_iters, user_agent.temperature, user_agent.max_tokens, user_agent.timeout

        agent, mcp_clients = await create_agent_executor(
            user_agent=user_agent,
            current_user_id=current_user_id,
            isolated=True,
        )
        try:
            # 加载共享上下文到子智能体内存
            if shared_messages:
                await load_session_into_memory(shared_messages, agent.memory)

            prompt_parts = []
            if context_summary:
                prompt_parts.append(f"对话背景：{context_summary}")
            prompt_parts.append(f"用户任务：{task}")
            prompt = "\n\n".join(prompt_parts)

            user_msg = Msg(name="user", content=prompt, role="user")
            result = await agent.reply(user_msg)
            response_text = result.get_text_content() or ""
            response_text = strip_thinking(response_text)
            return True, response_text
        finally:
            try:
                await close_clients(mcp_clients)
            except BaseException as exc:
                logger.debug("close_clients error (ignored): %s", exc)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    except Exception as e:
        logger.error(
            "subagent thread failed: agent=%s, error=%s",
            agent_name, e, exc_info=True,
        )
        return False, str(e)[:200]
    finally:
        loop.close()


def register_subagent_tool(
    toolkit: Toolkit,
    visible_agents: List[Dict[str, Any]],
    current_user_id: str,
    agent_ref: Optional[Dict] = None,
) -> None:
    """Register the call_subagent tool into the main agent's toolkit.

    Args:
        agent_ref: Mutable container ``{"agent": None}`` — set to the main
            agent instance after creation.  Used to extract shared context
            for sub-agents that have ``extra_config.shared_context == True``.
    """
    agent_map = {a["agent_id"]: a for a in visible_agents}

    async def call_subagent(
        agent_id: str,
        task: str,
        context_summary: str = "",
    ) -> ToolResponse:
        """调用子智能体执行专业任务。子智能体拥有独立的工具和专业知识。

        子智能体看不到当前对话历史，因此 task 必须包含足够的背景信息。
        像给一个刚加入的同事布置任务一样编写 task：说明要完成什么、为什么、
        已知什么信息、需要回答什么具体问题。
        不要委托理解——不要写"根据你的发现帮我总结"，而是说明具体要分析什么。

        需要并行调用多个子智能体时，在同一轮回复中生成多个 call_subagent 调用，
        系统会自动并行执行。

        Args:
            agent_id (`str`):
                要调用的子智能体 ID（参见系统提示中的可用子智能体列表）。
            task (`str`):
                完整的任务描述。必须包含：要完成什么及为什么、已知的背景信息、
                需要回答的具体问题。简短的命令式指令会导致低质量结果。
            context_summary (`str`):
                当前对话的关键背景摘要（可选），帮助子智能体理解上下文。
                应包含与任务相关的已知事实，而非完整对话记录。

        Returns:
            `ToolResponse`:
                子智能体的执行结果。结果对用户不可见，你需要汇总后呈现给用户。
        """
        if agent_id not in agent_map:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"错误：子智能体 {agent_id} 不存在或无权访问。请检查 ID 是否正确。",
            )])

        agent_info = agent_map[agent_id]
        agent_name = agent_info.get("name", agent_id)

        # 检查是否启用共享上下文
        shared_context = (agent_info.get("extra_config") or {}).get("shared_context", False)
        shared_messages = None
        if shared_context and agent_ref and agent_ref.get("agent"):
            try:
                from core.llm.message_compat import extract_messages_from_memory
                shared_messages = await extract_messages_from_memory(agent_ref["agent"].memory)
                logger.info(
                    "[subagent_tool] shared_context enabled for agent=%s, messages=%d",
                    agent_name, len(shared_messages),
                )
            except Exception as exc:
                logger.warning("[subagent_tool] shared_context extraction failed: %s", exc)

        try:
            loop = asyncio.get_running_loop()
            ok, text = await loop.run_in_executor(
                _subagent_pool,
                _run_subagent_in_thread,
                agent_id, agent_name, task, context_summary, current_user_id,
                shared_messages,
            )

            if not ok:
                return ToolResponse(content=[TextBlock(
                    type="text",
                    text=f"子智能体「{agent_name}」执行出错：{text}",
                )])

            logger.info(
                "[subagent_tool] call_subagent completed: agent=%s, task_len=%d, response_len=%d",
                agent_name, len(task), len(text),
            )

            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"【{agent_name}】的回复：\n\n{text}",
            )])

        except Exception as e:
            logger.error("call_subagent failed: agent=%s, error=%s", agent_id, e, exc_info=True)
            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"子智能体「{agent_name}」执行出错：{str(e)[:200]}",
            )])

    toolkit.register_tool_function(call_subagent, namesake_strategy="skip")


def _get_tools_desc(agent_info: Dict[str, Any]) -> str:
    """Return a short comma-separated list of MCP tools available to the agent."""
    mcp_ids = agent_info.get("mcp_server_ids") or []
    if not mcp_ids:
        return "默认工具集"
    return ", ".join(mcp_ids)


def build_subagent_prompt_section(
    visible_agents: List[Dict[str, Any]],
    mentioned_agent_ids: Optional[List[str]] = None,
) -> str:
    """Build the system prompt section describing available sub-agents."""
    if not visible_agents:
        return ""

    rows = []
    shared_agents = []
    for a in visible_agents:
        desc = a.get("description", "")
        tools = _get_tools_desc(a)
        has_shared = (a.get("extra_config") or {}).get("shared_context", False)
        ctx_col = "是" if has_shared else "否"
        rows.append(f"| {a['agent_id']} | {a['name']} | {desc} | {tools} | {ctx_col} |")
        if has_shared:
            shared_agents.append(a["name"])

    table = "| ID | 名称 | 适用场景 | 可用工具 | 共享上下文 |\n|---|---|---|---|---|\n" + "\n".join(rows)

    section = (
        "## 可用子智能体\n\n"
        "你可以通过 `call_subagent` 工具将专业任务分派给子智能体处理。"
        "每个子智能体拥有独立的工具和专业知识。\n\n"
        + table + "\n\n"
        "### 何时使用子智能体\n"
        "- 任务需要子智能体拥有的专业工具（参见上表「可用工具」列）\n"
        "- 用户通过 @名称 明确指定时\n"
        "- 需要多个独立信息源时，在同一轮并行调用多个子智能体以提高效率\n\n"
        "### 何时不使用子智能体\n"
        "- 你自己的工具已能完成的单步查询或操作\n"
        "- 简单问答或你已有足够信息直接回答的问题\n"
        "- 不确定是否需要时，优先自己处理\n\n"
        "### 编写 task 描述的要求\n"
        "除标注「共享上下文=是」的子智能体外，其余子智能体看不到当前对话历史。"
        "像给一个刚加入的同事布置任务一样编写 task：\n"
        "- 说明要完成什么，以及为什么需要这个信息\n"
        "- 描述你已经了解到或排除了什么\n"
        "- 提供足够的背景让子智能体能做判断，而不是死板执行\n"
        "- 如果需要简短回复，明确说明（如「200字以内」）\n"
        "- **不要委托理解**——不要写「根据你的分析帮我总结」，"
        "而是说明具体要查什么数据、对比什么指标、回答什么问题\n\n"
        "### 共享上下文子智能体\n"
        "标注「共享上下文=是」的子智能体能自动读取当前完整对话历史（含工具调用结果），"
        "无需在 task 中重复传递已有信息。对这类子智能体，task 只需简洁说明要执行的操作。\n\n"
        "### 处理结果\n"
        "- 子智能体的回复对用户不可见，你必须汇总整合后呈现给用户\n"
        "- 多个子智能体的结果需要你做综合分析，不要简单拼接\n"
    )

    if mentioned_agent_ids:
        agent_map = {a["agent_id"]: a["name"] for a in visible_agents}
        names = [agent_map.get(aid, aid) for aid in mentioned_agent_ids if aid in agent_map]
        if names:
            section += (
                f"\n**用户已指定调用子智能体：{'、'.join(names)}。"
                "请直接使用 call_subagent 工具调用指定的子智能体。**\n"
            )

    return section
