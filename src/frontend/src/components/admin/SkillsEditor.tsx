import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Descriptions, Divider, Drawer, Form, Input, Modal, Popconfirm,
  Space, Switch, Table, Tabs, Tag, Tooltip, Upload, message,
} from 'antd';
import {
  PlusOutlined, UploadOutlined, DeleteOutlined, EyeOutlined, EditOutlined,
  FileTextOutlined, SaveOutlined, UndoOutlined, ExportOutlined, ImportOutlined,
} from '@ant-design/icons';
import { adminFetch, API_BASE } from '../../utils/adminApi';

interface SkillItem {
  id: string;
  name: string;
  description: string;
  version: string;
  tags: string[];
  allowed_tools: string[];
  source: string;
  is_enabled?: boolean;
}

interface ExtraFileInfo {
  filename: string;
  size: number;
}

interface SkillDetail extends SkillItem {
  instructions: string[];
  inputs: string;
  outputs: string;
  extra_files?: ExtraFileInfo[];
}

const SOURCE_COLORS: Record<string, string> = {
  'built-in': 'blue',
  admin: 'orange',
  user: 'green',
  project: 'purple',
};

const SOURCE_LABELS: Record<string, string> = {
  'built-in': '内置',
  admin: '管理员',
  user: '用户',
  project: '项目',
};

