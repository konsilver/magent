import type { ReactNode } from 'react';
import { create } from 'zustand';
import type { UpdateEntry, CapItem, UpdateCategory } from '../types';
import type { SearchResultItem } from '../api';

export type HistoryTimeFilter = 'all' | 'today' | '7d' | '30d';
export type DocsSubTab = 'updates' | 'capabilities';
export type UpdateFilter = '全部' | UpdateCategory;

const DISPATCH_PROCESS_STORAGE_KEY = 'jingxin_dispatch_process_visible';

function loadDispatchProcessVisible(): boolean {
  if (typeof window === 'undefined') return false;
  const raw = window.localStorage.getItem(DISPATCH_PROCESS_STORAGE_KEY);
  return raw == null ? false : raw !== 'false';
}

interface UIState {
  siderCollapsed: boolean;

  // ── Search ──
  searchMode: boolean;
  searchKeyword: string;
  searchResults: SearchResultItem[];
  searchLoading: boolean;

  // ── History filtering ──
  historyTimeFilter: HistoryTimeFilter;
  historyTopicFilter: string;
  editingChatId: string | null;
  editingTitle: string;

  // ── Image preview ──
  previewImage: { url: string; name: string } | null;

  // ── Detail modal ──
  detailModal: { title: string; body: ReactNode } | null;

  // ── Recommend banner ──
  recommendBarVisible: boolean;

  // ── Docs panel ──
  activeDocsSubTab: DocsSubTab;
  activeUpdateFilter: UpdateFilter;
  featureUpdates: UpdateEntry[];
  capabilitiesList: CapItem[];

  // ── Prompt Hub ──
  promptHubOpen: boolean;
  dispatchProcessVisible: boolean;

  // ── Actions ──
  setSiderCollapsed: (v: boolean) => void;
  toggleSider: () => void;

  setSearchMode: (v: boolean) => void;
  setSearchKeyword: (keyword: string) => void;
  setSearchResults: (results: SearchResultItem[]) => void;
  setSearchLoading: (v: boolean) => void;
  exitSearch: () => void;

  setHistoryTimeFilter: (filter: HistoryTimeFilter) => void;
  setHistoryTopicFilter: (topic: string) => void;
  setEditingChatId: (id: string | null) => void;
  setEditingTitle: (title: string) => void;

  setRecommendBarVisible: (v: boolean) => void;

  setPreviewImage: (image: { url: string; name: string } | null) => void;
  setDetailModal: (modal: { title: string; body: ReactNode } | null) => void;

  setActiveDocsSubTab: (tab: DocsSubTab) => void;
  setActiveUpdateFilter: (filter: UpdateFilter) => void;
  setFeatureUpdates: (updates: UpdateEntry[]) => void;
  setCapabilitiesList: (items: CapItem[]) => void;

  setPromptHubOpen: (v: boolean) => void;
  setDispatchProcessVisible: (v: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
  siderCollapsed: false,

  searchMode: false,
  searchKeyword: '',
  searchResults: [],
  searchLoading: false,

  historyTimeFilter: 'all',
  historyTopicFilter: 'all',
  editingChatId: null,
  editingTitle: '',

  recommendBarVisible: true,

  previewImage: null,
  detailModal: null,

  activeDocsSubTab: 'updates',
  activeUpdateFilter: '全部',
  featureUpdates: [],
  capabilitiesList: [],

  promptHubOpen: false,
  dispatchProcessVisible: loadDispatchProcessVisible(),

  setSiderCollapsed: (v) => set({ siderCollapsed: v }),
  toggleSider: () => set((s) => ({ siderCollapsed: !s.siderCollapsed })),

  setSearchMode: (v) => set({ searchMode: v }),
  setSearchKeyword: (keyword) => set({ searchKeyword: keyword }),
  setSearchResults: (results) => set({ searchResults: results }),
  setSearchLoading: (v) => set({ searchLoading: v }),
  exitSearch: () => set({ searchMode: false, searchKeyword: '', searchResults: [], searchLoading: false }),

  setHistoryTimeFilter: (filter) => set({ historyTimeFilter: filter }),
  setHistoryTopicFilter: (topic) => set({ historyTopicFilter: topic }),
  setEditingChatId: (id) => set({ editingChatId: id }),
  setEditingTitle: (title) => set({ editingTitle: title }),

  setRecommendBarVisible: (v) => set({ recommendBarVisible: v }),

  setPreviewImage: (image) => set({ previewImage: image }),
  setDetailModal: (modal) => set({ detailModal: modal }),

  setActiveDocsSubTab: (tab) => set({ activeDocsSubTab: tab }),
  setActiveUpdateFilter: (filter) => set({ activeUpdateFilter: filter }),
  setFeatureUpdates: (updates) => set({ featureUpdates: updates }),
  setCapabilitiesList: (items) => set({ capabilitiesList: items }),

  setPromptHubOpen: (v) => set({ promptHubOpen: v }),
  setDispatchProcessVisible: (v) => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(DISPATCH_PROCESS_STORAGE_KEY, String(v));
    }
    set({ dispatchProcessVisible: v });
  },
}));
