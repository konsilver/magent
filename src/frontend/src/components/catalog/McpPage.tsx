import { useCallback, useEffect, useMemo, useState } from 'react';
import { Switch, Tag, Input, Typography } from 'antd';
import { SearchOutlined, LeftOutlined } from '@ant-design/icons';
import { useCatalogStore } from '../../stores';
import { mdToHtml, parseFrontmatter } from '../../utils/markdown';

// Map MCP server ids to icon files
const MCP_ICONS: Record<string, string> = {
  query_database: '/home/mcp/数据库.svg',
  retrieve_dataset_content: '/home/mcp/知识.svg',
  internet_search: '/home/mcp/互联网.svg',
  ai_chain_information_mcp: '/home/mcp/产业链.svg',
  generate_chart_tool: '/home/mcp/数据.svg',
  report_export_mcp: '/home/mcp/报告.svg',
  web_fetch: '/home/mcp/来源.svg',
};

function McpIcon({ id }: { id: string }) {
  const src = MCP_ICONS[id];
  if (src) {
    return (
      <div className="jx-mcp-iconWrap">
        <img src={src} alt="" className="jx-mcp-iconImg" />
      </div>
    );
  }
  // Fallback: colored circle with first char
  return (
    <div className="jx-mcp-iconWrap jx-mcp-iconFallback">
      <span>{(id || '?')[0].toUpperCase()}</span>
    </div>
  );
}

