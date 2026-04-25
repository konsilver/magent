import { create } from 'zustand';
import type { ChatItem, ChatMessage, ChatStore as ChatStoreData } from '../types';
import { loadChatStore, saveChatStore, nowId } from '../storage';

const CURRENT_CHAT_KEY = 'jingxin_current_chat_id';
const PENDING_SCROLL_MESSAGE_TS_KEY = 'jingxin_pending_scroll_message_ts';

function loadCurrentChatId() {
  if (typeof window === 'undefined') return nowId('chat');
  return window.localStorage.getItem(CURRENT_CHAT_KEY) || nowId('chat');
}

function saveCurrentChatId(chatId: string) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(CURRENT_CHAT_KEY, chatId);
}

function loadPendingScrollMessageTs() {
  if (typeof window === 'undefined') return null;
  const raw = window.localStorage.getItem(PENDING_SCROLL_MESSAGE_TS_KEY);
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function savePendingScrollMessageTs(ts: number | null) {
  if (typeof window === 'undefined') return;
  if (ts === null) {
    window.localStorage.removeItem(PENDING_SCROLL_MESSAGE_TS_KEY);
    return;
  }
  window.localStorage.setItem(PENDING_SCROLL_MESSAGE_TS_KEY, String(ts));
}

interface ChatState {
  /** All chat sessions keyed by id */
  store: ChatStoreData;
  /** Ref-like mutable mirror of store for use in closures */
  storeRef: ChatStoreData;
  /** Currently active chat id */
  currentChatId: string;
  /** Input text */
  input: string;
  /** Whether the *current* chat is streaming (derived from sendingChatIds) */
  sending: boolean;
  /** Set of chat IDs that are currently streaming responses.
   *  Multiple chats can stream in parallel — e.g. user starts chat A,
   *  switches to a new chat B, and sends while A is still running. */
  sendingChatIds: Set<string>;
  /** Set of thinking block IDs that are expanded */
  expandedThinking: Set<string>;
  /** Whether thinking mode is enabled */
  thinkingMode: boolean;
  /** Tool result detail panel state */
  toolResultPanel: {
    key: string;
    toolName: string;
    displayName: string;
    output: unknown;
    summary?: string;
  } | null;
  /** Copied message index */
  copiedMsg: number | null;
  /** Whether chats are loading from backend */
  chatsLoading: boolean;
  /** Feedback map: message timestamp → feedback type */
  feedbackMap: Record<number, 'like' | 'dislike'>;
  /** Message being disliked (for comment modal) */
  dislikingTs: number | null;
  /** Dislike comment text */
  dislikeComment: string;
  /** Tool display names from backend */
  toolDisplayNames: Record<string, string>;
  /** Backend session IDs (tracks which chats exist on the server) */
  backendSessionIds: Set<string>;
  /** Chat IDs whose messages have been loaded from backend */
  loadedMsgIds: Set<string>;
  /** Whether share selection mode is enabled */
  shareSelectionMode: boolean;
  /** Selected message timestamps for share generation */
  selectedShareMessageTs: Set<number>;
  /** Message timestamp to scroll into view after jumping from share records */
  pendingScrollMessageTs: number | null;
  /** Quoted message used for follow-up prompting */
  quotedFollowUp: { text: string; ts: number } | null;
  /** Active skill selected via / slash command */
  activeSkill: { id: string; name: string } | null;
  /** Active @mention selected via popup */
  activeMention: { name: string } | null;
  /** Whether plan mode is enabled (计划模式) */
  planMode: boolean;
  /** Current plan ID being executed in plan mode */
  currentPlanId: string | null;
  /** Whether code execution mode is active (实验室代码执行) */
  codeExecMode: boolean;
  /** Timestamp of user message being edited */
  editingMessageTs: number | null;
  /** Monotonic counter incremented after each fetchSessions completes;
   *  used as an effect dependency to re-trigger the lazy message loader. */
  sessionLoadEpoch: number;

  // ── Actions ──
  setStore: (store: ChatStoreData) => void;
  updateStore: (updater: (prev: ChatStoreData) => ChatStoreData) => void;
  setCurrentChatId: (id: string) => void;
  setInput: (input: string) => void;
  setSending: (v: boolean) => void;
  /** Mark a chat id as currently streaming. Adds to set + updates derived `sending`. */
  addSendingChatId: (id: string) => void;
  /** Mark a chat id as no longer streaming. Removes from set + updates derived `sending`. */
  removeSendingChatId: (id: string) => void;
  toggleThinking: (id: string) => void;
  setThinkingMode: (v: boolean) => void;
  setToolResultPanel: (panel: ChatState['toolResultPanel']) => void;
  setCopiedMsg: (ts: number | null) => void;
  setChatsLoading: (v: boolean) => void;
  setFeedbackMap: (map: Record<number, 'like' | 'dislike'>) => void;
  setDislikingTs: (ts: number | null) => void;
  setDislikeComment: (comment: string) => void;
  setToolDisplayNames: (names: Record<string, string>) => void;
  addBackendSessionId: (id: string) => void;
  removeBackendSessionId: (id: string) => void;
  clearBackendSessionIds: () => void;
  addLoadedMsgId: (id: string) => void;
  removeLoadedMsgId: (id: string) => void;
  clearLoadedMsgIds: () => void;
  setShareSelectionMode: (v: boolean) => void;
  toggleShareMessageTs: (ts: number) => void;
  clearShareSelection: () => void;
  /** 进入"分享选择"模式并默认勾选传入的消息 ts 列表 */
  startShareSelectionWithAll: (tsList: number[]) => void;
  setPendingScrollMessageTs: (ts: number | null) => void;
  setQuotedFollowUp: (quote: { text: string; ts: number } | null) => void;
  setActiveSkill: (skill: { id: string; name: string } | null) => void;
  setActiveMention: (mention: { name: string } | null) => void;
  setPlanMode: (v: boolean) => void;
  setCurrentPlanId: (id: string | null) => void;
  setCodeExecMode: (v: boolean) => void;
  setEditingMessageTs: (ts: number | null) => void;
  bumpSessionLoadEpoch: () => void;
  /** Truncate messages from the given timestamp (inclusive) */
  truncateMessagesFrom: (chatId: string, ts: number) => void;

  /** Create a new chat and switch to it */
  newChat: () => void;
  /** Delete a chat by id */
  deleteChat: (id: string) => void;
  /** Update messages for a given chat */
  updateMessages: (chatId: string, messages: ChatMessage[]) => void;
  /** Get the current chat item */
  currentChat: () => ChatItem | undefined;
}

export const useChatStore = create<ChatState>((set, get) => ({
  store: loadChatStore(),
  storeRef: loadChatStore(),
  currentChatId: loadCurrentChatId(),
  input: '',
  sending: false,
  sendingChatIds: new Set(),
  expandedThinking: new Set(),
  thinkingMode: false,
  toolResultPanel: null,
  copiedMsg: null,
  chatsLoading: false,
  feedbackMap: {},
  dislikingTs: null,
  dislikeComment: '',
  toolDisplayNames: {},
  backendSessionIds: new Set(),
  loadedMsgIds: new Set(),
  shareSelectionMode: false,
  selectedShareMessageTs: new Set(),
  pendingScrollMessageTs: loadPendingScrollMessageTs(),
  quotedFollowUp: null,
  activeSkill: null,
  activeMention: null,
  planMode: false,
  currentPlanId: null,
  codeExecMode: false,
  editingMessageTs: null,
  sessionLoadEpoch: 0,

  setStore: (store) => {
    set({ store, storeRef: store });
    saveChatStore(store);
  },
  updateStore: (updater) => {
    const next = updater(get().store);
    set({ store: next, storeRef: next });
    saveChatStore(next);
  },
  setCurrentChatId: (id) => {
    saveCurrentChatId(id);
    const chat = get().store.chats[id];
    set({ currentChatId: id, sending: get().sendingChatIds.has(id), planMode: !!chat?.planChat, codeExecMode: !!chat?.codeExecChat, currentPlanId: null });
  },
  setInput: (input) => set({ input }),
  setSending: (v) => set({ sending: v }),
  addSendingChatId: (id) => set((s) => {
    const next = new Set(s.sendingChatIds);
    next.add(id);
    return { sendingChatIds: next, sending: next.has(s.currentChatId) };
  }),
  removeSendingChatId: (id) => set((s) => {
    const next = new Set(s.sendingChatIds);
    next.delete(id);
    return { sendingChatIds: next, sending: next.has(s.currentChatId) };
  }),
  toggleThinking: (id) => {
    const next = new Set(get().expandedThinking);
    if (next.has(id)) next.delete(id); else next.add(id);
    set({ expandedThinking: next });
  },
  setThinkingMode: (v) => set({ thinkingMode: v }),
  setToolResultPanel: (panel) => set({ toolResultPanel: panel }),
  setCopiedMsg: (ts) => set({ copiedMsg: ts }),
  setChatsLoading: (v) => set({ chatsLoading: v }),
  setFeedbackMap: (map) => set({ feedbackMap: map }),
  setDislikingTs: (ts) => set({ dislikingTs: ts }),
  setDislikeComment: (comment) => set({ dislikeComment: comment }),
  setToolDisplayNames: (names) => set({ toolDisplayNames: names }),
  addBackendSessionId: (id) => set((s) => {
    const next = new Set(s.backendSessionIds);
    next.add(id);
    return { backendSessionIds: next };
  }),
  removeBackendSessionId: (id) => set((s) => {
    const next = new Set(s.backendSessionIds);
    next.delete(id);
    return { backendSessionIds: next };
  }),
  clearBackendSessionIds: () => set({ backendSessionIds: new Set() }),
  addLoadedMsgId: (id) => set((s) => {
    const next = new Set(s.loadedMsgIds);
    next.add(id);
    return { loadedMsgIds: next };
  }),
  removeLoadedMsgId: (id) => set((s) => {
    const next = new Set(s.loadedMsgIds);
    next.delete(id);
    return { loadedMsgIds: next };
  }),
  clearLoadedMsgIds: () => set({ loadedMsgIds: new Set() }),
  setShareSelectionMode: (v) => set((s) => ({
    shareSelectionMode: v,
    selectedShareMessageTs: v ? s.selectedShareMessageTs : new Set(),
  })),
  toggleShareMessageTs: (ts) => set((s) => {
    const next = new Set(s.selectedShareMessageTs);
    if (next.has(ts)) next.delete(ts); else next.add(ts);
    return { selectedShareMessageTs: next };
  }),
  clearShareSelection: () => set({ shareSelectionMode: false, selectedShareMessageTs: new Set() }),
  startShareSelectionWithAll: (tsList) => set({
    shareSelectionMode: true,
    selectedShareMessageTs: new Set(tsList),
  }),
  setPendingScrollMessageTs: (ts) => {
    savePendingScrollMessageTs(ts);
    set({ pendingScrollMessageTs: ts });
  },
  setQuotedFollowUp: (quote) => set({ quotedFollowUp: quote }),
  setActiveSkill: (skill) => set({ activeSkill: skill }),
  setActiveMention: (mention) => set({ activeMention: mention }),
  setPlanMode: (v) => set({ planMode: v }),
  setCodeExecMode: (v) => set({ codeExecMode: v }),
  setCurrentPlanId: (id) => set({ currentPlanId: id }),
  setEditingMessageTs: (ts) => set({ editingMessageTs: ts }),
  bumpSessionLoadEpoch: () => set((s) => ({ sessionLoadEpoch: s.sessionLoadEpoch + 1 })),
  truncateMessagesFrom: (chatId, ts) => {
    const { store } = get();
    const chat = store.chats[chatId];
    if (!chat) return;
    const filtered = chat.messages.filter((m) => m.ts < ts);
    const next: ChatStoreData = {
      ...store,
      chats: { ...store.chats, [chatId]: { ...chat, messages: filtered, updatedAt: Date.now() } },
    };
    set({ store: next, storeRef: next });
    saveChatStore(next);
  },

  newChat: () => {
    const id = nowId('chat');
    saveCurrentChatId(id);
    set({
      currentChatId: id,
      input: '',
      sending: false,
      expandedThinking: new Set(),
      shareSelectionMode: false,
      selectedShareMessageTs: new Set(),
      quotedFollowUp: null,
      activeSkill: null,
      activeMention: null,
    });
  },

  deleteChat: (id) => {
    const { store, currentChatId } = get();
    const { [id]: _, ...rest } = store.chats;
    const next: ChatStoreData = {
      chats: rest,
      order: store.order.filter((oid) => oid !== id),
    };
    set({ store: next, storeRef: next });
    saveChatStore(next);
    if (currentChatId === id) {
      const newId = next.order[0] || nowId('chat');
      saveCurrentChatId(newId);
      set({ currentChatId: newId, shareSelectionMode: false, selectedShareMessageTs: new Set(), quotedFollowUp: null, activeSkill: null, activeMention: null });
    }
  },

  updateMessages: (chatId, messages) => {
    const { store } = get();
    const chat = store.chats[chatId];
    if (!chat) return;
    const next: ChatStoreData = {
      ...store,
      chats: { ...store.chats, [chatId]: { ...chat, messages } },
    };
    set({ store: next, storeRef: next });
    saveChatStore(next);
  },

  currentChat: () => {
    const { store, currentChatId } = get();
    return store.chats[currentChatId];
  },
}));
