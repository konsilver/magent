# 隐藏工具调用 & 思考过程的内联摘要优化方案 v3

> 调研日期：2026-03-30
> 对标参考：Claude Coworker 内联摘要 + 右侧 Canvas 详情面板
> 设计原则：**极简灰调、一行摘要、点击展开 Canvas**

---

## 一、当前问题

### 1.1 工具调用隐藏后无进度

`MessageBubble.tsx:497` 中 `dispatchProcessVisible=false` 时 tool segment 返回 `null`，streaming placeholder（533-546 行）检测到末尾有 running tool 仍渲染三个跳动圆点。用户只看到无限转圈，无任何信息。

### 1.2 思考过程占据过多空间

当前思考块（`renderThinkingBlock`）展开后占据 max-height 280px 的内容区域，内联在对话流中打断阅读节奏。思考和工具调用应统一为同一种极简交互模式：**一行摘要 + Canvas 详情**。

---

## 二、目标效果（对标 Claude Coworker）

### 2.1 对话列表中的内联摘要

Claude Coworker 在隐藏工具详情时，**不是完全隐藏**，而是在对话流中用一行极淡的灰色文字标注当前动作，视觉权重极低，类似"思考过程"的呈现方式。

**我们的目标效果**（ASCII 线框）：

**工具调用摘要**：

```
┌─ 对话气泡 ─────────────────────────────────────────┐
│                                                      │
│  (●) 联网搜索...                             >       │  ← running 脉冲圆点
│                                                      │
│  [后续文本继续渲染...]                                │
│                                                      │
└──────────────────────────────────────────────────────┘

┌─ 完成后 ─────────────────────────────────────────────┐
│                                                       │
│   ·  联网搜索 · 知识库检索 · 数据分析           >     │  ← 静态圆点
│                                                       │
│  [后续文本继续渲染...]                                 │
│                                                       │
└───────────────────────────────────────────────────────┘
```

**思考过程摘要**（同样一行，同样交互）：

```
┌─ 对话气泡 ─────────────────────────────────────────┐
│                                                      │
│  (●) 正在思考...                                >    │  ← running 脉冲圆点
│                                                      │
│  [后续文本继续渲染...]                                │
│                                                      │
└──────────────────────────────────────────────────────┘

┌─ 思考完成后 ─────────────────────────────────────────┐
│                                                       │
│   ·  思考过程                                   >     │  ← 静态圆点
│                                                       │
│  [后续文本继续渲染...]                                 │
│                                                       │
└───────────────────────────────────────────────────────┘
```

**核心设计要点**：

1. **只占一行** — 工具调用和思考过程在对话流中各只占一行
2. **无左侧竖线** — 不使用 border-left，保持极简纯净
3. **极低视觉权重** — 灰色文字 `rgba(100,116,139, .55)` 不使用彩色
4. **统一交互** — 工具调用和思考过程都是：一行摘要 + 点击 `>` 打开右侧 Canvas 详情
5. **脉冲信号（Pulse Dot）** — 对标 Claude Coworker：
   - **running**：6px 实心灰圆 + 向外扩散脉冲光环（sonar），持续循环
   - **完成**：脉冲停止，静态圆点
   - 光环 `scale(1) → scale(2.8)`，`opacity: 0.6 → 0`
6. **思考 Canvas** — 点击思考摘要后右侧 Canvas 显示完整思考内容（纯文本，可滚动）

### 2.2 右侧 Canvas 详情面板

点击摘要行后，右侧打开详情面板（复用现有 `ToolResultPanel` 的位置和框架）。**工具调用**和**思考过程**各有对应的 Canvas 视图：

**工具调用 Canvas — 时间线 + 可展开输出**：

```
┌─ Canvas 面板 ──────────────────────────────────┐
│                                                  │
│  工具调用详情                            [✕]     │
│  ─────────────────────────────────────────────   │
│                                                  │
│   ·  联网搜索                                    │
│      "产业政策" · 8 条结果                        │
│      12:03:42                                    │
│   │                                              │
│   ·  知识库检索                                   │
│      找到 5 条文档                                │
│      12:03:44                                    │
│   │                                              │
│  (●) 数据分析                                    │
│      执行中...                                   │
│                                                  │
│  ─────────────────────────────────────────────   │
│                                                  │
│  点击已完成的步骤查看输出详情                      │
│                                                  │
│  ═══════════════════════════════════════════════  │
│                                                  │
│  [展开的工具输出 — 复用 renderToolOutputBody]      │
│                                                  │
└──────────────────────────────────────────────────┘
```

**思考过程 Canvas — 纯文本详情**：

