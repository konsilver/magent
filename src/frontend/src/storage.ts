import type { Catalog, ChatStore } from './types';

export const STORAGE_KEY = 'jingxin_ui_chat_history_v2';
export const ENABLE_KEY = 'jingxin_ui_enabled_catalog_v1';

export const defaultCatalog: Catalog = {
  skills: [],
  agents: [],
  mcp: [],
  kb: [],
};

export function loadChatStore(): ChatStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { chats: {}, order: [] };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return { chats: {}, order: [] };
    return {
      chats: parsed.chats || {},
      order: parsed.order || [],
    };
  } catch {
    return { chats: {}, order: [] };
  }
}

export function saveChatStore(store: ChatStore) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
}

export function loadCatalog(): Catalog {
  try {
    const raw = localStorage.getItem(ENABLE_KEY);
    if (!raw) return structuredClone(defaultCatalog);
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return structuredClone(defaultCatalog);
    return {
      skills: Array.isArray(parsed.skills) ? parsed.skills : [],
      agents: Array.isArray(parsed.agents) ? parsed.agents : [],
      mcp: Array.isArray(parsed.mcp) ? parsed.mcp : [],
      kb: Array.isArray(parsed.kb) ? parsed.kb : [],
    };
  } catch {
    return structuredClone(defaultCatalog);
  }
}

export function saveCatalog(catalog: Catalog) {
  localStorage.setItem(ENABLE_KEY, JSON.stringify(catalog));
}

export function nowId(prefix = 'chat') {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${prefix}_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}
