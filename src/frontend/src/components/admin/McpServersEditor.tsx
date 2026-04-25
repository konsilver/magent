import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Drawer, Form, Input, Modal, Popconfirm, Select,
  Space, Switch, Table, Tag, message, Divider, Tooltip,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined,
  ApiOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { adminFetch } from '../../utils/adminApi';

const { TextArea } = Input;

interface McpServerItem {
  server_id: string;
  display_name: string;
  description: string;
  transport: 'stdio' | 'streamable_http' | 'sse';
  command?: string;
  args: string[];
  url?: string;
  env_vars: Record<string, string>;
  env_inherit: string[];
  headers: Record<string, string>;
  is_stable: boolean;
  is_enabled: boolean;
  sort_order: number;
  extra_config: Record<string, unknown>;
  pool_connected?: boolean | null;
  created_at?: string;
  updated_at?: string;
}

const TRANSPORT_LABELS: Record<string, string> = {
  stdio: 'StdIO',
  streamable_http: 'HTTP',
  sse: 'SSE',
};

const TRANSPORT_COLORS: Record<string, string> = {
  stdio: 'blue',
  streamable_http: 'green',
  sse: 'orange',
};

export function McpServersEditor({ token, fetchFn = adminFetch }: { token: string; fetchFn?: typeof adminFetch }) {
  const [servers, setServers] = useState<McpServerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServerItem | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchFn(token, '/v1/admin/mcp-servers');
      setServers(res.data || []);
    } catch (e) {
      message.error(`加载失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  // ── Create ───────────────────────────────────────────────────────

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      // Parse args from comma-separated string
      if (typeof values.args === 'string') {
        values.args = values.args.split(',').map((s: string) => s.trim()).filter(Boolean);
      }
      // Parse env_inherit
      if (typeof values.env_inherit === 'string') {
        values.env_inherit = values.env_inherit.split(',').map((s: string) => s.trim()).filter(Boolean);
      }
      // Parse env_vars from key=value lines
      if (typeof values.env_vars_text === 'string') {
        const env: Record<string, string> = {};
        values.env_vars_text.split('\n').forEach((line: string) => {
          const idx = line.indexOf('=');
          if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
        values.env_vars = env;
        delete values.env_vars_text;
      }
      await fetchFn(token, '/v1/admin/mcp-servers', {
        method: 'POST',
        body: JSON.stringify(values),
      });
      message.success('MCP 服务创建成功');
      setCreateOpen(false);
      form.resetFields();
      reload();
    } catch (e) {
      message.error(`创建失败：${(e as Error).message}`);
    }
  };

  // ── Toggle ──────────────────────────────────────────────────────

  const handleToggle = async (record: McpServerItem, enabled: boolean) => {
    try {
      await fetchFn(token, `/v1/admin/mcp-servers/${record.server_id}/toggle`, {
        method: 'PUT',
        body: JSON.stringify({ is_enabled: enabled }),
      });
      setServers(prev => prev.map(s =>
        s.server_id === record.server_id ? { ...s, is_enabled: enabled } : s
      ));
    } catch (e) {
      message.error(`切换失败：${(e as Error).message}`);
    }
  };

  // ── Edit ────────────────────────────────────────────────────────

  const openEdit = async (record: McpServerItem) => {
    try {
      const res = await fetchFn(token, `/v1/admin/mcp-servers/${record.server_id}`);
      const srv = res.data as McpServerItem;
      setEditingServer(srv);
      editForm.setFieldsValue({
        display_name: srv.display_name,
        description: srv.description,
        transport: srv.transport,
        command: srv.command,
        args: (srv.args || []).join(', '),
        url: srv.url,
        env_inherit: (srv.env_inherit || []).join(', '),
        env_vars_text: Object.entries(srv.env_vars || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
        headers_text: Object.entries(srv.headers || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
        is_stable: srv.is_stable,
        sort_order: srv.sort_order,
      });
      setEditOpen(true);
    } catch (e) {
      message.error(`加载详情失败：${(e as Error).message}`);
    }
  };

  const handleUpdate = async () => {
    if (!editingServer) return;
    try {
      const values = await editForm.validateFields();
      if (typeof values.args === 'string') {
        values.args = values.args.split(',').map((s: string) => s.trim()).filter(Boolean);
      }
      if (typeof values.env_inherit === 'string') {
        values.env_inherit = values.env_inherit.split(',').map((s: string) => s.trim()).filter(Boolean);
      }
      if (typeof values.env_vars_text === 'string') {
        const env: Record<string, string> = {};
        values.env_vars_text.split('\n').forEach((line: string) => {
          const idx = line.indexOf('=');
          if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
        values.env_vars = env;
        delete values.env_vars_text;
      }
      if (typeof values.headers_text === 'string') {
        const hdrs: Record<string, string> = {};
        values.headers_text.split('\n').forEach((line: string) => {
          const idx = line.indexOf('=');
          if (idx > 0) hdrs[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
        values.headers = hdrs;
        delete values.headers_text;
      }
      await fetchFn(token, `/v1/admin/mcp-servers/${editingServer.server_id}`, {
        method: 'PUT',
        body: JSON.stringify(values),
      });
      message.success('更新成功');
      setEditOpen(false);
      setEditingServer(null);
      reload();
    } catch (e) {
      message.error(`更新失败：${(e as Error).message}`);
    }
  };

  // ── Delete ──────────────────────────────────────────────────────

  const handleDelete = async (serverId: string) => {
    try {
      await fetchFn(token, `/v1/admin/mcp-servers/${serverId}`, { method: 'DELETE' });
      message.success('已删除');
      reload();
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`);
    }
  };

  // ── Test connectivity ───────────────────────────────────────────

  const handleTest = async (serverId: string) => {
    setTestingId(serverId);
    try {
      const res = await fetchFn(token, `/v1/admin/mcp-servers/${serverId}/test`, {
        method: 'POST',
      });
      const data = res.data;
      if (data.status === 'ok') {
        message.success(`连接成功 (${data.latency_ms}ms)，发现 ${data.tools_discovered?.length || 0} 个工具`);
      } else {
        message.warning(`连接失败 (${data.latency_ms}ms): ${data.error}`);
      }
    } catch (e) {
      message.error(`测试失败：${(e as Error).message}`);
    } finally {
      setTestingId(null);
    }
  };

  // ── Reload pool ─────────────────────────────────────────────────

  const handleReloadPool = async () => {
    setReloading(true);
    try {
      const res = await fetchFn(token, '/v1/admin/mcp-servers/reload-pool', {
        method: 'POST',
      });
      message.success(`连接池已重载：${res.data?.stable_connections || 0} 个常驻连接 (${res.data?.latency_ms}ms)`);
      reload();
    } catch (e) {
      message.error(`重载失败：${(e as Error).message}`);
    } finally {
      setReloading(false);
    }
  };

  // ── Transport-conditional form fields ───────────────────────────

  const TransportFields = ({ formInstance }: { formInstance: ReturnType<typeof Form.useForm>[0] }) => {
    const transport = Form.useWatch('transport', formInstance);
    return (
      <>
        {transport === 'stdio' && (
          <>
            <Form.Item name="command" label="Command" rules={[{ required: true }]}>
              <Input placeholder="python" />
            </Form.Item>
            <Form.Item name="args" label="Args" help="逗号分隔，如: -m, mcp_servers.xxx.server">
              <Input placeholder="-m, mcp_servers.xxx.server" />
            </Form.Item>
          </>
        )}
        {(transport === 'streamable_http' || transport === 'sse') && (
          <Form.Item name="url" label="URL" rules={[{ required: true }]}>
            <Input placeholder="http://host:port/path" />
          </Form.Item>
        )}
      </>
    );
  };

  // ── Table columns ───────────────────────────────────────────────

  const columns = [
    {
      title: '名称',
      dataIndex: 'display_name',
      key: 'display_name',
      render: (name: string, record: McpServerItem) => (
        <div>
          <div style={{ fontWeight: 500 }}>{name}</div>
          <div style={{ fontSize: 12, color: '#808080' }}>{record.server_id}</div>
        </div>
      ),
    },
    {
      title: '传输',
      dataIndex: 'transport',
      key: 'transport',
      width: 100,
      render: (t: string) => (
        <Tag color={TRANSPORT_COLORS[t] || 'default'}>{TRANSPORT_LABELS[t] || t}</Tag>
      ),
    },
    {
      title: '模式',
      dataIndex: 'is_stable',
      key: 'is_stable',
      width: 90,
      render: (stable: boolean) => (
        <Tag color={stable ? 'cyan' : 'volcano'}>{stable ? '常驻' : '临时'}</Tag>
      ),
    },
    {
      title: '连接',
      key: 'pool_connected',
      width: 70,
      render: (_: unknown, record: McpServerItem) => {
        if (record.pool_connected === null || record.pool_connected === undefined) {
          return <Tag>-</Tag>;
        }
        return record.pool_connected
          ? <Tag color="success">在线</Tag>
          : <Tag color="error">离线</Tag>;
      },
    },
    {
      title: '启用',
      key: 'is_enabled',
      width: 70,
      render: (_: unknown, record: McpServerItem) => (
        <Switch
          size="small"
          checked={record.is_enabled}
          onChange={(checked) => handleToggle(record, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: McpServerItem) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            icon={<ThunderboltOutlined />}
            loading={testingId === record.server_id}
            onClick={() => handleTest(record.server_id)}
          >
            测试
          </Button>
          <Popconfirm
            title="确定删除此 MCP 服务？"
            onConfirm={() => handleDelete(record.server_id)}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新增 MCP 服务
          </Button>
          <Tooltip title="使用最新配置重新初始化连接池">
            <Button
              icon={<ReloadOutlined />}
              loading={reloading}
              onClick={handleReloadPool}
            >
              重载连接池
            </Button>
          </Tooltip>
        </Space>
        <Button icon={<ApiOutlined />} onClick={reload}>刷新</Button>
      </div>

      <Table
        dataSource={servers}
        columns={columns}
        rowKey="server_id"
        loading={loading}
        pagination={false}
        size="middle"
      />

      {/* ── Create Modal ──────────────────────────────────────── */}
      <Modal
        title="新增 MCP 服务"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); form.resetFields(); }}
        width={600}
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ transport: 'stdio', is_stable: true, sort_order: 0 }}>
          <Form.Item name="server_id" label="Server ID" rules={[
            { required: true },
            { pattern: /^[a-z0-9_-]{1,63}$/, message: '仅限小写字母、数字、下划线、短横线' },
          ]}>
            <Input placeholder="my_mcp_server" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}>
            <Input placeholder="我的 MCP 服务" />
          </Form.Item>
          <Form.Item name="description" label="功能描述">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="transport" label="传输方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'stdio', label: 'StdIO (子进程)' },
              { value: 'streamable_http', label: 'Streamable HTTP' },
              { value: 'sse', label: 'SSE' },
            ]} />
          </Form.Item>
          <TransportFields formInstance={form} />
          <Form.Item name="env_inherit" label="继承环境变量" help="逗号分隔的 OS 环境变量 key 列表">
            <Input placeholder="PATH, PYTHONPATH, HOME, DATABASE_URL" />
          </Form.Item>
          <Form.Item name="env_vars_text" label="自定义环境变量" help="每行一个：KEY=VALUE">
            <TextArea rows={3} placeholder="MY_API_KEY=xxx&#10;MY_CONFIG=value" />
          </Form.Item>
          <Form.Item name="is_stable" label="连接模式" valuePropName="checked">
            <Switch checkedChildren="常驻" unCheckedChildren="临时" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <Input type="number" />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── Edit Drawer ───────────────────────────────────────── */}
      <Drawer
        title={`编辑 MCP 服务 — ${editingServer?.server_id || ''}`}
        open={editOpen}
        onClose={() => { setEditOpen(false); setEditingServer(null); }}
        width={560}
        extra={
          <Button type="primary" onClick={handleUpdate}>保存</Button>
        }
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="功能描述">
            <TextArea rows={2} />
          </Form.Item>
          <Divider />
          <Form.Item name="transport" label="传输方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'stdio', label: 'StdIO (子进程)' },
              { value: 'streamable_http', label: 'Streamable HTTP' },
              { value: 'sse', label: 'SSE' },
            ]} />
          </Form.Item>
          <TransportFields formInstance={editForm} />
          <Divider />
          <Form.Item name="env_inherit" label="继承环境变量" help="逗号分隔">
            <Input />
          </Form.Item>
          <Form.Item name="env_vars_text" label="自定义环境变量" help="每行一个：KEY=VALUE（秘钥类显示 ***）">
            <TextArea rows={4} />
          </Form.Item>
          <Form.Item name="headers_text" label="HTTP Headers" help="每行一个：Key=Value">
            <TextArea rows={2} />
          </Form.Item>
          <Divider />
          <Form.Item name="is_stable" label="常驻连接" valuePropName="checked">
            <Switch checkedChildren="常驻" unCheckedChildren="临时" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <Input type="number" />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
