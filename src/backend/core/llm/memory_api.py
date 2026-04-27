"""统一 Memory 操作接口

本模块提供 KV 和 Graph 两种记忆模式的公共 API，外部模块应通过此文件访问记忆功能，
避免直接调用 memory.py 底层实现。

── KV 模式（向量检索，Milvus）────────────────────────────────────────────────

  kv_save(user_id, user_msg, assistant_msg)
      保存一条对话到 KV 记忆。mem0 自动从中提取事实并向量化存储。

  kv_search(user_id, query, limit, min_score) -> str
      按语义检索相关记忆，返回格式化文本（含时间衰减排序）。

  kv_get_all(user_id) -> List[dict]
      获取用户全部 KV 记忆条目（供管理接口）。

  kv_delete(memory_id) -> bool
      删除单条 KV 记忆。

  kv_delete_all(user_id) -> bool
      清空用户全部 KV 记忆。

── Graph 模式（实体关系图，Neo4j）────────────────────────────────────────────

  graph_save(user_id, user_msg, assistant_msg)
      保存一条对话到 Graph 记忆。mem0 自动解析实体和关系写入 Neo4j。
      仅当 MEM0_GRAPH_ENABLED=true 时生效，否则静默跳过。

  graph_search(user_id, query, limit) -> List[dict]
      按语义检索图关系，返回 {"source", "relationship", "target"} 列表。
      仅当 MEM0_GRAPH_ENABLED=true 时生效，否则返回空列表。

  注：mem0 v1.1 在 Memory.add() 时同时写 KV 和 Graph（如果两者都启用）。
      kv_save / graph_save 实际上都调用同一个底层 save_conversation()。
      graph_save 是语义上的区分——调用方说明自己在写图结构数据。

── 集成工具（chat 工作流）─────────────────────────────────────────────────────

  chat_retrieve_start(user_id, user_message, enabled) -> Task
      在 chat 流程开始时并发启动记忆检索（asyncio Task）。

  chat_inject(memory_task, session_messages) -> List[dict]
      等待检索结果并将记忆注入消息列表。

  chat_save_background(user_id, user_message, assistant_reply, enabled)
      后台 fire-and-forget 保存对话记忆。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)


# ── KV 模式 ──────────────────────────────────────────────────────────────────


async def kv_save(user_id: str, user_msg: str, assistant_msg: str) -> None:
    """保存一条对话到 KV 记忆（mem0 自动提取事实并向量化）。

    fire-and-forget 场景请用 asyncio.create_task() 包装此函数。
    """
    try:
        from core.llm.memory import save_conversation
        await save_conversation(user_id, user_msg, assistant_msg)
    except Exception as exc:
        logger.debug("[memory_api] kv_save failed (non-critical): %s", exc)


async def kv_search(
    user_id: str,
    query: str,
    limit: int = 10,
    min_score: float = 0.4,
) -> str:
    """按语义检索 KV 记忆，返回格式化文本。

    返回值：Markdown 格式的记忆摘要字符串，可直接注入 prompt。
    检索失败或未启用时返回空字符串。
    """
    try:
        from core.llm.memory import retrieve_memories
        return await retrieve_memories(user_id, query, limit=limit, min_score=min_score)
    except Exception as exc:
        logger.debug("[memory_api] kv_search failed (non-critical): %s", exc)
        return ""


async def kv_get_all(user_id: str) -> List[Dict[str, Any]]:
    """获取用户所有 KV 记忆条目（供管理 API）。"""
    try:
        from core.llm.memory import get_all_memories
        return await get_all_memories(user_id)
    except Exception as exc:
        logger.debug("[memory_api] kv_get_all failed: %s", exc)
        return []


async def kv_delete(memory_id: str) -> bool:
    """删除单条 KV 记忆，返回是否成功。"""
    try:
        from core.llm.memory import delete_memory
        return await delete_memory(memory_id)
    except Exception as exc:
        logger.debug("[memory_api] kv_delete failed: %s", exc)
        return False


async def kv_delete_all(user_id: str) -> bool:
    """清空用户全部 KV 记忆，返回是否成功。"""
    try:
        from core.llm.memory import delete_all_memories
        return await delete_all_memories(user_id)
    except Exception as exc:
        logger.debug("[memory_api] kv_delete_all failed: %s", exc)
        return False


# ── Graph 模式 ────────────────────────────────────────────────────────────────


async def graph_save(user_id: str, user_msg: str, assistant_msg: str) -> None:
    """保存图结构数据到 Graph 记忆（mem0 自动解析实体/关系写入 Neo4j）。

    仅当 MEM0_GRAPH_ENABLED=true 时生效，否则静默跳过。
    """
    try:
        from core.llm.memory import MEM0_GRAPH_ENABLED, save_conversation
        if not MEM0_GRAPH_ENABLED:
            return
        await save_conversation(user_id, user_msg, assistant_msg)
    except Exception as exc:
        logger.debug("[memory_api] graph_save failed (non-critical): %s", exc)


async def graph_search(
    user_id: str,
    query: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """按语义检索图关系，返回 {"source", "relationship", "target"} 列表。

    仅当 MEM0_GRAPH_ENABLED=true 时生效，否则返回空列表。
    """
    try:
        from core.llm.memory import MEM0_GRAPH_ENABLED, _get_memory
        if not MEM0_GRAPH_ENABLED:
            return []
        memory = _get_memory()
        if memory is None:
            return []
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: memory.search(query, user_id=user_id, limit=limit),
        )
        if isinstance(result, dict):
            relations = result.get("relations", [])
        else:
            relations = []
        return [r for r in relations if isinstance(r, dict)]
    except Exception as exc:
        logger.debug("[memory_api] graph_search failed (non-critical): %s", exc)
        return []


# ── chat 工作流集成工具 ──────────────────────────────────────────────────────


def chat_retrieve_start(
    user_id: str,
    user_message: str,
    enabled: bool,
) -> Optional[asyncio.Task]:
    """在 chat 流程开始时并发启动记忆检索（asyncio.Task）。

    返回 Task，传给 chat_inject() 等待结果。未启用时返回 None。
    """
    try:
        from routing.memory_integration import launch_memory_retrieval
        # launch_memory_retrieval 是 async 函数，需要 await 才能获得 Task
        # 此处封装为 create_task 以保持同步调用接口
        async def _wrap():
            return await launch_memory_retrieval(user_id, user_message, enabled)

        # 不能在同步上下文里 create_task，保留原接口语义
        # 调用方应使用 asyncio.create_task(chat_retrieve_start_async(...))
        raise NotImplementedError(
            "请直接调用 routing.memory_integration.launch_memory_retrieval()"
        )
    except NotImplementedError:
        raise
    except Exception as exc:
        logger.debug("[memory_api] chat_retrieve_start failed: %s", exc)
        return None


async def chat_retrieve_start_async(
    user_id: str,
    user_message: str,
    enabled: bool,
) -> Optional[asyncio.Task]:
    """在 chat 流程开始时并发启动记忆检索（asyncio.Task）。

    用法::

        memory_task = await chat_retrieve_start_async(user_id, message, enabled)
        # ... 进行其他工作 ...
        messages = await chat_inject(memory_task, messages)
    """
    from routing.memory_integration import launch_memory_retrieval
    return await launch_memory_retrieval(user_id, user_message, enabled)


async def chat_inject(
    memory_task: Optional[asyncio.Task],
    session_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """等待记忆检索结果并注入到消息列表头部。

    Args:
        memory_task: chat_retrieve_start_async() 返回的 Task，为 None 时直接返回原列表。
        session_messages: 当前会话消息列表。

    Returns:
        注入记忆后的消息列表（若无记忆则返回原列表）。
    """
    from routing.memory_integration import inject_memories
    return await inject_memories(memory_task, session_messages)


def chat_save_background(
    user_id: str,
    user_message: str,
    assistant_reply: str,
    enabled: bool,
) -> None:
    """后台 fire-and-forget 保存对话记忆。不阻塞主流程。"""
    from routing.memory_integration import save_memories_background
    save_memories_background(user_id, user_message, assistant_reply, enabled)
