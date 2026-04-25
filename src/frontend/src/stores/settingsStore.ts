import { create } from 'zustand';
import type { MemoryItem } from '../types';
import { getMemories, deleteMemory, clearAllMemories, getMemorySettings, updateMemorySettings, updateMemoryWriteSettings, updateRerankerSettings } from '../api';
import { message } from 'antd';

interface SettingsState {
  settingsOpen: boolean;
  memoryEnabled: boolean;
  memoryWriteEnabled: boolean;
  memoryItems: MemoryItem[];
  memoryPanelOpen: boolean;
  memoryLoading: boolean;
  rerankerEnabled: boolean;
  rerankerAvailable: boolean;

  setSettingsOpen: (v: boolean) => void;
  setMemoryEnabled: (v: boolean) => void;
  setMemoryWriteEnabled: (v: boolean) => void;
  setMemoryItems: (items: MemoryItem[]) => void;
  setMemoryPanelOpen: (v: boolean) => void;
  setMemoryLoading: (v: boolean) => void;
  setRerankerEnabled: (v: boolean) => void;
  setRerankerAvailable: (v: boolean) => void;

  /** Load memory settings from backend */
  loadMemorySettings: () => Promise<void>;
  /** Toggle memory retrieval on/off (syncs to backend) */
  toggleMemory: (enabled: boolean) => Promise<void>;
  /** Toggle memory write on/off (syncs to backend) */
  toggleMemoryWrite: (enabled: boolean) => Promise<void>;
  /** Toggle reranker on/off (syncs to backend) */
  toggleReranker: (enabled: boolean) => Promise<void>;
  /** Load memory items from backend */
  loadMemories: () => Promise<void>;
  /** Delete a single memory item */
  removeMemory: (id: string) => Promise<void>;
  /** Clear all memories */
  clearMemories: () => Promise<void>;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settingsOpen: false,
  memoryEnabled: localStorage.getItem('jingxin_memory_enabled') === 'true',
  memoryWriteEnabled: localStorage.getItem('jingxin_memory_write_enabled') === 'true',
  memoryItems: [],
  memoryPanelOpen: false,
  memoryLoading: false,
  rerankerEnabled: false,
  rerankerAvailable: false,

  setSettingsOpen: (v) => set({ settingsOpen: v }),
  setMemoryEnabled: (v) => {
    localStorage.setItem('jingxin_memory_enabled', String(v));
    set({ memoryEnabled: v });
  },
  setMemoryWriteEnabled: (v) => {
    localStorage.setItem('jingxin_memory_write_enabled', String(v));
    set({ memoryWriteEnabled: v });
  },
  setMemoryItems: (items) => set({ memoryItems: items }),
  setMemoryPanelOpen: (v) => set({ memoryPanelOpen: v }),
  setMemoryLoading: (v) => set({ memoryLoading: v }),
  setRerankerEnabled: (v) => set({ rerankerEnabled: v }),
  setRerankerAvailable: (v) => set({ rerankerAvailable: v }),

  loadMemorySettings: async () => {
    try {
      const settings = await getMemorySettings();
      set({
        memoryEnabled: settings.memory_enabled,
        memoryWriteEnabled: settings.memory_write_enabled,
        rerankerEnabled: settings.reranker_enabled,
        rerankerAvailable: settings.reranker_available,
      });
      localStorage.setItem('jingxin_memory_enabled', String(settings.memory_enabled));
      localStorage.setItem('jingxin_memory_write_enabled', String(settings.memory_write_enabled));
    } catch (e) {
      console.error('Failed to load memory settings:', e);
    }
  },

  toggleMemory: async (enabled) => {
    const prev = get().memoryEnabled;
    set({ memoryEnabled: enabled });
    localStorage.setItem('jingxin_memory_enabled', String(enabled));
    try {
      await updateMemorySettings(enabled);
    } catch {
      set({ memoryEnabled: prev });
      localStorage.setItem('jingxin_memory_enabled', String(prev));
      message.error('记忆设置更新失败');
    }
  },

  toggleMemoryWrite: async (enabled) => {
    const prev = get().memoryWriteEnabled;
    set({ memoryWriteEnabled: enabled });
    localStorage.setItem('jingxin_memory_write_enabled', String(enabled));
    try {
      await updateMemoryWriteSettings(enabled);
    } catch {
      set({ memoryWriteEnabled: prev });
      localStorage.setItem('jingxin_memory_write_enabled', String(prev));
      message.error('写入记忆设置更新失败');
    }
  },

  toggleReranker: async (enabled) => {
    const prev = get().rerankerEnabled;
    set({ rerankerEnabled: enabled });
    try {
      await updateRerankerSettings(enabled);
    } catch {
      set({ rerankerEnabled: prev });
      message.error('重排序设置更新失败');
    }
  },

  loadMemories: async () => {
    set({ memoryLoading: true });
    try {
      const data = await getMemories();
      set({ memoryItems: data.items, memoryLoading: false });
    } catch {
      set({ memoryLoading: false });
      message.error('加载记忆失败');
    }
  },

  removeMemory: async (id) => {
    try {
      await deleteMemory(id);
      set((s) => ({ memoryItems: s.memoryItems.filter((m) => m.id !== id) }));
    } catch {
      message.error('删除记忆失败');
    }
  },

  clearMemories: async () => {
    try {
      await clearAllMemories();
      set({ memoryItems: [] });
      message.success('已清除所有记忆');
    } catch {
      message.error('清除记忆失败');
    }
  },
}));