```
┌─ Canvas 面板 ──────────────────────────────────┐
│                                                  │
│  思考过程                                [✕]     │
│  ─────────────────────────────────────────────   │
│                                                  │
│  用户询问了关于产业政策的问题。我需要               │
│  先搜索最新的政策文件，然后结合知识库               │
│  中的历史数据进行综合分析...                       │
│                                                  │
│  关于这个问题，我认为需要从以下几个                  │
│  角度来回答：                                     │
│  1. 政策背景                                     │
│  2. 具体措施                                     │
│  3. 影响分析                                     │
│  ...                                             │
│                                                  │
│  (全文可滚动查看)                                 │
│                                                  │
└──────────────────────────────────────────────────┘
```

---

## 三、视觉设计规范

### 3.1 色板（灰调为主，对标 Claude Coworker）

```
主文字色:     rgba(100, 116, 139, .72)    /* slate-400 偏暗 */
次文字色:     rgba(100, 116, 139, .50)    /* 摘要、时间戳 */
脉冲圆点色: rgba(100, 116, 139, .65)    /* 实心圆点 */
脉冲光环色: rgba(100, 116, 139, .40)    /* 扩散光环，起始 opacity */
箭头色:       rgba(100, 116, 139, .36)    /* 极淡 > 箭头 */
hover 背景:  rgba(100, 116, 139, .04)    /* 鼠标悬浮微微变灰 */
active 背景: rgba(100, 116, 139, .07)    /* Canvas 打开时 */
分隔符 ·:    rgba(148, 163, 184, .50)    /* 工具名之间的点 */
静态圆点:    rgba(100, 116, 139, .45)    /* 完成后静态圆点，比 running 稍淡 */
error:       rgba(239, 68, 68, .60)      /* 仅 error 用红色，但降低饱和 */
```

### 3.2 字体

```
工具名:    13px, font-weight: 500, 与思考块 label 一致
摘要文字:  12px, font-weight: 400
时间戳:    11px, font-weight: 400, 次文字色
箭头 >:   12px, font-weight: 400
```

### 3.3 间距

```
内联摘要行：
  padding: 3px 8px（无左侧竖线，整行可点击区域）
  行高: 28px（确保单行，不换行）
  margin: 4px 0 6px
  border-radius: 6px（hover 时有微弱背景色）

Canvas 时间线：
  每个步骤之间: 16px gap
  步骤内行间距: 4px
  工具输出区域 padding: 14px 16px（复用现有 .jx-trp-body）
```

---

## 四、组件实现细节

### 4.1 新组件：`ToolProgressInline`

**文件**：`src/frontend/src/components/tool/ToolProgressInline.tsx`

命名为 "Inline" 而非 "Bar"，强调它是内联在对话流中的一行文字。

```tsx
// ── ToolProgressInline.tsx ──────────────────────────────────
import { RightOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { TOOL_NAME_OVERRIDES } from '../../utils/constants';
import { useChatStore } from '../../stores';
import type { ToolCall } from '../../types';

interface ToolProgressInlineProps {
  toolCalls: ToolCall[];
  isStreaming?: boolean;
  messageTs: number;
}

export function ToolProgressInline({ toolCalls, isStreaming, messageTs }: ToolProgressInlineProps) {
  const { toolResultPanel, setToolResultPanel } = useChatStore();
  const { toolDisplayNames } = useChatStore();

  const resolveStatus = (tool: ToolCall): 'running' | 'success' | 'error' => {
    const raw = tool.status ?? 'success';
    if (raw === 'error') return 'error';
    if (raw === 'running' && !isStreaming) return 'success';
    if (raw === 'running') return 'running';
    return 'success';
  };

  const hasRunning = toolCalls.some(t => resolveStatus(t) === 'running');
  const hasError = toolCalls.some(t => resolveStatus(t) === 'error');

  const displayNames = toolCalls.map(t =>
    t.displayName || TOOL_NAME_OVERRIDES[t.name] || toolDisplayNames[t.name] || t.name
  );

  const runningTool = toolCalls.find(t => resolveStatus(t) === 'running');
  const runningName = runningTool
    ? (runningTool.displayName || TOOL_NAME_OVERRIDES[runningTool.name] || toolDisplayNames[runningTool.name] || runningTool.name)
    : null;

  const panelKey = `progress-${messageTs}`;
  const isActive = toolResultPanel?.key === panelKey;

  const openCanvas = () => {
    if (isActive) {
      setToolResultPanel(null);
    } else {
      setToolResultPanel({
        key: panelKey,
        toolName: '__progress_timeline__',
        displayName: '工具调用详情',
        output: { toolCalls, isStreaming, messageTs },
        summary: '',
      });
    }
  };

  return (
    <div
      className={`jx-inlineSummary${isActive ? ' active' : ''}`}
      role="button"
      tabIndex={0}
      onClick={openCanvas}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCanvas(); } }}
    >
      <div className="jx-inlineSummaryLeft">
        {/* 脉冲信号圆点 — 对标 Claude Coworker */}
        {hasError ? (
          <CloseCircleOutlined className="jx-inlineSummaryIcon error" />
        ) : (
          <span className={`jx-pulseDot${hasRunning ? ' running' : ''}`}>
            <span className="jx-pulseDotCore" />
            {hasRunning && <span className="jx-pulseDotRing" />}
          </span>
        )}

        {/* 文字内容 */}
        <span className="jx-inlineSummaryText">
          {hasRunning ? (
            <>{runningName}<span className="jx-inlineSummaryDots" /></>
          ) : (
            displayNames.map((name, i) => (
              <span key={i}>
                {i > 0 && <span className="jx-inlineSummarySep"> · </span>}
                {name}
              </span>
            ))
          )}
        </span>
      </div>

      <RightOutlined className="jx-inlineSummaryArrow" />
    </div>
  );
}
```

