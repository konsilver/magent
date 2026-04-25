import { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Input, Modal, Skeleton, Switch, Tooltip, message } from 'antd';
import { PlusOutlined, SearchOutlined, DeleteOutlined, EditOutlined, LeftOutlined } from '@ant-design/icons';
import { useAgentStore, type UserAgentItem } from '../../stores/agentStore';
import { useCatalogStore } from '../../stores/catalogStore';
import { useChatStore } from '../../stores/chatStore';
import { nowId } from '../../storage';
import { formatDateTime } from '../../utils/date';
import { mdToHtml } from '../../utils/markdown';
import { AgentCreatePage } from './AgentCreatePage';

const AGENT_ICON_MAP: Record<string, string> = {
  '报告生成子智能体': '/home/agent-icons/报告.svg',
  '知识检索子智能体': '/home/agent-icons/知识.svg',
  '报告撰写': '/home/agent-icons/报告撰写.svg',
  '知识检索': '/home/agent-icons/知识检索.svg',
  '智能问答': '/home/agent-icons/智能问答.svg',
  '数据分析': '/home/agent-icons/数据分析.svg',
  '政策解读': '/home/agent-icons/政策解读.svg',
  '信息提取': '/home/agent-icons/信息提取.svg',
  '企业画像': '/home/agent-icons/企业画像.svg',
  '产业链分析': '/home/agent-icons/产业链分析.svg',
  '材料分析': '/home/agent-icons/材料分析.svg',
  '流程指引': '/home/agent-icons/流程指引.svg',
};

const RANDOM_ICONS = [
  'Frame 442.svg', 'Frame 443.svg', 'Frame 444.svg', 'Frame 445.svg',
  'Frame 446.svg', 'Frame 447.svg', 'Frame 448.svg', 'Frame 449.svg',
  'Frame 450.svg', 'Frame 451.svg', 'Frame 452.svg', 'Frame 453.svg',
  'Frame 454.svg', 'Frame 455.svg', 'Frame 456.svg', 'Frame 457.svg',
  'Frame 458.svg', 'Frame 459.svg', 'Frame 460.svg', 'Frame 461.svg',
  'Frame 462.svg', 'Frame 463.svg', 'Frame 464.svg', 'Frame 465.svg',
  'Frame 466.svg', 'Frame 467.svg', 'Frame 468.svg', 'Frame 469.svg',
  'Frame 470.svg', 'Frame 471.svg', 'Frame 472.svg',
];

/** Deterministic hash from string → index into RANDOM_ICONS */
function hashToIconIndex(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % RANDOM_ICONS.length;
}

/** Get a random icon URL based on agent id or name */
export function getRandomIconUrl(key: string): string {
  const fileName = RANDOM_ICONS[hashToIconIndex(key)];
  return `/home/random-icons/${encodeURIComponent(fileName)}`;
}

const AGENT_DETAIL_ID_KEY = 'jingxin_agent_detail_id';

interface AgentDetailItem {
  label: string;
  value: string | string[];
  multiline?: boolean;
  markdown?: boolean;
  list?: boolean;
  emptyText?: string;
}

interface AgentDetailSection {
  key: string;
  title: string;
  items: AgentDetailItem[];
}

function loadDetailId() {
  return typeof window !== 'undefined' ? window.localStorage.getItem(AGENT_DETAIL_ID_KEY) : null;
}
function saveDetailId(id: string | null) {
  if (typeof window === 'undefined') return;
  id ? window.localStorage.setItem(AGENT_DETAIL_ID_KEY, id) : window.localStorage.removeItem(AGENT_DETAIL_ID_KEY);
}

function AgentIcon({ agent, size }: { agent: UserAgentItem; size: number; colorIndex?: number }) {
  const radius = size < 36 ? '50%' : 8;
  if (agent.avatar) {
    return <img src={agent.avatar} alt="" width={size} height={size}
      style={{ borderRadius: radius, objectFit: 'cover', display: 'block' }} />;
  }
  const mapped = AGENT_ICON_MAP[agent.name];
  if (mapped) {
    return <img src={mapped} alt="" width={size} height={size}
      style={{ borderRadius: radius, objectFit: 'cover', display: 'block' }} />;
  }
  // Fallback: deterministic random icon based on agent_id or name
  const iconUrl = getRandomIconUrl(agent.agent_id || agent.name);
  return <img src={iconUrl} alt="" width={size} height={size}
    style={{ borderRadius: radius, objectFit: 'cover', display: 'block' }} />;
}

