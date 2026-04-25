import { useEffect, useMemo, useRef } from 'react';
import {
  Layout, Input, Dropdown, Select, Tooltip, Badge, Modal, message,
} from 'antd';
import {
  DeleteOutlined, EditOutlined,
  PushpinOutlined, PushpinFilled, StarOutlined, StarFilled,
  SearchOutlined, CloseOutlined, EllipsisOutlined,
  ExportOutlined, ExclamationCircleFilled,
} from '@ant-design/icons';
import { useUIStore, useChatStore, useAuthStore, useMySpaceStore, useAutomationChatStore, useAutomationStore } from '../../stores';
import { useCatalogStore } from '../../stores/catalogStore';
import { inferBusinessTopic, isAutomationHistoryChat, matchesTimeFilter, getHistoryGroupKey } from '../../utils/history';
import { highlightKeyword } from '../../utils/highlight';
import { resolveAvatarUrl } from '../../utils/avatar';
import { getAutomationRuns } from '../../api';
import type { ChatItem, PanelKey } from '../../types';
import type { SearchResultItem } from '../../api';

type HistoryGroupKey = 'pinned' | 'today' | 'yesterday' | 'week' | 'month' | 'older';

const { Sider } = Layout;

interface SidebarProps {
  onNewChat: () => void;
  onDeleteChat: (id: string) => void;
  onTogglePinned: (id: string) => void;
  onToggleFavorite: (id: string) => void;
  onStartRename: (item: ChatItem) => void;
  onCommitRename: (id: string) => void;
  onExportChat: (id: string) => void;
  onSelectChat: (id: string) => void;
  onSelectSearchResult: (item: SearchResultItem) => void;
  onSetPanel: (p: PanelKey) => void;
}

const NAV_ITEMS: Array<{
  key: string;
  label: string;
  icon: string;
  targetPanel?: PanelKey;
  activePanels?: PanelKey[];
}> = [
  { key: 'agents', label: '子智能体', icon: '/home/子智能体.svg', targetPanel: 'agents', activePanels: ['agents'] },
  { key: 'kb', label: '知识库', icon: '/home/知识库.svg', targetPanel: 'kb', activePanels: ['kb'] },
  { key: 'my_space', label: '我的空间', icon: '/home/我的空间.svg', targetPanel: 'my_space', activePanels: ['my_space'] },
];

