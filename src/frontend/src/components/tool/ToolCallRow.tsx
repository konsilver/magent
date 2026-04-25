import { useMemo, useState } from 'react';
import { CheckOutlined, CloseOutlined, LoadingOutlined } from '@ant-design/icons';
import type { ToolCall } from '../../types';
import { TOOL_NAME_OVERRIDES } from '../../utils/constants';
import { useChatStore, useUIStore } from '../../stores';
import { renderToolOutputBody } from './ToolOutputRenderer';
import { parseExecOutput } from '../../utils/codeExecParser';
import { LANG_LABELS, formatTime } from '../../utils/codeExecUtils';
import { CodeExecBodyContent } from './renderers/CodeExecRenderer';
import { MySpaceBodyContent } from './renderers/MySpaceRenderer';
import { renderInternetSearchInline } from './renderers/SearchRenderer';
import { coerceOutput, computeEffectiveStatus } from './renderers/utils';

/**
 * Returns a `{ prefix, value, count }` label descriptor for the header row.
 * `value` is rendered as a subtle chip; omitted when empty.
 */
function getRowLabel(
  tool: ToolCall,
  parsed: unknown,
  displayName: string,
): { prefix: string; value: string; count?: number } {
  if (!tool.output || tool.status === 'running') return { prefix: displayName, value: '' };

  try {
    const out = parsed as any;

    switch (tool.name) {
      case 'internet_search': {
        const sr = out?.result ?? out;
        const rawQuery = String(sr?.query ?? out?.query ?? '').trim();
        const query = rawQuery.length > 60 ? rawQuery.slice(0, 60) + '…' : rawQuery;
        const count = Array.isArray(sr?.results) ? sr.results.length : undefined;
        return { prefix: '搜索页面：', value: query || displayName, count };
      }
      case 'retrieve_dataset_content': {
        const items = out?.items;
        return { prefix: '知识库检索', value: '', count: Array.isArray(items) ? items.length : undefined };
      }
      case 'retrieve_local_kb': {
        const items = Array.isArray(out) ? out : out?.items;
        return { prefix: '本地知识库', value: '', count: Array.isArray(items) ? items.length : undefined };
      }
      case 'get_industry_news':
        return { prefix: '产业资讯', value: '', count: Array.isArray(out?.items) ? out.items.length : undefined };
      case 'get_latest_ai_news':
        return { prefix: 'AI 热点资讯', value: '', count: Array.isArray(out?.items) ? out.items.length : undefined };
      case 'search_company':
        return { prefix: '企业搜索', value: '', count: Array.isArray(out?.items) ? out.items.length : undefined };
      case 'list_datasets': {
        const n =
          (Array.isArray(out?.public_datasets) ? out.public_datasets.length : 0) +
          (Array.isArray(out?.private_datasets) ? out.private_datasets.length : 0);
        return { prefix: '知识库列表', value: '', count: n };
      }
      case 'execute_code':
      case 'run_command': {
        const p = parseExecOutput(tool.output);
        const rawLang = (tool.input as any)?.language || 'python';
        const lang = LANG_LABELS[rawLang] || rawLang;
        const label = tool.name === 'run_command' ? '执行命令' : '代码执行';
        return {
          prefix: label + '：',
          value: p.exitCode === 0
            ? `${lang}${p.executionTimeMs > 0 ? ` · ${formatTime(p.executionTimeMs)}` : ''}`
            : `失败 (exit ${p.exitCode})`,
        };
      }
      case 'load_skill': {
        const sn = (tool.input as any)?.skill_name || (tool.input as any)?.name || '';
        return { prefix: '激活技能：', value: sn || '' };
      }
      case 'view_text_file': {
        const fp = (tool.input as any)?.file_name || (tool.input as any)?.path || '';
        const fn = fp ? String(fp).split('/').pop() || '' : '';
        return { prefix: '读取文件：', value: fn };
      }
      case 'generate_chart_tool': return { prefix: '生成图表', value: '' };
      case 'export_report_to_docx': return { prefix: '导出 Word 报告', value: '' };
      case 'export_table_to_excel': return { prefix: '导出 Excel 表格', value: '' };
      case 'web_fetch': {
        let domain = '';
        try { domain = new URL(out?.url || '').hostname; } catch { /* noop */ }
        return { prefix: '获取网页：', value: domain };
      }
      case 'call_subagent': {
        const an = tool.displayName?.split('：')[1]?.trim() || '';
        return { prefix: '调用智能体：', value: an };
      }
      case 'list_myspace_files': return { prefix: '读取我的空间', value: '' };
      case 'stage_myspace_file': {
        const fn = (tool.input as any)?.file_path?.split('/').pop() || '';
        return { prefix: '导入文件：', value: fn };
      }
      case 'list_favorite_chats': return { prefix: '获取收藏会话', value: '' };
      case 'get_chat_messages': return { prefix: '读取会话记录', value: '' };
      case 'get_chain_information': return { prefix: '产业链分析', value: '' };
      case 'get_company_base_info': return { prefix: '企业基本信息', value: '' };
      case 'get_company_business_analysis': return { prefix: '企业经营分析', value: '' };
      case 'get_company_tech_insight': return { prefix: '企业技术洞察', value: '' };
      case 'get_company_funding': return { prefix: '资金穿透分析', value: '' };
      case 'get_company_risk_warning': return { prefix: '风险预警', value: '' };
      case 'query_database': return { prefix: '数据库查询', value: '' };
      case 'run_skill_script': return { prefix: '执行技能脚本', value: '' };
      case 'get_skills': return { prefix: '获取技能列表', value: '' };
      case 'get_agents': return { prefix: '获取智能体列表', value: '' };
      case 'get_mcp_tools': return { prefix: '获取 MCP 工具', value: '' };
      default: return { prefix: displayName, value: '' };
    }
  } catch {
    return { prefix: displayName, value: '' };
  }
}

