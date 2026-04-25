import { useState, useEffect, useCallback } from 'react';
import {
  Button, Drawer, Form, Input, Modal, Popconfirm,
  Space, Switch, Table, Tabs, Tag, Upload, message,
} from 'antd';
import {
  PlusOutlined, EyeOutlined, EditOutlined, DeleteOutlined,
  ArrowUpOutlined, ArrowDownOutlined, HistoryOutlined, RollbackOutlined,
  ExportOutlined, ImportOutlined,
} from '@ant-design/icons';
import { adminFetch } from '../../utils/adminApi';
import { formatDateTime } from '../../utils/date';

interface PromptPart {
  part_id: string;
  content: string;
  display_name: string;
  sort_order: number;
  is_enabled: boolean;
  source: string;
  updated_at?: string;
  created_by?: string;
}

interface PromptVersion {
  version_id: number;
  part_id: string;
  display_name: string;
  sort_order: number;
  is_enabled: boolean;
  created_at: string | null;
  created_by: string | null;
  content_length: number;
}

export function PromptsEditor({ token, fetchFn = adminFetch }: { token: string; fetchFn?: typeof adminFetch }) {
  const [parts, setParts] = useState<PromptPart[]>([]);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [editingPart, setEditingPart] = useState<PromptPart | null>(null);
  const [fsContent, setFsContent] = useState<string | null>(null);
  const [form] = Form.useForm();
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewContent, setPreviewContent] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);

  // Version history state
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionPreviewOpen, setVersionPreviewOpen] = useState(false);
  const [versionPreviewContent, setVersionPreviewContent] = useState('');
  const [versionPreviewLoading, setVersionPreviewLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchFn(token, '/v1/admin/prompts/parts');
      setParts(res.data || []);
    } catch (e) {
      message.error(`加载失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  // ── Edit ────────────────────────────────────────────────────────

  const openEdit = async (record: PromptPart) => {
    setEditingPart(record);
    setEditOpen(true);
    setFsContent(null);
    setVersions([]);
    try {
      const res = await fetchFn(token, `/v1/admin/prompts/parts/${record.part_id}`);
      const d = res.data;
      form.setFieldsValue({
        content: d.current.content,
        display_name: d.current.display_name,
        sort_order: d.current.sort_order,
        is_enabled: d.current.is_enabled,
      });
      setFsContent(d.filesystem_content);
    } catch (e) {
      message.error(`加载详情失败：${(e as Error).message}`);
    }
    // Load versions in parallel
    loadVersions(record.part_id);
  };

  const handleSave = async () => {
    if (!editingPart) return;
    try {
      const values = await form.validateFields();
      await fetchFn(token, `/v1/admin/prompts/parts/${editingPart.part_id}`, {
        method: 'PUT',
        body: JSON.stringify(values),
      });
      message.success('保存成功');
      setEditOpen(false);
      setEditingPart(null);
      form.resetFields();
      reload();
    } catch (e) {
      if (e && typeof e === 'object' && 'message' in e) {
        message.error(`保存失败：${(e as Error).message}`);
      }
    }
  };

  // ── Version history ────────────────────────────────────────────

  const loadVersions = async (partId: string) => {
    setVersionsLoading(true);
    try {
      const res = await fetchFn(token, `/v1/admin/prompts/parts/${partId}/versions`);
      setVersions(res.data || []);
    } catch {
      // Silently fail — versions may not exist yet
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  };

  const previewVersion = async (partId: string, versionId: number) => {
    setVersionPreviewOpen(true);
    setVersionPreviewLoading(true);
    try {
      const res = await fetchFn(token, `/v1/admin/prompts/parts/${partId}/versions/${versionId}`);
      setVersionPreviewContent(res.data.content || '');
    } catch (e) {
      message.error(`加载版本内容失败：${(e as Error).message}`);
    } finally {
      setVersionPreviewLoading(false);
    }
  };

  const restoreVersion = async (partId: string, versionId: number) => {
    try {
      await fetchFn(token, `/v1/admin/prompts/parts/${partId}/versions/${versionId}/restore`, {
        method: 'POST',
      });
      message.success('版本已恢复');
      // Refresh the editor content
      const res = await fetchFn(token, `/v1/admin/prompts/parts/${partId}`);
      const d = res.data;
      form.setFieldsValue({
        content: d.current.content,
        display_name: d.current.display_name,
        sort_order: d.current.sort_order,
        is_enabled: d.current.is_enabled,
      });
      // Reload versions list
      loadVersions(partId);
    } catch (e) {
      message.error(`恢复失败：${(e as Error).message}`);
    }
  };

  // ── Create ────────────────────────────────────────────────────────

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields();
      const partId = values.part_id;
      await fetchFn(token, `/v1/admin/prompts/parts/${partId}`, {
        method: 'PUT',
        body: JSON.stringify({
          content: values.content,
          display_name: values.display_name,
          sort_order: values.sort_order ?? 99,
          is_enabled: true,
        }),
      });
      message.success('模块创建成功');
      setCreateOpen(false);
      createForm.resetFields();
      reload();
    } catch (e) {
      if (e && typeof e === 'object' && 'message' in e) {
        message.error(`创建失败：${(e as Error).message}`);
      }
    }
  };

  // ── Delete (restore filesystem) ──────────────────────────────────

  const handleDelete = async (partId: string) => {
    try {
      await fetchFn(token, `/v1/admin/prompts/parts/${partId}`, { method: 'DELETE' });
      message.success('已恢复为文件系统版本');
      reload();
    } catch (e) {
      message.error(`删除失败：${(e as Error).message}`);
    }
  };

  // ── Toggle enabled ────────────────────────────────────────────────

  const handleToggle = async (record: PromptPart, enabled: boolean) => {
    try {
      await fetchFn(token, `/v1/admin/prompts/parts/${record.part_id}`, {
        method: 'PUT',
        body: JSON.stringify({
          content: record.content,
          display_name: record.display_name,
          sort_order: record.sort_order,
          is_enabled: enabled,
        }),
      });
      message.success(enabled ? '已启用' : '已禁用');
      reload();
    } catch (e) {
      message.error(`操作失败：${(e as Error).message}`);
    }
  };

  // ── Move (reorder) ────────────────────────────────────────────────

  const handleMove = async (index: number, direction: 'up' | 'down') => {
    const swapIndex = direction === 'up' ? index - 1 : index + 1;
    if (swapIndex < 0 || swapIndex >= parts.length) return;

    const newParts = [...parts];
    const a = newParts[index];
    const b = newParts[swapIndex];

    // Swap sort_order values
    const tempOrder = a.sort_order;
    a.sort_order = b.sort_order;
    b.sort_order = tempOrder;

    // Save both to DB
    try {
      await fetchFn(token, '/v1/admin/prompts/order', {
        method: 'PUT',
        body: JSON.stringify({
          order: [
            { part_id: a.part_id, sort_order: a.sort_order },
            { part_id: b.part_id, sort_order: b.sort_order },
          ],
        }),
      });
      // Only DB-sourced parts can be reordered via the order API,
      // but we also need to ensure both are saved to DB first.
      // For file-only parts, we need to save them first.
      if (a.source === 'file') {
        await fetchFn(token, `/v1/admin/prompts/parts/${a.part_id}`, {
          method: 'PUT',
          body: JSON.stringify({
            content: a.content,
            display_name: a.display_name,
            sort_order: a.sort_order,
            is_enabled: a.is_enabled,
          }),
        });
      }
      if (b.source === 'file') {
        await fetchFn(token, `/v1/admin/prompts/parts/${b.part_id}`, {
          method: 'PUT',
          body: JSON.stringify({
            content: b.content,
            display_name: b.display_name,
            sort_order: b.sort_order,
            is_enabled: b.is_enabled,
          }),
        });
      }
      reload();
    } catch (e) {
      message.error(`排序失败：${(e as Error).message}`);
    }
  };

  // ── Export / Import ─────────────────────────────────────────────

  const handleExport = async () => {
    try {
      const res = await fetchFn(token, '/v1/admin/prompts/export');
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `prompts-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('提示词配置已导出');
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`);
    }
  };

  const handleImportJson = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!Array.isArray(data)) { message.error('JSON 格式错误：需要数组'); return false; }
      await fetchFn(token, '/v1/admin/prompts/import', {
        method: 'POST',
        body: JSON.stringify({ parts: data, overwrite: true }),
      });
      message.success('提示词配置已导入');
      reload();
    } catch (e) {
      message.error(`导入失败：${(e as Error).message}`);
    }
    return false;
  };

  // ── Preview ────────────────────────────────────────────────────────

  const handlePreview = async () => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    try {
      const res = await fetchFn(token, '/v1/admin/prompts/preview', {
        method: 'POST',
      });
      setPreviewContent(res.data.prompt || '');
    } catch (e) {
      message.error(`预览失败：${(e as Error).message}`);
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Version history columns ──────────────────────────────────────

  const versionColumns = [
    {
      title: '版本 ID',
      dataIndex: 'version_id',
      key: 'version_id',
      width: 80,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (val: string | null) => formatDateTime(val, '-'),
    },
    {
      title: '操作者',
      dataIndex: 'created_by',
      key: 'created_by',
      width: 100,
      render: (val: string | null) => val || '-',
    },
    {
      title: '内容长度',
      dataIndex: 'content_length',
      key: 'content_length',
      width: 90,
      render: (val: number) => `${val} 字符`,
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: unknown, record: PromptVersion) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => previewVersion(record.part_id, record.version_id)}
          >
            预览
          </Button>
          <Popconfirm
            title="恢复到此版本？当前内容将被保存为新版本。"
            onConfirm={() => restoreVersion(record.part_id, record.version_id)}
          >
            <Button type="link" size="small" icon={<RollbackOutlined />}>
              恢复
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Table columns ──────────────────────────────────────────────────

  const columns = [
    {
      title: '排序',
      dataIndex: 'sort_order',
      key: 'sort_order',
      width: 70,
    },
    {
      title: 'Part ID',
      dataIndex: 'part_id',
      key: 'part_id',
      width: 200,
      ellipsis: true,
    },
    {
      title: '名称',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 150,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 90,
      render: (source: string) => (
        <Tag color={source === 'database' ? 'orange' : 'blue'}>
          {source === 'database' ? '数据库' : '文件'}
        </Tag>
      ),
    },
    {
      title: '启用',
      key: 'is_enabled',
      width: 80,
      render: (_: unknown, record: PromptPart) => (
        <Switch
          size="small"
          checked={record.is_enabled}
          onChange={(checked) => handleToggle(record, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: unknown, record: PromptPart, index: number) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<ArrowUpOutlined />}
            disabled={index === 0}
            onClick={() => handleMove(index, 'up')}
          />
          <Button
            type="link"
            size="small"
            icon={<ArrowDownOutlined />}
            disabled={index === parts.length - 1}
            onClick={() => handleMove(index, 'down')}
          />
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          {record.source === 'database' && (
            <Popconfirm
              title="删除数据库覆盖，恢复为文件系统版本？"
              onConfirm={() => handleDelete(record.part_id)}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                恢复
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新增模块
        </Button>
        <Button icon={<EyeOutlined />} onClick={handlePreview}>
          预览完整提示词
        </Button>
        <Button icon={<ExportOutlined />} onClick={handleExport}>导出</Button>
        <Upload accept=".json" showUploadList={false} beforeUpload={handleImportJson}>
          <Button icon={<ImportOutlined />}>导入</Button>
        </Upload>
      </Space>

      <Table
        rowKey="part_id"
        columns={columns}
        dataSource={parts}
        loading={loading}
        pagination={false}
        size="middle"
      />

      {/* Edit Drawer — with Tabs for editing + version history */}
      <Drawer
        title={`编辑提示词模块: ${editingPart?.part_id || ''}`}
        open={editOpen}
        onClose={() => {
          setEditOpen(false);
          setEditingPart(null);
          form.resetFields();
          setVersions([]);
        }}
        width={750}
        extra={
          <Button type="primary" onClick={handleSave}>保存</Button>
        }
      >
        <Tabs
          defaultActiveKey="edit"
          items={[
            {
              key: 'edit',
              label: '编辑内容',
              children: (
                <>
                  <Form form={form} layout="vertical">
                    <Form.Item
                      name="display_name"
                      label="显示名称"
                      rules={[{ required: true, message: '请输入显示名称' }]}
                    >
                      <Input placeholder="角色定义" />
                    </Form.Item>
                    <Form.Item name="sort_order" label="排序">
                      <Input type="number" placeholder="0" />
                    </Form.Item>
                    <Form.Item name="is_enabled" label="启用" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                    <Form.Item
                      name="content"
                      label="内容 (Markdown)"
                      rules={[{ required: true, message: '请输入内容' }]}
                    >
                      <Input.TextArea
                        rows={18}
                        style={{ fontFamily: 'monospace', fontSize: 13 }}
                        placeholder="# 系统提示词模块内容..."
                      />
                    </Form.Item>
                  </Form>
                  {fsContent !== null && (
                    <div style={{ marginTop: 16 }}>
                      <h4 style={{ color: '#808080' }}>文件系统原版内容（只读参考）</h4>
                      <pre style={{
                        background: '#F5F6F7',
                        padding: 12,
                        borderRadius: 4,
                        whiteSpace: 'pre-wrap',
                        fontSize: 12,
                        maxHeight: 300,
                        overflow: 'auto',
                      }}>
                        {fsContent}
                      </pre>
                    </div>
                  )}
                </>
              ),
            },
            {
              key: 'versions',
              label: (
                <span>
                  <HistoryOutlined style={{ marginRight: 4 }} />
                  版本历史 ({versions.length})
                </span>
              ),
              children: (
                <div>
                  {versions.length === 0 && !versionsLoading ? (
                    <div style={{ textAlign: 'center', color: '#B3B3B3', padding: 40 }}>
                      暂无版本历史（每次保存会自动记录一个版本）
                    </div>
                  ) : (
                    <Table
                      rowKey="version_id"
                      columns={versionColumns}
                      dataSource={versions}
                      loading={versionsLoading}
                      pagination={false}
                      size="small"
                    />
                  )}
                </div>
              ),
            },
          ]}
        />
      </Drawer>

      {/* Create Modal */}
      <Modal
        title="新增提示词模块"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={handleCreate}
        okText="创建"
        cancelText="取消"
        width={640}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="part_id"
            label="Part ID"
            rules={[{ required: true, message: '请输入 Part ID (如 system/99_custom)' }]}
          >
            <Input placeholder="system/99_custom" />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="显示名称"
            rules={[{ required: true, message: '请输入显示名称' }]}
          >
            <Input placeholder="自定义模块" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序" initialValue={99}>
            <Input type="number" />
          </Form.Item>
          <Form.Item
            name="content"
            label="内容 (Markdown)"
            rules={[{ required: true, message: '请输入内容' }]}
          >
            <Input.TextArea
              rows={10}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
              placeholder="# 模块标题&#10;&#10;内容..."
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Full Prompt Preview Modal */}
      <Modal
        title="完整系统提示词预览"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={800}
      >
        {previewLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>
        ) : (
          <pre style={{
            background: '#F5F6F7',
            padding: 16,
            borderRadius: 4,
            whiteSpace: 'pre-wrap',
            fontSize: 13,
            maxHeight: '70vh',
            overflow: 'auto',
          }}>
            {previewContent}
          </pre>
        )}
      </Modal>

      {/* Version Preview Modal */}
      <Modal
        title="版本内容预览"
        open={versionPreviewOpen}
        onCancel={() => setVersionPreviewOpen(false)}
        footer={null}
        width={750}
      >
        {versionPreviewLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>
        ) : (
          <pre style={{
            background: '#F5F6F7',
            padding: 16,
            borderRadius: 4,
            whiteSpace: 'pre-wrap',
            fontSize: 13,
            maxHeight: '70vh',
            overflow: 'auto',
          }}>
            {versionPreviewContent}
          </pre>
        )}
      </Modal>
    </>
  );
}
