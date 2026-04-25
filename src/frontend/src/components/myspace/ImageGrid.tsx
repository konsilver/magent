import { useCallback } from 'react';
import { DownloadOutlined, ExportOutlined, DeleteOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';
import type { ResourceItem } from '../../types';
import { useUIStore } from '../../stores';
import { buildFileUrl } from '../../utils/constants';
import { confirmDelete } from '../../utils/confirmDelete';

interface ImageGridProps {
  items: ResourceItem[];
  onDownload: (item: ResourceItem) => void;
  onNavigate: (item: ResourceItem) => void;
  onDelete?: (item: ResourceItem) => void;
}

function ImageThumbnail({ item }: { item: ResourceItem }) {
  if (!item.file_id) {
    return (
      <div className="jx-mySpace-imgCell">
        <div className="jx-mySpace-imgPlaceholder">无文件</div>
      </div>
    );
  }

  return (
    <div className="jx-mySpace-imgCell">
      <img
        src={buildFileUrl(item.file_id)}
        alt={item.name}
        className="jx-mySpace-imgThumb"
        loading="lazy"
        onError={(e) => {
          const el = e.currentTarget;
          el.style.display = 'none';
          el.parentElement?.classList.add('jx-mySpace-imgCell--error');
        }}
      />
    </div>
  );
}

export function ImageGrid({ items, onDownload, onNavigate, onDelete }: ImageGridProps) {
  const { setPreviewImage } = useUIStore();

  const handlePreview = useCallback((item: ResourceItem) => {
    if (!item.file_id) return;
    setPreviewImage({ url: buildFileUrl(item.file_id), name: item.name });
  }, [setPreviewImage]);

  const handleDelete = useCallback((item: ResourceItem) => {
    confirmDelete(item.name, () => onDelete?.(item), '图片');
  }, [onDelete]);

  if (items.length === 0) return null;

  return (
    <div className="jx-mySpace-imgGrid">
      {items.map((item) => (
        <div key={item.id} className="jx-mySpace-imgItem" onClick={() => handlePreview(item)}>
          <ImageThumbnail item={item} />
          <div className="jx-mySpace-imgOverlay">
            <Tooltip title="下载">
              <button className="jx-mySpace-actionBtn" onClick={(e) => { e.stopPropagation(); onDownload(item); }}>
                <DownloadOutlined />
              </button>
            </Tooltip>
            {item.source_chat_id && (
              <Tooltip title="跳转到对话">
                <button className="jx-mySpace-actionBtn" onClick={(e) => { e.stopPropagation(); onNavigate(item); }}>
                  <ExportOutlined />
                </button>
              </Tooltip>
            )}
            {onDelete && (
              <Tooltip title="删除">
                <button className="jx-mySpace-actionBtn jx-mySpace-actionBtn--danger" onClick={(e) => { e.stopPropagation(); handleDelete(item); }}>
                  <DeleteOutlined />
                </button>
              </Tooltip>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
