import type { PanelKey } from '../types';

export type CatalogKind = Exclude<PanelKey, 'chat' | 'docs' | 'app_center' | 'share_records' | 'settings'>;

/** Tool names whose output should open in the right-side panel (not inline) */
export const PANEL_TOOL_NAMES = new Set([
  'query_database',
  'retrieve_dataset_content',
  'retrieve_local_kb',
  'list_datasets',
  'internet_search',
  'get_industry_news',
  'get_latest_ai_news',
  'get_chain_information',
  'search_company',
  'get_company_base_info',
  'get_company_business_analysis',
  'get_company_tech_insight',
  'get_company_funding',
  'get_company_risk_warning',
]);

/** Icons for each tool (under /icons/) */
export const TOOL_ICONS: Record<string, string> = {
  query_database: '/icons/数据库.png',
  retrieve_dataset_content: '/icons/知识库.png',
  retrieve_local_kb: '/icons/知识库.png',
  list_datasets: '/icons/知识库.png',
  internet_search: '/icons/互联网.png',
  web_fetch: '/icons/互联网-mcp.svg',
  get_industry_news: '/icons/资讯.png',
  get_latest_ai_news: '/icons/AI资讯.png',
  get_chain_information: '/icons/产业链.png',
  search_company: '/icons/产业链.png',
  get_company_base_info: '/icons/产业链.png',
  get_company_business_analysis: '/icons/产业链.png',
  get_company_tech_insight: '/icons/产业链.png',
  get_company_funding: '/icons/产业链.png',
  get_company_risk_warning: '/icons/产业链.png',
};

/** Frontend-local tool name overrides (higher priority than backend displayName) */
export const TOOL_NAME_OVERRIDES: Record<string, string> = {
  view_text_file: '读取文件',
  load_skill: '加载技能',
  execute_code: '代码执行',
  run_command: '执行命令',
  // MySpace tools (code execution mode)
  list_myspace_files: '浏览我的空间',
  stage_myspace_file: '导入文件到工作区',
  list_favorite_chats: '浏览收藏会话',
  get_chat_messages: '读取会话记录',
};

export interface CapabilityCard {
  id: string;
  label: string;
  icon: string;       // path under /home/ — SVGs are 56x56 with built-in colored circles
}

export const CAPABILITY_CARDS: CapabilityCard[] = [
  { id: 'knowledge', label: '知识检索', icon: '/home/企业调研.svg' },
  { id: 'portrait',  label: '企业画像', icon: '/home/企业画像.svg' },
  { id: 'policy',    label: '政策对比', icon: '/home/icon3.svg' },
  { id: 'compare',   label: '材料对比', icon: '/home/icon1.svg' },
  { id: 'data',      label: '数据分析', icon: '/home/icon2.svg' },
];

export const TOPIC_TAG_COLORS: Record<string, string> = {
  '综合咨询': 'default',
  '政策解读': 'blue',
  '事项办理': 'cyan',
  '材料比对': 'purple',
  '知识检索': 'geekblue',
  '数据分析': 'green',
};

export function isCatalogKind(kind: PanelKey): kind is CatalogKind {
  return kind === 'skills' || kind === 'agents' || kind === 'mcp' || kind === 'kb';
}

/** Max rounds to refresh the summary title */
export const SUMMARY_MAX_ROUNDS = 3;

/** API base URL (e.g. '/api') */
export const getApiBase = (): string =>
  (import.meta.env.VITE_API_BASE_URL as string || '/api').replace(/\/+$/, '');

/** Build a direct download URL for an artifact file. */
export const buildFileUrl = (fileId: string): string =>
  `${getApiBase()}/files/${fileId}`;
