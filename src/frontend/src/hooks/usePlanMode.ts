import { message } from 'antd';
import { generatePlanStream, updatePlanApi, executePlanStream, getPlanApi } from '../api';
import { parseSpaceFileContent } from '../utils/fileParser';
import { useChatStore, useCatalogStore, useFileStore } from '../stores';
import type { ChatItem, ChatMessage, MessageSegment, ToolCall } from '../types';

/** Helper: read SSE stream and collect events */
export async function readPlanSse(response: Response): Promise<Array<Record<string, unknown>>> {
  const events: Array<Record<string, unknown>> = [];
  const reader = response.body?.getReader();
  if (!reader) return events;
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split(/\n\n+/);
      buf = blocks.pop() || '';
      for (const block of blocks) {
        for (const line of block.split(/\r?\n/)) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') return events;
          try { events.push(JSON.parse(data)); } catch { /* skip */ }
        }
      }
    }
  } finally { reader.releaseLock(); }
  return events;
}

/** Helper: append/update assistant message in current chat */
export function makePlanAppender(chatId: string, ts: number) {
  return (content: string, streaming: boolean, toolCalls?: ToolCall[], segments?: MessageSegment[]) => {
    useChatStore.getState().updateStore((prev) => {
      const c = prev.chats[chatId];
      const msgs = [...(c?.messages || [])];
      const last = msgs[msgs.length - 1];
      const isMd = content.includes('\n') || content.includes('**') || /^\s*[#\-\d]/.test(content);
      const updated: Partial<ChatMessage> & { content: string; isMarkdown: boolean; isStreaming: boolean } = {
        content, isMarkdown: isMd, isStreaming: streaming,
        ...(toolCalls && toolCalls.length > 0 ? { toolCalls } : {}),
        ...(segments && segments.length > 0 ? { segments } : {}),
      };
      if (last?.role === 'assistant' && last.ts === ts) {
        msgs[msgs.length - 1] = { ...last, ...updated };
      } else {
        msgs.push({ role: 'assistant', ts, ...updated });
      }
      return { chats: { ...prev.chats, [chatId]: { ...(c as any), messages: msgs, updatedAt: Date.now() } }, order: [chatId, ...(prev.order || []).filter((x) => x !== chatId)] };
    });
  };
}

/** Build structured plan data for PlanCard segment rendering */
export function buildPlanSegmentData(planData: Record<string, unknown>): MessageSegment['planData'] {
  const steps = (planData.steps || []) as Array<Record<string, unknown>>;
  return {
    mode: 'preview',
    title: String(planData.title || ''),
    description: planData.description ? String(planData.description) : undefined,
    steps: steps.map(s => ({
      step_order: Number(s.step_order || 0),
      title: String(s.title || ''),
      brief_description: s.brief_description ? String(s.brief_description) : undefined,
      description: s.description ? String(s.description) : undefined,
      expected_tools: (s.expected_tools as string[]) || [],
      expected_skills: (s.expected_skills as string[]) || [],
      expected_agents: (s.expected_agents as string[]) || [],
      acceptance_criteria: s.acceptance_criteria ? String(s.acceptance_criteria) : undefined,
    })),
    agentNameMap: (planData.agent_name_map as Record<string, string>) || undefined,
  };
}

export async function sendPlanMode(
  effectiveApiUrl: string,
  abortControllersRef: React.MutableRefObject<Map<string, AbortController>>,
  fileUploadMap: React.MutableRefObject<Map<File, Promise<{ content: string; file_id: string; download_url: string }>>>,
  generateSummary: (chatId: string) => Promise<void>,
  directMessage?: string,
) {
  const { input, setInput, sending, addSendingChatId, removeSendingChatId, currentChatId, updateStore, addBackendSessionId, addLoadedMsgId, currentPlanId, setCurrentPlanId } = useChatStore.getState();
  const { catalog } = useCatalogStore.getState();
  const { uploadedFiles, setUploadedFiles, setUploadingFiles, importedSpaceFiles, clearImportedSpaceFiles } = useFileStore.getState();
  const msg = directMessage?.trim() || input.trim();
  if (!msg || sending) return;
  if (!effectiveApiUrl) {
    message.error('请先在设置中配置 API 地址。');
    return;
  }

  // When there's a pending plan, always route through intent classification on /generate.
  // Backend LLM decides: confirm → execute, replan → generate new plan.
  const hasPendingPlan = !!currentPlanId;

  const streamChatId = currentChatId;
  addSendingChatId(streamChatId);
  if (!directMessage) setInput('');

  type Attachment = { name: string; content: string; mime_type: string; file_id: string; download_url: string };
  const attachments: Attachment[] = [];
  for (const file of uploadedFiles) {
    const promise = fileUploadMap.current.get(file);
    const result = promise ? await promise : { content: '', file_id: '', download_url: '' };
    attachments.push({ name: file.name, content: result.content, mime_type: file.type || '', file_id: result.file_id, download_url: result.download_url });
  }
  const spaceResults = await Promise.all(
    importedSpaceFiles.map(async (f) => ({
      name: f.name,
      content: await parseSpaceFileContent(f.download_url, f.name, f.mime_type, effectiveApiUrl ?? ''),
      mime_type: f.mime_type, file_id: f.file_id, download_url: f.download_url,
    })),
  );
  attachments.push(...spaceResults);
  setUploadedFiles([]);
  setUploadingFiles(new Set());
  clearImportedSpaceFiles();
  fileUploadMap.current.clear();

  const enabledMcpIds = (catalog.mcp || []).filter(x => x.enabled).map(x => String(x.id).trim()).filter(Boolean);
  const enabledSkillIds = (catalog.skills || []).filter(x => x.enabled).map(x => String(x.id).trim()).filter(Boolean);
  const enabledKbIds = (catalog.kb || []).filter(x => x.enabled).map(x => String(x.id).trim()).filter(Boolean);

  const userMsg: ChatMessage = {
    role: 'user', content: msg, isMarkdown: false, ts: Date.now(),
    ...(attachments.length > 0 && {
      attachments: attachments.map(a => ({
        name: a.name, mime_type: a.mime_type, file_id: a.file_id, download_url: a.download_url,
      })),
    }),
  };
  updateStore((prev) => {
    const c = prev.chats[currentChatId];
    const nextChat: ChatItem = {
      ...(c || { id: currentChatId, title: '新对话', createdAt: Date.now(), updatedAt: Date.now(), messages: [], favorite: false, pinned: false, businessTopic: '综合咨询' }),
      messages: [...(c?.messages || []), userMsg],
      updatedAt: Date.now(),
      title: c?.title && c.title !== '新对话' ? c.title : msg.slice(0, 18) || '新对话',
    };
    return { chats: { ...prev.chats, [currentChatId]: nextChat }, order: [currentChatId, ...(prev.order || []).filter((x) => x !== currentChatId)] };
  });

  const placeholderTs = Date.now();
  const appendAssistant = makePlanAppender(currentChatId, placeholderTs);
  appendAssistant('', true);

  const chatForHistory = useChatStore.getState().store.chats[currentChatId];
  const historyMessages: Array<{ role: string; content: string }> = [];
  if (chatForHistory?.messages) {
    for (const m of chatForHistory.messages) {
      if (m.ts === userMsg.ts) continue;
      if (m.content && (m.role === 'user' || m.role === 'assistant')) {
        historyMessages.push({ role: m.role, content: m.content });
      }
    }
  }

  const abortController = new AbortController();
  abortControllersRef.current.set(streamChatId, abortController);

  // Execute an approved plan, streaming step-by-step updates.
  // Returns true if execution started, false on global reset (new plan shown instead).
  const runExecutePlan = async (planIdToExec: string) => {
    appendAssistant('正在执行计划...', true);
    await updatePlanApi(planIdToExec, { status: 'approved' });
    const execResp = await executePlanStream(planIdToExec, abortController.signal, enabledMcpIds, enabledSkillIds, enabledKbIds, currentChatId, historyMessages);
    if (!execResp.ok) throw new Error(`计划执行请求失败: ${execResp.status}`);

    const decoder = new TextDecoder();
    const execReader = execResp.body?.getReader();
    if (!execReader) throw new Error('No response body');
    let execBuf = '';
    const stepResults: Record<string, { status: string; summary: string; text: string; title: string; order: number; step_id: string; replaced?: boolean; is_replan_new?: boolean; replan_reason?: string }> = {};
    const toolCalls: ToolCall[] = [];
    let planTitle = '';
    let planDesc = '';
    let planStepDefs: Array<Record<string, unknown>> = [];
    let planCompleted = false;
    let planAgentNameMap: Record<string, string> | undefined;

    try {
      const plan = await getPlanApi(planIdToExec);
      planTitle = plan.title;
      planDesc = plan.description || '';
      planStepDefs = plan.steps as any[];
      planAgentNameMap = (plan as any).agent_name_map || undefined;
    } catch { /* fallback */ }

    const buildExecPlanData = (mode: 'executing' | 'complete', completedSteps?: number, totalSteps?: number, resultText?: string): MessageSegment['planData'] => {
      const stepSource = planStepDefs.length > 0 ? planStepDefs : Object.values(stepResults).sort((a, b) => a.order - b.order);
      const steps = stepSource.map(s => {
        const sid = (s as any).step_id;
        const r = sid ? stepResults[sid] : undefined;
        return {
          step_order: r?.order || (s as any).step_order || 0,
          title: r?.title || (s as any).title || '',
          brief_description: (s as any).brief_description || undefined,
          description: (s as any).description,
          status: (r?.status || 'pending') as any,
          summary: r?.summary || '',
          text: r?.text || '',
          replaced: r?.replaced,
          is_replan_new: r?.is_replan_new,
          replan_reason: r?.replan_reason,
        };
      });
      return {
        mode,
        title: planTitle || '执行中...',
        description: planDesc || undefined,
        steps,
        completedSteps,
        totalSteps,
        resultText,
        agentNameMap: planAgentNameMap,
      };
    };

    const updatePlanCard = (streaming: boolean, mode: 'executing' | 'complete' = 'executing', completedSteps?: number, totalSteps?: number, resultText?: string) => {
      const planData = buildExecPlanData(mode, completedSteps, totalSteps, resultText);
      const segments: MessageSegment[] = [{ type: 'plan', planData }];
      toolCalls.forEach((_tc, idx) => { segments.push({ type: 'tool', toolIndex: idx }); });
      const content = resultText || '';
      if (resultText) segments.push({ type: 'text', content });
      appendAssistant(content, streaming, [...toolCalls], [...segments]);
    };

    updatePlanCard(true);

    while (true) {
      const { done, value } = await execReader.read();
      if (done) break;
      execBuf += decoder.decode(value, { stream: true });
      const blocks = execBuf.split(/\n\n+/);
      execBuf = blocks.pop() || '';
      for (const block of blocks) {
        for (const line of block.split(/\r?\n/)) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') break;
          try {
            const evt = JSON.parse(data);
            const stepId = evt.step_id as string | undefined;
            switch (evt.type) {
              case 'plan_step_start':
                if (stepId) stepResults[stepId] = { status: 'running', summary: '', text: '', title: evt.title || '', order: evt.step_order || 0, step_id: stepId };
                updatePlanCard(true);
                break;
              case 'plan_step_progress':
                if (stepId && stepResults[stepId]) { stepResults[stepId].text += evt.delta || ''; updatePlanCard(true); }
                break;
              case 'plan_step_qa':
                // Show redo_failed status temporarily when QA returns REDO_STEP
                if (stepId && stepResults[stepId] && evt.verdict === 'REDO_STEP') {
                  stepResults[stepId].status = 'redo_failed';
                  updatePlanCard(true);
                }
                break;
              case 'plan_replan': {
                // Local replan: mark old steps as replaced, new steps as replan_new
                const replacedFrom: number = evt.replaced_from_order || 0;
                const newStepList: Array<Record<string, unknown>> = evt.new_steps || [];
                // Mark steps from replacedFrom onward as replaced
                for (const r of Object.values(stepResults)) {
                  if (r.order >= replacedFrom && !r.replaced) {
                    r.replaced = true;
                    r.status = 'skipped';
                  }
                }
                // Register incoming new steps
                for (const ns of newStepList) {
                  const nsId = String(ns.step_id || '');
                  if (nsId) {
                    stepResults[nsId] = {
                      status: 'pending', summary: '', text: '',
                      title: String(ns.title || ''), order: Number(ns.step_order || 0),
                      step_id: nsId, is_replan_new: true,
                      replan_reason: String(evt.reason || ''),
                    };
                  }
                }
                // Refresh planStepDefs to include replaced + new steps
                planStepDefs = [
                  ...planStepDefs.filter((s: any) => (stepResults[s.step_id]?.order || s.step_order || 0) < replacedFrom),
                  ...planStepDefs.filter((s: any) => (stepResults[s.step_id]?.order || s.step_order || 0) >= replacedFrom).map((s: any) => ({ ...s, _replaced: true })),
                  ...newStepList,
                ];
                updatePlanCard(true);
                break;
              }
              case 'plan_global_reset': {
                // Execution stream interrupted: new plan generated, show as preview for user confirmation
                execReader.releaseLock();
                const failureReason = String(evt.failure_reason || '执行方案无法达到预期效果');
                const newPlanData = evt.new_plan as Record<string, unknown>;
                const newPlanId = `plan_reset_${Date.now()}`;
                const resetSegData = newPlanData ? buildPlanSegmentData({ ...newPlanData, plan_id: newPlanId }) : null;

                const resetMsg = `在执行过程中，发现原定方案无法达到您的预期效果（${failureReason}）。为了更准确地完成任务，我重新制定了一份方案，您看是否按这个新方案继续？`;
                if (resetSegData) {
                  const segs: MessageSegment[] = [{ type: 'text', content: resetMsg }, { type: 'plan', planData: resetSegData }];
                  appendAssistant(resetMsg, false, undefined, segs);
                  setCurrentPlanId((evt.new_plan_id as string) || newPlanId);
                } else {
                  appendAssistant(resetMsg, false);
                  setCurrentPlanId(null);
                }
                planCompleted = true; // prevent fallback update
                return;
              }
              case 'tool_call': {
                if (stepId) {
                  let tcDisplayName = typeof evt.tool_display_name === 'string' && evt.tool_display_name.trim()
                    ? evt.tool_display_name.trim()
                    : undefined;
                  if (tcDisplayName && typeof evt.subagent_name === 'string' && evt.subagent_name.trim()) {
                    tcDisplayName += `：${(evt.subagent_name as string).trim()}`;
                  }
                  toolCalls.push({ id: evt.tool_id, name: evt.tool_name || 'unknown', displayName: tcDisplayName, input: evt.tool_args, status: 'running', timestamp: Date.now() });
                  updatePlanCard(true);
                }
                break;
              }
              case 'tool_result': {
                if (evt.tool_id) {
                  const idx = toolCalls.findIndex(t => t.id === evt.tool_id);
                  if (idx >= 0) {
                    let resultDisplayName: string | undefined;
                    if (typeof evt.subagent_name === 'string' && evt.subagent_name.trim()) {
                      resultDisplayName = `调用子智能体：${(evt.subagent_name as string).trim()}`;
                    }
                    toolCalls[idx] = { ...toolCalls[idx], output: evt.result, status: 'success', ...(resultDisplayName ? { displayName: resultDisplayName } : {}) };
                    updatePlanCard(true);
                  }
                }
                break;
              }
              case 'plan_step_complete':
                if (stepId && stepResults[stepId]) {
                  stepResults[stepId].status = evt.status || 'success';
                  stepResults[stepId].summary = evt.summary || '';
                  stepResults[stepId].text = '';
                  updatePlanCard(true);
                }
                break;
              case 'plan_error':
                if (stepId && stepResults[stepId]) { stepResults[stepId].status = 'failed'; stepResults[stepId].summary = evt.error || '执行出错'; }
                updatePlanCard(true);
                break;
              case 'plan_complete': {
                planCompleted = true;
                updatePlanCard(false, 'complete', evt.completed_steps, evt.total_steps, evt.result_text || undefined);
                break;
              }
            }
          } catch { /* skip */ }
        }
      }
    }
    execReader.releaseLock();
    toolCalls.forEach(tc => { if (tc.status === 'running') tc.status = 'success'; });
    if (!planCompleted) updatePlanCard(false);
    setCurrentPlanId(null);
    addBackendSessionId(currentChatId);
    addLoadedMsgId(currentChatId);
    setTimeout(() => generateSummary(currentChatId), 500);
  };

  try {
    if (hasPendingPlan && currentPlanId) {
      // Route through intent classification: send user reply to /generate with previous_plan_id
      appendAssistant('正在识别意图...', true);
      const intentResp = await generatePlanStream(msg, 'qwen', abortController.signal, enabledMcpIds, enabledSkillIds, enabledKbIds, currentChatId, historyMessages, attachments, undefined, currentPlanId, msg);
      if (!intentResp.ok) throw new Error(`意图识别请求失败: ${intentResp.status}`);

      const intentEvents = await readPlanSse(intentResp);
      const confirmEvt = intentEvents.find(e => e.type === 'plan_confirm');
      const newPlanEvt = intentEvents.find(e => e.type === 'plan_generated');
      const errorEvt = intentEvents.find(e => e.type === 'plan_error');

      if (errorEvt) { appendAssistant(`操作失败：${errorEvt.error}`, false); return; }

      if (confirmEvt) {
        // User confirmed: execute the current plan
        await runExecutePlan(currentPlanId);
      } else if (newPlanEvt) {
        // User requested replan: show new plan for confirmation
        setCurrentPlanId(newPlanEvt.plan_id as string);
        const planSegData = buildPlanSegmentData(newPlanEvt);
        const planSegments: MessageSegment[] = [{ type: 'plan', planData: planSegData }];
        appendAssistant('', false, undefined, planSegments);
      } else {
        appendAssistant('未能识别您的意图，请明确回复"确认执行"或"重新计划+建议"。', false);
      }

    } else {
      // Phase 1: Generate plan
      appendAssistant('🔍 正在分析任务并生成执行计划...', true);

      const genResp = await generatePlanStream(msg, 'qwen', abortController.signal, enabledMcpIds, enabledSkillIds, enabledKbIds, currentChatId, historyMessages, attachments);
      if (!genResp.ok) throw new Error(`计划生成请求失败: ${genResp.status}`);

      const events = await readPlanSse(genResp);
      const planEvt = events.find(e => e.type === 'plan_generated');
      const errorEvt = events.find(e => e.type === 'plan_error');

      if (errorEvt) {
        appendAssistant(`计划生成失败：${errorEvt.error}`, false);
        return;
      }
      if (!planEvt) {
        appendAssistant('计划生成未返回有效结果，请重试。', false);
        return;
      }

      setCurrentPlanId(planEvt.plan_id as string);
      const planSegData = buildPlanSegmentData(planEvt);
      const planSegments: MessageSegment[] = [{ type: 'plan', planData: planSegData }];
      appendAssistant('', false, undefined, planSegments);
    }

  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      appendAssistant(`计划模式出错：${e?.message || String(e)}`, false);
    }
  } finally {
    abortControllersRef.current.delete(streamChatId);
    removeSendingChatId(streamChatId);
  }
}
