import { RightOutlined } from '@ant-design/icons';
import { TOOL_NAME_OVERRIDES } from '../../utils/constants';
import { useChatStore } from '../../stores';
import { BrandLoader } from '../common';
import type { ChatMessage } from '../../types';

interface ToolProgressInlineProps {
  message: ChatMessage;
  /** Segment-level tool calls (subset) — if provided, only these are shown */
  toolCalls?: NonNullable<ChatMessage['toolCalls']>;
}

/**
 * Single-line inline summary for tool calls when dispatchProcessVisible is off.
 * Shows a pulse dot + tool names + ">" arrow. Clicking opens the Canvas timeline.
 */
export function ToolProgressInline({ message, toolCalls }: ToolProgressInlineProps) {
  const { toolDisplayNames, toolResultPanel, setToolResultPanel } = useChatStore();
  const tools = toolCalls ?? message.toolCalls ?? [];
  if (tools.length === 0) return null;

  const anyRunning = tools.some(t => t.status === 'running');
  const names = tools
    .map(t => t.displayName || TOOL_NAME_OVERRIDES[t.name] || toolDisplayNames[t.name] || t.name)
    .filter((v, i, a) => a.indexOf(v) === i)   // dedupe
    .slice(0, 3);
  const label = names.join('、') + (tools.length > 3 ? ` 等${tools.length}项` : '');

  const panelKey = `__progress_timeline__-${message.ts}`;
  const isOpen = toolResultPanel?.key === panelKey;

  const handleClick = () => {
    if (isOpen) {
      setToolResultPanel(null);
    } else {
      setToolResultPanel({
        key: panelKey,
        toolName: '__progress_timeline__',
        displayName: '工具调用',
        output: { message, toolCalls: tools },
      });
    }
  };

  return (
    <div className="jx-inlineSummary" role="button" tabIndex={0} onClick={handleClick}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(); } }}>
      <BrandLoader done={!anyRunning} label={anyRunning ? '正在调用工具' : '工具调用完成'} />
      <span className="jx-inlineSummaryText">
        {anyRunning ? `正在调用 ${label}` : `已调用 ${label}`}
      </span>
      <RightOutlined className="jx-inlineSummaryArrow" />
    </div>
  );
}
