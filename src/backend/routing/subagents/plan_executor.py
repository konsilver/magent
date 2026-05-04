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
from core.llm.message_compat import strip_thinking, strip_final_output_thinking

_CODE_EXEC_PROMPT_CACHE: Optional[str] = None


def _load_code_exec_prompt() -> str:
    """Load and concatenate all code_exec system prompt files (cached)."""
    global _CODE_EXEC_PROMPT_CACHE
    if _CODE_EXEC_PROMPT_CACHE is not None:
        return _CODE_EXEC_PROMPT_CACHE
    _code_exec_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', 'prompts', 'prompt_text', 'code_exec', 'system',
    )
    if not os.path.isdir(_code_exec_dir):
        _CODE_EXEC_PROMPT_CACHE = ""
        return ""
    sections = []
    for fname in sorted(f for f in os.listdir(_code_exec_dir) if f.endswith('.system.md')):
        with open(os.path.join(_code_exec_dir, fname), 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content:
                sections.append(content)
    _CODE_EXEC_PROMPT_CACHE = "\n\n".join(sections)
    return _CODE_EXEC_PROMPT_CACHE


def _build_subagent_instruction(
    step: Any,
    next_step: Optional[Any],
    board: Dict[str, Any],
    local_constraint: Dict,
    expected_schema: Dict,
    retrieved_memory: Dict,
    code_exec_enabled: bool = False,
) -> str:
    """Build full instruction string for SubAgent."""
    parts = []

    parts.append(f"## context 黑板（共享状态）\n{_context_board_summary(board)}")

    step_desc = step.description or getattr(step, "title", "")
    parts.append(f"## 你的当前任务\n步骤 {step.step_order}：{step_desc}")

    if local_constraint:
        parts.append(f"## 上个 Agent 为你制定的局部约束\n{json.dumps(local_constraint, ensure_ascii=False, indent=2)}")

    if expected_schema:
        parts.append(f"## 上个 Agent 为你定义的输出格式\n{json.dumps(expected_schema, ensure_ascii=False, indent=2)}")

    # REDO 情况：从 board["plan"]["suggestion"] 读取 QA 建议
    redo_suggestion = board.get("plan", {}).get("suggestion")
    if redo_suggestion:
        parts.append(
            f"##QA 优化建议\n"
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
        "rule": "字段规则",
        "priority": "hard | soft"
      }}
    ]
  }},
  "expected_output_schema": {{
    "fields": ["字段1", "字段2"],
    "required": ["字段1"]
  }}
}}"""

    _code_exec_hint = (
        "（若执行了代码，result 字段需包含代码内容与执行结果；超过100行则用伪代码；执行失败则填报错信息）"
    ) if code_exec_enabled else ""

    parts.append(f"""## 执行要求
1. 聚焦当前步骤目标，不执行其他步骤的任务
2. 必须遵守上述局部约束（如有）和 context 黑板中的 global_constraints
3. context 黑板中已完成步骤的 output 字段记录了前序步骤的执行结果，结合这些输出完成你的任务
4. if_code_exc=false 时禁止调用代码执行工具；if_code_exc=true 时必须执行代码并验证结果
5. 完成执行后，输出如下 JSON 块{_code_exec_hint}：
```json
{{
  "result": "当前步骤执行结果摘要",
  "next_step_instruction": {next_step_instruction_hint}
}}
```
""")

    if code_exec_enabled:
        _code_exec_prompt = _load_code_exec_prompt()
        if _code_exec_prompt:
            parts.append(_code_exec_prompt)

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
    code_exec_enabled: bool = False,
    step_complexity: str = "simple",
) -> AsyncIterator[Dict[str, Any]]:
    """Execute a single SubAgent step, yield SSE events."""
    instruction = _build_subagent_instruction(
        step, next_step, board, local_constraint, expected_schema,
        retrieved_memory, code_exec_enabled=code_exec_enabled,
    )

    logger.info("[SubAgent] START step=%d(%s) id=%s model=%s prompt_chars=%d",
                step.step_order, step.title, step.step_id, model_name, len(instruction))

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
        if code_exec_enabled:
            # Pool agents have a fixed toolkit; execute_code cannot be injected at runtime.
            # Force fresh agent creation so code_exec_enabled is properly wired up.
            _use_pool = False
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
                # Pool agents carry the full main-agent system prompt; replace with
                # the plan-mode minimal prompt so SubAgent gets no irrelevant context.
                import datetime as _datetime
                _plan_sys = "\n\n".join([
                    f"## 当前时间\n{_datetime.datetime.now().isoformat()}",
                    (
                        "## 输出格式\n"
                        "- 代码必须放在带语言标识的 Markdown 代码块中（` ```python `、` ```bash ` 等）\n"
                        "- 执行成功：输出 `执行成功（exit_code: 0）` 及关键 stdout\n"
                        "- 执行失败：输出 `执行失败（exit_code: N）` 及完整 stderr\n"
                        "- 语言：中文输出，技术术语保留英文原文"
                    ),
                ])
                try:
                    agent.sys_prompt = _plan_sys
                except AttributeError:
                    object.__setattr__(agent, "_sys_prompt", _plan_sys)
                # Pool agents carry a full MCP+Skills toolkit built for the main agent.
                # Replace with an empty toolkit for simple steps (no tools allowed),
                # or keep as-is for complex steps (full MCP access).
                if step_complexity != "complex":
                    from agentscope.tool import Toolkit as _Toolkit
                    agent.toolkit = _Toolkit()
                _pool_slot = _pooled
                logger.info("[SubAgent] step=%d acquired from pool in %.0fms model=%s",
                            step.step_order, (_time.monotonic() - _acquire_t0) * 1000, model_name)
            except Exception as _pe:
                logger.warning("[SubAgent] step=%d pool acquire failed (%s), falling back to create_agent_executor",
                               step.step_order, _pe)
                _use_pool = False

        if not _use_pool:
            _create_t0 = _time.monotonic()
            # complex 步骤注入全部 MCP；simple 步骤传空列表禁用 MCP
            # skills 在计划模式下始终禁用（传空列表，避免 None 触发全量加载）
            _mcp_ids = None if step_complexity == "complex" else []
            agent, mcp_clients = await create_agent_executor(
                enabled_mcp_ids=_mcp_ids,
                enabled_skill_ids=[],
                enabled_kb_ids=enabled_kb_ids,
                current_user_id=user_id,
                model_name=model_name,
                isolated=False,
                max_iters=_step_max_iters,
                code_exec_enabled=code_exec_enabled,
                plan_mode=True,
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
            step_text = strip_thinking(step_text)
            step_text = strip_final_output_thinking(step_text)
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
            yield {"type": "plan_step_progress", "step_id": step.step_id, "delta": _narrative_for_display or step_text}

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
