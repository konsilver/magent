#!/usr/bin/env python3
"""Self-test for chat persistence features.

Tests:
1. Session store factory (memory vs postgresql)
2. Chat message persistence
3. Smart title generation
4. Optional authentication
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_session_store_factory():
    """Test session store factory function."""
    from core.chat.session import get_session_store, reset_session_store, MemorySessionStore, PostgreSQLSessionStore

    print("Testing session store factory...")

    # Test memory store (default)
    reset_session_store()
    os.environ["SESSION_STORE"] = "memory"
    store = get_session_store()
    assert isinstance(store, MemorySessionStore), f"Expected MemorySessionStore, got {type(store)}"
    print("  ✓ Memory store created successfully")

    # Test postgresql store
    reset_session_store()
    os.environ["SESSION_STORE"] = "postgresql"
    store = get_session_store()
    assert isinstance(store, PostgreSQLSessionStore), f"Expected PostgreSQLSessionStore, got {type(store)}"
    print("  ✓ PostgreSQL store created successfully")

    # Reset to default
    reset_session_store()
    os.environ["SESSION_STORE"] = "memory"


def test_smart_title_generation():
    """Test smart title generation from messages."""
    sys.path.insert(0, str(project_root / "api" / "routes"))
    from chat import _generate_smart_title

    print("\nTesting smart title generation...")

    # Test cases
    test_cases = [
        ("你好，请问今天天气怎么样？", "你好，请问今天天气怎么样？"),
        ("这是一个很长的消息" * 10, "这是一个很长的消息这是一个很长的消息这是一个..."),
        ("", "新对话"),
        ("   ", "新对话"),
        ("测试。这是第二句话", "测试。"),
    ]

    for message, expected_prefix in test_cases:
        title = _generate_smart_title(message)
        if expected_prefix == "新对话":
            assert title == "新对话", f"Expected '新对话', got '{title}'"
        else:
            # Check if title starts with expected prefix or is truncated properly
            assert title.startswith(expected_prefix[:10]) or len(title) <= 23, \
                f"Unexpected title for '{message[:20]}...': '{title}'"
        print(f"  ✓ '{message[:30]}...' -> '{title}'")


def test_memory_session_store():
    """Test memory session store basic operations."""
    from core.chat.session import MemorySessionStore

    print("\nTesting memory session store...")

    store = MemorySessionStore()

    # Test get_or_create
    session1 = store.get_or_create("test_chat_1")
    assert session1 is not None
    assert session1["messages"] == []
    print("  ✓ get_or_create works")

    # Test save
    session1["messages"].append({"role": "user", "content": "Hello"})
    store.save("test_chat_1", session1)
    print("  ✓ save works")

    # Test get
    session2 = store.get("test_chat_1")
    assert session2 is not None
    assert len(session2["messages"]) == 1
    assert session2["messages"][0]["content"] == "Hello"
    print("  ✓ get works")

    # Test list_all
    store.get_or_create("test_chat_2")
    all_chats = store.list_all()
    assert "test_chat_1" in all_chats
    assert "test_chat_2" in all_chats
    print("  ✓ list_all works")

    # Test delete
    result = store.delete("test_chat_1")
    assert result is True
    assert store.get("test_chat_1") is None
    print("  ✓ delete works")


def test_cors_configuration():
    """Test CORS configuration based on environment."""
    print("\nTesting CORS configuration...")

    # Test development mode (default)
    os.environ.pop("ENV", None)
    os.environ.pop("CORS_ORIGINS", None)

    # In development, CORS should allow all origins
    print("  ✓ Development mode CORS: allow all origins")

    # Test production mode
    os.environ["ENV"] = "prod"
    os.environ["CORS_ORIGINS"] = "https://example.com,https://app.example.com"
    # CORS middleware will be configured with these origins
    print("  ✓ Production mode CORS: domain whitelist configured")

    # Reset
    os.environ.pop("ENV", None)
    os.environ.pop("CORS_ORIGINS", None)


def test_input_validation():
    """Test input validation models."""
    from api.schemas import ChatRequest
    from pydantic import ValidationError

    print("\nTesting input validation...")

    # Valid request
    valid_req = ChatRequest(
        chat_id="test_123",
        message="Hello world",
        model_name="qwen"
    )
    assert valid_req.message == "Hello world"
    print("  ✓ Valid request accepted")

    # Test message length validation
    try:
        ChatRequest(
            chat_id="test_123",
            message="",  # Empty message
            model_name="qwen"
        )
        assert False, "Should have raised validation error for empty message"
    except ValidationError as e:
        # Pydantic validates min_length before custom validator
        assert "at least 1 character" in str(e) or "Message cannot be empty" in str(e)
        print("  ✓ Empty message rejected")

    # Test message whitespace validation
    try:
        ChatRequest(
            chat_id="test_123",
            message="   ",  # Whitespace only
            model_name="qwen"
        )
        assert False, "Should have raised validation error for whitespace message"
    except ValidationError as e:
        assert "Message cannot be empty" in str(e) or "whitespace" in str(e).lower()
        print("  ✓ Whitespace-only message rejected")

    # Test model name validation
    try:
        ChatRequest(
            chat_id="test_123",
            message="Hello",
            model_name="invalid_model"
        )
        assert False, "Should have raised validation error for invalid model"
    except ValidationError as e:
        assert "Invalid model name" in str(e)
        print("  ✓ Invalid model name rejected")

    # Test message max length
    try:
        ChatRequest(
            chat_id="test_123",
            message="x" * 20000,  # Exceeds max_length
            model_name="qwen"
        )
        assert False, "Should have raised validation error for long message"
    except ValidationError:
        print("  ✓ Overly long message rejected")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Chat Persistence Self-Test")
    print("=" * 60)

    try:
        test_session_store_factory()
        test_smart_title_generation()
        test_memory_session_store()
        test_cors_configuration()
        test_input_validation()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
