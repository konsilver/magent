import { useCallback, useEffect, useMemo, useState } from 'react';
import { Switch, Tag, Input, Typography } from 'antd';
import { SearchOutlined, LeftOutlined } from '@ant-design/icons';
import { useCatalogStore } from '../../stores';
import type { PanelKey } from '../../types';
import { isCatalogKind } from '../../utils/constants';
import { mdToHtml, parseFrontmatter } from '../../utils/markdown';

const SKILLS_DETAIL_ID_STORAGE_KEY = 'jingxin_skills_detail_id';
const SKILLS_DETAIL_KIND_STORAGE_KEY = 'jingxin_skills_detail_kind';

function loadSkillsDetailState(): { id: string | null; kind: 'skills' | 'agents' } {
  if (typeof window === 'undefined') {
    return { id: null, kind: 'skills' };
  }
  const id = window.localStorage.getItem(SKILLS_DETAIL_ID_STORAGE_KEY);
  const rawKind = window.localStorage.getItem(SKILLS_DETAIL_KIND_STORAGE_KEY);
  return {
    id: id || null,
    kind: rawKind === 'agents' ? 'agents' : 'skills',
  };
}

function saveSkillsDetailState(id: string | null, kind: 'skills' | 'agents') {
  if (typeof window === 'undefined') return;
  if (!id) {
    window.localStorage.removeItem(SKILLS_DETAIL_ID_STORAGE_KEY);
    window.localStorage.removeItem(SKILLS_DETAIL_KIND_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(SKILLS_DETAIL_ID_STORAGE_KEY, id);
  window.localStorage.setItem(SKILLS_DETAIL_KIND_STORAGE_KEY, kind);
}

export function SkillsPage({ embedded = false, onDetailChange }: { embedded?: boolean; onDetailChange?: (hasDetail: boolean) => void }) {
  const {
    catalog,
    panel,
    panelEntryNonce,
    manageQuery, setManageQuery,
    toggleItem,
  } = useCatalogStore();

  const initialDetailState = embedded ? { id: null, kind: 'skills' as const } : loadSkillsDetailState();
  const [selectedId, setSelectedId] = useState<string | null>(initialDetailState.id);
  const [selectedKind, setSelectedKind] = useState<'skills' | 'agents'>(initialDetailState.kind);
  const [searchVisible, setSearchVisible] = useState(false);

  const query = manageQuery.trim().toLowerCase();

  const filteredSkills = useMemo(() => {
    const arr = catalog.skills;
    return query ? arr.filter((x) => `${x.id} ${x.name} ${x.desc} ${(x.tags || []).join(' ')}`.toLowerCase().includes(query)) : arr;
  }, [catalog.skills, query]);

  const filteredAgents = useMemo(() => {
    const arr = catalog.agents;
    return query ? arr.filter((x) => `${x.id} ${x.name} ${x.desc} ${(x.tags || []).join(' ')}`.toLowerCase().includes(query)) : arr;
  }, [catalog.agents, query]);
  const totalSkillsCount = catalog.skills.length;

  const selectedItem = useMemo(() => {
    if (!selectedId) return null;
    const arr = selectedKind === 'skills' ? catalog.skills : catalog.agents;
    return arr.find((x) => x.id === selectedId) || null;
  }, [selectedId, selectedKind, catalog]);

  useEffect(() => {
    if (embedded) return;
    saveSkillsDetailState(selectedId, selectedKind);
  }, [embedded, selectedId, selectedKind]);

  useEffect(() => {
    if (!selectedId) return;
    if (selectedItem) return;
    setSelectedId(null);
    setSelectedKind('skills');
  }, [selectedId, selectedItem]);

  useEffect(() => {
    if (!embedded) return;
    setSelectedId(null);
    setSelectedKind('skills');
    setSearchVisible(false);
  }, [embedded]);

  useEffect(() => {
    if (panel !== 'skills') return;
    setSelectedId(null);
    setSelectedKind('skills');
    setSearchVisible(false);
  }, [panel, panelEntryNonce]);

  const toggleEnabled = (kind: PanelKey, id: string, enabled: boolean) => {
    if (!isCatalogKind(kind)) return;
    void toggleItem(kind as 'skills' | 'agents' | 'mcp' | 'kb', id, enabled);
  };

  // Notify parent of detail state changes (covers all code paths including useEffect resets)
  useEffect(() => {
    onDetailChange?.(!!selectedId);
  }, [selectedId, onDetailChange]);

  const openDetail = useCallback((id: string, kind: 'skills' | 'agents') => {
    setSelectedId(id);
    setSelectedKind(kind);
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedId(null);
  }, []);

  // ── Detail View ──────────────────────────────────────────────
  if (selectedItem) {
    const resolvedDetail = (selectedItem as any).detail || '';
    const version = (selectedItem as any).version || '';
    const tags = selectedItem.tags || [];

    // Parse frontmatter from detail markdown if present
    let frontmatter: Record<string, string> = {};
    let markdownBody = resolvedDetail;
    if (resolvedDetail && selectedKind === 'skills') {
      const parsed = parseFrontmatter(resolvedDetail);
      frontmatter = parsed.frontmatter;
      markdownBody = parsed.body;
    }

    // Metadata: prefer frontmatter display_name/description, fallback to item fields
    const metaName = frontmatter.display_name || selectedItem.name;
    const metaDesc = frontmatter.description || selectedItem.desc;
    const metaTags = frontmatter.tags
      ? frontmatter.tags.split(',').map((t: string) => t.trim()).filter(Boolean)
      : tags;

    return (
      <div className="jx-sk-detailPage">
        {/* Sticky header: back + name + tag + toggle */}
        <div className="jx-sk-stickyHeader">
          <button className="jx-sk-backBtn jx-sk-backBtn--inline" onClick={closeDetail}>
            <LeftOutlined style={{ fontSize: 14 }} />
          </button>
          <span className="jx-sk-detailName">{selectedItem.name}</span>
          <Tag className="jx-sk-tag" color={selectedItem.enabled ? 'blue' : 'default'}>
            {selectedItem.enabled ? '已启用' : '未启用'}
          </Tag>
          {version && <span className="jx-sk-version" style={{ marginLeft: 4, marginTop: 0, marginBottom: 0 }}>v{version}</span>}
          <div style={{ flex: 1 }} />
          <span className="jx-sk-enableLabel">启用</span>
          <Switch
            checked={!!selectedItem.enabled}
            onChange={(v) => toggleEnabled(selectedKind, selectedItem.id, v)}
          />
        </div>

        {/* Scrollable body */}
        <div className="jx-sk-stickyBody">

          {/* Metadata card */}
          <div className="jx-sk-metaCard">
            <h4 className="jx-sk-metaName">{metaName}</h4>
            <p className="jx-sk-metaDesc">{metaDesc}</p>
            {metaTags.length > 0 && (
              <div className="jx-sk-metaTags">
                {metaTags.map((tag: string, i: number) => (
                  <Tag key={i} className="jx-sk-metaTag">{tag}</Tag>
                ))}
              </div>
            )}
          </div>

          {/* Body: markdown content */}
          <div className="jx-sk-detailBody">
            {markdownBody ? (
              <div className="jx-md jx-sk-detailMarkdown" dangerouslySetInnerHTML={{ __html: mdToHtml(markdownBody) }} />
            ) : (
              <Typography.Text type="secondary">暂无详情</Typography.Text>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── List View ────────────────────────────────────────────────
  return (
    <div className="jx-sk-page">
      {/* Header */}
      <div className="jx-sk-header">
        <div>
          <h2 className="jx-sk-title">
            技能库
            <span className="jx-sectionTitleCount">（共 {totalSkillsCount} 项）</span>
          </h2>
          <p className="jx-sk-subtitle">启用/停用技能，并查看详细介绍、输入输出与示例</p>
        </div>
        <div className="jx-sk-headerRight">
          {searchVisible ? (
            <Input
              allowClear
              placeholder="搜索技能关键词"
              className="jx-mcp-searchInput"
              value={manageQuery}
              onChange={(e) => setManageQuery(e.target.value)}
              autoFocus
              onBlur={() => { if (!manageQuery) setSearchVisible(false); }}
            />
          ) : (
            <div className="jx-mcp-searchBox" onClick={() => setSearchVisible(true)}>
              <SearchOutlined style={{ color: '#B3B3B3', fontSize: 14 }} />
              <span className="jx-mcp-searchPlaceholder">搜索技能关键词</span>
            </div>
          )}
        </div>
      </div>

      {/* Section 1: 技能 */}
      <div className="jx-sk-list">
        {filteredSkills.map((item) => (
          <div
            key={item.id}
            className="jx-sk-item"
            onClick={() => openDetail(item.id, 'skills')}
          >
            <div className="jx-sk-itemNameRow">
              <span className="jx-sk-itemName">{item.name}</span>
              {item.enabled && (
                <Tag className="jx-sk-tag" color="blue">已启用</Tag>
              )}
            </div>
            <div className="jx-sk-itemDesc">{item.desc}</div>
          </div>
        ))}
      </div>

      {/* Section 2: 智能体 */}
      {filteredAgents.length > 0 && (
        <div className="jx-sk-list">
          {filteredAgents.map((item) => (
            <div
              key={item.id}
              className="jx-sk-item"
              onClick={() => openDetail(item.id, 'agents')}
            >
              <div className="jx-sk-itemNameRow">
                <span className="jx-sk-itemName">{item.name}</span>
                {item.enabled && (
                  <Tag className="jx-sk-tag" color="blue">已启用</Tag>
                )}
              </div>
              <div className="jx-sk-itemDesc">{item.desc}</div>
            </div>
          ))}
        </div>
      )}

      {filteredSkills.length === 0 && filteredAgents.length === 0 && (
        <div style={{ padding: '40px 0', textAlign: 'center' }}>
          <Typography.Text type="secondary">没有匹配的技能</Typography.Text>
        </div>
      )}
    </div>
  );
}
