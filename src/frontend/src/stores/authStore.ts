import { create } from 'zustand';
import { checkSession, exchangeTicket, logout, onUnauthorized, type AuthUser } from '../api';
import { getStoredAvatarUrl, saveStoredAvatarUrl } from '../utils/avatar';

export const LOGIN_LANDING_KEY = 'jingxin_login_landing';
let authInitPromise: Promise<void> | null = null;

interface AuthState {
  authUser: AuthUser | null;
  authChecking: boolean;
  authExpiredUrl: string | null;
  /** Whether user was ever authenticated in this session */
  wasAuthed: boolean;

  setAuthUser: (user: AuthUser | null) => void;
  setAvatarUrl: (avatarUrl: string | null) => void;
  setAuthChecking: (v: boolean) => void;
  setAuthExpiredUrl: (url: string | null) => void;
  triggerExpired: (loginUrl?: string) => void;
  initAuth: () => Promise<void>;
  doLogout: () => Promise<void>;
}

const SSO_LOGIN_URL = (import.meta.env.SSO_LOGIN_URL as string) || '';

function isSessionExpiredError(error: unknown): boolean {
  return error instanceof Error && error.message === 'Session expired';
}

function isMockLoginUrl(url?: string | null): boolean {
  const value = (url || '').trim();
  return !value || value.includes('/mock-sso/login');
}

function buildLoginUrl(): string {
  if (SSO_LOGIN_URL && !isMockLoginUrl(SSO_LOGIN_URL)) return SSO_LOGIN_URL;
  const origin = window.location.origin;
  return `${origin}/mock-sso/login?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`;
}

function withStoredAvatar(user: AuthUser | null): AuthUser | null {
  if (!user) return user;
  const storedAvatar = getStoredAvatarUrl();
  if (!storedAvatar) return user;
  return { ...user, avatar_url: storedAvatar };
}

export const useAuthStore = create<AuthState>((set, get) => ({
  authUser: null,
  authChecking: true,
  authExpiredUrl: null,
  wasAuthed: false,

  setAuthUser: (user) => {
    const nextUser = withStoredAvatar(user);
    if (nextUser) set({ authUser: nextUser, wasAuthed: true });
    else set({ authUser: user });
  },
  setAvatarUrl: (avatarUrl) => {
    saveStoredAvatarUrl(avatarUrl);
    set((state) => ({
      authUser: state.authUser ? { ...state.authUser, avatar_url: avatarUrl || undefined } : state.authUser,
    }));
  },
  setAuthChecking: (v) => set({ authChecking: v }),
  setAuthExpiredUrl: (url) => set({ authExpiredUrl: url }),

  triggerExpired: (loginUrl?: string) => {
    const url = isMockLoginUrl(loginUrl) ? buildLoginUrl() : (loginUrl || buildLoginUrl());

    // If user was previously authenticated, show the expired modal
    // Otherwise redirect directly to login
    if (get().wasAuthed) {
      set({ authExpiredUrl: url });
    } else {
      window.location.href = url;
    }
  },

  initAuth: async () => {
    if (authInitPromise) {
      await authInitPromise;
      return;
    }

    authInitPromise = (async () => {
    // Register global 401 handler
      onUnauthorized((loginUrl: string) => {
        get().triggerExpired(loginUrl);
      });

      set({ authChecking: true });

      // Check for SSO ticket in URL
      const params = new URLSearchParams(window.location.search);
      const ticket = params.get('ticket');

      if (ticket) {
        // Remove the one-time ticket from the URL before exchanging it.
        // In React StrictMode, initAuth can run twice in development; if the
        // ticket stays in the URL, the second run will try to exchange the same
        // ticket again and incorrectly trigger an auth-expired flow.
        params.delete('ticket');
        params.delete('redirect');
        const clean = params.toString();
        const newUrl = window.location.pathname + (clean ? `?${clean}` : '');
        window.history.replaceState({}, '', newUrl);

        try {
          const user = withStoredAvatar(await exchangeTicket(ticket));
          window.sessionStorage.setItem(LOGIN_LANDING_KEY, '1');
          set({ authUser: user, authChecking: false, wasAuthed: true });
          return;
        } catch (error) {
          if (isSessionExpiredError(error)) {
            set({ authUser: null, authChecking: false });
            return;
          }
          // Fall through to session check
        }
      }

      try {
        const user = withStoredAvatar(await checkSession());
        set({ authUser: user, authChecking: false, wasAuthed: true });
      } catch (error) {
        set({ authUser: null, authChecking: false });
        if (!isSessionExpiredError(error)) {
          get().triggerExpired();
        }
      }
    })();

    try {
      await authInitPromise;
    } finally {
      authInitPromise = null;
    }
  },

  doLogout: async () => {
    let serverLoginUrl: string | undefined;
    try {
      serverLoginUrl = await logout();
    } catch {
      // ignore — cookie may already be gone
    }
    set({ authUser: null });
    const loginUrl = serverLoginUrl || buildLoginUrl();
    window.location.href = loginUrl;
  },
}));