### 4.2 新组件：`ThinkingInline`

**文件**：`src/frontend/src/components/chat/ThinkingInline.tsx`

思考过程的内联摘要，与 ToolProgressInline 使用完全相同的视觉语言。

```tsx
// ── ThinkingInline.tsx ──────────────────────────────────
import { RightOutlined } from '@ant-design/icons';
import { useChatStore } from '../../stores';

interface ThinkingInlineProps {
  content: string;
  isActiveThinking: boolean;
  messageTs: number;
  thinkingIndex: number;  // 同一消息中第几个思考块
}

export function ThinkingInline({ content, isActiveThinking, messageTs, thinkingIndex }: ThinkingInlineProps) {
  const { toolResultPanel, setToolResultPanel } = useChatStore();

  const panelKey = `thinking-${messageTs}-${thinkingIndex}`;
  const isActive = toolResultPanel?.key === panelKey;

  const openCanvas = () => {
    if (isActiveThinking && !content) return;  // 刚开始思考还没有内容时不打开
    if (isActive) {
      setToolResultPanel(null);
    } else {
      setToolResultPanel({
        key: panelKey,
        toolName: '__thinking_detail__',  // 特殊标记
        displayName: '思考过程',
        output: { content, isActiveThinking },
        summary: '',
      });
    }
  };

  return (
    <div
      className={`jx-inlineSummary${isActive ? ' active' : ''}`}
      role="button"
      tabIndex={0}
      onClick={openCanvas}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCanvas(); } }}
    >
      <div className="jx-inlineSummaryLeft">
        {/* 脉冲圆点 — 思考中有脉冲，思考完成静态 */}
        <span className={`jx-pulseDot${isActiveThinking ? ' running' : ''}`}>
          <span className="jx-pulseDotCore" />
          {isActiveThinking && <span className="jx-pulseDotRing" />}
        </span>

        <span className="jx-inlineSummaryText">
          {isActiveThinking ? (
            <>正在思考<span className="jx-inlineSummaryDots" /></>
          ) : (
            '思考过程'
          )}
        </span>
      </div>

      <RightOutlined className="jx-inlineSummaryArrow" />
    </div>
  );
}
```

### 4.3 思考详情 Canvas：`ThinkingDetailPanel`

**文件**：`src/frontend/src/components/chat/ThinkingDetailPanel.tsx`

在右侧 Canvas 中展示完整思考内容。

```tsx
// ── ThinkingDetailPanel.tsx ──────────────────────────────
import { useEffect, useRef } from 'react';

interface ThinkingDetailPanelProps {
  content: string;
  isActiveThinking?: boolean;
}

export function ThinkingDetailPanel({ content, isActiveThinking }: ThinkingDetailPanelProps) {
  const contentRef = useRef<HTMLDivElement | null>(null);

  // 思考进行中时自动滚到底部
  useEffect(() => {
    if (isActiveThinking && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, isActiveThinking]);

  return (
    <div className="jx-thinkingDetailPanel">
      <div className="jx-thinkingDetailContent" ref={contentRef}>
        {content || (isActiveThinking ? '思考中...' : '暂无内容')}
      </div>
    </div>
  );
}
```

### 4.4 完整 CSS 样式

**追加到**：`src/frontend/src/styles/tool.css`

