"""历史对话结构化摘要。

当历史消息超出上下文预算时，对旧消息生成结构化摘要，
用摘要替代原始消息，保留对话连续性的同时控制上下文大小。

摘要结构借鉴 ReMe (Goal/Progress/Decisions) 和
Factory.ai (anchored iterative summarization) 的最佳实践。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.llm.chat_models import get_summarize_model
from core.llm.message_compat import extract_text_from_chat_response

logger = logging.getLogger(__name__)

SUMMARY_TEMPLATE = """你是对话上下文压缩助手。请对以下对话历史生成结构化摘要，用于在后续对话中保持连续性。

## 要求
1. 使用中文输出
2. 保持简洁，总长度不超过 800 字
3. 保留对后续对话有价值的关键信息
4. 不遗漏重要数据（如具体数字、指标、结论）

## 摘要结构（请严格按照此格式输出）

### 用户意图
用户在这段对话中的主要目标和需求

### 关键信息与决策
- 提到的重要数据、事实、结论
- 已做出的关键决策

### 已完成操作
- 调用的工具及其核心结果（保留关键数据，省略冗余）

### 当前状态
- 待处理的问题或后续步骤
- 需要保持的上下文约束

## 对话内容
{conversation}

请直接按上述结构输出摘要。"""

INCREMENTAL_SUMMARY_TEMPLATE = """你是对话上下文压缩助手。以下是一段已有的对话摘要，以及摘要之后发生的新对话。请更新摘要以包含新信息。

## 要求
1. 使用中文输出
2. 保持简洁，总长度不超过 800 字
3. 更新已有信息（不要重复），补充新信息
4. 如果新对话改变了之前的结论或状态，以新信息为准

## 已有摘要
{existing_summary}

## 新增对话
{new_conversation}

## 摘要结构（请严格按照此格式输出）

### 用户意图
（更新后的用户目标）

### 关键信息与决策
（合并后的关键信息列表）

### 已完成操作
（合并后的操作列表）

### 当前状态
（最新状态）

请直接按上述结构输出更新后的摘要。"""


def _format_messages_for_summary(messages: List[Dict[str, Any]], max_chars: int = 20_000) -> str:
    """将消息列表格式化为文本，用于提交给 LLM 做摘要。"""
    lines = []
    total_chars = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not content:
            continue
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
        line = f"[{role_label}]: {content}"
        if total_chars + len(line) > max_chars:
            lines.append(f"[{role_label}]: {content[:max_chars - total_chars]}…（已截断）")
            break
        lines.append(line)
        total_chars += len(line)
    return "\n\n".join(lines)


async def summarize_history(
    messages: List[Dict[str, Any]],
    model=None,
    existing_summary: Optional[str] = None,
) -> str:
    """对历史消息生成结构化摘要。

    Args:
        messages: 需要摘要的消息列表。
        model: LLM model 实例（OpenAIChatModel），为 None 时使用 summarizer 模型。
        existing_summary: 已有摘要（如果有），用于增量更新。

    Returns:
        结构化摘要文本。
    """
    if not messages:
        return ""

    # 获取 summarizer 模型
    if model is None:
        try:
            model = get_summarize_model()
        except Exception as exc:
            logger.warning("[HistorySummarizer] 无法获取摘要模型: %s", exc)
            return _fallback_summary(messages)

    conversation_text = _format_messages_for_summary(messages)

    if existing_summary:
        prompt = INCREMENTAL_SUMMARY_TEMPLATE.format(
            existing_summary=existing_summary,
            new_conversation=conversation_text,
        )
    else:
        prompt = SUMMARY_TEMPLATE.format(conversation=conversation_text)

    try:
        result = await model(
            messages=[{"role": "user", "content": prompt}]
        )
        summary = extract_text_from_chat_response(result).strip()
        if summary:
            logger.info(
                "[HistorySummarizer] 生成摘要: %d 条消息 → %d 字符",
                len(messages), len(summary),
            )
            return summary
    except Exception as exc:
        logger.warning("[HistorySummarizer] LLM 摘要失败: %s", exc)

    return _fallback_summary(messages)


def _fallback_summary(messages: List[Dict[str, Any]]) -> str:
    """LLM 不可用时的降级摘要：提取用户消息摘要。"""
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return ""

    lines = ["### 用户意图", "用户在对话中提出了以下问题："]
    for msg in user_msgs[:5]:
        text = msg[:100] + "…" if len(msg) > 100 else msg
        lines.append(f"- {text}")

    if len(user_msgs) > 5:
        lines.append(f"- …（共 {len(user_msgs)} 条提问）")

    return "\n".join(lines)
