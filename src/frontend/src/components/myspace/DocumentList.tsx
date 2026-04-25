import { useState, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Checkbox, Modal } from 'antd';
import type { ResourceItem } from '../../types';
import { ResourceCard } from './ResourceCard';

interface DocumentListProps {
  items: ResourceItem[];
  onDownload: (item: ResourceItem) => void;
  onNavigate: (item: ResourceItem) => void;
  onDelete: (item: ResourceItem) => void;
  onPreview?: (item: ResourceItem) => void;
  onAddToKb?: (items: ResourceItem[]) => void;
}

export function DocumentList({ items, onDownload, onNavigate, onDelete, onPreview, onAddToKb }: DocumentListProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    setSelectedIds((prev) => {
      const next = new Set<string>();
      items.forEach((item) => {
        if (prev.has(item.id)) next.add(item.id);
      });
      return next;
    });
  }, [items]);

  const allSelected = items.length > 0 && items.every((i) => selectedIds.has(i.id));
  const someSelected = items.some((i) => selectedIds.has(i.id));
  const anySelected = someSelected;
  const selectedCount = selectedIds.size;

  const handleCheckItem = useCallback((id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const handleSelectAll = useCallback((checked: boolean) => {
    if (checked) {
      setSelectedIds(new Set(items.map((i) => i.id)));
    } else {
      setSelectedIds(new Set());
    }
  }, [items]);

  const selectedItems = items.filter((i) => selectedIds.has(i.id));

  const handleBulkDownload = () => {
    selectedItems.forEach((item) => {
      if (item.file_id) onDownload(item);
    });
  };

  const handleBulkAddToKb = () => {
    const validItems = selectedItems.filter((item) => item.file_id);
    if (validItems.length > 0) {
      onAddToKb?.(validItems);
    }
  };

  const handleBulkDelete = () => {
    Modal.confirm({
      title: `确认删除 ${selectedCount} 个文件`,
      content: `确定要删除选中的 ${selectedCount} 个文件吗？此操作不可撤销。`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => {
        selectedItems.forEach((item) => onDelete(item));
        setSelectedIds(new Set());
      },
    });
  };

  if (items.length === 0) return null;

  return (
    <>
      <div className={`jx-mySpace-docTable${anySelected ? ' jx-mySpace-docTable--hasSelection' : ''}`}>
        {/* Table header */}
        <div className="jx-mySpace-docTable-header">
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--check">
            <Checkbox
              checked={allSelected}
              indeterminate={someSelected && !allSelected}
              onChange={(e) => handleSelectAll(e.target.checked)}
            />
          </div>
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--name">名称</div>
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--size">大小</div>
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--source">来源</div>
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--time">最近更新</div>
          <div className="jx-mySpace-docTable-col jx-mySpace-docTable-col--actions" />
        </div>

        {/* Rows */}
        {items.map((item) => (
          <ResourceCard
            key={item.id}
            item={item}
            checked={selectedIds.has(item.id)}
            anySelected={anySelected}
            onCheck={(checked) => handleCheckItem(item.id, checked)}
            onDownload={onDownload}
            onNavigate={onNavigate}
            onDelete={onDelete}
            onPreview={onPreview}
            onAddToKb={onAddToKb ? (resource) => onAddToKb([resource]) : undefined}
          />
        ))}
      </div>

      {/* Bulk action bar — floats at bottom of viewport via Portal */}
      {anySelected && createPortal(
        <div className="jx-mySpace-bulkBar">
          <span className="jx-mySpace-bulkBar-count">已选 {selectedCount} 项</span>
          <div className="jx-mySpace-bulkBar-divider" />
          {onAddToKb && (
            <button type="button" className="jx-mySpace-bulkBar-btn" onClick={handleBulkAddToKb}>
              <span>加入知识库</span>
            </button>
          )}
          <button type="button" className="jx-mySpace-bulkBar-btn" onClick={handleBulkDownload}>
            <span>下载</span>
          </button>
          <button type="button" className="jx-mySpace-bulkBar-btn jx-mySpace-bulkBar-btn--danger" onClick={handleBulkDelete}>
            <span>删除</span>
          </button>
          <div className="jx-mySpace-bulkBar-divider" />
          <button
            type="button"
            className="jx-mySpace-bulkBar-btn jx-mySpace-bulkBar-btn--cancel"
            onClick={() => setSelectedIds(new Set())}
          >
            取消
          </button>
        </div>,
        document.body,
      )}
    </>
  );
}
