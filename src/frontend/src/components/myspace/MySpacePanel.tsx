import { useEffect, useCallback, useMemo, useRef, useState } from 'react';
import { Button, Input, Modal, Select, Spin, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { addArtifactToKnowledgeBase, getAutomationRuns } from '../../api';
import type { KBItem, MySpaceTab, ResourceItem } from '../../types';
import { useMySpaceStore } from '../../stores/mySpaceStore';
import { useAutomationChatStore } from '../../stores/automationChatStore';
import { useCatalogStore, useChatStore, useCanvasStore } from '../../stores';
import { buildFileUrl } from '../../utils/constants';
import { DocumentList } from './DocumentList';
import { ImageGrid } from './ImageGrid';
import { FavoriteList } from './FavoriteList';
import { NotificationList } from './NotificationList';
import { ShareRecordsPage } from '../share';

const TABS: Array<{ key: MySpaceTab; label: string }> = [
  { key: 'assets', label: '文件资产' },
  { key: 'favorites', label: '会话收藏' },
  { key: 'shares', label: '分享记录' },
  { key: 'notifications', label: '消息通知' },
];

export function MySpacePanel() {
  const enterAutomationChat = useAutomationChatStore((s) => s.enterAutomationChat);
  const exitAutomationChat = useAutomationChatStore((s) => s.exitAutomationChat);
  const {
    resources, favorites, loading, tab, searchKeyword, hasMore, favHasMore,
    assetFilter, sourceFilter, notifUnreadCount,
    setTab, setSearchKeyword, setAssetFilter, setSourceFilter,
    fetchResources, fetchFavorites, deleteResource, unfavoriteChat, removeFavorite, loadMore,
  } = useMySpaceStore();
  const { catalog, setPanel } = useCatalogStore();
  const { setCurrentChatId } = useChatStore();
  const openCanvas = useCanvasStore((s) => s.openCanvas);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  const [kbPickerLoading, setKbPickerLoading] = useState(false);
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>([]);
  const [pendingResources, setPendingResources] = useState<ResourceItem[]>([]);

  // Initial fetch on mount
  useEffect(() => {
    if (tab === 'favorites') {
      void fetchFavorites(true);
    } else if (tab === 'assets') {
      void fetchResources(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
    }
  }, []);

  // Search debounce
  const handleSearch = useCallback((value: string) => {
    setSearchKeyword(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      if (tab === 'favorites') {
        void fetchFavorites(true);
      } else if (tab === 'assets') {
        void fetchResources(true);
      }
    }, 300);
  }, [tab, setSearchKeyword, fetchFavorites, fetchResources]);

  const handleDownload = useCallback((item: ResourceItem) => {
    if (!item.file_id) return;
    const a = document.createElement('a');
    a.href = buildFileUrl(item.file_id);
    a.download = item.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }, []);

  const handleNavigate = useCallback((item: ResourceItem) => {
    if (!item.source_chat_id) return;
    if (item.source_chat_id.startsWith('automation:')) {
      const taskId = item.source_chat_id.slice('automation:'.length);
      void (async () => {
        try {
          const runs = await getAutomationRuns(taskId, 50);
          enterAutomationChat(taskId, item.source_chat_title || item.name || '自动化任务', runs);
        } catch (err: any) {
          message.error(err?.message || '打开自动化任务失败');
        }
      })();
      return;
    }
    if (useAutomationChatStore.getState().activeGroup) exitAutomationChat();
    setCurrentChatId(item.source_chat_id);
    setPanel('chat');
  }, [enterAutomationChat, exitAutomationChat, setCurrentChatId, setPanel]);

  const handleDelete = useCallback((item: ResourceItem) => {
    void deleteResource(item.id);
  }, [deleteResource]);

  const handleRequestUnfavorite = useCallback(async (item: ResourceItem) => {
    if (!item.source_chat_id) return;
    return await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: '取消收藏',
        content: '确定将这条会话从收藏列表中移除吗？',
        okText: '取消收藏',
        cancelText: '保留',
        onOk: async () => {
          try {
            await unfavoriteChat(item.source_chat_id as string);
            message.success('已取消收藏');
            resolve(true);
          } catch (err: any) {
            message.error(err?.message || '取消收藏失败');
            resolve(false);
          }
        },
        onCancel: () => resolve(false),
      });
    });
  }, [unfavoriteChat]);

  const handleFinalizeUnfavorite = useCallback((item: ResourceItem) => {
    if (!item.source_chat_id) return;
    removeFavorite(item.source_chat_id);
  }, [removeFavorite]);

  const handlePreview = useCallback((item: ResourceItem) => {
    if (!item.file_id) return;
    openCanvas({
      file_id: item.file_id,
      name: item.name,
      url: `/files/${item.file_id}`,
      mime_type: item.mime_type,
      size: item.size,
    });
  }, [openCanvas]);

  // Scroll-to-load-more
  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 100) {
      void loadMore();
    }
  }, [loadMore]);

  const currentItems = tab === 'favorites' ? favorites : resources;
  const currentHasMore = tab === 'favorites' ? favHasMore : hasMore;

  const privateKbOptions = useMemo<KBItem[]>(
    () => catalog.kb.filter((item) => item.visibility === 'private' && item.editable !== false && !item.system_managed),
    [catalog.kb],
  );

  const openKbPicker = useCallback((items: ResourceItem | ResourceItem[]) => {
    if (privateKbOptions.length === 0) {
      message.warning('请先创建至少一个私有知识库');
      return;
    }
    const nextItems = (Array.isArray(items) ? items : [items]).filter((item) => !!item.file_id);
    if (nextItems.length === 0) {
      message.warning('当前选择中没有可加入知识库的文件');
      return;
    }
    setPendingResources(nextItems);
    setSelectedKbIds(privateKbOptions[0]?.id ? [privateKbOptions[0].id] : []);
    setKbPickerOpen(true);
  }, [privateKbOptions]);

  const closeKbPicker = useCallback(() => {
    if (kbPickerLoading) return;
    setKbPickerOpen(false);
    setPendingResources([]);
    setSelectedKbIds([]);
  }, [kbPickerLoading]);

  const handleAddToKb = useCallback(async () => {
    if (pendingResources.length === 0 || selectedKbIds.length === 0) {
      message.warning('请至少选择一个目标知识库');
      return;
    }
    setKbPickerLoading(true);
    try {
      const results = await Promise.all(
        pendingResources.flatMap((resource) => (
          selectedKbIds.map(async (kbId) => ({
            resourceId: resource.id,
            result: await addArtifactToKnowledgeBase(resource.id, kbId),
          }))
        ))
      );
      const alreadyExistsCount = results.filter(({ result }) => result.already_exists).length;
      const addedCount = results.length - alreadyExistsCount;
      const fileCount = pendingResources.length;
      const kbCount = selectedKbIds.length;
      if (addedCount > 0 && alreadyExistsCount > 0) {
        message.success(`已处理 ${fileCount} 个文件，${addedCount} 条加入成功，${alreadyExistsCount} 条已存在`);
      } else if (addedCount > 0) {
        message.success(`已将 ${fileCount} 个文件加入 ${kbCount} 个知识库，正在索引`);
      } else {
        message.success('所选知识库中均已存在这些文件');
      }
      await fetchResources(true);
      setKbPickerOpen(false);
      setPendingResources([]);
      setSelectedKbIds([]);
    } catch (err: any) {
      message.error(err?.message || '加入知识库失败');
    } finally {
      setKbPickerLoading(false);
    }
  }, [pendingResources, selectedKbIds, fetchResources]);

  const tabDescriptions: Record<MySpaceTab, string> = {
    assets: '汇集与AI会话过程中上传或生成的各类文档与图片，可按需加入你创建的私有知识库',
    favorites: '集中管理你收藏的重要会话与自动化任务，方便快速回看与继续交流',
    shares: '查看并管理已生成的分享链接与有效状态，查看浏览量',
    notifications: '查看自动化任务执行结果通知，及时了解任务完成状态',
  };

  return (
    <div className="jx-mySpace">
      <div className="jx-mySpace-shell">
        <div className="jx-mySpace-header">
          <div className="jx-mySpace-tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                className={`jx-mySpace-tab${tab === t.key ? ' active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                <span>{t.label}</span>
                {t.key === 'notifications' && notifUnreadCount > 0 && (
                  <span className="jx-mySpace-tabBadge">
                    {notifUnreadCount > 99 ? '99+' : notifUnreadCount}
                  </span>
                )}
              </button>
            ))}
          </div>
          <div className={`jx-mySpace-subHeader${tab === 'shares' ? ' jx-mySpace-subHeader-shares' : ''}`}>
            <p className="jx-mySpace-desc">{tabDescriptions[tab]}</p>
          </div>

          {tab === 'assets' && (
            <div className="jx-mySpace-assetBar">
              <div className="jx-mySpace-assetTabs">
                {[
                  { key: 'document', label: '文档' },
                  { key: 'image', label: '图片' },
                ].map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={`jx-mySpace-assetChip${assetFilter === item.key ? ' active' : ''}`}
                    onClick={() => setAssetFilter(item.key as 'document' | 'image')}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div className="jx-mySpace-filterTools">
                <Select
                  popupClassName="jx-mySpace-sourceFilterPopup"
                  className="jx-mySpace-sourceFilter"
                  value={sourceFilter}
                  onChange={(value) => setSourceFilter(value as 'all' | 'user_upload' | 'ai_generated')}
                  options={[
                    { value: 'all', label: '全部来源' },
                    { value: 'user_upload', label: '用户上传' },
                    { value: 'ai_generated', label: 'AI生成' },
                  ]}
                />
                <Input
                  className="jx-mySpace-search"
                  placeholder="搜索"
                  prefix={<SearchOutlined style={{ color: 'var(--color-text-placeholder)' }} />}
                  value={searchKeyword}
                  onChange={(e) => handleSearch(e.target.value)}
                  allowClear
                />
              </div>
            </div>
          )}
        </div>

        <div className="jx-mySpace-body" onScroll={tab === 'shares' || tab === 'notifications' ? undefined : handleScroll}>
          {tab === 'notifications' ? (
            <NotificationList />
          ) : tab === 'shares' ? (
            <ShareRecordsPage embedded hideEmbeddedDesc />
          ) : loading && currentItems.length === 0 ? (
            <div className="jx-mySpace-loading">
              <Spin />
            </div>
          ) : currentItems.length === 0 ? (
            <div className="jx-mySpace-empty">
              <div className="jx-mySpace-emptyIcon">
                <svg viewBox="0 0 48 48" width="48" height="48" fill="none">
                  <rect x="8" y="6" width="32" height="36" rx="4" stroke="var(--color-fill-deep)" strokeWidth="2" />
                  <path d="M16 18h16M16 26h10" stroke="var(--color-fill-deep)" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </div>
              <div className="jx-mySpace-emptyText">暂无内容</div>
            </div>
          ) : (
            <>
              {tab === 'assets' && (
                <>
                  {assetFilter === 'document' ? (
                    <DocumentList
                      items={resources}
                      onDownload={handleDownload}
                      onNavigate={handleNavigate}
                      onDelete={handleDelete}
                      onPreview={handlePreview}
                      onAddToKb={openKbPicker}
                    />
                  ) : (
                    <ImageGrid
                      items={resources}
                      onDownload={handleDownload}
                      onNavigate={handleNavigate}
                      onDelete={handleDelete}
                    />
                  )}
                </>
              )}

              {tab === 'favorites' && (
                <FavoriteList
                  items={currentItems}
                  onNavigate={handleNavigate}
                  onRequestUnfavorite={handleRequestUnfavorite}
                  onFinalizeUnfavorite={handleFinalizeUnfavorite}
                />
              )}

              {loading && currentItems.length > 0 && (
                <div className="jx-mySpace-loadMore"><Spin size="small" /></div>
              )}
              {!loading && currentHasMore && currentItems.length > 0 && (
                <div className="jx-mySpace-loadMore">
                  <Button onClick={() => void loadMore()}>加载更多</Button>
                </div>
              )}
              {!loading && !currentHasMore && currentItems.length > 0 && (
                <div className="jx-mySpace-noMore">已加载全部内容</div>
              )}
            </>
          )}
        </div>
      </div>
      <Modal
        title="加入私有知识库"
        open={kbPickerOpen}
        onCancel={closeKbPicker}
        footer={[
          <Button key="cancel" onClick={closeKbPicker} disabled={kbPickerLoading}>取消</Button>,
          <Button key="submit" type="primary" loading={kbPickerLoading} onClick={() => void handleAddToKb()}>
            确认加入
          </Button>,
        ]}
      >
        <div className="jx-mySpace-kbPicker">
          <div className="jx-mySpace-kbPickerText">
            {pendingResources.length > 1
              ? `选择一个或多个私有知识库，用于收录已选的 ${pendingResources.length} 个文件`
              : pendingResources[0]
                ? `选择一个或多个私有知识库，用于收录文件“${pendingResources[0].name}”`
                : '请选择目标私有知识库'}
          </div>
          <Select
            className="jx-mySpace-kbPickerSelect"
            mode="multiple"
            value={selectedKbIds}
            onChange={setSelectedKbIds}
            placeholder="请选择一个或多个私有知识库"
            options={privateKbOptions.map((item) => ({ value: item.id, label: item.name }))}
          />
        </div>
      </Modal>
    </div>
  );
}
