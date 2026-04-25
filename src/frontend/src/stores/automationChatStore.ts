import { create } from 'zustand';
import type { AutomationChatGroup, AutomationRun, AutomationTask } from '../types';
import { useChatStore } from './chatStore';
import { useCatalogStore } from './catalogStore';

const AUTOMATION_SIDEBAR_PREFS_KEY = 'jingxin_automation_sidebar_prefs_v1';

interface AutomationSidebarPref {
  pinned?: boolean;
  favorite?: boolean;
}

function loadSidebarPrefs(): Record<string, AutomationSidebarPref> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(AUTOMATION_SIDEBAR_PREFS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed as Record<string, AutomationSidebarPref> : {};
  } catch {
    return {};
  }
}

function saveSidebarPrefs(prefs: Record<string, AutomationSidebarPref>) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(AUTOMATION_SIDEBAR_PREFS_KEY, JSON.stringify(prefs));
}

interface AutomationChatState {
  /** Currently active automation chat group (null = normal chat mode) */
  activeGroup: AutomationChatGroup | null;
  /** Currently selected run ID within the group */
  selectedRunId: string | null;
  /** Sidebar-activated automation tasks (for sidebar display) */
  sidebarTasks: AutomationTask[];
  /** Local sidebar-only preferences for automation entries */
  sidebarPrefs: Record<string, AutomationSidebarPref>;

  enterAutomationChat: (
    taskId: string,
    taskName: string,
    runs: AutomationRun[],
    initialRunId?: string,
  ) => void;
  exitAutomationChat: () => void;
  selectRun: (runId: string) => void;
  setRuns: (runs: AutomationRun[]) => void;
  setSidebarTasks: (tasks: AutomationTask[]) => void;
  updateSidebarTask: (task: AutomationTask) => void;
  renameActiveGroup: (taskId: string, taskName: string) => void;
  toggleSidebarPinned: (taskId: string) => void;
  toggleSidebarFavorite: (taskId: string) => void;
  setSidebarFavorite: (taskId: string, favorite: boolean) => void;
}

