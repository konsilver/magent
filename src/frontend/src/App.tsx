import { useEffect, useRef, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  Layout, Button, Typography, Tag, Modal, Spin,
} from 'antd';
import 'highlight.js/styles/github.css';

/* styles loaded via styles/index.ts in main.tsx */
import type { PanelKey } from './types';
import { authFetch } from './api';
import type { SearchResultItem } from './api';
import { TOPIC_TAG_COLORS } from './utils/constants';
import { SCROLL_FOLLOW_THRESHOLD, distanceFromBottom, scrollElementToBottom } from './utils/scroll';
import { Sidebar } from './components/sidebar';
import { ChatArea, PromptHubPanel } from './components/chat';
import { ToolResultPanel } from './components/tool';
import { CatalogPanel, AbilityCenterPage, SkillsPage, McpPage } from './components/catalog';
import { AgentPanel } from './components/agent';
import { DocsPanel } from './components/docs';
import LabPanel from './components/lab/LabPanel';
import { MySpacePanel } from './components/myspace';
import { CanvasPanel } from './components/canvas';
import { CodeArtifactPanel } from './components/code-artifact';
import { ImagePreview, AuthExpiredModal } from './components/common';
import { SettingsPage } from './components/settings';
import { CreateKBModal, ReindexModal } from './components/kb';
import {
  useUIStore, useChatStore, useCatalogStore, useCanvasStore, useCodeArtifactStore, useAuthStore,
  useAutomationChatStore,
} from './stores';
import { RunTimelinePanel } from './components/automation/RunTimelinePanel';
import { useChatInit, useChatActions, useStreaming } from './hooks';
import { useMySpaceStore } from './stores/mySpaceStore';

const { Header, Content } = Layout;

const SLIDE_EASE = [0.16, 1, 0.3, 1] as const;

