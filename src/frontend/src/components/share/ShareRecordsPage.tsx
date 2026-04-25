import { useEffect, useMemo, useState } from 'react';
import { Button, Input, Popconfirm, Popover, Tag, Tooltip, Typography, message } from 'antd';
import { CopyOutlined, ExportOutlined, EyeOutlined, InfoCircleOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { listChatShares, restoreChatShare, revokeChatShare, type ChatShareRecord } from '../../api';
import { useCatalogStore, useChatStore } from '../../stores';
import { formatDateTime } from '../../utils/date';

const SHARE_RECORDS_CACHE_KEY = 'jingxin_share_records_cache';

function formatShareExpiry(value?: string | null) {
  if (!value) return '长期';
  return formatDateTime(value, '--');
}

function getShareStatusLabel(record: ChatShareRecord) {
  return record.status === 'valid' ? '生效中' : '已失效';
}

function getShareExpiryLabel(record: ChatShareRecord) {
  if (record.expiry_option === '3d') return '有效期3天';
  if (record.expiry_option === '15d') return '有效期15天';
  if (record.expiry_option === '3m') return '有效期3个月';
  if (record.expiry_option === 'permanent' || !record.expires_at) return '长期有效';

  const createdAt = new Date(record.created_at).getTime();
  const expiresAt = new Date(record.expires_at).getTime();
  if (!Number.isNaN(createdAt) && !Number.isNaN(expiresAt)) {
    const diffDays = (expiresAt - createdAt) / (24 * 60 * 60 * 1000);
    if (diffDays <= 3.5) return '有效期3天';
    if (diffDays <= 15.5) return '有效期15天';
    if (diffDays <= 95) return '有效期3个月';
  }

  return '有效期';
}

function isShareWithinExpiry(expiresAt?: string | null) {
  if (!expiresAt) return true;
  const ts = new Date(expiresAt).getTime();
  return !Number.isNaN(ts) && ts > Date.now();
}

function loadCachedShareRecords(): ChatShareRecord[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(SHARE_RECORDS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveCachedShareRecords(records: ChatShareRecord[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(SHARE_RECORDS_CACHE_KEY, JSON.stringify(records));
  } catch {
    // ignore cache write errors
  }
}

function copyTextFallback(text: string) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  textarea.style.pointerEvents = 'none';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  const copied = document.execCommand('copy');
  document.body.removeChild(textarea);
  return copied;
}

interface ShareRecordsPageProps {
  embedded?: boolean;
  hideEmbeddedDesc?: boolean;
}

export default function ShareRecordsPage({ embedded = false, hideEmbeddedDesc = false }: ShareRecordsPageProps) {
  const [records, setRecords] = useState<ChatShareRecord[]>(() => loadCachedShareRecords());
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [messageApi, contextHolder] = message.useMessage();
  const loadingCardCount = useMemo(() => {
    if (records.length > 0) return Math.min(Math.max(records.length, 1), 4);
    return embedded ? 3 : 4;
  }, [embedded, records.length]);
  const loadingCards = useMemo(
    () => Array.from({ length: loadingCardCount }, (_, index) => index),
    [loadingCardCount],
  );

  const handleJumpToOriginChat = (record: ChatShareRecord) => {
    useCatalogStore.getState().setPanel('chat');
    useChatStore.getState().setCurrentChatId(record.chat_id);
    useChatStore.getState().setPendingScrollMessageTs(record.origin_message_ts ?? null);
  };

  const loadRecords = async (options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false;
    if (!silent) setLoading(true);
    try {
      const items = await listChatShares();
      setRecords(items);
      saveCachedShareRecords(items);
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '加载分享记录失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRecords();
  }, []);

  const sortedRecords = useMemo(
    () => [...records].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [records],
  );

  const filteredRecords = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    if (!query) return sortedRecords;
    return sortedRecords.filter((record) => (
      `${record.title}`.toLowerCase().includes(query)
      || `${record.share_id}`.toLowerCase().includes(query)
    ));
  }, [keyword, sortedRecords]);

  const handleCopyLink = async (previewUrl: string) => {
    const targetUrl = new URL(previewUrl, window.location.origin).toString();
    try {
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(targetUrl);
      } else {
        const copied = copyTextFallback(targetUrl);
        if (!copied) throw new Error('copy_failed');
      }
      messageApi.success('分享链接已复制');
    } catch {
      const copied = copyTextFallback(targetUrl);
      if (copied) {
        messageApi.success('分享链接已复制');
        return;
      }
      messageApi.error('复制失败，请手动复制');
    }
  };

  const handleRevoke = async (shareId: string) => {
    try {
      await revokeChatShare(shareId);
      setRecords((current) => {
        const next = current.map((record) => (
          record.share_id === shareId
            ? { ...record, status: 'expired' as const, revoked: true }
            : record
        ));
        saveCachedShareRecords(next);
        return next;
      });
      messageApi.success('访问已终止');
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '终止访问失败');
    }
  };

  const handleRestore = async (shareId: string) => {
    try {
      await restoreChatShare(shareId);
      setRecords((current) => {
        const next = current.map((record) => (
          record.share_id === shareId
            ? { ...record, status: 'valid' as const, revoked: false }
            : record
        ));
        saveCachedShareRecords(next);
        return next;
      });
      messageApi.success('访问已启用');
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '启用访问失败');
    }
  };

  return (
    <div className={`jx-shareRecordsPage${embedded ? ' embedded' : ''}`}>
      {contextHolder}
      <div className={`jx-shareRecordsHead${embedded && hideEmbeddedDesc ? ' noDesc' : ''}`}>
        <div className="jx-shareRecordsHeadMain">
          {!embedded && <h2 className="jx-shareRecordsTitle">分享记录</h2>}
          {!(embedded && hideEmbeddedDesc) && (
            <p className="jx-shareRecordsDesc">查看并管理已生成的分享链接与有效状态，查看浏览量</p>
          )}
        </div>
        <div className="jx-shareRecordsToolbar">
          <Input
            allowClear
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索标题关键词/分享ID"
            prefix={<SearchOutlined />}
            className="jx-shareRecordsSearch"
          />
          <Button icon={<ReloadOutlined />} onClick={() => void loadRecords()} disabled={loading}>
            刷新
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="jx-shareRecordsLoading">
          {loadingCards.map((item) => (
            <div key={item} className="jx-shareRecordCard jx-shareRecordCardSkeleton" aria-hidden="true">
              <div className="jx-shareRecordTop">
                <div className="jx-shareRecordMain">
                  <div className="jx-shareRecordTitleRow">
                    <div className="jx-skeletonBlock jx-shareSkTitle" />
                    <div className="jx-skeletonBlock jx-shareSkTag" />
                    <div className="jx-skeletonBlock jx-shareSkTag jx-shareSkTagWide" />
                  </div>
                  <div className="jx-shareRecordMeta">
                    <div className="jx-skeletonBlock jx-shareSkMeta" />
                    <div className="jx-skeletonBlock jx-shareSkMeta" />
                    <div className="jx-skeletonBlock jx-shareSkMeta jx-shareSkMetaShort" />
                  </div>
                </div>
                <div className="jx-shareRecordSide jx-shareRecordSideSkeleton">
                  <div className="jx-skeletonBlock jx-shareSkAction" />
                  <div className="jx-skeletonBlock jx-shareSkAction" />
                  <div className="jx-shareRecordViewsRow">
                    <div className="jx-skeletonBlock jx-shareSkViews" />
                    <div className="jx-skeletonBlock jx-shareSkTextBtn" />
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : records.length === 0 ? (
        <div className="jx-shareRecordsEmpty">
          <Typography.Text type="secondary">暂无分享记录</Typography.Text>
        </div>
      ) : filteredRecords.length === 0 ? (
        <div className="jx-shareRecordsEmpty">
          <Typography.Text type="secondary">没有匹配的分享记录</Typography.Text>
        </div>
      ) : (
        <div className="jx-shareRecordsList">
          {filteredRecords.map((record) => (
            <div key={record.share_id} className="jx-shareRecordCard">
              <div className="jx-shareRecordTop">
                <div className="jx-shareRecordMain">
                  <div className="jx-shareRecordTitleRow">
                    <button
                      type="button"
                      className="jx-shareRecordTitleBtn"
                      onClick={() => window.open(record.preview_url, '_blank', 'noopener,noreferrer')}
                    >
                      <span className="jx-shareRecordTitle">{record.title || '未命名分享'}</span>
                    </button>
                    <Tag color={record.status === 'valid' ? 'green' : 'default'}>
                      {getShareStatusLabel(record)}
                    </Tag>
                    <Tag style={{ background: '#EBF2FF', borderColor: '#DBE9FF', color: '#126DFF' }}>
                      {getShareExpiryLabel(record)}
                    </Tag>
                    <button
                      type="button"
                      className="jx-shareRecordJumpBtn"
                      onClick={() => handleJumpToOriginChat(record)}
                      title="跳转关联会话记录"
                    >
                      <img src="/home/share-link-gray.svg" alt="跳转关联会话记录" className="jx-shareRecordJumpIcon" />
                    </button>
                  </div>
                  <div className="jx-shareRecordMeta">
                    <span>{`链接生成：${formatDateTime(record.created_at, '--')}`}</span>
                    <span>{`有效期至：${formatShareExpiry(record.expires_at)}`}</span>
                    <Popover
                      trigger="click"
                      placement="bottomLeft"
                      overlayClassName="jx-shareRecordIdPopover"
                      content={<span className="jx-shareRecordIdValue">{record.share_id}</span>}
                    >
                      <button type="button" className="jx-shareRecordMetaBtn">
                        <span>分享ID</span>
                        <InfoCircleOutlined />
                      </button>
                    </Popover>
                  </div>
                </div>
                <div className="jx-shareRecordSide">
                  <Button
                    icon={<CopyOutlined />}
                    onClick={() => void handleCopyLink(record.preview_url)}
                    className="jx-shareRecordActionCopy"
                  >
                    复制链接
                  </Button>
                  <Button
                    icon={<ExportOutlined />}
                    onClick={() => window.open(record.preview_url, '_blank', 'noopener,noreferrer')}
                    className="jx-shareRecordActionPreview"
                  >
                    打开预览
                  </Button>
                  <div className="jx-shareRecordViewsRow">
                    <Tooltip title="总浏览量">
                      <span className="jx-shareRecordViewsMain" aria-label={`浏览 ${record.view_count ?? 0} 次`}>
                        <EyeOutlined />
                        <span>{record.view_count ?? 0}</span>
                      </span>
                    </Tooltip>
                    {record.status === 'valid' ? (
                      <Popconfirm
                        title="确认终止该访问？"
                        description="终止后，该链接将立即失效，无法继续访问"
                        okText="确认"
                        cancelText="取消"
                        onConfirm={() => void handleRevoke(record.share_id)}
                      >
                        <button type="button" className="jx-shareRecordTerminateBtn">
                          终止访问
                        </button>
                      </Popconfirm>
                    ) : record.revoked && isShareWithinExpiry(record.expires_at) ? (
                      <button type="button" className="jx-shareRecordTerminateBtn" onClick={() => void handleRestore(record.share_id)}>
                        启用访问
                      </button>
                    ) : (
                      <span />
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