export const useAutomationChatStore = create<AutomationChatState>((set, get) => ({
  activeGroup: null,
  selectedRunId: null,
  sidebarTasks: [],
  sidebarPrefs: loadSidebarPrefs(),

  enterAutomationChat: (taskId, taskName, runs, initialRunId) => {
    // Find the target run: either the specified one, or the latest completed run
    const completedRuns = runs.filter(
      (r) => r.status !== 'running' && r.chat_id,
    );
    let targetRun = initialRunId
      ? completedRuns.find((r) => r.run_id === initialRunId)
      : completedRuns[0]; // runs are ordered desc by started_at

    if (!targetRun && completedRuns.length > 0) {
      targetRun = completedRuns[0];
    }

    const latestRunAt = completedRuns.length > 0
      ? new Date(completedRuns[0].started_at).getTime()
      : Date.now();

    set({
      activeGroup: {
        taskId,
        taskName,
        runs,
        latestCompletedChatId: targetRun?.chat_id || null,
        latestRunAt,
      },
      selectedRunId: targetRun?.run_id || null,
    });

    // Switch to the target chat and panel.
    // 1. Ensure a stub chat entry exists in store.chats so the lazy-load
    //    effect can write fetched messages into it (without this, the
    //    `if (!c) return prev` guard discards them).
    // 2. Mark the chat as backend-known so ChatArea shows the loading
    //    skeleton instead of the home page while messages load.
    if (targetRun?.chat_id) {
      const chatStore = useChatStore.getState();
      chatStore.updateStore((prev) => {
        if (prev.chats[targetRun.chat_id!]) return prev;
        return {
          chats: {
            ...prev.chats,
            [targetRun.chat_id!]: {
              id: targetRun.chat_id!,
              title: taskName || '自动化任务',
              createdAt: new Date(targetRun.started_at).getTime(),
              updatedAt: new Date(targetRun.started_at).getTime(),
              messages: [],
              automationRun: true,
              automationTaskId: taskId,
            },
          },
          order: prev.order.includes(targetRun.chat_id!)
            ? prev.order
            : [targetRun.chat_id!, ...prev.order],
        };
      });
      chatStore.addBackendSessionId(targetRun.chat_id);
      chatStore.setCurrentChatId(targetRun.chat_id);
    }
    useCatalogStore.getState().setPanel('chat');
  },

  exitAutomationChat: () => {
    set({ activeGroup: null, selectedRunId: null });
  },

  selectRun: (runId) => {
    const { activeGroup } = get();
    if (!activeGroup) return;
    const run = activeGroup.runs.find((r) => r.run_id === runId);
    if (!run || !run.chat_id || run.status === 'running') return;

    set({ selectedRunId: runId });
    const chatStore = useChatStore.getState();
    // Ensure stub chat entry + backend marker exist (same as enterAutomationChat)
    chatStore.updateStore((prev) => {
      if (prev.chats[run.chat_id!]) return prev;
      return {
        chats: {
          ...prev.chats,
          [run.chat_id!]: {
            id: run.chat_id!,
            title: activeGroup.taskName || '自动化任务',
            createdAt: new Date(run.started_at).getTime(),
            updatedAt: new Date(run.started_at).getTime(),
            messages: [],
            automationRun: true,
            automationTaskId: activeGroup.taskId,
          },
        },
        order: prev.order.includes(run.chat_id!)
          ? prev.order
          : [run.chat_id!, ...prev.order],
      };
    });
    chatStore.addBackendSessionId(run.chat_id);
    chatStore.setCurrentChatId(run.chat_id);
  },

  setRuns: (runs) => {
    const { activeGroup } = get();
    if (!activeGroup) return;
    const completedRuns = runs.filter(
      (r) => r.status !== 'running' && r.chat_id,
    );
    set({
      activeGroup: {
        ...activeGroup,
        runs,
        latestCompletedChatId: completedRuns[0]?.chat_id || null,
        latestRunAt: completedRuns.length > 0
          ? new Date(completedRuns[0].started_at).getTime()
          : activeGroup.latestRunAt,
      },
    });
  },

  setSidebarTasks: (tasks) => set({ sidebarTasks: tasks }),

  updateSidebarTask: (task) => set((state) => ({
    sidebarTasks: state.sidebarTasks.some((item) => item.task_id === task.task_id)
      ? state.sidebarTasks.map((item) => (item.task_id === task.task_id ? task : item))
      : [task, ...state.sidebarTasks],
  })),

  renameActiveGroup: (taskId, taskName) => set((state) => ({
    activeGroup: state.activeGroup?.taskId === taskId
      ? { ...state.activeGroup, taskName }
      : state.activeGroup,
  })),

  toggleSidebarPinned: (taskId) => set((state) => {
    const prev = state.sidebarPrefs[taskId] || {};
    const nextPrefs = {
      ...state.sidebarPrefs,
      [taskId]: {
        ...prev,
        pinned: !prev.pinned,
      },
    };
    saveSidebarPrefs(nextPrefs);
    return { sidebarPrefs: nextPrefs };
  }),

  toggleSidebarFavorite: (taskId) => set((state) => {
    const prev = state.sidebarPrefs[taskId] || {};
    const nextPrefs = {
      ...state.sidebarPrefs,
      [taskId]: {
        ...prev,
        favorite: !prev.favorite,
      },
    };
    saveSidebarPrefs(nextPrefs);
    return { sidebarPrefs: nextPrefs };
  }),

  setSidebarFavorite: (taskId, favorite) => set((state) => {
    const prev = state.sidebarPrefs[taskId] || {};
    const nextPrefs = {
      ...state.sidebarPrefs,
      [taskId]: {
        ...prev,
        favorite,
      },
    };
    saveSidebarPrefs(nextPrefs);
    return { sidebarPrefs: nextPrefs };
  }),
}));
