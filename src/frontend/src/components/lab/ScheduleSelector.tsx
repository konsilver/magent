/**
 * ScheduleSelector — 自动化任务调度配置的统一 UI。
 * 同时供 AutomationCreateModal 和 AutomationDetailPage 编辑态使用。
 *
 * 三种调度方式：
 *   - recurring 周期执行  → 选频率 (hourly/daily/weekday/weekly) + 具体时间；weekly 还要选周几
 *   - once     单次执行  → 选具体日期时间
 *   - manual   手动执行  → 无额外配置
 *
 * 对外暴露的唯一"权威值"是 schedule_type 和 cron_expression。
 * 不再暴露自定义 cron 输入框——用户不需要理解 cron 语法。
 */

import { useEffect, useMemo, useState } from 'react';
import { Radio, Select, TimePicker, DatePicker } from 'antd';
import dayjs, { Dayjs } from 'dayjs';
import type { AutomationScheduleType } from '../../types';

type FrequencyKey = 'hourly' | 'daily' | 'weekday' | 'weekly';

const FREQ_OPTIONS: { value: FrequencyKey; label: string }[] = [
  { value: 'hourly', label: '每小时' },
  { value: 'daily', label: '每天' },
  { value: 'weekday', label: '工作日（周一至周五）' },
  { value: 'weekly', label: '每周' },
];

const WEEKDAY_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 0, label: '周日' },
];

export interface ScheduleValue {
  schedule_type: AutomationScheduleType;
  cron_expression: string;
}

interface Props {
  value: ScheduleValue;
  onChange: (next: ScheduleValue) => void;
  disabled?: boolean;
}

// ─── Cron builders ─────────────────────────────────────────────

function buildRecurringCron(
  freq: FrequencyKey,
  hour: number,
  minute: number,
  weekday: number,
): string {
  switch (freq) {
    case 'hourly':
      return `${minute} * * * *`;
    case 'daily':
      return `${minute} ${hour} * * *`;
    case 'weekday':
      return `${minute} ${hour} * * 1-5`;
    case 'weekly':
      return `${minute} ${hour} * * ${weekday}`;
  }
}

function buildOnceCron(dt: Dayjs): string {
  // One-shot: pin minute/hour/day/month; weekday wildcard.
  return `${dt.minute()} ${dt.hour()} ${dt.date()} ${dt.month() + 1} *`;
}

// ─── Parse cron back into UI state (best-effort) ─────────────

function parseRecurringCron(cron: string): {
  freq: FrequencyKey;
  hour: number;
  minute: number;
  weekday: number;
} {
  const fallback = { freq: 'daily' as FrequencyKey, hour: 9, minute: 0, weekday: 1 };
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return fallback;
  const [m, h, , , dow] = parts;
  const minute = /^\d+$/.test(m) ? parseInt(m, 10) : 0;
  const hour = /^\d+$/.test(h) ? parseInt(h, 10) : 9;

  // hourly: minute固定，hour=*
  if (h === '*' || h.startsWith('*/')) {
    return { freq: 'hourly', hour: 0, minute, weekday: 1 };
  }
  if (dow === '1-5') {
    return { freq: 'weekday', hour, minute, weekday: 1 };
  }
  if (/^\d$/.test(dow)) {
    return { freq: 'weekly', hour, minute, weekday: parseInt(dow, 10) };
  }
  return { freq: 'daily', hour, minute, weekday: 1 };
}

function parseOnceCron(cron: string): Dayjs {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return dayjs().add(1, 'hour').startOf('minute');
  const [m, h, d, mo] = parts.map((p) => parseInt(p, 10));
  const now = dayjs();
  let year = now.year();
  // If the month/day has already passed this year, assume next year.
  const candidate = dayjs().year(year).month(mo - 1).date(d).hour(h).minute(m).second(0);
  if (candidate.isBefore(now)) {
    year += 1;
  }
  return dayjs().year(year).month(mo - 1).date(d).hour(h).minute(m).second(0);
}

// ─── Component ─────────────────────────────────────────────

