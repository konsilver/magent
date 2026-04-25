import { Modal, Input, Typography, message } from 'antd';
import { useKbStore } from '../../stores';
import { createKBSpace } from '../../api';

interface CreateKBModalProps {
  onCreated?: () => void;
}

export default function CreateKBModal({ onCreated }: CreateKBModalProps) {
  const {
    createKBModalOpen,
    createKBName,
    createKBDesc,
    createKBLoading,
    closeCreateKBModal,
    setCreateKBName,
    setCreateKBDesc,
    setCreateKBLoading,
  } = useKbStore();

  return (
    <Modal
      title="新增私有知识库"
      open={createKBModalOpen}
      onCancel={closeCreateKBModal}
      confirmLoading={createKBLoading}
      okText="创建"
      cancelText="取消"
      onOk={async () => {
        if (!createKBName.trim()) { message.warning('请输入知识库名称'); return; }
        setCreateKBLoading(true);
        try {
          await createKBSpace(createKBName.trim(), createKBDesc.trim() || undefined);
          message.success('知识库创建成功');
          closeCreateKBModal();
          onCreated?.();
        } catch (err: any) {
          message.error(err.message || '创建失败');
        } finally {
          setCreateKBLoading(false);
        }
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, paddingTop: 8 }}>
        <div>
          <div style={{ marginBottom: 4, fontWeight: 600, fontSize: 13 }}>名称 <span style={{ color: 'red' }}>*</span></div>
          <Input placeholder="知识库名称" value={createKBName} onChange={(e) => setCreateKBName(e.target.value)} maxLength={255} />
        </div>
        <div>
          <div style={{ marginBottom: 4, fontWeight: 600, fontSize: 13 }}>描述</div>
          <Input.TextArea
            placeholder="知识库描述（可选）"
            value={createKBDesc}
            onChange={(e) => setCreateKBDesc(e.target.value)}
            rows={3}
            maxLength={150}
            showCount
          />
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          分块方法可在上传文档时逐文件选择
        </Typography.Text>
      </div>
    </Modal>
  );
}
