/**
 * Shared utilities for code execution UI components.
 * Used by CodeExecRenderer and CodeArtifactPanel.
 */

import hljs from 'highlight.js';

export const LANG_LABELS: Record<string, string> = {
  python: 'Python',
  javascript: 'JavaScript',
  bash: 'Bash',
  sh: 'Shell',
};

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

/** Syntax-highlight code using highlight.js. Returns HTML string. */
export function highlightCode(code: string, language: string): string {
  if (!code) return '';
  const lang = language === 'sh' ? 'bash' : language;
  if (hljs.getLanguage(lang)) {
    try {
      return hljs.highlight(code, { language: lang }).value;
    } catch { /* fallback */ }
  }
  return code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** API base URL — single source of truth for all components */
export const effectiveApiUrl = (import.meta.env.VITE_API_BASE_URL as string || '').trim() || '/api';
