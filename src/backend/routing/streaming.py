"""Streaming agent wrapper for AgentScope.

Consumes the agent's msg_queue to emit SSE-compatible events:
- ("text_delta", str)      - incremental text output
- ("tool_call", dict)      - tool invocation started
- ("tool_result", dict)    - tool invocation completed
- ("done", None)           - agent finished
- ("error", Exception)     - agent error
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from agentscope.agent import ReActAgent
from agentscope.mcp import StdIOStatefulClient
from agentscope.message import Msg

from core.infra import log_writer
from core.infra.logging import LogContext
from core.llm.hooks import ModelContext
from core.llm.message_compat import load_session_into_memory

logger = logging.getLogger(__name__)


class _UsageTrackingModel:
    """Transparent proxy around the real model that records token usage.

    AgentScope calls ``model(messages, ...)`` which invokes ``__call__``.
    We intercept this and passively accumulate ChatUsage data without
    altering any return values or control flow.
    """

    def __init__(self, real_model: Any):
        self._real = real_model
        self.usage_records: List[Dict[str, int]] = []

    def __getattr__(self, name: str) -> Any:
        if self._real is None:
            raise AttributeError(name)
        return getattr(self._real, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._real(*args, **kwargs)

        if hasattr(result, "__aiter__"):
            records = self.usage_records

            async def _wrap_gen():
                last_usage: Dict[str, int] | None = None
                async for chat_resp in result:
                    _u = getattr(chat_resp, "usage", None)
                    if _u is not None:
                        last_usage = {
                            "prompt_tokens": getattr(_u, "input_tokens", 0),
                            "completion_tokens": getattr(_u, "output_tokens", 0),
                        }
                    yield chat_resp
                # Only record the final usage per LLM call
                if last_usage is not None:
                    records.append(last_usage)

            return _wrap_gen()

        _u = getattr(result, "usage", None)
        if _u is not None:
            self.usage_records.append({
                "prompt_tokens": getattr(_u, "input_tokens", 0),
                "completion_tokens": getattr(_u, "output_tokens", 0),
            })
        return result


class StreamingAgent:
    """Wraps an AgentScope ReActAgent to produce streaming SSE events.

    Consumes the agent's msg_queue to detect:
    - Text deltas (cumulative → delta conversion)
    - Tool call starts
    - Tool results

    Key design: AgentScope streaming is *cumulative* — each chunk contains
    all text so far.  We track ``_previous_text`` to compute deltas.
    Between reasoning steps (after a tool call), the agent creates a fresh
    Msg so cumulative text restarts from "".  We must reset
    ``_previous_text`` at that boundary.
    """

    def __init__(
        self,
        agent: ReActAgent,
        mcp_clients: List[StdIOStatefulClient],
    ):
        self.agent = agent
        self.mcp_clients = mcp_clients
        self._previous_text = ""
        self._in_thinking = False
        self._enable_thinking = False
        self._usage_proxy: Optional[_UsageTrackingModel] = None
        # tool_id → {tool_name, tool_args, started_at_monotonic}
        self._pending_tool_calls: Dict[str, Dict[str, Any]] = {}

    def get_usage(self) -> Dict[str, int]:
        """Return accumulated token usage across all LLM calls in this session."""
        if self._usage_proxy is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_call_count": 0}
        records = self._usage_proxy.usage_records
        total_prompt = sum(r.get("prompt_tokens", 0) for r in records)
        total_completion = sum(r.get("completion_tokens", 0) for r in records)
        return {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "llm_call_count": len(records),
        }

    async def stream(
        self,
        session_messages: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> AsyncIterator[Tuple[str, Any]]:
        """Stream agent execution events.

        Yields:
            Tuples of (event_type, payload):
            - ("text_delta", str) - incremental text chunk
            - ("tool_call", {"name": str, "args": dict, "id": str})
            - ("tool_result", {"name": str, "id": str, "content": str})
            - ("error", Exception)
        """
        agent = self.agent

        ctx = ModelContext(
            model_name=str(context.get("model_name", "")),
            user_id=str(context.get("user_id", "")),
            chat_id=str(context.get("chat_id", "")),
            enable_thinking=bool(context.get("enable_thinking", True)),
            uploaded_files=list(context.get("uploaded_files", [])),
            historical_files=list(context.get("historical_files", [])),
        )
        agent._jx_context = ctx  # type: ignore[attr-defined]
        self._enable_thinking = ctx.enable_thinking

        # Observability log writers read user_id / chat_id from these
        # ContextVars.  SSE body-only endpoints don't populate them in the
        # outer middleware, so set them here for the scope of this stream.
        _log_ctx = LogContext(user_id=ctx.user_id or None, chat_id=ctx.chat_id or None)
        _log_ctx.__enter__()

        # Load session history into agent memory, EXCLUDING the last user
        # message.  agent.reply() will add it via memory.add(msg) internally,
        # so including it here would cause a duplicate.
        history = list(session_messages)
        last_user_content = ""
        if history and history[-1].get("role") in ("user", "human"):
            last_msg = history.pop()
            last_user_content = last_msg.get("content", "")

        if history:
            await load_session_into_memory(history, agent.memory)

        # Build user message from the last session message
        user_msg: Msg | None = None
        if last_user_content:
            user_msg = Msg(name="user", content=last_user_content, role="user")

        # Enable msg_queue for streaming
        agent.set_msg_queue_enabled(True)

        # Run agent.reply in background task
        import time as _time
        _stream_start = _time.monotonic()
        _first_event_logged = False

        reply_task = asyncio.create_task(self._run_agent(user_msg))

        # Consume msg_queue
        self._previous_text = ""
        self._in_thinking = False
        agent_finished = False
        # Some OpenAI-compatible models (MiniMax, etc.) buffer tool_call
        # args server-side and only emit the completed chunk once the LLM
        # finishes writing the JSON. That produces 30-60s of silence
        # mid-stream. A short poll + tool_pending ping replaces the stuck
        # "..." indicator with a clear "preparing tool call" placeholder.
        _poll_interval = 3.0
        _total_silence_cap = 120.0
        _silence_accum = 0.0
        _pending_emitted = False
        try:
            while not agent_finished:
                try:
                    msg, is_last, _speech = await asyncio.wait_for(
                        agent.msg_queue.get(), timeout=_poll_interval
                    )
                except asyncio.TimeoutError:
                    if reply_task.done():
                        exc = reply_task.exception() if not reply_task.cancelled() else None
                        logger.warning(
                            "StreamingAgent: msg_queue timeout (agent done, exception=%s)",
                            exc,
                        )
                        break
                    _silence_accum += _poll_interval
                    if _silence_accum >= _total_silence_cap:
                        logger.warning(
                            "StreamingAgent: total silence exceeded %ss, breaking",
                            _total_silence_cap,
                        )
                        break
                    if not _pending_emitted:
                        yield ("tool_pending", {"reason": "llm_buffering"})
                        _pending_emitted = True
                    yield ("heartbeat", None)
                    continue

                _silence_accum = 0.0
                _pending_emitted = False

                # Process the message and yield events
                async for event in self._process_msg(msg, is_last):
                    if not _first_event_logged:
                        _ttfe = (_time.monotonic() - _stream_start) * 1000
                        logger.info("[stream] TTFE (time to first event): %.0fms, type=%s", _ttfe, event[0])
                        _first_event_logged = True
                    yield event

                # Detect agent completion: the FINAL assistant message has
                # is_last=True and does NOT contain tool_use blocks (no more
                # tool calls pending).  An intermediate reasoning step that
                # triggers a tool call also has is_last=True but includes
                # tool_use blocks — we must NOT break in that case.
                if is_last and msg.role == "assistant":
                    if not msg.has_content_blocks("tool_use"):
                        # This is the final text-only response → done
                        agent_finished = True

        except Exception as e:
            yield ("error", e)
        finally:
            # Record any tool_use block that never received its matching
            # tool_result (e.g. agent crashed mid-call) so the log reflects
            # the abort instead of silently leaking the entry.
            for _tid, _rec in list(self._pending_tool_calls.items()):
                try:
                    _started_mono = _rec.get("started_monotonic")
                    _dur = (
                        int((time.monotonic() - _started_mono) * 1000)
                        if _started_mono is not None else None
                    )
                    log_writer.schedule_tool_call_write({
                        "tool_name": _rec.get("tool_name", "unknown"),
                        "tool_call_id": _tid,
                        "tool_args": _rec.get("tool_args"),
                        "tool_result": None,
                        "status": "failed",
                        "error_message": "no tool_result received (stream ended)",
                        "duration_ms": _dur,
                        "started_at": _rec.get("started_at"),
                    })
                except Exception:
                    logger.debug("pending tool_call flush failed", exc_info=True)
            self._pending_tool_calls.clear()

            try:
                _log_ctx.__exit__(None, None, None)
            except Exception:
                pass

            # Don't block on reply_task — it may hang if the agent is stuck
            # in post-processing or MCP cleanup.  We schedule a background
            # task to wait/cancel it so the SSE stream can emit meta/follow_up
            # events without delay.
            if not reply_task.done():
                async def _wait_reply():
                    try:
                        await asyncio.wait_for(asyncio.shield(reply_task), timeout=10)
                    except asyncio.TimeoutError:
                        logger.warning("StreamingAgent: reply_task timed out, cancelling")
                        reply_task.cancel()
                        try:
                            await reply_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    except Exception as exc:
                        logger.warning("StreamingAgent: reply_task error: %s", exc)

                asyncio.create_task(_wait_reply())

    async def _run_agent(self, user_msg: Optional[Msg]) -> Msg:
        """Run agent.reply in the background.

        Wraps the dynamic-model hook so that whatever model it assigns
        gets wrapped by our usage-tracking proxy before the agent uses it.
        """
        agent = self.agent
        proxy = _UsageTrackingModel(None)
        self._usage_proxy = proxy

        # Patch the dynamic_model hook to route through our proxy
        orig_hook = agent._instance_pre_reply_hooks.get("dynamic_model")
        if orig_hook:
            async def _patched_hook(ag: Any, kwargs: Any) -> Any:
                result = await orig_hook(ag, kwargs)
                # After dynamic_model sets ag.model, wrap it
                real = ag.model
                if not isinstance(real, _UsageTrackingModel):
                    proxy._real = real
                    ag.model = proxy
                return result
            agent._instance_pre_reply_hooks["dynamic_model"] = _patched_hook

        try:
            return await agent.reply(user_msg)
        except Exception as e:
            logger.error("Agent reply failed: %s", e)
            raise
        finally:
            # Restore original hook
            if orig_hook:
                agent._instance_pre_reply_hooks["dynamic_model"] = orig_hook

    async def _process_msg(
        self, msg: Msg, is_last: bool
    ) -> AsyncIterator[Tuple[str, Any]]:
        """Process a single msg_queue message into events."""

        # ── tool_use blocks (assistant requesting tool calls) ─────────
        has_tool_use = msg.has_content_blocks("tool_use")
        if has_tool_use:
            for block in msg.get_content_blocks("tool_use"):
                tool_name = block.get("name", "unknown")
                tool_args = block.get("input", {})
                tool_id = block.get("id", "")
                # Remember the call so we can pair it with its result
                # when the matching tool_result block arrives.
                self._pending_tool_calls[tool_id] = {
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "started_monotonic": time.monotonic(),
                    "started_at": datetime.now(timezone.utc),
                }
                yield ("tool_call", {
                    "name": tool_name,
                    "args": tool_args,
                    "id": tool_id,
                })

            # After a tool call, the next reasoning step will produce
            # fresh text starting from "".  Reset the accumulator so
            # the delta is computed correctly.
            if is_last:
                self._previous_text = ""
                self._in_thinking = False

            # IMPORTANT: Do NOT extract text from this message.
            # The text (e.g. "I'll search for that") was already
            # streamed in earlier chunks.  Extracting it again after
            # resetting _previous_text would send it a second time.
            return

        # ── tool_result blocks (system reporting tool output) ─────────
        if msg.has_content_blocks("tool_result"):
            for block in msg.get_content_blocks("tool_result"):
                tool_name = block.get("name", "unknown")
                tool_id = block.get("id", "")
                output = block.get("output", [])
                # Convert output blocks to string
                if isinstance(output, list):
                    content_parts = []
                    for item in output:
                        if isinstance(item, dict):
                            text_val = item.get("text")
                            if text_val is not None:
                                content_parts.append(str(text_val))
                            else:
                                content_parts.append(str(item))
                        elif isinstance(item, str):
                            content_parts.append(item)
                    content = "\n".join(content_parts)
                else:
                    content = str(output)
                # ── Observability: persist paired tool call log ──
                pending = self._pending_tool_calls.pop(tool_id, None)
                try:
                    is_error = bool(block.get("is_error"))
                    started_mono = pending.get("started_monotonic") if pending else None
                    duration_ms = (
                        int((time.monotonic() - started_mono) * 1000)
                        if started_mono is not None
                        else None
                    )
                    log_writer.schedule_tool_call_write({
                        "tool_name": (pending or {}).get("tool_name") or tool_name,
                        "tool_call_id": tool_id,
                        "tool_args": (pending or {}).get("tool_args"),
                        "tool_result": output,
                        "status": "failed" if is_error else "success",
                        "error_message": content if is_error else None,
                        "duration_ms": duration_ms,
                        "started_at": (pending or {}).get("started_at"),
                    })
                except Exception:  # noqa: BLE001 — logging is best-effort
                    logger.debug("tool_call log persist failed", exc_info=True)

                yield ("tool_result", {
                    "name": tool_name,
                    "id": tool_id,
                    "content": content,
                })
            # tool_result messages have no user-visible text to extract
            return

        # ── text blocks (streaming text delta) ────────────────────────
        text = self._extract_text(msg)
        if text and text != self._previous_text:
            delta = text[len(self._previous_text):] if text.startswith(self._previous_text) else text
            if delta:
                yield ("text_delta", delta)
            self._previous_text = text

    def _extract_text(self, msg: Msg) -> str:
        """Extract text content from a Msg, optionally stripping thinking blocks.

        Thinking models (e.g. DeepSeek R1) emit thinking text before
        ``</think>`` and the real answer after it.  The ``<think>`` opening
        tag may or may not be present.

        When ``_enable_thinking`` is True, the raw text (including
        ``<think>``/``</think>`` tags) is returned as-is so the frontend
        can parse and display thinking blocks.

        When ``_enable_thinking`` is False, we suppress ALL text until
        ``</think>`` appears and return only the text after the tag.
        """
        if not msg.has_content_blocks("text"):
            return ""
        text_blocks = msg.get_content_blocks("text")
        parts = [b.get("text", "") for b in text_blocks if isinstance(b, dict)]
        raw = "".join(parts)

        # When thinking mode is enabled, pass through raw text with tags
        # so the frontend can parse <think>...</think> blocks itself.
        if self._enable_thinking:
            return raw

        last_end = raw.rfind("</think>")
        if last_end != -1:
            # Closing tag found — return everything after it
            raw = raw[last_end + len("</think>"):]
        elif "<think>" in raw or self._in_thinking:
            # Still inside thinking — suppress output
            self._in_thinking = True
            return ""

        if raw:
            self._in_thinking = False

        return raw

    async def shutdown(self):
        """Close transient (per-request) MCP clients.

        Only clients in self.mcp_clients are transient — stable pooled
        clients are managed by MCPConnectionPool and are NOT included here.

        Stdio clients: terminate subprocess directly to avoid anyio cancel
        scope leaks when close() is called from a different task.

        HTTP (streamable_http/sse) clients: nullify the AsyncExitStack
        directly rather than calling close(), because the anyio cancel scope
        created during connect() is bound to the originating task and cannot
        be exited from a different task without raising RuntimeError.
        """
        for client in self.mcp_clients:
            try:
                # HTTP transient clients: abandon the exit stack in-place.
                # The underlying HTTP session is stateless-enough that leaking
                # it is safe; the remote server will time out the session.
                if getattr(client, "transport", None) in ("streamable_http", "sse"):
                    client.stack = None
                    client.session = None
                    client.is_connected = False
                    continue

                # Stdio clients: terminate subprocess to avoid cancel-scope issues.
                proc = getattr(client, "_process", None) or getattr(client, "process", None)
                if proc is not None and proc.returncode is None:
                    proc.terminate()
            except Exception:
                pass
        self.mcp_clients = []
