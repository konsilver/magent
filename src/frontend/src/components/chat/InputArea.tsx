import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Dropdown } from 'antd';
import { FileImageOutlined, FileTextOutlined, CloudDownloadOutlined } from '@ant-design/icons';
import { useChatStore, useFileStore, useUIStore } from '../../stores';
import { FileAttachmentCard, MySpaceImportModal } from '../file';
import { getApiUrl } from '../../api';
import { AgentMentionPopup, useAgentMention } from '../agent';
import { SkillSlashPopup, useSkillSlash } from './SkillSlashPopup';

interface InputAreaProps {
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  send: () => void;
  abort?: () => void;
  handleFileSelect: (e: React.ChangeEvent<HTMLInputElement>, ref: React.RefObject<HTMLInputElement | null>) => void;
  removeFile: (index: number) => void;
  placeholder?: string;
  rows?: number;
  disableMention?: boolean;
}

// ── ContentEditable helpers ─────────────────────────────────────────────

/** Extract plain text from editor, skipping chip spans. */
function getEditorText(el: HTMLElement): string {
  let t = '';
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) {
      // Convert non-breaking spaces back to regular
      t += (n.textContent || '').replace(/\u00A0/g, ' ');
    } else if (n instanceof HTMLBRElement) {
      t += '\n';
    } else if (n instanceof HTMLElement) {
      if (n.dataset.chip) return; // skip chips
      const isBlock = n.tagName === 'DIV' || n.tagName === 'P';
      if (isBlock && t && !t.endsWith('\n')) t += '\n';
      for (const c of n.childNodes) walk(c);
    }
  };
  for (const c of el.childNodes) walk(c);
  return t;
}

/** Remove text backwards from cursor to the trigger char (@ or /). */
function removeQueryAtCursor(_editor: HTMLElement, trigger: string) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return;
  const range = sel.getRangeAt(0);
  const node = range.startContainer;
  if (node.nodeType !== Node.TEXT_NODE) return;
  const text = node.textContent || '';
  const cursor = range.startOffset;
  const idx = text.lastIndexOf(trigger, cursor - 1);
  if (idx === -1) return;
  node.textContent = text.slice(0, idx) + text.slice(cursor);
  try {
    range.setStart(node, idx);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
  } catch { /* empty text node edge case */ }
}

/** Insert an inline chip span at the current cursor, followed by a space. */
function insertChipAtCursor(editor: HTMLElement, prefix: string, name: string, cls: string) {
  const chip = document.createElement('span');
  chip.contentEditable = 'false';
  chip.className = `jx-editorChip ${cls}`;
  chip.dataset.chip = prefix === '@' ? 'mention' : 'skill';
  chip.dataset.chipName = name;
  chip.innerHTML =
    `<span class="jx-editorChip-prefix">${prefix}</span>` +
    `<span class="jx-editorChip-name">${name}</span>`;

  const space = document.createTextNode('\u00A0');
  const sel = window.getSelection();
  if (sel && sel.rangeCount > 0) {
    const range = sel.getRangeAt(0);
    range.collapse(true);
    range.insertNode(space);
    range.insertNode(chip);
    const r2 = document.createRange();
    r2.setStartAfter(space);
    r2.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r2);
  } else {
    editor.appendChild(chip);
    editor.appendChild(space);
  }
}

function setEditorPlainText(editor: HTMLElement, text: string) {
  editor.innerHTML = '';
  if (text) {
    editor.textContent = text;
  }
}

