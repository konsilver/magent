export const API_BASE = (import.meta.env.VITE_API_BASE_URL as string) || '/api';

// Admin 平台
export const ADMIN_STORAGE_KEY = 'jx_admin_token';
export const ADMIN_AUTH_EXPIRED_EVENT = 'jx-admin-auth-expired';
// Config 平台
export const CONFIG_STORAGE_KEY = 'jx_config_token';
export const CONFIG_AUTH_EXPIRED_EVENT = 'jx-config-auth-expired';

// 向后兼容
export const STORAGE_KEY = ADMIN_STORAGE_KEY;

export function move<T>(arr: T[], from: number, to: number): T[] {
  if (to < 0 || to >= arr.length) return arr;
  const next = [...arr];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function createAuthFetch(storageKey: string, expiredEvent: string) {
  return async (token: string, path: string, init?: RequestInit) => {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        ...((init?.headers as Record<string, string>) || {}),
      },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 401 || res.status === 403) {
        localStorage.removeItem(storageKey);
        window.dispatchEvent(new CustomEvent(expiredEvent));
      }
      throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
    }
    return res.json();
  };
}

export const adminFetch = createAuthFetch(ADMIN_STORAGE_KEY, ADMIN_AUTH_EXPIRED_EVENT);
export const configFetch = createAuthFetch(CONFIG_STORAGE_KEY, CONFIG_AUTH_EXPIRED_EVENT);

export const fetchContent = (token: string) => adminFetch(token, '/v1/content/docs');

export async function saveBlock(token: string, blockId: string, payload: unknown[]) {
  return adminFetch(token, `/v1/content/docs/${blockId}`, {
    method: 'PUT',
    body: JSON.stringify({ payload }),
  });
}
