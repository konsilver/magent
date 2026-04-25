export type PanelKey = 'chat' | 'skills' | 'agents' | 'mcp' | 'kb' | 'docs' | 'app_center' | 'settings' | 'share_records' | 'my_space' | 'ability_center' | 'lab';

export type CitationSourceType =
  | 'internet'
  | 'knowledge_base'
  | 'database'
  | 'industry_news'
  | 'ai_news'
  | 'chain_info'
  | 'company_profile'
  | 'unknown';

export interface CitationItem {
  id: string;            // e.g. "internet_search-1", "retrieve_dataset_content-2"
  tool_name: string;
  tool_id?: string;
  title: string;
  url: string;
  snippet: string;
  source_type: CitationSourceType;
}

export type UpdateCategory = '模型迭代' | '信息处理' | '应用上新' | '体验优化';

export interface UpdateEntry {
  date: string;
  year: string;
  title: string;
  category: UpdateCategory;
  desc: string;
}

export interface CapItem {
  title: string;
  desc: string;
  bullets: string[];
}

export type ChatRole = 'user' | 'assistant';

export interface ToolCall {
  id?: string;
  name: string;
  displayName?: string;
  input?: any;
  output?: any;
  status?: 'pending' | 'running' | 'success' | 'error';
  timestamp?: number;
}

export interface ThinkingBlock {
  content: string;
  timestamp?: number;
}

/** 用于记录消息中各元素（文本/工具调用/思考）的顺序，实现内联交错展示 */
export interface MessageSegment {
  type: 'text' | 'tool' | 'thinking' | 'plan';
  content?: string;    // 'text' 和 'thinking' 类型时使用
  toolIndex?: number;  // 'tool' 类型时使用，对应 toolCalls[toolIndex]
  planData?: {         // 'plan' 类型时使用
    mode: 'preview' | 'executing' | 'complete';
    title: string;
    description?: string;
    steps: Array<{
      step_order: number;
      title: string;
      description?: string;
      expected_tools?: string[];
      expected_skills?: string[];
      expected_agents?: string[];
      acceptance_criteria?: string;
      status?: 'pending' | 'running' | 'success' | 'failed' | 'skipped';
      summary?: string;
      text?: string;
    }>;
    completedSteps?: number;
    totalSteps?: number;
    resultText?: string;
    agentNameMap?: Record<string, string>;
  };
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
  isMarkdown?: boolean;
  ts: number;
  quotedFollowUp?: {
    text: string;
    ts?: number;
  };
  skillId?: string;
  skillName?: string;
  mentionName?: string;
  messageId?: string;   // 后端 message_id，用于 feedback 提交
  toolCalls?: ToolCall[];
  thinking?: ThinkingBlock[];
  segments?: MessageSegment[];  // 有序片段列表（新消息使用）
  citations?: CitationItem[];   // 工具调用引用注册表
  followUpQuestions?: string[]; // 延伸问题（可点击发送）
  isStreaming?: boolean;
  /** Backend signals an extended LLM silence; UI replaces streaming dots with a "正在准备调用工具…" indicator. */
  toolPending?: boolean;
  attachments?: Array<{
    name: string;
    mime_type?: string;
    file_id?: string;       // OSS 文件 ID，存在时可下载
    download_url?: string;  // 下载路径，如 /files/{file_id}
  }>;  // 用户上传的文件
}

export interface ChatItem {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  favorite?: boolean;
  pinned?: boolean;
  businessTopic?: string;
  /** Sub-agent binding (set when chat is started from a sub-agent) */
  agentId?: string;
  agentName?: string;
  /** Whether this chat was created via plan mode from the App Center */
  planChat?: boolean;
  /** Whether this chat was created via code execution from the Lab */
  codeExecChat?: boolean;
  /** Automation task ID — set on virtual sidebar entries for automation tasks */
  automationTaskId?: string;
  /** Whether this is an automation-generated chat (virtual sidebar entry) */
  automationRun?: boolean;
}

export interface ChatStore {
  chats: Record<string, ChatItem>;
  order: string[];
}

export interface CatalogItemBase {
  id: string;
  name: string;
  desc: string;
  enabled: boolean;
  tags?: string[];
  detail?: string; // markdown
}

export interface SkillItem extends CatalogItemBase {
  provider?: string;
  version?: string;
  inputs?: string;
  outputs?: string;
}

export interface AgentItem extends CatalogItemBase {
  owner?: string;
  model?: string;
  routeHint?: string;
}

export interface MCPItem extends CatalogItemBase {
  server?: string;
  tools?: string[];
}

export interface KBDocument {
  id: string;
  title: string;
  desc?: string;
  content?: string;
  indexing_status?: string;  // "processing" | "completed" | "failed"
  word_count?: number;
  size_bytes?: number;
  created_at?: number;
}

export interface KBChunk {
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  tags: string[];
  questions: string[];
}

export interface KBItem extends CatalogItemBase {
  provider?: string;
  version?: string;
  inputs?: string;
  outputs?: string;
  documents?: KBDocument[];
  visibility?: 'public' | 'private';
  is_public?: boolean;
  document_count?: number;
  chunk_method?: string;
  system_managed?: boolean;
  pinned?: boolean;
  editable?: boolean;
  deletable?: boolean;
  uploadable?: boolean;
}

export interface ChunkPreviewChild {
  index: number;
  content: string;
}

export interface ChunkPreviewItem {
  index: number;
  content: string;
  token_count: number;
  children_count: number;
  children_preview: ChunkPreviewChild[];
}