function moveCaretToEnd(editor: HTMLElement) {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

// ── Component ───────────────────────────────────────────────────────────

export function InputArea({
  inputRef, fileInputRef, send, abort, handleFileSelect, removeFile,
  placeholder = '请输入你的问题，按Enter发送，Shift+Enter换行',
  rows: _rows = 3,
  disableMention = false,
}: InputAreaProps) {
  const {
    input, setInput, sending, thinkingMode, setThinkingMode,
    quotedFollowUp, setQuotedFollowUp,
    activeSkill, setActiveSkill, activeMention, setActiveMention,
    planMode, setPlanMode,
  } = useChatStore();
  const { uploadedFiles, uploadingFiles, importedSpaceFiles, removeImportedSpaceFile } = useFileStore();
  const { promptHubOpen, setPromptHubOpen } = useUIStore();
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const [mySpaceImportOpen, setMySpaceImportOpen] = useState(false);

  // Object URLs for uploaded image files — revoked when files change
  const uploadedImageUrls = useMemo(() => {
    return uploadedFiles.map((f) => (f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined));
  }, [uploadedFiles]);
  useEffect(() => {
    return () => { uploadedImageUrls.forEach((u) => u && URL.revokeObjectURL(u)); };
  }, [uploadedImageUrls]);

  const editorRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const [isComposing, setIsComposing] = useState(false);
  const prevTextRef = useRef('');

  const {
    mentionVisible, setMentionVisible,
    selectedIndex: mIdx, setSelectedIndex: setMIdx,
    handleInputChange: mentionInputChange, handleKeyDown: mentionKeyDown,
    getFiltered: getMentionFiltered,
  } = useAgentMention();
  const {
    slashVisible, setSlashVisible,
    selectedIndex: sIdx, setSelectedIndex: setSIdx,
    handleSlashInputChange: slashInputChange, handleSlashKeyDown: slashKeyDown,
    getFiltered: getSlashFiltered,
  } = useSkillSlash();

  // ── Sync editor text → store ──
  const syncTextRef = useRef<() => void>(() => {});
  syncTextRef.current = () => {
    if (!editorRef.current) return;
    const text = getEditorText(editorRef.current);
    const prev = prevTextRef.current;
    if (text === prev) return; // no change
    prevTextRef.current = text;
    setInput(text);
    if (!disableMention) mentionInputChange(text, prev);
    slashInputChange(text, prev);
  };
  function syncText() { syncTextRef.current(); }

  // ── Native input event listener (more reliable than React onInput for contentEditable) ──
  useEffect(() => {
    const el = editorRef.current;
    if (!el) return;
    const handler = () => { if (!composingRef.current) syncTextRef.current(); };
    el.addEventListener('input', handler);
    return () => el.removeEventListener('input', handler);
  }, []);

  // ── Sync external store updates back into the contentEditable editor ──
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || composingRef.current || input === prevTextRef.current) return;

    const hadMentionChip = !!editor.querySelector('[data-chip="mention"]');
    const hadSkillChip = !!editor.querySelector('[data-chip="skill"]');

    setEditorPlainText(editor, input);
    prevTextRef.current = input;

    if (hadMentionChip && activeMention) setActiveMention(null);
    if (hadSkillChip && activeSkill) setActiveSkill(null);

    if (document.activeElement === editor) {
      moveCaretToEnd(editor);
    }
  }, [activeMention, activeSkill, input, setActiveMention, setActiveSkill]);

  // ── Expose the editor as inputRef for external .focus() calls ──
  useEffect(() => {
    if (editorRef.current) {
      (inputRef as React.MutableRefObject<any>).current = editorRef.current;
    }
  }, []);

  // ── Chip insertion handlers ──
  function onMentionSelect(agentName: string) {
    const ed = editorRef.current;
    if (!ed) return;
    removeQueryAtCursor(ed, '@');
    insertChipAtCursor(ed, '@', agentName, 'jx-editorChip--mention');
    setActiveMention({ name: agentName });
    setMentionVisible(false);
    syncText();
    ed.focus();
  }

  function onSlashSelect(skillId: string, skillName: string) {
    const ed = editorRef.current;
    if (!ed) return;
    removeQueryAtCursor(ed, '/');
    insertChipAtCursor(ed, '/', skillName, 'jx-editorChip--skill');
    setActiveSkill({ id: skillId, name: skillName });
    setSlashVisible(false);
    syncText();
    ed.focus();
  }

  // ── Keyboard ──
  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    // Slash popup: Enter/Tab → select skill
    if (slashVisible && (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey))) {
      e.preventDefault();
      const list = getSlashFiltered(input);
      const sel = list[sIdx] || list[0];
      if (sel) onSlashSelect(sel.id, sel.name);
      return;
    }
    // Slash popup: ArrowUp/Down/Escape
    if (slashVisible && slashKeyDown(e)) return;

    // Mention popup: Enter/Tab → select mention
    if (mentionVisible && (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey))) {
      e.preventDefault();
      const list = getMentionFiltered(input);
      const sel = list[mIdx] || list[0];
      if (sel) onMentionSelect(sel.name);
      return;
    }
    // Mention popup: Escape
    if (mentionVisible && e.key === 'Escape') {
      e.preventDefault();
      setMentionVisible(false);
      return;
    }
    // Mention popup: ArrowUp/Down
    if (!disableMention && mentionVisible) {
      mentionKeyDown(e, input);
      if (e.defaultPrevented) return;
    }

    // Backspace: if editor only has chip(s) and maybe whitespace, remove last chip
    if (e.key === 'Backspace') {
      const ed = editorRef.current;
      if (ed) {
        const text = getEditorText(ed).trim();
        if (!text) {
          // No real text — check if a chip exists to remove
          const chips = ed.querySelectorAll('[data-chip]');
          if (chips.length > 0) {
            const last = chips[chips.length - 1] as HTMLElement;
            const type = last.dataset.chip;
            // Remove the chip and the space after it
            if (last.nextSibling?.nodeType === Node.TEXT_NODE) last.nextSibling.remove();
            last.remove();
            if (type === 'mention') setActiveMention(null);
            if (type === 'skill') setActiveSkill(null);
            e.preventDefault();
            syncText();
            return;
          }
        }
      }
    }

    // Enter → send, Shift+Enter → newline
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
      return;
    }
  }

  const isEmpty = !input.trim() && !activeMention && !activeSkill && !isComposing;

  const hasAttachments = uploadedFiles.length > 0 || importedSpaceFiles.length > 0;

  return (
    <div className="jx-inputArea">
      {hasAttachments && (
        <div className="jx-inputAttachments">
          {uploadedFiles.map((file, idx) => (
            <FileAttachmentCard
              key={`upload-${file.name}-${idx}`}
              name={file.name}
              loading={uploadingFiles.has(file)}
              onClose={() => removeFile(idx)}
              previewUrl={uploadedImageUrls[idx]}
            />
          ))}
          {importedSpaceFiles.map((file, idx) => {
            const previewUrl = file.type === 'image'
              ? `${getApiUrl()}${file.download_url || `/files/${file.file_id}`}`
              : undefined;
            return (
              <FileAttachmentCard
                key={`space-${file.file_id}-${idx}`}
                name={file.name}
                onClose={() => removeImportedSpaceFile(idx)}
                previewUrl={previewUrl}
              />
            );
          })}
        </div>
      )}
      {quotedFollowUp && (
        <div className="jx-inputQuote">
          <div className="jx-inputQuoteBadge">追问引用</div>
          <div className="jx-inputQuoteText" title={quotedFollowUp.text}>{quotedFollowUp.text}</div>
          <button type="button" className="jx-inputQuoteRemove" onClick={() => setQuotedFollowUp(null)} aria-label="移除引用">×</button>
        </div>
      )}
      <div className="jx-composerWrap">
        {!disableMention && (
          <AgentMentionPopup input={input} visible={mentionVisible} selectedIndex={mIdx} onSelect={onMentionSelect} onHover={setMIdx} />
        )}
        <SkillSlashPopup input={input} visible={slashVisible} selectedIndex={sIdx} onSelect={onSlashSelect} onHover={setSIdx} />

        <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
          accept=".pdf,.docx,.doc,.wps,.txt,.xlsx,.xls,.csv"
          onChange={(e) => handleFileSelect(e, fileInputRef)} />
        <input ref={imageInputRef} type="file" multiple style={{ display: 'none' }}
          accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml"
          onChange={(e) => handleFileSelect(e, imageInputRef)} />

        {/* ContentEditable editor — chips and text live on the same layer */}
        <div
          ref={editorRef}
          contentEditable
          suppressContentEditableWarning
          className={`jx-composer jx-composerEditor${isEmpty ? ' jx-composerEditor--empty' : ''}`}
          data-placeholder={placeholder}
          onInput={() => { if (!composingRef.current) syncText(); }}
          onCompositionStart={() => { composingRef.current = true; setIsComposing(true); }}
          onCompositionEnd={() => { composingRef.current = false; setIsComposing(false); syncText(); }}
          onKeyDown={onKeyDown}
          onPaste={(e) => {
            e.preventDefault();
            const text = e.clipboardData.getData('text/plain');
            document.execCommand('insertText', false, text);
          }}
          onBlur={() => { setTimeout(() => { setMentionVisible(false); setSlashVisible(false); }, 200); }}
        />

        <div className="jx-composerBar">
          <Dropdown
            menu={{
              items: [
                {
                  key: 'fast',
                  label: (
                    <div className="jx-modeOption">
                      <div className="jx-modeOptionHead">
                        <span className="jx-modeOptionTitle">快速模式</span>
                        {!thinkingMode && <img src="/home/选中.svg" alt="" className="jx-modeCheckIcon" />}
                      </div>
                      <div className="jx-modeOptionDesc">适用于大部分情况</div>
                    </div>
                  ),
                  onClick: () => setThinkingMode(false),
                },
                {
                  key: 'think',
                  label: (
                    <div className="jx-modeOption">
                      <div className="jx-modeOptionHead">
                        <span className="jx-modeOptionTitle">思考模式</span>
                        {thinkingMode && <img src="/home/选中.svg" alt="" className="jx-modeCheckIcon" />}
                      </div>
                      <div className="jx-modeOptionDesc">研究级别的专家智能体</div>
                    </div>
                  ),
                  onClick: () => setThinkingMode(true),
                },
              ],
              selectedKeys: [thinkingMode ? 'think' : 'fast'],
            }}
            trigger={['click']}
            placement="topLeft"
            overlayClassName="jx-modeMenu"
          >
            <button className={`jx-modeDropBtn${thinkingMode ? ' thinking' : ''}`}
              aria-label={`当前为${thinkingMode ? '思考模式' : '快速模式'}，点击切换`}>
              <img src={thinkingMode ? '/home/思考.svg' : '/home/快速.svg'} alt="" className="jx-modeIcon" />
              <span>{thinkingMode ? '思考模式' : '快速模式'}</span>
              <img src="/home/箭头-下.svg" alt="" className="jx-modeArrow" />
            </button>
          </Dropdown>

          <button className="jx-promptHubBtn" onClick={() => setPromptHubOpen(!promptHubOpen)} aria-label="提示词中心">
            <img src="/home/提示词.svg" alt="" className="jx-promptHubIcon" />
            <span>提示词中心</span>
          </button>

          <button
            className={`jx-planModeToggleBtn${planMode ? ' active' : ''}`}
            onClick={() => setPlanMode(!planMode)}
            aria-label={planMode ? '关闭计划模式' : '开启计划模式'}
            title={planMode ? '关闭计划模式' : '开启计划模式'}
          >
            <span className="jx-planModeToggleIcon">⚡</span>
            <span>计划模式</span>
          </button>

          <div style={{ flex: 1 }} />

          <Dropdown
            trigger={['click']}
            placement="topRight"
            overlayClassName="jx-attachMenu"
            menu={{
              items: [
                {
                  key: 'image',
                  icon: <FileImageOutlined />,
                  label: '上传图片',
                  onClick: () => imageInputRef.current?.click(),
                },
                {
                  key: 'file',
                  icon: <FileTextOutlined />,
                  label: '上传文件',
                  onClick: () => fileInputRef.current?.click(),
                },
                {
                  type: 'divider',
                },
                {
                  key: 'myspace',
                  icon: <CloudDownloadOutlined />,
                  label: '从我的空间导入',
                  onClick: () => setMySpaceImportOpen(true),
                },
              ],
            }}
          >
            <button className="jx-attachBtn" title="添加文件" aria-label="添加文件">
              <img src="/home/附件.svg" alt="" className="jx-attachIcon" />
            </button>
          </Dropdown>
          <MySpaceImportModal open={mySpaceImportOpen} onClose={() => setMySpaceImportOpen(false)} />
          {sending ? (
            <button className="jx-sendBtn" onClick={() => abort?.()} aria-label="中止">
              <img src="/home/终止.svg" alt="" className="jx-sendIcon" />
            </button>
          ) : (
            <button className="jx-sendBtn" onClick={() => send()} disabled={uploadingFiles.size > 0} aria-label="发送">
              <img src="/home/发送.svg" alt="" className="jx-sendIcon" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
