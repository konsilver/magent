import { useState, type ReactNode } from 'react';
import {
  CheckCircleFilled, CloseCircleFilled, LoadingOutlined,
  RightOutlined, DownOutlined,
  ToolOutlined, AppstoreOutlined, SafetyCertificateOutlined,
  ThunderboltOutlined, OrderedListOutlined, RobotOutlined,
  DatabaseOutlined, SafetyOutlined, BranchesOutlined,
} from '@ant-design/icons';

/* ───────────────────────────────────────────
   Plan Card — structured plan rendering
   ─────────────────────────────────────────── */

export type AgentActivityType = 'memory_query' | 'subagent_executing' | 'qa_checking' | 'planner_replanning';

export interface AgentActivity {
  activity: AgentActivityType;
  label: string;
  status: 'running' | 'done';
}

export interface PlanStepData {
  step_order: number;
  title: string;
  brief_description?: string;  // one-line summary shown in collapsed view
  description?: string;
  expected_tools?: string[];
  expected_skills?: string[];
  expected_agents?: string[];
  acceptance_criteria?: string;
  status?: 'pending' | 'running' | 'success' | 'failed' | 'skipped' | 'redo_failed';
  summary?: string;
  text?: string;          // live progress text during execution
  agentActivities?: AgentActivity[];  // live agent activity timeline during execution
  // REPLAN display fields
  replaced?: boolean;     // step was replaced by replan (show strikethrough)
  is_replan_new?: boolean; // this is a newly inserted replan step
  replan_reason?: string; // why this step was replanned
}

export interface PlanCardProps {
  mode: 'preview' | 'executing' | 'complete';
  title: string;
  description?: string;
  steps: PlanStepData[];
  completedSteps?: number;
  totalSteps?: number;
  resultText?: string;    // final report for 'complete' mode
  isStreaming?: boolean;
  agentNameMap?: Record<string, string>;
  /**
   * Override the footer rendered in 'preview' mode.
   * - `undefined` → 默认"请回复确认执行"提示（聊天场景）
   * - `null`       → 隐藏 footer
   * - ReactNode    → 自定义 footer 内容
   */
  previewFooter?: ReactNode | null;
  /** 额外的 className，用于变体样式（如 embed） */
  className?: string;
  /** 默认步骤展开（preview 模式下） */
  defaultExpandSteps?: boolean;
}

/* ── Agent activity icon helper ── */
function activityIcon(activity: AgentActivityType) {
  switch (activity) {
    case 'memory_query': return <DatabaseOutlined />;
    case 'subagent_executing': return <RobotOutlined />;
    case 'qa_checking': return <SafetyOutlined />;
    case 'planner_replanning': return <BranchesOutlined />;
  }
}

/* ── Agent activity timeline (shown inside running step) ── */
function AgentActivityTimeline({ activities }: { activities: AgentActivity[] }) {
  if (!activities || activities.length === 0) return null;
  return (
    <div className="jx-plan-activityTimeline">
      {activities.map((act, i) => (
        <div key={i} className={`jx-plan-activityItem jx-plan-activityItem--${act.status}`}>
          <span className="jx-plan-activityIcon">
            {act.status === 'running'
              ? <LoadingOutlined spin />
              : act.status === 'done'
                ? <CheckCircleFilled className="jx-plan-activityDone" />
                : activityIcon(act.activity)}
          </span>
          <span className="jx-plan-activityLabel">{act.label}</span>
        </div>
      ))}
    </div>
  );
}

/* ── Status icon helper ── */
function StepStatusIcon({ status }: { status?: string }) {
  switch (status) {
    case 'success':
      return <CheckCircleFilled className="jx-plan-stepIcon jx-plan-stepIcon--success" />;
    case 'failed':
    case 'redo_failed':
      return <CloseCircleFilled className="jx-plan-stepIcon jx-plan-stepIcon--error" />;
    case 'running':
      return <LoadingOutlined className="jx-plan-stepIcon jx-plan-stepIcon--running" spin />;
    case 'skipped':
      return <span className="jx-plan-stepIcon jx-plan-stepIcon--skipped">—</span>;
    default:
      return <span className="jx-plan-stepIcon jx-plan-stepIcon--pending" />;
  }
}

