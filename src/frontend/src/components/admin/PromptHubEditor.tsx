import { useState, useEffect } from 'react';
import { Button, Form, Input, Modal, Popconfirm, Space, Table, Typography, Upload, message } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ArrowUpOutlined, ArrowDownOutlined, ExportOutlined, ImportOutlined } from '@ant-design/icons';
import { fetchContent, saveBlock, move } from '../../utils/adminApi';

const { Text } = Typography;
const { TextArea } = Input;

interface PromptItem {
  title: string;
  content: string;
  sort_order: number;
}

export function PromptHubEditor({ token }: { token: string }) {
  const [items, setItems] = useState<PromptItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [form] = Form.useForm<{ title: string; content: string }>();

  useEffect(() => {
    fetchContent(token)
      .then((data) => {
        const list: PromptItem[] = data?.data?.prompt_hub || [];
        list.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
        setItems(list);
      })
      .catch(() => message.error('加载失败'))
      .finally(() => setLoading(false));
  }, [token]);

  const openAdd = () => {
    setEditIndex(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (i: number) => {
    setEditIndex(i);
    form.setFieldsValue({ title: items[i].title, content: items[i].content });
    setModalOpen(true);
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      const item: PromptItem = {
        title: values.title,
        content: values.content,
        sort_order: editIndex === null ? items.length : items[editIndex].sort_order,
      };
      const next =
        editIndex === null
          ? [...items, item]
          : items.map((it, i) => (i === editIndex ? item : it));
      setItems(next);
      setModalOpen(false);
    });
  };

  const handleDelete = (i: number) => setItems(items.filter((_, idx) => idx !== i));

  const handleMove = (from: number, to: number) => {
    const next = move(items, from, to).map((it, idx) => ({ ...it, sort_order: idx }));
    setItems(next);
  };

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `prompt-hub-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    message.success('已导出');
  };

  const handleImport = (file: File) => {
    file.text().then(text => {
      try {
        const data = JSON.parse(text);
        if (!Array.isArray(data)) { message.error('JSON 格式错误：需要数组'); return; }
        setItems(data);
        message.success(`已导入 ${data.length} 条提示词`);
      } catch { message.error('JSON 解析失败'); }
    });
    return false;
  };

  const handlePublish = async () => {
    setSaving(true);
    try {
      const payload = items.map((it, idx) => ({ ...it, sort_order: idx }));
      await saveBlock(token, 'prompt_hub', payload);
      message.success('提示词中心已保存');
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    {
      title: '#',
      width: 40,
      render: (_: unknown, __: PromptItem, i: number) => <Text type="secondary">{i + 1}</Text>,
    },
    { title: '标题', dataIndex: 'title', width: 200 },
    { title: '提示词内容', dataIndex: 'content', ellipsis: true },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, __: PromptItem, i: number) => (
        <Space size={4}>
          <Button size="small" icon={<ArrowUpOutlined />} onClick={() => handleMove(i, i - 1)} disabled={i === 0} />
          <Button size="small" icon={<ArrowDownOutlined />} onClick={() => handleMove(i, i + 1)} disabled={i === items.length - 1} />
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(i)} />
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(i)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Text strong>共 {items.length} 条提示词</Text>
        <Space>
          <Button icon={<PlusOutlined />} onClick={openAdd}>新增提示词</Button>
          <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
          <Upload accept=".json" showUploadList={false} beforeUpload={handleImport}>
            <Button icon={<ImportOutlined />}>导入</Button>
          </Upload>
          <Button type="primary" loading={saving} onClick={handlePublish}>保存并发布</Button>
        </Space>
      </div>

      <Table
        dataSource={items}
        columns={columns}
        rowKey={(_, i) => String(i)}
        size="small"
        loading={loading}
        pagination={false}
      />

      <Modal
        title={editIndex === null ? '新增提示词' : '编辑提示词'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={600}
        okText="确认"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item label="标题" name="title" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="例：政策解读（知识库优先）" />
          </Form.Item>
          <Form.Item label="提示词内容" name="content" rules={[{ required: true, message: '请输入提示词内容' }]}>
            <TextArea rows={6} placeholder="请基于内部知识库，解读【政策名称】对..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
