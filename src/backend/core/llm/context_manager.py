"""上下文窗口预算管理器。

在消息加载到 Agent Memory 之前按预算裁剪历史消息，防止超出模型上下文限制。
与 AgentScope 的 CompressionConfig 互补：
- ContextWindowManager: 加载前裁剪（粗粒度，防溢出）
- CompressionConfig: 推理中自动压缩（细粒度，保留关键信息）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 已知模型上下文窗口大小（token）
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # Qwen 系列
    "qwen3.5-122b": 256_000,
    "qwen3-235b": 256_000,
    "qwen3-32b": 128_000,
    "qwen3-30b-a3b": 128_000,
    "qwen3-8b": 128_000,
    "qwen-max": 128_000,
    "qwen-plus": 128_000,
    "qwen-turbo": 128_000,
    "qwen3_80b": 128_000,
    # 通用别名（前端/env 传入的 role key 或简写）
    "qwen": 128_000,
    # GLM 系列
    "glm-5": 130_000,
    "glm-4": 128_000,
    "glm-4-plus": 128_000,
    "glm-4-long": 1_000_000,
    # MiniMax 系列
    "minimax-m27": 200_000,
    "minimax": 200_000,
    # DeepSeek 系列
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    "deepseek-v3": 128_000,
    "deepseekr1": 131_072,
}

DEFAULT_CONTEXT_WINDOW = 128_000

CHARS_PER_TOKEN = 2.5  # 中文平均估算


def resolve_model_context_window(model_name: str) -> int:
    """从模型名称推断上下文窗口大小。

    匹配规则：先精确匹配，再前缀匹配，最后降级默认值。
    """
    if not model_name:
        return DEFAULT_CONTEXT_WINDOW

    # 去除 openai: 等前缀
    clean = model_name
    for prefix in ("openai:", "azure:", "local:"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    clean = clean.strip().lower()

    # 精确匹配
    if clean in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[clean]

    # 前缀匹配（例如 qwen3.5-122b-instruct 匹配 qwen3.5-122b）
    for key, value in sorted(MODEL_CONTEXT_WINDOWS.items(), key=lambda x: -len(x[0])):
        if clean.startswith(key):
            return value

    logger.warning(
        "[ContextManager] 未知模型 '%s'，使用默认上下文窗口 %d tokens",
        model_name, DEFAULT_CONTEXT_WINDOW,
    )
    return DEFAULT_CONTEXT_WINDOW


def estimate_tokens(text: str) -> int:
    """估算文本 token 数。中文约 2.5 字符/token。"""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN))


@dataclass
class ContextBudget:
    """Token 预算分配，各分区独立管控。

    总预算按以下顺序分配：
    1. system_prompt_reserve: 系统提示 + 技能描述
    2. memory_reserve: mem0 长期记忆注入
    3. output_reserve: 模型输出预留
    4. tool_reserve: 工具调用 + 结果
    5. 剩余空间 → 历史消息
    """
    model_context_window: int = DEFAULT_CONTEXT_WINDOW
    system_prompt_reserve: int = 10_000
    memory_reserve: int = 2_000
    output_reserve: int = 4_096
    tool_reserve: int = 20_000
    safety_margin: float = 0.10

    def __post_init__(self):
        reserved = (self.system_prompt_reserve + self.memory_reserve
                     + self.output_reserve + self.tool_reserve)
        if reserved >= self.model_context_window:
            logger.warning(
                "[ContextBudget] 预留空间 (%d) >= 模型上下文窗口 (%d)，历史消息预算为 0",
                reserved, self.model_context_window,
            )

    @property
    def history_budget(self) -> int:
        """留给历史消息的可用 token 数。"""
        used = (
            self.system_prompt_reserve
            + self.memory_reserve
            + self.output_reserve
            + self.tool_reserve
        )
        available = self.model_context_window - used
        return max(0, int(available * (1.0 - self.safety_margin)))


class ContextWindowManager:
    """在加载到 Agent Memory 前裁剪历史消息。

    保持 user-assistant 轮次完整性：不会只保留半个轮次。
    """

    def __init__(self, budget: Optional[ContextBudget] = None):
        self.budget = budget or ContextBudget()

    @classmethod
    def for_model(cls, model_name: str) -> "ContextWindowManager":
        """根据模型名称自动创建带正确预算的管理器。"""
        ctx_window = resolve_model_context_window(model_name)
        budget = ContextBudget(model_context_window=ctx_window)
        return cls(budget=budget)

    def trim_history(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """从最新消息向前保留，直到用完预算。

        保持 user-assistant 轮次完整性。返回裁剪后的消息列表。
        """
        if not messages:
            return messages

        budget = max_tokens if max_tokens is not None else self.budget.history_budget

        # 从后向前累计 token，按轮次边界切割
        total_tokens = 0
        keep_from = len(messages)

        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            content = msg.get("content", "")
            tokens = estimate_tokens(content) if isinstance(content, str) else estimate_tokens(str(content))
            if total_tokens + tokens > budget:
                break
            total_tokens += tokens
            keep_from = i
            i -= 1

        # 如果裁剪点在一个轮次中间（assistant/system 消息没有 user 前缀），
        # 向前跳过直到找到 user 消息作为起点
        while keep_from < len(messages) and messages[keep_from].get("role") != "user":
            keep_from += 1

        trimmed = messages[keep_from:]
        if len(trimmed) < len(messages):
            dropped = len(messages) - len(trimmed)
            logger.info(
                "[ContextManager] 裁剪历史消息: %d → %d 条 (丢弃 %d 条, 预算 %d tokens)",
                len(messages), len(trimmed), dropped, budget,
            )

        return trimmed