/* ── Single step row ── */
function PlanStepRow({ step, index, mode, agentNameMap, defaultExpanded }: { step: PlanStepData; index: number; mode: string; agentNameMap?: Record<string, string>; defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(!!defaultExpanded);
  const hasDetails = !!(step.description || step.expected_tools?.length || step.expected_skills?.length || step.expected_agents?.length || step.acceptance_criteria);
  const showExpand = hasDetails;
  const isActive = step.status === 'running';
  const isRedoFailed = step.status === 'redo_failed';
  const isReplaced = step.replaced;
  const isReplanNew = step.is_replan_new;

  const stepCls = [
    'jx-plan-step',
    isActive ? 'jx-plan-step--active' : '',
    step.status === 'success' ? 'jx-plan-step--done' : '',
    isRedoFailed ? 'jx-plan-step--redo-failed' : '',
    isReplaced ? 'jx-plan-step--replaced' : '',
    isReplanNew ? 'jx-plan-step--replan-new' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={stepCls}>
      {/* REPLAN: replaced step (strikethrough label) */}
      {isReplaced && (
        <div className="jx-plan-replanBadge jx-plan-replanBadge--replaced">已替换</div>
      )}
      {/* REPLAN: newly inserted step */}
      {isReplanNew && (
        <div className="jx-plan-replanBadge jx-plan-replanBadge--new">已自动优化</div>
      )}

      <div
        className="jx-plan-stepHeader"
        onClick={showExpand ? () => setExpanded(!expanded) : undefined}
        style={showExpand ? { cursor: 'pointer' } : undefined}
      >
        <div className="jx-plan-stepLeft">
          {mode === 'preview' ? (
            <span className="jx-plan-stepNum">{index + 1}</span>
          ) : (
            <StepStatusIcon status={step.status} />
          )}
          <div className="jx-plan-stepTitleGroup">
            <span className={`jx-plan-stepTitle ${isReplaced ? 'jx-plan-stepTitle--struck' : ''}`}>
              {step.title}
            </span>
            {/* brief_description shown below title when collapsed */}
            {step.brief_description && !expanded && (
              <span className="jx-plan-stepBrief">{step.brief_description}</span>
            )}
          </div>
        </div>
        {showExpand && (
          <span className="jx-plan-stepExpand">
            {expanded ? <DownOutlined /> : <RightOutlined />}
          </span>
        )}
      </div>

      {/* Expandable details (both preview and execution modes) */}
      {expanded && (
        <div className="jx-plan-stepDetails">
          {step.description && <p className="jx-plan-stepDesc">{step.description}</p>}
          {step.replan_reason && (
            <p className="jx-plan-replanReason">优化原因：{step.replan_reason}</p>
          )}
          {step.expected_tools && step.expected_tools.length > 0 && (
            <div className="jx-plan-stepMeta">
              <ToolOutlined className="jx-plan-metaIcon" />
              <span className="jx-plan-metaLabel">MCP 工具</span>
              <div className="jx-plan-tags">
                {step.expected_tools.map((t, i) => <span key={i} className="jx-plan-tag jx-plan-tag--tool">{t}</span>)}
              </div>
            </div>
          )}
          {step.expected_skills && step.expected_skills.length > 0 && (
            <div className="jx-plan-stepMeta">
              <AppstoreOutlined className="jx-plan-metaIcon" />
              <span className="jx-plan-metaLabel">技能</span>
              <div className="jx-plan-tags">
                {step.expected_skills.map((s, i) => <span key={i} className="jx-plan-tag jx-plan-tag--skill">{s}</span>)}
              </div>
            </div>
          )}
          {step.expected_agents && step.expected_agents.length > 0 && (
            <div className="jx-plan-stepMeta">
              <RobotOutlined className="jx-plan-metaIcon" />
              <span className="jx-plan-metaLabel">子智能体</span>
              <div className="jx-plan-tags">
                {step.expected_agents.map((a, i) => <span key={i} className="jx-plan-tag jx-plan-tag--agent">{agentNameMap?.[a] || a}</span>)}
              </div>
            </div>
          )}
          {step.acceptance_criteria && (
            <div className="jx-plan-stepMeta">
              <SafetyCertificateOutlined className="jx-plan-metaIcon" />
              <span className="jx-plan-metaLabel">验收标准</span>
              <span className="jx-plan-metaVal">{step.acceptance_criteria}</span>
            </div>
          )}
        </div>
      )}

      {/* Execution mode: REDO notification */}
      {mode !== 'preview' && isRedoFailed && (
        <div className="jx-plan-redoHint">QA 验证未通过，正在重试...</div>
      )}

      {/* Execution mode: agent activity timeline (running step only) */}
      {mode !== 'preview' && isActive && step.agentActivities && step.agentActivities.length > 0 && (
        <AgentActivityTimeline activities={step.agentActivities} />
      )}

      {/* Execution mode: summary or live progress */}
      {mode !== 'preview' && step.summary && !isActive && (
        <div className="jx-plan-stepSummary">{step.summary}</div>
      )}
      {mode !== 'preview' && isActive && step.text && (
        <div className="jx-plan-stepProgress">
          {step.text.length > 300 ? step.text.slice(-300) : step.text}
        </div>
      )}
    </div>
  );
}

/* ── Progress bar ── */
function ProgressBar({ completed, total }: { completed: number; total: number }) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  return (
    <div className="jx-plan-progress">
      <div className="jx-plan-progressTrack">
        <div className="jx-plan-progressFill" style={{ width: `${pct}%` }} />
      </div>
      <span className="jx-plan-progressLabel">{completed}/{total}</span>
    </div>
  );
}