export function Sidebar({
  onNewChat, onDeleteChat, onTogglePinned, onToggleFavorite,
  onStartRename, onCommitRename, onExportChat, onSelectChat,
  onSelectSearchResult, onSetPanel,
}: SidebarProps) {
  const {
    siderCollapsed,
    setSiderCollapsed,
    searchMode, setSearchMode,
    searchKeyword, setSearchKeyword,
    searchResults, setSearchResults,
    searchLoading,
    historyTimeFilter, setHistoryTimeFilter,
    historyTopicFilter, setHistoryTopicFilter,
    editingChatId, setEditingChatId,
    editingTitle, setEditingTitle,
  } = useUIStore();
  const { store, currentChatId, chatsLoading, sendingChatIds, updateStore, addBackendSessionId } = useChatStore();
  const { authUser, doLogout } = useAuthStore();
  const { panel } = useCatalogStore();
  const notifUnreadCount = useMySpaceStore((s) => s.notifUnreadCount);
  const sidebarTasks = useAutomationChatStore((s) => s.sidebarTasks);
  const sidebarPrefs = useAutomationChatStore((s) => s.sidebarPrefs);
  const automationActiveGroup = useAutomationChatStore((s) => s.activeGroup);
  const selectedRunId = useAutomationChatStore((s) => s.selectedRunId);
  const enterAutomationChat = useAutomationChatStore((s) => s.enterAutomationChat);
  const updateSidebarTask = useAutomationChatStore((s) => s.updateSidebarTask);
  const renameActiveGroup = useAutomationChatStore((s) => s.renameActiveGroup);
  const toggleSidebarPinned = useAutomationChatStore((s) => s.toggleSidebarPinned);
  const toggleSidebarFavorite = useAutomationChatStore((s) => s.toggleSidebarFavorite);
  const updateAutomationTask = useAutomationStore((s) => s.updateTask);
  const setAutomationSelectedTaskId = useAutomationStore((s) => s.setSelectedTaskId);

  const historyListRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<any>(null);
  const showScrollbar = () => historyListRef.current?.classList.add('show-scrollbar');
  const hideScrollbar = () => historyListRef.current?.classList.remove('show-scrollbar');

  useEffect(() => {
    if (searchMode) searchInputRef.current?.focus();
  }, [searchMode]);

  const historyList = useMemo(() => {
    const ids = (store.order || []).filter((id) => store.chats[id]);
    const normalChats = ids
      .map((id) => store.chats[id])
      .filter((item) => !isAutomationHistoryChat(item));

    // Create virtual sidebar entries for activated automation tasks
    const automationItems: ChatItem[] = sidebarTasks.map((t) => ({
      id: `automation:${t.task_id}`,
      title: t.name || t.prompt?.slice(0, 30) || '自动化任务',
      createdAt: new Date(t.created_at).getTime(),
      updatedAt: t.last_run_at ? new Date(t.last_run_at).getTime() : new Date(t.updated_at).getTime(),
      messages: [],
      pinned: !!sidebarPrefs[t.task_id]?.pinned,
      favorite: !!sidebarPrefs[t.task_id]?.favorite,
      automationTaskId: t.task_id,
      automationRun: true,
    }));

    return [...normalChats, ...automationItems];
  }, [store, sidebarPrefs, sidebarTasks]);

  const startRenameItem = (item: ChatItem) => {
    if (item.automationRun) {
      setEditingChatId(item.id);
      setEditingTitle(item.title || '自动化任务');
      return;
    }
    onStartRename(item);
  };

  const commitRenameItem = async (item: ChatItem) => {
    if (!item.automationRun || !item.automationTaskId) {
      onCommitRename(item.id);
      return;
    }

    const nextTitle = editingTitle.trim() || '自动化任务';
    setEditingChatId(null);
    setEditingTitle('');

    if (nextTitle === item.title) return;

    try {
      const updated = await updateAutomationTask(item.automationTaskId, { name: nextTitle });
      updateSidebarTask(updated);
      renameActiveGroup(item.automationTaskId, updated.name || nextTitle);
      message.success('自动化任务已重命名');
    } catch (e) {
      message.error((e as Error)?.message || '重命名失败');
    }
  };

  const exportAutomationItem = async (item: ChatItem) => {
    if (!item.automationTaskId) return;
    try {
      const runs = await getAutomationRuns(item.automationTaskId, 50);
      const preferredRun = automationActiveGroup?.taskId === item.automationTaskId && selectedRunId
        ? runs.find((run) => run.run_id === selectedRunId && run.status !== 'running' && run.chat_id)
        : undefined;
      const fallbackRun = runs.find((run) => run.status !== 'running' && run.chat_id);
      const targetRun = preferredRun || fallbackRun;

      if (!targetRun?.chat_id) {
        message.warning('暂无可导出的执行记录');
        return;
      }

      updateStore((prev) => {
        if (prev.chats[targetRun.chat_id!]) return prev;
        return {
          chats: {
            ...prev.chats,
            [targetRun.chat_id!]: {
              id: targetRun.chat_id!,
              title: item.title || '自动化任务',
              createdAt: item.createdAt,
              updatedAt: item.updatedAt,
              messages: [],
              automationRun: true,
              automationTaskId: item.automationTaskId,
            },
          },
          order: prev.order.includes(targetRun.chat_id!) ? prev.order : [targetRun.chat_id!, ...prev.order],
        };
      });
      addBackendSessionId(targetRun.chat_id);
      onExportChat(targetRun.chat_id);
    } catch (e) {
      message.error((e as Error)?.message || '导出失败');
    }
  };

  const getChatTopic = (item: ChatItem) => {
    if (item.businessTopic) return item.businessTopic;
    const firstUserMsg = (item.messages || []).find((m) => m.role === 'user')?.content || '';
    return inferBusinessTopic(`${item.title} ${firstUserMsg}`);
  };

  const historyTopicOptions = useMemo(() => {
    return Array.from(new Set(historyList.map((x) => getChatTopic(x)))).filter(Boolean);
  }, [historyList]);

  const filteredHistoryList = useMemo(() => {
    const filtered = historyList.filter((item) => {
      const matchTime = matchesTimeFilter(item.updatedAt || item.createdAt, historyTimeFilter);
      let matchTopic: boolean;
      if (historyTopicFilter === 'all') {
        matchTopic = true;
      } else if (historyTopicFilter === '_mode:agent') {
        matchTopic = !!(item as any).agentName;
      } else if (historyTopicFilter === '_mode:codeExec') {
        matchTopic = !!(item as any).codeExecChat;
      } else if (historyTopicFilter === '_mode:plan') {
        matchTopic = !!(item as any).planChat;
      } else {
        const topic = getChatTopic(item);
        matchTopic = topic === historyTopicFilter;
      }
      return matchTime && matchTopic;
    });
    return [...filtered].sort((a, b) => {
      const pinDiff = Number(!!b.pinned) - Number(!!a.pinned);
      if (pinDiff !== 0) return pinDiff;
      return (b.updatedAt || 0) - (a.updatedAt || 0);
    });
  }, [historyList, historyTimeFilter, historyTopicFilter]);

  const groupedHistoryList = useMemo(() => {
    const labels: Record<HistoryGroupKey, string> = {
      pinned: '置顶',
      today: '今天',
      yesterday: '昨天',
      week: '近7天',
      month: '近30天',
      older: '更早',
    };
    const pinnedItems = filteredHistoryList.filter((item) => item.pinned);
    const groups: Record<Exclude<HistoryGroupKey, 'pinned'>, ChatItem[]> = {
      today: [],
      yesterday: [],
      week: [],
      month: [],
      older: [],
    };
    filteredHistoryList.forEach((item) => {
      if (item.pinned) return;
      const ts = item.updatedAt || item.createdAt || 0;
      groups[getHistoryGroupKey(ts)].push(item);
    });
    const orderedGroups = (['today', 'yesterday', 'week', 'month', 'older'] as const)
      .map((key) => ({ key, label: labels[key], items: groups[key] }))
      .filter((group) => group.items.length > 0);
    return pinnedItems.length > 0
      ? [{ key: 'pinned' as const, label: labels.pinned, items: pinnedItems }, ...orderedGroups]
      : orderedGroups;
  }, [filteredHistoryList]);

  const historySkeletonGroups = [
    { key: 'today', label: '今天', rows: 4 },
    { key: 'yesterday', label: '昨天', rows: 4 },
    { key: 'week', label: '近7天', rows: 5 },
  ];

  return (
    <Sider width={280} className="jx-sider" theme="light" collapsed={siderCollapsed} collapsedWidth={0}
      style={{ overflow: 'hidden' }}>
      <div className="jx-siderInner">
        {/* Logo row + collapse button */}
        <div className="jx-brandRow">
          <button type="button" className="jx-brandHomeBtn" onClick={onNewChat} title="回到首页">
            <div className="jx-logo"><img src="/home/logo.svg" alt="" className="jx-logoImg" /></div>
            <div className="jx-brand-text">
              <div className="jx-brand-title">经信智能体</div>
              <div className="jx-brand-sub">宁波经信AI智能助手</div>
            </div>
          </button>
          <button className="jx-searchBtn" onClick={() => setSearchMode(!searchMode)} title="搜索对话">
            <img src="/home/搜索.svg" alt="" style={{ width: 20, height: 20 }} />
          </button>
          <button className="jx-collapseBtn" onClick={() => setSiderCollapsed(true)} title="收起侧边栏">
            <img src="/home/展开.svg" alt="" style={{ width: 20, height: 20 }} />
          </button>
        </div>

        {/* New Chat button */}
        <button className="jx-newChatBtn" onClick={onNewChat}>
          <img src="/home/新建对话.svg" alt="" className="jx-newChatIcon" />
          <span>新建对话</span>
        </button>

        {/* Primary nav menu */}
        <div className="jx-navMenu">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              className={`jx-navItem${item.activePanels?.includes(panel) ? ' active' : ''}`}
              onClick={() => { if (item.targetPanel) onSetPanel(item.targetPanel); }}>
              {item.key === 'my_space' && notifUnreadCount > 0 ? (
                <Badge count={notifUnreadCount} size="small" offset={[-2, 2]}>
                  <img src={item.icon} alt="" className="jx-navItemIcon" />
                </Badge>
              ) : (
                <img src={item.icon} alt="" className="jx-navItemIcon" />
              )}
              <span>{item.label}</span>
            </button>
          ))}
        </div>

        {/* History header */}
        <div className="jx-historyHead">
            <span className="jx-historyLabel">历史对话</span>
            <div className="jx-historyHeadRight">
              <Select
                value={historyTimeFilter}
                onChange={(v) => setHistoryTimeFilter(v as any)}
                className="jx-filterSelect"
                size="small"
                variant="borderless"
                options={[
                  { value: 'all', label: '全部时间' },
                  { value: 'today', label: '今天' },
                  { value: '7d', label: '近7天' },
                  { value: '30d', label: '近30天' },
                ]}
                popupClassName="jx-filterSelectPopup"
              />
              <Select
                value={historyTopicFilter}
                onChange={(v) => setHistoryTopicFilter(v)}
                className="jx-filterSelect"
                size="small"
                variant="borderless"
                options={[
                  { value: 'all', label: '全部类型' },
                  { value: '_mode:agent', label: '子智能体' },
                  { value: '_mode:codeExec', label: '代码执行' },
                  { value: '_mode:plan', label: '计划模式' },
                  ...historyTopicOptions.map((t) => ({ value: t, label: t })),
                ]}
                popupClassName="jx-filterSelectPopup"
              />
            </div>
          </div>

          <div className={`jx-expandWrap jx-searchRowWrap${searchMode ? ' jx-expandWrap--open' : ''}`}>
            <div className="jx-searchRow">
              <Input
                ref={searchInputRef}
                className="jx-searchInput"
                placeholder="搜索对话"
                prefix={<SearchOutlined style={{ color: 'rgba(0,0,0,.25)' }} />}
                suffix={<CloseOutlined className="jx-searchClearBtn" onClick={() => { setSearchMode(false); setSearchKeyword(''); setSearchResults([]); }} />}
                value={searchKeyword} onChange={(e) => setSearchKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Escape') { setSearchMode(false); setSearchKeyword(''); setSearchResults([]); } }}
              />
            </div>
          </div>

          {/* History list */}
          <div className="jx-historyListWrap" ref={historyListRef} onMouseEnter={showScrollbar} onMouseLeave={hideScrollbar}>
            {searchMode ? (
              searchLoading ? (
                <div className="jx-historySkeletonList" aria-hidden="true">
                  {[0, 1, 2, 3].map((item) => (
                    <div key={item} className="jx-historySkeletonItem">
                      <div className="jx-skeletonBlock jx-historySkTitle" />
                      <div className="jx-skeletonBlock jx-historySkSnippet" />
                    </div>
                  ))}
                </div>
              ) : searchKeyword.trim() && searchResults.length === 0 ? (
                <div className="jx-historyEmpty">无匹配结果</div>
              ) : (
                <div className="jx-historyGroupList">
                  {searchResults.map((item) => (
                    <div key={item.id} className={`jx-historyItem${panel === 'chat' && item.id === currentChatId ? ' active' : ''}`}
                      onClick={() => onSelectSearchResult(item)}>
                      <div className="jx-searchResultMain">
                        <span className="jx-historyTitle">{highlightKeyword(item.title || '对话', searchKeyword)}</span>
                        {(item as any).match_type === 'content' && (item as any).matched_snippet && (
                          <span className="jx-searchSnippet">{highlightKeyword((item as any).matched_snippet, searchKeyword)}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )
            ) : chatsLoading ? (
              <div className="jx-historySkeletonList" aria-hidden="true">
                {historySkeletonGroups.map((group) => (
                  <div key={group.key} className="jx-historyGroup">
                    <div className="jx-historyGroupTitle">{group.label}</div>
                    <div className="jx-historyGroupList">
                      {Array.from({ length: group.rows }).map((_, index) => (
                        <div key={`${group.key}-${index}`} className="jx-historyItem jx-historyItemSkeleton">
                          <div className="jx-historyMain">
                            <div className="jx-skeletonBlock jx-historySkLine" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : groupedHistoryList.length === 0 ? (
              <div className="jx-historyEmpty">暂无对话记录</div>
            ) : (
              groupedHistoryList.map((group) => (
                <div key={group.key} className="jx-historyGroup" aria-label={group.label}>
                  <div className="jx-historyGroupTitle">{group.label}</div>
                  <div className="jx-historyGroupList">
                    {group.items.map((item) => {
                      const isAutomation = !!item.automationRun;
                      const isActive = isAutomation
                        ? (panel === 'chat' && automationActiveGroup?.taskId === item.automationTaskId)
                        : (panel === 'chat' && item.id === currentChatId);
                      const isEditing = editingChatId === item.id;

                      const handleClick = async () => {
                        if (editingChatId && editingChatId !== item.id) { setEditingChatId(null); setEditingTitle(''); }
                        if (isAutomation && item.automationTaskId) {
                          // Fetch runs then enter automation chat mode
                          try {
                            const runs = await getAutomationRuns(item.automationTaskId, 50);
                            enterAutomationChat(item.automationTaskId, item.title, runs);
                          } catch { /* ignore */ }
                        } else {
                          onSelectChat(item.id);
                        }
                      };

                      return (
                        <div key={item.id} className={`jx-historyItem${isActive ? ' active' : ''}${isEditing ? ' editing' : ''}`}
                          onClick={handleClick}>
                          {isEditing ? (
                            <Input size="small" value={editingTitle} autoFocus
                              onChange={(e) => setEditingTitle(e.target.value)}
                              onPressEnter={() => void commitRenameItem(item)}
                              onBlur={() => void commitRenameItem(item)}
                              onClick={(e) => e.stopPropagation()}
                              maxLength={30} className="jx-historyEditInput" />
                          ) : (
                            <div className="jx-historyMain">
                              {item.pinned ? (
                                <Tooltip title="已置顶">
                                  <span className="jx-historyPinIcon" onClick={(e) => e.stopPropagation()}>
                                    <PushpinFilled />
                                  </span>
                                </Tooltip>
                              ) : null}
                              {isAutomation ? (
                                <Tooltip title="自动化任务">
                                  <span className="jx-historyTypeIcon jx-historyTypeIcon--automation" style={{ fontSize: 13, color: '#faad14', flexShrink: 0 }}>&#9889;</span>
                                </Tooltip>
                              ) : (item as any).agentName ? (
                                <Tooltip title={(item as any).agentName}>
                                  <img src="/home/新增icon/智能体.svg" alt="子智能体" className="jx-historyTypeIcon jx-historyTypeIcon--agent" style={{ width: 14, height: 14 }} />
                                </Tooltip>
                              ) : (item as any).codeExecChat ? (
                                <Tooltip title="代码执行">
                                  <img src="/home/新增icon/代码-线条.svg" alt="代码执行" className="jx-historyTypeIcon jx-historyTypeIcon--code" style={{ width: 14, height: 14 }} />
                                </Tooltip>
                              ) : (item as any).planChat ? (
                                <Tooltip title="计划模式">
                                  <img src="/home/新增icon/计划.svg" alt="计划模式" className="jx-historyTypeIcon jx-historyTypeIcon--plan" style={{ width: 14, height: 14 }} />
                                </Tooltip>
                              ) : null}
                              <span className="jx-historyTitle">
                                {item.title || '对话'}
                              </span>
                              {sendingChatIds.has(item.id) && (
                                <Tooltip title="运行中">
                                  <span className="jx-historyRunningDot" />
                                </Tooltip>
                              )}
                            </div>
                          )}
                          <div className="jx-historyActions">
                            <Dropdown menu={{
                              items: isAutomation
                                ? [
                                    { key: 'pin', label: item.pinned ? '取消置顶' : '置顶', icon: item.pinned ? <PushpinFilled /> : <PushpinOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); if (item.automationTaskId) toggleSidebarPinned(item.automationTaskId); } },
                                    { key: 'fav', label: item.favorite ? '取消收藏' : '收藏', icon: item.favorite ? <StarFilled /> : <StarOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); if (item.automationTaskId) toggleSidebarFavorite(item.automationTaskId); } },
                                    { key: 'rename', label: '重命名', icon: <EditOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); startRenameItem(item); } },
                                    { key: 'export', label: '导出', icon: <ExportOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); void exportAutomationItem(item); } },
                                  ]
                                : [
                                    { key: 'pin', label: item.pinned ? '取消置顶' : '置顶', icon: item.pinned ? <PushpinFilled /> : <PushpinOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); onTogglePinned(item.id); } },
                                    { key: 'fav', label: item.favorite ? '取消收藏' : '收藏', icon: item.favorite ? <StarFilled /> : <StarOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); onToggleFavorite(item.id); } },
                                    { key: 'rename', label: '重命名', icon: <EditOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); startRenameItem(item); } },
                                    { key: 'export', label: '导出', icon: <ExportOutlined />, onClick: ({ domEvent }) => { domEvent.stopPropagation(); onExportChat(item.id); } },
                                    { type: 'divider' as const },
                                    { key: 'delete', label: '删除', icon: <DeleteOutlined />, danger: true, onClick: ({ domEvent }) => { domEvent.stopPropagation(); onDeleteChat(item.id); } },
                                  ],
                            }} trigger={['click']} placement="bottomRight" overlayClassName="jx-chatItemMenu">
                              <button aria-label="更多操作" className="jx-historyMoreBtn" onClick={(e) => e.stopPropagation()}><EllipsisOutlined /></button>
                            </Dropdown>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>

        {/* Footer: user info + help button */}
        <div className="jx-sideFooter">
          <Dropdown menu={{
            items: [
              { key: 'settings', label: '设置', icon: <img src="/home/设置.svg" alt="" style={{ width: 16, height: 16 }} />, onClick: () => onSetPanel('settings') },
              { key: 'ability_center', label: '能力中心', icon: <img src="/home/能力中心.svg" alt="" style={{ width: 16, height: 16 }} />, onClick: () => onSetPanel('ability_center') },
              { key: 'lab', label: '实验室', icon: <img src="/home/新增icon/实验室.svg" alt="" style={{ width: 16, height: 16 }} />, onClick: () => { setAutomationSelectedTaskId(null); onSetPanel('lab'); } },
              { type: 'divider' as const },
              {
                key: 'logout',
                label: '退出登录',
                icon: <img src="/home/退出.svg" alt="" style={{ width: 16, height: 16 }} />,
                danger: true,
                onClick: () => {
                  Modal.confirm({
                    title: '确认退出登录？',
                    icon: <ExclamationCircleFilled style={{ color: '#F8AB42' }} />,
                    content: '退出登录不会丢失任何数据，你仍可以登录此账号。',
                    okText: '退出登录',
                    cancelText: '取消',
                    okButtonProps: { danger: true },
                    onOk: () => void doLogout(),
                  });
                },
              },
            ],
          }} trigger={['click']} placement="topLeft" overlayClassName="jx-settingsMenu">
            <button className="jx-userInfoBtn">
              <img src={resolveAvatarUrl(authUser?.avatar_url)} alt="" className="jx-userAvatar" />
              <span className="jx-userName">{authUser?.username || '用户'}</span>
            </button>
          </Dropdown>
          <Dropdown menu={{
            items: [
              { key: 'docs', label: '更新记录', icon: <img src="/home/更新记录.svg" alt="" style={{ width: 16, height: 16 }} />, onClick: () => onSetPanel('docs') },
              { key: 'manual', label: '操作手册', icon: <img src="/home/知识库.svg" alt="" style={{ width: 16, height: 16 }} />, onClick: () => window.open('/docs/manual/操作手册.pdf', '_blank') },
            ],
          }} trigger={['click']} placement="topRight" overlayClassName="jx-settingsMenu">
            <button className="jx-helpBtn" title="帮助">
              <img src="/home/帮助.svg" alt="" style={{ width: 16, height: 16, opacity: 0.45 }} />
            </button>
          </Dropdown>
        </div>
      </div>
    </Sider>
  );
}