```css
/* ══════════════════════════════════════════════════════════════
   内联摘要行 — 工具调用 & 思考过程共用
   对标 Claude Coworker 极简灰调风格，无左侧竖线
   ══════════════════════════════════════════════════════════════ */

/* ── 通用内联摘要行（工具调用 & 思考过程共用） ── */
.jx-inlineSummary {
  background: transparent;
  margin: 4px 0 6px;
  padding: 3px 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 28px;
  cursor: pointer;
  user-select: none;
  transition: background .15s ease;
  border-radius: 6px;
}

.jx-inlineSummary:hover {
  background: rgba(100, 116, 139, .04);
}

.jx-inlineSummary:focus-visible {
  outline: 2px solid rgba(100, 116, 139, .20);
  outline-offset: 2px;
}

.jx-inlineSummary.active {
  background: rgba(100, 116, 139, .06);
}

.jx-inlineSummaryLeft {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

/* ── 脉冲信号圆点（Pulse Dot）── 对标 Claude Coworker ── */
.jx-pulseDot {
  position: relative;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* 实心圆点核心 */
.jx-pulseDotCore {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(100, 116, 139, .45);
  position: relative;
  z-index: 1;
  transition: background .3s ease;
}

/* running 时圆点颜色更实 */
.jx-pulseDot.running .jx-pulseDotCore {
  background: rgba(100, 116, 139, .65);
}

/* 向外扩散的脉冲光环 */
.jx-pulseDotRing {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 6px;
  height: 6px;
  margin-top: -3px;
  margin-left: -3px;
  border-radius: 50%;
  border: 1.5px solid rgba(100, 116, 139, .40);
  animation: jxPulseRing 1.8s ease-out infinite;
}

@keyframes jxPulseRing {
  0% {
    transform: scale(1);
    opacity: 0.6;
  }
  100% {
    transform: scale(2.8);
    opacity: 0;
  }
}

/* Icon — 仅 error 使用（红色圆） */
.jx-inlineSummaryIcon {
  font-size: 12px;
  flex-shrink: 0;
}

.jx-inlineSummaryIcon.error {
  color: rgba(239, 68, 68, .60);
}

/* 文字 */
.jx-inlineSummaryText {
  font-size: 13px;
  font-weight: 500;
  color: rgba(100, 116, 139, .72);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

/* 工具名之间的分隔符 · */
.jx-inlineSummarySep {
  color: rgba(148, 163, 184, .50);
  margin: 0 1px;
}

/* running 时的省略号动画 */
.jx-inlineSummaryDots::after {
  content: '';
  animation: jxToolInlineDots 1.4s steps(4, end) infinite;
}

@keyframes jxToolInlineDots {
  0%   { content: ''; }
  25%  { content: '.'; }
  50%  { content: '..'; }
  75%  { content: '...'; }
  100% { content: ''; }
}

/* 右侧箭头 > */
.jx-inlineSummaryArrow {
  font-size: 10px;
  color: rgba(100, 116, 139, .30);
  flex-shrink: 0;
  margin-left: 8px;
  transition: color .15s, transform .15s;
}

.jx-inlineSummary:hover .jx-inlineSummaryArrow {
  color: rgba(100, 116, 139, .55);
}

.jx-inlineSummary.active .jx-inlineSummaryArrow {
  color: rgba(100, 116, 139, .55);
  transform: rotate(0deg);
}

/* ══════════════════════════════════════════════════════════════
   Canvas 面板中的工具执行时间线
   ══════════════════════════════════════════════════════════════ */

.jx-toolTimeline {
  padding: 0;
}

.jx-toolTimelineStep {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(15, 23, 42, .05);
  cursor: pointer;
  transition: background .12s ease;
  border-radius: 6px;
  margin: 0 -8px;
  padding-left: 8px;
  padding-right: 8px;
}

.jx-toolTimelineStep:last-child {
  border-bottom: none;
}

.jx-toolTimelineStep:hover {
  background: rgba(100, 116, 139, .04);
}

.jx-toolTimelineStep.selected {
  background: rgba(100, 116, 139, .06);
}

/* 时间线左侧轨道 */
.jx-toolTimelineTrack {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 20px;
  flex-shrink: 0;
  padding-top: 2px;
}

/* 时间线中的脉冲圆点（与内联摘要复用同一套 pulse 动画） */
.jx-toolTimelineTrackDot {
  position: relative;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.jx-toolTimelineTrackDot .jx-pulseDotCore {
  width: 7px;
  height: 7px;
}

.jx-toolTimelineTrackDot .jx-pulseDotRing {
  width: 7px;
  height: 7px;
  margin-top: -3.5px;
  margin-left: -3.5px;
}

.jx-toolTimelineTrackIcon.error {
  font-size: 13px;
  color: rgba(239, 68, 68, .55);
}

.jx-toolTimelineLine {
  width: 1px;
  flex: 1;
  background: rgba(148, 163, 184, .20);
  margin-top: 4px;
  min-height: 8px;
}

/* 时间线右侧内容 */
.jx-toolTimelineContent {
  min-width: 0;
  flex: 1;
}

.jx-toolTimelineName {
  font-size: 13px;
  font-weight: 600;
  color: rgba(15, 23, 42, .78);
  line-height: 1.4;
}

.jx-toolTimelineSummary {
  font-size: 12px;
  color: rgba(100, 116, 139, .62);
  margin-top: 2px;
  line-height: 1.4;
}

.jx-toolTimelineTime {
  font-size: 11px;
  color: rgba(100, 116, 139, .40);
  margin-top: 2px;
}

.jx-toolTimelineStatus {
  font-size: 12px;
  color: rgba(100, 116, 139, .50);
  margin-top: 2px;
}

.jx-toolTimelineStatus.running {
  color: rgba(100, 116, 139, .62);
}

.jx-toolTimelineStatus.error {
  color: rgba(239, 68, 68, .60);
}

/* 时间线和输出之间的分隔 */
.jx-toolTimelineDivider {
  height: 1px;
  background: rgba(15, 23, 42, .06);
  margin: 12px 0;
}

/* 输出区域标题 */
.jx-toolTimelineOutputHeader {
  font-size: 12px;
  font-weight: 600;
  color: rgba(15, 23, 42, .50);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

/* 无选中时的提示 */
.jx-toolTimelineHint {
  font-size: 12px;
  color: rgba(100, 116, 139, .45);
  padding: 20px 0;
  text-align: center;
}

/* ══════════════════════════════════════════════════════════════
   Canvas 面板中的思考详情
   ══════════════════════════════════════════════════════════════ */

.jx-thinkingDetailPanel {
  padding: 0;
}

.jx-thinkingDetailContent {
  font-size: 13.5px;
  line-height: 1.75;
  color: rgba(15, 23, 42, .72);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: calc(100vh - 160px);
  overflow-y: auto;
}

.jx-thinkingDetailContent::-webkit-scrollbar { width: 4px; }
.jx-thinkingDetailContent::-webkit-scrollbar-thumb { background: rgba(100, 116, 139, .14); border-radius: 3px; }

/* reduced motion */
@media (prefers-reduced-motion: reduce) {
  .jx-pulseDotRing {
    animation: none;
    opacity: 0.3;
    transform: scale(1.8);
  }
  .jx-inlineSummaryDots::after {
    animation: none;
    content: '...';
  }
}
```

