import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Input, InputNumber, Select, Space, Switch,
  Typography, Upload, message,
} from 'antd';
import {
  ExportOutlined, ImportOutlined, SaveOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { adminFetch } from '../../utils/adminApi';
import type { SystemConfig, SystemConfigGroup, TestConnectionResult } from '../../types';

const { Text } = Typography;
const { TextArea } = Input;

const SERVICE_GROUP_META: Record<string, { icon: string; title: string }> = {
  query_database: { icon: '\u{1F5C4}\uFE0F', title: '数据库查询服务' },
  knowledge_base: { icon: '\u{1F4DA}', title: '知识库服务' },
  industry: { icon: '\u{1F3ED}', title: '产业知识中心' },
  file_parser: { icon: '\u{1F4C4}', title: '文件解析服务' },
  internet_search: { icon: '\u{1F50D}', title: '互联网搜索' },
};

export function ServiceConfigsEditor({ token, fetchFn = adminFetch }: { token: string; fetchFn?: typeof adminFetch }) {
  const [groups, setGroups] = useState<SystemConfigGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingGroup, setTestingGroup] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string | null>>({});
  const [dirty, setDirty] = useState(false);

  const reload = useCallback(async () => {
    try {
      const res = await fetchFn(token, '/v1/service-configs');
      const data = (res.data || []) as SystemConfigGroup[];
      setGroups(data);
      const vals: Record<string, string | null> = {};
      for (const g of data) {
        for (const item of g.items) {
          vals[item.config_key] = item.config_value;
        }
      }
      setEditValues(vals);
      setDirty(false);
    } catch (e) {
      message.error(`加载失败：${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { reload(); }, [reload]);

  const handleChange = (key: string, value: string | null) => {
    setEditValues(prev => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const items = Object.entries(editValues).map(([key, value]) => ({ key, value }));
      await fetchFn(token, '/v1/service-configs', {
        method: 'PUT',
        body: JSON.stringify({ items }),
      });
      message.success('服务配置已保存');
      setDirty(false);
      reload();
    } catch (e) {
      message.error(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (groupKey: string) => {
    setTestingGroup(groupKey);
    try {
      if (dirty) {
        const items = Object.entries(editValues).map(([key, value]) => ({ key, value }));
        await fetchFn(token, '/v1/service-configs', {
          method: 'PUT',
          body: JSON.stringify({ items }),
        });
        setDirty(false);
      }
      const res = await fetchFn(token, `/v1/service-configs/test/${groupKey}`, { method: 'POST' });
      const r = res.data as TestConnectionResult;
      if (r.success) {
        message.success(`连接成功 (${r.latency_ms}ms)`);
      } else {
        message.error(`连接失败：${r.error}`);
      }
    } catch (e) {
      message.error(`测试失败：${(e as Error).message}`);
    } finally {
      setTestingGroup(null);
    }
  };

  const handleExport = async () => {
    try {
      const res = await fetchFn(token, '/v1/service-configs/export');
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `service-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      message.success('服务配置已导出');
    } catch (e) {
      message.error(`导出失败：${(e as Error).message}`);
    }
  };

  const handleImport = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      await fetchFn(token, '/v1/service-configs/import', {
        method: 'POST',
        body: JSON.stringify(data),
      });
      message.success('服务配置已导入');
      reload();
    } catch (e) {
      message.error(`导入失败：${(e as Error).message}`);
    }
    return false;
  };

  const renderConfigField = (item: SystemConfig) => {
    const key = item.config_key;
    const value = editValues[key] ?? '';
    const shortKey = key.split('.').pop() || key;

    if (item.is_secret) {
      return (
        <Input.Password
          value={value || ''}
          onChange={e => handleChange(key, e.target.value || null)}
          placeholder={`输入${item.display_name}`}
        />
      );
    }
    if (shortKey === 'timeout' || shortKey === 'retry_times' || shortKey === 'max_output_tokens' || shortKey === 'detail_max_chars') {
      return (
        <InputNumber
          style={{ width: '100%' }}
          value={value ? Number(value) : undefined}
          onChange={v => handleChange(key, v != null ? String(v) : null)}
          placeholder={item.description || undefined}
          min={0}
        />
      );
    }
    if (shortKey === 'formula_enable' || shortKey === 'table_enable') {
      return (
        <Switch
          checked={value === 'true'}
          onChange={v => handleChange(key, v ? 'true' : 'false')}
        />
      );
    }
    if (shortKey === 'engine' && key.startsWith('internet_search.')) {
      return (
        <Select
          style={{ width: '100%' }}
          value={value || undefined}
          onChange={v => handleChange(key, v)}
          options={[
            { value: 'tavily', label: 'Tavily' },
            { value: 'baidu', label: '百度搜索' },
          ]}
          placeholder="选择搜索引擎"
        />
      );
    }
    if (shortKey === 'provider') {
      return (
        <Select
          style={{ width: '100%' }}
          value={value || undefined}
          onChange={v => handleChange(key, v)}
          options={[
            { value: 'dify', label: 'Dify' },
            { value: 'custom', label: '自定义' },
          ]}
          placeholder="选择知识库后端"
        />
      );
    }
    if (shortKey === 'backend') {
      return (
        <Select
          style={{ width: '100%' }}
          value={value || undefined}
          onChange={v => handleChange(key, v)}
          options={[
            { value: 'pipeline', label: 'Pipeline' },
            { value: 'paddle', label: 'Paddle' },
          ]}
          placeholder="选择解析后端"
        />
      );
    }
    if (shortKey === 'parse_method') {
      return (
        <Select
          style={{ width: '100%' }}
          value={value || undefined}
          onChange={v => handleChange(key, v)}
          options={[
            { value: 'auto', label: 'Auto' },
            { value: 'ocr', label: 'OCR' },
            { value: 'txt', label: 'TXT' },
          ]}
          placeholder="选择解析方法"
        />
      );
    }
    if (shortKey === 'allowed_dataset_ids') {
      return (
        <TextArea
          rows={2}
          value={value || ''}
          onChange={e => handleChange(key, e.target.value || null)}
          placeholder="逗号分隔的数据集 ID，为空则全部允许"
        />
      );
    }
    return (
      <Input
        value={value || ''}
        onChange={e => handleChange(key, e.target.value || null)}
        placeholder={item.description || `输入${item.display_name}`}
      />
    );
  };

  if (loading) {
    return <Card loading />;
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={!dirty}
            onClick={handleSave}
          >
            保存所有配置
          </Button>
          {dirty && <Text type="warning" style={{ fontSize: 12 }}>有未保存的修改</Text>}
        </Space>
        <Space>
          <Button icon={<ExportOutlined />} onClick={handleExport}>导出配置</Button>
          <Upload accept=".json" showUploadList={false} beforeUpload={handleImport}>
            <Button icon={<ImportOutlined />}>导入配置</Button>
          </Upload>
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(520px, 1fr))', gap: 16 }}>
        {groups.map(group => {
          const meta = SERVICE_GROUP_META[group.group_key] || { icon: '\u2699\uFE0F', title: group.label };
          const canTest = group.items.some(i =>
            i.config_key.endsWith('.url') || i.config_key.endsWith('.api_url') || i.config_key.endsWith('.api_key') || i.config_key.endsWith('.tavily_api_key')
          );
          return (
            <Card
              key={group.group_key}
              bordered={false}
              style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}
              title={
                <Space>
                  <span>{meta.icon}</span>
                  <Text strong>{meta.title}</Text>
                </Space>
              }
              extra={
                canTest && (
                  <Button
                    icon={<ThunderboltOutlined />}
                    loading={testingGroup === group.group_key}
                    onClick={() => handleTest(group.group_key)}
                    size="small"
                  >
                    测试连接
                  </Button>
                )
              }
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {group.items
                  .filter(item => {
                    // For internet_search group, only show the API key that matches the selected engine
                    if (group.group_key === 'internet_search') {
                      const engine = editValues['internet_search.engine'] || 'tavily';
                      if (item.config_key === 'internet_search.tavily_api_key' && engine !== 'tavily') return false;
                      if (item.config_key === 'internet_search.baidu_api_key' && engine !== 'baidu') return false;
                    }
                    return true;
                  })
                  .map(item => (
                  <div key={item.config_key}>
                    <div style={{ marginBottom: 4 }}>
                      <Text style={{ fontSize: 13 }}>{item.display_name}</Text>
                      {item.description && (
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>{item.description}</Text>
                      )}
                    </div>
                    {renderConfigField(item)}
                  </div>
                ))}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
