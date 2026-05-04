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


import re as _re

# Lines whose first non-empty content signals agent self-commentary.
_FINAL_REASONING_PATTERN = _re.compile(
    r"^(好的|当然|让我|我来|我需要|我将|我会|我可以|我看到|我注意到|我了解"
    r"|首先|其次|最后|根据|分析|理解|明白|现在|接下来|下面|以下"
    r"|从.{1,20}中.*(?:看到|可以)|从context"
    r"|步骤\d+已完成|步骤\d+[：:]|现在我需要|我需要编写|让我设计|让我来"
    r"|根据局部约束|根据历史|根据上述|根据context|根据以上|根据前面"
    r"|用户要求我|用户希望我|用户想要我"
    r"|必须包含|应包含|需要包含|代码必须|脚本必须"
    r"|okay|sure|let me |i will |i'll |i need to |i can see |i notice"
    r"|first[,， ]|second[,， ]|finally[,， ]|based on |alright|now[,， ]|next[,， ])",
    _re.IGNORECASE,
)

# Markers that definitely indicate the start of real user-facing content.
_CONTENT_START_PATTERN = _re.compile(
    r"^(#{1,6}\s|```|\d+\.|以下是|下面是|这是|完整代码|完整脚本|Here is|Here's|The following)",
    _re.IGNORECASE,
)


def strip_final_output_thinking(text: str) -> str:
    """Remove agent self-commentary paragraphs from a final user-facing response.

    Unlike _strip_thinking_preamble (which gives up when thinking exceeds a
    threshold), this function always filters — it is intended only for the
    final result shown to the user, where any reasoning leak is unacceptable.

    Strategy:
    - Scan from the top, skipping paragraphs whose first non-empty line matches
      _FINAL_REASONING_PATTERN (agent meta-commentary).
    - A paragraph that starts with a _CONTENT_START_PATTERN marker, or whose
      first line does NOT match _FINAL_REASONING_PATTERN, is treated as real
      content — everything from that point is returned as-is.
    - Code fences always mark real content.
    - If nothing survives, return the original so the user gets some response.
    """
    if not text:
        return text

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == "" and current:
            paragraphs.append(current)
            current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(current)

    result_lines: list[str] = []
    found_real_content = False

    for para in paragraphs:
        if found_real_content:
            result_lines.extend(para)
            result_lines.append("\n")
            continue

        first_non_empty = next((l.strip() for l in para if l.strip()), "")
        if not first_non_empty:
            continue

        # Code fence — always real content
        if first_non_empty.startswith("```"):
            found_real_content = True
            result_lines.extend(para)
            result_lines.append("\n")
            continue

        # Explicit content marker
        if _CONTENT_START_PATTERN.match(first_non_empty):
            found_real_content = True
            result_lines.extend(para)
            result_lines.append("\n")
            continue

        # Reasoning paragraph — skip
        if _FINAL_REASONING_PATTERN.match(first_non_empty):
            continue

        # Doesn't look like reasoning — real content starts here
        found_real_content = True
        result_lines.extend(para)
        result_lines.append("\n")

    candidate = "".join(result_lines).strip()
    return candidate if candidate else text


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