### 4.3 Canvas 时间线组件：`ToolTimelinePanel`

**文件**：`src/frontend/src/components/tool/ToolTimelinePanel.tsx`

这是在右侧 Canvas 面板中渲染的工具执行时间线。当 `toolResultPanel.toolName === '__progress_timeline__'` 时，`ToolResultPanel` 委托给此组件渲染。

```tsx
// ── ToolTimelinePanel.tsx ──────────────────────────────────
import { useState } from 'react';
import { CloseCircleOutlined } from '@ant-design/icons';
import { TOOL_NAME_OVERRIDES, PANEL_TOOL_NAMES } from '../../utils/constants';
import { useChatStore, useUIStore } from '../../stores';
import { renderToolOutputBody } from './ToolOutputRenderer';
import type { ToolCall } from '../../types';

interface ToolTimelinePanelProps {
  toolCalls: ToolCall[];
  isStreaming?: boolean;
}

export function ToolTimelinePanel({ toolCalls, isStreaming }: ToolTimelinePanelProps) {
  const { toolDisplayNames } = useChatStore();
  const { setDetailModal } = useUIStore();
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const resolveStatus = (tool: ToolCall): 'running' | 'success' | 'error' => {
    const raw = tool.status ?? 'success';
    if (raw === 'error') return 'error';
    if (raw === 'running' && !isStreaming) return 'success';
    if (raw === 'running') return 'running';
    return 'success';
  };

  const resolveName = (tool: ToolCall): string =>
    tool.displayName || TOOL_NAME_OVERRIDES[tool.name] || toolDisplayNames[tool.name] || tool.name;

  const coerceOutput = (raw: unknown): unknown => {
    if (typeof raw !== 'string') return raw;
    try { return JSON.parse(raw); } catch { return raw; }
  };

  // 简短摘要（复用 MessageBubble 中的 getToolSummary 逻辑）
  const getSummary = (tool: ToolCall): string => {
    if (!tool.output || resolveStatus(tool) === 'running') return '';
    try {
      const parsed = coerceOutput(tool.output) as any;
      switch (tool.name) {
        case 'internet_search': {
          const sr = parsed?.result ?? parsed;
          const results = sr?.results;
          const query = String(sr?.query ?? parsed?.query ?? '').slice(0, 18);
          return Array.isArray(results) ? `"${query}" · ${results.length} 条结果` : '';
        }
        case 'retrieve_dataset_content': {
          const items = parsed?.items;
          return Array.isArray(items) ? `找到 ${items.length} 条文档` : '';
        }
        case 'retrieve_local_kb': {
          const items = Array.isArray(parsed) ? parsed : parsed?.items;
          return Array.isArray(items) ? `找到 ${items.length} 条文档` : '';
        }
        case 'query_database': {
          const s = typeof tool.output === 'string' ? tool.output : '';
          if (s.includes('查询成功') || s.includes('✅')) return '数据查询成功';
          return '';
        }
        case 'search_company': {
          const items = parsed?.items;
          return Array.isArray(items) ? `找到 ${items.length} 家企业` : '';
        }
        case 'get_industry_news': {
          const items = parsed?.items;
          return Array.isArray(items) ? `${items.length} 条产业资讯` : '';
        }
        case 'get_latest_ai_news': {
          const items = parsed?.items;
          return Array.isArray(items) ? `${items.length} 条 AI 热点` : '';
        }
        case 'get_chain_information': return '分析完成';
        case 'get_company_base_info': return '基本信息已获取';
        case 'get_company_business_analysis': return '经营分析完成';
        case 'get_company_tech_insight': return '技术洞察完成';
        case 'get_company_funding': return '资金穿透完成';
        case 'get_company_risk_warning': return '风险预警完成';
        default: return '';
      }
    } catch { return ''; }
  };

  const formatTime = (ts?: number): string => {
    if (!ts) return '';
    const d = new Date(ts);
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}`;
  };

  const selectedTool = selectedIndex !== null ? toolCalls[selectedIndex] : null;

  return (
    <div className="jx-toolTimeline">
      {/* 时间线步骤列表 */}
      {toolCalls.map((tool, idx) => {
        const status = resolveStatus(tool);
        const name = resolveName(tool);
        const summary = getSummary(tool);
        const isSelected = selectedIndex === idx;
        const hasPanelOutput = PANEL_TOOL_NAMES.has(tool.name) && tool.output && status !== 'running';

        return (
          <div
            key={idx}
            className={`jx-toolTimelineStep${isSelected ? ' selected' : ''}`}
            onClick={() => {
              if (hasPanelOutput) setSelectedIndex(isSelected ? null : idx);
            }}
            style={{ cursor: hasPanelOutput ? 'pointer' : 'default' }}
          >
            {/* 左侧轨道 — 脉冲圆点 */}
            <div className="jx-toolTimelineTrack">
              {status === 'error' ? (
                <CloseCircleOutlined className="jx-toolTimelineTrackIcon error" />
              ) : (
                <span className={`jx-toolTimelineTrackDot jx-pulseDot${status === 'running' ? ' running' : ''}`}>
                  <span className="jx-pulseDotCore" />
                  {status === 'running' && <span className="jx-pulseDotRing" />}
                </span>
              )}
              {idx < toolCalls.length - 1 && <div className="jx-toolTimelineLine" />}
            </div>

            {/* 右侧内容 */}
            <div className="jx-toolTimelineContent">
              <div className="jx-toolTimelineName">{name}</div>
              {status === 'running' ? (
                <div className="jx-toolTimelineStatus running">执行中...</div>
              ) : summary ? (
                <div className="jx-toolTimelineSummary">{summary}</div>
              ) : status === 'error' ? (
                <div className="jx-toolTimelineStatus error">执行失败</div>
              ) : null}
              {tool.timestamp && <div className="jx-toolTimelineTime">{formatTime(tool.timestamp)}</div>}
            </div>
          </div>
        );
      })}

      {/* 分隔线 + 选中工具的输出 */}
      {selectedTool && selectedTool.output && (
        <>
          <div className="jx-toolTimelineDivider" />
          <div className="jx-toolTimelineOutputHeader">{resolveName(selectedTool)} — 输出详情</div>
          {renderToolOutputBody(selectedTool.name, coerceOutput(selectedTool.output), setDetailModal)}
        </>
      )}

      {/* 无选中时的提示 */}
      {!selectedTool && toolCalls.some(t => resolveStatus(t) !== 'running' && t.output) && (
        <div className="jx-toolTimelineHint">
          点击已完成的步骤查看输出详情
        </div>
      )}
    </div>
  );
}
```

### 4.6 修改 `ToolResultPanel.tsx`

在现有面板中增加对 `__progress_timeline__` 和 `__thinking_detail__` 的识别：

```tsx
// ToolResultPanel.tsx — 在 body 渲染部分增加分支
import { ToolTimelinePanel } from './ToolTimelinePanel';
import { ThinkingDetailPanel } from '../chat/ThinkingDetailPanel';

