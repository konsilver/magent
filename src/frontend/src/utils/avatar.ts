const AVATAR_STORAGE_KEY = 'jingxin_user_avatar';

export function getStoredAvatarUrl(): string | null {
  try {
    return localStorage.getItem(AVATAR_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function saveStoredAvatarUrl(value: string | null) {
  try {
    if (value) {
      localStorage.setItem(AVATAR_STORAGE_KEY, value);
    } else {
      localStorage.removeItem(AVATAR_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures and keep runtime behavior intact.
  }
}

export function resolveAvatarUrl(avatarUrl?: string | null): string {
  return avatarUrl || '/home/头像.svg';
}
