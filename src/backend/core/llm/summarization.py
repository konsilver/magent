"""单次 Turn 内工具结果摘要（AgentScope 版）。

与 ReActAgent 内置的 CompressionConfig 不同，本模块专门处理
单次对话轮次内，Agent 多次工具调用导致的上下文膨胀问题。

迁移自原 LangChain InTurnSummarizationMiddleware，现在作为
独立异步函数供 hooks 调用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List

from agentscope.message import Msg

from core.llm.context_manager import CHARS_PER_TOKEN, estimate_tokens as _estimate_tokens

logger = logging.getLogger(__name__)

_SUMMARIZED_MARKER = "[已压缩]"


# ---------------------------------------------------------------------------
# Message format adapters (for Msg objects)
# ---------------------------------------------------------------------------

def _msg_role(msg: Msg) -> str:
    return msg.role


def _msg_content(msg: Msg) -> str:
    text = msg.get_text_content()
    return text if text else ""


def _msg_has_tool_use(msg: Msg) -> bool:
    return msg.has_content_blocks("tool_use")


def _msg_has_tool_result(msg: Msg) -> bool:
    return msg.has_content_blocks("tool_result")


def _count_messages_tokens(messages: List[Msg]) -> int:
    total = 0
    for msg in messages:
        total += _estimate_tokens(_msg_content(msg))
    return total


# ---------------------------------------------------------------------------
# Current Turn analysis
# ---------------------------------------------------------------------------

def _find_current_turn_start(messages: List[Msg]) -> int:
    """返回最后一条用户消息的索引（即当前 Turn 的起点）。"""
    for i in range(len(messages) - 1, -1, -1):
        if _msg_role(messages[i]) == "user":
            return i
    return 0


def _find_compressible_tool_results(
    messages: List[Msg],
    start_idx: int,
    keep: int,
) -> List[int]:
    """Find tool_result messages that can be compressed.

    Returns indices of compressible tool_result messages,
    excluding the most recent `keep` results.
    """
    tool_result_indices: List[int] = []
    for i in range(start_idx, len(messages)):
        if _msg_role(messages[i]) == "system" and _msg_has_tool_result(messages[i]):
            tool_result_indices.append(i)

    if len(tool_result_indices) <= keep:
        return []
    return tool_result_indices[:len(tool_result_indices) - keep]


def _is_already_compressed(msg: Msg) -> bool:
    return _msg_content(msg).startswith(_SUMMARIZED_MARKER)


# ---------------------------------------------------------------------------
# LLM summarization
# ---------------------------------------------------------------------------

async def _summarize_tool_result(
    model,
    msg: Msg,
) -> str:
    """Summarize a single tool result message using the LLM."""
    content = _msg_content(msg)
    preview = content[:4_000] + ("…（已截断）" if len(content) > 4_000 else "")

    prompt = (
        "你是上下文压缩助手。请将以下工具调用结果压缩为简洁摘要，"
        "不超过 150 字，保留关键数据、重要发现及对后续分析有价值的信息，"
        "去除冗余细节。直接输出摘要内容，无需任何解释。\n\n"
        + preview
    )

    try:
        from core.llm.message_compat import extract_text_from_chat_response
        result = await model(
            messages=[{"role": "user", "content": prompt}]
        )
        return extract_text_from_chat_response(result).strip()
    except Exception as exc:
        logger.warning("InTurnSummarization: LLM 摘要失败: %s", exc)
        return content[:150] + "…"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def compress_in_turn_tool_results(
    messages: List[Msg],
    model,
    trigger_fraction: float = 0.5,
    keep: int = 2,
    model_max_tokens: int = 128_000,
) -> List[Msg]:
    """Compress tool results within the current turn if context exceeds threshold.

    This is the AgentScope replacement for InTurnSummarizationMiddleware.
    Called by hooks or directly before reasoning steps.

    Args:
        messages: Current memory messages.
        model: Summarization model (OpenAIChatModel).
        trigger_fraction: Context usage fraction to trigger compression.
        keep: Number of most recent tool results to preserve.
        model_max_tokens: Model context window size.

    Returns:
        Possibly compressed message list.
    """
    total_tokens = _count_messages_tokens(messages)
    threshold = model_max_tokens * trigger_fraction

    if total_tokens < threshold:
        return messages

    turn_start = _find_current_turn_start(messages)
    compressible = _find_compressible_tool_results(messages, turn_start, keep)

    if not compressible:
        return messages

    # Filter out already compressed
    pending = [i for i in compressible if not _is_already_compressed(messages[i])]
    if not pending:
        return messages

    # Summarize in parallel
    summaries = await asyncio.gather(
        *[_summarize_tool_result(model, messages[i]) for i in pending],
        return_exceptions=True,
    )

    # Apply summaries
    messages = list(messages)
    compressed_count = 0

    for idx, summary in zip(pending, summaries):
        if isinstance(summary, Exception) or not summary:
            continue
        original_len = len(_msg_content(messages[idx]))
        new_content = (
            f"{_SUMMARIZED_MARKER} {summary}\n"
            f"（原始内容已压缩，原始长度 {original_len} 字符）"
        )
        # Create new Msg with compressed content
        messages[idx] = Msg(
            name=messages[idx].name if hasattr(messages[idx], 'name') else "system",
            content=new_content,
            role=messages[idx].role,
        )
        compressed_count += 1

    if compressed_count:
        after_tokens = _count_messages_tokens(messages)
        logger.info(
            "InTurnSummarization: 压缩了 %d 条工具结果，压缩后约 %d tokens",
            compressed_count,
            after_tokens,
        )

    return messages
