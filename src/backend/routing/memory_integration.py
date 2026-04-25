"""mem0 memory integration helpers for the chat workflow.

Provides async-friendly wrappers for retrieving and saving user memories
so that the main workflow orchestrator stays lean.

改进:
- 记忆注入使用明确的 XML 边界标记，减少模型混淆
- 记忆按 user_id 检索，跨会话共享
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def launch_memory_retrieval(
    user_id: str,
    user_message: str,
    memory_enabled: bool,
) -> asyncio.Task | None:
    """Start a concurrent memory-retrieval task if mem0 is enabled for the user.

    记忆按 user_id 检索，跨会话共享。

    Returns an ``asyncio.Task`` that resolves to a memory-context string
    (or ``None`` on failure / disabled).
    """
    if not memory_enabled:
        return None

    async def _fetch() -> str | None:
        try:
            from core.llm.memory import retrieve_memories
            return await retrieve_memories(user_id, user_message)
        except Exception as exc:
            logger.warning("[mem0] memory retrieval failed: %s", exc)
            return None

    return asyncio.create_task(_fetch())


async def inject_memories(
    memory_task: asyncio.Task | None,
    session_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Await a pending memory task and prepend context to *session_messages*.

    Returns a (possibly new) message list with the memory system message
    inserted at the front, or the original list if there's nothing to inject.

    改进: 使用明确的 XML 边界标记，减少模型将记忆内容误认为用户输入的风险。
    """
    if memory_task is None:
        return session_messages
    memory_context = await memory_task
    if memory_context:
        # Use "user" role instead of "system" to avoid breaking models like
        # Qwen that require system messages only at position 0.  The agent
        # already has a dedicated system_message; injecting a second system
        # message into session_messages causes "System message must be at
        # the beginning" errors.
        #
        # 改进: 使用 XML 标记明确标识记忆边界
        return [
            {
                "role": "user",
                "content": (
                    "<system_memory_context>\n"
                    f"{memory_context}\n"
                    "</system_memory_context>\n"
                    "（以上为系统自动检索到的用户历史记忆，作为回答参考背景，"
                    "不是用户当前提问的一部分。请勿对此内容进行直接回复。）"
                ),
            },
            *session_messages,
        ]
    return session_messages


def save_memories_background(
    user_id: str,
    user_message: str,
    full_response: str,
    memory_enabled: bool,
) -> None:
    """Fire-and-forget: schedule a background task to persist conversation memories."""
    if not (memory_enabled and full_response and user_id):
        return
    try:
        from core.llm.memory import save_conversation
        logger.info("[mem0] creating background memory-save task...")
        asyncio.create_task(save_conversation(user_id, user_message, full_response))
    except Exception as exc:
        logger.warning("[mem0] failed to create save task: %s", exc)