// ... 在 return 的 body 区域：
<div className="jx-trp-body" ref={trpBodyRef}>
  {toolResultPanel.toolName === '__progress_timeline__' ? (
    <ToolTimelinePanel
      toolCalls={(toolResultPanel.output as any)?.toolCalls || []}
      isStreaming={(toolResultPanel.output as any)?.isStreaming}
    />
  ) : toolResultPanel.toolName === '__thinking_detail__' ? (
    <ThinkingDetailPanel
      content={(toolResultPanel.output as any)?.content || ''}
      isActiveThinking={(toolResultPanel.output as any)?.isActiveThinking}
    />
  ) : (
    renderToolOutputBody(toolResultPanel.toolName, toolResultPanel.output, setDetailModal)
  )}
</div>
```

### 4.7 修改 `MessageBubble.tsx` 渲染逻辑

**改动 1**：新增 import

```tsx
import { ToolProgressInline } from '../tool';
import { ThinkingInline } from './ThinkingInline';
```

**改动 2**：segment 渲染循环中的 tool 分支（line 496-501）

```tsx
// 当前代码：
if (seg.type === 'tool') {
  if (!dispatchProcessVisible) return null;  // ← 直接隐藏
  const tool = m.toolCalls?.[seg.toolIndex!];
  if (!tool) return null;
  return renderToolCard(tool, segKey);
}

