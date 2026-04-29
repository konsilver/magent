/**
 * API Client for Jingxin-Agent Backend.
 *
 * Uses v1 unified response envelope.
 */

import type { Catalog, ChatItem, ChatMessage, ChunkPreviewResult, KBChunk, MemoryItem, ResourceItem, AutomationTask, AutomationRun, AutomationNotification } from './types';

type JsonObject = Record<string, unknown>;

interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
  trace_id?: string;
  timestamp?: number;
}

interface Pagination {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  has_previous: boolean;
  has_next: boolean;
}

interface PaginatedData<T> {
  items: T[];
  pagination: Pagination;
}

export interface CatalogItem {
  id: string;
  name: string;
  desc: string;
  enabled: boolean;
  tags?: string[];
  detail?: string;
  [key: string]: unknown;
}

export interface CatalogResponse {
  skills: CatalogItem[];
  agents: CatalogItem[];
  mcp: CatalogItem[];
  kb: CatalogItem[];
}

export interface KBDocumentsResponse {
  items: KBDocumentItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface SessionListResponse {
  items: ChatItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface CreateSessionRequest {
  title?: string;
  business_topic?: string;
}

export interface UpdateSessionRequest {
  title?: string;
  pinned?: boolean;
  favorite?: boolean;
  business_topic?: string;
}

export interface ChatRequest {
  chat_id: string;
  message: string;
  model_name?: string;
  user_id?: string;
  enabled_kbs?: string[];
  quoted_follow_up?: {
    text: string;
    ts?: number;
  };
}

export interface ChatResponse {
  chat_id: string;
  response: string;
  timestamp: string;
  route?: string;
  sources?: unknown[];
  artifacts?: unknown[];
  warnings?: string[];
  is_markdown?: boolean;
}

export interface SSEEvent {
  type?: string;
  delta?: string;
  content?: string;
  text?: string;
  [key: string]: unknown;
}

export interface UserInfo {
  user_id: string;
  username: string;
  email?: string;
  avatar_url?: string;
}

export interface UserPreferences {
  default_model?: string;
  language?: string;
  theme?: string;
  enabled_skills?: string[];
  enabled_mcps?: string[];
}

export interface AddArtifactToKBResult {
  document_id: string;
  kb_id: string;
  title: string;
  filename: string;
  size_bytes: number;
  uploaded_at: string;
  already_exists?: boolean;
}

export interface HealthResponse {
  status: string;
  service: string;
  timestamp: string;
}

export const getApiUrl = () => import.meta.env.VITE_API_BASE_URL || '/api';

function isApiEnvelope<T>(payload: unknown): payload is ApiEnvelope<T> {
  return !!payload && typeof payload === 'object' && 'code' in payload && 'data' in payload;
}

function unwrapData<T>(payload: unknown): T {
  if (isApiEnvelope<T>(payload)) {
    return payload.data;
  }
  return payload as T;
}

function readErrorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const record = payload as JsonObject;
    const message = record.message;
    if (typeof message === 'string' && message.trim()) {
      return message;
    }
    const detail = record.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}

function toTimestamp(value: unknown): number {
  if (typeof value !== 'string' || !value) return Date.now();
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function toChatItem(raw: JsonObject): ChatItem {
  const metadata = (raw.metadata ?? {}) as JsonObject;
  return {
    id: String(raw.chat_id ?? raw.id ?? ''),
    title: String(raw.title ?? '新对话'),
    createdAt: toTimestamp(raw.created_at),
    updatedAt: toTimestamp(raw.updated_at),
    messages: [],
    favorite: Boolean(raw.favorite),
    pinned: Boolean(raw.pinned),
    businessTopic: typeof raw.business_topic === 'string' ? raw.business_topic : undefined,
    agentId: typeof metadata.agent_id === 'string' ? metadata.agent_id : undefined,
    agentName: typeof metadata.agent_name === 'string' ? metadata.agent_name : undefined,
    planChat: metadata.plan_chat === true ? true : undefined,
    codeExecChat: metadata.code_exec_chat === true ? true : undefined,
  };
}

// ── Global 401 handler ──────────────────────────────────────────────────
let _on401: ((loginUrl: string) => void) | null = null;

/** Register a callback invoked on any 401 with login_url. */
export function onUnauthorized(handler: (loginUrl: string) => void) {
  _on401 = handler;
}

async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${getApiUrl()}${path}`;
  const response = await fetch(url, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers ?? {}),
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    // Handle 401/403 → session expired
    if ((response.status === 401 || response.status === 403) && _on401) {
      const loginUrl =
        (payload as any)?.data?.login_url ||
        (payload as any)?.detail?.data?.login_url ||
        '';
      _on401(loginUrl);
      throw new Error('Session expired');
    }
    throw new Error(readErrorMessage(payload, `API Error: ${response.status}`));
  }
  return payload as T;
}

export async function getCatalog(): Promise<Catalog> {
  const wrapped = await apiRequest<unknown>('/v1/catalog');
  const data = unwrapData<JsonObject>(wrapped);
  return {
    skills: (Array.isArray(data.skills) ? data.skills : []) as CatalogItem[],
    agents: (Array.isArray(data.agents) ? data.agents : []) as CatalogItem[],
    mcp: (Array.isArray(data.mcp) ? data.mcp : Array.isArray(data.mcp_servers) ? data.mcp_servers : []) as CatalogItem[],
    kb: (Array.isArray(data.kb) ? data.kb : []) as CatalogItem[],
  };
}

export async function updateCatalogItem(
  kind: 'skills' | 'agents' | 'mcp' | 'kb',
  itemId: string,
  enabled: boolean
): Promise<void> {
  await apiRequest(`/v1/catalog/${kind}/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
}

export async function listSessions(page: number = 1, pageSize: number = 50): Promise<SessionListResponse> {
  const wrapped = await apiRequest<unknown>(`/v1/chats?page=${page}&page_size=${pageSize}`);
  const data = unwrapData<PaginatedData<JsonObject>>(wrapped);
  const items = Array.isArray(data.items) ? data.items.map((item) => toChatItem(item)) : [];
  const pagination = data.pagination;
  return {
    items,
    total: pagination?.total_items ?? items.length,
    page: pagination?.page ?? page,
    page_size: pagination?.page_size ?? pageSize,
    has_more: Boolean(pagination?.has_next),
  };
}

export interface SearchResultItem extends ChatItem {
  match_type?: 'title' | 'content';
  matched_snippet?: string;
}

export async function searchSessions(
  query: string,
  page = 1,
  pageSize = 20,
): Promise<{ items: SearchResultItem[]; total: number }> {
  const wrapped = await apiRequest<unknown>(
    `/v1/chats/search?q=${encodeURIComponent(query)}&scope=all&page=${page}&page_size=${pageSize}`,
  );
  const data = unwrapData<{ items: JsonObject[]; total: number }>(wrapped);
  return {
    items: (data.items || []).map((raw) => ({
      ...toChatItem(raw),
      match_type: (raw.match_type as 'title' | 'content') || 'title',
      matched_snippet: typeof raw.matched_snippet === 'string' ? raw.matched_snippet : undefined,
    })),
    total: data.total ?? 0,
  };
}

export async function getSession(chatId: string): Promise<ChatItem> {
  const wrapped = await apiRequest<unknown>(`/v1/chats/${chatId}`);
  const data = unwrapData<JsonObject>(wrapped);
  return toChatItem(data);
}

export async function createSession(data: CreateSessionRequest): Promise<ChatItem> {
  const wrapped = await apiRequest<unknown>('/v1/chats', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  const payload = unwrapData<JsonObject>(wrapped);
  return toChatItem(payload);
}

export async function updateSession(chatId: string, data: UpdateSessionRequest): Promise<ChatItem> {
  const wrapped = await apiRequest<unknown>(`/v1/chats/${chatId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  const payload = unwrapData<JsonObject>(wrapped);
  return {
    id: chatId,
    title: String(payload.title ?? '新对话'),
    createdAt: Date.now(),
    updatedAt: toTimestamp(payload.updated_at),
    messages: [],
    favorite: Boolean(payload.favorite),
    pinned: Boolean(payload.pinned),
    businessTopic: undefined,
  };
}

export async function deleteSession(chatId: string): Promise<void> {
  await apiRequest(`/v1/chats/${chatId}`, {
    method: 'DELETE',
  });
}

export async function getChatMessages(chatId: string): Promise<ChatMessage[]> {
  const wrapped = await apiRequest<unknown>(`/v1/chats/${chatId}/messages`);
  const data = unwrapData<PaginatedData<JsonObject>>(wrapped);
  const items = Array.isArray(data.items) ? data.items : [];
  return items.map((item) => ({
    role: String(item.role) === 'assistant' ? 'assistant' : 'user',
    content: String(item.content ?? ''),
    isMarkdown: Boolean((item.metadata as JsonObject | undefined)?.is_markdown),
    ts: toTimestamp(item.created_at),
    citations: Array.isArray((item.metadata as JsonObject | undefined)?.citations)
      ? ((item.metadata as JsonObject).citations as ChatMessage['citations'])
      : undefined,
  }));
}

export async function sendChatMessage(request: ChatRequest): Promise<ChatResponse> {
  return await apiRequest<ChatResponse>('/v1/chats/send', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

export async function* sendChatMessageStream(
  request: ChatRequest
): AsyncGenerator<SSEEvent, void, unknown> {
  const url = `${getApiUrl()}/v1/chats/stream`;
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Stream request failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('No response body');
  }

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') return;

        try {
          const parsed = JSON.parse(data) as unknown;
          if (parsed && typeof parsed === 'object') {
            yield parsed as SSEEvent;
          }
        } catch {
          yield { delta: data };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Regenerate an assistant message. Returns a fetch Response with SSE body.
 */
export async function regenerateMessage(
  chatId: string,
  messageIndex: number,
  signal?: AbortSignal,
): Promise<Response> {
  return authFetch(`${getApiUrl()}/v1/chats/${chatId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex }),
    signal,
  });
}