function SlidePanel({ show, panelKey, children, x = 24, duration = 0.25 }: {
  show: boolean; panelKey: string; children: ReactNode; x?: number; duration?: number;
}) {
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          key={panelKey}
          initial={{ opacity: 0, x }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x }}
          transition={{ duration, ease: SLIDE_EASE }}
          style={{ display: 'contents' }}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default function App() {
  const { authUser, authChecking } = useAuthStore();
  const {
    siderCollapsed, setSiderCollapsed,
    searchKeyword, setSearchResults, setSearchLoading,
    detailModal, setDetailModal,
    promptHubOpen,
  } = useUIStore();
  const {
    store, currentChatId, setCurrentChatId,
    toolResultPanel, setToolResultPanel,
    backendSessionIds,
  } = useChatStore();
  const { panel } = useCatalogStore();
  const setCatalogPanel = useCatalogStore((s) => s.setPanel);
  const setMySpaceTab = useMySpaceStore((s) => s.setTab);
  const canvasOpen = useCanvasStore((s) => s.isOpen);
  const closeCanvas = useCanvasStore((s) => s.closeCanvas);
  const codeArtifactOpen = useCodeArtifactStore((s) => s.isOpen);
  const closeCodeArtifact = useCodeArtifactStore((s) => s.closeCodeArtifact);
  const automationActiveGroup = useAutomationChatStore((s) => s.activeGroup);
  const exitAutomationChat = useAutomationChatStore((s) => s.exitAutomationChat);

  useEffect(() => {
    if (panel === 'share_records') {
      setMySpaceTab('shares');
      setCatalogPanel('my_space');
    }
  }, [panel, setCatalogPanel, setMySpaceTab]);

  // ── Notification polling (60s) — updates sidebar badge on 我的空间 ──
  const fetchNotifCount = useMySpaceStore((s) => s.fetchNotifications);
  useEffect(() => {
    if (!authUser) return;
    // Initial fetch
    void fetchNotifCount();
    const timer = setInterval(() => void fetchNotifCount(), 60_000);
    return () => clearInterval(timer);
  }, [authUser, fetchNotifCount]);

  // Close canvas/code-artifact when panel or chat changes
  useEffect(() => {
    closeCanvas();
    closeCodeArtifact();
  }, [panel, currentChatId, closeCanvas, closeCodeArtifact]);

  const chat = store.chats[currentChatId];
  // Treat a chat as non-empty while its messages are still loading from the
  // backend (backendSessionIds has the ID but messages array is empty).
  // This prevents the homepage / recommend-banner from flashing when switching
  // between history items.
  const isChatLoadingFromBackend = (!chat || chat.messages.length === 0) && backendSessionIds.has(currentChatId);
  const isEmptyChat = (!chat || chat.messages.length === 0) && !isChatLoadingFromBackend;
  // ChatArea only mounts the scrollable list once a message exists; the scroll effects
  // below must re-run when this flips so they attach to the new DOM (e.g. entering an
  // automation run chat before its messages have loaded).
  const hasMessages = !!chat?.messages.length;

  // ── Refs ──
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const chatListRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const userScrolledUpRef = useRef(false);
  // smooth 动画每一帧都会触发 scroll 事件，期间需要屏蔽监听器，
  // 否则会把动画中间态误判成"用户主动上滑"。
  const isAutoScrollingRef = useRef(false);

  // ── Initialization hook (auth, sessions, catalog, etc.) ──
  const { effectiveApiUrl, refreshCatalog, searchTimerRef } = useChatInit();

  // ── Chat actions hook ──
  const {
    newChat, deleteChat,
    toggleChatPinned, toggleChatFavorite,
    startRenameChat, commitRenameChat,
    exportChatRecord,
    createChatShare,
    generateSummary, generateClassification,
    setPanelSafe,
  } = useChatActions(effectiveApiUrl);

  // ── Streaming hook ──
  const { send: rawSend, abort, handleFileSelect, removeFile, regenerate, editAndResend } = useStreaming(
    effectiveApiUrl, generateSummary, generateClassification,
  );

  // 用户主动发送（输入框回车 / 点击追问问题）都视为"请把我带到底部"的明确意图，
  // 重置上滑标记，让下方 ResizeObserver 在新消息撑开列表后自动滚到底。
  const send = (text?: string) => {
    userScrolledUpRef.current = false;
    return rawSend(text);
  };

  // 跟踪用户是否主动上滑
  useEffect(() => {
    const content = document.querySelector<HTMLElement>('.jx-content');
    if (!content) return;
    const handleScroll = () => {
      if (isAutoScrollingRef.current) return;
      userScrolledUpRef.current = distanceFromBottom(content) > SCROLL_FOLLOW_THRESHOLD;
    };
    content.addEventListener('scroll', handleScroll, { passive: true });
    return () => content.removeEventListener('scroll', handleScroll);
  }, []);

  // 切换会话：重置跟随状态，smooth 动画滚到底（保留"从上往下拉"的视觉效果）。
  // 滚到底之后的追问/操作栏动画撑开由下方 ResizeObserver 兜底。
  // hasMessages 作为依赖：进入一个尚未拉取消息的会话时首次渲染 scrollHeight===clientHeight，
  // smooth 滚动是空操作；等消息异步加载后此 effect 再跑一次，确保真正落到底部。
  useEffect(() => {
    userScrolledUpRef.current = false;
    const content = document.querySelector<HTMLElement>('.jx-content');
    if (!content) return;
    isAutoScrollingRef.current = true;
    const raf = requestAnimationFrame(() => scrollElementToBottom(content, true));
    const release = () => { isAutoScrollingRef.current = false; };
    // scrollend 是现代浏览器事件（Chrome 114+/Firefox 109+/Safari 17+）；
    // 老浏览器兜底用一次 setTimeout 保险。
    content.addEventListener('scrollend', release, { once: true });
    const fallback = window.setTimeout(release, 1000);
    return () => {
      cancelAnimationFrame(raf);
      content.removeEventListener('scrollend', release);
      window.clearTimeout(fallback);
    };
  }, [currentChatId, hasMessages]);

  // 观察聊天列表尺寸变化：流式 chunk、追问/操作栏的 framer-motion 动画撑开高度时，
  // 只要用户没主动上滑就瞬时对齐到底部。相比 setTimeout 多阶段兜底，这里按"内容实际
  // 变化"驱动，没有时间魔法数、也不会在空闲时累积待处理的 setTimeout。
  // hasMessages 作为依赖：chatListRef 指向的 .jx-chatList 只在有消息时才挂载，
  // 列表从无到有时需要重新 observe 新节点。
  useEffect(() => {
    if (panel !== 'chat' || !hasMessages) return;
    const content = document.querySelector<HTMLElement>('.jx-content');
    const list = chatListRef.current;
    if (!content || !list || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      if (userScrolledUpRef.current || isAutoScrollingRef.current) return;
      content.scrollTop = content.scrollHeight;
    });
    ro.observe(list);
    return () => ro.disconnect();
  }, [panel, currentChatId, hasMessages]);

  // ── Search debounce ──
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    const kw = searchKeyword.trim();
    if (!kw) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    searchTimerRef.current = setTimeout(async () => {
      try {
        const r = await authFetch(
          `${effectiveApiUrl}/v1/chats/search?q=${encodeURIComponent(kw)}&scope=all&page_size=50`,
        );
        if (!r.ok) { setSearchResults([]); return; }
        const payload = await r.json();
        const items: any[] = payload?.data?.items || [];
        setSearchResults(
          items.map((raw: any) => ({
            id: String(raw.chat_id ?? ''),
            title: String(raw.title ?? '新对话'),
            createdAt: raw.created_at ? new Date(raw.created_at).getTime() : Date.now(),
            updatedAt: raw.updated_at ? new Date(raw.updated_at).getTime() : Date.now(),
            messages: [],
            favorite: Boolean(raw.favorite),
            pinned: Boolean(raw.pinned),
            businessTopic: raw.metadata?.businessTopic || '综合咨询',
            match_type: raw.match_type || 'title',
            matched_snippet: raw.matched_snippet || undefined,
          })),
        );
      } catch {
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300);
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [searchKeyword, effectiveApiUrl]);

  // ── Sidebar handlers ──
  const handleSelectChat = (id: string) => {
    // Automation virtual entries are handled by Sidebar via automationChatStore
    // — but if the user clicks a *normal* chat while in automation mode, exit first.
    if (!id.startsWith('automation:') && automationActiveGroup) {
      exitAutomationChat();
    }
    setPanelSafe('chat');
    setCurrentChatId(id);
    setToolResultPanel(null);
  };

  const handleSelectSearchResult = (item: SearchResultItem) => {
    if (automationActiveGroup) exitAutomationChat();
    useChatStore.getState().updateStore((prev) => {
      if (prev.chats[item.id]) return prev;
      return {
        chats: {
          ...prev.chats,
          [item.id]: {
            id: item.id,
            title: item.title || '新对话',
            createdAt: item.createdAt,
            updatedAt: item.updatedAt,
            messages: [],
            favorite: item.favorite,
            pinned: item.pinned,
            businessTopic: (item as any).businessTopic || '综合咨询',
          },
        },
        order: [item.id, ...prev.order.filter((x) => x !== item.id)],
      };
    });
    setPanelSafe('chat');
    setCurrentChatId(item.id);
    setToolResultPanel(null);
  };

  const handleSetPanel = (p: PanelKey) => setPanelSafe(p);

  const handleCapabilityClick = (capabilityId: string) => {
    if (capabilityId === 'knowledge') setPanelSafe('kb');
  };

  // ── Derived header text (for non-chat panels) ──
  const title = panel === 'ability_center' ? '能力中心'
    : panel === 'skills' ? '技能库'
    : panel === 'agents' ? '子智能体'
    : panel === 'mcp' ? 'MCP工具库'
    : panel === 'kb' ? '知识库'
    : panel === 'docs' ? '更新记录'
    : panel === 'lab' ? '实验室'
    : panel === 'settings' ? '系统设置'
    : panel === 'my_space' ? '我的空间'
    : '经信智能体';

  const hint = panel === 'ability_center' ? '智能体基础能力管理，包含技能库以及MCP工具库'
    : panel === 'skills' ? '启用/停用技能，并查看详细介绍、输入输出与示例。'
    : panel === 'agents' ? '选择与启用子智能体，并查看其职责边界与路由提示。'
    : panel === 'mcp' ? '管理 MCP 工具服务，并查看其作用范围与可靠性影响。'
    : panel === 'kb' ? '浏览知识库、查看文档列表，并支持文档内检索。'
    : panel === 'docs' ? '查看功能更新、能力中心与平台说明。'
    : panel === 'lab' ? 'AI 能力实验性应用'
    : '查看功能更新记录与能力中心说明。';

  // Whether to show the header: only for non-chat panels, or chat panels with messages
  const showHeader = panel !== 'chat'
    && panel !== 'settings'
    && panel !== 'skills'
    && panel !== 'mcp'
    && panel !== 'agents'
    && panel !== 'my_space'
    && panel !== 'ability_center'
    && panel !== 'lab';
  const showChatHeader = panel === 'chat' && !isEmptyChat;

  if (authChecking) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!authUser || window.location.pathname.startsWith('/mock-sso/login')) return null;

  return (
    <Layout style={{ height: '100%' }}>
      <Sidebar
        onNewChat={() => newChat(inputRef)}
        onDeleteChat={deleteChat}
        onTogglePinned={toggleChatPinned}
        onToggleFavorite={toggleChatFavorite}
        onStartRename={startRenameChat}
        onCommitRename={commitRenameChat}
        onExportChat={(id) => void exportChatRecord(id)}
        onSelectChat={handleSelectChat}
        onSelectSearchResult={handleSelectSearchResult}
        onSetPanel={handleSetPanel}
      />

      <Layout style={{ overflow: 'hidden', background: '#ffffff', paddingLeft: siderCollapsed ? 48 : 0, transition: 'padding-left 0.3s cubic-bezier(0.4, 0, 0.2, 1)', willChange: 'padding-left' }}>
        {/* Expand sidebar button — only when collapsed */}
        {siderCollapsed && (
          <button className="jx-expandSiderBtn" onClick={() => setSiderCollapsed(false)} aria-label="展开侧边栏">
            <img src="/home/收起.svg" alt="" style={{ width: 20, height: 20 }} />
          </button>
        )}

        {/* Non-chat panels: standard header */}
        {showHeader && (
          <Header className="jx-topbar" style={{ paddingInline: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0, flex: 1 }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <Typography.Title level={5} style={{ margin: 0, fontWeight: 900 }} ellipsis>{title}</Typography.Title>
                <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 2 }} ellipsis>{hint}</Typography.Text>
              </div>
            </div>
          </Header>
        )}

        {/* Chat panel with messages: minimal header with title */}
        {showChatHeader && (
          <div className="jx-chatTopbar">
            <span className="jx-chatTopbarTitle">{chat?.title || '对话'}</span>
            {chat?.agentName && (
              <Tag className="jx-headerTopicTag" color="blue">{chat.agentName}</Tag>
            )}
            {(chat as any)?.codeExecChat && (
              <Tag className="jx-headerTopicTag" color="green">代码执行</Tag>
            )}
            {(chat as any)?.planChat && (
              <Tag className="jx-headerTopicTag" color="blue">计划模式</Tag>
            )}
            {chat?.businessTopic && (
              <Tag className="jx-headerTopicTag" color={TOPIC_TAG_COLORS[chat.businessTopic] || 'default'}>{chat.businessTopic}</Tag>
            )}
          </div>
        )}


        <div className="jx-mainRow">
          <Content className="jx-content">
            <div className="jx-panel">
              {panel === 'chat' && (
                <ChatArea
                  send={send}
                  abort={abort}
                  exportChatRecord={exportChatRecord}
                  createChatShare={createChatShare}
                  onCapabilityClick={handleCapabilityClick}
                  handleFileSelect={handleFileSelect}
                  removeFile={removeFile}
                  regenerate={regenerate}
                  editAndResend={editAndResend}
                  inputRef={inputRef}
                  fileInputRef={fileInputRef}
                  chatListRef={chatListRef}
                  messagesEndRef={messagesEndRef}
                />
              )}
              {panel === 'ability_center' && <AbilityCenterPage />}
              {panel === 'skills' && <SkillsPage />}
              {panel === 'mcp' && <McpPage />}
              {panel === 'agents' && <AgentPanel />}
              {panel !== 'chat' && panel !== 'docs' && panel !== 'lab' && panel !== 'settings' && panel !== 'skills' && panel !== 'mcp' && panel !== 'agents' && panel !== 'share_records' && panel !== 'my_space' && panel !== 'ability_center' && <CatalogPanel />}
              {panel === 'docs' && <DocsPanel />}
              {panel === 'lab' && <LabPanel />}
              {panel === 'settings' && <SettingsPage />}
              {panel === 'my_space' && <MySpacePanel />}
            </div>
          </Content>

          <SlidePanel show={!!toolResultPanel && !promptHubOpen && !canvasOpen && !codeArtifactOpen && panel === 'chat'} panelKey="tool-result-panel" x={20} duration={0.22}>
            <ToolResultPanel />
          </SlidePanel>
          <SlidePanel show={promptHubOpen && !canvasOpen && !codeArtifactOpen && panel === 'chat'} panelKey="prompt-hub">
            <PromptHubPanel />
          </SlidePanel>
          <SlidePanel show={canvasOpen} panelKey="canvas" x={30} duration={0.28}>
            <CanvasPanel />
          </SlidePanel>
          <SlidePanel show={!canvasOpen && codeArtifactOpen} panelKey="code-artifact" x={30} duration={0.28}>
            <CodeArtifactPanel />
          </SlidePanel>

          {/* Automation run timeline — persistent panel (not mutually exclusive with SlidePanels) */}
          {automationActiveGroup && panel === 'chat' && <RunTimelinePanel />}
        </div>
      </Layout>

      {/* Global modals */}
      <Modal
        title={detailModal?.title}
        open={!!detailModal}
        onCancel={() => setDetailModal(null)}
        footer={<Button onClick={() => setDetailModal(null)}>关闭</Button>}
        width={640}
        className="jx-detailModal"
        destroyOnHidden
      >
        {detailModal?.body}
      </Modal>

      <ImagePreview />
      <CreateKBModal onCreated={() => void refreshCatalog()} />
      <ReindexModal />
      <AuthExpiredModal />
    </Layout>
  );
}
