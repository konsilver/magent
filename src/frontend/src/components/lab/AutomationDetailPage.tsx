import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  Popconfirm,
  Spin,
  Switch,
  message,
} from 'antd';
import { LeftOutlined, DeleteOutlined, EditOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { AutomationRun, AutomationScheduleType, AutomationTask } from '../../types';
import { getAutomation, getAutomationRuns, activateAutomationSidebar } from '../../api';
import { useAutomationStore, useAutomationChatStore } from '../../stores';
import { RUN_STATUS_LABEL, cronToHumanReadable, formatRelativeTime } from './automationUtils';
import { ScheduleSelector, type ScheduleValue } from './ScheduleSelector';

interface Props {
  taskId: string;
  onBack: () => void;
}

const STATUS_LABEL: Record<string, string> = {
  active: '运行中',
  paused: '已暂停',
  disabled: '已停用',
  completed: '已完成',
  expired: '已过期',
};

const RUN_STATUS_CLASS: Record<string, string> = {
  running: 'is-running',
  success: 'is-success',
  failed: 'is-failed',
};

const SCHEDULE_TYPE_LABEL: Record<AutomationScheduleType, string> = {
  recurring: '周期执行',
  once: '单次执行',
  manual: '手动执行',
};

function fmtDateTime(iso?: string): string {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function fmtDateOnly(iso?: string): string {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleDateString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).replace(/-/g, '/');
  } catch {
    return iso;
  }
}

interface EditFormValues {
  name?: string;
  description?: string;
  prompt?: string;
}