function AgentListSkeleton() {
  return (
    <div className="jx-agentPage-grid">
      {Array.from({ length: 6 }, (_, idx) => (
        <div key={idx} className="jx-agentCard jx-agentCardSkeleton" aria-hidden="true">
          <div className="jx-agentCard-body">
            <div className="jx-agentCard-head">
              <Skeleton.Avatar active size={28} shape="circle" />
              <div className="jx-agentCardSkeletonMeta">
                <Skeleton.Input active size="small" className="jx-agentCardSkeletonTitle" />
                <Skeleton.Input active size="small" className="jx-agentCardSkeletonBadge" />
              </div>
            </div>
            <Skeleton active paragraph={{ rows: 2, width: ['92%', '74%'] }} title={false} />
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentDetailSkeleton() {
  return (
    <div className="jx-agentPage">
      <div className="jx-agentDetail-top">
        <button className="jx-agentDetail-backBtn" type="button" aria-hidden="true">
          <LeftOutlined style={{ fontSize: 14 }} />
        </button>
        <div className="jx-agentDetail-content">
          <div className="jx-agentDetail-nameRow">
            <Skeleton.Avatar active size={44} shape="square" />
            <Skeleton.Input active className="jx-agentDetailSkeletonTitle" />
            <Skeleton.Input active size="small" className="jx-agentDetailSkeletonBadge" />
          </div>
          <Skeleton.Input active size="small" className="jx-agentDetailSkeletonVersion" />
          <hr className="jx-agentDetail-divider" />
          <div className="jx-agentDetail-sections">
            {Array.from({ length: 3 }, (_, idx) => (
              <section key={idx} className="jx-agentDetail-section" aria-hidden="true">
                <div className="jx-agentDetail-sectionHead">
                  <Skeleton.Input active size="small" className="jx-agentDetailSkeletonSectionTitle" />
                </div>
                <div className="jx-agentDetail-grid">
                  <div className="jx-agentDetail-field">
                    <Skeleton active paragraph={{ rows: 2, width: ['30%', '80%'] }} title={false} />
                  </div>
                  <div className="jx-agentDetail-field">
                    <Skeleton active paragraph={{ rows: 2, width: ['34%', '68%'] }} title={false} />
                  </div>
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AgentPanel() {
  const {
    agents, loading, fetchAgents, deleteAgent, updateAgent, setCurrentAgent,
    fetchAvailableResources, availableResources,
  } = useAgentStore();
  const { panel, panelEntryNonce, setPanel } = useCatalogStore();
  const { setCurrentChatId, updateStore } = useChatStore();

  const [search, setSearch] = useState('');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(loadDetailId);
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false);
  // undefined = list/detail, null = create page, UserAgentItem = edit page
  const [formPageAgent, setFormPageAgent] = useState<UserAgentItem | null | undefined>(undefined);

  useEffect(() => {
    void fetchAgents();
    void fetchAvailableResources();
  }, []);
  useEffect(() => { saveDetailId(selectedAgentId); }, [selectedAgentId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = agents.filter((a) => a.is_enabled || a.owner_type === 'user');
    if (!q) return list;
    return list.filter((a) => a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q));
  }, [agents, search]);

  const selectedAgent = useMemo(
    () => (selectedAgentId ? agents.find((a) => a.agent_id === selectedAgentId) ?? null : null),
    [selectedAgentId, agents],
  );

  useEffect(() => {
    if (selectedAgentId && !selectedAgent) setSelectedAgentId(null);
  }, [selectedAgentId, selectedAgent]);
  useEffect(() => {
    setHistoryDrawerOpen(false);
  }, [selectedAgentId]);

  useEffect(() => {
    if (panel !== 'agents') return;
    setSelectedAgentId(null);
    setHistoryDrawerOpen(false);
    setFormPageAgent(undefined);
    setSearch('');
  }, [panel, panelEntryNonce]);

  function startAgentChat(agent: UserAgentItem) {
    setCurrentAgent(agent);
    const chatId = nowId('agent');
    updateStore((prev) => ({
      chats: {
        ...prev.chats,
        [chatId]: {
          id: chatId, title: agent.name,
          createdAt: Date.now(), updatedAt: Date.now(),
          messages: [], agentId: agent.agent_id, agentName: agent.name,
        },
      },
      order: [chatId, ...(prev.order || [])],
    }));
    setCurrentChatId(chatId);
    setPanel('chat');
  }

  function handleDelete(agent: UserAgentItem, e?: React.MouseEvent) {
    e?.stopPropagation();
    Modal.confirm({
      title: '删除子智能体', content: `确定删除「${agent.name}」吗？`,
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        try {
          await deleteAgent(agent.agent_id);
          message.success('已删除');
          if (selectedAgentId === agent.agent_id) setSelectedAgentId(null);
        } catch (err: unknown) {
          message.error((err as Error).message || '删除失败');
        }
      },
    });
  }

  async function handleToggleEnabled(agent: UserAgentItem, enabled: boolean) {
    try {
      await updateAgent(agent.agent_id, { is_enabled: enabled });
    } catch (err: unknown) {
      message.error((err as Error).message || '操作失败');
    }
  }

  // ── Create / Edit form page ──────────────────────────────────
  if (formPageAgent !== undefined) {
    return (
      <AgentCreatePage
        agent={formPageAgent}
        onBack={() => setFormPageAgent(undefined)}
        onCreated={() => setFormPageAgent(undefined)}
      />
    );
  }

  if (loading && selectedAgentId) {
    return <AgentDetailSkeleton />;
  }

  // ── Detail view ──────────────────────────────────────────────
  if (selectedAgent) {
    const canEdit = selectedAgent.owner_type === 'user';
    const agentIdx = agents.findIndex((a) => a.agent_id === selectedAgent.agent_id);
    const skillNameMap = new Map((availableResources?.skills || []).map((item) => [item.id, item.name]));
    const mcpNameMap = new Map((availableResources?.mcp_servers || []).map((item) => [item.id, item.name]));
    const skillLabels = (selectedAgent.skill_ids || []).map((id) => skillNameMap.get(id) || id);
    const mcpLabels = (selectedAgent.mcp_server_ids || []).map((id) => mcpNameMap.get(id) || id);
    const version = selectedAgent.version || 'V1.0';
    const changeHistory = [...(selectedAgent.change_history || [])].reverse();
    const detailSections: AgentDetailSection[] = [
      {
        key: 'basic',
        title: '基础信息',
        items: [
          { label: '名称', value: selectedAgent.name || '未填写' },
          { label: '简介', value: selectedAgent.description || '未填写' },
          { label: '创建者类型', value: selectedAgent.owner_type === 'user' ? '用户创建' : '系统内置' },
          { label: '创建时间', value: formatDateTime(selectedAgent.created_at, '未记录') },
        ],
      },
      {
        key: 'interaction',
        title: '交互设定',
        items: [
          { label: '角色设定', value: selectedAgent.system_prompt || '未填写', multiline: true, markdown: true },
          { label: '开场白', value: selectedAgent.welcome_message || '未填写', multiline: true },
        ],
      },
      {
        key: 'bindings',
        title: '能力绑定',
        items: [
          { label: '绑定工具 (MCP)', value: mcpLabels, list: true, emptyText: '未绑定工具' },
          { label: '绑定技能', value: skillLabels, list: true, emptyText: '未绑定技能' },
        ],
      },
      {
        key: 'runtime',
        title: '执行参数',
        items: [
          { label: '最大推理轮次', value: String(selectedAgent.max_iters ?? 10) },
          { label: '共享上下文', value: (selectedAgent.extra_config || {}).shared_context ? '已启用' : '未启用' },
        ],
      },
    ];

    return (
      <div className="jx-agentPage">
        <div className="jx-agentDetail-top">
          {/* Back button — outside the content area */}
          <button className="jx-agentDetail-backBtn" onClick={() => setSelectedAgentId(null)}>
            <LeftOutlined style={{ fontSize: 14 }} />
          </button>

          <div className="jx-agentDetail-content">
            {/* Name row: icon + name + badge + [启用 switch] */}
            <div className="jx-agentDetail-nameRow">
              <div className="jx-agentDetail-iconWrap">
                <AgentIcon agent={selectedAgent} size={44} colorIndex={agentIdx >= 0 ? agentIdx : 0} />
              </div>
              <span className="jx-agentDetail-name">{selectedAgent.name}</span>
              <span className={`jx-agentDetail-badge${selectedAgent.is_enabled ? ' on' : ''}`}>
                {selectedAgent.is_enabled ? '已启用' : '未启用'}
              </span>
              {canEdit && (
                <div className="jx-agentDetail-enableRow">
                  <span className="jx-agentDetail-enableLabel">启用</span>
                  <Switch
                    size="small"
                    checked={selectedAgent.is_enabled}
                    onChange={(v) => handleToggleEnabled(selectedAgent, v)}
                  />
                </div>
              )}
            </div>

            <div className="jx-agentDetail-versionRow">
              <div className="jx-agentDetail-versionLeft">
                <div className="jx-agentDetail-version">版本号：{version}</div>
                <Button
                  type="text"
                  size="small"
                  className="jx-agentDetail-versionAction"
                  onClick={() => setHistoryDrawerOpen(true)}
                >
                  变更记录
                </Button>
              </div>
              <div className="jx-agentDetail-version jx-agentDetail-versionMeta">
                最近更新：{formatDateTime(selectedAgent.updated_at, '未记录')}
              </div>
            </div>

            <hr className="jx-agentDetail-divider" />

            <div className="jx-agentDetail-sections">
              {detailSections.map((section) => (
                <section key={section.key} className="jx-agentDetail-section">
                  <div className="jx-agentDetail-sectionHead">
                    <h3 className="jx-agentDetail-sectionTitle">{section.title}</h3>
                  </div>
                  <div className="jx-agentDetail-grid">
                    {section.items.map((item) => (
                      <div
                        key={`${section.key}-${item.label}`}
                        className={`jx-agentDetail-field${item.multiline ? ' is-multiline' : ''}`}
                      >
                        <div className="jx-agentDetail-fieldLabel">{item.label}</div>
                        <div className="jx-agentDetail-fieldValue">
                          {item.list ? (
                            Array.isArray(item.value) && item.value.length > 0 ? (
                              <div className="jx-agentDetail-chipList">
                                {item.value.map((entry) => (
                                  <span key={`${item.label}-${entry}`} className="jx-agentDetail-chip">{entry}</span>
                                ))}
                              </div>
                            ) : (
                              <span className="jx-agentDetail-emptyText">{item.emptyText || '未填写'}</span>
                            )
                          ) : item.markdown && typeof item.value === 'string' && item.value !== '未填写' ? (
                            <div
                              className="jx-md jx-agentDetail-markdown"
                              dangerouslySetInnerHTML={{ __html: mdToHtml(item.value) }}
                            />
                          ) : (
                            <span className={item.value === '未填写' || item.value === '未记录' ? 'jx-agentDetail-emptyText' : ''}>
                              {item.value}
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              ))}

            </div>

            {/* Actions */}
            <div className="jx-agentDetail-actionsWrap">
              <div className="jx-agentDetail-actions">
                <Button type="primary" onClick={() => startAgentChat(selectedAgent)}>开始对话</Button>
                {canEdit && (
                  <>
                    <Tooltip title="编辑">
                      <Button
                        aria-label="编辑"
                        icon={<EditOutlined />}
                        className="jx-agentDetail-iconBtn"
                        onClick={() => setFormPageAgent(selectedAgent)}
                      />
                    </Tooltip>
                    <Tooltip title="删除">
                      <Button
                        danger
                        aria-label="删除"
                        icon={<DeleteOutlined />}
                        className="jx-agentDetail-iconBtn danger"
                        onClick={(e) => handleDelete(selectedAgent, e)}
                      />
                    </Tooltip>
                  </>
                )}
              </div>
            </div>

            <Drawer
              title="变更记录"
              placement="right"
              width={460}
              open={historyDrawerOpen}
              onClose={() => setHistoryDrawerOpen(false)}
              className="jx-agentHistoryDrawer"
            >
              {changeHistory.length > 0 ? (
                <div className="jx-agentDetail-historyList">
                  {changeHistory.map((item, index) => (
                    <div
                      key={`${item.timestamp}-${item.version || index}`}
                      className="jx-agentDetail-historyItem"
                    >
                      <div className="jx-agentDetail-historyDot" aria-hidden="true" />
                      <div className="jx-agentDetail-historyBody">
                        <div className="jx-agentDetail-historyMeta">
                          {item.version ? (
                            <span className="jx-agentDetail-historyVersion">{item.version}</span>
                          ) : null}
                          <span className="jx-agentDetail-historyTime">{formatDateTime(item.timestamp, '未记录')}</span>
                        </div>
                        <div className="jx-agentDetail-historyInfoRow">
                          <span className="jx-agentDetail-historyInfoLabel">操作人员</span>
                          <span className="jx-agentDetail-historyInfoValue">{item.operator_name || '未知用户'}</span>
                        </div>
                        <div className="jx-agentDetail-historyInfoRow">
                          <span className="jx-agentDetail-historyInfoLabel">操作时间</span>
                          <span className="jx-agentDetail-historyInfoValue">{formatDateTime(item.timestamp, '未记录')}</span>
                        </div>
                        <div className="jx-agentDetail-historyInfoRow">
                          <span className="jx-agentDetail-historyInfoLabel">变更内容</span>
                          <span className="jx-agentDetail-historyInfoValue">{item.content}</span>
                        </div>
                        {item.details?.length > 0 ? (
                          <div className="jx-agentDetail-historyDetailList">
                            {item.details.map((detail, detailIndex) => (
                              <div
                                key={`${item.timestamp}-${detail.field}-${detailIndex}`}
                                className="jx-agentDetail-historyDetailItem"
                              >
                                <div className="jx-agentDetail-historyDetailField">{detail.field}</div>
                                <div className="jx-agentDetail-historyDetailValues">
                                  <div className="jx-agentDetail-historyDetailLine">
                                    <span className="jx-agentDetail-historyDetailTag">修改前</span>
                                    <span className="jx-agentDetail-historyDetailText">{detail.before}</span>
                                  </div>
                                  <div className="jx-agentDetail-historyDetailLine">
                                    <span className="jx-agentDetail-historyDetailTag is-after">修改后</span>
                                    <span className="jx-agentDetail-historyDetailText">{detail.after}</span>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="jx-agentDetail-historyEmpty">暂无变更记录</div>
              )}
            </Drawer>
          </div>
        </div>
      </div>
    );
  }

  // ── List view ────────────────────────────────────────────────
  return (
    <div className="jx-agentPage">
      {/* Header */}
      <div className="jx-agentPage-header">
        <div>
          <h2 className="jx-agentPage-title">子智能体</h2>
          <p className="jx-agentPage-subtitle">选择与启用子智能体，并查看其职责边界与路由提示</p>
        </div>
        <div className="jx-agentPage-headerRight">
          <Input
            placeholder="搜索"
            prefix={<SearchOutlined style={{ color: '#B3BAC8' }} />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            className="jx-agentPage-search"
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            className="jx-agentPage-createBtn"
            onClick={() => setFormPageAgent(null)}
          >
            创建智能体
          </Button>
        </div>
      </div>

      {/* Card grid */}
      {loading ? (
        <AgentListSkeleton />
      ) : filtered.length === 0 ? (
        <div className="jx-agentPage-empty">暂无子智能体</div>
      ) : (
        <div className="jx-agentPage-grid">
          {filtered.map((agent, idx) => {
            const canEdit = agent.owner_type === 'user';
            return (
              <div key={agent.agent_id} className="jx-agentCard"
                onClick={() => setSelectedAgentId(agent.agent_id)}>
                <div className="jx-agentCard-body">
                  <div className="jx-agentCard-head">
                    {/* 28px circle icon */}
                    <div className="jx-agentCard-iconWrap">
                      <AgentIcon agent={agent} size={28} colorIndex={idx} />
                    </div>
                    {/* name + badge */}
                    <div className="jx-agentCard-nameRow">
                      <span className="jx-agentCard-name">{agent.name}</span>
                      <span className={`jx-agentCard-badge${agent.is_enabled ? ' on' : ''}`}>
                        {agent.is_enabled ? '已启用' : '未启用'}
                      </span>
                    </div>
                    {/* edit / delete on hover */}
                    {canEdit && (
                      <span className="jx-agentCard-ops" onClick={(e) => e.stopPropagation()}>
                        <Tooltip title="编辑">
                          <button onClick={() => setFormPageAgent(agent)}><EditOutlined /></button>
                        </Tooltip>
                        <Tooltip title="删除">
                          <button className="danger" onClick={(e) => handleDelete(agent, e)}>
                            <DeleteOutlined />
                          </button>
                        </Tooltip>
                      </span>
                    )}
                  </div>
                  <p className="jx-agentCard-desc">
                    {agent.description || agent.system_prompt?.slice(0, 100) || '暂无描述'}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