/* ── Main PlanCard ── */
export function PlanCard({ mode, title, description, steps, completedSteps, totalSteps, resultText, isStreaming, agentNameMap, previewFooter, className, defaultExpandSteps }: PlanCardProps) {
  const [stepsCollapsed, setStepsCollapsed] = useState(false);
  const completed = completedSteps ?? steps.filter(s => s.status === 'success').length;
  const total = totalSteps ?? steps.length;
  const isComplete = mode === 'complete';
  const isExecuting = mode === 'executing';
  // In complete mode with resultText, default to collapsed steps
  const showSteps = isComplete && resultText ? !stepsCollapsed : true;

  return (
    <div className={`jx-plan-card ${isComplete ? 'jx-plan-card--complete' : ''} ${isExecuting ? 'jx-plan-card--executing' : ''} ${className ?? ''}`}>
      {/* Header */}
      <div className="jx-plan-header">
        <div className="jx-plan-headerIcon">
          {isComplete ? (
            <CheckCircleFilled style={{ fontSize: 18, color: 'var(--color-success)' }} />
          ) : isExecuting ? (
            <ThunderboltOutlined style={{ fontSize: 18, color: 'var(--color-primary)' }} />
          ) : (
            <OrderedListOutlined style={{ fontSize: 18, color: 'var(--color-primary)' }} />
          )}
        </div>
        <div className="jx-plan-headerText">
          <h3 className="jx-plan-title">{title}</h3>
          {description && <p className="jx-plan-desc">{description}</p>}
        </div>
        {(isExecuting || isComplete) && (
          <div className="jx-plan-headerBadge">
            <span className={`jx-plan-badge ${isComplete ? 'jx-plan-badge--done' : 'jx-plan-badge--running'}`}>
              {isComplete ? '已完成' : '执行中'}
            </span>
          </div>
        )}
      </div>

      {/* Progress bar (execution & complete) */}
      {(isExecuting || isComplete) && <ProgressBar completed={completed} total={total} />}

      {/* Steps toggle (complete mode with result) */}
      {isComplete && resultText && (
        <div className="jx-plan-stepsToggle" onClick={() => setStepsCollapsed(!stepsCollapsed)}>
          <span>{stepsCollapsed ? '展开步骤详情' : '收起步骤详情'}</span>
          {stepsCollapsed ? <RightOutlined style={{ fontSize: 10 }} /> : <DownOutlined style={{ fontSize: 10 }} />}
        </div>
      )}

      {/* Steps list */}
      {showSteps && (
        <div className="jx-plan-steps">
          <div className="jx-plan-stepsLabel">
            {mode === 'preview' ? `执行计划（共 ${steps.length} 步）` : `步骤进度`}
          </div>
          {steps.map((step, idx) => (
            <PlanStepRow
              key={step.step_order ?? idx}
              step={step}
              index={idx}
              mode={mode}
              agentNameMap={agentNameMap}
              defaultExpanded={mode === 'preview' && defaultExpandSteps}
            />
          ))}
        </div>
      )}

      {/* Execution timeline connector */}
      {isExecuting && isStreaming && (
        <div className="jx-plan-streamingHint">
          <LoadingOutlined spin style={{ fontSize: 12 }} />
          <span>正在执行中...</span>
        </div>
      )}

      {/* Footer */}
      {mode === 'preview' && previewFooter !== null && (
        <div className="jx-plan-footer">
          {previewFooter ?? (
            <div className="jx-plan-footerTip">
              回复 <strong>"确认执行"</strong> 开始执行，或回复 <strong>"重新计划 + 您的建议"</strong> 让系统根据建议重新制定方案。
            </div>
          )}
        </div>
      )}

      {isComplete && (
        <div className="jx-plan-completeFooter">
          <CheckCircleFilled style={{ color: 'var(--color-success)', fontSize: 13 }} />
          <span>执行完成：共 {total} 步，完成 {completed} 步</span>
        </div>
      )}
    </div>
  );
}