export function AutomationDetailPage({ taskId, onBack }: Props) {
  const [task, setTask] = useState<AutomationTask | null>(null);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<EditFormValues>();
  const [editSchedule, setEditSchedule] = useState<ScheduleValue>({
    schedule_type: 'recurring',
    cron_expression: '0 9 * * *',
  });

  const { removeTask, togglePause, triggerNow, updateTask } = useAutomationStore();
  const { enterAutomationChat } = useAutomationChatStore();

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    try {
      const [t, r] = await Promise.all([getAutomation(taskId), getAutomationRuns(taskId, 20)]);
      setTask(t);
      setRuns(r);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  // ─── Derived ───
  const scheduleType: AutomationScheduleType = useMemo(() => {
    if (!task) return 'recurring';
    return task.schedule_type || (task.recurring ? 'recurring' : 'once');
  }, [task]);

  const canTrigger = useMemo(() => {
    if (!task) return false;
    return task.status === 'active' || task.status === 'paused';
  }, [task]);

  // Switch 只在"周期/单次" + active/paused 时显示；manual 任务不靠 Switch 控制
  const canToggleRun = useMemo(() => {
    if (!task) return false;
    if (scheduleType === 'manual') return false;
    return task.status === 'active' || task.status === 'paused';
  }, [task, scheduleType]);

  const displayName = useMemo(() => {
    if (!task) return '';
    return (
      task.name ||
      (task.task_type === 'prompt'
        ? task.prompt?.slice(0, 40) || '提示词任务'
        : task.plan_title || '计划任务')
    );
  }, [task]);

  // ─── Handlers (view mode) ───
  const handleToggleRun = async (checked: boolean) => {
    if (!task || !canToggleRun) return;
    try {
      await togglePause(task);
      message.success(checked ? '已恢复运行' : '已暂停');
      const updated = await getAutomation(task.task_id);
      setTask(updated);
    } catch {
      message.error('操作失败');
    }
  };

  const handleTrigger = async () => {
    if (!task) return;
    try {
      await triggerNow(task.task_id);
      message.success('已触发执行');
    } catch {
      message.error('触发失败');
    }
  };

  const handleDelete = async () => {
    if (!task) return;
    try {
      await removeTask(task.task_id);
      message.success('已删除');
      onBack();
    } catch {
      message.error('删除失败');
    }
  };

  const navigateToChat = (run: AutomationRun) => {
    if (!task) return;
    // Activate sidebar on first click (idempotent)
    if (!task.sidebar_activated) {
      activateAutomationSidebar(task.task_id).catch(() => {});
      setTask((prev) => prev ? { ...prev, sidebar_activated: true } : prev);
    }
    // Enter automation chat mode with timeline panel
    const taskName = task.name || task.prompt?.slice(0, 30) || '自动化任务';
    enterAutomationChat(task.task_id, taskName, runs, run.run_id);
  };

  // ─── Edit mode ───
  const enterEdit = () => {
    if (!task) return;
    form.setFieldsValue({
      name: task.name || '',
      description: task.description || '',
      prompt: task.prompt || '',
    });
    setEditSchedule({
      schedule_type: scheduleType,
      cron_expression: task.cron_expression,
    });
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    form.resetFields();
  };

  const handleSave = async () => {
    if (!task) return;
    try {
      const values = await form.validateFields();
      setSaving(true);

      const payload = {
        name: values.name?.trim() || undefined,
        description: values.description?.trim() || undefined,
        prompt: task.task_type === 'prompt' ? values.prompt?.trim() : undefined,
        cron_expression: editSchedule.cron_expression,
        recurring: editSchedule.schedule_type === 'recurring',
        schedule_type: editSchedule.schedule_type,
      };

      const updated = await updateTask(task.task_id, payload);
      setTask(updated);
      setIsEditing(false);
      message.success('已保存');
    } catch (e) {
      const errMsg =
        (e as { errorFields?: unknown[]; message?: string })?.errorFields
          ? '请检查表单填写'
          : (e as Error)?.message || '保存失败';
      message.error(errMsg);
    } finally {
      setSaving(false);
    }
  };

  // ─── Render ───
  if (loading && !task) {
    return (
      <div className="jx-agentPage">
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Spin />
        </div>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="jx-agentPage">
        <div className="jx-automation-detail-top">
          <button className="jx-automation-detail-backBtn" onClick={onBack} aria-label="返回">
            <LeftOutlined />
          </button>
          <div className="jx-automation-detail-content">
            <div style={{ color: '#9CA3AF', marginTop: 40 }}>任务不存在或已被删除。</div>
          </div>
        </div>
      </div>
    );
  }

  const statusLabel = STATUS_LABEL[task.status] || task.status;
  const badgeClass = `jx-automation-detail-badge is-${task.status}`;
  const failedRunWithChat = runs.find((r) => r.status === 'failed' && r.chat_id);

  const isManual = scheduleType === 'manual';
  const isOnce = scheduleType === 'once';

  return (
    <div className="jx-agentPage">
      <div className="jx-automation-detail-top">
        <button className="jx-automation-detail-backBtn" onClick={onBack} aria-label="返回">
          <LeftOutlined />
        </button>

        <div className="jx-automation-detail-content">
          {isEditing && (
            <div className="jx-automation-detail-editBar">
              <span className="jx-automation-detail-editBar-text">编辑中 · 修改后请保存</span>
              <div className="jx-automation-detail-editBar-actions">
                <Button onClick={cancelEdit} disabled={saving}>取消</Button>
                <Button type="primary" onClick={handleSave} loading={saving}>保存</Button>
              </div>
            </div>
          )}

          {/* ── Name row ── */}
          <div className="jx-automation-detail-nameRow">
            <div className="jx-automation-detail-iconWrap">
              <img src="/home/新增icon/自动化-有色.svg" alt="自动化" />
            </div>
            {isEditing ? (
              <Form form={form} component={false}>
                <Form.Item name="name" style={{ marginBottom: 0, flex: 1 }}>
                  <Input
                    placeholder="任务名称（可选）"
                    maxLength={200}
                    style={{ fontSize: 16, fontWeight: 500 }}
                  />
                </Form.Item>
              </Form>
            ) : (
              <span className="jx-automation-detail-name" title={displayName}>
                {displayName}
              </span>
            )}
            {!isEditing && <span className={badgeClass}>{statusLabel}</span>}
            {!isEditing && canToggleRun && (
              <div className="jx-automation-detail-runSwitch">
                <span className="jx-automation-detail-runSwitch-label">
                  {task.status === 'active' ? '运行中' : '已暂停'}
                </span>
                <Switch
                  checked={task.status === 'active'}
                  onChange={handleToggleRun}
                />
              </div>
            )}
          </div>

          {/* ── Meta row (view only) ── */}
          {!isEditing && (
            <div className="jx-automation-detail-metaRow">
              <span>{SCHEDULE_TYPE_LABEL[scheduleType]}</span>
              <span className="jx-automation-detail-metaRow-sep">·</span>
              <span>
                下次执行：
                {isManual
                  ? '仅手动触发'
                  : task.next_run_at
                  ? formatRelativeTime(task.next_run_at)
                  : '-'}
              </span>
              <span className="jx-automation-detail-metaRow-sep">·</span>
              <span>累计执行 {task.run_count} 次</span>
              <span className="jx-automation-detail-metaRow-sep">·</span>
              <span>创建于 {fmtDateOnly(task.created_at)}</span>
            </div>
          )}

          {/* ── Error alert ── */}
          {!isEditing && task.last_error && (
            <Alert
              className="jx-automation-detail-errorAlert"
              type="error"
              showIcon
              message={
                <span>
                  最近一次执行失败：{task.last_error}
                  {failedRunWithChat && (
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: '0 6px' }}
                      onClick={() => failedRunWithChat.chat_id && navigateToChat(failedRunWithChat)}
                    >
                      查看详情
                    </Button>
                  )}
                </span>
              }
            />
          )}

          <hr className="jx-automation-detail-divider" />

          {/* ── Sections ── */}
          <Form form={form} component={false}>
            <div className="jx-automation-detail-sections">
              {/* 任务内容 */}
              <section className="jx-automation-detail-section">
                <div className="jx-automation-detail-sectionHead">
                  <h3 className="jx-automation-detail-sectionTitle">任务内容</h3>
                </div>
                <div className="jx-automation-detail-grid">
                  <div className="jx-automation-detail-field">
                    <div className="jx-automation-detail-fieldLabel">任务类型</div>
                    <div className="jx-automation-detail-fieldValue">
                      {task.task_type === 'prompt' ? '提示词' : '执行计划'}
                      {isEditing && (
                        <span className="jx-automation-detail-fieldValue is-muted" style={{ fontSize: 12, marginLeft: 8 }}>
                          (不可修改)
                        </span>
                      )}
                    </div>
                  </div>

                  {task.task_type === 'prompt' ? (
                    <div className="jx-automation-detail-field is-multiline">
                      <div className="jx-automation-detail-fieldLabel">提示词</div>
                      {isEditing ? (
                        <Form.Item
                          name="prompt"
                          style={{ marginBottom: 0 }}
                          rules={[{ required: true, message: '请输入提示词' }]}
                        >
                          <Input.TextArea rows={5} maxLength={5000} showCount />
                        </Form.Item>
                      ) : (
                        <div className="jx-automation-detail-fieldValue">
                          {task.prompt || <span className="is-muted">（空）</span>}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="jx-automation-detail-field is-multiline">
                      <div className="jx-automation-detail-fieldLabel">关联计划</div>
                      <div className="jx-automation-detail-fieldValue">
                        {task.plan_title || task.plan_id || <span className="is-muted">（未绑定）</span>}
                      </div>
                    </div>
                  )}

                  <div className="jx-automation-detail-field is-multiline">
                    <div className="jx-automation-detail-fieldLabel">描述</div>
                    {isEditing ? (
                      <Form.Item name="description" style={{ marginBottom: 0 }}>
                        <Input.TextArea rows={2} maxLength={500} placeholder="任务描述（可选）" />
                      </Form.Item>
                    ) : (
                      <div className="jx-automation-detail-fieldValue">
                        {task.description || <span className="is-muted">（未填写）</span>}
                      </div>
                    )}
                  </div>
                </div>
              </section>

              {/* 调度设定 */}
              <section className="jx-automation-detail-section">
                <div className="jx-automation-detail-sectionHead">
                  <h3 className="jx-automation-detail-sectionTitle">调度设定</h3>
                </div>
                {isEditing ? (
                  <div className="jx-automation-detail-field is-multiline">
                    <div className="jx-automation-detail-fieldLabel">调度方式</div>
                    <ScheduleSelector value={editSchedule} onChange={setEditSchedule} />
                  </div>
                ) : (
                  <div className="jx-automation-detail-grid">
                    <div className="jx-automation-detail-field">
                      <div className="jx-automation-detail-fieldLabel">调度方式</div>
                      <div className="jx-automation-detail-fieldValue">
                        {SCHEDULE_TYPE_LABEL[scheduleType]}
                      </div>
                    </div>
                    <div className="jx-automation-detail-field">
                      <div className="jx-automation-detail-fieldLabel">时区</div>
                      <div className="jx-automation-detail-fieldValue">
                        {task.timezone || 'Asia/Shanghai'}
                      </div>
                    </div>
                    {!isManual && (
                      <div className="jx-automation-detail-field is-multiline">
                        <div className="jx-automation-detail-fieldLabel">
                          {isOnce ? '执行时间' : '执行频率'}
                        </div>
                        <div className="jx-automation-detail-fieldValue">
                          {isOnce
                            ? fmtDateTime(task.next_run_at) === '-'
                              ? '已执行'
                              : fmtDateTime(task.next_run_at)
                            : cronToHumanReadable(task.cron_expression)}
                          <span style={{ color: '#7B8794', marginLeft: 10, fontSize: 12 }}>
                            ({task.cron_expression})
                          </span>
                        </div>
                      </div>
                    )}
                    {isManual && (
                      <div className="jx-automation-detail-field is-multiline">
                        <div className="jx-automation-detail-fieldLabel">说明</div>
                        <div className="jx-automation-detail-fieldValue is-muted">
                          该任务仅在您点击"立即执行"时运行，不会自动触发。
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </section>

              {/* 执行记录 */}
              {!isEditing && (
                <section className="jx-automation-detail-section">
                  <div className="jx-automation-detail-sectionHead">
                    <h3 className="jx-automation-detail-sectionTitle">执行记录</h3>
                    <span style={{ fontSize: 12, color: '#7B8794' }}>
                      {runs.length > 0 ? `最近 ${runs.length} 次` : ''}
                    </span>
                  </div>
                  {runs.length === 0 ? (
                    <div className="jx-automation-detail-runEmpty">暂无执行记录</div>
                  ) : (
                    <div className="jx-automation-detail-runList">
                      {runs.map((run) => (
                        <div key={run.run_id} className="jx-automation-detail-runRow">
                          <span
                            className={`jx-automation-detail-runRow-dot ${RUN_STATUS_CLASS[run.status] || 'is-failed'}`}
                            title={RUN_STATUS_LABEL[run.status] || run.status}
                          />
                          <span className="jx-automation-detail-runRow-time">
                            {fmtDateTime(run.started_at)}
                          </span>
                          <span className="jx-automation-detail-runRow-duration">
                            {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : '-'}
                          </span>
                          <span className="jx-automation-detail-runRow-summary">
                            {run.result_summary || RUN_STATUS_LABEL[run.status] || '-'}
                          </span>
                          {run.status !== 'running' && run.chat_id && (
                            <Button
                              type="link"
                              size="small"
                              onClick={() => navigateToChat(run)}
                            >
                              查看对话
                            </Button>
                          )}
                          {run.status === 'failed' && run.error_message && (
                            <div className="jx-automation-detail-runRow-error">
                              {run.error_message}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          </Form>

          {/* ── Action bar (view mode only) ── */}
          {!isEditing && (
            <div className="jx-automation-detail-actionsWrap">
              <div className="jx-automation-detail-actions">
                <Button
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  disabled={!canTrigger}
                  onClick={handleTrigger}
                >
                  立即执行
                </Button>
                <Button icon={<EditOutlined />} onClick={enterEdit}>
                  编辑
                </Button>
                <Popconfirm
                  title="确定删除此自动化任务？"
                  onConfirm={handleDelete}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
