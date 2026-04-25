import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Modal, Select, message } from 'antd';
import {
  SCROLL_TO_BOTTOM_BTN_THRESHOLD,
  distanceFromBottom,
  scrollElementToBottom,
} from '../../utils/scroll';
import { useChatStore } from '../../stores';
import { useAgentStore } from '../../stores/agentStore';
import { MessageBubble } from './MessageBubble';
import { InputArea } from './InputArea';

interface ChatAreaProps {
  send: (text?: string) => void;
  abort?: () => void;
  exportChatRecord: (id: string) => Promise<void>;
  createChatShare: (
    id: string,
    selectedTs: number[],
    expiryOption: '3d' | '15d' | '3m' | 'permanent'
  ) => Promise<{ share_id: string; preview_url: string; expires_at?: string | null; expiry_option: '3d' | '15d' | '3m' | 'permanent' }>;
  onCapabilityClick: (capabilityId: string) => void;
  handleFileSelect: (e: React.ChangeEvent<HTMLInputElement>, ref: React.RefObject<HTMLInputElement | null>) => void;
  removeFile: (index: number) => void;
  regenerate?: (messageIndex: number) => void;
  editAndResend?: (messageIndex: number, newContent: string) => void;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  chatListRef: React.RefObject<HTMLDivElement | null>;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
}

