import { Button, Checkbox, Tag, Empty, Modal } from 'antd';
import { CheckOutlined, DeleteOutlined, CloseOutlined, EyeOutlined } from '@ant-design/icons';
import { useChatStore, useCatalogStore, useMySpaceStore, useAutomationChatStore } from '../../stores';

export function NotificationList() {
  const { setCurrentChatId } = useChatStore();
  const { setPanel } = useCatalogStore();

  const notifications = useMySpaceStore((s) => s.notifications);
  const notifSelectedIds = useMySpaceStore((s) => s.notifSelectedIds);
  const markNotificationRead = useMySpaceStore((s) => s.markNotificationRead);
  const markAllNotificationsRead = useMySpaceStore((s) => s.markAllNotificationsRead);
  const markSelectedNotificationsRead = useMySpaceStore((s) => s.markSelectedNotificationsRead);
  const deleteNotification = useMySpaceStore((s) => s.deleteNotification);
  const deleteSelectedNotifications = useMySpaceStore((s) => s.deleteSelectedNotifications);
  const toggleNotifSelected = useMySpaceStore((s) => s.toggleNotifSelected);
  const toggleNotifSelectAll = useMySpaceStore((s) => s.toggleNotifSelectAll);
  const clearNotifSelection = useMySpaceStore((s) => s.clearNotifSelection);

  if (notifications.length === 0) {
    return (
      <Empty
        description="暂无通知"
        style={{ marginTop: 60 }}
      />
    );
  }

  const unreadCount = notifications.filter((n) => !n.read).length;
  const hasSelection = notifSelectedIds.size > 0;
  const allSelected = notifications.length > 0 && notifications.every((n) => notifSelectedIds.has(n.id));
  const selectedHasUnread = hasSelection && notifications.some((n) => notifSelectedIds.has(n.id) && !n.read);

  const handleClick = (n: typeof notifications[number]) => {
    if (hasSelection) {
      toggleNotifSelected(n.id);
      return;
    }
    if (!n.read) {
      markNotificationRead(n.id);
    }
    if (n.chat_id) {
      if (useAutomationChatStore.getState().activeGroup) {
        useAutomationChatStore.getState().exitAutomationChat();
      }
      setCurrentChatId(n.chat_id);
      setPanel('chat');
    }
  };

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    Modal.confirm({
      title: '删除通知',
      content: '确定要删除这条通知吗？',
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => deleteNotification(id),
    });
  };

  const handleDeleteSelected = () => {
    Modal.confirm({
      title: '批量删除',
      content: `确定要删除选中的 ${notifSelectedIds.size} 条通知吗？`,
      okText: '全部删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => deleteSelectedNotifications(),
    });
  };

  return (
    <div className="jx-mySpace-notifList">
      <div className="jx-mySpace-notifActions">
        <Checkbox
          checked={allSelected}
          indeterminate={hasSelection && !allSelected}
          onChange={toggleNotifSelectAll}
        >
          <span className="jx-mySpace-notifActions-label">全选</span>
        </Checkbox>
        <div className="jx-mySpace-notifActions-right">
          {unreadCount > 0 && (
            <Button
              type="link"
              size="small"
              icon={<CheckOutlined />}
              onClick={markAllNotificationsRead}
            >
              全部标为已读
            </Button>
          )}
        </div>
      </div>

      {notifications.map((n) => (
        <div
          key={n.id}
          className={
            `jx-mySpace-notifCard`
            + (n.read ? '' : ' jx-mySpace-notifCard--unread')
            + (notifSelectedIds.has(n.id) ? ' jx-mySpace-notifCard--selected' : '')
          }
          onClick={() => handleClick(n)}
          role="button"
          tabIndex={0}
        >
          <div className="jx-mySpace-notifCard-left">
            {hasSelection ? (
              <Checkbox
                checked={notifSelectedIds.has(n.id)}
                onClick={(e) => e.stopPropagation()}
                onChange={() => toggleNotifSelected(n.id)}
              />
            ) : (
              !n.read && <span className="jx-mySpace-notifDot" />
            )}
          </div>
          <div className="jx-mySpace-notifCard-body">
            <div className="jx-mySpace-notifCard-header">
              <span className="jx-mySpace-notifCard-name">{n.task_name}</span>
              <Tag color={n.status === 'success' ? 'success' : 'error'}>
                {n.status === 'success' ? '成功' : '失败'}
              </Tag>
            </div>
            <div className="jx-mySpace-notifCard-summary">{n.summary}</div>
            <div className="jx-mySpace-notifCard-time">
              {new Date(n.timestamp).toLocaleString('zh-CN')}
            </div>
          </div>
          <button
            className="jx-mySpace-notifCard-deleteBtn"
            onClick={(e) => handleDelete(e, n.id)}
            title="删除通知"
          >
            <DeleteOutlined />
          </button>
        </div>
      ))}

      {hasSelection && (
        <div className="jx-mySpace-bulkBar">
          <span className="jx-mySpace-bulkBar-count">
            已选 {notifSelectedIds.size} 项
          </span>
          <div className="jx-mySpace-bulkBar-divider" />
          {selectedHasUnread && (
            <button
              className="jx-mySpace-bulkBar-btn"
              onClick={markSelectedNotificationsRead}
            >
              <EyeOutlined />
              <span>标为已读</span>
            </button>
          )}
          <button
            className="jx-mySpace-bulkBar-btn jx-mySpace-bulkBar-btn--danger"
            onClick={handleDeleteSelected}
          >
            <DeleteOutlined />
            <span>批量删除</span>
          </button>
          <button
            className="jx-mySpace-bulkBar-btn jx-mySpace-bulkBar-btn--cancel"
            onClick={clearNotifSelection}
          >
            <CloseOutlined />
            <span>取消</span>
          </button>
        </div>
      )}
    </div>
  );
}