interface ToolCallRowProps {
  tool: ToolCall;
  isStreaming?: boolean;
}

export function ToolCallRow({ tool, isStreaming }: ToolCallRowProps) {
  const [expanded, setExpanded] = useState(false);
  const toolDisplayNames = useChatStore((s) => s.toolDisplayNames);
  const setDetailModal = useUIStore((s) => s.setDetailModal);

  const effectiveStatus = computeEffectiveStatus(tool, isStreaming);
  const parsed = useMemo(() => coerceOutput(tool.output), [tool.output]);

  const displayName =
    tool.displayName ||
    TOOL_NAME_OVERRIDES[tool.name] ||
    toolDisplayNames[tool.name] ||
    tool.name;

  const { prefix, value, count } = useMemo(
    () => getRowLabel(tool, parsed, displayName),
    [tool, parsed, displayName],
  );

  const hasOutput = !!tool.output;
  const canExpand = hasOutput && effectiveStatus !== 'running';
  const toggle = () => { if (canExpand) setExpanded((v) => !v); };

  const renderBody = () => {
    if (!tool.output) return null;
    switch (tool.name) {
      case 'execute_code':
      case 'run_command':
        return <CodeExecBodyContent tool={tool} isStreaming={isStreaming} />;
      case 'list_myspace_files':
      case 'stage_myspace_file':
      case 'list_favorite_chats':
      case 'get_chat_messages':
        return <MySpaceBodyContent tool={tool} />;
      case 'internet_search':
        return renderInternetSearchInline(parsed);
      default:
        return renderToolOutputBody(tool.name, parsed, setDetailModal);
    }
  };

  return (
    <div className={`jx-tcr${effectiveStatus === 'error' ? ' jx-tcr--error' : ''}`}>
      <div
        className={`jx-tcr-header${expanded ? ' jx-tcr-header--open' : ''}`}
        role={canExpand ? 'button' : undefined}
        tabIndex={canExpand ? 0 : undefined}
        onClick={toggle}
        onKeyDown={(e) => {
          if (canExpand && (e.key === 'Enter' || e.key === ' ')) {
            e.preventDefault();
            toggle();
          }
        }}
      >
        <span className="jx-tcr-status">
          {effectiveStatus === 'running' && <LoadingOutlined spin className="jx-tcr-icon jx-tcr-icon--running" />}
          {effectiveStatus === 'success' && <CheckOutlined className="jx-tcr-icon jx-tcr-icon--success" />}
          {effectiveStatus === 'error' && <CloseOutlined className="jx-tcr-icon jx-tcr-icon--error" />}
        </span>
        <span className="jx-tcr-label">
          <span className="jx-tcr-prefix">{prefix}</span>
          {value && <span className="jx-tcr-value">{value}</span>}
          {count != null && <span className="jx-tcr-count">&nbsp;({count})</span>}
        </span>
        {canExpand && (
          <span className={`jx-tcr-arrow${expanded ? ' jx-tcr-arrow--open' : ''}`} />
        )}
      </div>

      {hasOutput && (
        <div className={`jx-expandWrap${expanded ? ' jx-expandWrap--open' : ''}`}>
          <div className="jx-tcr-body">
            {expanded && renderBody()}
          </div>
        </div>
      )}
    </div>
  );
}
