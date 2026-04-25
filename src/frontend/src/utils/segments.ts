import type { ChatMessage, MessageSegment } from '../types';

/**
 * 从历史消息的 content 字段解析多轮思考块，重建 segments 用于历史记录的内联渲染。
 *
 * content 的存储格式（多轮工具调用）：
 *   [thinking1]</think>[thinking2]</think>...[thinkingN]</think>[最终正文]
 *
 * 对应关系：
 *   thinking1 → tool[0] → thinking2 → tool[1] → ... → thinkingN → 最终正文
 *
 * - 按 </think> 拆分，除最后一段外每段均为思考块
 * - 每个思考块之后配对下一个工具调用（按顺序）
 * - 最后一段为最终正文
 * - 若无 </think> 则直接输出工具调用 + 正文
 */
export function buildHistorySegments(
  content: string,
  toolCalls?: ChatMessage['toolCalls']
): { segments: MessageSegment[] | undefined; cleanContent: string } {
  const parts = content.split('</think>');
  const toolCount = toolCalls?.length ?? 0;

  // 没有任何 </think>：无思考块，直接工具 + 正文
  if (parts.length === 1) {
    const segments: MessageSegment[] = [];
    if (toolCount > 0) toolCalls!.forEach((_, i) => segments.push({ type: 'tool', toolIndex: i }));
    const text = content.trim();
    if (text) segments.push({ type: 'text', content: text });
    return { segments: segments.length > 0 ? segments : undefined, cleanContent: text };
  }

  const segments: MessageSegment[] = [];
  const thinkingParts = parts.slice(0, -1);
  const finalText = parts[parts.length - 1].trim();

  thinkingParts.forEach((part, idx) => {
    const openTagIdx = part.indexOf('<think>');
    const thinkContent = openTagIdx >= 0 ? part.slice(openTagIdx + 7) : part;
    if (thinkContent.trim()) segments.push({ type: 'thinking', content: thinkContent });
    if (idx < toolCount) segments.push({ type: 'tool', toolIndex: idx });
  });

  if (finalText) segments.push({ type: 'text', content: finalText });
  for (let i = thinkingParts.length; i < toolCount; i++) {
    segments.push({ type: 'tool', toolIndex: i });
  }

  return {
    segments: segments.length > 0 ? segments : undefined,
    cleanContent: finalText,
  };
}
