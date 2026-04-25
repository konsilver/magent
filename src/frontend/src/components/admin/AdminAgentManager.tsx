import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Upload,
  message,
} from 'antd';
import { DeleteOutlined, EditOutlined, ExportOutlined, ImportOutlined, PlusOutlined } from '@ant-design/icons';
import { adminFetch } from '../../utils/adminApi';

const { TextArea } = Input;

interface AgentRow {
  agent_id: string;
  name: string;
  description: string;
  system_prompt: string;
  welcome_message: string;
  mcp_server_ids: string[];
  skill_ids: string[];
  kb_ids: string[];
  is_enabled: boolean;
  sort_order: number;
  max_iters: number;
  temperature: number | null;
  extra_config?: Record<string, unknown>;
  created_at: string | null;
}

interface ResourceOption {
  id: string;
  name: string;
  description: string;
}

export function AdminAgentManager({ token }: { token: string }) {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AgentRow | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const [mcpOptions, setMcpOptions] = useState<ResourceOption[]>([]);
  const [skillOptions, setSkillOptions] = useState<ResourceOption[]>([]);

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminFetch(token, '/v1/admin/agents');
      const raw = res?.data ?? res ?? [];
      const items = Array.isArray(raw) ? raw : Array.isArray(raw?.items) ? raw.items : [];
      setAgents(items);
    } catch (e: any) {
      message.error(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [token]);

  const fetchResources = useCallback(async () => {
    try {
      const res = await adminFetch(token, '/v1/agents/available-resources');
      const data = res?.data ?? res;
      setMcpOptions(data?.mcp_servers ?? []);
      setSkillOptions(data?.skills ?? []);
    } catch {
      // non-critical
    }
  }, [token]);

  useEffect(() => {
    fetchAgents();
    fetchResources();
  }, [fetchAgents, fetchResources]);

  function openCreate() {
    setEditingAgent(null);
    form.resetFields();
    form.setFieldsValue({ max_iters: 10, sort_order: 0, shared_context: false, temperature: 0.6 });
    setFormOpen(true);
  }

  function openEdit(agent: AgentRow) {
    setEditingAgent(agent);
    const ec = agent.extra_config || {};
    form.setFieldsValue({
      name: agent.name,
      description: agent.description,
      system_prompt: agent.system_prompt,
      welcome_message: agent.welcome_message,
      mcp_server_ids: agent.mcp_server_ids || [],
      skill_ids: agent.skill_ids || [],
      max_iters: agent.max_iters ?? 10,
      sort_order: agent.sort_order ?? 0,
      shared_context: !!ec.shared_context,
      temperature: agent.temperature ?? 0.6,
    });
    setFormOpen(true);
  }

  async function handleSave() {
    try {
      const values = await form.validateFields();
      // 将 shared_context 合并到 extra_config
      const { shared_context, ...rest } = values;
      const existingExtra = editingAgent?.extra_config || {};
      const extra_config = {
        ...existingExtra,
        shared_context: !!shared_context,
      };
      const payload = { ...rest, extra_config };

      setSaving(true);
      if (editingAgent) {
        await adminFetch(token, `/v1/admin/agents/${editingAgent.agent_id}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        message.success('已更新');
      } else {
        await adminFetch(token, '/v1/admin/agents', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        message.success('已创建');
      }
      setFormOpen(false);
      fetchAgents();
    } catch (e: any) {
      if (e.errorFields) return;
      message.error(e.message || '操作失败');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(agent: AgentRow) {
    Modal.confirm({
      title: '删除子智能体',
      content: `确定删除「${agent.name}」吗？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await adminFetch(token, `/v1/admin/agents/${agent.agent_id}`, { method: 'DELETE' });
          message.success('已删除');
          fetchAgents();
        } catch (e: any) {
          message.error(e.message || '删除失败');
        }
      },
    });
  }

  async function handleToggle(agent: AgentRow) {
    try {
      await adminFetch(token, `/v1/admin/agents/${agent.agent_id}/toggle`, { method: 'POST' });
      fetchAgents();
    } catch (e: any) {
      message.error(e.message || '操作失败');
    }
  }

  // ── Export / Import ─────────────────────────────────────────────

  const handleExport = async () => {
    try {
      const res = await adminFetch(token, '/v1/admin/agents/export');
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `agents-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('子智能体配置已导出');
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`);
    }
  };

  const handleImportJson = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!Array.isArray(data)) { message.error('JSON 格式错误：需要数组'); return false; }
      await adminFetch(token, '/v1/admin/agents/import', {
        method: 'POST',
        body: JSON.stringify({ agents: data, overwrite: true }),
      });
      message.success('子智能体配置已导入');
      fetchAgents();
    } catch (e) {
      message.error(`导入失败：${(e as Error).message}`);
    }
    return false;
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <strong>{name}</strong>,
    },
    {
      title: '简介',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      width: 200,
    },
    {
      title: '工具',
      dataIndex: 'mcp_server_ids',
      key: 'mcp',
      render: (ids: string[]) =>
        (ids || []).map((id) => (
          <Tag key={id} style={{ marginBottom: 2 }}>
            {mcpOptions.find((o) => o.id === id)?.name ?? id}
          </Tag>
        )),
    },
    {
      title: '状态',
      dataIndex: 'is_enabled',
      key: 'status',
      width: 80,
      render: (enabled: boolean, record: AgentRow) => (
        <Switch size="small" checked={enabled} onChange={() => handleToggle(record)} />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: AgentRow) => (
        <Space size="small">
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record)} />
        </Space>
      ),
    },
  ];

  return (
    <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ fontSize: 16, fontWeight: 600 }}>子智能体管理</span>
        <Space>
          <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
          <Upload accept=".json" showUploadList={false} beforeUpload={handleImportJson}>
            <Button icon={<ImportOutlined />}>导入</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            创建子智能体
          </Button>
        </Space>
      </div>

      <Spin spinning={loading}>
        {agents.length > 0 ? (
          <Table
            dataSource={agents}
            columns={columns}
            rowKey="agent_id"
            pagination={false}
            size="small"
          />
        ) : (
          <Empty description="暂无子智能体" />
        )}
      </Spin>

      <Modal
        title={editingAgent ? '编辑子智能体' : '创建子智能体'}
        open={formOpen}
        onOk={handleSave}
        onCancel={() => setFormOpen(false)}
        confirmLoading={saving}
        okText={editingAgent ? '保存' : '创建'}
        cancelText="取消"
        width={600}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="产业链分析师" maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label="简介">
            <Input placeholder="一句话描述" maxLength={200} />
          </Form.Item>
          <Form.Item name="system_prompt" label="角色设定" rules={[{ required: true, message: '请输入角色设定' }]}>
            <TextArea rows={5} placeholder="定义智能体角色和行为规范..." maxLength={5000} showCount />
          </Form.Item>
          <Form.Item name="welcome_message" label="开场白">
            <TextArea rows={2} placeholder="欢迎消息" maxLength={500} />
          </Form.Item>
          <Form.Item name="mcp_server_ids" label="绑定工具 (MCP)">
            <Select
              mode="multiple"
              placeholder="选择 MCP 工具"
              options={mcpOptions.map((o) => ({ label: o.name, value: o.id }))}
              allowClear
            />
          </Form.Item>
          <Form.Item name="skill_ids" label="绑定技能">
            <Select
              mode="multiple"
              placeholder="选择技能"
              options={skillOptions.map((o) => ({ label: o.name, value: o.id }))}
              allowClear
            />
          </Form.Item>
          <Form.Item name="max_iters" label="最大推理轮次">
            <InputNumber min={1} max={30} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="temperature"
            label="温度 (Temperature)"
            tooltip="控制生成结果的随机性；值越低越确定，越高越发散。范围 0–2，默认 0.6"
          >
            <InputNumber min={0} max={2} step={0.1} placeholder="0.6" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="sort_order" label="排序权重">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="shared_context" label="共享上下文" valuePropName="checked" tooltip="启用后，被主智能体调用时可读取完整对话历史和工具调用结果">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
