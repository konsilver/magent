import { ExportOutlined, StarOutlined } from '@ant-design/icons';
import { useEffect, useRef, useState } from 'react';
import { Tooltip } from 'antd';
import type { ResourceItem } from '../../types';
import { formatDateTime } from '../../utils/date';

interface FavoriteListProps {
  items: ResourceItem[];
  onNavigate: (item: ResourceItem) => void;
  onRequestUnfavorite: (item: ResourceItem) => Promise<boolean | void>;
  onFinalizeUnfavorite: (item: ResourceItem) => void;
}

const REMOVE_ANIMATION_MS = 500;

export function FavoriteList({
  items,
  onNavigate,
  onRequestUnfavorite,
  onFinalizeUnfavorite,
}: FavoriteListProps) {
  const [removingIds, setRemovingIds] = useState<string[]>([]);
  const timersRef = useRef<number[]>([]);

  useEffect(() => () => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
    timersRef.current = [];
  }, []);

  async function handleUnfavorite(item: ResourceItem) {
    if (removingIds.includes(item.id)) return;
    const confirmed = await onRequestUnfavorite(item);
    if (!confirmed) return;

    setRemovingIds((prev) => [...prev, item.id]);
    const timer = window.setTimeout(() => {
      onFinalizeUnfavorite(item);
      setRemovingIds((prev) => prev.filter((id) => id !== item.id));
      timersRef.current = timersRef.current.filter((id) => id !== timer);
    }, REMOVE_ANIMATION_MS);
    timersRef.current.push(timer);
  }

  if (items.length === 0) return null;

  return (
    <div className="jx-mySpace-favList">
      {items.map((item) => {
        const isRemoving = removingIds.includes(item.id);
        const isAutomationFavorite = item.source_chat_id?.startsWith('automation:');
        return (
          <div
            key={item.id}
            className={`jx-mySpace-favCard${isRemoving ? ' jx-mySpace-favCard--removing' : ''}`}
          >
            <div className="jx-mySpace-favHeader">
              <span className="jx-mySpace-favSource">
                来自「{item.source_chat_title || '对话'}」
              </span>
              <span className="jx-mySpace-favTime">{formatDateTime(item.created_at, '')}</span>
            </div>
            {item.content_preview && (
              <div className="jx-mySpace-favPreview">
                {item.content_preview}
              </div>
            )}
            <div className="jx-mySpace-favActions">
              <Tooltip title="取消收藏">
                <button className="jx-mySpace-actionBtn jx-mySpace-actionBtn--danger" onClick={() => void handleUnfavorite(item)}>
                  <StarOutlined /> 取消收藏
                </button>
              </Tooltip>
              <Tooltip title={isAutomationFavorite ? '查看自动化记录' : '跳转到对话'}>
                <button className="jx-mySpace-actionBtn" onClick={() => onNavigate(item)}>
                  <ExportOutlined /> {isAutomationFavorite ? '查看记录' : '查看对话'}
                </button>
              </Tooltip>
            </div>
          </div>
        );
      })}
    </div>
  );
}
