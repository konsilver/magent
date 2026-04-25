import {
  Button, Input, message,
} from 'antd';
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'motion/react';

import {
  BulbOutlined, BulbFilled,
  DownOutlined, UpOutlined,
  CopyOutlined, CheckOutlined,
  DownloadOutlined, EyeOutlined,
  LikeOutlined, DislikeOutlined, LikeFilled, DislikeFilled,
  ExportOutlined, ShareAltOutlined, RedoOutlined,
  EditOutlined, SyncOutlined,
} from '@ant-design/icons';
import { parseExecOutput } from '../../utils/codeExecParser';
import { extractArtifactOutputs } from '../../utils/fileParser';
import { getFileIconSrc } from '../../utils/fileIcon';
import { getContextualCitations, getCitationOutputSlice, resolveConversationCitations } from '../../utils/citations';
import { ToolCallRow } from '../tool/ToolCallRow';
import { ToolProgressInline } from '../tool/ToolProgressInline';
import { ThinkingInline } from './ThinkingInline';
import { PlanCard } from './PlanCard';
import { CitationMarkdownBlock } from '../citation';
import { FileAttachmentCard } from '../file';
import { BrandLoader } from '../common';
import { useChatStore, useUIStore, useCanvasStore } from '../../stores';
import { authFetch } from '../../api';
import type { ChatMessage, CitationItem } from '../../types';

const effectiveApiUrl = (import.meta.env.VITE_API_BASE_URL as string || '').trim() || '/api';

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderFileIcon(name: string) {
  return (
    <div className="jx-dlCard-fileBadge">
      <img src={getFileIconSrc(name)} width="28" height="28" alt="" aria-hidden="true" className="jx-dlCard-fileIcon" />
    </div>
  );
}

interface MessageBubbleProps {
  m: ChatMessage;
  messageIndex: number;
  currentChatId: string;
  send: (text?: string) => void;
  exportChatRecord: (id: string) => Promise<void>;
  regenerate?: (messageIndex: number) => void;
  editAndResend?: (messageIndex: number, newContent: string) => void;
}

