import { useState, useEffect } from 'react';
import { Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, Upload, message } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ArrowUpOutlined, ArrowDownOutlined, ExportOutlined, ImportOutlined } from '@ant-design/icons';
import { fetchContent, saveBlock, move } from '../../utils/adminApi';
import type { UpdateEntry, UpdateCategory } from '../../types';

const { Text } = Typography;
const { TextArea } = Input;

const CATEGORIES: UpdateCategory[] = ['模型迭代', '信息处理', '应用上新', '体验优化'];

export function UpdatesEditor({ token }: { token: string }) {
  const [items, setItems] = useState<UpdateEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [form] = Form.useForm<UpdateEntry>();

  useEffect(() => {
    fetchContent(token)
      .then(data => {
        if (Array.isArray(data?.data?.updates)) setItems(data.data.updates as UpdateEntry[]);
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
    form.setFieldsValue(items[i]);
    setModalOpen(true);
  };

  const handleSave = () => {
    form.validateFields().then(values => {
      const next = editIndex === null
        ? [values, ...items]
        : items.map((it, i) => (i === editIndex ? values : it));
      setItems(next);
      setModalOpen(false);
    });
  };

  const handleDelete = (i: number) => {
    setItems(items.filter((_, idx) => idx !== i));
  };

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `updates-${new Date().toISOString().slice(0, 10)}.json`;
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
        message.success(`已导入 ${data.length} 条记录`);
      } catch { message.error('JSON 解析失败'); }
    });
    return false;
  };

  const handlePublish = async () => {
    setSaving(true);
    try {
      await saveBlock(token, 'updates', items);
      message.success('功能更新已保存');
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    {
      title: '日期',
      width: 90,
      render: (_: unknown, r: UpdateEntry) => <Text style={{ fontSize: 13 }}>{r.date}<br /><Text type="secondary" style={{ fontSize: 11 }}>{r.year}</Text></Text>,
    },
    { title: '分类', dataIndex: 'category', width: 90, render: (v: string) => <Tag>{v}</Tag> },
    { title: '标题', dataIndex: 'title', ellipsis: true },
    {
      title: '操作',
      width: 140,
      render: (_: unknown, __: UpdateEntry, i: number) => (
        <Space size={4}>
          <Button size="small" icon={<ArrowUpOutlined />} onClick={() => setItems(move(items, i, i - 1))} disabled={i === 0} />
          <Button size="small" icon={<ArrowDownOutlined />} onClick={() => setItems(move(items, i, i + 1))} disabled={i === items.length - 1} />
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
        <Text strong>共 {items.length} 条记录</Text>
        <Space>
          <Button icon={<PlusOutlined />} onClick={openAdd}>新增条目</Button>
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
        title={editIndex === null ? '新增功能更新' : '编辑功能更新'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={560}
        okText="确认"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item label="分类" name="category" rules={[{ required: true }]}>
            <Select options={CATEGORIES.map(c => ({ value: c, label: c }))} />
          </Form.Item>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Form.Item label="日期（如 02.27）" name="date" rules={[{ required: true }]}>
              <Input placeholder="02.27" />
            </Form.Item>
            <Form.Item label="年份" name="year" rules={[{ required: true }]}>
              <Input placeholder="2026" />
            </Form.Item>
          </div>
          <Form.Item label="标题" name="title" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="描述" name="desc" rules={[{ required: true }]}>
            <TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
