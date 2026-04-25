import { useMemo } from 'react';
import { Tooltip } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloseOutlined,
  MessageOutlined,
  LoadingOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { useAutomationChatStore, useAutomationStore, useCatalogStore } from '../../stores';
import type { AutomationRun, AutomationRunStatus } from '../../types';
import { pad2 } from '../../utils/date';
import { RUN_STATUS_LABEL } from '../lab/automationUtils';
import '../../styles/automation-timeline.css';

interface DateGroup {
  date: string;       // e.g. "04.16"
  fullDate: string;   // e.g. "2026-04-16" for key
  runs: (AutomationRun & { runNo: number })[];
}

function formatTime(iso?: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function formatDateLabel(iso: string): string {
  const d = new Date(iso);
  return `${pad2(d.getMonth() + 1)}.${pad2(d.getDate())}`;
}

function formatShortDate(iso: string): string {
  const d = new Date(iso);
  return `${pad2(d.getMonth() + 1)}/${pad2(d.getDate())} ${formatTime(iso)}`;
}

function getDateKey(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function formatDuration(durationMs?: number): string {
  if (!durationMs || durationMs <= 0) return '-';
  if (durationMs < 1000) return `${durationMs}ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSeconds = Math.round(seconds % 60);
  return `${minutes}m ${pad2(remainSeconds)}s`;
}

function formatFullDateTime(iso?: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return `${d.getFullYear()}.${pad2(d.getMonth() + 1)}.${pad2(d.getDate())} ${formatTime(iso)}`;
}

function formatCompactDate(iso?: string): string {
  if (!iso) return '-';
  const d = new Date(iso);
  return `${pad2(d.getMonth() + 1)}.${pad2(d.getDate())}`;
}

// 第一次、第二次…第十次，超过 10 使用阿拉伯数字
const CN_DIGITS = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
function formatRunOrdinal(n: number): string {
  return `第${CN_DIGITS[n] || n}次`;
}

function summarizeRun(run: AutomationRun): string {
  if (run.status === 'running') return '任务正在执行，结果生成后会自动更新到对话区。';
  if (run.status === 'failed') {
    return String(run.error_message || run.result_summary || '执行失败，请进入详情查看错误信息。').replace(/\s+/g, ' ').trim();
  }
  return String(run.result_summary || '执行完成，可进入对话查看完整输出与附件结果。').replace(/\s+/g, ' ').trim();
}

export function RunTimelinePanel() {
  const { activeGroup, selectedRunId, selectRun, exitAutomationChat } = useAutomationChatStore();
  const { setSelectedTaskId } = useAutomationStore();
  const { setPanel } = useCatalogStore();

  const numberedRuns = useMemo<(AutomationRun & { runNo: number })[]>(() => {
    if (!activeGroup) return [];

    // Number runs in ascending order (oldest = #1)
    const allRuns = [...activeGroup.runs];
    const ascending = [...allRuns].reverse();
    return allRuns.map((run) => ({
      ...run,
      runNo: ascending.findIndex((r) => r.run_id === run.run_id) + 1,
    }));
  }, [activeGroup]);

  const groupedRuns = useMemo<DateGroup[]>(() => {
    if (!activeGroup) return [];

    // Group by date (runs are already desc by started_at)
    const map = new Map<string, DateGroup>();
    for (const run of numberedRuns) {
      const key = getDateKey(run.started_at);
      if (!map.has(key)) {
        map.set(key, {
          date: formatDateLabel(run.started_at),
          fullDate: key,
          runs: [],
        });
      }
      map.get(key)!.runs.push(run);
    }
    return Array.from(map.values());
  }, [activeGroup, numberedRuns]);

  // 一次遍历累积所有状态计数 + 最近一次成功记录，避免多次 filter/find 带来的 O(kn) 开销。
  const stats = useMemo(() => {
    let running = 0;
    let success = 0;
    let failed = 0;
    let latestSuccessRun: AutomationRun | undefined;
    let latestFinishedRun: AutomationRun | undefined;
    for (const run of numberedRuns) {
      if (run.status === 'running') running += 1;
      else if (run.status === 'success') {
        success += 1;
        if (!latestSuccessRun) latestSuccessRun = run;
        if (!latestFinishedRun) latestFinishedRun = run;
      } else if (run.status === 'failed') {
        failed += 1;
        if (!latestFinishedRun) latestFinishedRun = run;
      }
    }
    const total = numberedRuns.length;
    const completed = total - running;
    const successRate = completed > 0 ? Math.round((success / completed) * 1000) / 10 : 0;
    return { total, running, success, failed, completed, successRate, latestSuccessRun, latestFinishedRun };
  }, [numberedRuns]);

  if (!activeGroup) return null;

  const { total: totalCount, running: runningCount, success: successCount, failed: failedCount,
    completed: completedCount, successRate, latestSuccessRun, latestFinishedRun } = stats;
  const latestRun = numberedRuns[0];

  const navigateBackToDetail = () => {
    setSelectedTaskId(activeGroup.taskId);
    setPanel('lab');
    exitAutomationChat();
  };

  const latestLabel = latestRun ? `最近一次${RUN_STATUS_LABEL[latestRun.status]}` : '暂无执行';
  const latestIndicatorRun = latestSuccessRun || latestRun;
  const latestIndicatorStatus: AutomationRunStatus | 'idle' =
    latestIndicatorRun ? latestIndicatorRun.status : 'idle';

  const topStats: { label: string; value: string | number; primary?: boolean }[] = [
    { label: '执行次数', value: totalCount, primary: true },
    { label: '成功率', value: `${successRate}%`, primary: true },
    { label: '最近完成时间', value: latestFinishedRun ? formatCompactDate(latestFinishedRun.started_at) : '—' },
  ];
  const miniStats: { label: string; value: number }[] = [
    { label: '全部', value: totalCount },
    { label: '成功', value: successCount },
    { label: '失败', value: failedCount },
    { label: '执行中', value: runningCount },
  ];

  return (
    <div className="jx-runTimeline">
      {/* Top bar: 自动化任务 >   X */}
      <div className="jx-runTimeline-topBar">
        <button
          type="button"
          className="jx-runTimeline-crumb"
          onClick={navigateBackToDetail}
          title="返回自动化任务详情"
        >
          <span>自动化任务</span>
          <RightOutlined className="jx-runTimeline-crumbIcon" />
        </button>
        <button
          type="button"
          className="jx-runTimeline-closeBtn"
          onClick={exitAutomationChat}
          aria-label="关闭"
          title="关闭面板"
        >
          <CloseOutlined />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="jx-runTimeline-body">
        {/* 标题 + 副标题 */}
        <div className="jx-runTimeline-titleBlock">
          <div className="jx-runTimeline-title" title={activeGroup.taskName}>
            {activeGroup.taskName}
          </div>
          <div className="jx-runTimeline-subtitle">
            运行舱记录最近执行状态、摘要与异常线索
          </div>
        </div>

        {/* 顶部 3 列统计 */}
        <div className="jx-runTimeline-statRow">
          {topStats.map((s) => (
            <div key={s.label} className="jx-runTimeline-statCard">
              <span className="jx-runTimeline-statLabel">{s.label}</span>
              <strong className={`jx-runTimeline-statValue${s.primary ? ' is-primary' : ''}`}>
                {s.value}
              </strong>
            </div>
          ))}
        </div>

        {/* 最近一次状态 */}
        <div className={`jx-runTimeline-latestRow is-${latestIndicatorStatus}`}>
          <span className="jx-runTimeline-latestIcon">
            {latestIndicatorStatus === 'success' && <CheckCircleOutlined />}
            {latestIndicatorStatus === 'failed' && <CloseCircleOutlined />}
            {latestIndicatorStatus === 'running' && <LoadingOutlined spin />}
            {latestIndicatorStatus === 'idle' && <CheckCircleOutlined />}
          </span>
          <span className="jx-runTimeline-latestLabel">
            {latestIndicatorRun ? latestLabel : '暂无执行'}：
          </span>
          <span className="jx-runTimeline-latestTime">
            {latestIndicatorRun ? formatFullDateTime(latestIndicatorRun.started_at) : '—'}
          </span>
        </div>

        {/* 执行记录 */}
        <section className="jx-runTimeline-section">
          <div className="jx-runTimeline-sectionHeading">
            <span className="jx-runTimeline-sectionBar" />
            <span>执行记录</span>
          </div>
          <div className="jx-runTimeline-sectionMeta">
            已完成 {completedCount} 次，失败 {failedCount} 次
          </div>
          <div className="jx-runTimeline-miniStatRow">
            {miniStats.map((s) => (
              <div key={s.label} className="jx-runTimeline-miniStat">
                <span className="jx-runTimeline-miniStatLabel">{s.label}</span>
                <strong className="jx-runTimeline-miniStatValue">{s.value}</strong>
              </div>
            ))}
          </div>
        </section>

        {/* 详情时间线 */}
        <section className="jx-runTimeline-section">
          <div className="jx-runTimeline-sectionHeading">
            <span className="jx-runTimeline-sectionBar" />
            <span>详情</span>
          </div>

          {groupedRuns.length === 0 ? (
            <div className="jx-runTimeline-empty">
              <div className="jx-runTimeline-emptyTitle">暂无运行记录</div>
              <div className="jx-runTimeline-emptyHint">等待下一次自动执行或手动触发任务</div>
            </div>
          ) : (
            <div className="jx-runTimeline-timeline">
              {groupedRuns.map((group) => (
                <div key={group.fullDate} className="jx-runTimeline-dateGroup">
                  <div className="jx-runTimeline-dateHeader">
                    <span className="jx-runTimeline-dateLabel">{group.date}</span>
                    <span className="jx-runTimeline-dateCount">{group.runs.length} 次</span>
                  </div>
                  <div className="jx-runTimeline-dateBody">
                    {group.runs.map((run) => {
                      const isActive = run.run_id === selectedRunId;
                      const isRunning = run.status === 'running';
                      const isClickable = !isRunning && !!run.chat_id;
                      const summary = summarizeRun(run);

                      return (
                        <Tooltip
                          key={run.run_id}
                          title={isRunning ? '执行中，暂不可查看' : RUN_STATUS_LABEL[run.status]}
                          placement="left"
                        >
                          <div
                            className={[
                              'jx-runTimeline-item',
                              isActive && 'is-active',
                              `is-${run.status}`,
                              !isClickable && 'is-disabled',
                            ].filter(Boolean).join(' ')}
                            role={isClickable ? 'button' : undefined}
                            tabIndex={isClickable ? 0 : -1}
                            onClick={() => isClickable && selectRun(run.run_id)}
                            onKeyDown={(event) => {
                              if (!isClickable) return;
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                selectRun(run.run_id);
                              }
                            }}
                          >
                            <div className="jx-runTimeline-itemTop">
                              <div className="jx-runTimeline-itemIdentity">
                                <span className="jx-runTimeline-runNo">{formatRunOrdinal(run.runNo)}</span>
                                <span className={`jx-runTimeline-statusTag is-${run.status}`}>
                                  {RUN_STATUS_LABEL[run.status]}
                                </span>
                              </div>
                              <span className="jx-runTimeline-itemTime">
                                {formatShortDate(run.started_at)}
                              </span>
                            </div>
                            <div className="jx-runTimeline-itemSummary">{summary}</div>
                            <div className="jx-runTimeline-itemBottom">
                              <span className="jx-runTimeline-itemDuration">
                                {run.duration_ms
                                  ? `耗时 ${formatDuration(run.duration_ms)}`
                                  : (isRunning ? '执行中' : '耗时 —')}
                              </span>
                              <span
                                className={[
                                  'jx-runTimeline-itemEnter',
                                  !isClickable && 'is-disabled',
                                ].filter(Boolean).join(' ')}
                              >
                                <MessageOutlined />
                                <span>{isClickable ? '进入对话' : '生成中'}</span>
                              </span>
                            </div>
                          </div>
                        </Tooltip>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
