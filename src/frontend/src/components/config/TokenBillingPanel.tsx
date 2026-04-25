import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Col, Collapse, DatePicker, Form, Input, InputNumber,
  Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tag, message,
} from 'antd';
import { ExportOutlined, PlusOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons';
import { configFetch, API_BASE } from '../../utils/adminApi';
import type { BillingSummaryItem, ModelPricingItem } from '../../types';
import type { Dayjs } from 'dayjs';

export function TokenBillingPanel({ token }: { token: string }) {
  const [billingData, setBillingData] = useState<BillingSummaryItem[]>([]);
  const [pricing, setPricing] = useState<ModelPricingItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [pricingLoading, setPricingLoading] = useState(false);
  const [groupBy, setGroupBy] = useState<string>('user');
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);
  const [pricingModal, setPricingModal] = useState(false);
  const [editingPricing, setEditingPricing] = useState<ModelPricingItem | null>(null);
  const [form] = Form.useForm();

  const loadBilling = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ group_by: groupBy });
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/billing/summary?${params}`);
      setBillingData(res.data || []);
    } catch (e: any) { message.error(e.message); }
    setLoading(false);
  }, [token, groupBy, filterDates]);

  const loadPricing = useCallback(async () => {
    setPricingLoading(true);
    try {
      const res = await configFetch(token, '/v1/admin/billing/pricing');
      setPricing(res.data || []);
    } catch (e: any) { message.error(e.message); }
    setPricingLoading(false);
  }, [token]);

  useEffect(() => { loadBilling(); }, [loadBilling]);
  useEffect(() => { loadPricing(); }, [loadPricing]);

  const handleExport = () => {
    const params = new URLSearchParams();
    if (filterDates) {
      params.set('date_from', filterDates[0].startOf('day').toISOString());
      params.set('date_to', filterDates[1].endOf('day').toISOString());
    }
    const url = `${API_BASE}/v1/admin/billing/export?${params}`;
    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.blob())
      .then(blob => {
        const blobUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = blobUrl;
        link.download = 'billing_export.csv';
        link.click();
        URL.revokeObjectURL(blobUrl);
      })
      .catch(() => message.error('导出失败'));
  };

  const handlePricingSave = async () => {
    try {
      const values = await form.validateFields();
      if (editingPricing) {
        await configFetch(token, `/v1/admin/billing/pricing/${editingPricing.pricing_id}`, {
          method: 'PUT', body: JSON.stringify(values),
        });
      } else {
        await configFetch(token, '/v1/admin/billing/pricing', {
          method: 'POST', body: JSON.stringify(values),
        });
      }
      message.success('保存成功');
      setPricingModal(false);
      setEditingPricing(null);
      form.resetFields();
      loadPricing();
      loadBilling();
    } catch (e: any) { message.error(e.message); }
  };

  const handlePricingDelete = async (id: string) => {
    try {
      await configFetch(token, `/v1/admin/billing/pricing/${id}`, { method: 'DELETE' });
      message.success('已删除');
      loadPricing();
      loadBilling();
    } catch (e: any) { message.error(e.message); }
  };

  // Summary stats
  const totalCost = billingData.reduce((s, i) => s + i.total_cost, 0);
  const totalTokens = billingData.reduce((s, i) => s + i.total_tokens, 0);
  const activeItems = billingData.length;

  // Dynamic billing columns based on group_by
  const billingColumns = [
    { title: groupBy === 'user' ? '用户' : groupBy === 'model' ? '模型' : '日期',
      dataIndex: groupBy === 'user' ? 'display_name' : 'group_key',
      key: 'group_key', width: 160,
      render: (v: string, row: BillingSummaryItem) => v || row.group_key },
    { title: '请求数', dataIndex: 'total_requests', key: 'total_requests', width: 100, align: 'right' as const },
    { title: '输入 Token', dataIndex: 'prompt_tokens', key: 'pt', width: 120, align: 'right' as const,
      render: (v: number) => v.toLocaleString() },
    { title: '输出 Token', dataIndex: 'completion_tokens', key: 'ct', width: 120, align: 'right' as const,
      render: (v: number) => v.toLocaleString() },
    { title: '输入费用', dataIndex: 'prompt_cost', key: 'pc', width: 120, align: 'right' as const,
      render: (v: number) => `¥${v.toFixed(4)}` },
    { title: '输出费用', dataIndex: 'completion_cost', key: 'cc', width: 120, align: 'right' as const,
      render: (v: number) => `¥${v.toFixed(4)}` },
    { title: '总费用', dataIndex: 'total_cost', key: 'tc', width: 120, align: 'right' as const,
      render: (v: number) => <strong>¥{v.toFixed(4)}</strong> },
  ];

  const pricingColumns = [
    { title: '模型名称', dataIndex: 'model_name', key: 'model_name' },
    { title: '显示名', dataIndex: 'display_name', key: 'display_name', render: (v: string | null) => v || '-' },
    { title: '输入单价 (¥/1K)', dataIndex: 'input_price', key: 'input_price', align: 'right' as const,
      render: (v: number) => v.toFixed(4) },
    { title: '输出单价 (¥/1K)', dataIndex: 'output_price', key: 'output_price', align: 'right' as const,
      render: (v: number) => v.toFixed(4) },
    { title: '币种', dataIndex: 'currency', key: 'currency', width: 60 },
    { title: '状态', dataIndex: 'is_active', key: 'is_active', width: 60,
      render: (v: boolean) => v ? <Tag color="success">启用</Tag> : <Tag>停用</Tag> },
    { title: '操作', key: 'actions', width: 120,
      render: (_: unknown, row: ModelPricingItem) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            setEditingPricing(row);
            form.setFieldsValue(row);
            setPricingModal(true);
          }} />
          <Popconfirm title="确认删除？" onConfirm={() => handlePricingDelete(row.pricing_id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small">
        <Space wrap>
          <DatePicker.RangePicker
            value={filterDates}
            onChange={(v) => setFilterDates(v as [Dayjs, Dayjs] | null)}
          />
          <Select
            value={groupBy} onChange={setGroupBy} style={{ width: 120 }}
            options={[
              { value: 'user', label: '按用户' },
              { value: 'model', label: '按模型' },
              { value: 'day', label: '按天' },
            ]}
          />
          <Button type="primary" onClick={loadBilling}>查询</Button>
          <Button icon={<ExportOutlined />} onClick={handleExport}>导出 CSV</Button>
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={8}><Card size="small"><Statistic title="总费用" value={totalCost} prefix="¥" precision={4} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title="总 Token" value={totalTokens} /></Card></Col>
        <Col span={8}><Card size="small"><Statistic title={groupBy === 'user' ? '活跃用户' : groupBy === 'model' ? '使用模型' : '活跃天数'} value={activeItems} /></Card></Col>
      </Row>

      <Table
        dataSource={billingData}
        columns={billingColumns}
        rowKey="group_key"
        loading={loading}
        size="small"
        pagination={false}
        scroll={{ x: 900 }}
      />

      <Collapse size="small" items={[{
        key: 'pricing',
        label: '模型定价配置',
        children: (
          <>
            <div style={{ marginBottom: 12 }}>
              <Button icon={<PlusOutlined />} onClick={() => {
                setEditingPricing(null);
                form.resetFields();
                setPricingModal(true);
              }}>新增定价</Button>
            </div>
            <Table
              dataSource={pricing}
              columns={pricingColumns}
              rowKey="pricing_id"
              loading={pricingLoading}
              size="small"
              pagination={false}
            />
          </>
        ),
      }]} />

      <Modal
        title={editingPricing ? '编辑定价' : '新增定价'}
        open={pricingModal}
        onOk={handlePricingSave}
        onCancel={() => { setPricingModal(false); setEditingPricing(null); form.resetFields(); }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="model_name" label="模型名称" rules={[{ required: true }]}>
            <Input disabled={!!editingPricing} placeholder="与 chat_messages.model 字段一致" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input placeholder="可选" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="input_price" label="输入单价 (¥/1K tokens)" rules={[{ required: true }]}>
                <InputNumber min={0} step={0.0001} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="output_price" label="输出单价 (¥/1K tokens)" rules={[{ required: true }]}>
                <InputNumber min={0} step={0.0001} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="currency" label="币种" initialValue="CNY">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
