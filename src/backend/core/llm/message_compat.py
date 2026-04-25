"""Message format conversion between dict messages and AgentScope Msg objects."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg


def dict_to_msg(d: Dict[str, Any]) -> Msg:
    """Convert a dict message (OpenAI format) to an AgentScope Msg."""
    role = d.get("role", "user")
    content = d.get("content", "")
    name = d.get("name", role)

    # Map roles: "human" -> "user", "ai"/"assistant" -> "assistant"
    role_map = {"human": "user", "ai": "assistant"}
    role = role_map.get(role, role)

    # Ensure valid role
    if role not in ("user", "assistant", "system"):
        role = "user"

    return Msg(name=name, content=content, role=role)


def msg_to_dict(msg: Msg) -> Dict[str, Any]:
    """Convert an AgentScope Msg to a dict message (OpenAI format)."""
    return {
        "role": msg.role,
        "content": msg.get_text_content(),
    }


async def load_session_into_memory(
    session_messages: List[Dict[str, Any]],
    memory: InMemoryMemory,
) -> None:
    """Load dict session messages into an InMemoryMemory instance."""
    msgs = [dict_to_msg(m) for m in session_messages if m.get("content")]
    if msgs:
        await memory.add(msgs)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from model output.

    Some thinking models (e.g. DeepSeek R1) emit reasoning wrapped in
    ``<think>...</think>`` tags.  The opening ``<think>`` may be absent.
    """
    if not text:
        return text
    last_end = text.rfind("</think>")
    if last_end != -1:
        return text[last_end + len("</think>"):].lstrip()
    return text


def _format_tool_output(output: Any) -> str:
    """Format tool output for inclusion in shared context messages."""
    if isinstance(output, str):
        return output[:2000] if len(output) > 2000 else output
    try:
        text = json.dumps(output, ensure_ascii=False)
        return text[:2000] if len(text) > 2000 else text
    except (TypeError, ValueError):
        return str(output)[:2000]


async def extract_messages_from_memory(memory: InMemoryMemory) -> list[dict]:
    """从 AgentScope Memory 提取消息为 dict 列表，保留工具调用块。

    用于共享上下文场景：将主智能体的内存传递给子智能体。
    """
    messages: list[dict] = []
    for msg in await memory.get_memory():
        d: dict[str, str] = {"role": msg.role, "content": msg.get_text_content() or ""}

        # 工具调用块序列化为文本附加到 content，确保子智能体能看到
        tool_use_blocks = (
            msg.get_content_blocks("tool_use")
            if msg.has_content_blocks("tool_use")
            else []
        )
        tool_result_blocks = (
            msg.get_content_blocks("tool_result")
            if msg.has_content_blocks("tool_result")
            else []
        )

        if tool_use_blocks:
            tool_desc = "\n".join(
                f"[调用工具 {b.get('name', '')}] 参数: {json.dumps(b.get('input', {}), ensure_ascii=False)}"
                for b in tool_use_blocks
            )
            d["content"] += f"\n\n{tool_desc}"

        if tool_result_blocks:
            result_desc = "\n".join(
                f"[工具结果 {b.get('name', '')}]\n{_format_tool_output(b.get('output', ''))}"
                for b in tool_result_blocks
            )
            d["content"] += f"\n\n{result_desc}"

        messages.append(d)
    return messages


def extract_text_from_chat_response(response: Any) -> str:
    """Extract text content from a ChatResponse or Msg object."""
    # ChatResponse
    content = getattr(response, "content", None)
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)
        return "".join(text_parts)

    return str(content)