export interface ChunkPreviewResult {
  total_chunks: number;
  total_children: number;
  chunks: ChunkPreviewItem[];
}

export interface MemoryItem {
  id: string;
  memory: string;
  created_at?: string;
  updated_at?: string;
  score?: number;
}

export interface Catalog {
  skills: SkillItem[];
  agents: AgentItem[];
  mcp: MCPItem[];
  kb: KBItem[];
}

// ── Model management types ──────────────────────────────────────────────────

export type ProviderType = 'chat' | 'embedding' | 'reranker';

export interface ModelProvider {
  provider_id: string;
  display_name: string;
  provider_type: ProviderType;
  base_url: string;
  api_key: string;       // masked in responses
  model_name: string;
  extra_config: Record<string, unknown>;
  is_active: boolean;
  last_tested_at: string | null;
  last_test_status: 'success' | 'failure' | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelRole {
  role_key: string;
  label: string;
  required_type: ProviderType;
  provider_id: string | null;
  provider_name: string | null;
  model_name: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface TestConnectionResult {
  success: boolean;
  latency_ms: number;
  error: string | null;
}

// ── Service configuration types ────────────────────────────────────────────

export interface SystemConfig {
  config_key: string;
  config_value: string | null;
  display_name: string;
  description: string | null;
  group_key: string;
  is_secret: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface SystemConfigGroup {
  group_key: string;
  label: string;
  items: SystemConfig[];
}

// ── My Space types ─────────────────────────────────────────────────────────

export type MySpaceTab = 'assets' | 'favorites' | 'shares' | 'notifications';

// ── Automation types ────────────────────────────────────────────
export type AutomationTaskType = 'prompt' | 'plan';
export type AutomationStatus = 'active' | 'paused' | 'disabled' | 'completed' | 'expired';
export type AutomationRunStatus = 'running' | 'success' | 'failed';
export type AutomationScheduleType = 'recurring' | 'once' | 'manual';

export interface AutomationTask {
  task_id: string;
  task_type: AutomationTaskType;
  prompt?: string;
  plan_id?: string;
  plan_title?: string;
  cron_expression: string;
  recurring: boolean;
  schedule_type: AutomationScheduleType;
  timezone: string;
  name?: string;
  description?: string;
  status: AutomationStatus;
  next_run_at?: string;
  last_run_at?: string;
  run_count: number;
  max_runs?: number;
  consecutive_failures: number;
  max_failures: number;
  last_error?: string;
  enabled_mcp_ids: string[];
  enabled_skill_ids: string[];
  enabled_kb_ids: string[];
  enabled_agent_ids: string[];
  sidebar_activated?: boolean;
  created_at: string;
  updated_at: string;
}

export interface AutomationChatGroup {
  taskId: string;
  taskName: string;
  runs: AutomationRun[];
  latestCompletedChatId: string | null;
  latestRunAt: number;
}

export interface AutomationRun {
  run_id: string;
  task_id: string;
  status: AutomationRunStatus;
  chat_id?: string;
  result_summary?: string;
  error_message?: string;
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
  usage?: Record<string, unknown>;
}

export interface AutomationNotification {
  id: string;
  task_id: string;
  task_name: string;
  status: 'success' | 'failed';
  summary: string;
  chat_id?: string;
  timestamp: number;
  read: boolean;
}

export interface ResourceItem {
  id: string;
  type: 'document' | 'image' | 'favorite';
  name: string;
  mime_type?: string;
  file_id?: string;
  download_url?: string;
  size?: number;
  source_kind?: 'user_upload' | 'ai_generated';
  knowledge_base_count?: number;
  knowledge_bases?: Array<{ kb_id: string; name: string }>;
  source_chat_id?: string;
  source_chat_title?: string;
  content_preview?: string;
  created_at: string;
}

// ── Plan Mode types ───────────────────────────────────────────────────────

export type PlanStatus = 'draft' | 'approved' | 'running' | 'completed' | 'failed' | 'cancelled';
export type PlanStepStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped';

export interface PlanStep {
  step_id: string;
  step_order: number;
  title: string;
  description: string;
  expected_tools: string[];
  expected_skills: string[];
  expected_agents: string[];
  status: PlanStepStatus;
  result_summary?: string;
  tool_calls?: ToolCall[];
  ai_output?: string;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
}

export interface Plan {
  plan_id: string;
  title: string;
  description: string;
  task_input: string;
  status: PlanStatus;
  total_steps: number;
  completed_steps: number;
  result_summary?: string;
  steps: PlanStep[];
  created_at: string;
  updated_at: string;
}

/* ───── Config 平台类型 ───── */

export interface UsageLogEntry {
  message_id: string;
  chat_id: string;
  user_id: string;
  username: string;
  session_title: string;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  has_error: boolean;
  created_at: string;
}

export interface UsageSummaryItem {
  group_key: string;
  display_name?: string;
  total_requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface BillingSummaryItem {
  group_key: string;
  display_name?: string;
  total_requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  prompt_cost: number;
  completion_cost: number;
  total_cost: number;
  currency: string;
}

export interface ModelPricingItem {
  pricing_id: string;
  model_name: string;
  display_name: string | null;
  input_price: number;
  output_price: number;
  currency: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminChatSession {
  chat_id: string;
  user_id: string;
  username: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface AdminChatMessage {
  message_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  model: string | null;
  tool_calls: unknown;
  usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } | null;
  error: unknown;
  metadata: unknown;
  created_at: string;
}
