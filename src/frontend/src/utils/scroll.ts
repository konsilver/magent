// 距离底部 > 此阈值时，视为用户主动上滑，流式消息不再强制把页面拉回底部
export const SCROLL_FOLLOW_THRESHOLD = 100;
// "回到底部"按钮的显示阈值；小于 SCROLL_FOLLOW_THRESHOLD 保证用户稍微上滑就能看到按钮
export const SCROLL_TO_BOTTOM_BTN_THRESHOLD = 80;

export function distanceFromBottom(el: HTMLElement): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight;
}

export function scrollElementToBottom(el: HTMLElement, smooth = false): void {
  if (smooth) {
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  } else {
    el.scrollTop = el.scrollHeight;
  }
}
