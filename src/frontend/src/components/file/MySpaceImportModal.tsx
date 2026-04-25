import { useState, useEffect, useCallback } from 'react';
import { Modal, Tabs, Checkbox, Input, Empty, Spin, Button } from 'antd';
import { SearchOutlined, FileOutlined, PictureOutlined } from '@ant-design/icons';
import { getArtifacts, getApiUrl } from '../../api';
import { useFileStore } from '../../stores';
import type { ResourceItem } from '../../types';
import type { ImportedSpaceFile } from '../../stores/fileStore';
import { getFileIconSrc } from '../../utils/fileIcon';

interface MySpaceImportModalProps {
  open: boolean;
  onClose: () => void;
}

const IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp', 'image/bmp', 'image/svg+xml']);

function isImageItem(item: ResourceItem) {
  return item.type === 'image' || (item.mime_type ? IMAGE_MIMES.has(item.mime_type) : false);
}

function formatSize(bytes?: number) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(dateStr: string) {
  try {
    const d = new Date(dateStr);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  } catch {
    return dateStr;
  }
}

interface FileListProps {
  items: ResourceItem[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}

function FileList({ items, selected, onToggle }: FileListProps) {
  if (items.length === 0) {
    return <Empty description="暂无文件" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ margin: '32px 0' }} />;
  }
  return (
    <div className="jx-spaceImportList">
      {items.map((item) => (
        <div
          key={item.id}
          className={`jx-spaceImportItem${selected.has(item.id) ? ' selected' : ''}`}
          onClick={() => onToggle(item.id)}
        >
          <Checkbox
            checked={selected.has(item.id)}
            onChange={() => onToggle(item.id)}
            onClick={(e) => e.stopPropagation()}
          />
          <div className="jx-spaceImportItem-icon">
            {isImageItem(item) ? (
              (item.download_url || item.file_id) ? (
                <img src={`${getApiUrl()}${item.download_url || `/files/${item.file_id}`}`} alt={item.name} className="jx-spaceImportItem-thumb" />
              ) : (
                <PictureOutlined style={{ fontSize: 22, color: 'var(--color-primary)' }} />
              )
            ) : (
              <img src={getFileIconSrc(item.name)} width={24} height={24} alt="" />
            )}
          </div>
          <div className="jx-spaceImportItem-info">
            <div className="jx-spaceImportItem-name" title={item.name}>{item.name}</div>
            <div className="jx-spaceImportItem-meta">
              {item.size ? <span>{formatSize(item.size)}</span> : null}
              <span>{formatDate(item.created_at)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function MySpaceImportModal({ open, onClose }: MySpaceImportModalProps) {
  const [allItems, setAllItems] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState<'all' | 'document' | 'image'>('all');

  const { addImportedSpaceFiles } = useFileStore();

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getArtifacts({ page: 1, page_size: 100 });
      setAllItems(res.items || []);
    } catch (e) {
      console.error('Failed to load My Space files:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setSelected(new Set());
      setKeyword('');
      setActiveTab('all');
      void fetchItems();
    }
  }, [open, fetchItems]);

  const filteredItems = allItems.filter((item) => {
    if (keyword && !item.name.toLowerCase().includes(keyword.toLowerCase())) return false;
    if (activeTab === 'document') return !isImageItem(item);
    if (activeTab === 'image') return isImageItem(item);
    return true;
  });

  const toggleItem = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const resolveDownloadUrl = (item: ResourceItem) => {
    return item.download_url || (item.file_id ? `/files/${item.file_id}` : '');
  };

  const handleConfirm = () => {
    const toImport: ImportedSpaceFile[] = allItems
      .filter((item) => selected.has(item.id) && item.file_id)
      .map((item) => ({
        name: item.name,
        file_id: item.file_id!,
        download_url: resolveDownloadUrl(item),
        mime_type: item.mime_type || (isImageItem(item) ? 'image/png' : 'application/octet-stream'),
        type: isImageItem(item) ? 'image' : 'document',
      }));
    if (toImport.length > 0) {
      addImportedSpaceFiles(toImport);
    }
    onClose();
  };

  const docCount = allItems.filter((i) => !isImageItem(i)).length;
  const imgCount = allItems.filter((i) => isImageItem(i)).length;

  return (
    <Modal
      title="从我的空间导入"
      open={open}
      onCancel={onClose}
      width={560}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: 'var(--color-text-tertiary)' }}>
            {selected.size > 0 ? `已选 ${selected.size} 个文件` : '请选择要导入的文件'}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" onClick={handleConfirm} disabled={selected.size === 0}>
              确认导入
            </Button>
          </div>
        </div>
      }
      className="jx-spaceImportModal"
      destroyOnClose
    >
      <Input
        placeholder="搜索文件名"
        prefix={<SearchOutlined />}
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        style={{ marginBottom: 12 }}
        allowClear
      />
      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as 'all' | 'document' | 'image')}
        size="small"
        items={[
          {
            key: 'all',
            label: `全部 (${allItems.length})`,
            children: (
              <Spin spinning={loading}>
                <FileList items={filteredItems} selected={selected} onToggle={toggleItem} />
              </Spin>
            ),
          },
          {
            key: 'document',
            label: (
              <span><FileOutlined style={{ marginRight: 4 }} />文档 ({docCount})</span>
            ),
            children: (
              <Spin spinning={loading}>
                <FileList items={filteredItems} selected={selected} onToggle={toggleItem} />
              </Spin>
            ),
          },
          {
            key: 'image',
            label: (
              <span><PictureOutlined style={{ marginRight: 4 }} />图片 ({imgCount})</span>
            ),
            children: (
              <Spin spinning={loading}>
                <FileList items={filteredItems} selected={selected} onToggle={toggleItem} />
              </Spin>
            ),
          },
        ]}
      />
    </Modal>
  );
}