export function ScheduleSelector({ value, onChange, disabled }: Props) {
  const [freq, setFreq] = useState<FrequencyKey>('daily');
  const [weekday, setWeekday] = useState<number>(1);
  const [time, setTime] = useState<Dayjs>(dayjs('09:00', 'HH:mm'));
  const [onceAt, setOnceAt] = useState<Dayjs>(dayjs().add(1, 'hour').startOf('minute'));

  // ── On mount (or when value changes from outside), hydrate local state from the current cron.
  useEffect(() => {
    if (value.schedule_type === 'recurring') {
      const parsed = parseRecurringCron(value.cron_expression);
      setFreq(parsed.freq);
      setWeekday(parsed.weekday);
      setTime(dayjs().hour(parsed.hour).minute(parsed.minute).second(0));
    } else if (value.schedule_type === 'once') {
      setOnceAt(parseOnceCron(value.cron_expression));
    }
    // manual: nothing to hydrate
    // Only re-run when the externally provided schedule_type/cron changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.schedule_type]);

  const emit = (next: ScheduleValue) => {
    if (disabled) return;
    onChange(next);
  };

  // ── Schedule type radio
  const handleTypeChange = (newType: AutomationScheduleType) => {
    if (newType === 'recurring') {
      emit({
        schedule_type: 'recurring',
        cron_expression: buildRecurringCron(freq, time.hour(), time.minute(), weekday),
      });
    } else if (newType === 'once') {
      emit({
        schedule_type: 'once',
        cron_expression: buildOnceCron(onceAt),
      });
    } else {
      // manual — cron stays semantically irrelevant, but backend requires min_length=9
      emit({
        schedule_type: 'manual',
        cron_expression: '0 0 1 1 *',
      });
    }
  };

  // ── Frequency change
  const handleFreqChange = (newFreq: FrequencyKey) => {
    setFreq(newFreq);
    emit({
      schedule_type: 'recurring',
      cron_expression: buildRecurringCron(newFreq, time.hour(), time.minute(), weekday),
    });
  };

  const handleWeekdayChange = (newWeekday: number) => {
    setWeekday(newWeekday);
    emit({
      schedule_type: 'recurring',
      cron_expression: buildRecurringCron(freq, time.hour(), time.minute(), newWeekday),
    });
  };

  const handleTimeChange = (newTime: Dayjs | null) => {
    if (!newTime) return;
    setTime(newTime);
    emit({
      schedule_type: 'recurring',
      cron_expression: buildRecurringCron(freq, newTime.hour(), newTime.minute(), weekday),
    });
  };

  const handleOnceChange = (newDt: Dayjs | null) => {
    if (!newDt) return;
    setOnceAt(newDt);
    emit({
      schedule_type: 'once',
      cron_expression: buildOnceCron(newDt),
    });
  };

  // ── Readable preview
  const previewText = useMemo(() => {
    if (value.schedule_type === 'manual') {
      return '仅手动触发，不会自动运行';
    }
    if (value.schedule_type === 'once') {
      return `将在 ${onceAt.format('YYYY-MM-DD HH:mm')} 执行一次`;
    }
    const t = time.format('HH:mm');
    switch (freq) {
      case 'hourly':
        return `每小时（每到 ${String(time.minute()).padStart(2, '0')} 分时）执行`;
      case 'daily':
        return `每天 ${t} 执行`;
      case 'weekday':
        return `工作日 ${t} 执行（周一至周五）`;
      case 'weekly': {
        const label = WEEKDAY_OPTIONS.find((w) => w.value === weekday)?.label || '周一';
        return `每${label.replace('周', '周')} ${t} 执行`;
      }
    }
  }, [value.schedule_type, freq, time, weekday, onceAt]);

  return (
    <div className="jx-schedule-selector">
      <Radio.Group
        value={value.schedule_type}
        onChange={(e) => handleTypeChange(e.target.value)}
        disabled={disabled}
        style={{ marginBottom: 12 }}
      >
        <Radio.Button value="recurring">周期执行</Radio.Button>
        <Radio.Button value="once">单次执行</Radio.Button>
        <Radio.Button value="manual">手动执行</Radio.Button>
      </Radio.Group>

      {value.schedule_type === 'recurring' && (
        <div className="jx-schedule-selector-body">
          <div className="jx-schedule-selector-row">
            <label className="jx-schedule-selector-label">频率</label>
            <Select
              value={freq}
              onChange={handleFreqChange}
              options={FREQ_OPTIONS}
              disabled={disabled}
              style={{ width: 220 }}
            />
          </div>

          {freq === 'weekly' && (
            <div className="jx-schedule-selector-row">
              <label className="jx-schedule-selector-label">星期</label>
              <Select
                value={weekday}
                onChange={handleWeekdayChange}
                options={WEEKDAY_OPTIONS}
                disabled={disabled}
                style={{ width: 160 }}
              />
            </div>
          )}

          {freq !== 'hourly' && (
            <div className="jx-schedule-selector-row">
              <label className="jx-schedule-selector-label">时间</label>
              <TimePicker
                value={time}
                onChange={handleTimeChange}
                format="HH:mm"
                minuteStep={5}
                allowClear={false}
                disabled={disabled}
                style={{ width: 140 }}
              />
            </div>
          )}

          {freq === 'hourly' && (
            <div className="jx-schedule-selector-row">
              <label className="jx-schedule-selector-label">起始分钟</label>
              <Select
                value={time.minute()}
                onChange={(m) => handleTimeChange(time.minute(m))}
                options={[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map((m) => ({
                  value: m,
                  label: `第 ${m} 分`,
                }))}
                disabled={disabled}
                style={{ width: 140 }}
              />
            </div>
          )}
        </div>
      )}

      {value.schedule_type === 'once' && (
        <div className="jx-schedule-selector-body">
          <div className="jx-schedule-selector-row">
            <label className="jx-schedule-selector-label">执行时间</label>
            <DatePicker
              value={onceAt}
              onChange={handleOnceChange}
              showTime={{ format: 'HH:mm', minuteStep: 5 }}
              format="YYYY-MM-DD HH:mm"
              allowClear={false}
              disabled={disabled}
              style={{ width: 220 }}
            />
          </div>
        </div>
      )}

      <div className="jx-schedule-selector-preview">{previewText}</div>
    </div>
  );
}
