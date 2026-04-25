import { useCallback, useEffect, useState } from 'react';
import { useCatalogStore } from '../../stores';
import { SkillsPage } from './SkillsPage';
import { McpPage } from './McpPage';

type AbilityTabKey = 'skills' | 'mcp';

export function AbilityCenterPage() {
  const { panel, panelEntryNonce, setManageQuery } = useCatalogStore();
  const [activeTab, setActiveTab] = useState<AbilityTabKey>('skills');
  const [hasDetail, setHasDetail] = useState(false);

  useEffect(() => {
    if (panel !== 'ability_center') return;
    setActiveTab('skills');
    setHasDetail(false);
    setManageQuery('');
  }, [panel, panelEntryNonce, setManageQuery]);

  const switchTab = (nextTab: AbilityTabKey) => {
    setActiveTab(nextTab);
    setHasDetail(false);
    setManageQuery('');
  };

  const handleDetailChange = useCallback((detail: boolean) => {
    setHasDetail(detail);
  }, []);

  return (
    <div className={`jx-abilityCenter${hasDetail ? ' jx-abilityCenter--detail' : ''}`}>
      {!hasDetail && (
        <div className="jx-abilityCenterHeader">
          <div className="jx-abilityCenterHero">
            <div className="jx-abilityCenterTabs" role="tablist" aria-label="能力中心分类">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'skills'}
                className={`jx-abilityCenterTab${activeTab === 'skills' ? ' active' : ''}`}
                onClick={() => switchTab('skills')}
              >
                <span className="jx-abilityCenterTabLabel">技能库</span>
              </button>
              <span className="jx-abilityCenterDivider" aria-hidden="true" />
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === 'mcp'}
                className={`jx-abilityCenterTab${activeTab === 'mcp' ? ' active' : ''}`}
                onClick={() => switchTab('mcp')}
              >
                <span className="jx-abilityCenterTabLabel">MCP工具库</span>
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="jx-abilityCenterBody">
        <div
          className={`jx-abilityCenterPane${activeTab === 'skills' ? ' active' : ''}`}
          aria-hidden={activeTab !== 'skills'}
        >
          <SkillsPage embedded onDetailChange={handleDetailChange} />
        </div>
        <div
          className={`jx-abilityCenterPane${activeTab === 'mcp' ? ' active' : ''}`}
          aria-hidden={activeTab !== 'mcp'}
        >
          <McpPage embedded onDetailChange={handleDetailChange} />
        </div>
      </div>
    </div>
  );
}
