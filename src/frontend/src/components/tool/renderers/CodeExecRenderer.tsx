import { useState, useMemo } from 'react';
import {
  CopyOutlined, CheckOutlined,
  ExpandOutlined, CaretRightOutlined,
} from '@ant-design/icons';
import type { ToolCall } from '../../../types';
import { parseExecOutput, extractCodeFromInput } from '../../../utils/codeExecParser';
import { LANG_LABELS, formatTime, highlightCode } from '../../../utils/codeExecUtils';
import { useCodeArtifactStore } from '../../../stores';
import { computeEffectiveStatus } from './utils';

/**
 * Standalone body content for code execution — used inside ToolCallRow.
 * Renders code block + output without any outer card or header.
 */
export function CodeExecBodyContent({ tool, isStreaming }: { tool: ToolCall; isStreaming?: boolean }) {
  const [copied, setCopied] = useState(false);
  const openCodeArtifact = useCodeArtifactStore((s) => s.openCodeArtifact);

  const { code, language } = useMemo(
    () => extractCodeFromInput(tool.name, tool.input),
    [tool.name, tool.input],
  );

  const parsed = useMemo(
    () => (tool.output ? parseExecOutput(tool.output) : null),
    [tool.output],
  );

  const isRunning = computeEffectiveStatus(tool, isStreaming) === 'running';
  const exitCode = parsed?.exitCode ?? 0;
  const isSuccess = parsed ? exitCode === 0 : true;

  const highlightedCode = useMemo(() => highlightCode(code, language), [code, language]);
  const lineCount = useMemo(() => (code ? code.split('\n').length : 0), [code]);

  const handleCopy = () => {
    if (!code) return;
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = code; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
    };
    (navigator.clipboard ? navigator.clipboard.writeText(code).catch(fallback) : (fallback(), undefined));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleExpandToPanel = () => {
    if (!parsed) return;
    openCodeArtifact({
      toolKey: tool.id || `tool_${tool.timestamp}`,
      code, language,
      stdout: parsed.stdout, stderr: parsed.stderr,
      exitCode: parsed.exitCode, executionTimeMs: parsed.executionTimeMs,
      files: parsed.files,
      isCommand: tool.name === 'run_command',
    });
  };

  return (
    <div className="jx-ce-cardBody">
      {/* Code block */}
      {code && (
        <div className="jx-ce-codeSection">
          <div className="jx-ce-codeBar">
            <div className="jx-ce-codeBarLeft">
              <span className="jx-ce-langDot" data-lang={language} />
              <span className="jx-ce-langLabel">{LANG_LABELS[language] || language}</span>
              {lineCount > 0 && <span className="jx-ce-lineCount">{lineCount} 行</span>}
            </div>
            <div className="jx-ce-codeBarActions" style={{ opacity: 1 }}>
              {parsed && (
                <button
                  className="jx-ce-barBtn jx-ce-barBtn--sm"
                  onClick={handleExpandToPanel}
                  title="在面板中查看"
                >
                  <ExpandOutlined />
                </button>
              )}
              <button
                className={`jx-ce-barBtn${copied ? ' jx-ce-barBtn--copied' : ''}`}
                onClick={handleCopy}
                title={copied ? '已复制' : '复制代码'}
              >
                {copied ? <CheckOutlined /> : <CopyOutlined />}
              </button>
            </div>
          </div>
          <div className="jx-ce-codeWrap">
            <div className="jx-ce-lineNums" aria-hidden="true">
              {Array.from({ length: lineCount }, (_, i) => <span key={i}>{i + 1}</span>)}
            </div>
            <pre className="jx-ce-code">
              <code className={`hljs language-${language}`} dangerouslySetInnerHTML={{ __html: highlightedCode }} />
            </pre>
          </div>
        </div>
      )}

      {/* Running indicator */}
      {isRunning && !parsed && (
        <div className="jx-ce-running">
          <div className="jx-ce-runningPulse" />
          <span className="jx-ce-runningText">
            <CaretRightOutlined className="jx-ce-runningIcon" />
            正在执行
          </span>
        </div>
      )}

      {/* Output */}
      {parsed && (parsed.stdout || parsed.stderr) && (
        <div className="jx-ce-outputSection">
          <div className="jx-ce-outputBar">
            <span className="jx-ce-outputLabel">
              {parsed.stderr && !parsed.stdout ? '错误输出' : '输出结果'}
            </span>
          </div>
          {parsed.stdout && <pre className="jx-ce-stdout">{parsed.stdout}</pre>}
          {parsed.stderr && <pre className="jx-ce-stderr">{parsed.stderr}</pre>}
        </div>
      )}

      {/* No output */}
      {parsed && !parsed.stdout && !parsed.stderr && parsed.files.length === 0 && (
        <div className="jx-ce-empty">无输出</div>
      )}

      {/* Footer */}
      {parsed && (
        <div className="jx-ce-footer">
          <span className={`jx-ce-statusDot ${isSuccess ? 'success' : 'error'}`} />
          <span className="jx-ce-statusText">{isSuccess ? '已完成' : `退出码 ${exitCode}`}</span>
          {parsed.executionTimeMs > 0 && <span className="jx-ce-time">{formatTime(parsed.executionTimeMs)}</span>}
        </div>
      )}
    </div>
  );
}
