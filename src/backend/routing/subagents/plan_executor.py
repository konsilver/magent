"""SubAgent step execution helpers for plan mode."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import time as _time

logger = logging.getLogger(__name__)

from core.infra import log_writer
from core.llm.agent_factory import create_agent_executor
from routing.streaming import _UsageTrackingModel
from routing.subagents.plan_store import (
    _context_board_summary,
    _parse_json_output,
)
from routing.subagents.plan_agents import _run_qa


def _build_subagent_instruction(
    step: Any,
    next_step: Optional[Any],
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    retrieved_memory: Dict,
) -> str:
    """Build full instruction string for SubAgent."""
    parts = []

    parts.append(f"## context 黑板（共享状态）\n{_context_board_summary(board)}")

    step_desc = step.description or getattr(step, "title", "")
    parts.append(f"## 我的当前任务\n步骤 {step.step_order}：{step_desc}")

    if local_constraint:
        parts.append(f"## 上个 Agent 为我制定的局部约束\n{json.dumps(local_constraint, ensure_ascii=False, indent=2)}")

    if expected_schema:
        parts.append(f"## 上个 Agent 为我定义的输出格式\n{json.dumps(expected_schema, ensure_ascii=False, indent=2)}")

    # REDO 情况：从 board["plan"]["suggestion"] 读取 QA 建议
    redo_suggestion = board.get("plan", {}).get("suggestion")
    if redo_suggestion:
        parts.append(
            f"## 【重做任务】QA 优化建议\n"
            f"你的上一次执行未通过 QA 检查，请参考以下建议重新完成任务：\n{redo_suggestion}"
        )

    patterns = retrieved_memory.get("relevant_patterns", [])
    if any(p for p in patterns):
        parts.append("## 历史相似执行经验（参考）\n" + "\n".join(f"- {p}" for p in patterns if p))

    if next_step:
        _next_order = getattr(next_step, "step_order", "?")
        _next_desc = getattr(next_step, "description", "") or getattr(next_step, "title", "")
        parts.append(f"## 下一步任务（你需要为它制定约束）\n步骤 {_next_order}：{_next_desc}")

    is_last_step = next_step is None

    next_step_instruction_hint = "null" if is_last_step else f"""\
{{
  "local_constraint": {{
    "constraint": [
      {{
        "constraint_type": "field_presence | value_range | format | dependency",
        "target": "字段名",
        "rule": "字段规则"
      }}
    ],
    "priority": "hard | soft"
  }},
  "expected_output_schema": {{
    "fields": ["字段1", "字段2"],
    "required": ["字段1"]
  }}
}}"""

    parts.append(f"""## 执行要求
1. 聚焦当前步骤目标，不执行其他步骤的任务
2. 必须遵守上述局部约束（如有）和 context 黑板中的 global_constraints
3. 【重要】context.user 字段（用户实时特征）优先级高于历史记忆中任何 suggestion
4. 【参考前序输出】context 黑板中已完成步骤的 output 字段记录了前序步骤的执行结果，可结合这些输出更好地完成当前任务
5. 【输出纪律】**禁止在回复中输出任何思考过程、推理步骤或自我分析**。直接输出最终结论和内容，不要写"我需要…"、"根据规则…"、"让我…"、"首先…其次…"等过程性文字。
6. 完成执行后，**必须**在输出末尾附加如下 JSON 块：

```json
{{
  "result": "当前步骤执行结果摘要",
  "next_step_instruction": {next_step_instruction_hint}
}}
```

{"【制定下一步约束】在输出 JSON 前，先审视整个计划（context 黑板中的全部步骤）以及下一步任务的具体内容，再为下一步制定局部约束与输出格式，遵循以下规则：" if not is_last_step else "这是最后一步，next_step_instruction 填 null。"}""")

    if not is_last_step:
        parts.append("""\
## 约束生成规则（为下一步制定约束时必须遵守）

**Step 1：生成 expected_output_schema（先于 constraint 生成）**
- fields：列出下一步输出应包含的所有字段
- required：必须是 fields 的子集

**Step 2：生成 local_constraint**
- constraint.target 必须 ∈ expected_output_schema.fields
- 每个 required 字段必须有 field_presence constraint
- 禁止引用 fields 中未定义的字段
- 不允许生成模糊约束（如：合理、尽量、适当）
- 软硬约束比例：hard ≥ 60%，soft ≤ 40%
- 禁止为低风险任务添加结构性约束

