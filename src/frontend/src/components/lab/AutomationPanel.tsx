import { useEffect } from 'react';
import { Button, Spin, Empty } from 'antd';
import { PlusOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import { useAutomationStore } from '../../stores';
import { AutomationCard } from './AutomationCard';
import { AutomationCreateModal } from './AutomationCreateModal';
import { AutomationDetailPage } from './AutomationDetailPage';
import '../../styles/automation.css';

interface Props {
  onBack: () => void;
}

export function AutomationPanel({ onBack }: Props) {
  const {
    tasks,
    loading,
    createModalOpen,
    selectedTaskId,
    fetchTasks,
    setCreateModalOpen,
    setSelectedTaskId,
  } = useAutomationStore();

  useEffect(() => {
    void fetchTasks();
  }, [fetchTasks]);

  // ── Detail view ──
  if (selectedTaskId) {
    return (
      <AutomationDetailPage
        taskId={selectedTaskId}
        onBack={() => {
          setSelectedTaskId(null);
          void fetchTasks();
        }}
      />
    );
  }

  // ── List view ──
  const activeTasks = tasks.filter((t) => t.status === 'active');
  const pausedTasks = tasks.filter((t) => t.status === 'paused');
  const otherTasks = tasks.filter((t) => !['active', 'paused'].includes(t.status));

  return (
    <div className="jx-agentPage">
      <div className="jx-agentPage-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={onBack}
            style={{ marginRight: 4 }}
          />
          <div>
            <div className="jx-agentPage-title">自动化</div>
            <div className="jx-agentPage-subtitle">设置定时或周期性 AI 任务，到时间后自动执行</div>
          </div>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateModalOpen(true)}
        >
          创建自动化
        </Button>
      </div>

      <div className="jx-automation-body">
        {loading && tasks.length === 0 ? (
          <div className="jx-automation-loading"><Spin /></div>
        ) : tasks.length === 0 ? (
          <Empty
            description="暂无自动化任务"
            style={{ marginTop: 80 }}
          >
            <Button type="primary" onClick={() => setCreateModalOpen(true)}>
              创建第一个自动化任务
            </Button>
          </Empty>
        ) : (
          <>
            {activeTasks.length > 0 && (
              <div className="jx-automation-section">
                <div className="jx-automation-sectionTitle">运行中</div>
                {activeTasks.map((t) => (
                  <AutomationCard key={t.task_id} task={t} onClick={() => setSelectedTaskId(t.task_id)} />
                ))}
              </div>
            )}
            {pausedTasks.length > 0 && (
              <div className="jx-automation-section">
                <div className="jx-automation-sectionTitle">已暂停</div>
                {pausedTasks.map((t) => (
                  <AutomationCard key={t.task_id} task={t} onClick={() => setSelectedTaskId(t.task_id)} />
                ))}
              </div>
            )}
            {otherTasks.length > 0 && (
              <div className="jx-automation-section">
                <div className="jx-automation-sectionTitle">已完成 / 已停用</div>
                {otherTasks.map((t) => (
                  <AutomationCard key={t.task_id} task={t} onClick={() => setSelectedTaskId(t.task_id)} />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <AutomationCreateModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCreated={() => {
          setCreateModalOpen(false);
          void fetchTasks();
        }}
      />
    </div>
  );
}
