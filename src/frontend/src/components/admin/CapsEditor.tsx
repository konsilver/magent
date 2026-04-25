import { useState, useEffect } from 'react';
import { Button, Form, Input, Modal, Popconfirm, Space, Table, Typography, Upload, message } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ArrowUpOutlined, ArrowDownOutlined, ExportOutlined, ImportOutlined } from '@ant-design/icons';
import { fetchContent, saveBlock, move } from '../../utils/adminApi';
import type { CapItem } from '../../types';

const { Text } = Typography;
const { TextArea } = Input;

export function CapsEditor({ token }: { token: string }) {
  const [items, setItems] = useState<CapItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [form] = Form.useForm<{ title: string; desc: string; bulletsText: string }>();

  useEffect(() => {
    fetchContent(token)
      .then(data => {
        if (Array.isArray(data?.data?.capabilities)) setItems(data.data.capabilities as CapItem[]);
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
    form.setFieldsValue({
      title: items[i].title,
      desc: items[i].desc,
      bulletsText: items[i].bullets.join('\n'),
    });
    setModalOpen(true);
  };

  const handleSave = () => {
    form.validateFields().then(values => {
      const item: CapItem = {
        title: values.title,
        desc: values.desc,
        bullets: values.bulletsText.split('\n').map(s => s.trim()).filter(Boolean),
      };
      const next = editIndex === null
        ? [...items, item]
        : items.map((it, i) => (i === editIndex ? item : it));
      setItems(next);
      setModalOpen(false);
    });
  };

  const handleDelete = (i: number) => setItems(items.filter((_, idx) => idx !== i));

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `capabilities-${new Date().toISOString().slice(0, 10)}.json`;
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
        message.success(`已导入 ${data.length} 项能力`);
      } catch { message.error('JSON 解析失败'); }
    });
    return false;
  };

  const handlePublish = async () => {
    setSaving(true);
    try {
      await saveBlock(token, 'capabilities', items);
      message.success('能力中心已保存');
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    { title: '#', width: 40, render: (_: unknown, __: CapItem, i: number) => <Text type="secondary">{i + 1}</Text> },
    { title: '标题', dataIndex: 'title', width: 200 },
    { title: '描述', dataIndex: 'desc', ellipsis: true },
    { title: '子项数', width: 70, render: (_: unknown, r: CapItem) => r.bullets.length },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, __: CapItem, i: number) => (
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
        <Text strong>共 {items.length} 项能力</Text>
        <Space>
          <Button icon={<PlusOutlined />} onClick={openAdd}>新增能力</Button>
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
        title={editIndex === null ? '新增能力项' : '编辑能力项'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={560}
        okText="确认"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item label="标题" name="title" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="描述" name="desc" rules={[{ required: true }]}>
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item
            label="子项（每行一条）"
            name="bulletsText"
            rules={[{ required: true }]}
          >
            <TextArea rows={5} placeholder={'单企业精准查询\n多维度筛选\n指标自动换算'} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
