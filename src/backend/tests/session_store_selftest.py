"""Selftest: session store abstraction.

Run:
  python -m selftests.session_store_selftest
"""

from __future__ import annotations

from core.chat.session import MemorySessionStore, get_session_store, reset_session_store


def main() -> int:
    # Test MemorySessionStore directly
    store = MemorySessionStore()

    # Test get_or_create
    session1 = store.get_or_create("chat_001")
    assert session1["messages"] == []
    assert "created_at" in session1
    assert "last_updated" in session1

    # Modify and save
    session1["messages"].append({"role": "user", "content": "Hello"})
    store.save("chat_001", session1)

    # Retrieve again
    session1_again = store.get("chat_001")
    assert session1_again is not None
    assert len(session1_again["messages"]) == 1
    assert session1_again["messages"][0]["content"] == "Hello"

    # Test list_all
    store.get_or_create("chat_002")
    store.get_or_create("chat_003")
    all_ids = store.list_all()
    assert len(all_ids) == 3
    assert "chat_001" in all_ids
    assert "chat_002" in all_ids
    assert "chat_003" in all_ids

    # Test delete
    assert store.delete("chat_002") is True
    assert store.delete("chat_999") is False
    assert len(store.list_all()) == 2

    # Test get_session_store singleton
    reset_session_store()
    global_store = get_session_store()
    assert isinstance(global_store, MemorySessionStore)

    # Verify it's a singleton
    global_store2 = get_session_store()
    assert global_store is global_store2

    print("session_store_selftest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
