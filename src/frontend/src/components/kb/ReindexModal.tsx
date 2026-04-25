import { Modal, Select, Typography, message } from 'antd';
import { useKbStore } from '../../stores';
import { reindexKBDocument } from '../../api';

const CHUNK_METHOD_OPTIONS = [
  { value: 'structured', label: '结构感知（按标题和段落）' },
  { value: 'recursive', label: '递归分块（多级分隔符）' },
  { value: 'embedding_semantic', label: '语义分块（基于嵌入相似度）⭐ 推荐' },
  { value: 'laws', label: '法律文书' },
  { value: 'qa', label: '问答对' },
];

export default function ReindexModal() {
  const {
    reindexModalOpen,
    reindexChunkMethod,
    reindexDocId,
    reindexKbId,
    reindexLoading,
    closeReindexModal,
    setReindexChunkMethod,
    setReindexLoading,
    activeKbDoc,
    setActiveKbDoc,
    setKbDocumentsMap,
  } = useKbStore();

  return (
    <Modal
      title="重新索引文档"
      open={reindexModalOpen}
      onCancel={closeReindexModal}
      confirmLoading={reindexLoading}
      okText="开始索引"
      cancelText="取消"
      onOk={async () => {
        if (!reindexKbId || !reindexDocId) return;
        setReindexLoading(true);
        try {
          await reindexKBDocument(reindexKbId, reindexDocId, undefined, reindexChunkMethod);
          message.success('重新索引已启动');
          if (activeKbDoc?.id === reindexDocId) {
            setActiveKbDoc({ ...activeKbDoc, indexing_status: 'processing' });
          }
          setKbDocumentsMap(prev => {
            const docs = prev[reindexKbId!];
            if (!Array.isArray(docs)) return prev;
            return { ...prev, [reindexKbId!]: docs.map(d => d.id === reindexDocId ? { ...d, indexing_status: 'processing' } : d) };
          });
          closeReindexModal();
        } catch (err: any) {
          message.error(err.message || '重新索引失败');
        } finally {
          setReindexLoading(false);
        }
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          将删除现有分块并使用所选方法重新解析索引。
        </Typography.Text>
        <div>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#808080' }}>分块方法</div>
          <Select
            value={reindexChunkMethod}
            onChange={setReindexChunkMethod}
            style={{ width: '100%' }}
            options={CHUNK_METHOD_OPTIONS}
          />
        </div>
      </div>
    </Modal>
  );
}