export function ChatArea({
  send, abort, exportChatRecord, createChatShare, onCapabilityClick, handleFileSelect, removeFile,
  regenerate, editAndResend,
  inputRef, fileInputRef, chatListRef, messagesEndRef,
}: ChatAreaProps) {
  type ShareExpiryOption = '3d' | '15d' | '3m' | 'permanent';
  const shareExpiryOptions = [
    { value: '3d', label: '3天' },
    { value: '15d', label: '15天' },
    { value: '3m', label: '3个月' },
    { value: 'permanent', label: '长期' },
  ] as const;
  const {
    store, currentChatId, setInput,
    shareSelectionMode, selectedShareMessageTs,
    pendingScrollMessageTs, setPendingScrollMessageTs,
    setQuotedFollowUp,
    clearShareSelection,
    chatsLoading,
    backendSessionIds,
    planMode,
  } = useChatStore();
  const [shareExpiryOption, setShareExpiryOption] = useState<ShareExpiryOption>('15d');
  const [shareExpiryModalOpen, setShareExpiryModalOpen] = useState(false);
  const [creatingShare, setCreatingShare] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const pendingShareExpiryRef = useRef<ShareExpiryOption>('15d');

  const chat = store.chats[currentChatId];

  useEffect(() => {
    const content = document.querySelector<HTMLElement>('.jx-content');
    if (!content) return;
    const handleScroll = () => {
      setShowScrollToBottom(distanceFromBottom(content) > SCROLL_TO_BOTTOM_BTN_THRESHOLD);
    };
    handleScroll();
    content.addEventListener('scroll', handleScroll, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(handleScroll) : null;
    ro?.observe(content);
    return () => {
      content.removeEventListener('scroll', handleScroll);
      ro?.disconnect();
    };
  }, []);

  const scrollToBottom = () => {
    const content = document.querySelector<HTMLElement>('.jx-content');
    if (content) {
      scrollElementToBottom(content, true);
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  };

  const applyQuickScenario = (prompt: string) => {
    setInput(prompt);
    inputRef.current?.focus();
  };

  useEffect(() => {
    useChatStore.getState().clearShareSelection();
    setQuotedFollowUp(null);
  }, [currentChatId]);

  useEffect(() => {
    if (!pendingScrollMessageTs) return;
    if (!chat?.messages.some((message) => message.ts === pendingScrollMessageTs)) return;

    const timer = window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>(`[data-message-ts="${pendingScrollMessageTs}"]`);
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setPendingScrollMessageTs(null);
    }, 120);

    return () => window.clearTimeout(timer);
  }, [chat?.messages, pendingScrollMessageTs, setPendingScrollMessageTs]);

  // ── Resolve sub-agent details for welcome page ──
  const { agents } = useAgentStore();
  const agentDetail = useMemo(() => {
    const aid = chat?.agentId;
    if (!aid) return null;
    return agents.find((a) => a.agent_id === aid) || null;
  }, [chat?.agentId, agents]);

  // ── Resolve hero text: sub-agent uses its own name/description ──
  const isAgentChat = !!(chat?.agentId);
  const heroTitle = isAgentChat ? (chat.agentName || '子智能体') : '经信智能体';
  const heroSubtitle = isAgentChat
    ? (agentDetail?.description || agentDetail?.welcome_message || '专业子智能体')
    : '基于AI能力的经信业务场景化智能工作平台';
  const suggestedQuestions = isAgentChat ? (agentDetail?.suggested_questions || []) : [];
  const inputPlaceholder = isAgentChat
    ? `向${chat.agentName || '子智能体'}提问...`
    : planMode
      ? '计划模式已开启，请输入你的任务目标'
      : '请输入你的问题，按Enter发送，Shift+Enter换行';

  const hasNoMessages = !chat || chat.messages.length === 0;

  // Show a spinner when:
  // 1. The session list is still being fetched (initial page load), OR
  // 2. The current chat exists on the backend but its messages haven't arrived
  //    yet — covers both "not started" and "in-flight" states.  Without this
  //    the home page flashes every time the user clicks a history item.
  const isMessagesLoading = hasNoMessages && backendSessionIds.has(currentChatId);
  if (hasNoMessages && (chatsLoading || isMessagesLoading)) {
    return (
      <div className="jx-emptyPage jx-chatSkeleton">
        <div className="jx-chatSkeletonCenter">
          <div className="jx-chatSkeletonHero">
            <div className="jx-skeletonBlock jx-chatSkeletonTitle" />
            <div className="jx-skeletonBlock jx-chatSkeletonSubtitle" />
          </div>
          <div className="jx-skeletonBlock jx-chatSkeletonInput" />
          <div className="jx-chatSkeletonCards">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="jx-skeletonBlock jx-chatSkeletonCard" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (hasNoMessages) {
    return (
      <div className="jx-emptyPage">
        <div className="jx-emptyCenter">
          <div className="jx-heroBg">
            <img src="/home/标题背景图片.png" alt="" className="jx-heroBgImg" />
            <h1 className="jx-heroTitle">{heroTitle}</h1>
            <p className="jx-heroSubtitle">{heroSubtitle}</p>
          </div>

          <div className="jx-homeInput">
            <InputArea
              inputRef={inputRef}
              fileInputRef={fileInputRef}
              send={() => send()}
              abort={abort}
              handleFileSelect={handleFileSelect}
              removeFile={removeFile}
              placeholder={inputPlaceholder}
              disableMention={isAgentChat}
            />
          </div>

          {/* Quick pills: only sub-agents show suggested questions */}
          {isAgentChat && suggestedQuestions.length > 0 && (
            <div className="jx-quickPills">
              {suggestedQuestions.map((prompt: string) => (
                <button key={prompt} className="jx-quickPill" onClick={() => applyQuickScenario(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
          )}


        </div>
        {!isAgentChat && (
          <div className="jx-aiDisclaimer">
            人工智能省部共建协同创新中心(浙江大学)
            <br />
            CCAI宁波中心产业链智能实验室建设
          </div>
        )}
      </div>
    );
  }

  const handleCreateShare = async () => {
    if (selectedShareMessageTs.size === 0) {
      message.warning('请先选择要分享的对话记录');
      return;
    }

    pendingShareExpiryRef.current = shareExpiryOption;
    setShareExpiryModalOpen(true);
  };

  const confirmCreateShare = async () => {
    if (selectedShareMessageTs.size === 0) {
      message.warning('请先选择要分享的对话记录');
      return;
    }

    const selectedExpiryOption = pendingShareExpiryRef.current;
    setCreatingShare(true);
    try {
      const result = await createChatShare(currentChatId, Array.from(selectedShareMessageTs), selectedExpiryOption);
      const targetUrl = new URL(result.preview_url, window.location.origin).toString();
      window.open(targetUrl, '_blank', 'noopener');
      message.success('分享链接已生成');
      setShareExpiryModalOpen(false);
      clearShareSelection();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成分享链接失败');
    } finally {
      setCreatingShare(false);
    }
  };

  return (
    <div className="jx-chatWrap">
      <Modal
        title="有效期设置"
        open={shareExpiryModalOpen}
        onOk={() => { void confirmCreateShare(); }}
        onCancel={() => {
          if (!creatingShare) {
            setShareExpiryModalOpen(false);
          }
        }}
        okText="生成链接"
        cancelText="取消"
        confirmLoading={creatingShare}
        destroyOnClose
      >
        <div style={{ display: 'grid', gap: 12 }}>
          <span>请选择分享链接的有效时间</span>
          <Select
            value={shareExpiryOption}
            onChange={(value) => {
              const nextValue = value as ShareExpiryOption;
              setShareExpiryOption(nextValue);
              pendingShareExpiryRef.current = nextValue;
            }}
            options={shareExpiryOptions.map((option) => ({ value: option.value, label: option.label }))}
          />
        </div>
      </Modal>
      {shareSelectionMode && (
        <div className="jx-shareSelectionBar">
          <div className="jx-shareSelectionInfo">
            <span className="jx-shareSelectionTitle">分享记录选择</span>
            <span className="jx-shareSelectionCount">{`已选择 ${selectedShareMessageTs.size} 条记录`}</span>
          </div>
          <div className="jx-shareSelectionActions">
            <button className="jx-shareSelectionSecondaryBtn" onClick={() => clearShareSelection()}>
              取消
            </button>
            <button
              className="jx-shareSelectionPrimaryBtn"
              onClick={() => { void handleCreateShare(); }}
              disabled={selectedShareMessageTs.size === 0}
            >
              生成分享链接
            </button>
          </div>
        </div>
      )}
      <div className="jx-chatList" ref={chatListRef}>
        {(chat.messages || []).map((m, idx) => (
          <MessageBubble
            key={m.ts}
            m={m}
            messageIndex={idx}
            currentChatId={currentChatId}
            send={send}
            exportChatRecord={exportChatRecord}
            regenerate={regenerate}
            editAndResend={editAndResend}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>
      {showScrollToBottom && (
        <button
          type="button"
          className="jx-scrollToBottomBtn"
          onClick={scrollToBottom}
          aria-label="回到底部"
          title="回到底部"
        >
          <img src="/home/箭头-下.svg" alt="" className="jx-scrollToBottomIcon" />
        </button>
      )}
      <InputArea
        inputRef={inputRef}
        fileInputRef={fileInputRef}
        send={() => send()}
        abort={abort}
        handleFileSelect={handleFileSelect}
        removeFile={removeFile}
        disableMention={isAgentChat}
      />
    </div>
  );
}
