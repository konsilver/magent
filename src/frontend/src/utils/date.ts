export function pad2(value: number) {
  return String(value).padStart(2, '0');
}

function toDate(value?: string | number | Date | null) {
  if (value === null || value === undefined || value === '') return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value?: string | number | Date | null, fallback = '--') {
  const date = toDate(value);
  if (!date) return fallback;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}
