import type { AutomationRunStatus } from '../../types';

/** 自动化单次执行状态的中文标签。统一放在这里避免在多个组件重复声明。 */
export const RUN_STATUS_LABEL: Record<AutomationRunStatus, string> = {
  running: '执行中',
  success: '成功',
  failed: '失败',
};

/** Convert a 5-field cron expression to a human-readable Chinese string. */
export function cronToHumanReadable(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, , , dayOfWeek] = parts;

  const timeStr = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

  const DOW_MAP: Record<string, string> = {
    '1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '0': '日', '7': '日',
  };

  // Every N hours
  if (hour.startsWith('*/')) {
    const n = hour.slice(2);
    return `每 ${n} 小时`;
  }
  // Every N minutes
  if (minute.startsWith('*/')) {
    const n = minute.slice(2);
    return `每 ${n} 分钟`;
  }

  // Specific day of week
  if (dayOfWeek === '1-5') return `工作日 ${timeStr}`;
  if (dayOfWeek === '*') return `每天 ${timeStr}`;
  if (/^\d$/.test(dayOfWeek)) return `每周${DOW_MAP[dayOfWeek] || dayOfWeek} ${timeStr}`;

  return `${cron} (自定义)`;
}

/** Format ISO date string to relative time (e.g., "2小时后"). */
export function formatRelativeTime(isoStr: string): string {
  const target = new Date(isoStr).getTime();
  const now = Date.now();
  const diffMs = target - now;

  if (diffMs < 0) return '已过期';

  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return '即将执行';
  if (minutes < 60) return `${minutes} 分钟后`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时后`;
  const days = Math.floor(hours / 24);
  return `${days} 天后`;
}

/** Cron preset options for the UI. */
export const CRON_PRESETS = [
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '工作日 09:00', value: '0 9 * * 1-5' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每 2 小时', value: '0 */2 * * *' },
  { label: '每 6 小时', value: '0 */6 * * *' },
  { label: '自定义', value: '' },
];
