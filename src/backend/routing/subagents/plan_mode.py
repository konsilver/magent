"""Plan mode orchestration — Planner → Warmup → SubAgent+QA pipeline.

Phase 1 (generate): Planner runs, memory queried, structured step list produced
                    and persisted. Warmup starts async in background.
Phase 2 (execute):  Await Warmup result (user_goal + global_constraints written
                    to board). Steps executed sequentially by SubAgents; each
                    writes output to context board and generates next step's
                    local constraint. QA validates every step result.
                    Control flow: PASS → next step,
                                  REDO (up to 2×) → retry current step,
                                  REPLAN → Planner re-plans from redo_id,
                                  global_replan > 1 → SSE interrupted, new plan
                                  sent to frontend for user confirmation.
                    Last step: QA verdict stored as plan_suggestion in memory,
                               loop exits regardless of verdict.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy.orm import Session

from core.infra import log_writer
from core.infra.logging import LogContext
from routing.streaming import _UsageTrackingModel

import time as _time

logger = logging.getLogger(__name__)

# ── Control-flow constants ────────────────────────────────────────────────────
_MAX_REDO_PER_STEP = 2      # REDO retries before escalating to REPLAN
_MAX_LOCAL_REPLAN = 2       # local REPLAN count before triggering full global reset

# ── Warmup task registry (plan_id → asyncio.Task) ────────────────────────────
# Warmup starts as soon as Phase 1 produces a plan; Phase 2 awaits it.
_WARMUP_TASKS: Dict[str, asyncio.Task] = {}

# ── Plan store, context board, and shared utility helpers ────────────────────
from routing.subagents.plan_store import (
    _role_model,
    _subagent_model,
    _qa_model,
    _PLAN_STORE, _PLAN_STORE_LOCK,
    _store_plan, _get_stored_plan, _update_stored_plan,
    _update_stored_step, _replace_stored_steps, _make_plan_dict,
    _StepProxy,
    _make_context_board, _context_board_summary,
    _collect_valid_tool_names, _load_visible_agents,
    _prepare_history, _build_file_context, _parse_json_output,
    _extract_summary, _terminate_mcp_processes, _mem0_enabled,
)

# ── Memory helpers ────────────────────────────────────────────────────────────
from routing.subagents.plan_memory import (
    _retrieve_plan_memory,
    _retrieve_step_memory,
    _save_task_memory_background,
    _save_step_memory_background,
    _save_user_profile_background,
)

# ── LLM agent helpers ─────────────────────────────────────────────────────────
from routing.subagents.plan_agents import (
    _call_llm_agent,
    _run_user_profile_agent,
    _classify_user_intent,
    _run_planner,
    _run_warmup,
    _run_qa,
)

# ── Step execution helpers ────────────────────────────────────────────────────
from routing.subagents.plan_executor import (
    _build_subagent_instruction,
    _run_subagent_step,
    _extract_next_step_instruction,
    _strip_thinking_preamble,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Generate Plan  (UserProfile + Planner run in parallel)
# ═══════════════════════════════════════════════════════════════════════════════

async def astream_generate_plan(
    task_description: str,
    user_id: str,
    db: Session,
    model_name: str = "qwen",
    enabled_mcp_ids: Optional[List[str]] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_kb_ids: Optional[List[str]] = None,
    enabled_agent_ids: Optional[List[str]] = None,
    session_messages: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
    previous_plan_id: Optional[str] = None,
    user_reply: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Phase 1: UserProfile + Planner in parallel, then persist plan.

    When previous_plan_id and user_reply are provided:
    - Classify user intent: 'confirm' → caller should execute; 'replan' → save rejected
      plan to memory then re-generate plan with user suggestions.

    Yields SSE events:
    - plan_intent      {intent: 'confirm'|'replan'}  (only when user_reply provided)
    - plan_generating  {delta: str}
    - plan_generated   {plan_id, title, description, steps: [...]}
    - plan_error       {error: str}
    """
    # ── Intent classification (when responding to a shown plan) ──────────────
    if user_reply and previous_plan_id:
        intent = await _classify_user_intent(user_reply, _role_model("intent", model_name), user_id)
        yield {"type": "plan_intent", "intent": intent, "plan_id": previous_plan_id}

        if intent == "confirm":
            yield {"type": "plan_confirm", "plan_id": previous_plan_id}
            return

        # intent == "replan": cancel pending warmup for the rejected plan, save to memory
        _prev_warmup = _WARMUP_TASKS.pop(previous_plan_id, None)
        if _prev_warmup and not _prev_warmup.done():
            _prev_warmup.cancel()
            logger.info("[Phase1] Warmup task cancelled for rejected plan_id=%s", previous_plan_id)

        prev_plan = _get_stored_plan(previous_plan_id)
        if prev_plan:
            _save_task_memory_background(
                user_id=user_id,
                user_goal=prev_plan.get("extra_data", {}).get("user_goal", prev_plan.get("title", "")),
                plan_steps=[s.get("title", "") for s in prev_plan.get("steps", [])],
                success=False,
                quality_score=0.0,
                failure_reason="",
                final_solution_summary="",
                forced=False,
                key_constraints=[],
                plan_id=previous_plan_id,
                step_details=[{"step_id": s["step_id"], "title": s.get("title", "")} for s in prev_plan.get("steps", [])],
                plan_suggestion=f"用户拒绝该方案并给出建议：{user_reply[:200]}",
            )
        yield {"type": "plan_generating", "delta": "已记录您的建议，正在重新制定计划...\n"}

    visible_agents = _load_visible_agents(db, user_id, enabled_agent_ids)

    board = _make_context_board()

    if not (user_reply and previous_plan_id):
        yield {"type": "plan_generating", "delta": "正在分析用户特征并查询历史记忆...\n"}

    logger.info("[Phase1] user=%s: launching UserProfileAgent + retrieve_plan_memory + Planner in parallel", user_id)
    _phase1_t0 = _time.monotonic()

    # Planner、UserProfile、memory 三者并发启动
    # planner 不依赖 user_profile 结果，board 中 user 字段可能为空（planner 有 fallback）
    # 三个任务并发后，等待 memory + user_profile 完成（planner 可能先完成也可能后完成）
    user_profile_task = asyncio.create_task(
        _run_user_profile_agent(user_id, task_description, _role_model("user_profile", model_name), board)
    )
    memory_task = asyncio.create_task(
        _retrieve_plan_memory(user_id, task_description)
    )

    # 等待 memory 完成（planner 需要 retrieved_memory），同时发送心跳保持 SSE 连接
    while not memory_task.done():
        yield {"type": "heartbeat"}
        await asyncio.sleep(0.5)

    try:
        retrieved_memory = await memory_task
    except Exception as _mem_exc:
        logger.warning("[Phase1] memory retrieval failed (non-critical): %s", _mem_exc)
        retrieved_memory = {}

    logger.info("[Phase1] memory done in %.0fms: similar_tasks=%d graph_plans=%d",
                (_time.monotonic() - _phase1_t0) * 1000,
                len(retrieved_memory.get("similar_tasks", [])),
                len(retrieved_memory.get("graph_plans", [])))

    yield {"type": "plan_generating", "delta": "正在制定执行计划...\n"}
    logger.info("[Phase1] calling Planner for user=%s", user_id)

    try:
        # 启动 planner（与 user_profile_task 并发运行）
        planner_task = asyncio.create_task(
            _run_planner(
                user_input=task_description,
                user_id=user_id,
                model_name=_role_model("plan", model_name),
                retrieved_memory=retrieved_memory,
                board=board,
                session_messages=session_messages,
            )
        )

        # 等待 planner 完成，期间发送心跳
        while not planner_task.done():
            yield {"type": "heartbeat"}
            await asyncio.sleep(0.5)

        plan_data = await planner_task

        # planner 完成后，等待 user_profile 最多7秒（非关键路径，失败不影响主流程）
        if not user_profile_task.done():
            logger.info("[Phase1] Planner done, waiting up to 7s for UserProfileAgent...")
            try:
                await asyncio.wait_for(asyncio.shield(user_profile_task), timeout=7.0)
            except asyncio.TimeoutError:
                logger.warning("[Phase1] UserProfileAgent did not finish in 7s, proceeding without user profile")
            except Exception as _up_exc:
                logger.warning("[Phase1] UserProfileAgent failed (non-critical): %s", _up_exc)
        else:
            try:
                await user_profile_task
            except Exception as _up_exc:
                logger.warning("[Phase1] UserProfileAgent failed (non-critical): %s", _up_exc)

        logger.info("[Phase1] Phase1 total %.0fms", (_time.monotonic() - _phase1_t0) * 1000)

        if not plan_data:
            yield {"type": "plan_error", "error": "Planner 输出格式解析失败，请重试"}
            return

        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
        _valid_agents = {a.get("agent_id") for a in visible_agents}
        for step_data in plan_data.get("steps", []):
            if _valid_tools is not None:
                step_data["expected_tools"] = [
                    t for t in (step_data.get("expected_tools") or [])
                    if t in _valid_tools
                ]
            if _valid_skills is not None:
                step_data["expected_skills"] = [
                    s for s in (step_data.get("expected_skills") or [])
                    if s in _valid_skills
                ]
            step_data["expected_agents"] = [
                a for a in (step_data.get("expected_agents") or [])
                if a in _valid_agents
            ]

        plan_id = f"plan_{uuid.uuid4().hex[:16]}"
        agent_name_map = {a.get("agent_id"): a.get("name", a.get("agent_id", "")) for a in visible_agents} if visible_agents else {}

        extra: Dict[str, Any] = {
            "user_goal": plan_data.get("user_goal", task_description),
            "retrieved_memory": retrieved_memory,
            "user_profile": board.get("user", {}),
        }
        if uploaded_files:
            extra["uploaded_files"] = uploaded_files
        if agent_name_map:
            extra["agent_name_map"] = agent_name_map

        plan_title = plan_data.get("user_goal") or plan_data.get("title") or "未命名计划"
        # Use brief_description as step title if no explicit title field
        steps_with_title = [
            {**s, "title": s.get("title") or s.get("brief_description") or f"步骤{i+1}"}
            for i, s in enumerate(plan_data.get("steps", []))
        ]
        plan_dict = _make_plan_dict(
            plan_id=plan_id,
            user_id=user_id,
            title=plan_title,
            description=plan_data.get("description", ""),
            task_input=task_description,
            steps=steps_with_title,
            extra_data=extra,
        )
        _store_plan(plan_dict)

        event: Dict[str, Any] = {
            "type": "plan_generated",
            "plan_id": plan_id,
            "title": plan_dict["title"],
            "description": plan_dict["description"],
            "task_input": plan_dict["task_input"],
            "status": plan_dict["status"],
            "total_steps": plan_dict["total_steps"],
            "completed_steps": 0,
            "result_summary": None,
            "steps": [
                {
                    "step_id": s["step_id"],
                    "step_order": s["step_order"],
                    "title": s["title"],
                    "brief_description": s.get("brief_description", ""),
                    "description": s["description"],
                    "expected_tools": s["expected_tools"],
                    "expected_skills": s["expected_skills"],
                    "expected_agents": s["expected_agents"],
                    "status": s["status"],
                    "result_summary": None,
                }
                for s in plan_dict["steps"]
            ],
        }
        if agent_name_map:
            event["agent_name_map"] = agent_name_map
        yield event

        # ── 异步启动 Warmup，与用户确认环节并行执行 ──────────────────────────
        # board 此时已含 user + plan.steps，retrieved_memory 含相似任务记忆
        _warmup_memory = {
            "similar_tasks": retrieved_memory.get("similar_tasks", []),
            "graph_plans": retrieved_memory.get("graph_plans", []),
        }
        _warmup_task = asyncio.create_task(
            _run_warmup(
                user_input=task_description,
                user_id=user_id,
                model_name=_role_model("warmup", model_name),
                retrieved_memory=_warmup_memory,
                board=board,
                session_messages=session_messages,
            )
        )
        _WARMUP_TASKS[plan_id] = _warmup_task
        logger.info("[Phase1] Warmup task started in background for plan_id=%s", plan_id)

    except Exception as exc:
        logger.exception("Plan generation failed")
        yield {"type": "plan_error", "error": f"计划生成失败: {exc}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Execute Plan  (Warmup → SubAgent + QA loop)
# ═══════════════════════════════════════════════════════════════════════════════

async def astream_execute_plan(
    plan_id: str,
    user_id: str,
    db: Session,
    model_name: str = "qwen",
    enabled_mcp_ids: Optional[List[str]] = None,
    enabled_skill_ids: Optional[List[str]] = None,
    enabled_kb_ids: Optional[List[str]] = None,
    enabled_agent_ids: Optional[List[str]] = None,
    session_messages: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Phase 2: Warmup → SubAgent+QA pipeline.

    Yields SSE events:
    - plan_step_start     {step_id, step_order, title}
    - plan_step_progress  {step_id, delta: str}
    - tool_call / tool_result  {step_id, ...}
    - plan_step_qa        {step_id, verdict}
    - plan_step_complete  {step_id, status, summary}
    - plan_error          {plan_id, step_id?, error}
    - plan_complete       {plan_id, status, summary, ...}
    """
    logger.warning("[plan-exec] astream_execute_plan called for plan_id=%s", plan_id)

    plan_dict = _get_stored_plan(plan_id)
    if not plan_dict or plan_dict.get("user_id") != user_id:
        yield {"type": "plan_error", "plan_id": plan_id, "error": "计划不存在"}
        return

    if plan_dict["status"] not in ("approved", "running"):
        yield {"type": "plan_error", "plan_id": plan_id, "error": f"计划状态 '{plan_dict['status']}' 不可执行"}
        return

    plan_meta = plan_dict.get("extra_data") or {}
    if not uploaded_files:
        uploaded_files = plan_meta.get("uploaded_files")

    user_goal = plan_meta.get("user_goal", plan_dict["task_input"])
    plan_retrieved_memory = plan_meta.get("retrieved_memory", {})
    saved_user_profile = plan_meta.get("user_profile", {})

    _update_stored_plan(plan_id, status="running")
    completed_count = 0
    cancelled = False

    _chat_hint = plan_meta.get("chat_id")
    _log_ctx = LogContext(user_id=user_id or None, chat_id=_chat_hint)
    _log_ctx.__enter__()

    _plan_run_start = _time.monotonic()
    _plan_subagent_log_id = await log_writer.start_subagent_log({
        "subagent_name": "plan_mode",
        "subagent_type": "plan_mode",
        "subagent_id": plan_id,
        "plan_id": plan_id,
        "model": model_name,
        "step_title": plan_dict["title"],
        "input_messages": {
            "task_input": plan_dict["task_input"],
            "total_steps": plan_dict["total_steps"],
        },
    })
    _plan_tool_count = 0
    _all_mcp_clients: List = []
    _cumulative_usage = _UsageTrackingModel(None)

    prepared_history = await _prepare_history(session_messages or [], model_name)

    # ── Build fresh context board ─────────────────────────────────────────────
    board = _make_context_board()

    if saved_user_profile:
        board["user"].update(saved_user_profile)

    board["plan"]["user_goal"] = user_goal
    board["plan"]["steps"] = [
        {
            "step_id": s["step_id"],
            "brief_description": s.get("brief_description", ""),
            "description": s.get("description") or s.get("title", ""),
            "output": None,
            "suggestion": None,
            "tool_use_trace": [],
        }
        for s in plan_dict["steps"]
    ]

    # ── Warmup Phase ──────────────────────────────────────────────────────────
    # Warmup was started in Phase 1 right after plan_generated; wait for it here.
    steps = [_StepProxy(s) for s in plan_dict["steps"]]

    async def _empty_memory() -> Dict:
        return {"relevant_patterns": []}

    _step0_desc = steps[0].description or steps[0].title if steps else ""
    _warmup_task = _WARMUP_TASKS.pop(plan_id, None)

    if _warmup_task is not None:
        logger.info("[Phase2] plan_id=%s: waiting for background Warmup task", plan_id)
        yield {"type": "plan_step_progress", "step_id": None, "delta": "等待 Warmup Agent 完成初始化...\n"}
        # 并行等待 warmup 完成和第一步记忆预取
        try:
            warmup_result, _prefetched_step0_memory = await asyncio.gather(
                asyncio.wait_for(_warmup_task, timeout=90),
                _retrieve_step_memory(user_id, _step0_desc) if _step0_desc else _empty_memory(),
            )
        except asyncio.TimeoutError:
            logger.warning("[Phase2] Warmup task timed out, proceeding with fallback")
            warmup_result = None
            _prefetched_step0_memory = await (_retrieve_step_memory(user_id, _step0_desc) if _step0_desc else _empty_memory())
        except asyncio.CancelledError:
            logger.warning("[Phase2] Warmup task was cancelled, proceeding with fallback")
            warmup_result = None
            _prefetched_step0_memory = await (_retrieve_step_memory(user_id, _step0_desc) if _step0_desc else _empty_memory())
        except Exception as _we:
            logger.warning("[Phase2] Warmup task failed: %s", _we)
            warmup_result = None
            _prefetched_step0_memory = await (_retrieve_step_memory(user_id, _step0_desc) if _step0_desc else _empty_memory())
    else:
        # Fallback：Phase 1 未产生 warmup task（极少数异常路径），同步执行
        logger.warning("[Phase2] No background Warmup task found for plan_id=%s, running synchronously", plan_id)
        yield {"type": "plan_step_progress", "step_id": None, "delta": "Warmup Agent 正在初始化执行语义空间...\n"}
        _warmup_memory = {
            "similar_tasks": plan_retrieved_memory.get("similar_tasks", []),
            "graph_plans": plan_retrieved_memory.get("graph_plans", []),
        }
        warmup_result, _prefetched_step0_memory = await asyncio.gather(
            _run_warmup(
                user_input=plan_dict["task_input"],
                user_id=user_id,
                model_name=_role_model("warmup", model_name),
                retrieved_memory=_warmup_memory,
                board=board,
                session_messages=session_messages,
            ),
            _retrieve_step_memory(user_id, _step0_desc) if _step0_desc else _empty_memory(),
        )

    if warmup_result is None:
        warmup_result = {
            "user_goal": user_goal,
            "global_constraints": [],
            "next_step_instruction": {"local_constraint": {}, "expected_output_schema": {}},
        }
        board["plan"]["user_goal"] = user_goal
    else:
        # warmup 写入的是 Phase 1 的 board；Phase 2 使用独立 board，需同步写入
        board["plan"]["user_goal"] = warmup_result.get("user_goal") or user_goal
        board["check"]["global_constraints"] = warmup_result.get("global_constraints", [])

    first_instr = warmup_result.get("next_step_instruction") or {}
    current_local_constraint = first_instr.get("local_constraint", {})
    current_expected_schema = first_instr.get("expected_output_schema", {})

    # ── Step execution state ──────────────────────────────────────────────────
    step_summaries: List[str] = []
    last_step_text: str = ""

    local_replan_count = 0
    global_reset_count = 0

    # 下一步记忆预取任务（在当前步执行期间异步跑）
    _prefetch_memory_task: Optional[asyncio.Task] = None

    try:
        step_idx = 0

        while step_idx < len(steps):
            step = steps[step_idx]

            logger.warning("[plan-exec] === Step %d/%d: %s ===",
                           step_idx + 1, len(steps), step.title)

            if cancelled:
                _update_stored_step(plan_id, step.step_id, status="skipped")
                step_idx += 1
                continue

            current_status = (_get_stored_plan(plan_id) or {}).get("status", "running")
            if current_status == "cancelled":
                cancelled = True
                _update_stored_step(plan_id, step.step_id, status="skipped")
                step_idx += 1
                continue

            yield {
                "type": "plan_step_start",
                "step_id": step.step_id,
                "step_order": step.step_order,
                "title": step.title,
            }
            yield {"type": "heartbeat"}
            _update_stored_step(plan_id, step.step_id, status="running", started_at=datetime.utcnow().isoformat())

            # ── 消费预取的记忆（step 0 来自 Warmup 并行，其余来自上一步预取任务）────
            if step_idx == 0:
                step_memory = _prefetched_step0_memory
            elif _prefetch_memory_task is not None:
                step_memory = await _prefetch_memory_task
                _prefetch_memory_task = None
            else:
                step_memory = await _retrieve_step_memory(user_id, step.description or step.title)

            yield {
                "type": "plan_step_agent_activity",
                "step_id": step.step_id,
                "activity": "memory_query",
                "label": "SubAgent 查询历史经验",
            }

            next_step = steps[step_idx + 1] if step_idx + 1 < len(steps) else None

            # ── 预取下一步记忆（与当前 SubAgent 执行并行）──────────────────────────
            if next_step is not None and _prefetch_memory_task is None:
                _next_desc = next_step.description or next_step.title
                _prefetch_memory_task = asyncio.ensure_future(
                    _retrieve_step_memory(user_id, _next_desc)
                )

            # ── REDO loop ──────────────────────────────────────────────────────
            redo_count = 0
            step_text = ""
            step_tool_calls: List[Dict] = []
            qa_verdict = "PASS"
            qa_data: Dict = {}

            while True:
                step_text = ""
                step_tool_calls = []

                yield {
                    "type": "plan_step_agent_activity",
                    "step_id": step.step_id,
                    "activity": "subagent_executing",
                    "label": "SubAgent 执行任务" if redo_count == 0 else f"SubAgent 重做任务（第 {redo_count} 次）",
                }
                _step_complexity = getattr(step, "complexity", "complex")
                logger.info("[complexity] step=%s(%r) complexity=%s subagent_model=%s qa_model=%s",
                            step.step_id, getattr(step, "title", ""), _step_complexity,
                            _subagent_model(_step_complexity, model_name),
                            _qa_model(_step_complexity, model_name))
                async for event in _run_subagent_step(
                    step=step,
                    next_step=next_step,
                    board=board,
                    local_constraint=current_local_constraint,
                    expected_schema=current_expected_schema,
                    retrieved_memory=step_memory,
                    prepared_history=prepared_history,
                    uploaded_files=uploaded_files,
                    model_name=_subagent_model(_step_complexity, model_name),
                    user_id=user_id,
                    enabled_kb_ids=enabled_kb_ids,
                    _cumulative_usage=_cumulative_usage,
                    _plan_subagent_log_id=_plan_subagent_log_id,
                    _all_mcp_clients=_all_mcp_clients,
                    code_exec_enabled=(_step_complexity == "complex"),
                ):
                    if event["type"] == "_step_result":
                        step_text = event["step_text"]
                        step_tool_calls = event["step_tool_calls"]
                    else:
                        yield event

                _plan_tool_count += len(step_tool_calls)

                narrative_text, subagent_json = _extract_next_step_instruction(step_text)

                yield {
                    "type": "plan_step_agent_activity",
                    "step_id": step.step_id,
                    "activity": "qa_checking",
                    "label": "QA 进行检查",
                }
                _is_last_step = (step_idx == len(steps) - 1)
                qa_data = await _run_qa(
                    step=step,
                    result=narrative_text or step_text,
                    board=board,
                    local_constraint=current_local_constraint,
                    expected_schema=current_expected_schema,
                    model_name=_qa_model(_step_complexity, model_name),
                    user_id=user_id,
                    is_last_step=_is_last_step,
                )
                qa_verdict = qa_data.get("verdict", "PASS")

                yield {
                    "type": "plan_step_qa",
                    "step_id": step.step_id,
                    "verdict": qa_verdict,
                }

                # 最后一步：REDO 最多两次，REPLAN 或超次数后直接接受结果
                if _is_last_step:
                    _final_plan_suggestion = qa_data.get("plan_suggestion", "")
                    if _final_plan_suggestion:
                        board["plan"]["plan_suggestion"] = _final_plan_suggestion
                    if qa_verdict == "PASS":
                        board["plan"]["suggestion"] = None
                        break
                    if qa_verdict == "REDO" and redo_count < _MAX_REDO_PER_STEP:
                        redo_count += 1
                        logger.warning("[QA] last-step REDO %d/%d", redo_count, _MAX_REDO_PER_STEP)
                        board["plan"]["suggestion"] = qa_data.get("suggestion") or "（无建议）"
                        yield {"type": "plan_step_progress", "step_id": step.step_id,
                               "delta": f"\nQA 验证未通过，正在重试最后一步 ({redo_count}/{_MAX_REDO_PER_STEP})...\n"}
                        continue
                    # REPLAN 或 REDO 超次数：直接接受当前结果，不触发重规划
                    logger.warning("[QA] last-step verdict=%s — accepting result directly", qa_verdict)
                    board["plan"]["suggestion"] = None
                    break

                if qa_verdict == "PASS":
                    board["plan"]["suggestion"] = None
                    break

                if qa_verdict == "REDO":
                    redo_count += 1
                    logger.warning("[QA] REDO step=%d redo=%d", step.step_order, redo_count)
                    # 写入 board["plan"]["suggestion"]，供 subagent 重做时读取
                    board["plan"]["suggestion"] = qa_data.get("suggestion") or "（无建议）"
                    if redo_count >= _MAX_REDO_PER_STEP:
                        qa_verdict = "REPLAN"
                        logger.warning("[QA] REDO limit reached → escalate to REPLAN")
                        board["plan"]["redo_id"] = step.step_order
                        break
                    yield {"type": "plan_step_progress", "step_id": step.step_id,
                           "delta": f"\nQA 验证未通过，正在重试 ({redo_count}/{_MAX_REDO_PER_STEP})...\n"}
                    continue

                if qa_verdict == "REPLAN":
                    break

                break

            # ── Handle REPLAN ─────────────────────────────────────────────────
            if qa_verdict == "REPLAN":
                local_replan_count += 1
                logger.warning("[plan-exec] REPLAN triggered at step %d (local_count=%d, global_reset=%d)",
                               step.step_order, local_replan_count, global_reset_count)

                # 确保 board 中记录了 redo_id 和 suggestion（QA 直接判断 REPLAN 时在此写入）
                if board["plan"].get("redo_id", -1) == -1:
                    board["plan"]["redo_id"] = step.step_order
                    board["plan"]["suggestion"] = qa_data.get("suggestion") or "（无建议）"

                yield {
                    "type": "plan_step_agent_activity",
                    "step_id": step.step_id,
                    "activity": "planner_replanning",
                    "label": "Planner 重新规划中...",
                }

                if local_replan_count >= _MAX_LOCAL_REPLAN:
                    global_reset_count += 1
                    logger.warning("[plan-exec] Global reset #%d triggered at step %d",
                                   global_reset_count, step.step_order)

                    failure_summary = qa_data.get("suggestion") or "执行方案无法达到预期效果"

                    _failed_plan_suggestion = f"该方案在第{step.step_order}步触发全局重置，原因：{failure_summary}"
                    _save_task_memory_background(
                        user_id=user_id,
                        user_goal=board["plan"].get("user_goal", ""),
                        plan_steps=[s.title for s in steps],
                        success=False,
                        quality_score=0.0,
                        failure_reason=_failed_plan_suggestion,
                        final_solution_summary="",
                        forced=False,
                        key_constraints=[
                            f"{c.get('target', '')}: {c.get('rule', '')}"
                            for c in board["check"].get("global_constraints", [])
                        ],
                        plan_id=plan_id,
                        step_details=[{"step_id": s.step_id, "title": s.title} for s in steps],
                    )

                    # 通知前端发生了全局重置
                    yield {
                        "type": "plan_global_reset_notify",
                        "plan_id": plan_id,
                        "failure_reason": failure_summary,
                        "reset_count": global_reset_count,
                    }

                    # 重新进入准备阶段：user_profile + planner 并发执行，生成新计划
                    _reset_board = _make_context_board()
                    replan_memory, _ = await asyncio.gather(
                        _retrieve_plan_memory(user_id, plan_dict["task_input"]),
                        _run_user_profile_agent(
                            user_id=user_id,
                            user_input=plan_dict["task_input"],
                            model_name=_role_model("user_profile", model_name),
                            board=_reset_board,
                        ),
                    )
                    new_plan_data = await _run_planner(
                        user_input=plan_dict["task_input"],
                        user_id=user_id,
                        model_name=_role_model("plan", model_name),
                        retrieved_memory=replan_memory,
                        board=_reset_board,
                        session_messages=session_messages,
                    )

                    new_steps = []
                    if new_plan_data and new_plan_data.get("steps"):
                        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
                        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
                        _valid_agents = {a.get("agent_id") for a in _load_visible_agents(db, user_id, enabled_agent_ids)}
                        for sd in new_plan_data["steps"]:
                            if _valid_tools is not None:
                                sd["expected_tools"] = [t for t in (sd.get("expected_tools") or []) if t in _valid_tools]
                            if _valid_skills is not None:
                                sd["expected_skills"] = [s for s in (sd.get("expected_skills") or []) if s in _valid_skills]
                            sd["expected_agents"] = [a for a in (sd.get("expected_agents") or []) if a in _valid_agents]
                        new_steps = new_plan_data["steps"]

                    # 存储新计划，等待用户确认后再执行
                    _new_plan_id = f"plan_{uuid.uuid4().hex[:16]}"
                    _new_agent_name_map = plan_meta.get("agent_name_map", {})
                    _new_extra: Dict[str, Any] = {
                        "user_goal": new_plan_data.get("user_goal", plan_dict["task_input"]) if new_plan_data else plan_dict["task_input"],
                        "retrieved_memory": replan_memory,
                        "user_profile": _reset_board.get("user", {}),
                    }
                    if uploaded_files:
                        _new_extra["uploaded_files"] = uploaded_files
                    if _new_agent_name_map:
                        _new_extra["agent_name_map"] = _new_agent_name_map
                    _new_plan_dict = _make_plan_dict(
                        plan_id=_new_plan_id,
                        user_id=user_id,
                        title=new_plan_data.get("title", "重规划方案") if new_plan_data else "重规划方案",
                        description=new_plan_data.get("description", "") if new_plan_data else "",
                        task_input=plan_dict["task_input"],
                        steps=new_steps,
                        extra_data=_new_extra,
                    )
                    _store_plan(_new_plan_dict)

                    # 发送 plan_generated 事件，让前端展示新计划等待用户确认
                    _new_plan_event: Dict[str, Any] = {
                        "type": "plan_generated",
                        "plan_id": _new_plan_id,
                        "title": _new_plan_dict["title"],
                        "description": _new_plan_dict["description"],
                        "task_input": _new_plan_dict["task_input"],
                        "status": _new_plan_dict["status"],
                        "total_steps": _new_plan_dict["total_steps"],
                        "completed_steps": 0,
                        "result_summary": None,
                        "steps": [
                            {
                                "step_id": s["step_id"],
                                "step_order": s["step_order"],
                                "title": s["title"],
                                "brief_description": s.get("brief_description", ""),
                                "description": s["description"],
                                "expected_tools": s["expected_tools"],
                                "expected_skills": s["expected_skills"],
                                "expected_agents": s["expected_agents"],
                                "status": s["status"],
                                "result_summary": None,
                            }
                            for s in _new_plan_dict["steps"]
                        ],
                    }
                    if _new_agent_name_map:
                        _new_plan_event["agent_name_map"] = _new_agent_name_map
                    yield _new_plan_event
                    return
                else:
                    yield {"type": "plan_step_progress", "step_id": step.step_id,
                           "delta": f"\nQA 触发局部重新规划（从当前步骤重做）...\n"}

                    # 局部 REPLAN 复用 Phase 1 检索到的记忆，避免重复 Milvus 查询
                    replan_memory = plan_retrieved_memory or await _retrieve_plan_memory(user_id, plan_dict["task_input"])
                    # board["plan"]["redo_id"] 和 "suggestion" 已在进入 REPLAN 处理块时写入
                    new_plan_data = await _run_planner(
                        user_input=plan_dict["task_input"],
                        user_id=user_id,
                        model_name=_role_model("plan", model_name),
                        retrieved_memory=replan_memory,
                        board=board,
                        session_messages=session_messages,
                    )

                    if new_plan_data and new_plan_data.get("steps"):
                        remaining_steps = new_plan_data["steps"]
                        _valid_tools = _collect_valid_tool_names(enabled_mcp_ids)
                        _valid_skills = set(enabled_skill_ids) if enabled_skill_ids is not None else None
                        _valid_agents = {a.get("agent_id") for a in _load_visible_agents(db, user_id, enabled_agent_ids)}
                        for sd in remaining_steps:
                            if _valid_tools is not None:
                                sd["expected_tools"] = [t for t in (sd.get("expected_tools") or []) if t in _valid_tools]
                            if _valid_skills is not None:
                                sd["expected_skills"] = [s for s in (sd.get("expected_skills") or []) if s in _valid_skills]
                            sd["expected_agents"] = [a for a in (sd.get("expected_agents") or []) if a in _valid_agents]

                        completed_steps_so_far = steps[:step_idx]
                        all_new_steps = [
                            {"title": s.get("title") or s.get("brief_description") or f"步骤{i+1}",
                             "brief_description": s.get("brief_description", ""),
                             "description": s.get("description", ""),
                             "complexity": s.get("complexity", "complex"),
                             "expected_tools": s.get("expected_tools", []),
                             "expected_skills": s.get("expected_skills", []),
                             "expected_agents": s.get("expected_agents", [])}
                            for i, s in enumerate(remaining_steps)
                        ]
                        merged_steps = [
                            {"title": s.title, "brief_description": s._d.get("brief_description", ""),
                             "description": s._d.get("description", ""),
                             "complexity": s._d.get("complexity", "complex"),
                             "expected_tools": s._d.get("expected_tools") or [],
                             "expected_skills": s._d.get("expected_skills") or [],
                             "expected_agents": s._d.get("expected_agents") or []}
                            for s in completed_steps_so_far
                        ] + all_new_steps
                        updated = _replace_stored_steps(plan_id, merged_steps)
                        if updated:
                            # 已完成步骤（step_idx之前）的board数据按step_id保留
                            old_board_by_order = {bs["step_id"]: bs for bs in board["plan"]["steps"]}
                            steps = [_StepProxy(s) for s in updated["steps"]]
                            new_board_steps = []
                            for i, s in enumerate(updated["steps"]):
                                if i < step_idx:
                                    # 已完成步骤：用旧board数据（按step_id匹配不到则按索引回退）
                                    old_bs = old_board_by_order.get(s["step_id"])
                                    if old_bs is None and i < len(board["plan"]["steps"]):
                                        old_bs = board["plan"]["steps"][i]
                                    new_board_steps.append({
                                        "step_id": s["step_id"],
                                        "brief_description": s.get("brief_description", ""),
                                        "description": s.get("description") or s.get("title", ""),
                                        "output": old_bs["output"] if old_bs else None,
                                        "suggestion": old_bs.get("suggestion") if old_bs else None,
                                        "tool_use_trace": old_bs.get("tool_use_trace", []) if old_bs else [],
                                    })
                                else:
                                    # 新规划步骤：初始化为空
                                    new_board_steps.append({
                                        "step_id": s["step_id"],
                                        "brief_description": s.get("brief_description", ""),
                                        "description": s.get("description") or s.get("title", ""),
                                        "output": None,
                                        "suggestion": None,
                                        "tool_use_trace": [],
                                    })
                            board["plan"]["steps"] = new_board_steps
                            # REPLAN 重建了步骤列表，旧预取任务已过期，取消并清空
                            if _prefetch_memory_task is not None and not _prefetch_memory_task.done():
                                _prefetch_memory_task.cancel()
                            _prefetch_memory_task = None
                            _update_stored_step(plan_id, steps[step_idx].step_id, status="running", started_at=datetime.utcnow().isoformat())
                            _replan_reason = qa_data.get("suggestion") or ""
                            yield {
                                "type": "plan_replan",
                                "plan_id": plan_id,
                                "replaced_from_order": step.step_order,
                                "reason": _replan_reason or "为了保证执行成功率，已自动优化后续步骤",
                                "new_steps": [
                                    {
                                        "step_id": s["step_id"],
                                        "step_order": s["step_order"],
                                        "title": s.get("title", ""),
                                        "brief_description": s.get("brief_description", ""),
                                    }
                                    for s in updated["steps"][step_idx:]
                                ],
                            }
                            continue

            # ── Finalize step ─────────────────────────────────────────────────
            narrative_text, subagent_json = _extract_next_step_instruction(step_text)
            display_text = _strip_thinking_preamble(narrative_text if narrative_text else step_text)

            step_result_summary = subagent_json.get("result", "") if subagent_json else ""
            for board_step in board["plan"]["steps"]:
                if board_step["step_id"] == step.step_id:
                    board_step["output"] = step_result_summary or _extract_summary(display_text)
                    board_step["tool_use_trace"] = [
                        tc.get("name") or tc.get("tool_name") or tc.get("function", {}).get("name", "")
                        for tc in (step_tool_calls or [])
                        if isinstance(tc, dict)
                    ]
                    board_step["_qa_passed"] = (qa_verdict == "PASS")
                    board_step["_had_redo"] = (redo_count > 0)
                    board_step["_qa_suggestion"] = qa_data.get("suggestion", "") if qa_verdict != "PASS" else ""
                    board_step["_step_description"] = step.description or step.title
                    board_step["_local_constraint"] = current_local_constraint
                    break

            if display_text:
                last_step_text = display_text

            summary = _extract_summary(display_text, max_len=200)
            step_summaries.append(f"步骤{step.step_order}({step.title}): {summary}")

            next_instr = subagent_json.get("next_step_instruction") if subagent_json else None
            if next_instr and isinstance(next_instr, dict):
                current_local_constraint = next_instr.get("local_constraint", {})
                current_expected_schema = next_instr.get("expected_output_schema", {})
            else:
                current_local_constraint = {}
                current_expected_schema = {}

            _final_step_status = (
                "success"
                if not step_text.startswith("执行出错") and not step_text.startswith("步骤初始化失败")
                else "failed"
            )

            _update_stored_step(
                plan_id,
                step.step_id,
                status=_final_step_status,
                result_summary=summary,
                ai_output=display_text[:5000],
                tool_calls_log=step_tool_calls,
                completed_at=datetime.utcnow().isoformat(),
            )

            if _final_step_status == "success":
                completed_count += 1

            yield {
                "type": "plan_step_complete",
                "step_id": step.step_id,
                "status": _final_step_status,
                "summary": summary,
            }

            if _final_step_status == "failed":
                yield {
                    "type": "plan_error",
                    "plan_id": plan_id,
                    "step_id": step.step_id,
                    "error": display_text,
                }

            step_idx += 1

        # ── Plan complete ─────────────────────────────────────────────────────
        logger.warning("[plan-exec] === All steps done. completed=%d/%d ===",
                       completed_count, len(steps))

        final_status = "completed" if completed_count == len(steps) else "failed"
        if cancelled:
            final_status = "cancelled"

        overall_summary = f"共 {len(steps)} 个步骤，完成 {completed_count} 个"
        task_success = final_status == "completed"

        # 最后一步 subagent 本身即为总结性 agent，其输出直接作为最终结果
        # plan_suggestion 已在最后一步 QA 时写入 board["plan"]["plan_suggestion"]
        _final_plan_suggestion = board["plan"].get("plan_suggestion", "")
        result_text = last_step_text

        _update_stored_plan(
            plan_id,
            status=final_status,
            completed_steps=completed_count,
            result_summary=result_text[:2000] if result_text else overall_summary,
        )

        records = _cumulative_usage.usage_records
        total_prompt = sum(r.get("prompt_tokens", 0) for r in records)
        total_completion = sum(r.get("completion_tokens", 0) for r in records)
        logger.info(
            "[plan-exec] USAGE SUMMARY plan_id=%s status=%s steps=%d llm_calls=%d "
            "prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            plan_id, final_status, len(steps), len(records),
            total_prompt, total_completion, total_prompt + total_completion,
        )
        exec_usage = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "llm_call_count": len(records),
        }

        yield {
            "type": "plan_complete",
            "plan_id": plan_id,
            "status": final_status,
            "summary": overall_summary,
            "result_text": result_text,
            "completed_steps": completed_count,
            "total_steps": plan_dict["total_steps"],
            "usage": exec_usage,
            "plan_suggestion": _final_plan_suggestion,
        }

        await log_writer.finish_subagent_log(
            _plan_subagent_log_id,
            status="success" if final_status == "completed" else final_status,
            output_content=result_text or overall_summary,
            intermediate_steps=step_summaries,
            token_usage=exec_usage,
            tool_calls_count=_plan_tool_count,
            duration_ms=int((_time.monotonic() - _plan_run_start) * 1000),
        )

        # ── 后台异步：记忆写入，不阻塞用户响应 ──────────────────────────────────
        async def _background_finalize() -> None:
            _save_task_memory_background(
                user_id=user_id,
                user_goal=board["plan"].get("user_goal", ""),
                plan_steps=[s.title for s in steps],
                success=bool(task_success),
                quality_score=1.0 if task_success else 0.5,
                failure_reason="",
                final_solution_summary=last_step_text[:500],
                forced=False,
                key_constraints=[
                    f"{c.get('target', '')}: {c.get('rule', '')}"
                    for c in board["check"].get("global_constraints", [])
                ],
                plan_id=plan_id,
                step_details=[{"step_id": s.step_id, "title": s.title} for s in steps],
                plan_suggestion=_final_plan_suggestion,
                model_name=model_name,
            )

            for board_step in board["plan"]["steps"]:
                if not board_step.get("_qa_passed"):
                    continue
                _save_step_memory_background(
                    user_id=user_id,
                    step_description=board_step.get("_step_description") or board_step.get("description", ""),
                    tool_use_trace=board_step.get("tool_use_trace", []),
                    local_constraint=board_step.get("_local_constraint", {}),
                    had_redo=board_step.get("_had_redo", False),
                    qa_suggestion=board_step.get("_qa_suggestion", ""),
                    model_name=_role_model("qa", model_name),
                )

        asyncio.create_task(_background_finalize())

    except Exception as exc:
        logger.exception("Plan execution failed")
        _update_stored_plan(plan_id, status="failed", result_summary=str(exc))
        await log_writer.finish_subagent_log(
            _plan_subagent_log_id,
            status="failed",
            error_message=str(exc),
            duration_ms=int((_time.monotonic() - _plan_run_start) * 1000),
        )
        yield {"type": "plan_error", "plan_id": plan_id, "error": str(exc)}

    finally:
        if _prefetch_memory_task is not None and not _prefetch_memory_task.done():
            _prefetch_memory_task.cancel()
        _terminate_mcp_processes(_all_mcp_clients)
        _all_mcp_clients.clear()
        try:
            _log_ctx.__exit__(None, None, None)
        except Exception:
            pass