**Step 3：一致性自检**
- 所有 constraint.target 是否都在 fields 中
- required 字段是否都有 field_presence constraint
- 是否存在模糊约束
如发现不一致，修正后再输出。""")

    return "\n\n".join(parts)


async def _run_subagent_step(
    step: Any,
    next_step: Optional[Any],
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    retrieved_memory: Dict,
    prepared_history: List[Dict],
    uploaded_files: Optional[List[Dict]],
    model_name: str,
    user_id: str,
    enabled_kb_ids: Optional[List[str]],
    _cumulative_usage: "_UsageTrackingModel",
    _plan_subagent_log_id: str,
    _all_mcp_clients: List,
) -> AsyncIterator[Dict[str, Any]]:
    """Execute a single SubAgent step, yield SSE events."""
    instruction = _build_subagent_instruction(
        step, next_step, board, local_constraint, expected_schema,
        retrieved_memory,
    )

    logger.info("[SubAgent] START step=%d(%s) id=%s model=%s prompt_chars=%d\n--- PROMPT ---\n%s\n--- PROMPT END ---",
                step.step_order, step.title, step.step_id, model_name, len(instruction), instruction)

    step_text = ""
    step_tool_calls: List[Dict] = []
    _step_start = _time.monotonic()
    # Snapshot cumulative usage before this step so we can compute per-step delta
    _usage_before = sum(
        r.get("prompt_tokens", 0) + r.get("completion_tokens", 0)
        for r in _cumulative_usage.usage_records
    )
    _calls_before = len(_cumulative_usage.usage_records)

    _step_log_id = await log_writer.start_subagent_log({
        "subagent_name": f"plan_mode:step_{step.step_order}",
        "subagent_type": "plan_step",
        "subagent_id": step.step_id,
        "step_id": step.step_id,
        "step_index": step.step_order,
        "step_title": step.title,
        "model": model_name,
        "parent_subagent_log_id": _plan_subagent_log_id,
        "input_messages": {"instruction": instruction},
    })
    _step_outcome = "success"
    _step_error_msg: Optional[str] = None
    _pool_slot = None
    mcp_clients = []

    try:
        _step_max_iters = int(os.environ.get("PLAN_STEP_MAX_ITERS", "5"))
        from core.llm.agent_pool import AgentPool as _AgentPool
        _pool = _AgentPool.get_instance()
        _use_pool = _pool.is_ready
        if _use_pool:
            try:
                _acquire_t0 = _time.monotonic()
                _pooled = await _pool._acquire_direct()
                _pooled.reset()
                agent = _pooled.agent
                agent.max_iters = _step_max_iters
                # Inject model_name into context so dynamic_model hook uses the right model
                if hasattr(agent, "_jx_context") and agent._jx_context is not None:
                    agent._jx_context.model_name = model_name
                _pool_slot = _pooled
                logger.info("[SubAgent] step=%d acquired from pool in %.0fms model=%s",
                            step.step_order, (_time.monotonic() - _acquire_t0) * 1000, model_name)
            except Exception as _pe:
                logger.warning("[SubAgent] step=%d pool acquire failed (%s), falling back to create_agent_executor",
                               step.step_order, _pe)
                _use_pool = False

        if not _use_pool:
            _create_t0 = _time.monotonic()
            # 非隔离模式（isolated=False）可从 MCP pool 缓存直接构建 toolkit，
            # 避免 isolated=True 时重新 spawn MCP 子进程的高延迟
            agent, mcp_clients = await create_agent_executor(
                enabled_mcp_ids=None,
                enabled_skill_ids=None,
                enabled_kb_ids=enabled_kb_ids,
                current_user_id=user_id,
                model_name=model_name,
                isolated=False,
                max_iters=_step_max_iters,
            )
            logger.info("[SubAgent] step=%d created fresh agent in %.0fms",
                        step.step_order, (_time.monotonic() - _create_t0) * 1000)

        _orig_hook = agent._instance_pre_reply_hooks.get("dynamic_model")
        if _orig_hook:
            async def _patched_hook(ag, kwargs, _oh=_orig_hook, _proxy=_cumulative_usage):
                result = await _oh(ag, kwargs)
                real = ag.model
                if not isinstance(real, _UsageTrackingModel):
                    _proxy._real = real
                    ag.model = _proxy
                return result
            agent._instance_pre_reply_hooks["dynamic_model"] = _patched_hook

        from agentscope.message import Msg
        from core.llm.message_compat import load_session_into_memory

        await load_session_into_memory(prepared_history, agent.memory)

        from routing.subagents.plan_store import _build_file_context
        file_context = _build_file_context(uploaded_files or [])
        if file_context:
            await agent.memory.add(Msg(name="user", content=file_context, role="user"))

        user_msg = Msg(name="user", content=instruction, role="user")

        yield {"type": "plan_step_progress", "step_id": step.step_id, "delta": "正在执行...\n"}

        try:
            _collected_calls: List[Dict] = []
            _pending_log: Dict[str, Dict] = {}

            with log_writer.subagent_scope(_step_log_id, source="subagent"):
                reply_task = asyncio.create_task(agent.reply(user_msg))
            while not reply_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(reply_task), timeout=15)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
                except asyncio.CancelledError:
                    raise

            reply = reply_task.result()

            try:
                mem_msgs = agent.memory.get_memory()
                if asyncio.iscoroutine(mem_msgs):
                    mem_msgs = await mem_msgs
                for mem_msg in (mem_msgs or []):
                    if hasattr(mem_msg, "has_content_blocks") and mem_msg.has_content_blocks("tool_use"):
                        for block in mem_msg.get_content_blocks("tool_use"):
                            tool_name = block.get("name", "unknown")
                            tool_id = block.get("id", "")
                            tool_args = block.get("input", {})
                            _collected_calls.append(block)
                            _pending_log[tool_id] = {"tool_name": tool_name, "tool_args": tool_args}
                            yield {
                                "type": "tool_call",
                                "step_id": step.step_id,
                                "tool_name": tool_name,
                                "tool_id": tool_id,
                                "tool_args": tool_args,
                            }
                    if hasattr(mem_msg, "has_content_blocks") and mem_msg.has_content_blocks("tool_result"):
                        for block in mem_msg.get_content_blocks("tool_result"):
                            tool_name = block.get("name", "unknown")
                            tool_id = block.get("id", "")
                            output = block.get("output", [])
                            content: Any = output
                            if isinstance(output, list):
                                text_parts = []
                                has_only_text = True
                                for item in output:
                                    if isinstance(item, dict):
                                        tv = item.get("text")
                                        if tv is not None:
                                            text_parts.append(str(tv))
                                        else:
                                            has_only_text = False
                                            break
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                    else:
                                        has_only_text = False
                                        break
                                if has_only_text and text_parts:
                                    joined = "\n".join(text_parts)
                                    try:
                                        content = json.loads(joined)
                                    except (json.JSONDecodeError, ValueError):
                                        content = joined
                            elif isinstance(output, str):
                                try:
                                    content = json.loads(output)
                                except (json.JSONDecodeError, ValueError):
                                    content = output
                            _call = _pending_log.pop(tool_id, {})
                            log_writer.schedule_tool_call_write({
                                "tool_name": _call.get("tool_name") or tool_name,
                                "tool_call_id": tool_id,
                                "tool_args": _call.get("tool_args"),
                                "tool_result": content,
                                "status": "success",
                                "source": "subagent",
                                "subagent_log_id": _step_log_id,
                            })
                            yield {
                                "type": "tool_result",
                                "step_id": step.step_id,
                                "tool_name": tool_name,
                                "tool_id": tool_id,
                                "result": content,
                            }
            except Exception as _mem_exc:
                logger.warning("[plan-exec] Failed to extract tool calls from memory: %s", _mem_exc)

            if hasattr(reply, "content"):
                if isinstance(reply.content, str):
                    step_text = reply.content
                elif isinstance(reply.content, list):
                    parts = []
                    for block in reply.content:
                        if hasattr(block, "text"):
                            parts.append(block.text)
                        elif isinstance(block, dict) and "text" in block:
                            parts.append(block["text"])
                        elif isinstance(block, str):
                            parts.append(block)
                    step_text = "\n".join(parts)
                else:
                    step_text = str(reply.content)
            else:
                step_text = str(reply)

            step_text = re.sub(r"<think>.*?</think>", "", step_text, flags=re.DOTALL).strip()
            step_tool_calls = _collected_calls

            _exec_elapsed = (_time.monotonic() - _step_start) * 1000
            _step_records = _cumulative_usage.usage_records[_calls_before:]
            _step_prompt = sum(r.get("prompt_tokens", 0) for r in _step_records)
            _step_completion = sum(r.get("completion_tokens", 0) for r in _step_records)
            _step_total = _step_prompt + _step_completion
            _step_llm_calls = len(_step_records)
            logger.info(
                "[SubAgent] step=%d(%s) DONE elapsed=%.0fms tool_calls=%d output_chars=%d "
                "llm_calls=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d",
                step.step_order, step.title, _exec_elapsed,
                len(step_tool_calls), len(step_text),
                _step_llm_calls, _step_prompt, _step_completion, _step_total,
            )
            if step_tool_calls:
                tool_names = [tc.get("name", "?") for tc in step_tool_calls[:5]]
                logger.info("[SubAgent] step=%d tools used: %s", step.step_order, tool_names)

            # 只向前端发送 narrative 部分，JSON 块留给后续约束传递，不展示给用户
            _narrative_for_display, _ = _extract_next_step_instruction(step_text)
            # threshold=0: no safety guard — always strip reasoning preamble for display
            _display = _strip_thinking_preamble(_narrative_for_display or step_text, threshold=0)
            yield {"type": "plan_step_progress", "step_id": step.step_id, "delta": _display}

        except asyncio.TimeoutError:
            logger.warning("[SubAgent] step=%d(%s) TIMEOUT", step.step_order, step.title)
            step_text = "步骤执行被取消"
            _step_outcome = "failed"
            _step_error_msg = "timeout"
        except Exception as _reply_exc:
            _err = f"{type(_reply_exc).__name__}: {_reply_exc}".strip(": ")
            logger.warning("[SubAgent] step=%d(%s) ERROR: %s", step.step_order, step.title, _err)
            step_text = f"执行出错: {_err or type(_reply_exc).__name__}"
            _step_outcome = "failed"
            _step_error_msg = _err

        _all_mcp_clients.extend(mcp_clients)

    except Exception as step_exc:
        logger.exception("Step %s agent setup failed", step.step_id)
        step_text = f"步骤初始化失败: {step_exc}"
        _step_outcome = "failed"
        _step_error_msg = str(step_exc)

    finally:
        if _pool_slot is not None:
            try:
                _pool_slot._lock.release()
            except Exception:
                pass
        await log_writer.finish_subagent_log(
            _step_log_id,
            status=_step_outcome,
            output_content=step_text,
            intermediate_steps=step_tool_calls[:100] if step_tool_calls else None,
            tool_calls_count=len(step_tool_calls),
            duration_ms=int((_time.monotonic() - _step_start) * 1000),
            error_message=_step_error_msg,
        )

    yield {
        "type": "_step_result",
        "step_id": step.step_id,
        "step_text": step_text,
        "step_tool_calls": step_tool_calls,
        "outcome": _step_outcome,
        "error_msg": _step_error_msg,
    }


def _strip_thinking_preamble(text: str, threshold: float = 0.70) -> str:
    """Strip model meta-commentary from the beginning of a response.

    Strategy: find the LAST line that looks like reasoning/preamble, strip
    everything up to and including it.  If the stripped portion exceeds
    `threshold` of the total length, keep the original (safety guard).
    """
    if not text:
        return text

    _REASONING_PATTERNS = re.compile(
        r"^(好的|当然|让我|我来|我需要|我将|我会|首先|根据|分析|理解|明白"
        r"|okay|sure|let me|i will|i'll|i need to|first|based on|alright"
        r"|以下是|下面是|接下来)[，,。.：: ]",
        re.IGNORECASE,
    )

    lines = text.splitlines()
    last_reasoning_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and _REASONING_PATTERNS.match(stripped):
            last_reasoning_idx = i

    if last_reasoning_idx == -1:
        return text

    candidate = "\n".join(lines[last_reasoning_idx + 1:]).strip()
    if not candidate:
        return text

    if len(candidate) < len(text) * threshold:
        return text

    return candidate


def _extract_next_step_instruction(step_text: str) -> Tuple[str, Dict]:
    """Split step_text into (narrative_text, next_step_instruction dict)."""
    match = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", step_text, re.DOTALL)
    if not match:
        last_brace = step_text.rfind("}")
        first_brace = step_text.rfind("{", 0, last_brace)
        if first_brace != -1 and last_brace != -1:
            candidate = step_text[first_brace:last_brace + 1]
            try:
                data = json.loads(candidate)
                narrative = step_text[:first_brace].strip()
                return narrative, data
            except Exception:
                pass
        return step_text, {}

    candidate = match.group(1)
    try:
        data = json.loads(candidate)
        narrative = step_text[:match.start()].strip()
        return narrative, data
    except Exception:
        return step_text, {}
