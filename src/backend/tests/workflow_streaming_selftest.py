"""Selftest: workflow main route should stream message tokens incrementally.

Run:
  PYTHONPATH=src/backend python -m tests.workflow_streaming_selftest
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def main() -> int:
    try:
        import routing.workflow as wf
    except ModuleNotFoundError as e:
        print(f"workflow_streaming_selftest: SKIP (missing dependency: {e})")
        return 0

    class _Router:
        def route(self, user_input: str, context=None) -> str:
            return "main"

    # Mock the streaming agent to produce known events
    async def mock_create_agent_executor(**kwargs):
        agent = MagicMock()
        agent._disable_console_output = True
        agent._jx_context = None
        agent.memory = AsyncMock()
        agent.memory.add = AsyncMock()
        agent.memory.get_memory = AsyncMock(return_value=[])

        # Set up msg_queue
        queue = asyncio.Queue()
        agent.msg_queue = queue
        agent.set_msg_queue_enabled = MagicMock()

        # Mock reply to emit events to queue
        from agentscope.message import Msg

        async def fake_reply(msg):
            # Emit a text message
            text_msg = Msg(name="agent", content="你好世界", role="assistant")
            await queue.put((text_msg, True, None))
            return text_msg

        agent.reply = fake_reply
        return agent, []  # agent, mcp_clients

    async def _run() -> list:
        with patch.object(wf, "get_router_strategy", return_value=_Router()), \
             patch.object(wf, "create_agent_executor", side_effect=mock_create_agent_executor), \
             patch.object(wf, "launch_memory_retrieval", return_value=AsyncMock(return_value=None)()), \
             patch.object(wf, "inject_memories", side_effect=lambda task, msgs: asyncio.coroutine(lambda: msgs)()):

            events = []
            async for item in wf.astream_chat_workflow(
                session_messages=[{"role": "user", "content": "你好"}],
                user_message="你好",
                context={"chat_id": "stream_case", "user_id": "tester"},
            ):
                events.append(item)
            return events

    events = asyncio.run(_run())

    # Check we got events
    assert events, "expected streamed events"

    # Last event should be meta
    last = events[-1]
    assert last.get("type") == "meta", f"expected last event=meta, got {last!r}"
    assert last.get("route") == "main", f"unexpected route in meta: {last!r}"

    print("workflow_streaming_selftest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
