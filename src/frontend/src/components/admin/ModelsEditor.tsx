import { useState, useEffect, useCallback } from 'react';
import {
  Alert, Button, Card, Form, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Switch, Table, Tag, Tooltip, Typography, Upload, message,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ApiOutlined,
  ExportOutlined, ImportOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { adminFetch } from '../../utils/adminApi';
import type { ModelProvider, ModelRole, ProviderType, TestConnectionResult } from '../../types';

const { Text } = Typography;

const PROVIDER_TYPE_LABELS: Record<ProviderType, string> = {
  chat: '对话模型',
  embedding: '向量模型',
  reranker: '重排序模型',
};
const PROVIDER_TYPE_COLORS: Record<ProviderType, string> = {
  chat: 'blue',
  embedding: 'green',
  reranker: 'orange',
};

export function ModelsEditor({ token, fetchFn = adminFetch }: { token: string; fetchFn?: typeof adminFetch }) {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [roles, setRoles] = useState<ModelRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<ModelProvider | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const reload = useCallback(async () => {
    try {
      const [pRes, rRes] = await Promise.all([
        fetchFn(token, '/v1/models/providers'),
        fetchFn(token, '/v1/models/roles'),
      ]);
      setProviders(pRes.data || []);
      setRoles(rRes.data || []);
    } catch (e) {
      message.error(`加载失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  // ── Provider CRUD ──────────────────────────────────────────────

  const openAdd = () => {
    setEditProvider(null);
    form.resetFields();
    form.setFieldsValue({ provider_type: 'chat', is_active: true });
    setModalOpen(true);
  };

  const openEdit = (p: ModelProvider) => {
    setEditProvider(p);
    form.setFieldsValue({
      display_name: p.display_name,
      provider_type: p.provider_type,
      base_url: p.base_url,
      api_key: '',
      model_name: p.model_name,
      is_active: p.is_active,
      temperature: p.extra_config?.temperature,
      max_tokens: p.extra_config?.max_tokens,
      dimensions: p.extra_config?.dimensions,
    });
    setModalOpen(true);
  };

  const handleSaveProvider = async () => {
    try {
      const values = await form.validateFields();
      const extra: Record<string, unknown> = {};
      if (values.temperature != null) extra.temperature = values.temperature;
      if (values.max_tokens != null) extra.max_tokens = values.max_tokens;
      if (values.dimensions != null) extra.dimensions = values.dimensions;

      const body: Record<string, unknown> = {
        display_name: values.display_name,
        provider_type: values.provider_type,
        base_url: values.base_url,
        model_name: values.model_name,
        extra_config: extra,
        is_active: values.is_active ?? true,
      };
      if (values.api_key) body.api_key = values.api_key;

      if (editProvider) {
        await fetchFn(token, `/v1/models/providers/${editProvider.provider_id}`, {
          method: 'PUT',
          body: JSON.stringify(body),
        });
        message.success('供应商已更新');
      } else {
        if (!values.api_key) {
          message.error('新建供应商必须填写 API Key');
          return;
        }
        body.api_key = values.api_key;
        await fetchFn(token, '/v1/models/providers', {
          method: 'POST',
          body: JSON.stringify(body),
        });
        message.success('供应商已添加');
      }
      setModalOpen(false);
      reload();
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return;
      message.error(`保存失败：${(e as Error).message}`);
    }
  };

  const handleDeleteProvider = async (id: string) => {
    try {
      await fetchFn(token, `/v1/models/providers/${id}`, { method: 'DELETE' });
      message.success('已删除');
      reload();
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`);
    }
  };

  const handleTestProvider = async (p: ModelProvider) => {
    setTestingId(p.provider_id);
    try {
      const res = await fetchFn(token, `/v1/models/providers/${p.provider_id}/test`, { method: 'POST' });
      const r = res.data as TestConnectionResult;
      if (r.success) {
        message.success(`连接成功 (${r.latency_ms}ms)`);
      } else {
        message.error(`连接失败：${r.error}`);
      }
      reload();
    } catch (e) {
      message.error(`测试失败：${(e as Error).message}`);
    } finally {
      setTestingId(null);
    }
  };

  const handleTestUnsaved = async () => {
    try {
      const values = await form.validateFields(['provider_type', 'base_url', 'api_key', 'model_name']);
      if (!values.api_key && !editProvider) {
        message.error('请填写 API Key');
        return;
      }
      const res = await fetchFn(token, '/v1/models/providers/test', {
        method: 'POST',
        body: JSON.stringify({
          provider_type: values.provider_type,
          base_url: values.base_url,
          api_key: values.api_key || 'placeholder',
          model_name: values.model_name,
        }),
      });
      const r = res.data as TestConnectionResult;
      if (r.success) {
        message.success(`连接成功 (${r.latency_ms}ms)`);
      } else {
        message.error(`连接失败：${r.error}`);
      }
    } catch (e) {
      if ((e as { errorFields?: unknown }).errorFields) return;
      message.error(`测试失败：${(e as Error).message}`);
    }
  };

  // ── Role assignment ────────────────────────────────────────────

  const handleAssignRole = async (roleKey: string, providerId: string) => {
    try {
      await fetchFn(token, `/v1/models/roles/${roleKey}`, {
        method: 'PUT',
        body: JSON.stringify({ provider_id: providerId }),
      });
      message.success('角色已分配');
      reload();
    } catch (e) {
      message.error(`分配失败：${(e as Error).message}`);
    }
  };

  const handleUnassignRole = async (roleKey: string) => {
    try {
      await fetchFn(token, `/v1/models/roles/${roleKey}`, { method: 'DELETE' });
      message.success('已取消分配');
      reload();
    } catch (e) {
      message.error(`取消失败：${(e as Error).message}`);
    }
  };

  // ── Export / Import ────────────────────────────────────────────

  const handleExport = async () => {
    try {
      const res = await fetchFn(token, '/v1/models/export');
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `model-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('配置已导出');
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`);
    }
  };

  const handleImport = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await fetchFn(token, '/v1/models/import', {
        method: 'POST',
        body: JSON.stringify({ ...data, overwrite: true }),
      });
      message.success('配置已导入');
      reload();
    } catch (e) {
      message.error(`导入失败：${(e as Error).message}`);
    }
    return false;
  };

  // ── Provider table columns ─────────────────────────────────────

  const providerColumns = [
    {
      title: '名称',
      dataIndex: 'display_name',
      width: 180,
      render: (v: string, r: ModelProvider) => (
        <Space direction="vertical" size={0}>
          <Text strong>{v}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>{r.model_name}</Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'provider_type',
      width: 100,
      render: (v: ProviderType) => (
        <Tag color={PROVIDER_TYPE_COLORS[v]}>{PROVIDER_TYPE_LABELS[v]}</Tag>
      ),
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      ellipsis: true,
      render: (v: string) => <Text copyable style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: '状态',
      width: 80,
      render: (_: unknown, r: ModelProvider) => (
        r.is_active
          ? <Tag color="green">启用</Tag>
          : <Tag color="default">停用</Tag>
      ),
    },
    {
      title: '测试',
      width: 100,
      render: (_: unknown, r: ModelProvider) => {
        const icon = r.last_test_status === 'success'
          ? <CheckCircleOutlined style={{ color: '#02B589' }} />
          : r.last_test_status === 'failure'
            ? <CloseCircleOutlined style={{ color: '#FC5D5D' }} />
            : null;
        return (
          <Tooltip title={r.last_tested_at ? `最后测试：${r.last_tested_at}` : '未测试'}>
            <Space size={4}>
              {icon}
              <Button
                size="small"
                icon={<ThunderboltOutlined />}
                loading={testingId === r.provider_id}
                onClick={() => handleTestProvider(r)}
              >
                测试
              </Button>
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, r: ModelProvider) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="确认删除？" onConfirm={() => handleDeleteProvider(r.provider_id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const selectedType = Form.useWatch('provider_type', form);

  return (
    <div>
      {/* Provider list */}
      <Card
        bordered={false}
        style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)', marginBottom: 24 }}
        title={<Space><ApiOutlined /> 模型供应商</Space>}
        extra={
          <Space>
            <Button icon={<PlusOutlined />} onClick={openAdd}>添加模型</Button>
            <Button icon={<ExportOutlined />} onClick={handleExport}>导出配置</Button>
            <Upload
              accept=".json"
              showUploadList={false}
              beforeUpload={handleImport}
            >
              <Button icon={<ImportOutlined />}>导入配置</Button>
            </Upload>
          </Space>
        }
      >
        <Table
          dataSource={providers}
          columns={providerColumns}
          rowKey="provider_id"
          size="small"
          loading={loading}
          pagination={false}
        />
      </Card>

      {/* Role assignments */}
      <Card
        bordered={false}
        style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}
        title="角色分配"
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
          {roles.map(role => {
            const compatProviders = providers.filter(
              p => p.provider_type === role.required_type && p.is_active,
            );
            return (
              <Card
                key={role.role_key}
                size="small"
                style={{ border: role.provider_id ? '1px solid #E3E6EA' : '1px dashed #F8AB42' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <Space>
                    <Text strong>{role.label}</Text>
                    <Tag>{role.role_key}</Tag>
                  </Space>
                  <Tag color={PROVIDER_TYPE_COLORS[role.required_type]}>
                    {PROVIDER_TYPE_LABELS[role.required_type]}
                  </Tag>
                </div>
                {!role.provider_id && (
                  <Alert type="warning" message="未配置" showIcon style={{ marginBottom: 8, padding: '4px 12px' }} />
                )}
                <Space style={{ width: '100%' }} direction="vertical" size={4}>
                  <Select
                    style={{ width: '100%' }}
                    placeholder="选择供应商"
                    value={role.provider_id || undefined}
                    onChange={(val) => handleAssignRole(role.role_key, val)}
                    options={compatProviders.map(p => ({
                      value: p.provider_id,
                      label: `${p.display_name} (${p.model_name})`,
                    }))}
                    allowClear
                    onClear={() => handleUnassignRole(role.role_key)}
                  />
                  {role.provider_name && (
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      当前：{role.provider_name} / {role.model_name}
                    </Text>
                  )}
                </Space>
              </Card>
            );
          })}
        </div>
      </Card>

      {/* Provider add/edit modal */}
      <Modal
        title={editProvider ? '编辑模型供应商' : '添加模型供应商'}
        open={modalOpen}
        onOk={handleSaveProvider}
        onCancel={() => setModalOpen(false)}
        width={560}
        okText="保存"
        cancelText="取消"
        footer={(_, { OkBtn, CancelBtn }) => (
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Button onClick={handleTestUnsaved} icon={<ThunderboltOutlined />}>测试连接</Button>
            <Space>
              <CancelBtn />
              <OkBtn />
            </Space>
          </div>
        )}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item label="显示名称" name="display_name" rules={[{ required: true }]}>
            <Input placeholder="如：DeepSeek 生产环境" />
          </Form.Item>
          <Form.Item label="模型类型" name="provider_type" rules={[{ required: true }]}>
            <Select
              options={Object.entries(PROVIDER_TYPE_LABELS).map(([k, v]) => ({ value: k, label: v }))}
            />
          </Form.Item>
          <Form.Item label="Base URL" name="base_url" rules={[{ required: true }]}>
            <Input placeholder="https://api.deepseek.com/v1" />
          </Form.Item>
          <Form.Item
            label={editProvider ? 'API Key（留空保持不变）' : 'API Key'}
            name="api_key"
            rules={editProvider ? [] : [{ required: true }]}
          >
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item label="模型名称" name="model_name" rules={[{ required: true }]}>
            <Input placeholder="deepseek-chat" />
          </Form.Item>

          {(selectedType === 'chat') && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item label="Temperature" name="temperature">
                <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} placeholder="0.6" />
              </Form.Item>
              <Form.Item label="Max Tokens" name="max_tokens">
                <InputNumber min={1} max={128000} style={{ width: '100%' }} placeholder="8192" />
              </Form.Item>
            </div>
          )}
          {selectedType === 'embedding' && (
            <Form.Item label="Dimensions" name="dimensions">
              <InputNumber min={1} max={8192} style={{ width: '100%' }} placeholder="1024" />
            </Form.Item>
          )}

          <Form.Item label="启用" name="is_active" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
