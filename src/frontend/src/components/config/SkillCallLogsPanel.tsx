import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Col, DatePicker, Descriptions, Drawer, Row, Select, Space,
  Statistic, Table, Tabs, Tag, Typography, message,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import { configFetch } from '../../utils/adminApi';

const { Text, Paragraph } = Typography;

interface UserOption { user_id: string; username: string }

interface SkillItem {
  id: string;
  trace_id: string | null;
  chat_id: string | null;
  session_title: string | null;
  user_id: string | null;
  user_name: string | null;
  skill_id: string;
  skill_name: string | null;
  skill_version: string | null;
  skill_source: string | null;
  invocation_type: string;
  script_name: string | null;
  script_language: string | null;
  script_args: any;
  script_stdin: string | null;
  script_stdout: string | null;
  script_stderr: string | null;
  output_truncated: boolean;
  exit_code: number | null;
  status: string;
  error_message: string | null;
  duration_ms: number | null;
  source: string;
  subagent_log_id: string | null;
  created_at: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  success: 'success',
  failed: 'error',
  timeout: 'warning',
};

const INVOCATION_COLORS: Record<string, string> = {
  view: 'blue',
  run_script: 'green',
  auto_load: 'geekblue',
};

export function SkillCallLogsPanel({ token }: { token: string }) {
  const [items, setItems] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [skillNames, setSkillNames] = useState<string[]>([]);
  const [detail, setDetail] = useState<SkillItem | null>(null);

  const [filterUser, setFilterUser] = useState<string | undefined>();
  const [filterSkill, setFilterSkill] = useState<string | undefined>();
  const [filterInvoke, setFilterInvoke] = useState<string | undefined>();
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);

  const loadFilters = useCallback(async () => {
    try {
      const [u, f] = await Promise.all([
        configFetch(token, '/v1/admin/chat-history/users'),
        configFetch(token, '/v1/admin/logs/skills/filters'),
      ]);
      setUsers(u.data || []);
      setSkillNames(f.data?.skill_names || []);
    } catch (e: any) { message.error(e.message); }
  }, [token]);

  const loadItems = useCallback(async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(ps) });
      if (filterUser) params.set('user_id', filterUser);
      if (filterSkill) params.set('skill_name', filterSkill);
      if (filterInvoke) params.set('invocation_type', filterInvoke);
      if (filterStatus) params.set('status', filterStatus);
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/logs/skills?${params}`);
      setItems(res.data?.items || []);
      setTotal(res.data?.pagination?.total_items || 0);
    } catch (e: any) { message.error(e.message); }
    setLoading(false);
  }, [token, page, pageSize, filterUser, filterSkill, filterInvoke, filterStatus, filterDates]);

  useEffect(() => { loadFilters(); }, [loadFilters]);
  useEffect(() => { loadItems(page, pageSize); /* eslint-disable-next-line */ }, [
    page, pageSize, filterUser, filterSkill, filterInvoke, filterStatus, filterDates,
  ]);

  const totalCalls = total;
  const runScriptCount = items.filter(i => i.invocation_type === 'run_script').length;
  const viewCount = items.filter(i => i.invocation_type === 'view' || i.invocation_type === 'auto_load').length;
  const failedCount = items.filter(i => i.status !== 'success').length;

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
    { title: '技能', dataIndex: 'skill_name', key: 'skill_name', width: 200,
      render: (_: any, r: SkillItem) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.3 }}>
          <Text strong>{r.skill_name || r.skill_id}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.skill_id}{r.skill_version ? ` v${r.skill_version}` : ''}
          </Text>
        </Space>
      ) },
    { title: '调用方式', dataIndex: 'invocation_type', key: 'invocation_type', width: 110,
      render: (v: string) => <Tag color={INVOCATION_COLORS[v] || 'default'}>{v}</Tag> },
    { title: '脚本', dataIndex: 'script_name', key: 'script_name', width: 180, ellipsis: true,
      render: (v: string | null) => v || '-' },
    { title: '用户', dataIndex: 'user_name', key: 'user_name', width: 110 },
    { title: '会话', dataIndex: 'session_title', key: 'session_title', width: 180, ellipsis: true },
    { title: 'exit', dataIndex: 'exit_code', key: 'exit_code', width: 70, align: 'right' as const,
      render: (v: number | null) => v ?? '-' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 90, align: 'right' as const,
      render: (v: number | null) => v !== null ? `${v} ms` : '-' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag> },
    { title: '操作', key: 'action', width: 80, fixed: 'right' as const,
      render: (_: any, r: SkillItem) => (
        <Button size="small" type="link" onClick={() => setDetail(r)}>详情</Button>
      ) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small">
        <Space wrap>
          <Select placeholder="用户" allowClear style={{ width: 160 }}
            value={filterUser} onChange={setFilterUser} showSearch optionFilterProp="label"
            options={users.map(u => ({ value: u.user_id, label: u.username }))}/>
          <Select placeholder="技能" allowClear style={{ width: 200 }}
            value={filterSkill} onChange={setFilterSkill} showSearch
            options={skillNames.map(n => ({ value: n, label: n }))}/>
          <Select placeholder="调用方式" allowClear style={{ width: 130 }}
            value={filterInvoke} onChange={setFilterInvoke}
            options={[
              { value: 'view', label: '查看 SKILL.md' },
              { value: 'run_script', label: '执行脚本' },
              { value: 'auto_load', label: '自动加载' },
            ]}/>
          <Select placeholder="状态" allowClear style={{ width: 120 }}
            value={filterStatus} onChange={setFilterStatus}
            options={[
              { value: 'success', label: '成功' },
              { value: 'failed', label: '失败' },
              { value: 'timeout', label: '超时' },
            ]}/>
          <DatePicker.RangePicker value={filterDates}
            onChange={(v) => setFilterDates(v as [Dayjs, Dayjs] | null)}/>
          <Button type="primary" onClick={() => { setPage(1); loadItems(1, pageSize); }}>查询</Button>
          <Button onClick={() => {
            setFilterUser(undefined); setFilterSkill(undefined); setFilterInvoke(undefined);
            setFilterStatus(undefined); setFilterDates(null); setPage(1);
          }}>重置</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadItems(page, pageSize)} />
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={6}><Card size="small"><Statistic title="调用总数" value={totalCalls} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="执行脚本" value={runScriptCount} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="查看/加载" value={viewCount} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="失败数" value={failedCount}
          valueStyle={failedCount > 0 ? { color: '#FC5D5D' } : undefined} /></Card></Col>
      </Row>

      <Table
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        scroll={{ x: 1400 }}
        pagination={{
          current: page, pageSize, total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />

      <Drawer
        title={detail ? `${detail.skill_name || detail.skill_id} · 调用详情` : '调用详情'}
        width={820}
        open={!!detail}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <Tabs items={[
            {
              key: 'overview',
              label: '概览',
              children: (
                <Descriptions size="small" column={2} bordered>
                  <Descriptions.Item label="技能 ID">{detail.skill_id}</Descriptions.Item>
                  <Descriptions.Item label="技能名">{detail.skill_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="版本">{detail.skill_version || '-'}</Descriptions.Item>
                  <Descriptions.Item label="来源">{detail.skill_source || '-'}</Descriptions.Item>
                  <Descriptions.Item label="调用方式">
                    <Tag color={INVOCATION_COLORS[detail.invocation_type]}>{detail.invocation_type}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <Tag color={STATUS_COLORS[detail.status]}>{detail.status}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="脚本">{detail.script_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="语言">{detail.script_language || '-'}</Descriptions.Item>
                  <Descriptions.Item label="耗时">
                    {detail.duration_ms !== null ? `${detail.duration_ms} ms` : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="退出码">{detail.exit_code ?? '-'}</Descriptions.Item>
                  <Descriptions.Item label="用户">{detail.user_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="会话">{detail.session_title || '-'}</Descriptions.Item>
                  <Descriptions.Item label="trace_id" span={2}>
                    <Text code copyable>{detail.trace_id || '-'}</Text>
                  </Descriptions.Item>
                  {detail.subagent_log_id && (
                    <Descriptions.Item label="所属子智能体" span={2}>
                      <Text code>{detail.subagent_log_id}</Text>
                    </Descriptions.Item>
                  )}
                  {detail.error_message && (
                    <Descriptions.Item label="错误" span={2}>
                      <Text type="danger">{detail.error_message}</Text>
                    </Descriptions.Item>
                  )}
                </Descriptions>
              ),
            },
            {
              key: 'io',
              label: '入参 / 输出',
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Card size="small" title="入参 (script_args)">
                    <pre style={{ maxHeight: 200, overflow: 'auto', margin: 0 }}>
                      {JSON.stringify(detail.script_args, null, 2)}
                    </pre>
                  </Card>
                  {detail.script_stdin && (
                    <Card size="small" title="stdin">
                      <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{detail.script_stdin}</Paragraph>
                    </Card>
                  )}
                  <Card size="small" title={`stdout${detail.output_truncated ? ' (已截断)' : ''}`}>
                    <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>
                      {detail.script_stdout || '—'}
                    </Paragraph>
                  </Card>
                  <Card size="small" title="stderr">
                    <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>
                      {detail.script_stderr || '—'}
                    </Paragraph>
                  </Card>
                </Space>
              ),
            },
          ]} />
        )}
      </Drawer>
    </Space>
  );
}
