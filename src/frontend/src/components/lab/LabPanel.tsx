import { useEffect, useState } from 'react';
import { useChatStore, useCatalogStore, useAutomationStore } from '../../stores';
import { nowId } from '../../storage';
import { AutomationPanel } from './AutomationPanel';

const LAB_ITEMS = [
  {
    id: 'code_exec',
    name: '代码执行',
    icon: '/home/新增icon/代码执行-有色.svg',
    description: '让 AI 在安全沙箱中编写并运行代码，支持 Python、JavaScript、Bash，适用于数据分析、脚本调试、算法验证等场景',
    enabled: true,
  },
  {
    id: 'automation',
    name: '自动化',
    icon: '/home/新增icon/自动化-有色.svg',
    description: '设置定时或周期性 AI 任务，支持自然语言提示词和计划模式的自动执行，适用于定期报告、数据监控等场景',
    enabled: true,
  },
  {
    id: 'coming_soon',
    name: '更多实验',
    icon: '/home/新增icon/更多.svg',
    description: '更多实验性 AI 能力将陆续开放',
    enabled: false,
  },
];

export default function LabPanel() {
  const { setCurrentChatId, setToolResultPanel, updateStore, setCodeExecMode } = useChatStore();
  const { setPanel } = useCatalogStore();
  const selectedTaskId = useAutomationStore((s) => s.selectedTaskId);
  // 若打开 LabPanel 时已有选中的自动化任务（例如从运行舱面包屑跳转过来），
  // 直接进入自动化子面板，避免出现实验室首页的中间态。
  const [subPanel, setSubPanel] = useState<string | null>(
    selectedTaskId ? 'automation' : null,
  );

  // 在 LabPanel 已挂载的情况下，外部又设置了 selectedTaskId，
  // 同样自动切换到自动化子面板。
  useEffect(() => {
    if (selectedTaskId) setSubPanel('automation');
  }, [selectedTaskId]);

  const handleClick = (id: string) => {
    if (id === 'code_exec') {
      const newChatId = nowId('chat');
      setCurrentChatId(newChatId);
      updateStore((prev) => ({
        ...prev,
        chats: {
          ...prev.chats,
          [newChatId]: {
            id: newChatId,
            title: '代码执行',
            createdAt: Date.now(),
            updatedAt: Date.now(),
            messages: [],
            favorite: false,
            pinned: false,
            businessTopic: '综合咨询',
            codeExecChat: true,
          },
        },
        order: [newChatId, ...prev.order.filter((oid) => oid !== newChatId)],
      }));
      setCodeExecMode(true);
      setToolResultPanel(null);
      setPanel('chat');
    } else if (id === 'automation') {
      setSubPanel('automation');
    }
  };

  if (subPanel === 'automation') {
    return <AutomationPanel onBack={() => setSubPanel(null)} />;
  }

  return (
    <div className="jx-agentPage">
      <div className="jx-agentPage-header">
        <div>
          <div className="jx-agentPage-title">实验室</div>
          <div className="jx-agentPage-subtitle">AI 能力实验性应用</div>
        </div>
      </div>

      <div className="jx-agentPage-grid">
        {LAB_ITEMS.map((app) => (
          <div
            key={app.id}
            className={`jx-agentCard${!app.enabled ? ' jx-agentCard--disabled' : ''}`}
            onClick={() => app.enabled && handleClick(app.id)}
            role={app.enabled ? 'button' : undefined}
            tabIndex={app.enabled ? 0 : undefined}
          >
            <div className="jx-agentCard-body">
              <div className="jx-agentCard-head">
                <img
                  src={app.icon}
                  alt=""
                  width={28}
                  height={28}
                  style={{ borderRadius: '50%', objectFit: 'cover', display: 'block' }}
                />
                <div className="jx-agentCard-nameRow">
                  <span className="jx-agentCard-name">{app.name}</span>
                  <span className={`jx-agentCard-badge${app.enabled ? ' jx-agentCard-badge--enabled' : ''}`}>
                    {app.enabled ? '可用' : '即将上线'}
                  </span>
                </div>
              </div>
              <div className="jx-agentCard-desc">{app.description}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