export function MessageBubble({ m, messageIndex, currentChatId, send, exportChatRecord, regenerate, editAndResend }: MessageBubbleProps) {
  const contentRef = useRef<HTMLDivElement | null>(null);
  const selectionRangeRef = useRef<Range | null>(null);
  const selectionCopiedTimerRef = useRef<number | null>(null);
  const selectionCopiedTextRef = useRef<string | null>(null);
  const selectionPointerDownRef = useRef<{ x: number; y: number; hadSelection: boolean } | null>(null);
  const selectionGuardUntilRef = useRef(0);
  const selectionToolbarRef = useRef<{ x: number; y: number; text: string } | null>(null);
  const [selectionToolbar, setSelectionToolbar] = useState<{ x: number; y: number; text: string } | null>(null);
  const [selectionCopied, setSelectionCopied] = useState(false);
  const {
    expandedThinking, toggleThinking,
    thinkingMode,
    copiedMsg, setCopiedMsg,
    feedbackMap, setFeedbackMap,
    dislikingTs, setDislikingTs,
    dislikeComment, setDislikeComment,
    shareSelectionMode, selectedShareMessageTs,
    toggleShareMessageTs, startShareSelectionWithAll,
    setQuotedFollowUp,
  } = useChatStore();
  const { setDetailModal, setPreviewImage, dispatchProcessVisible } = useUIStore();
  const openCanvas = useCanvasStore((s) => s.openCanvas);
  const chatMessages = useChatStore(state => state.store.chats[currentChatId]?.messages ?? []);
  const { editingMessageTs, setEditingMessageTs } = useChatStore();
  const [editText, setEditText] = useState('');
  const shareSelected = selectedShareMessageTs.has(m.ts);
  const isEditing = editingMessageTs === m.ts;
  const messagePlainText = m.segments
    ? m.segments.filter(s => s.type === 'text').map(s => s.content || '').join('\n\n') || m.content
    : m.content;

  const hideSelectionToolbar = () => {
    selectionToolbarRef.current = null;
    selectionCopiedTextRef.current = null;
    setSelectionToolbar(null);
    setSelectionCopied(false);
  };

  const guardSelectionState = () => {
    selectionGuardUntilRef.current = Date.now() + 80;
  };

  const restoreSelectionRange = () => {
    if (!selectionRangeRef.current) return;
    const selection = window.getSelection();
    if (!selection) return;
    selection.removeAllRanges();
    selection.addRange(selectionRangeRef.current);
  };

  const hasSelectionInsideContent = () => {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed || !contentRef.current) {
      return false;
    }
    const range = selection.getRangeAt(0);
    return contentRef.current.contains(range.startContainer) || contentRef.current.contains(range.endContainer);
  };

  const clearSelectionState = () => {
    selectionGuardUntilRef.current = 0;
    selectionRangeRef.current = null;
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
    }
    hideSelectionToolbar();
  };

  const getStoredSelectionText = () => {
    const current = window.getSelection()?.toString().trim();
    if (current) return current;
    const stored = selectionRangeRef.current?.toString().trim();
    return stored || '';
  };

  /**
   * Check if the current window selection overlaps with this message's
   * contentRef and, if so, show the toolbar.  Called from both mouseup
   * and selectionchange so we catch every path (mouse, keyboard, touch).
   */
  const checkAndShowToolbar = (fallbackRange?: Range | null) => {
    const selection = window.getSelection();
    let range: Range | null = null;
    let selectedText = '';

    if (selection && selection.rangeCount > 0 && !selection.isCollapsed) {
      range = selection.getRangeAt(0).cloneRange();
      selectedText = selection.toString().trim();
    } else if (fallbackRange) {
      range = fallbackRange.cloneRange();
      selectedText = range.toString().trim();
    }

    if (!range || !selectedText) {
      hideSelectionToolbar();
      selectionRangeRef.current = null;
      return;
    }

    // At least one endpoint of the selection must be inside our content
    const anchorNode = selection && !selection.isCollapsed ? selection.anchorNode : range.startContainer;
    const focusNode = selection && !selection.isCollapsed ? selection.focusNode : range.endContainer;
    if (!contentRef.current || !anchorNode || !focusNode) {
      hideSelectionToolbar();
      selectionRangeRef.current = null;
      return;
    }

    const anchorInside = contentRef.current.contains(anchorNode);
    const focusInside = contentRef.current.contains(focusNode);
    if (!anchorInside && !focusInside) {
      // Selection is entirely outside this bubble — ignore
      hideSelectionToolbar();
      selectionRangeRef.current = null;
      return;
    }

    selectionRangeRef.current = range.cloneRange();
    const rect = range.getBoundingClientRect();
    if (!rect.width && !rect.height) {
      hideSelectionToolbar();
      selectionRangeRef.current = null;
      return;
    }

    const pos = { x: rect.left + rect.width / 2, y: rect.top - 12, text: selectedText };
    selectionToolbarRef.current = pos;
    guardSelectionState();
    if (selectionCopiedTextRef.current !== selectedText) {
      setSelectionCopied(false);
    }
    setSelectionToolbar(pos);
  };

  const handleSelectionFollowUpQuote = () => {
    const quoteText = getStoredSelectionText() || selectionToolbar?.text || '';
    if (!quoteText) return;
    setQuotedFollowUp({ text: quoteText, ts: m.ts });
    restoreSelectionRange();
  };

  const handleSelectionToolbarMouseDown = (
    e: MouseEvent<HTMLButtonElement>,
    action: () => void,
  ) => {
    e.preventDefault();
    e.stopPropagation();
    guardSelectionState();
    restoreSelectionRange();
    action();
  };

  // Defensive: restore selection after React re-render caused by toolbar state update.
  // When setSelectionToolbar(pos) triggers a re-render, the DOM reconciliation may
  // cause the browser to lose the active selection. This effect restores it.
  useLayoutEffect(() => {
    if (selectionToolbar && selectionRangeRef.current) {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        guardSelectionState();
        restoreSelectionRange();
      }
    }
  }, [selectionToolbar]);

  // ---------- Selection event listeners ----------
  // Registered ONCE ([] deps) so listeners are never torn down and re-added.
  // This avoids the race where React's async effect cleanup removes the
  // selectionchange listener while a pending selectionchange event fires
  // and clears the selection.
  useEffect(() => {
    const handleSelectionChange = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) {
        if (Date.now() < selectionGuardUntilRef.current) {
          return;
        }
        // Only update state if toolbar is currently shown (avoid unnecessary re-renders)
        if (selectionToolbarRef.current) {
          selectionToolbarRef.current = null;
          selectionRangeRef.current = null;
          setSelectionToolbar(null);
          setSelectionCopied(false);
        }
      }
    };

    const handleWindowScroll = () => {
      // Read ref (always current) instead of closed-over state
      if (selectionToolbarRef.current && selectionRangeRef.current) {
        checkAndShowToolbar(selectionRangeRef.current);
      }
    };

    document.addEventListener('selectionchange', handleSelectionChange);
    window.addEventListener('scroll', handleWindowScroll, true);
    window.addEventListener('resize', handleWindowScroll);

    return () => {
      if (selectionCopiedTimerRef.current) {
        window.clearTimeout(selectionCopiedTimerRef.current);
      }
      document.removeEventListener('selectionchange', handleSelectionChange);
      window.removeEventListener('scroll', handleWindowScroll, true);
      window.removeEventListener('resize', handleWindowScroll);
    };
  }, []);

  /** Render a thinking block */
  const renderThinkingBlock = (content: string, thinkKey: string, isActiveThinking: boolean) => {
    const isExpanded = isActiveThinking || expandedThinking.has(thinkKey);
    const toggleThink = () => {
      if (isActiveThinking) return;
      toggleThinking(thinkKey);
    };
    return (
      <div key={thinkKey} className="jx-thinkingBlock">
        <div className={`jx-thinkingBlockHeader${isActiveThinking ? ' jx-thinkingActive' : ''}`}
          role="button" tabIndex={0} onClick={toggleThink}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleThink(); } }}>
          <div className="jx-thinkingHeaderLeft">
            {isActiveThinking ? <div className="jx-thinkingSpinner" /> : <BulbFilled className="jx-thinkingIcon" />}
            <span className="jx-thinkingLabel">{isActiveThinking ? '正在思考…' : '思考过程'}</span>
          </div>
          {!isActiveThinking && (isExpanded ? <UpOutlined className="jx-expandIcon" /> : <DownOutlined className="jx-expandIcon" />)}
        </div>
        <div className={`jx-expandWrap${isExpanded ? ' jx-expandWrap--open' : ''}`}>
          <div className="jx-thinkingContent" ref={(el) => { if (el && isActiveThinking) el.scrollTop = el.scrollHeight; }}>
            {(isExpanded || isActiveThinking) && content}
          </div>
        </div>
      </div>
    );
  };

  /** Render file download/image artifact cards */
  const renderArtifactCards = () => {
    if (!m.toolCalls || m.isStreaming) return null;
    const artifactMap = new Map<string, any>();
    const pushArtifact = (artifact: any) => {
      if (!artifact?.file_id) return;
      artifactMap.set(String(artifact.file_id), artifact);
    };
    for (const tool of m.toolCalls) {
      const out = tool.output as any;
      if (tool.status !== 'success' && tool.status != null) {
        continue;
      }
      for (const artifact of extractArtifactOutputs(out)) {
        pushArtifact(artifact);
      }
      // Code execution files: parse from text output
      if ((tool.name === 'execute_code' || tool.name === 'run_command') && tool.output) {
        const parsed = parseExecOutput(tool.output);
        for (const f of parsed.files) {
          pushArtifact({ ok: true, file_id: f.file_id, url: f.url, name: f.name, mime_type: f.mime_type, size: f.size });
        }
      }
    }
    const artifacts = Array.from(artifactMap.values());
    if (artifacts.length === 0) return null;

    return (
      <div className="jx-artifactCards">
        {artifacts.map((art: any) => {
          const isImage = typeof art.mime_type === 'string' && art.mime_type.startsWith('image/');
          const fileUrl = `${effectiveApiUrl}${art.url}`;

          if (isImage) {
            return (
              <div key={art.file_id} className="jx-imgCard" role="button" tabIndex={0}
                aria-label={`查看大图：${art.name || '生成图片'}`}
                onClick={() => setPreviewImage({ url: fileUrl, name: art.name || '生成图片' })}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setPreviewImage({ url: fileUrl, name: art.name || '生成图片' }); } }}>
                <img src={fileUrl} alt={art.name} className="jx-imgCard-img" loading="lazy" />
                <div className="jx-imgCard-overlay" />
                <div className="jx-imgCard-overlayBtns">
                  <button className="jx-imgCard-overlayBtn" title="复制图片"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const getBlob = (): Promise<Blob> => new Promise((resolve, reject) => {
                        const i = new Image();
                        i.crossOrigin = 'anonymous';
                        i.onload = () => {
                          const c = document.createElement('canvas');
                          c.width = i.naturalWidth; c.height = i.naturalHeight;
                          c.getContext('2d')!.drawImage(i, 0, 0);
                          c.toBlob(b => b ? resolve(b) : reject(new Error('toBlob failed')), 'image/png');
                        };
                        i.onerror = () => reject(new Error('load failed'));
                        i.src = fileUrl;
                      });
                      if (navigator.clipboard && window.ClipboardItem) {
                        try {
                          const blob = await getBlob();
                          await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
                          message.success('图片已复制到剪贴板');
                          return;
                        } catch { /* fall through */ }
                      }
                      try {
                        await new Promise<void>((resolve, reject) => {
                          const container = document.createElement('div');
                          container.setAttribute('contenteditable', 'true');
                          container.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none;';
                          const img = document.createElement('img');
                          img.onload = () => {
                            document.body.appendChild(container);
                            container.focus();
                            const sel = window.getSelection()!;
                            const range = document.createRange();
                            range.selectNodeContents(container);
                            sel.removeAllRanges();
                            sel.addRange(range);
                            const ok = document.execCommand('copy');
                            sel.removeAllRanges();
                            document.body.removeChild(container);
                            ok ? resolve() : reject(new Error('execCommand failed'));
                          };
                          img.onerror = () => reject(new Error('image load failed'));
                          img.src = fileUrl;
                          container.appendChild(img);
                        });
                        message.success('图片已复制到剪贴板');
                      } catch {
                        message.error('复制失败，请右键图片选择"复制图片"');
                      }
                    }}>
                    <CopyOutlined />
                  </button>
                  <a href={fileUrl} download={art.name} className="jx-imgCard-overlayBtn" title="下载图片"
                    onClick={(e) => e.stopPropagation()}>
                    <DownloadOutlined />
                  </a>
                </div>
              </div>
            );
          }

          return (
            <div key={art.file_id} className="jx-dlCard">
              <div className="jx-dlCard-left">
                {renderFileIcon(art.name)}
                <div className="jx-dlCard-meta">
                  <span className="jx-dlCard-name">{art.name}</span>
                  {art.size && <span className="jx-dlCard-size">{formatFileSize(art.size)}</span>}
                </div>
              </div>
              <div className="jx-dlCard-actions">
                <button className="jx-dlCard-previewBtn" onClick={() => openCanvas({
                  file_id: art.file_id,
                  name: art.name || '文件',
                  url: art.url,
                  mime_type: art.mime_type,
                  size: art.size,
                })}><EyeOutlined /> 预览</button>
                <a href={fileUrl} download={art.name} className="jx-dlCard-btn">
                  <DownloadOutlined /> 下载
                </a>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  /** Open citation detail */
  const openCitationAction = (citation: CitationItem, toolCalls?: ChatMessage['toolCalls']) => {
    const { toolName, output } = getCitationOutputSlice(citation, toolCalls);

    if (toolName === 'internet_search') {
      const data = (typeof output === 'object' && output !== null ? output : {}) as any;
      const searchResult = data?.result ?? data;
      const results: any[] = Array.isArray(searchResult?.results) ? searchResult.results : [];
      const first = results[0] ?? {};
      const targetUrl = String(first?.url || citation.url || '');
      if (targetUrl) {
        window.open(targetUrl, '_blank', 'noopener,noreferrer');
        return;
      }
      const title = String(first?.title || citation.title || '互联网搜索结果');
      const content = String(first?.content || first?.snippet || citation.snippet || '暂无内容');
      setDetailModal({ title, body: <div className="jx-tr-detailBody">{content}</div> });
      return;
    }

    if (toolName === 'retrieve_dataset_content') {
      const data = (typeof output === 'object' && output !== null ? output : {}) as any;
      const item = Array.isArray(data?.items) ? data.items[0] : undefined;
      const docName = String(item?.['文件名称'] || item?.title || item?.document_name || citation.title || '未知文档');
      const content = String(item?.['文件内容'] || item?.content || citation.snippet || '');
      setDetailModal({ title: docName, body: <div className="jx-tr-detailBody">{content || '暂无内容'}</div> });
      return;
    }

    if (toolName === 'get_industry_news') {
      const data = (typeof output === 'object' && output !== null ? output : {}) as any;
      const item = Array.isArray(data?.items) ? data.items[0] : undefined;
      const title = String(item?.['标题'] || item?.title || citation.title || '资讯详情');
      const summary = String(item?.['摘要'] || item?.summary || citation.snippet || '');
      const tags = [item?.['标签'], item?.['对应产业链'], item?.['地区']].filter(Boolean).map(String);
      setDetailModal({
        title,
        body: (
          <div>
            {tags.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 10 }}>{tags.map((tag, ti) => <span key={ti} className="jx-tr-newsTag">{tag}</span>)}</div>}
            <div className="jx-tr-detailBody">{summary || '暂无摘要'}</div>
          </div>
        ),
      });
      return;
    }

    // fallback
    const title = citation.title || '引用详情';
    const snippet = citation.snippet || '暂无内容';
    setDetailModal({ title, body: <div className="jx-tr-detailBody">{snippet}</div> });
  };

  // Stable callback for citation actions — prevents CitationMarkdownBlock re-renders
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleCitationAction = useCallback(
    (citation: CitationItem) => openCitationAction(citation, m.toolCalls),
    [m.toolCalls],
  );

  /** Copy message text */
  const doCopy = (str: string) => {
    const copyFallback = (s: string) => {
      const ta = document.createElement('textarea');
      ta.value = s; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
      setCopiedMsg(m.ts);
      setTimeout(() => { if (useChatStore.getState().copiedMsg === m.ts) setCopiedMsg(null); }, 2000);
    };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(str).then(() => {
        setCopiedMsg(m.ts);
        setTimeout(() => { if (useChatStore.getState().copiedMsg === m.ts) setCopiedMsg(null); }, 2000);
      }).catch(() => copyFallback(str));
    } else {
      copyFallback(str);
    }
  };

  const doSelectionCopy = (raw: string) => {
    const str = getStoredSelectionText() || raw;
    if (!str) return;
    const markSelectionCopied = () => {
      selectionCopiedTextRef.current = str;
      setSelectionCopied(true);
      if (selectionCopiedTimerRef.current) {
        window.clearTimeout(selectionCopiedTimerRef.current);
      }
      selectionCopiedTimerRef.current = window.setTimeout(() => {
        selectionCopiedTextRef.current = null;
        setSelectionCopied(false);
        selectionCopiedTimerRef.current = null;
      }, 1800);
    };

    const copyFallback = (s: string) => {
      const ta = document.createElement('textarea');
      ta.value = s;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      restoreSelectionRange();
      markSelectionCopied();
    };

    if (navigator.clipboard) {
      navigator.clipboard.writeText(str).then(() => {
        restoreSelectionRange();
        markSelectionCopied();
      }).catch(() => copyFallback(str));
    } else {
      copyFallback(str);
    }
  };

  const sending = useChatStore.getState().sending;
  const renderUserQuote = () => {
    if (m.role !== 'user' || !m.quotedFollowUp?.text) return null;
    return (
      <div className="jx-userQuote" title={m.quotedFollowUp.text}>
        <span className="jx-userQuoteLabel">引用</span>
        <span className="jx-userQuoteText">{m.quotedFollowUp.text}</span>
      </div>
    );
  };

  const renderChipBadges = () => {
    if (m.role !== 'user') return null;
    const hasMention = !!m.mentionName;
    const hasSkill = !!m.skillName;
    if (!hasMention && !hasSkill) return null;
    return (
      <div className="jx-msgChipBadges">
        {hasMention && (
          <span className="jx-msgChip jx-msgChip--mention">
            <span className="jx-msgChip-prefix">@</span>{m.mentionName}
          </span>
        )}
        {hasSkill && (
          <span className="jx-msgChip jx-msgChip--skill">
            <span className="jx-msgChip-prefix">/</span>{m.skillName}
          </span>
        )}
      </div>
    );
  };

  return (
    <div
      className={`jx-msg ${m.role === 'user' ? 'user' : 'assistant'}`}
      data-message-ts={m.ts}
    >
      <div className={`jx-msgInner${m.role === 'user' ? ' user' : ''}${shareSelectionMode && !m.isStreaming ? ' share-selectable' : ''}`}>
        {shareSelectionMode && !m.isStreaming && (
          <label className="jx-shareCheckboxWrap" aria-label="选择这条对话记录">
            <input
              type="checkbox"
              className="jx-shareCheckbox"
              checked={shareSelected}
              onChange={() => toggleShareMessageTs(m.ts)}
            />
          </label>
        )}
      <div
        className="jx-msgContent"
        ref={contentRef}
        onMouseDown={(e) => {
          if (e.button !== 0) return;
          selectionPointerDownRef.current = {
            x: e.clientX,
            y: e.clientY,
            hadSelection: hasSelectionInsideContent(),
          };
        }}
        onMouseUp={(e) => {
          const pointerDown = selectionPointerDownRef.current;
          selectionPointerDownRef.current = null;
          if (pointerDown?.hadSelection) {
            const moved = Math.abs(e.clientX - pointerDown.x) > 3 || Math.abs(e.clientY - pointerDown.y) > 3;
            if (!moved) {
              clearSelectionState();
              return;
            }
          }

          // Save selection range immediately before any async processing,
          // so it can be restored if a React re-render destroys the selection.
          const sel = window.getSelection();
          let capturedRange: Range | null = null;
          if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
            capturedRange = sel.getRangeAt(0).cloneRange();
            selectionRangeRef.current = capturedRange;
            guardSelectionState();
          }
          window.setTimeout(() => checkAndShowToolbar(capturedRange), 0);
        }}
      >
        {/* User attachments */}
        {m.role === 'user' && m.attachments && m.attachments.length > 0 && (
          <div className="jx-userAttachments">
            {m.attachments.map((att, idx) => (
              <FileAttachmentCard key={idx} name={att.name} downloadHref={(att.download_url || att.file_id) ? `${effectiveApiUrl}${att.download_url || `/files/${att.file_id}`}` : undefined} />
            ))}
          </div>
        )}

        {m.segments && m.segments.length > 0 ? (
          /* Segment-based rendering */
          <>
            {m.segments.map((seg, segIdx) => {
              const isLastSeg = segIdx === m.segments!.length - 1;
              const segKey = `${m.ts}-seg-${segIdx}`;

              if (seg.type === 'tool') {
                const tool = m.toolCalls?.[seg.toolIndex!];
                if (!tool) return null;

                if (dispatchProcessVisible) {
                  return <ToolCallRow key={segKey} tool={tool} isStreaming={m.isStreaming} />;
                }
                // Group consecutive tool segments into one summary row
                const prevSeg = segIdx > 0 ? m.segments![segIdx - 1] : null;
                if (prevSeg?.type === 'tool') return null; // already rendered by first in group
                const groupTools: NonNullable<typeof m.toolCalls>[number][] = [];
                for (let si = segIdx; si < m.segments!.length && m.segments![si].type === 'tool'; si++) {
                  const t = m.toolCalls?.[m.segments![si].toolIndex!];
                  if (t) groupTools.push(t);
                }
                if (groupTools.length === 0) return null;
                return <ToolProgressInline key={segKey} message={m} toolCalls={groupTools} />;
              }

              if (seg.type === 'plan' && seg.planData) {
                return (
                  <PlanCard
                    key={segKey}
                    mode={seg.planData.mode}
                    title={seg.planData.title}
                    description={seg.planData.description}
                    steps={seg.planData.steps}
                    completedSteps={seg.planData.completedSteps}
                    totalSteps={seg.planData.totalSteps}
                    resultText={seg.planData.resultText}
                    isStreaming={m.isStreaming}
                    agentNameMap={seg.planData.agentNameMap}
                  />
                );
              }

              if (seg.type === 'thinking') {
                const isActiveThinking = !!(m.isStreaming && !m.segments!.slice(segIdx + 1).some(s => s.type === 'text'));
                return <ThinkingInline key={segKey} content={seg.content || ''} thinkKey={segKey} isActive={isActiveThinking} />;
              }

              if (seg.type === 'text') {
                const textContent = seg.content || '';
                if (!textContent && !m.isStreaming) return null;
                const msgCitations = getContextualCitations(m.citations ?? [], m.segments, m.toolCalls, segIdx);
                const effectiveCitations = resolveConversationCitations(textContent, msgCitations, chatMessages, m.ts);
                return (
                  <React.Fragment key={segKey}>
                    <div className={`jx-bubble ${m.role === 'user' ? 'user' : ''} ${m.isMarkdown ? 'jx-md' : ''} ${m.isStreaming && isLastSeg ? 'streaming' : ''}`}>
                      {segIdx === 0 && renderUserQuote()}
                      {segIdx === 0 && renderChipBadges()}
                      <CitationMarkdownBlock
                        text={textContent}
                        isMarkdown={m.isMarkdown ?? false}
                        citations={effectiveCitations}
                        messageIsStreaming={m.isStreaming}
                        onCitationAction={handleCitationAction}
                      />
                      {m.isStreaming && isLastSeg && !m.toolPending && (
                        <span className="jx-streamingIndicator" aria-hidden="true">
                          <span className="jx-streamingDot" /><span className="jx-streamingDot" /><span className="jx-streamingDot" />
                        </span>
                      )}
                    </div>
                    {m.isStreaming && isLastSeg && m.toolPending && (
                      <div className="jx-inlineSummary" role="status" aria-live="polite" style={{ cursor: 'default' }}>
                        <BrandLoader done={false} label="正在准备调用工具" />
                        <span className="jx-inlineSummaryText">正在准备调用工具…</span>
                      </div>
                    )}
                  </React.Fragment>
                );
              }
              return null;
            })}
            {/* Streaming placeholder — show "正在思考" instead of dots */}
            {m.isStreaming && (() => {
              const segs = m.segments!;
              const hasThinking = segs.some(s => s.type === 'thinking');
              const hasText = segs.some(s => s.type === 'text');
              // If there's already a thinking or text segment rendered, no placeholder needed
              if (hasThinking || hasText) return false;
              const lastSeg = segs[segs.length - 1];
              if (lastSeg.type === 'tool') {
                if (!dispatchProcessVisible) return false;
                const tool = m.toolCalls?.[lastSeg.toolIndex!];
                return !tool || tool.status !== 'running';
              }
              return true;
            })() && (
              <ThinkingInline content="" thinkKey={`${m.ts}-placeholder`} isActive={true} />
            )}
          </>
        ) : (
          /* Legacy rendering path */
          <>
            {m.toolCalls && m.toolCalls.length > 0 && dispatchProcessVisible && (
              <div className="jx-toolCallsList">
                {m.toolCalls.map((tool, idx) => (
                  <ToolCallRow key={`${m.ts}-tool-${idx}`} tool={tool} isStreaming={m.isStreaming} />
                ))}
              </div>
            )}
            {m.thinking && m.thinking.length > 0 && thinkingMode && (
              <div className="jx-thinkingSection">
                <div className="jx-sectionHeader">
                  <BulbOutlined className="jx-sectionIcon" />
                  <span className="jx-sectionTitle">思考过程 ({m.thinking.length})</span>
                </div>
                <div className="jx-thinkingList">
                  {m.thinking.map((think, idx) => renderThinkingBlock(think.content, `${m.ts}-think-${idx}`, false))}
                </div>
              </div>
            )}
            {m.isStreaming && !m.content ? (
              <ThinkingInline content="" thinkKey={`${m.ts}-legacy-placeholder`} isActive={true} />
            ) : (
            <div className={`jx-bubble ${m.role === 'user' ? 'user' : ''} ${m.isMarkdown ? 'jx-md' : ''} ${m.isStreaming ? 'streaming' : ''}`}>
              {renderUserQuote()}
              {renderChipBadges()}
              <CitationMarkdownBlock
                text={m.content}
                isMarkdown={m.isMarkdown ?? false}
                citations={resolveConversationCitations(m.content, m.citations ?? [], chatMessages, m.ts)}
                messageIsStreaming={m.isStreaming}
                onCitationAction={handleCitationAction}
              />
              {m.isStreaming && (
                <span className="jx-streamingIndicator" aria-hidden="true">
                  <span className="jx-streamingDot" /><span className="jx-streamingDot" /><span className="jx-streamingDot" />
                </span>
              )}
            </div>
            )}
          </>
        )}

        {/* Artifact cards */}
        {m.role === 'assistant' && renderArtifactCards()}

        {/* Follow-up questions */}
        {m.role === 'assistant' && m.followUpQuestions && m.followUpQuestions.length > 0 && (
          <motion.div
            className="jx-followUpQuestions"
            initial="hidden"
            animate="visible"
            variants={{ visible: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } } }}
          >
            {m.followUpQuestions.map((q, qi) => (
              <motion.button
                key={qi}
                className="jx-followUpBtn"
                variants={{
                  hidden: { opacity: 0, y: 6 },
                  visible: { opacity: 1, y: 0, transition: { duration: 0.2, ease: 'easeOut' } },
                }}
                onClick={() => send(q)}
                disabled={sending}
              >
                <span className="jx-followUpText">{q}</span>
                <span className="jx-followUpArrow">→</span>
              </motion.button>
            ))}
          </motion.div>
        )}

        {/* Selection quick menu — rendered via portal to document.body so that
            position:fixed is relative to the viewport, not to any transformed
            Framer-Motion ancestor which would otherwise break fixed positioning. */}
        {!m.isStreaming && selectionToolbar && createPortal(
          <div
            className="jx-selectionToolbar"
            style={{ left: selectionToolbar.x, top: selectionToolbar.y }}
          >
            <button
              type="button"
              className={`jx-selectionToolbarBtn${selectionCopied ? ' copied' : ''}`}
              title={selectionCopied ? '已复制' : '复制'}
              onMouseDown={(e) => handleSelectionToolbarMouseDown(e, () => {
                doSelectionCopy(selectionToolbar.text);
              })}
            >
              <span className="jx-selectionToolbarIcon">{selectionCopied ? <CheckOutlined /> : <CopyOutlined />}</span>
              <span className="jx-selectionToolbarLabel">{selectionCopied ? '已复制' : '复制'}</span>
            </button>
            <button
              type="button"
              className="jx-selectionToolbarBtn"
              title="追问"
              onMouseDown={(e) => handleSelectionToolbarMouseDown(e, handleSelectionFollowUpQuote)}
            >
              <span className="jx-selectionToolbarIcon"><RedoOutlined /></span>
              <span className="jx-selectionToolbarLabel">追问</span>
            </button>
          </div>,
          document.body,
        )}

        {/* User message editing */}
        {isEditing && (
          <div className="jx-editMessage">
            <Input.TextArea
              autoFocus
              rows={3}
              value={editText}
              onChange={e => setEditText(e.target.value)}
              className="jx-editMessage-input"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (editText.trim() && editAndResend) {
                    editAndResend(messageIndex, editText.trim());
                  }
                }
              }}
            />
            <div className="jx-editMessage-btns">
              <Button size="small" onClick={() => setEditingMessageTs(null)}>取消</Button>
              <Button size="small" type="primary" disabled={!editText.trim()}
                onClick={() => {
                  if (editAndResend) {
                    editAndResend(messageIndex, editText.trim());
                  }
                }}>发送</Button>
            </div>
          </div>
        )}

        {/* Message action bar */}
        {!m.isStreaming && !isEditing && (
          <div className={`jx-msgActions ${m.role === 'user' ? 'user' : ''}`}>
            <button className={`jx-msgActionBtn${copiedMsg === m.ts ? ' copied' : ''}`}
              title={copiedMsg === m.ts ? '已复制' : '复制内容'}
              onClick={() => doCopy(messagePlainText)}>
              {copiedMsg === m.ts ? <CheckOutlined /> : <CopyOutlined />}
            </button>
            {m.role === 'user' && editAndResend && (
              <button className="jx-msgActionBtn" title="编辑消息"
                onClick={() => {
                  setEditText(m.content);
                  setEditingMessageTs(m.ts);
                }}>
                <EditOutlined />
              </button>
            )}
            {m.role === 'assistant' && (<>
              <button className={`jx-msgActionBtn${feedbackMap[m.ts] === 'like' ? ' active-like' : ''}`} title="有帮助"
                onClick={() => {
                  const next = feedbackMap[m.ts] === 'like' ? undefined : 'like' as const;
                  setFeedbackMap(next ? { ...feedbackMap, [m.ts]: next } : Object.fromEntries(Object.entries(feedbackMap).filter(([k]) => Number(k) !== m.ts)));
                  if (next && m.messageId) {
                    authFetch(`${effectiveApiUrl}/v1/chats/messages/${m.messageId}/feedback`, {
                      method: 'POST', headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ rating: 'like', chat_id: currentChatId }),
                    }).catch(() => {});
                  }
                }}>
                {feedbackMap[m.ts] === 'like' ? <LikeFilled /> : <LikeOutlined />}
              </button>
              <button className={`jx-msgActionBtn${feedbackMap[m.ts] === 'dislike' ? ' active-dislike' : ''}`} title="没有帮助"
                onClick={() => {
                  if (feedbackMap[m.ts] === 'dislike') {
                    setFeedbackMap(Object.fromEntries(Object.entries(feedbackMap).filter(([k]) => Number(k) !== m.ts)));
                    setDislikingTs(null);
                  } else {
                    setFeedbackMap({ ...feedbackMap, [m.ts]: 'dislike' });
                    setDislikingTs(m.ts);
                    setDislikeComment('');
                  }
                }}>
                {feedbackMap[m.ts] === 'dislike' ? <DislikeFilled /> : <DislikeOutlined />}
              </button>
            </>)}
            {m.role === 'assistant' && (
              <button className="jx-msgActionBtn" title="导出为PDF文件" aria-label="导出为PDF文件" onClick={() => { void exportChatRecord(currentChatId); }}>
                <ExportOutlined />
              </button>
            )}
            {m.role === 'assistant' && (
              <button
                className={`jx-msgActionBtn${shareSelectionMode ? ' active-share' : ''}`}
                title="生成分享链接"
                aria-label="生成分享链接"
                onClick={() => {
                  // 默认选中当前对话中所有已完成的消息，点击分享后即可直接生成链接
                  startShareSelectionWithAll(
                    chatMessages.filter((msg) => !msg.isStreaming).map((msg) => msg.ts),
                  );
                }}
              >
                <ShareAltOutlined />
              </button>
            )}
            {m.role === 'assistant' && regenerate && (
              <button className="jx-msgActionBtn" title="重新生成"
                onClick={() => regenerate(messageIndex)}>
                <SyncOutlined />
              </button>
            )}
          </div>
        )}

        {/* Dislike feedback form */}
        {dislikingTs === m.ts && (
          <div className="jx-dislikeFeedback">
            <p className="jx-dislikeFeedback-title">请告诉我们哪里不好（可选）</p>
            <Input.TextArea autoFocus rows={3} placeholder="内容不准确 / 答非所问 / 其他..."
              value={dislikeComment} onChange={e => setDislikeComment(e.target.value)} className="jx-dislikeFeedback-input" />
            <div className="jx-dislikeFeedback-btns">
              <Button size="small" onClick={() => setDislikingTs(null)}>跳过</Button>
              <Button size="small" type="primary" onClick={() => {
                if (m.messageId) {
                  authFetch(`${effectiveApiUrl}/v1/chats/messages/${m.messageId}/feedback`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rating: 'dislike', comment: dislikeComment || undefined, chat_id: currentChatId }),
                  }).catch(() => {});
                }
                setDislikingTs(null);
              }}>提交</Button>
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