/**
 * Edit a user message and regenerate. Returns a fetch Response with SSE body.
 */
export async function editAndRegenerate(
  chatId: string,
  messageIndex: number,
  newContent: string,
  signal?: AbortSignal,
): Promise<Response> {
  return authFetch(`${getApiUrl()}/v1/chats/${chatId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex, new_content: newContent }),
    signal,
  });
}

export async function getFollowUpQuestions(
  chatId: string,
  messageId: string,
): Promise<string[]> {
  try {
    const wrapped = await apiRequest<unknown>(
      `/v1/chats/${chatId}/messages/${messageId}/followups`,
    );
    const data = unwrapData<{ follow_up_questions?: string[] }>(wrapped);
    return Array.isArray(data?.follow_up_questions) ? data.follow_up_questions : [];
  } catch {
    return [];
  }
}

export async function getCurrentUser(): Promise<UserInfo> {
  const wrapped = await apiRequest<unknown>('/v1/me');
  const data = unwrapData<JsonObject>(wrapped);
  return {
    user_id: String(data.user_id ?? ''),
    username: String(data.username ?? ''),
    email: typeof data.email === 'string' ? data.email : undefined,
    avatar_url: typeof data.avatar === 'string' ? data.avatar : undefined,
  };
}

export async function getUserPreferences(userId: string): Promise<UserPreferences> {
  const wrapped = await apiRequest<unknown>(`/v1/users/${userId}/preferences`);
  return unwrapData<UserPreferences>(wrapped);
}

export async function updateUserPreferences(userId: string, preferences: UserPreferences): Promise<void> {
  await apiRequest(`/v1/users/${userId}/preferences`, {
    method: 'PUT',
    body: JSON.stringify(preferences),
  });
}

export async function healthCheck(): Promise<HealthResponse> {
  return await apiRequest<HealthResponse>('/health');
}

export interface KBDocumentItem {
  id: string;
  title: string;
  desc?: string;
  word_count?: number;
  indexing_status?: string;
  enabled?: boolean;
  data_source_type?: string;
  created_at?: number;
  content?: string;
}

export async function getKBDocuments(
  kbId: string,
  page = 1,
  pageSize = 20,
): Promise<KBDocumentsResponse> {
  try {
    const wrapped = await apiRequest<unknown>(
      `/v1/catalog/kb/${kbId}/documents?page=${page}&page_size=${pageSize}`,
    );
    const data = unwrapData<PaginatedData<KBDocumentItem>>(wrapped);
    const items = Array.isArray(data.items) ? data.items : [];
    const pagination = data.pagination;
    return {
      items,
      total: typeof pagination?.total_items === 'number' ? pagination.total_items : items.length,
      page: typeof pagination?.page === 'number' ? pagination.page : page,
      page_size: typeof pagination?.page_size === 'number' ? pagination.page_size : pageSize,
      has_more: Boolean(pagination?.has_next),
    };
  } catch {
    return {
      items: [],
      total: 0,
      page,
      page_size: pageSize,
      has_more: false,
    };
  }
}

export async function getKBDocumentDetail(
  kbId: string,
  _documentId: string,
): Promise<{ title: string; content: string; desc?: string }> {
  const wrapped = await apiRequest<unknown>(
    `/v1/catalog/kb/${kbId}/documents/${_documentId}`,
  );
  const data = unwrapData<{ title?: string; content?: string; desc?: string }>(wrapped);
  const rawTitle = typeof data.title === 'string' ? data.title.trim() : '';
  return {
    title: rawTitle,
    content: data.content || '',
    desc: data.desc,
  };
}

// ── 私有知识库管理 API ──────────────────────────────────────────

export interface IndexingConfig {
  parent_chunk_size?: number;
  child_chunk_size?: number;
  overlap_tokens?: number;
  parent_child_indexing?: boolean;
  auto_keywords_count?: number;
  auto_questions_count?: number;
}

export async function createKBSpace(
  name: string,
  description?: string,
  chunkMethod?: string,
  indexingConfig?: IndexingConfig,
): Promise<Record<string, unknown>> {
  const wrapped = await apiRequest<unknown>('/v1/catalog/kb', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: description || undefined,
      chunk_method: chunkMethod || 'semantic',
      indexing_config: indexingConfig || undefined,
    }),
  });
  return unwrapData<Record<string, unknown>>(wrapped);
}

export async function updateKBSpace(
  kbId: string,
  payload: { name?: string; description?: string },
): Promise<Record<string, unknown>> {
  const wrapped = await apiRequest<unknown>(`/v1/catalog/kb/${kbId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
  return unwrapData<Record<string, unknown>>(wrapped);
}

export async function polishKBDescription(
  name: string,
  description?: string,
): Promise<string> {
  const wrapped = await apiRequest<unknown>('/v1/catalog/kb/polish-description', {
    method: 'POST',
    body: JSON.stringify({
      name,
      description: description || undefined,
    }),
  });
  const data = unwrapData<{ description?: string }>(wrapped);
  return typeof data.description === 'string' ? data.description : '';
}

export async function uploadKBDocument(
  kbId: string,
  file: File,
  title?: string,
  indexingConfig?: IndexingConfig,
  chunkMethod?: string,
): Promise<Record<string, unknown>> {
  const url = `${getApiUrl()}/v1/catalog/kb/${kbId}/documents`;
  const formData = new FormData();
  formData.append('file', file);
  if (title) formData.append('title', title);
  if (indexingConfig) formData.append('indexing_config', JSON.stringify(indexingConfig));
  if (chunkMethod) formData.append('chunk_method', chunkMethod);

  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if ((response.status === 401 || response.status === 403) && _on401) {
      const loginUrl = (payload as any)?.data?.login_url || '';
      _on401(loginUrl);
      throw new Error('Session expired');
    }
    throw new Error(readErrorMessage(payload, `Upload failed: ${response.status}`));
  }

  const payload = await response.json();
  return unwrapData<Record<string, unknown>>(payload);
}

export async function deleteKBSpace(kbId: string): Promise<void> {
  await apiRequest(`/v1/catalog/kb/${kbId}`, { method: 'DELETE' });
}

export async function deleteKBDocument(kbId: string, documentId: string): Promise<void> {
  await apiRequest(`/v1/catalog/kb/${kbId}/documents/${documentId}`, { method: 'DELETE' });
}

export async function getKBChunks(
  kbId: string,
  docId: string,
  page = 1,
  pageSize = 100,
): Promise<KBChunk[]> {
  try {
    const wrapped = await apiRequest<unknown>(
      `/v1/catalog/kb/${kbId}/chunks?document_id=${docId}&page=${page}&page_size=${pageSize}`,
    );
    const data = unwrapData<{ items?: KBChunk[] }>(wrapped);
    return Array.isArray(data.items) ? data.items : [];
  } catch {
    return [];
  }
}

export async function updateKBChunk(
  kbId: string,
  chunkId: string,
  data: { tags?: string[]; questions?: string[] },
): Promise<void> {
  await apiRequest(`/v1/catalog/kb/${kbId}/chunks/${chunkId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function reindexKBDocument(
  kbId: string,
  docId: string,
  indexingConfig?: IndexingConfig,
  chunkMethod?: string,
): Promise<void> {
  const body: Record<string, unknown> = {};
  if (indexingConfig) body.indexing_config = { ...indexingConfig };
  if (chunkMethod) body.chunk_method = chunkMethod;
  await apiRequest(`/v1/catalog/kb/${kbId}/documents/${docId}/reindex`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// ── 分块预览 API ────────────────────────────────────────────────

export async function previewChunks(
  file: File,
  chunkMethod = 'structured',
  parentChunkSize = 1024,
  childChunkSize = 128,
  overlapTokens = 20,
  parentChildIndexing = true,
): Promise<ChunkPreviewResult> {
  const url = `${getApiUrl()}/v1/catalog/kb/preview-chunks`;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('chunk_method', chunkMethod);
  formData.append('parent_chunk_size', String(parentChunkSize));
  formData.append('child_chunk_size', String(childChunkSize));
  formData.append('overlap_tokens', String(overlapTokens));
  formData.append('parent_child_indexing', String(parentChildIndexing));

  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if ((response.status === 401 || response.status === 403) && _on401) {
      const loginUrl = (payload as any)?.data?.login_url || '';
      _on401(loginUrl);
      throw new Error('Session expired');
    }
    throw new Error(readErrorMessage(payload, `Preview failed: ${response.status}`));
  }

  const payload = await response.json();
  return unwrapData<ChunkPreviewResult>(payload);
}

// ── 记忆管理 API ────────────────────────────────────────────────

export async function getMemories(): Promise<{ enabled: boolean; items: MemoryItem[]; count: number }> {
  const wrapped = await apiRequest<unknown>('/v1/memories');
  return unwrapData<{ enabled: boolean; items: MemoryItem[]; count: number }>(wrapped);
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await apiRequest(`/v1/memories/${memoryId}`, { method: 'DELETE' });
}

export async function clearAllMemories(): Promise<void> {
  await apiRequest('/v1/memories', { method: 'DELETE' });
}

export async function clearMemoriesByType(memoryType: string): Promise<void> {
  await apiRequest(`/v1/memories?type=${encodeURIComponent(memoryType)}`, { method: 'DELETE' });
}

export interface UserSettings {
  memory_enabled: boolean;
  memory_write_enabled: boolean;
  mem0_available: boolean;
  reranker_enabled: boolean;
  reranker_available: boolean;
}

export async function getMemorySettings(): Promise<UserSettings> {
  const wrapped = await apiRequest<unknown>('/v1/memories/settings');
  return unwrapData<UserSettings>(wrapped);
}

export async function updateMemorySettings(memoryEnabled: boolean): Promise<void> {
  await apiRequest('/v1/memories/settings', {
    method: 'PATCH',
    body: JSON.stringify({ memory_enabled: memoryEnabled }),
  });
}

export async function updateMemoryWriteSettings(memoryWriteEnabled: boolean): Promise<void> {
  await apiRequest('/v1/memories/settings', {
    method: 'PATCH',
    body: JSON.stringify({ memory_write_enabled: memoryWriteEnabled }),
  });
}

export async function updateRerankerSettings(rerankerEnabled: boolean): Promise<void> {
  await apiRequest('/v1/memories/settings', {
    method: 'PATCH',
    body: JSON.stringify({ reranker_enabled: rerankerEnabled }),
  });
}

// ── Auth API (SSO session) ──────────────────────────────────────────────

export interface AuthUser {
  user_id: string;
  username: string;
  email?: string;
  avatar_url?: string;
  expires_at?: string;
}

export interface ChatShareRecord {
  share_id: string;
  chat_id: string;
  origin_message_ts?: number | null;
  title: string;
  preview_url: string;
  created_at: string;
  expires_at?: string | null;
  expiry_option?: '3d' | '15d' | '3m' | 'permanent';
  created_by: string;
  created_by_username?: string;
  status: 'valid' | 'expired';
  view_count: number;
  revoked?: boolean;
}

export async function listChatShares(): Promise<ChatShareRecord[]> {
  const wrapped = await apiRequest<unknown>('/v1/chat-shares');
  const data = unwrapData<{ items?: ChatShareRecord[] }>(wrapped);
  return Array.isArray(data?.items) ? data.items : [];
}

export async function revokeChatShare(shareId: string): Promise<void> {
  await apiRequest(`/v1/chat-shares/${encodeURIComponent(shareId)}/revoke`, {
    method: 'POST',
  });
}

export async function restoreChatShare(shareId: string): Promise<void> {
  await apiRequest(`/v1/chat-shares/${encodeURIComponent(shareId)}/restore`, {
    method: 'POST',
  });
}

/** Exchange a one-time SSO ticket for a session cookie + user info. */
export async function exchangeTicket(ticket: string): Promise<AuthUser> {
  const wrapped = await apiRequest<unknown>('/v1/auth/ticket/exchange', {
    method: 'POST',
    body: JSON.stringify({ ticket }),
  });
  return unwrapData<AuthUser>(wrapped);
}

/** Check if the current cookie session is still valid. */
export async function checkSession(): Promise<AuthUser> {
  const wrapped = await apiRequest<unknown>('/v1/auth/session/check');
  return unwrapData<AuthUser>(wrapped);
}

/** Revoke current session and clear the cookie. */
export async function logout(): Promise<string | undefined> {
  const res = await apiRequest<unknown>('/v1/auth/logout', { method: 'POST' });
  const data = unwrapData<{ login_url?: string }>(res);
  return data?.login_url || undefined;
}

/**
 * Convenience wrapper: adds `credentials: 'include'` and handles 401.
 * Use in App.tsx for direct fetch() calls that bypass apiRequest().
 */
export function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: 'include',
  }).then(async (response) => {
    if ((response.status === 401 || response.status === 403) && _on401) {
      let loginUrl = '';
      try {
        const payload = await response.clone().json();
        loginUrl =
          payload?.data?.login_url ||
          payload?.detail?.data?.login_url ||
          '';
      } catch {
        // ignore parse errors
      }
      _on401(loginUrl);
    }
    return response;
  });
}

// ── 文件上传 API ────────────────────────────────────────────────

export interface UploadedFile {
  file_id: string;
  name: string;
  size: number;
  mime_type: string;
  download_url: string;
}

export async function uploadFile(file: File, chatId?: string): Promise<UploadedFile> {
  const url = `${getApiUrl()}/v1/file/upload`;
  const formData = new FormData();
  formData.append('file', file);
  if (chatId) formData.append('chat_id', chatId);

  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if ((response.status === 401 || response.status === 403) && _on401) {
      const loginUrl = (payload as any)?.data?.login_url || '';
      _on401(loginUrl);
      throw new Error('Session expired');
    }
    throw new Error(readErrorMessage(payload, `Upload failed: ${response.status}`));
  }

  const payload = await response.json();
  return unwrapData<UploadedFile>(payload);
}

/** Overwrite existing file content in-place (same file_id & URL). */
export async function overwriteFile(fileId: string, file: File): Promise<UploadedFile> {
  const url = `${getApiUrl()}/v1/file/${encodeURIComponent(fileId)}`;
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(url, {
    method: 'PUT',
    credentials: 'include',
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if ((response.status === 401 || response.status === 403) && _on401) {
      const loginUrl = (payload as any)?.data?.login_url || '';
      _on401(loginUrl);
      throw new Error('Session expired');
    }
    throw new Error(readErrorMessage(payload, `Overwrite failed: ${response.status}`));
  }

  const payload = await response.json();
  return unwrapData<UploadedFile>(payload);
}

// ── 我的空间 API ────────────────────────────────────────────────

export async function getArtifacts(params?: {
  type?: 'document' | 'image';
  source_kind?: 'user_upload' | 'ai_generated';
  keyword?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: ResourceItem[]; total: number; has_more: boolean }> {
  const qs = new URLSearchParams();
  if (params?.type) qs.set('type', params.type);
  if (params?.source_kind) qs.set('source_kind', params.source_kind);
  if (params?.keyword) qs.set('keyword', params.keyword);
  if (params?.page) qs.set('page', String(params.page));
  if (params?.page_size) qs.set('page_size', String(params.page_size));
  const query = qs.toString();
  const wrapped = await apiRequest<unknown>(`/v1/artifacts${query ? '?' + query : ''}`);
  return unwrapData<{ items: ResourceItem[]; total: number; has_more: boolean }>(wrapped);
}

export async function getFavoriteChats(params?: {
  keyword?: string;
  page?: number;
  page_size?: number;
}): Promise<{ items: ResourceItem[]; total: number; has_more: boolean }> {
  const qs = new URLSearchParams();
  if (params?.keyword) qs.set('keyword', params.keyword);
  if (params?.page) qs.set('page', String(params.page));
  if (params?.page_size) qs.set('page_size', String(params.page_size));
  const query = qs.toString();
  const wrapped = await apiRequest<unknown>(`/v1/artifacts/favorites${query ? '?' + query : ''}`);
  return unwrapData<{ items: ResourceItem[]; total: number; has_more: boolean }>(wrapped);
}

export async function deleteArtifact(id: string): Promise<void> {
  await apiRequest(`/v1/artifacts/${id}`, { method: 'DELETE' });
}

export async function addArtifactToKnowledgeBase(
  artifactId: string,
  kbId: string,
): Promise<AddArtifactToKBResult> {
  const wrapped = await apiRequest<unknown>(`/v1/artifacts/${encodeURIComponent(artifactId)}/knowledge-base`, {
    method: 'POST',
    body: JSON.stringify({ kb_id: kbId }),
  });
  return unwrapData<AddArtifactToKBResult>(wrapped);
}

// ── Plan Mode API ─────────────────────────────────────────────────────────

import type { Plan } from './types';

export async function generatePlanStream(
  taskDescription: string,
  modelName: string = 'qwen',
  signal?: AbortSignal,
  enabledMcpIds?: string[],
  enabledSkillIds?: string[],
  enabledKbIds?: string[],
  chatId?: string,
  historyMessages?: Array<{ role: string; content: string }>,
  attachments?: Array<{ name: string; content: string; mime_type: string; file_id: string; download_url: string }>,
  enabledAgentIds?: string[],
  previousPlanId?: string,
  userReply?: string,
): Promise<Response> {
  const url = `${getApiUrl()}/v1/plans/generate`;
  return authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task_description: taskDescription,
      model_name: modelName,
      ...(enabledMcpIds ? { enabled_mcp_ids: enabledMcpIds } : {}),
      ...(enabledSkillIds ? { enabled_skill_ids: enabledSkillIds } : {}),
      ...(enabledKbIds ? { enabled_kb_ids: enabledKbIds } : {}),
      ...(enabledAgentIds ? { enabled_agent_ids: enabledAgentIds } : {}),
      ...(chatId ? { chat_id: chatId } : {}),
      ...(historyMessages && historyMessages.length > 0 ? { history_messages: historyMessages } : {}),
      ...(attachments && attachments.length > 0 ? { attachments } : {}),
      ...(previousPlanId ? { previous_plan_id: previousPlanId } : {}),
      ...(userReply ? { user_reply: userReply } : {}),
    }),
    signal,
  });
}

export async function listPlans(): Promise<Plan[]> {
  const res = await apiRequest<unknown>('/v1/plans');
  return unwrapData<Plan[]>(res);
}

export async function getPlanApi(planId: string): Promise<Plan> {
  const res = await apiRequest<unknown>(`/v1/plans/${planId}`);
  return unwrapData<Plan>(res);
}

export async function updatePlanApi(
  planId: string,
  updates: { status?: string; title?: string; steps?: Record<string, unknown>[] },
): Promise<Plan> {
  const res = await apiRequest<unknown>(`/v1/plans/${planId}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
  return unwrapData<Plan>(res);
}

export async function deletePlanApi(planId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/plans/${planId}`, { method: 'DELETE' });
}

export async function executePlanStream(
  planId: string,
  signal?: AbortSignal,
  enabledMcpIds?: string[],
  enabledSkillIds?: string[],
  enabledKbIds?: string[],
  chatId?: string,
  historyMessages?: Array<{ role: string; content: string }>,
  enabledAgentIds?: string[],
): Promise<Response> {
  const url = `${getApiUrl()}/v1/plans/${planId}/execute`;
  return authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...(enabledMcpIds ? { enabled_mcp_ids: enabledMcpIds } : {}),
      ...(enabledSkillIds ? { enabled_skill_ids: enabledSkillIds } : {}),
      ...(enabledKbIds ? { enabled_kb_ids: enabledKbIds } : {}),
      ...(enabledAgentIds ? { enabled_agent_ids: enabledAgentIds } : {}),
      ...(chatId ? { chat_id: chatId } : {}),
      ...(historyMessages && historyMessages.length > 0 ? { history_messages: historyMessages } : {}),
    }),
    signal,
  });
}

export async function cancelPlanApi(planId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/plans/${planId}/cancel`, { method: 'POST' });
}

export const api = {
  getCatalog,
  updateCatalogItem,
  getKBDocuments,
  getKBDocumentDetail,
  createKBSpace,
  polishKBDescription,
  updateKBSpace,
  uploadKBDocument,
  deleteKBSpace,
  deleteKBDocument,
  getKBChunks,
  updateKBChunk,
  reindexKBDocument,
  previewChunks,
  listSessions,
  searchSessions,
  getSession,
  createSession,
  updateSession,
  deleteSession,
  getChatMessages,
  getFollowUpQuestions,
  sendChatMessage,
  sendChatMessageStream,
  getCurrentUser,
  getUserPreferences,
  updateUserPreferences,
  healthCheck,
  getMemories,
  deleteMemory,
  clearAllMemories,
  getMemorySettings,
  updateMemorySettings,
  updateMemoryWriteSettings,
  updateRerankerSettings,
  exchangeTicket,
  checkSession,
  logout,
  listChatShares,
  authFetch,
  uploadFile,
  overwriteFile,
  getArtifacts,
  getFavoriteChats,
  deleteArtifact,
  addArtifactToKnowledgeBase,
  executeCodeDirect,
};

export default api;

// ── Automation API ──────────────────────────────────────────────

export interface CreateAutomationRequest {
  task_type: 'prompt' | 'plan';
  prompt?: string;
  plan_id?: string;
  cron_expression: string;
  recurring: boolean;
  schedule_type?: 'recurring' | 'once' | 'manual';
  name?: string;
  description?: string;
  timezone?: string;
  enabled_mcp_ids?: string[];
  enabled_skill_ids?: string[];
  enabled_kb_ids?: string[];
  enabled_agent_ids?: string[];
  max_runs?: number;
}

export interface UpdateAutomationRequest {
  name?: string;
  description?: string;
  cron_expression?: string;
  recurring?: boolean;
  schedule_type?: 'recurring' | 'once' | 'manual';
  prompt?: string;
  enabled_mcp_ids?: string[];
  enabled_skill_ids?: string[];
  enabled_kb_ids?: string[];
  enabled_agent_ids?: string[];
}

export async function createAutomation(data: CreateAutomationRequest): Promise<AutomationTask> {
  const res = await apiRequest<unknown>('/v1/automations', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return unwrapData<AutomationTask>(res);
}

export async function listAutomations(status?: string): Promise<AutomationTask[]> {
  const qs = status ? `?status=${status}` : '';
  const res = await apiRequest<unknown>(`/v1/automations${qs}`);
  return unwrapData<AutomationTask[]>(res);
}

export async function getAutomation(taskId: string): Promise<AutomationTask> {
  const res = await apiRequest<unknown>(`/v1/automations/${taskId}`);
  return unwrapData<AutomationTask>(res);
}

export async function updateAutomation(taskId: string, data: UpdateAutomationRequest): Promise<AutomationTask> {
  const res = await apiRequest<unknown>(`/v1/automations/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  return unwrapData<AutomationTask>(res);
}

export async function deleteAutomation(taskId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/automations/${taskId}`, { method: 'DELETE' });
}

export async function pauseAutomation(taskId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/automations/${taskId}/pause`, { method: 'POST' });
}

export async function resumeAutomation(taskId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/automations/${taskId}/resume`, { method: 'POST' });
}

export async function triggerAutomation(taskId: string): Promise<void> {
  await apiRequest<unknown>(`/v1/automations/${taskId}/trigger`, { method: 'POST' });
}

export async function getAutomationRuns(taskId: string, limit?: number): Promise<AutomationRun[]> {
  const res = await apiRequest<unknown>(`/v1/automations/${taskId}/runs?limit=${limit || 10}`);
  return unwrapData<AutomationRun[]>(res);
}

export async function activateAutomationSidebar(taskId: string): Promise<AutomationTask> {
  const res = await apiRequest<unknown>(`/v1/automations/${taskId}/activate-sidebar`, { method: 'POST' });
  return unwrapData<AutomationTask>(res);
}

export async function listSidebarAutomations(): Promise<AutomationTask[]> {
  const res = await apiRequest<unknown>('/v1/automations?sidebar_activated=true');
  return unwrapData<AutomationTask[]>(res);
}

export async function getAutomationNotifications(): Promise<AutomationNotification[]> {
  const res = await apiRequest<unknown>('/v1/automations/notifications/list');
  return unwrapData<AutomationNotification[]>(res);
}

export async function markNotificationsRead(ids: string[]): Promise<void> {
  await apiRequest<unknown>('/v1/automations/notifications/read', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export async function deleteNotifications(ids: string[]): Promise<void> {
  await apiRequest<unknown>('/v1/automations/notifications/delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

// ── Standalone code execution (for Artifacts panel re-execute) ──

export interface CodeExecDirectResult {
  stdout: string;
  stderr: string;
  exit_code: number;
  execution_time_ms: number;
  files: Array<{ file_id: string; name: string; url: string; mime_type: string; size: number }>;
}

export async function executeCodeDirect(params: {
  language: string;
  code: string;
  timeout?: number;
}): Promise<CodeExecDirectResult> {
  const resp = await authFetch(`${getApiUrl()}/v1/code/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      language: params.language,
      code: params.code,
      timeout: params.timeout ?? 60,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`代码执行失败: ${resp.status} ${text}`);
  }
  const envelope = await resp.json();
  return envelope.data as CodeExecDirectResult;
}