// 改为：
if (seg.type === 'tool') {
  if (!dispatchProcessVisible) {
    // 只在第一个 tool segment 位置渲染内联摘要
    const firstToolSegIdx = m.segments!.findIndex(s => s.type === 'tool');
    if (segIdx === firstToolSegIdx) {
      return (
        <ToolProgressInline
          key={segKey}
          toolCalls={m.toolCalls || []}
          isStreaming={m.isStreaming}
          messageTs={m.ts}
        />
      );
    }
    return null;
  }
  const tool = m.toolCalls?.[seg.toolIndex!];
  if (!tool) return null;
  return renderToolCard(tool, segKey);
}
```

**改动 3**：segment 渲染循环中的 thinking 分支（line 503-506）

```tsx
// 当前代码：
if (seg.type === 'thinking') {
  const isActiveThinking = !!(m.isStreaming && !m.segments!.slice(segIdx + 1).some(s => s.type === 'text'));
  return renderThinkingBlock(seg.content || '', segKey, isActiveThinking);
}

// 改为（当开启思考模式时，用内联摘要替代展开块）：
if (seg.type === 'thinking') {
  const isActiveThinking = !!(m.isStreaming && !m.segments!.slice(segIdx + 1).some(s => s.type === 'text'));

  // 统计当前是第几个 thinking segment（用于生成唯一 panelKey）
  const thinkingIndex = m.segments!.slice(0, segIdx).filter(s => s.type === 'thinking').length;

  return (
    <ThinkingInline
      key={segKey}
      content={seg.content || ''}
      isActiveThinking={isActiveThinking}
      messageTs={m.ts}
      thinkingIndex={thinkingIndex}
    />
  );
}
```

> **注意**：这里所有 thinking segment 都替换为 ThinkingInline（不再区分 thinkingMode 开关）。
> 旧的 `renderThinkingBlock` 函数可以保留但不再被调用，后续清理时删除。

**改动 4**：streaming placeholder 条件（line 533-546）

```tsx
// 改为：内联摘要已提供视觉反馈，不需要额外 placeholder
{m.isStreaming && (() => {
  const lastSeg = m.segments![m.segments!.length - 1];
  if (lastSeg.type === 'tool') {
    if (!dispatchProcessVisible) return false;  // ← 新增
    const tool = m.toolCalls?.[lastSeg.toolIndex!];
    return !tool || tool.status !== 'running';
  }
  if (lastSeg.type === 'thinking') return false;  // ← 新增：ThinkingInline 已有脉冲圆点
  return !m.segments!.some(s => s.type === 'text')
      && !m.segments!.some(s => s.type === 'thinking');
})() && (
  <div className="jx-bubble streaming">
    <span className="jx-streamingIndicator" aria-hidden="true">
      <span className="jx-streamingDot" /><span className="jx-streamingDot" /><span className="jx-streamingDot" />
    </span>
  </div>
)}
```

### 4.8 修改 `components/tool/index.ts`

```ts
export { renderToolOutputBody, ToolOutputBody } from './ToolOutputRenderer';
export { ToolResultPanel } from './ToolResultPanel';
export { ToolProgressInline } from './ToolProgressInline';
export { ToolTimelinePanel } from './ToolTimelinePanel';
```

---

## 五、完整文件改动清单

| 文件 | 类型 | 改动说明 |
|------|------|----------|
| `components/tool/ToolProgressInline.tsx` | **新建** | 工具调用内联一行摘要组件 |
| `components/tool/ToolTimelinePanel.tsx` | **新建** | Canvas 面板中的工具时间线详情组件 |
| `components/chat/ThinkingInline.tsx` | **新建** | 思考过程内联一行摘要组件 |
| `components/chat/ThinkingDetailPanel.tsx` | **新建** | Canvas 面板中的思考详情组件 |
| `components/tool/ToolResultPanel.tsx` | 修改 | 增加 `__progress_timeline__` 和 `__thinking_detail__` 分支 |
| `components/tool/index.ts` | 修改 | 导出新组件 |
| `components/chat/MessageBubble.tsx` | 修改 | tool → ToolProgressInline, thinking → ThinkingInline + streaming placeholder |
| `styles/tool.css` | 修改 | 追加 `.jx-inlineSummary*`、`.jx-pulseDot*`、`.jx-toolTimeline*`、`.jx-thinkingDetail*` |
| `types.ts` | 无改动 | 现有类型已满足 |
| `stores/` | 无改动 | 复用 chatStore.toolResultPanel |
| `hooks/useStreaming.ts` | 无改动 | 数据收集逻辑不变 |

**新增依赖：无**（纯 CSS 动画 + 已有 antd icons）

---

## 六、实现步骤

### Step 1：创建内联摘要组件

1. 新建 `components/tool/ToolProgressInline.tsx` — 工具调用一行摘要
2. 新建 `components/chat/ThinkingInline.tsx` — 思考过程一行摘要
3. 两者共用 `.jx-inlineSummary` 样式类和 `.jx-pulseDot` 脉冲圆点

### Step 2：创建 Canvas 详情组件

1. 新建 `components/tool/ToolTimelinePanel.tsx` — 工具时间线 + 可展开输出
2. 新建 `components/chat/ThinkingDetailPanel.tsx` — 思考内容全文展示

### Step 3：添加 CSS 样式

1. 在 `styles/tool.css` 末尾追加所有样式
2. `.jx-inlineSummary*` — 通用内联摘要行
3. `.jx-pulseDot*` — 脉冲信号圆点 + `@keyframes jxPulseRing`
4. `.jx-inlineSummaryDots` — 省略号动画
5. `.jx-toolTimeline*` — Canvas 工具时间线
6. `.jx-thinkingDetail*` — Canvas 思考详情

### Step 4：修改 ToolResultPanel

1. import ToolTimelinePanel 和 ThinkingDetailPanel
2. 在 body 渲染中增加 `__progress_timeline__` 和 `__thinking_detail__` 两个分支

### Step 5：修改 MessageBubble

1. import ToolProgressInline 和 ThinkingInline
2. tool segment：隐藏模式下第一个 tool segment 渲染 ToolProgressInline
3. thinking segment：所有 thinking segment 替换为 ThinkingInline
4. streaming placeholder：tool 隐藏 + thinking 末尾时不显示三圆点

### Step 6：导出 & 测试

1. 更新 `components/tool/index.ts` 导出
2. 测试场景：
   - **工具调用**：
     - 单工具 running → 脉冲圆点 + "联网搜索..."
     - 多工具 running → 脉冲圆点 + 当前 running 工具名
     - 全部完成 → 静态圆点 + "联网搜索 · 知识库检索 · 数据分析"
     - 有 error → 红色 icon
     - 点击 → Canvas 打开工具时间线
     - 时间线中点击步骤 → 展开工具输出
   - **思考过程**：
     - 思考中 → 脉冲圆点 + "正在思考..."
     - 思考完成 → 静态圆点 + "思考过程"
     - 点击 → Canvas 打开完整思考内容
     - 思考中 Canvas 自动滚到底部
   - **兼容性**：
     - 开启 dispatchProcessVisible → 工具调用仍显示完整 tool card
     - 思考内联摘要始终生效（不区分 thinkingMode 开关）

---

## 七、效果对比

| 维度 | 当前 | 优化后 |
|------|------|--------|
| 工具调用（隐藏模式） | 三个跳动圆点（空白） | 脉冲圆点 + 一行灰色工具名摘要 |
| 思考过程 | 内联展开块，max-height 280px | 脉冲圆点 + 一行灰色 "正在思考..." / "思考过程" |
| 信息密度 | 工具：零；思考：占 280px | 各占一行（~28px），极低视觉权重 |
| 详情入口 | 工具：需开启设置；思考：内联展开 | 统一：点击 `>` 打开右侧 Canvas |
| Canvas 内容 | 无 | 工具：时间线 + 可展开输出；思考：全文滚动 |
| 色彩 | 蓝色圆点 | 全灰色调，仅 error 使用淡红 |
| 视觉一致性 | 工具和思考是两套不同的UI | 统一为脉冲圆点 + 一行摘要 + Canvas 详情 |
| 新增依赖 | — | **零** |
| ���动文件 | — | 4 新 + 3 改 |
