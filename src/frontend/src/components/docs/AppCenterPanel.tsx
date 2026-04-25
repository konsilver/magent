import { useCatalogStore } from '../../stores';

const APP_ITEMS = [
  {
    id: 'coming_soon',
    name: '更多应用',
    icon: '/home/random-icons/Frame 460.svg',
    description: '基于 AI 能力的场景化应用将陆续开放',
    enabled: false,
  },
];

export default function AppCenterPanel() {
  const { setPanel } = useCatalogStore();

  const handleClick = (_id: string) => {
    // placeholder for future app entries
    void setPanel;
  };

  return (
    <div className="jx-agentPage">
      <div className="jx-agentPage-header">
        <div>
          <div className="jx-agentPage-title">应用中心</div>
          <div className="jx-agentPage-subtitle">基于 AI 能力的经信业务场景化智能应用</div>
        </div>
      </div>

      <div className="jx-agentPage-grid">
        {APP_ITEMS.map((app) => (
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
