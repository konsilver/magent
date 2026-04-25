import { Modal } from 'antd';

export function confirmDelete(name: string, onOk: () => void | Promise<void>, kind = ''): void {
  Modal.confirm({
    title: '确认删除',
    content: `确定要删除${kind}「${name}」吗？此操作不可撤销。`,
    okText: '删除',
    cancelText: '取消',
    okButtonProps: { danger: true },
    onOk,
  });
}
