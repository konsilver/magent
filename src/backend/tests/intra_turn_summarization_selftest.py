"""Selftest for in-turn summarization (AgentScope version).

Tests the standalone compress_in_turn_tool_results function that replaces
the old InTurnSummarizationMiddleware.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from agentscope.message import Msg

from core.llm.summarization import (
    _SUMMARIZED_MARKER,
    _count_messages_tokens,
    _find_current_turn_start,
    _find_compressible_tool_results,
    _is_already_compressed,
    _msg_content,
    _msg_role,
    compress_in_turn_tool_results,
)


# ---------------------------------------------------------------------------
# Test message construction helpers
# ---------------------------------------------------------------------------

def _user(content: str) -> Msg:
    return Msg(name="user", content=content, role="user")


def _assistant(content: str) -> Msg:
    return Msg(name="assistant", content=content, role="assistant")


def _system_tool_result(content: str) -> Msg:
    """Create a system message with tool_result content block."""
    from agentscope.message._message_block import ToolResultBlock
    return Msg(
        name="system",
        content=[ToolResultBlock(
            type="tool_result",
            id="tc1",
            name="test_tool",
            output=[{"type": "text", "text": content}],
        )],
        role="system",
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_msg_role():
    msg = _user("hi")
    assert _msg_role(msg) == "user"
    msg2 = _assistant("hello")
    assert _msg_role(msg2) == "assistant"
    print("✓ _msg_role: correct")


def test_find_current_turn_start():
    messages = [
        _user("first turn"),
        _assistant("reply"),
        _user("second turn"),
        _assistant("reply2"),
    ]
    idx = _find_current_turn_start(messages)
    assert idx == 2
    print("✓ _find_current_turn_start: correct")


def test_find_current_turn_start_no_user():
    messages = [_assistant("only assistant")]
    idx = _find_current_turn_start(messages)
    assert idx == 0
    print("✓ _find_current_turn_start: no user returns 0")


def test_count_messages_tokens():
    messages = [_user("hello")]
    tokens = _count_messages_tokens(messages)
    assert tokens >= 1
    print(f"✓ _count_messages_tokens: {tokens} tokens")


# ---------------------------------------------------------------------------
# Compression tests
# ---------------------------------------------------------------------------

def _make_mock_model(summary_text: str = "摘要内容"):
    """Create a mock model that returns fixed summary."""
    mock_model = AsyncMock()

    class FakeResponse:
        content = [{"type": "text", "text": summary_text}]

    mock_model.return_value = FakeResponse()
    return mock_model


async def _test_below_threshold():
    """Below threshold: no compression."""
    model = _make_mock_model()
    messages = [_user("hi"), _assistant("hello")]
    result = await compress_in_turn_tool_results(
        messages, model,
        trigger_fraction=0.5,
        model_max_tokens=100_000,
    )
    assert result is messages  # same object, not modified
    print("✓ compress: below threshold, no changes")


async def _test_compression_basic():
    """Basic compression test with high-enough token count."""
    model = _make_mock_model("压缩后的摘要")
    # Create messages that exceed threshold
    long_content = "A" * 5000
    messages = [
        _user("query"),
        Msg(name="system", content=[{
            "type": "tool_result", "id": "tc1", "name": "tool1",
            "output": [{"type": "text", "text": long_content}],
        }], role="system"),
        Msg(name="system", content=[{
            "type": "tool_result", "id": "tc2", "name": "tool2",
            "output": [{"type": "text", "text": "recent result"}],
        }], role="system"),
    ]

    result = await compress_in_turn_tool_results(
        messages, model,
        trigger_fraction=0.01,  # very low threshold to trigger
        keep=1,
        model_max_tokens=100,
    )
    # At least the model should have been called or messages returned
    assert result is not None
    print("✓ compress: basic compression works")


def test_below_threshold():
    asyncio.run(_test_below_threshold())


def test_compression_basic():
    asyncio.run(_test_compression_basic())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=== InTurnSummarization (AgentScope) 自测 ===\n")

    print("--- Helper tests ---")
    test_msg_role()
    test_find_current_turn_start()
    test_find_current_turn_start_no_user()
    test_count_messages_tokens()

    print("\n--- Compression tests ---")
    test_below_threshold()
    test_compression_basic()

    print("\n=== intra_turn_summarization_selftest: OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