export function McpPage({ embedded = false, onDetailChange }: { embedded?: boolean; onDetailChange?: (hasDetail: boolean) => void }) {
  const {
    catalog,
    panel,
    panelEntryNonce,
    manageQuery, setManageQuery,
    toggleItem,
  } = useCatalogStore();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchVisible, setSearchVisible] = useState(false);

  const query = manageQuery.trim().toLowerCase();

  const filteredItems = useMemo(() => {
    const arr = catalog.mcp;
    return query ? arr.filter((x) => `${x.id} ${x.name} ${x.desc} ${(x.tags || []).join(' ')}`.toLowerCase().includes(query)) : arr;
  }, [catalog.mcp, query]);
  const totalMcpCount = catalog.mcp.length;

  const selectedItem = useMemo(() => {
    if (!selectedId) return null;
    return catalog.mcp.find((x) => x.id === selectedId) || null;
  }, [selectedId, catalog.mcp]);

  const toggleEnabled = (id: string, enabled: boolean) => {
    void toggleItem('mcp', id, enabled);
  };

  // Notify parent of detail state changes (covers all code paths including useEffect resets)
  useEffect(() => {
    onDetailChange?.(!!selectedId);
  }, [selectedId, onDetailChange]);

  const openDetail = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedId(null);
  }, []);

  useEffect(() => {
    if (embedded) return;
    if (panel !== 'mcp') return;
    setSelectedId(null);
    setSearchVisible(false);
  }, [embedded, panel, panelEntryNonce]);

  useEffect(() => {
    if (!embedded) return;
    setSelectedId(null);
    setSearchVisible(false);
  }, [embedded]);

  // ── Detail View ──────────────────────────────────────────────
  if (selectedItem) {
    const version = (selectedItem as any).version || '';
    const resolvedDetail = (selectedItem as any).detail || '';
    const markdownBody = resolvedDetail
      ? parseFrontmatter(resolvedDetail).body
      : '';

    return (
      <div className="jx-mcp-detailPage">
        {/* Sticky header: back + icon + name + tag + toggle */}
        <div className="jx-mcp-stickyHeader">
          <button className="jx-mcp-backBtn jx-mcp-backBtn--inline" onClick={closeDetail}>
            <LeftOutlined style={{ fontSize: 14 }} />
          </button>
          <McpIcon id={selectedItem.id} />
          <span className="jx-mcp-detailName">{selectedItem.name}</span>
          <Tag className="jx-mcp-enabledTag"
            style={selectedItem.enabled
              ? { background: '#DBE9FF', color: '#126DFF', border: 'none' }
              : { background: '#F5F6F7', color: '#B3B3B3', border: 'none' }
            }>
            {selectedItem.enabled ? '已启用' : '未启用'}
          </Tag>
          {version && <span className="jx-mcp-version" style={{ marginLeft: 4 }}>v{version}</span>}
          <div style={{ flex: 1 }} />
          <span className="jx-mcp-enableLabel">启用</span>
          <Switch
            checked={!!selectedItem.enabled}
            onChange={(v) => toggleEnabled(selectedItem.id, v)}
          />
        </div>

        {/* Scrollable body */}
        <div className="jx-mcp-stickyBody">
          {/* MCP explain card */}
          <div className="jx-mcp-explainCard">
            <div className="jx-mcp-explainRow">
              <span className="jx-mcp-explainLabel">这是什么</span>
              <span className="jx-mcp-explainValue">MCP 工具服务，用于接入外部数据源或执行专用能力</span>
            </div>
            <div className="jx-mcp-explainRow">
              <span className="jx-mcp-explainLabel">有什么用</span>
              <span className="jx-mcp-explainValue">可补充模型本身不具备的检索、查询、图表、联网等能力</span>
            </div>
            <div className="jx-mcp-explainRow">
              <span className="jx-mcp-explainLabel">对结果可靠性的影响</span>
              <span className="jx-mcp-explainValue">启用后可提升可验证性和可引用性；关闭后回答更依赖模型与知识库</span>
            </div>
          </div>

          {/* Tool detail body */}
          <div className="jx-mcp-detailBody">
            <h4 className="jx-mcp-bodyTitle" style={{ fontWeight: 700 }}>基本功能</h4>
            <p className="jx-mcp-bodyDesc">{selectedItem.desc}</p>

            {/* Markdown detail content */}
            {markdownBody ? (
              <div className="jx-md jx-mcp-detailMarkdown" dangerouslySetInnerHTML={{ __html: mdToHtml(markdownBody) }} />
            ) : (
              <>
                {selectedItem.desc && !((selectedItem as any).server) && (
                  <p className="jx-mcp-bodyDesc">{selectedItem.desc}</p>
                )}
              </>
            )}

            {/* Tools list */}
            {(selectedItem as any).tools && (selectedItem as any).tools.length > 0 && (
              <>
                <h4 className="jx-mcp-bodyTitle">工具列表</h4>
                <ul className="jx-mcp-toolsList">
                  {((selectedItem as any).tools as string[]).map((t, i) => (
                    <li key={i}>{t}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── List View ────────────────────────────────────────────────
  return (
    <div className="jx-mcp-page">
      {/* Header */}
      <div className="jx-mcp-header">
        <div>
          <h2 className="jx-mcp-title">
            MCP工具库
            <span className="jx-sectionTitleCount">（共 {totalMcpCount} 项）</span>
          </h2>
          <p className="jx-mcp-subtitle">管理 MCP 工具服务，并查看其作用范围与可靠性影响。</p>
        </div>
        <div className="jx-mcp-headerRight">
          {searchVisible ? (
            <Input
              allowClear
              placeholder="搜索工具关键词"
              className="jx-mcp-searchInput"
              value={manageQuery}
              onChange={(e) => setManageQuery(e.target.value)}
              prefix={<SearchOutlined style={{ color: '#B3B3B3' }} />}
              autoFocus
              onBlur={() => { if (!manageQuery) setSearchVisible(false); }}
            />
          ) : (
            <div className="jx-mcp-searchBox" onClick={() => setSearchVisible(true)}>
              <SearchOutlined style={{ color: '#B3B3B3', fontSize: 14 }} />
              <span className="jx-mcp-searchPlaceholder">搜索工具关键词</span>
            </div>
          )}
        </div>
      </div>

      {/* Card grid — 2 columns */}
      <div className="jx-mcp-grid">
        {filteredItems.map((item) => (
          <div
            key={item.id}
            className="jx-mcp-card"
            onClick={() => openDetail(item.id)}
          >
            <div className="jx-mcp-cardTop">
              <McpIcon id={item.id} />
              <div className="jx-mcp-cardNameGroup">
                <span className="jx-mcp-cardName">{item.name}</span>
                <Tag className="jx-mcp-enabledTag"
                  style={item.enabled
                    ? { background: '#DBE9FF', color: '#126DFF', border: 'none' }
                    : { background: '#F5F6F7', color: '#B3B3B3', border: 'none' }
                  }>
                  {item.enabled ? '已启用' : '未启用'}
                </Tag>
              </div>
            </div>
            <div className="jx-mcp-cardDesc">{item.desc}</div>
          </div>
        ))}
      </div>

      {filteredItems.length === 0 && (
        <div style={{ padding: '40px 0', textAlign: 'center' }}>
          <Typography.Text type="secondary">没有匹配的工具</Typography.Text>
        </div>
      )}
    </div>
  );
}
