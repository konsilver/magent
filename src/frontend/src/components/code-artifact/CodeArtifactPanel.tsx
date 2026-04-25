import { useCallback, useMemo, useRef, useState } from 'react';
import {
  CloseOutlined, ExpandOutlined, CompressOutlined,
  CopyOutlined, CheckOutlined, DownloadOutlined,
  CodeOutlined, EyeOutlined,
} from '@ant-design/icons';
import { Segmented } from 'antd';
import { useCodeArtifactStore } from '../../stores/codeArtifactStore';
import { useUIStore } from '../../stores';
import { getFileIconSrc } from '../../utils/fileIcon';
import { LANG_LABELS, formatFileSize, formatTime, highlightCode, effectiveApiUrl } from '../../utils/codeExecUtils';

export function CodeArtifactPanel() {
  const { isOpen, artifact, activeView, setActiveView, closeCodeArtifact } = useCodeArtifactStore();
  const { setPreviewImage } = useUIStore();
  const [expanded, setExpanded] = useState(false);
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = panelRef.current?.offsetWidth || 600;
    const onMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX;
      setDragWidth(Math.max(400, Math.min(startWidth + delta, window.innerWidth * 0.85)));
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, []);

  const handleCopy = () => {
    if (!artifact?.code) return;
    navigator.clipboard?.writeText(artifact.code).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const highlightedCode = useMemo(
    () => highlightCode(artifact?.code ?? '', artifact?.language ?? ''),
    [artifact?.code, artifact?.language],
  );

  const lineCount = useMemo(() => artifact?.code ? artifact.code.split('\n').length : 0, [artifact?.code]);

  if (!isOpen || !artifact) return null;

  const isSuccess = artifact.exitCode === 0;
  const imageFiles = artifact.files.filter(f => f.mime_type.startsWith('image/'));
  const otherFiles = artifact.files.filter(f => !f.mime_type.startsWith('image/'));

  return (
    <div
      ref={panelRef}
      className={`jx-cap ${expanded ? 'jx-cap--expanded' : ''}`}
      style={dragWidth && !expanded ? { width: dragWidth } : undefined}
    >
      <div className="jx-cap-dragHandle" onMouseDown={handleDragStart} />

      {/* ── Header ── */}
      <div className="jx-cap-header">
        <div className="jx-cap-headerLeft">
          <div className="jx-cap-iconWrap">
            <CodeOutlined />
          </div>
          <div className="jx-cap-headerMeta">
            <span className="jx-cap-title">
              {artifact.isCommand ? '执行命令' : '代码执行'}
            </span>
            <span className="jx-cap-subtitle">
              {LANG_LABELS[artifact.language] || artifact.language}
              {artifact.executionTimeMs > 0 && ` · ${formatTime(artifact.executionTimeMs)}`}
            </span>
          </div>
        </div>
        <div className="jx-cap-headerActions">
          <Segmented
            size="small"
            value={activeView}
            onChange={(v) => setActiveView(v as 'code' | 'preview')}
            options={[
              { label: <><CodeOutlined /> 代码</>, value: 'code' },
              { label: <><EyeOutlined /> 结果</>, value: 'preview' },
            ]}
          />
          <button className="jx-cap-actionBtn" onClick={() => setExpanded(!expanded)} title={expanded ? '还原' : '最大化'}>
            {expanded ? <CompressOutlined /> : <ExpandOutlined />}
          </button>
          <button className="jx-cap-actionBtn jx-cap-closeBtn" onClick={closeCodeArtifact} title="关闭">
            <CloseOutlined />
          </button>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="jx-cap-body">
        {activeView === 'code' && (
          <div className="jx-cap-codeReadonly">
            <div className="jx-cap-codeBar">
              <div className="jx-cap-codeBarLeft">
                <span className="jx-ce-langDot" data-lang={artifact.language} />
                <span className="jx-ce-langLabel">{LANG_LABELS[artifact.language] || artifact.language}</span>
                {lineCount > 0 && <span className="jx-ce-lineCount">{lineCount} 行</span>}
              </div>
              <button
                className={`jx-ce-barBtn${copied ? ' jx-ce-barBtn--copied' : ''}`}
                onClick={handleCopy}
                title={copied ? '已复制' : '复制代码'}
              >
                {copied ? <CheckOutlined /> : <CopyOutlined />}
              </button>
            </div>
            {artifact.code ? (
              <div className="jx-cap-codeScrollWrap">
                <div className="jx-ce-lineNums" aria-hidden="true">
                  {Array.from({ length: lineCount }, (_, i) => <span key={i}>{i + 1}</span>)}
                </div>
                <pre className="jx-ce-code">
                  <code className={`hljs language-${artifact.language}`} dangerouslySetInnerHTML={{ __html: highlightedCode }} />
                </pre>
              </div>
            ) : (
              <div className="jx-ce-empty" style={{ padding: '40px 0' }}>暂无代码内容</div>
            )}
          </div>
        )}

        {activeView === 'preview' && (
          <div className="jx-cap-preview">
            {artifact.stdout && (
              <div className="jx-cap-section">
                <div className="jx-cap-sectionLabel">标准输出</div>
                <pre className="jx-ce-stdout" style={{ maxHeight: 'none', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-border)' }}>
                  {artifact.stdout}
                </pre>
              </div>
            )}
            {artifact.stderr && (
              <div className="jx-cap-section">
                <div className="jx-cap-sectionLabel jx-cap-sectionLabel--error">错误输出</div>
                <pre className="jx-ce-stderr" style={{ borderRadius: 'var(--radius-sm)' }}>
                  {artifact.stderr}
                </pre>
              </div>
            )}
            {imageFiles.length > 0 && (
              <div className="jx-cap-section">
                <div className="jx-cap-sectionLabel">图片</div>
                <div className="jx-ce-imageGrid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
                  {imageFiles.map((f) => {
                    const url = `${effectiveApiUrl}${f.url}`;
                    return (
                      <div key={f.file_id} className="jx-ce-imageCard">
                        <img src={url} alt={f.name} className="jx-ce-imageThumb"
                          style={{ maxHeight: 360 }}
                          onClick={() => setPreviewImage({ url, name: f.name })} />
                        <div className="jx-ce-imageMeta">
                          <span className="jx-ce-imageMetaName">{f.name}</span>
                          <a href={url} download={f.name} className="jx-ce-imageMetaDl"
                            onClick={(e) => e.stopPropagation()}><DownloadOutlined /></a>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {otherFiles.length > 0 && (
              <div className="jx-cap-section">
                <div className="jx-cap-sectionLabel">文件</div>
                <div className="jx-ce-fileList">
                  {otherFiles.map((f) => {
                    const url = `${effectiveApiUrl}${f.url}`;
                    return (
                      <a key={f.file_id} href={url} download={f.name} className="jx-ce-fileRow">
                        <img src={getFileIconSrc(f.name)} width="20" height="20" alt="" className="jx-ce-fileIcon" />
                        <span className="jx-ce-fileName">{f.name}</span>
                        <span className="jx-ce-fileSize">{formatFileSize(f.size)}</span>
                        <DownloadOutlined className="jx-ce-fileDlIcon" />
                      </a>
                    );
                  })}
                </div>
              </div>
            )}
            {!artifact.stdout && !artifact.stderr && artifact.files.length === 0 && (
              <div className="jx-ce-empty" style={{ padding: '40px 0' }}>无输出</div>
            )}
          </div>
        )}
      </div>

      {/* ── Footer ── */}
      <div className="jx-cap-footer">
        <span className={`jx-ce-statusDot ${isSuccess ? 'success' : 'error'}`} />
        <span className="jx-cap-footerText">{isSuccess ? '执行成功' : `退出码 ${artifact.exitCode}`}</span>
        {artifact.executionTimeMs > 0 && (
          <span className="jx-cap-footerTime">{formatTime(artifact.executionTimeMs)}</span>
        )}
      </div>
    </div>
  );
}