export function SkillsEditor({ token }: { token: string }) {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [form] = Form.useForm();
  const [editOpen, setEditOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillItem | null>(null);
  const [editForm] = Form.useForm();

  // ── Extra files state ──────────────────────────────────────────
  const [extraFiles, setExtraFiles] = useState<ExtraFileInfo[]>([]);
  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminFetch(token, '/v1/admin/skills');
      setSkills(res.data || []);
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
      if (typeof values.tags === 'string') {
        values.tags = values.tags.split(',').map((t: string) => t.trim()).filter(Boolean);
      }
      if (typeof values.allowed_tools === 'string') {
        values.allowed_tools = values.allowed_tools.split(',').map((t: string) => t.trim()).filter(Boolean);
      }
      await adminFetch(token, '/v1/admin/skills', {
        method: 'POST',
        body: JSON.stringify(values),
      });
      message.success('技能创建成功');
      setCreateOpen(false);
      form.resetFields();
      reload();
    } catch (e) {
      if (e && typeof e === 'object' && 'message' in e) {
        message.error(`创建失败：${(e as Error).message}`);
      }
    }
  };

  // ── Upload ───────────────────────────────────────────────────────

  const handleUpload = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(`${API_BASE}/v1/admin/skills/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      message.success('技能上传成功');
      reload();
    } catch (e) {
      message.error(`上传失败：${(e as Error).message}`);
    }
    return false;
  };

  // ── Export / Import ─────────────────────────────────────────────

  const handleExport = async () => {
    try {
      const res = await adminFetch(token, '/v1/admin/skills/export');
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `skills-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('技能配置已导出');
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`);
    }
  };

  const handleImportJson = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!Array.isArray(data)) { message.error('JSON 格式错误：需要数组'); return false; }
      await adminFetch(token, '/v1/admin/skills/import', {
        method: 'POST',
        body: JSON.stringify({ skills: data, overwrite: true }),
      });
      message.success('技能配置已导入');
      reload();
    } catch (e) {
      message.error(`导入失败：${(e as Error).message}`);
    }
    return false;
  };

  // ── Delete ───────────────────────────────────────────────────────

  const handleDelete = async (skillId: string) => {
    try {
      await adminFetch(token, `/v1/admin/skills/${skillId}`, { method: 'DELETE' });
      message.success('技能已删除');
      reload();
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`);
    }
  };

  // ── Fork (built-in → admin) ──────────────────────────────────────

  const handleFork = async (skillId: string) => {
    try {
      await adminFetch(token, `/v1/admin/skills/${skillId}/fork`, { method: 'POST' });
      message.success('技能已复制为管理员版本，可以编辑');
      // Re-fetch to get updated source info and auto-open edit
      const res = await adminFetch(token, '/v1/admin/skills');
      const updatedSkills: SkillItem[] = res.data || [];
      setSkills(updatedSkills);
      const forkedSkill = updatedSkills.find(s => s.id === skillId && s.source === 'admin');
      if (forkedSkill) {
        openEdit(forkedSkill);
      }
    } catch (e) {
      message.error(`Fork 失败：${(e as Error).message}`);
    }
  };

  // ── Restore (delete admin override, restore built-in) ──────────

  const handleRestore = async (skillId: string) => {
    try {
      await adminFetch(token, `/v1/admin/skills/${skillId}`, { method: 'DELETE' });
      message.success('已恢复为内置版本');
      reload();
    } catch (e) {
      message.error(`恢复失败：${(e as Error).message}`);
    }
  };

  // ── Toggle enabled ─────────────────────────────────────────────

  const handleToggleEnabled = async (record: SkillItem, enabled: boolean) => {
    try {
      await adminFetch(token, `/v1/admin/skills/${record.id}/toggle`, {
        method: 'PUT',
        body: JSON.stringify({ is_enabled: enabled }),
      });
      message.success(enabled ? '技能已启用' : '技能已禁用');
      reload();
    } catch (e) {
      message.error(`操作失败：${(e as Error).message}`);
    }
  };

  // ── Edit ────────────────────────────────────────────────────────

  const openEdit = async (record: SkillItem) => {
    setEditingSkill(record);
    setEditOpen(true);
    setEditingFile(null);
    setFileContent('');
    try {
      const res = await adminFetch(token, `/v1/admin/skills/${record.id}`);
      const d = res.data;
      // Prefer instructions_raw (full body text) over parsed instructions (list items only)
      const instructionsText = d.instructions_raw
        || (Array.isArray(d.instructions) ? d.instructions.join('\n') : (d.instructions || ''));
      editForm.setFieldsValue({
        display_name: d.name,
        description: d.description,
        version: d.version,
        tags: (d.tags || []).join(', '),
        allowed_tools: (d.allowed_tools || []).join(', '),
        instructions: instructionsText,
      });
      setExtraFiles(d.extra_files || []);
    } catch (e) {
      message.error(`加载技能详情失败：${(e as Error).message}`);
    }
  };

  const handleEdit = async () => {
    if (!editingSkill) return;
    try {
      const values = await editForm.validateFields();
      const payload: Record<string, unknown> = {
        display_name: values.display_name,
        description: values.description,
        version: values.version,
        instructions: values.instructions,
      };
      if (typeof values.tags === 'string') {
        payload.tags = values.tags.split(',').map((t: string) => t.trim()).filter(Boolean);
      }
      if (typeof values.allowed_tools === 'string') {
        payload.allowed_tools = values.allowed_tools.split(',').map((t: string) => t.trim()).filter(Boolean);
      }
      await adminFetch(token, `/v1/admin/skills/${editingSkill.id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      message.success('技能已更新');
      setEditOpen(false);
      setEditingSkill(null);
      editForm.resetFields();
      setExtraFiles([]);
      reload();
    } catch (e) {
      if (e && typeof e === 'object' && 'message' in e) {
        message.error(`更新失败：${(e as Error).message}`);
      }
    }
  };

  // ── Extra file operations ──────────────────────────────────────

  const loadFileContent = async (filename: string) => {
    if (!editingSkill) return;
    setFileLoading(true);
    setEditingFile(filename);
    try {
      const res = await adminFetch(token, `/v1/admin/skills/${editingSkill.id}/files/${filename}`);
      setFileContent(res.data.content);
    } catch (e) {
      message.error(`加载文件失败：${(e as Error).message}`);
    } finally {
      setFileLoading(false);
    }
  };

  const saveFileContent = async () => {
    if (!editingSkill || !editingFile) return;
    try {
      await adminFetch(token, `/v1/admin/skills/${editingSkill.id}/files/${editingFile}`, {
        method: 'PUT',
        body: JSON.stringify({ content: fileContent }),
      });
      message.success('文件已保存');
      // Update size in list
      setExtraFiles(prev =>
        prev.map(f => f.filename === editingFile ? { ...f, size: fileContent.length } : f)
      );
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`);
    }
  };

  const deleteFile = async (filename: string) => {
    if (!editingSkill) return;
    try {
      await adminFetch(token, `/v1/admin/skills/${editingSkill.id}/files/${filename}`, {
        method: 'DELETE',
      });
      message.success('文件已删除');
      setExtraFiles(prev => prev.filter(f => f.filename !== filename));
      if (editingFile === filename) {
        setEditingFile(null);
        setFileContent('');
      }
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`);
    }
  };

  // ── Detail ───────────────────────────────────────────────────────

  const showDetail = async (skillId: string) => {
    setDrawerOpen(true);
    setDetailLoading(true);
    try {
      const res = await adminFetch(token, `/v1/admin/skills/${skillId}`);
      setDetail(res.data);
    } catch (e) {
      message.error(`加载详情失败：${(e as Error).message}`);
    } finally {
      setDetailLoading(false);
    }
  };

  // ── Table ────────────────────────────────────────────────────────

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 180,
      ellipsis: true,
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 160,
      ellipsis: true,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 90,
      align: 'center' as const,
      render: (source: string) => (
        <Tag color={SOURCE_COLORS[source] || 'default'} bordered={false}>
          {SOURCE_LABELS[source] || source}
        </Tag>
      ),
    },
    {
      title: '启用',
      key: 'is_enabled',
      width: 70,
      align: 'center' as const,
      render: (_: unknown, record: SkillItem) => (
        <Switch
          size="small"
          checked={record.is_enabled !== false}
          onChange={(checked) => handleToggleEnabled(record, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: unknown, record: SkillItem) => {
        const isAdmin = record.source === 'admin';
        const isBuiltIn = record.source === 'built-in';
        return (
          <Space size={0} split={isAdmin ? <Divider type="vertical" style={{ margin: '0 2px' }} /> : undefined}>
            <Space size={0}>
              <Tooltip title="查看详情">
                <Button
                  type="text"
                  size="small"
                  icon={<EyeOutlined />}
                  onClick={() => showDetail(record.id)}
                  style={{ color: '#126DFF' }}
                />
              </Tooltip>
              {isAdmin && (
                <Tooltip title="编辑">
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(record)}
                    style={{ color: '#126DFF' }}
                  />
                </Tooltip>
              )}
              {isBuiltIn && (
                <Popconfirm
                  title="复制为管理员版本？"
                  description="将内置技能复制为管理员版本以进行编辑"
                  onConfirm={() => handleFork(record.id)}
                >
                  <Tooltip title="复制并编辑">
                    <Button type="text" size="small" icon={<EditOutlined />} style={{ color: '#126DFF' }} />
                  </Tooltip>
                </Popconfirm>
              )}
            </Space>
            {isAdmin && (
              <Space size={0}>
                <Popconfirm
                  title="恢复为内置版本？"
                  description="管理员的修改将被删除"
                  onConfirm={() => handleRestore(record.id)}
                >
                  <Tooltip title="恢复内置">
                    <Button type="text" size="small" icon={<UndoOutlined />} style={{ color: '#F8AB42' }} />
                  </Tooltip>
                </Popconfirm>
                <Popconfirm
                  title="确定删除该技能？"
                  description="删除后不可恢复"
                  onConfirm={() => handleDelete(record.id)}
                >
                  <Tooltip title="删除">
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                  </Tooltip>
                </Popconfirm>
              </Space>
            )}
          </Space>
        );
      },
    },
  ];

  // ── File list columns (for edit modal) ─────────────────────────

  const fileColumns = [
    {
      title: '文件名',
      dataIndex: 'filename',
      key: 'filename',
      render: (name: string) => (
        <Space size="small">
          <FileTextOutlined />
          <span>{name}</span>
        </Space>
      ),
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 100,
      render: (size: number) => size >= 1024 ? `${(size / 1024).toFixed(1)} KB` : `${size} B`,
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: unknown, record: ExtraFileInfo) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => loadFileContent(record.filename)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定删除该文件？"
            onConfirm={() => deleteFile(record.filename)}
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
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建技能
        </Button>
        <Upload
          accept=".zip"
          showUploadList={false}
          beforeUpload={handleUpload}
        >
          <Button icon={<UploadOutlined />}>上传技能 (zip)</Button>
        </Upload>
        <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
        <Upload accept=".json" showUploadList={false} beforeUpload={handleImportJson}>
          <Button icon={<ImportOutlined />}>导入</Button>
        </Upload>
      </Space>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={skills}
        loading={loading}
        pagination={{ pageSize: 20 }}
        size="middle"
        scroll={{ x: 'max-content' }}
      />

      {/* Create Modal */}
      <Modal
        title="新建技能"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreate}
        okText="创建"
        cancelText="取消"
        width={640}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="技能 ID"
            rules={[
              { required: true, message: '请输入技能 ID' },
              { pattern: /^[a-z0-9_-]{1,63}$/, message: '仅限小写字母、数字、连字符和下划线' },
            ]}
          >
            <Input placeholder="my-custom-skill" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true, message: '请输入显示名称' }]}>
            <Input placeholder="自定义技能" />
          </Form.Item>
          <Form.Item name="description" label="描述" rules={[{ required: true, message: '请输入描述' }]}>
            <Input.TextArea rows={2} placeholder="技能描述..." />
          </Form.Item>
          <Form.Item name="version" label="版本" initialValue="1.0.0">
            <Input placeholder="1.0.0" />
          </Form.Item>
          <Form.Item name="tags" label="标签（逗号分隔）">
            <Input placeholder="tag1, tag2" />
          </Form.Item>
          <Form.Item name="allowed_tools" label="允许工具（逗号分隔）">
            <Input placeholder="search, database" />
          </Form.Item>
          <Form.Item name="instructions" label="执行指令" rules={[{ required: true, message: '请输入执行指令' }]}>
            <Input.TextArea rows={6} placeholder={"1. 步骤一\n2. 步骤二\n3. 步骤三"} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Detail Drawer */}
      <Drawer
        title={detail ? `技能详情: ${detail.name || detail.id}` : '技能详情'}
        open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setDetail(null); }}
        width={560}
        loading={detailLoading}
      >
        {detail && (
          <>
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="ID">{detail.id}</Descriptions.Item>
              <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="描述">{detail.description}</Descriptions.Item>
              <Descriptions.Item label="版本">{detail.version}</Descriptions.Item>
              <Descriptions.Item label="来源">
                <Tag color={SOURCE_COLORS[detail.source] || 'default'}>
                  {SOURCE_LABELS[detail.source] || detail.source}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="标签">
                {(detail.tags || []).map(t => <Tag key={t}>{t}</Tag>)}
              </Descriptions.Item>
              <Descriptions.Item label="允许工具">
                {(detail.allowed_tools || []).map(t => <Tag key={t} color="cyan">{t}</Tag>)}
              </Descriptions.Item>
            </Descriptions>
            <div style={{ marginTop: 16 }}>
              <h4>执行指令</h4>
              {(detail as any).instructions_raw ? (
                <pre style={{ background: '#F5F6F7', padding: 12, borderRadius: 4, whiteSpace: 'pre-wrap', fontSize: 13 }}>
                  {(detail as any).instructions_raw}
                </pre>
              ) : (
                <ol style={{ paddingLeft: 20 }}>
                  {(detail.instructions || []).map((inst, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>{inst}</li>
                  ))}
                </ol>
              )}
            </div>
            {detail.inputs && (
              <div style={{ marginTop: 12 }}>
                <h4>输入</h4>
                <pre style={{ background: '#F5F6F7', padding: 8, borderRadius: 4, whiteSpace: 'pre-wrap' }}>
                  {detail.inputs}
                </pre>
              </div>
            )}
            {detail.outputs && (
              <div style={{ marginTop: 12 }}>
                <h4>输出</h4>
                <pre style={{ background: '#F5F6F7', padding: 8, borderRadius: 4, whiteSpace: 'pre-wrap' }}>
                  {detail.outputs}
                </pre>
              </div>
            )}
            {detail.extra_files && detail.extra_files.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4>附加文件 ({detail.extra_files.length})</h4>
                <Table
                  rowKey="filename"
                  columns={[
                    { title: '文件名', dataIndex: 'filename', key: 'filename' },
                    {
                      title: '大小', dataIndex: 'size', key: 'size', width: 100,
                      render: (size: number) => size >= 1024 ? `${(size / 1024).toFixed(1)} KB` : `${size} B`,
                    },
                  ]}
                  dataSource={detail.extra_files}
                  pagination={false}
                  size="small"
                />
              </div>
            )}
          </>
        )}
      </Drawer>

      {/* Edit Skill Modal — with Tabs */}
      <Modal
        title={`编辑技能: ${editingSkill?.name || editingSkill?.id || ''}`}
        open={editOpen}
        onCancel={() => {
          setEditOpen(false);
          setEditingSkill(null);
          editForm.resetFields();
          setExtraFiles([]);
          setEditingFile(null);
          setFileContent('');
        }}
        onOk={handleEdit}
        okText="保存"
        cancelText="取消"
        width={720}
      >
        <Tabs
          defaultActiveKey="config"
          items={[
            {
              key: 'config',
              label: '技能配置',
              children: (
                <Form form={editForm} layout="vertical">
                  <Form.Item
                    name="display_name"
                    label="展示名称"
                    rules={[{ required: true, message: '请输入展示名称' }]}
                  >
                    <Input placeholder="输入技能的展示名称" />
                  </Form.Item>
                  <Form.Item
                    name="description"
                    label="描述"
                    rules={[{ required: true, message: '请输入描述' }]}
                  >
                    <Input.TextArea rows={2} placeholder="技能描述..." />
                  </Form.Item>
                  <Form.Item name="version" label="版本">
                    <Input placeholder="1.0.0" />
                  </Form.Item>
                  <Form.Item name="tags" label="标签（逗号分隔）">
                    <Input placeholder="tag1, tag2" />
                  </Form.Item>
                  <Form.Item name="allowed_tools" label="允许工具（逗号分隔）">
                    <Input placeholder="search, database" />
                  </Form.Item>
                  <Form.Item
                    name="instructions"
                    label="执行指令"
                    rules={[{ required: true, message: '请输入执行指令' }]}
                  >
                    <Input.TextArea rows={8} placeholder={"1. 步骤一\n2. 步骤二\n3. 步骤三"} />
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: 'files',
              label: `附加文件 (${extraFiles.length})`,
              children: (
                <div>
                  <Table
                    rowKey="filename"
                    columns={fileColumns}
                    dataSource={extraFiles}
                    pagination={false}
                    size="small"
                    style={{ marginBottom: 16 }}
                    locale={{ emptyText: '暂无附加文件（通过 zip 上传添加）' }}
                  />
                  {editingFile && (
                    <div style={{ border: '1px solid #E3E6EA', borderRadius: 6, padding: 12 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <strong>{editingFile}</strong>
                        <Button
                          type="primary"
                          size="small"
                          icon={<SaveOutlined />}
                          onClick={saveFileContent}
                        >
                          保存文件
                        </Button>
                      </div>
                      <Input.TextArea
                        value={fileContent}
                        onChange={e => setFileContent(e.target.value)}
                        rows={12}
                        disabled={fileLoading}
                        style={{ fontFamily: 'monospace', fontSize: 13 }}
                      />
                    </div>
                  )}
                </div>
              ),
            },
          ]}
        />
      </Modal>
    </Card>
  );
}
