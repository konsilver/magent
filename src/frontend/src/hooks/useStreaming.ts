import { useRef } from 'react';
import { message } from 'antd';
import { authFetch, getFollowUpQuestions, regenerateMessage, editAndRegenerate } from '../api';
import { normalizeArtifactOutput } from '../utils/fileParser';
import { parseFileContent, parseSpaceFileContent, uploadFileToOSS } from '../utils/fileParser';
import { inferBusinessTopic } from '../utils/history';
import { useChatStore, useAuthStore, useCatalogStore, useFileStore, useUIStore } from '../stores';

import type { ChatItem, ChatMessage, CitationItem, MessageSegment } from '../types';

export function useStreaming(
  effectiveApiUrl: string,
  generateSummary: (chatId: string) => Promise<void>,
  generateClassification: (chatId: string) => Promise<void>,
) {
  const fileUploadMap = useRef<Map<File, Promise<{ content: string; file_id: string; download_url: string }>>>(new Map());
  /** AbortControllers keyed by chat id — allows multiple chats to stream in parallel
   *  (e.g. user starts chat A, switches to new chat B, sends while A is still running). */
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());

  function handleFileSelect(
    e: React.ChangeEvent<HTMLInputElement>,
    fileInputRef: React.RefObject<HTMLInputElement | null>,
  ) {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const newFiles = Array.from(files);
    if (fileInputRef.current) fileInputRef.current.value = '';

    const { setUploadedFiles, uploadedFiles } = useFileStore.getState();
    setUploadedFiles([...uploadedFiles, ...newFiles]);

    const curApiUrl = effectiveApiUrl ?? '';
    const curChatId = useChatStore.getState().currentChatId;

    for (const file of newFiles) {
      const { addUploadingFile, removeUploadingFile } = useFileStore.getState();
      addUploadingFile(file);
      const promise = Promise.all([
        parseFileContent(file, curApiUrl),
        uploadFileToOSS(file, curApiUrl, curChatId),
      ]).then(([content, { file_id, download_url }]) => {
        if (!file_id) message.warning(`文件"${file.name}"上传失败，发送后将无法下载`);
        return { content, file_id, download_url };
      })
        .catch(() => {
          message.warning(`文件"${file.name}"上传失败，发送后将无法下载`);
          return { content: '', file_id: '', download_url: '' };
        })
        .finally(() => { removeUploadingFile(file); });
      fileUploadMap.current.set(file, promise);
    }
  }

  function removeFile(index: number) {
    const { uploadedFiles, setUploadedFiles, removeUploadingFile } = useFileStore.getState();
    const removedFile = uploadedFiles[index];
    if (removedFile) {
      fileUploadMap.current.delete(removedFile);
      removeUploadingFile(removedFile);
    }
    setUploadedFiles(uploadedFiles.filter((_, i) => i !== index));
  }

  async function send(directMessage?: string) {
    const { input, setInput, sending, addSendingChatId, removeSendingChatId, thinkingMode, currentChatId, updateStore, addBackendSessionId, addLoadedMsgId, quotedFollowUp, setQuotedFollowUp, activeSkill, setActiveSkill, activeMention, setActiveMention } = useChatStore.getState();
    const { authUser } = useAuthStore.getState();
    const { catalog } = useCatalogStore.getState();
    const { uploadedFiles, setUploadedFiles, setUploadingFiles, importedSpaceFiles, clearImportedSpaceFiles } = useFileStore.getState();

    let msg = directMessage?.trim() || input.trim();
    if (!msg || sending) return;
    if (!effectiveApiUrl) {
      message.error('请先在设置中配置 API 地址。');
      useCatalogStore.getState().setPanel('settings');
      return;
    }

    const currentSkill = activeSkill;
    const currentMention = activeMention;

    if (currentMention) {
      msg = `@${currentMention.name} ${msg}`;
    }

    // Snapshot the chat id — user may switch chats mid-stream, but this stream
    // continues writing to the chat it was started in.
    const streamChatId = currentChatId;
    addSendingChatId(streamChatId);
    if (!directMessage) setInput('');
    if (quotedFollowUp) setQuotedFollowUp(null);
    if (currentSkill) setActiveSkill(null);
    if (currentMention) setActiveMention(null);
    // 发送对话后自动收起"提示词中心"侧边栏
    if (useUIStore.getState().promptHubOpen) {
      useUIStore.getState().setPromptHubOpen(false);
    }

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

    const userMsg: ChatMessage = {
      role: 'user',
      content: msg,
      isMarkdown: false,
      ts: Date.now(),
      ...(quotedFollowUp && {
        quotedFollowUp: {
          text: quotedFollowUp.text,
          ts: quotedFollowUp.ts,
        },
      }),
      ...(attachments.length > 0 && {
        attachments: attachments.map(a => ({
          name: a.name,
          mime_type: a.mime_type,
          file_id: a.file_id,
          download_url: a.download_url,
        })),
      }),
      ...(currentSkill ? { skillId: currentSkill.id, skillName: currentSkill.name } : {}),
      ...(currentMention ? { mentionName: currentMention.name } : {}),
    };

    updateStore((prev) => {
      const c = prev.chats[currentChatId];
      const inferredTopic = c?.businessTopic && c.businessTopic !== '综合咨询' ? c.businessTopic : inferBusinessTopic(msg);
      const nextChat: ChatItem = {
        ...(c || {
          id: currentChatId,
          title: '新对话',
          createdAt: Date.now(),
          updatedAt: Date.now(),
          messages: [],
          favorite: false,
          pinned: false,
          businessTopic: '综合咨询',
        }),
        messages: [...(c?.messages || []), userMsg],
        updatedAt: Date.now(),
        title: c?.title && c.title !== '新对话' ? c.title : msg.slice(0, 18) || '新对话',
        businessTopic: inferredTopic,
      };
      return {
        chats: { ...prev.chats, [currentChatId]: nextChat },
        order: [currentChatId, ...(prev.order || []).filter((x) => x !== currentChatId)],
      };
    });

    try {
      const enabledKbIds = (catalog.kb || [])
        .filter((x) => !!x.enabled)
        .map((x) => String(x.id).trim())
        .filter((x) => !!x);

      const abortController = new AbortController();
      abortControllersRef.current.set(streamChatId, abortController);

      const currentChat = useChatStore.getState().store.chats[currentChatId];
      const agentId = (currentChat as any)?.agentId || undefined;
      const codeExec = !!(currentChat as any)?.codeExecChat;
      const isPlanChat = !!(currentChat as any)?.planChat || useChatStore.getState().planMode;

      const r = await authFetch(`${effectiveApiUrl}/v1/chats/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: currentChatId,
          message: msg,
          model_name: 'qwen',
          user_id: authUser?.user_id,
          enable_thinking: thinkingMode,
          attachments,
          enabled_kbs: enabledKbIds,
          ...(quotedFollowUp ? {
            quoted_follow_up: {
              text: quotedFollowUp.text,
              ts: quotedFollowUp.ts,
            },
          } : {}),
          ...(agentId ? { agent_id: agentId } : {}),
          ...(currentSkill ? { skill_id: currentSkill.id } : {}),
          ...(codeExec ? { code_exec: true } : {}),
          ...(isPlanChat ? { plan_chat: true } : {}),
        }),
        signal: abortController.signal,
      });
      if (!r.ok || !r.body) throw new Error(await r.text());

      const reader = r.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let sseBuffer = '';
      let full = '';
      let streamEnded = false;
      type StreamToolCall = {
        id?: string;
        name: string;
        displayName?: string;
        input?: unknown;
        output?: unknown;
        status: 'running' | 'success' | 'error';
        timestamp: number;
      };
      let toolCalls: StreamToolCall[] = [];
      let thinking: any[] = [];
      let segments: MessageSegment[] = [];
      let metaMessageId: string | undefined;
      let metaFollowUps: string[] = [];
      let allCitations: CitationItem[] = [];
      let parseBuffer = '';
      let toolPending = false;

      // Plan mode state (auto-routed by backend classifier)
      type PlanStepResult = { status: string; summary: string; text: string; title: string; order: number; step_id: string };
      let planStepResults: Record<string, PlanStepResult> = {};
      let planTitle = '';
      let planDesc = '';
      let planStepDefs: Array<Record<string, unknown>> = [];
      let planAgentNameMap: Record<string, string> | undefined;

      const buildExecPlanData = (mode: 'preview' | 'executing' | 'complete', completedSteps?: number, totalSteps?: number, resultText?: string): MessageSegment['planData'] => {
        const stepSource = planStepDefs.length > 0 ? planStepDefs : Object.values(planStepResults).sort((a, b) => a.order - b.order);
        const steps = stepSource.map(s => {
          const sid = (s as any).step_id;
          const r = sid ? planStepResults[sid] : undefined;
          return {
            step_order: r?.order ?? ((s as any).step_order ?? 0),
            title: r?.title ?? ((s as any).title ?? ''),
            description: (s as any).description,
            status: ((r?.status || 'pending') as any),
            summary: r?.summary ?? '',
            text: r?.text ?? '',
          };
        });
        return { mode, title: planTitle || '执行计划', description: planDesc || undefined, steps, completedSteps, totalSteps, resultText, agentNameMap: planAgentNameMap };
      };

      const updatePlanCard = (streaming: boolean, mode: 'preview' | 'executing' | 'complete' = 'executing', completedSteps?: number, totalSteps?: number, resultText?: string) => {
        const planData = buildExecPlanData(mode, completedSteps, totalSteps, resultText);
        const planSegs: MessageSegment[] = [{ type: 'plan', planData }];
        toolCalls.forEach((_tc, idx) => { planSegs.push({ type: 'tool', toolIndex: idx }); });
        const content = resultText || '';
        if (resultText) planSegs.push({ type: 'text', content });
        useChatStore.getState().updateStore((prev) => {
          const c = prev.chats[currentChatId];
          const msgs = [...(c?.messages || [])];
          const last = msgs[msgs.length - 1];
          const updated: any = { content, isMarkdown: false, isStreaming: streaming, toolCalls: toolCalls.length > 0 ? [...toolCalls] : undefined, segments: planSegs };
          if (last?.role === 'assistant' && last.ts === placeholderTs) msgs[msgs.length - 1] = { ...last, ...updated };
          else msgs.push({ role: 'assistant', ts: placeholderTs, ...updated });
          return { chats: { ...prev.chats, [currentChatId]: { ...(c as any), messages: msgs, updatedAt: Date.now() } }, order: [currentChatId, ...(prev.order || []).filter((x) => x !== currentChatId)] };
        });
      };

      const isThinkingMode = thinkingMode;
      let thinkingPhaseActive = isThinkingMode;

      const getPartialTagLen = (text: string, tag: string): number => {
        for (let len = Math.min(tag.length - 1, text.length); len >= 1; len--) {
          if (tag.startsWith(text.slice(text.length - len))) return len;
        }
        return 0;
      };

      const appendThinkContent = (content: string, isDelta: boolean) => {
        if (!content) return;
        const lastSeg = segments[segments.length - 1];
        const lastThink = isDelta && lastSeg?.type === 'thinking' ? lastSeg : null;
        if (lastThink) {
          lastThink.content = (lastThink.content || '') + content;
          if (thinking.length > 0) thinking[thinking.length - 1].content += content;
          else thinking.push({ content, timestamp: Date.now() });
        } else {
          segments.push({ type: 'thinking', content });
          thinking.push({ content, timestamp: Date.now() });
        }
      };

      const processTextChunk = (chunk: string) => {
        parseBuffer += chunk;
        while (parseBuffer.length > 0) {
          if (thinkingPhaseActive) {
            const openIdx = parseBuffer.indexOf('<think>');
            const closeIdx = parseBuffer.indexOf('</think>');
            if (openIdx >= 0 && (closeIdx === -1 || openIdx < closeIdx)) {
              if (openIdx > 0) appendThinkContent(parseBuffer.slice(0, openIdx), true);
              parseBuffer = parseBuffer.slice(openIdx + 7);
              continue;
            }
            if (closeIdx === -1) {
              const partialLen = getPartialTagLen(parseBuffer, '</think>');
              const safeLen = parseBuffer.length - partialLen;
              if (safeLen > 0) {
                appendThinkContent(parseBuffer.slice(0, safeLen), true);
                parseBuffer = parseBuffer.slice(safeLen);
              }
              break;
            } else {
              if (closeIdx > 0) appendThinkContent(parseBuffer.slice(0, closeIdx), true);
              parseBuffer = parseBuffer.slice(closeIdx + 8);
              thinkingPhaseActive = false;
            }
          } else {
            const openIdx = parseBuffer.indexOf('<think>');
            const closeIdx = parseBuffer.indexOf('</think>');
            if (closeIdx >= 0 && (openIdx === -1 || closeIdx <= openIdx)) {
              if (closeIdx > 0) {
                const text = parseBuffer.slice(0, closeIdx);
                full += text;
                const last = segments[segments.length - 1];
                if (last && last.type === 'text') last.content = (last.content || '') + text;
                else segments.push({ type: 'text', content: text });
              }
              parseBuffer = parseBuffer.slice(closeIdx + 8);
              continue;
            }
            if (openIdx >= 0) {
              if (openIdx > 0) {
                const text = parseBuffer.slice(0, openIdx);
                full += text;
                const last = segments[segments.length - 1];
                if (last && last.type === 'text') last.content = (last.content || '') + text;
                else segments.push({ type: 'text', content: text });
              }
              parseBuffer = parseBuffer.slice(openIdx + 7);
              continue;
            }
            const partialLen = Math.max(
              getPartialTagLen(parseBuffer, '<think>'),
              getPartialTagLen(parseBuffer, '</think>')
            );
            const safeLen = parseBuffer.length - partialLen;
            if (safeLen > 0) {
              const text = parseBuffer.slice(0, safeLen);
              full += text;
              const last = segments[segments.length - 1];
              if (last && last.type === 'text') last.content = (last.content || '') + text;
              else segments.push({ type: 'text', content: text });
              parseBuffer = parseBuffer.slice(safeLen);
            }
            break;
          }
        }
      };

      const normalizeToolId = (value: unknown): string | undefined => {
        if (typeof value !== 'string') return undefined;
        const id = value.trim();
        return id.length > 0 ? id : undefined;
      };

      const getEventToolId = (obj: Record<string, unknown>) =>
        normalizeToolId(obj.id) || normalizeToolId(obj.tool_call_id) || normalizeToolId(obj.call_id) || normalizeToolId(obj.tool_id);

      const getEventToolRawName = (obj: Record<string, unknown>) => {
        const candidates = [obj.name, obj.tool_name, obj.tool, obj.title];
        for (const candidate of candidates) {
          if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
        }
        return undefined;
      };

      const getEventToolDisplayName = (obj: Record<string, unknown>) => {
        if (typeof obj.tool_display_name === 'string' && obj.tool_display_name.trim()) {
          let displayName = obj.tool_display_name.trim();
          if (typeof obj.subagent_name === 'string' && obj.subagent_name.trim()) {
            displayName += `：${obj.subagent_name.trim()}`;
          }
          return displayName;
        }
        return undefined;
      };

      const findLastRunningToolIndex = (name?: string) => {
        for (let i = toolCalls.length - 1; i >= 0; i--) {
          if (toolCalls[i].status !== 'running') continue;
          if (name && toolCalls[i].name !== name) continue;
          return i;
        }
        return -1;
      };

      const findToolCallIndex = (obj: Record<string, unknown>) => {
        const eventToolId = getEventToolId(obj);
        if (eventToolId) {
          const directIndex = toolCalls.findIndex((tool) => normalizeToolId(tool.id) === eventToolId);
          if (directIndex >= 0) return directIndex;
        }
        const eventToolName = getEventToolRawName(obj);
        const byNameIndex = findLastRunningToolIndex(eventToolName);
        if (byNameIndex >= 0) return byNameIndex;
        return findLastRunningToolIndex();
      };

      const finalizeRunningTools = (status: 'success' | 'error' = 'success') => {
        let changed = false;
        toolCalls = toolCalls.map((tool) => {
          if (tool.status !== 'running') return tool;
          changed = true;
          return { ...tool, status };
        });
        return changed;
      };

      const appendArtifactsToStreamToolCalls = (artifacts: unknown[]) => {
        if (!Array.isArray(artifacts) || artifacts.length === 0) return false;
        const existingFileIds = new Set<string>();
        for (const tool of toolCalls) {
          if (!tool?.output || typeof tool.output !== 'object') continue;
          const fileId = (tool.output as Record<string, unknown>).file_id;
          if (typeof fileId === 'string' && fileId.trim()) existingFileIds.add(fileId.trim());
        }
        let changed = false;
        for (const artifact of artifacts) {
          const output = normalizeArtifactOutput(artifact);
          if (!output) continue;
          const fileId = String(output.file_id);
          if (existingFileIds.has(fileId)) continue;
          existingFileIds.add(fileId);
          toolCalls.push({ id: `artifact_${fileId}`, name: '附件', output, status: 'success', timestamp: Date.now() });
          segments.push({ type: 'tool', toolIndex: toolCalls.length - 1 });
          changed = true;
        }
        return changed;
      };

      const placeholderTs = Date.now();
      const appendOrUpdate = (text: string, tools: any[], thinks: any[], segs: MessageSegment[], streaming: boolean, cits?: CitationItem[]) => {
        useChatStore.getState().updateStore((prev) => {
          const c = prev.chats[currentChatId];
          const msgs = [...(c?.messages || [])];
          const last = msgs[msgs.length - 1];
          const isMd = streaming && (text.includes('\n') || text.includes('```') || text.includes('**') || /^\s*#\s/m.test(text));
          const updatedMsg: Partial<ChatMessage> & { content: string; isMarkdown: boolean; isStreaming: boolean } = {
            content: text,
            isMarkdown: isMd,
            toolCalls: tools.length > 0 ? tools : undefined,
            thinking: thinks.length > 0 ? thinks : undefined,
            segments: segs.length > 0 ? segs : undefined,
            isStreaming: streaming,
            toolPending: streaming && toolPending,
          };
          if (cits !== undefined) updatedMsg.citations = cits.length > 0 ? cits : undefined;
          if (metaFollowUps.length > 0) updatedMsg.followUpQuestions = metaFollowUps;

          if (last?.role === 'assistant' && last.ts === placeholderTs) {
            msgs[msgs.length - 1] = { ...last, ...updatedMsg };
          } else {
            msgs.push({ role: 'assistant', ts: placeholderTs, ...updatedMsg });
          }
          const nextChat: ChatItem = { ...(c as any), messages: msgs, updatedAt: Date.now() };
          return { chats: { ...prev.chats, [currentChatId]: nextChat }, order: [currentChatId, ...(prev.order || []).filter((x) => x !== currentChatId)] };
        });
      };

      appendOrUpdate('', [], [], [], true);

      const handleSsePayload = (payload: string) => {
        const trimmedPayload = payload.trim();
        if (!trimmedPayload) return;
        if (trimmedPayload === '[DONE]') {
          if (finalizeRunningTools()) appendOrUpdate(full, toolCalls, thinking, segments, true);
          streamEnded = true;
          return;
        }

        let textChunk = '';
        let parsed = false;
        try {
          const obj = JSON.parse(trimmedPayload);
          parsed = true;
          if (typeof obj === 'string') {
            textChunk = obj;
          } else if (obj && typeof obj === 'object') {
            const eventObj = obj as Record<string, unknown>;
            const eventType = typeof obj.type === 'string' ? obj.type : '';
            if (eventType === 'classifying') {
              appendOrUpdate('正在分析任务类型...', [], [], [], true);
              return;
            }

            if (eventType === 'classified') {
              const taskType = String(eventObj.task_type || 'simple');
              if (taskType !== 'simple') {
                appendOrUpdate('正在规划任务...', [], [], [], true);
              }
              return;
            }

            if (eventType === 'plan_generating') {
              return; // suppress raw delta text; plan_generated will show the card
            }

            if (eventType === 'plan_generated') {
              planTitle = String(eventObj.title || '');
              planDesc = String(eventObj.description || '');
              planStepDefs = (Array.isArray(eventObj.steps) ? eventObj.steps : []) as Array<Record<string, unknown>>;
              planAgentNameMap = (eventObj.agent_name_map as Record<string, string>) || undefined;
              const initialMode = eventObj.executing ? 'executing' : 'preview';
              updatePlanCard(true, initialMode);
              return;
            }

            if (eventType === 'plan_needs_confirmation') {
              updatePlanCard(false, 'preview');
              return;
            }

            if (eventType === 'plan_step_start') {
              const stepId = String(eventObj.step_id || '');
              if (stepId) planStepResults[stepId] = { status: 'running', summary: '', text: '', title: String(eventObj.title || ''), order: Number(eventObj.step_order || 0), step_id: stepId };
              updatePlanCard(true);
              return;
            }

            if (eventType === 'plan_step_progress') {
              const stepId = String(eventObj.step_id || '');
              if (stepId && planStepResults[stepId]) { planStepResults[stepId].text += String(eventObj.delta || ''); updatePlanCard(true); }
              return;
            }

            if (eventType === 'plan_step_complete') {
              const stepId = String(eventObj.step_id || '');
              if (stepId && planStepResults[stepId]) {
                planStepResults[stepId].status = String(eventObj.status || 'success');
                planStepResults[stepId].summary = String(eventObj.summary || '');
                planStepResults[stepId].text = '';
              }
              updatePlanCard(true);
              return;
            }

            if (eventType === 'plan_complete') {
              updatePlanCard(false, 'complete', Number(eventObj.completed_steps), Number(eventObj.total_steps), String(eventObj.result_text || ''));
              streamEnded = true;
              addBackendSessionId(currentChatId);
              addLoadedMsgId(currentChatId);
              setTimeout(() => generateSummary(currentChatId), 500);
              return;
            }

            if (eventType === 'plan_error') {
              const stepId = String(eventObj.step_id || '');
              if (stepId && planStepResults[stepId]) { planStepResults[stepId].status = 'failed'; planStepResults[stepId].summary = String(eventObj.error || '执行出错'); }
              updatePlanCard(true);
              return;
            }

            if (eventType === 'end') {
              if (finalizeRunningTools()) appendOrUpdate(full, toolCalls, thinking, segments, true);
              streamEnded = true;
              return;
            }
            if (eventType === 'error') throw new Error(typeof obj.error === 'string' ? obj.error : '流式响应异常');

            if (eventType === 'tool_pending') {
              if (!toolPending) {
                toolPending = true;
                appendOrUpdate(full, toolCalls, thinking, segments, true);
              }
              return;
            }

            if (toolPending && eventType !== 'heartbeat') {
              toolPending = false;
              appendOrUpdate(full, toolCalls, thinking, segments, true);
            }

            if (eventType === 'tool_use' || eventType === 'tool_call' || eventType === 'tool_start') {
              const eventToolId = getEventToolId(eventObj);
              const existingIndex = eventToolId ? toolCalls.findIndex((tool) => normalizeToolId(tool.id) === eventToolId) : -1;
              const toolInput = eventObj.input ?? eventObj.args ?? eventObj.tool_args ?? eventObj.arguments;
              const rawName = getEventToolRawName(eventObj);
              const displayName = getEventToolDisplayName(eventObj);
              if (existingIndex >= 0) {
                const existing = toolCalls[existingIndex];
                toolCalls[existingIndex] = { ...existing, name: rawName || existing.name, displayName: displayName || existing.displayName, input: toolInput ?? existing.input, status: 'running' };
              } else {
                toolCalls.push({ id: eventToolId || `tool_${Date.now()}_${toolCalls.length}`, name: rawName || '工具调用', displayName, input: toolInput, status: 'running', timestamp: Date.now() });
                segments.push({ type: 'tool', toolIndex: toolCalls.length - 1 });
              }
              appendOrUpdate(full, toolCalls, thinking, segments, true);
              return;
            }

            if (eventType === 'tool_result' || eventType === 'tool_end') {
              const toolIndex = findToolCallIndex(eventObj);
              const status: StreamToolCall['status'] = obj.error ? 'error' : 'success';
              const output = eventObj.output ?? eventObj.result;

              let resultDisplayName: string | undefined;
              if (typeof obj.subagent_name === 'string' && obj.subagent_name.trim()) {
                resultDisplayName = `调用子智能体：${obj.subagent_name.trim()}`;
              }

              if (toolIndex >= 0) {
                const existing = toolCalls[toolIndex];
                toolCalls[toolIndex] = { ...existing, output: output ?? existing.output, status, ...(resultDisplayName ? { displayName: resultDisplayName } : {}) };
              } else {
                toolCalls.push({ id: getEventToolId(eventObj) || `tool_${Date.now()}_${toolCalls.length}`, name: getEventToolRawName(eventObj) || '工具调用', displayName: resultDisplayName || getEventToolDisplayName(eventObj), output, status, timestamp: Date.now() });
                segments.push({ type: 'tool', toolIndex: toolCalls.length - 1 });
              }
              if (Array.isArray(eventObj.citations)) allCitations = [...allCitations, ...(eventObj.citations as CitationItem[])];
              if (isThinkingMode) {
                if (parseBuffer) {
                  full += parseBuffer;
                  const last = segments[segments.length - 1];
                  if (last?.type === 'text') last.content = (last.content || '') + parseBuffer;
                  else segments.push({ type: 'text', content: parseBuffer });
                  parseBuffer = '';
                }
                thinkingPhaseActive = true;
              }
              appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
              return;
            }

            if (eventType === 'thinking' || eventType === 'thought') {
              const thinkContent = (obj.content || obj.text || obj.delta || '') as string;
              if (thinkContent) {
                const lastSeg = segments[segments.length - 1];
                if (thinking.length > 0 && obj.delta && lastSeg?.type === 'thinking') {
                  thinking[thinking.length - 1].content += thinkContent;
                  lastSeg.content = (lastSeg.content || '') + thinkContent;
                } else {
                  thinking.push({ content: thinkContent, timestamp: Date.now() });
                  segments.push({ type: 'thinking', content: thinkContent });
                }
                appendOrUpdate(full, toolCalls, thinking, segments, true);
              }
              return;
            }

            if (eventType === 'meta') {
              if (typeof eventObj.message_id === 'string') metaMessageId = eventObj.message_id;
              if (Array.isArray(eventObj.citations) && (eventObj.citations as CitationItem[]).length > 0) {
                allCitations = eventObj.citations as CitationItem[];
              }
              if (appendArtifactsToStreamToolCalls(Array.isArray(eventObj.artifacts) ? eventObj.artifacts : [])) {
                appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
              }
              return;
            }

            if (eventType === 'follow_up') {
              if (Array.isArray(eventObj.follow_up_questions) && eventObj.follow_up_questions.length > 0) {
                metaFollowUps = eventObj.follow_up_questions as string[];
                appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
              }
              return;
            }

            if (eventType === 'content' || eventType === 'ai_message' || eventType === 'text' || eventType === 'delta') {
              textChunk = (obj.delta || obj.content || obj.text || '') as string;
            }
          }
        } catch (err: any) {
          if (parsed) throw err;
          textChunk = trimmedPayload;
        }

        if (textChunk) {
          processTextChunk(textChunk);
          appendOrUpdate(full, toolCalls, thinking, segments, true);
        }
      };

      const processSseBlock = (block: string) => {
        if (!block.trim()) return;
        const lines = block.split(/\r?\n/);
        const dataLines: string[] = [];
        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data:')) dataLines.push(trimmed.slice(5).trim());
        }
        if (dataLines.length === 0) return;
        handleSsePayload(dataLines.join('\n'));
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        const blocks = sseBuffer.split(/\r?\n\r?\n/);
        sseBuffer = blocks.pop() || '';
        for (const block of blocks) {
          processSseBlock(block);
          if (streamEnded) break;
        }
        if (streamEnded) break;
      }

      const tail = sseBuffer.trim();
      if (tail && !streamEnded) processSseBlock(tail);

      finalizeRunningTools();
      if (parseBuffer) {
        if (thinkingPhaseActive) {
          appendThinkContent(parseBuffer, true);
        } else {
          full += parseBuffer;
          const last = segments[segments.length - 1];
          if (last && last.type === 'text') last.content = (last.content || '') + parseBuffer;
          else segments.push({ type: 'text', content: parseBuffer });
        }
        parseBuffer = '';
      }
      const isMd = /\n|```|\*\*|^\s*#\s/m.test(full);
      useChatStore.getState().updateStore((prev) => {
        const c = prev.chats[currentChatId];
        const msgs = [...(c?.messages || [])];
        const last = msgs[msgs.length - 1];
        if (last?.role === 'assistant' && last.ts === placeholderTs) {
          msgs[msgs.length - 1] = {
            ...last,
            content: full,
            isMarkdown: isMd,
            toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
            thinking: thinking.length > 0 ? thinking : undefined,
            segments: segments.length > 0 ? segments : undefined,
            citations: allCitations.length > 0 ? allCitations : undefined,
            followUpQuestions: metaFollowUps.length > 0 ? metaFollowUps : undefined,
            messageId: metaMessageId,
            isStreaming: false,
          };
        }
        const nextChat: ChatItem = { ...(c as any), messages: msgs, updatedAt: Date.now() };
        return { chats: { ...prev.chats, [currentChatId]: nextChat }, order: [currentChatId, ...(prev.order || []).filter((x) => x !== currentChatId)] };
      });
      addBackendSessionId(currentChatId);
      addLoadedMsgId(currentChatId);
      setTimeout(() => generateSummary(currentChatId), 500);
      setTimeout(() => generateClassification(currentChatId), 800);

      if (metaMessageId && metaFollowUps.length === 0) {
        const _pollChatId = currentChatId;
        const _pollMsgId = metaMessageId;
        const _pollTs = placeholderTs;
        (async () => {
          const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
          await delay(4000);
          for (let attempt = 0; attempt < 5; attempt++) {
            if (attempt > 0) await delay(3000);
            try {
              const questions = await getFollowUpQuestions(_pollChatId, _pollMsgId);
              if (questions.length > 0) {
                useChatStore.getState().updateStore((prev) => {
                  const c = prev.chats[_pollChatId];
                  if (!c) return { chats: prev.chats, order: prev.order };
                  const msgs = [...(c.messages || [])];
                  const idx = msgs.findIndex(
                    (m) => m.role === 'assistant' && (m.messageId === _pollMsgId || m.ts === _pollTs),
                  );
                  if (idx >= 0) {
                    msgs[idx] = { ...msgs[idx], followUpQuestions: questions };
                  }
                  return { chats: { ...prev.chats, [_pollChatId]: { ...c, messages: msgs } }, order: prev.order };
                });
                break;
              }
            } catch {
              // ignore polling errors
            }
          }
        })();
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') {
        useChatStore.getState().updateStore((prev) => {
          const c = prev.chats[currentChatId];
          if (!c) return { chats: prev.chats, order: prev.order };
          const msgs = [...(c.messages || [])];
          const last = msgs[msgs.length - 1];
          if (last?.role === 'assistant' && last.isStreaming) {
            msgs[msgs.length - 1] = { ...last, isStreaming: false };
          }
          return { chats: { ...prev.chats, [currentChatId]: { ...c, messages: msgs } }, order: prev.order };
        });
      } else {
        message.error(`发送失败：${e?.message || String(e)}`);
      }
    } finally {
      abortControllersRef.current.delete(streamChatId);
      removeSendingChatId(streamChatId);
      setUploadedFiles([]);
      setUploadingFiles(new Set());
      fileUploadMap.current.clear();
    }
  }

  /**
   * Shared SSE stream processor for regenerate/edit flows.
   * Reuses the same event handling as the main send() but skips user message creation.
   */
  async function processRegenerateStream(response: Response, chatId: string) {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder('utf-8');
    let sseBuffer = '';
    let full = '';
    let streamEnded = false;
    type StreamToolCall = {
      id?: string; name: string; displayName?: string;
      input?: unknown; output?: unknown;
      status: 'running' | 'success' | 'error'; timestamp: number;
    };
    let toolCalls: StreamToolCall[] = [];
    let thinking: any[] = [];
    let segments: MessageSegment[] = [];
    let metaMessageId: string | undefined;
    let metaFollowUps: string[] = [];
    let allCitations: CitationItem[] = [];

    const placeholderTs = Date.now();
    const appendOrUpdate = (text: string, tools: any[], thinks: any[], segs: MessageSegment[], streaming: boolean, cits?: CitationItem[]) => {
      useChatStore.getState().updateStore((prev) => {
        const c = prev.chats[chatId];
        const msgs = [...(c?.messages || [])];
        const last = msgs[msgs.length - 1];
        const isMd = streaming && (text.includes('\n') || text.includes('```') || text.includes('**') || /^\s*#\s/m.test(text));
        const updatedMsg: Partial<ChatMessage> & { content: string; isMarkdown: boolean; isStreaming: boolean } = {
          content: text, isMarkdown: isMd,
          toolCalls: tools.length > 0 ? tools : undefined,
          thinking: thinks.length > 0 ? thinks : undefined,
          segments: segs.length > 0 ? segs : undefined,
          isStreaming: streaming,
        };
        if (cits !== undefined) updatedMsg.citations = cits.length > 0 ? cits : undefined;
        if (metaFollowUps.length > 0) updatedMsg.followUpQuestions = metaFollowUps;
        if (last?.role === 'assistant' && last.ts === placeholderTs) {
          msgs[msgs.length - 1] = { ...last, ...updatedMsg };
        } else {
          msgs.push({ role: 'assistant', ts: placeholderTs, ...updatedMsg });
        }
        const nextChat: ChatItem = { ...(c as any), messages: msgs, updatedAt: Date.now() };
        return { chats: { ...prev.chats, [chatId]: nextChat }, order: [chatId, ...(prev.order || []).filter((x) => x !== chatId)] };
      });
    };

    appendOrUpdate('', [], [], [], true);

    const processSseBlock = (block: string) => {
      if (!block.trim()) return;
      const lines = block.split(/\r?\n/);
      const dataLines: string[] = [];
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data:')) dataLines.push(trimmed.slice(5).trim());
      }
      if (dataLines.length === 0) return;
      const payload = dataLines.join('\n').trim();
      if (!payload) return;
      if (payload === '[DONE]') { streamEnded = true; return; }

      try {
        const obj = JSON.parse(payload);
        if (typeof obj !== 'object' || !obj) return;
        const eventType = typeof obj.type === 'string' ? obj.type : '';

        if (eventType === 'content' || eventType === 'ai_message' || eventType === 'text' || eventType === 'delta') {
          const delta = (obj.delta || obj.content || obj.text || '') as string;
          if (delta) {
            full += delta;
            const last = segments[segments.length - 1];
            if (last && last.type === 'text') last.content = (last.content || '') + delta;
            else segments.push({ type: 'text', content: delta });
            appendOrUpdate(full, toolCalls, thinking, segments, true);
          }
        } else if (eventType === 'tool_call' || eventType === 'tool_use' || eventType === 'tool_start') {
          const rawName = obj.tool_name || obj.name || '工具调用';
          let displayName = obj.tool_display_name || undefined;
          if (displayName && obj.subagent_name) displayName += `：${obj.subagent_name}`;
          toolCalls.push({ id: obj.tool_id || `tool_${Date.now()}_${toolCalls.length}`, name: rawName, displayName, input: obj.tool_args || obj.input, status: 'running', timestamp: Date.now() });
          segments.push({ type: 'tool', toolIndex: toolCalls.length - 1 });
          appendOrUpdate(full, toolCalls, thinking, segments, true);
        } else if (eventType === 'tool_result' || eventType === 'tool_end') {
          const tid = obj.tool_id;
          const idx = tid ? toolCalls.findIndex(t => t.id === tid) : toolCalls.findIndex(t => t.status === 'running');
          if (idx >= 0) {
            let resultDisplayName: string | undefined;
            if (obj.subagent_name) resultDisplayName = `调用子智能体：${obj.subagent_name}`;
            toolCalls[idx] = { ...toolCalls[idx], output: obj.result ?? obj.output, status: obj.error ? 'error' : 'success', ...(resultDisplayName ? { displayName: resultDisplayName } : {}) };
          }
          if (Array.isArray(obj.citations)) allCitations = [...allCitations, ...(obj.citations as CitationItem[])];
          appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
        } else if (eventType === 'thinking' || eventType === 'thought') {
          const thinkContent = (obj.content || obj.text || obj.delta || '') as string;
          if (thinkContent) {
            thinking.push({ content: thinkContent, timestamp: Date.now() });
            segments.push({ type: 'thinking', content: thinkContent });
            appendOrUpdate(full, toolCalls, thinking, segments, true);
          }
        } else if (eventType === 'meta') {
          if (typeof obj.message_id === 'string') metaMessageId = obj.message_id;
          if (Array.isArray(obj.citations) && obj.citations.length > 0) allCitations = obj.citations;
          appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
        } else if (eventType === 'follow_up') {
          if (Array.isArray(obj.follow_up_questions)) {
            metaFollowUps = obj.follow_up_questions;
            appendOrUpdate(full, toolCalls, thinking, segments, true, allCitations);
          }
        } else if (eventType === 'error') {
          message.error(obj.error || '流式响应异常');
        }
      } catch { /* skip invalid JSON */ }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        const blocks = sseBuffer.split(/\r?\n\r?\n/);
        sseBuffer = blocks.pop() || '';
        for (const block of blocks) {
          processSseBlock(block);
          if (streamEnded) break;
        }
        if (streamEnded) break;
      }
      const tail = sseBuffer.trim();
      if (tail && !streamEnded) processSseBlock(tail);
    } catch (e: any) {
      if (e?.name !== 'AbortError') throw e;
    }

    // Finalize
    toolCalls = toolCalls.map(t => t.status === 'running' ? { ...t, status: 'success' as const } : t);
    const isMd = /\n|```|\*\*|^\s*#\s/m.test(full);
    useChatStore.getState().updateStore((prev) => {
      const c = prev.chats[chatId];
      const msgs = [...(c?.messages || [])];
      const last = msgs[msgs.length - 1];
      if (last?.role === 'assistant' && last.ts === placeholderTs) {
        msgs[msgs.length - 1] = {
          ...last, content: full, isMarkdown: isMd,
          toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
          thinking: thinking.length > 0 ? thinking : undefined,
          segments: segments.length > 0 ? segments : undefined,
          citations: allCitations.length > 0 ? allCitations : undefined,
          followUpQuestions: metaFollowUps.length > 0 ? metaFollowUps : undefined,
          messageId: metaMessageId, isStreaming: false,
        };
      }
      const nextChat: ChatItem = { ...(c as any), messages: msgs, updatedAt: Date.now() };
      return { chats: { ...prev.chats, [chatId]: nextChat }, order: [chatId, ...(prev.order || []).filter((x) => x !== chatId)] };
    });

    useChatStore.getState().addBackendSessionId(chatId);
    useChatStore.getState().addLoadedMsgId(chatId);
    setTimeout(() => generateSummary(chatId), 500);
    setTimeout(() => generateClassification(chatId), 800);
  }

  /** Regenerate the last assistant response */
  async function regenerate(messageIndex: number) {
    const { sending, addSendingChatId, removeSendingChatId, currentChatId, truncateMessagesFrom } = useChatStore.getState();
    if (sending) return;
    const streamChatId = currentChatId;
    addSendingChatId(streamChatId);

    const abortController = new AbortController();
    abortControllersRef.current.set(streamChatId, abortController);

    try {
      const chat = useChatStore.getState().store.chats[streamChatId];
      const targetMsg = chat?.messages[messageIndex];
      if (targetMsg) {
        truncateMessagesFrom(streamChatId, targetMsg.ts);
      }

      const r = await regenerateMessage(streamChatId, messageIndex, abortController.signal);
      if (!r.ok || !r.body) throw new Error(await r.text());

      await processRegenerateStream(r, streamChatId);
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        message.error(`重新生成失败：${e?.message || String(e)}`);
      }
    } finally {
      abortControllersRef.current.delete(streamChatId);
      removeSendingChatId(streamChatId);
    }
  }

  /** Edit a user message and regenerate */
  async function editAndResend(messageIndex: number, newContent: string) {
    const { sending, addSendingChatId, removeSendingChatId, currentChatId, truncateMessagesFrom, setEditingMessageTs } = useChatStore.getState();
    if (sending || !newContent.trim()) return;
    const streamChatId = currentChatId;
    addSendingChatId(streamChatId);
    setEditingMessageTs(null);

    const abortController = new AbortController();
    abortControllersRef.current.set(streamChatId, abortController);

    try {
      const chat = useChatStore.getState().store.chats[streamChatId];
      const targetMsg = chat?.messages[messageIndex];
      if (targetMsg) {
        truncateMessagesFrom(streamChatId, targetMsg.ts);
      }

      // Add the edited user message to local store
      const userMsg: ChatMessage = {
        role: 'user', content: newContent.trim(), isMarkdown: false, ts: Date.now(),
      };
      useChatStore.getState().updateStore((prev) => {
        const c = prev.chats[streamChatId];
        const msgs = [...(c?.messages || []), userMsg];
        return {
          chats: { ...prev.chats, [streamChatId]: { ...(c as any), messages: msgs, updatedAt: Date.now() } },
          order: [streamChatId, ...(prev.order || []).filter(x => x !== streamChatId)],
        };
      });

      const r = await editAndRegenerate(streamChatId, messageIndex, newContent.trim(), abortController.signal);
      if (!r.ok || !r.body) throw new Error(await r.text());

      await processRegenerateStream(r, streamChatId);
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        message.error(`编辑重发失败：${e?.message || String(e)}`);
      }
    } finally {
      abortControllersRef.current.delete(streamChatId);
      removeSendingChatId(streamChatId);
    }
  }

  async function smartSend(directMessage?: string) {
    return send(directMessage);
  }

  /** Abort the stream for a specific chat (defaults to the currently viewed chat). */
  function abort(chatId?: string) {
    const targetId = chatId || useChatStore.getState().currentChatId;
    const controller = abortControllersRef.current.get(targetId);
    if (controller) {
      controller.abort();
      abortControllersRef.current.delete(targetId);
    }
  }

  return { send: smartSend, abort, handleFileSelect, removeFile, fileUploadMap, regenerate, editAndResend };
}
