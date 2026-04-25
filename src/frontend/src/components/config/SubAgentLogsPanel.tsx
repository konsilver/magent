import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Button, Card, Col, DatePicker, Descriptions, Drawer, Row, Select, Space,
  Statistic, Table, Tabs, Tag, Timeline, Typography, message,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import { configFetch } from '../../utils/adminApi';

const { Text, Paragraph } = Typography;

interface UserOption { user_id: string; username: string }

interface SubAgentItem {
  id: string;
  trace_id: string | null;
  chat_id: string | null;
  session_title: string | null;
  user_id: string | null;
  user_name: string | null;
  subagent_id: string | null;
  subagent_name: string;
  subagent_type: string | null;
  plan_id: string | null;
  step_id: string | null;
  step_index: number | null;
  step_title: string | null;
  model: string | null;
  input_messages: any;
  output_content: string | null;
  intermediate_steps: any;
  token_usage: any;
  tool_calls_count: number;
  skill_calls_count: number;
  status: string;
  error_message: string | null;
  duration_ms: number | null;
  parent_subagent_log_id: string | null;
  created_at: string | null;
}

interface DetailData extends SubAgentItem {
  child_steps: SubAgentItem[];
  tool_calls: any[];
  skill_calls: any[];
}

const STATUS_COLORS: Record<string, string> = {
  running: 'processing',
  success: 'success',
  failed: 'error',
  cancelled: 'default',
};

export function SubAgentLogsPanel({ token }: { token: string }) {
  const [items, setItems] = useState<SubAgentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [subagentNames, setSubagentNames] = useState<string[]>([]);
  const [detail, setDetail] = useState<DetailData | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [filterUser, setFilterUser] = useState<string | undefined>();
  const [filterName, setFilterName] = useState<string | undefined>();
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);

  const loadFilters = useCallback(async () => {
    try {
      const [u, f] = await Promise.all([
        configFetch(token, '/v1/admin/chat-history/users'),
        configFetch(token, '/v1/admin/logs/subagents/filters'),
      ]);
      setUsers(u.data || []);
      setSubagentNames(f.data?.subagent_names || []);
    } catch (e: any) { message.error(e.message); }
  }, [token]);

  const loadItems = useCallback(async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(p), page_size: String(ps), only_parents: 'true',
      });
      if (filterUser) params.set('user_id', filterUser);
      if (filterName) params.set('subagent_name', filterName);
      if (filterStatus) params.set('status', filterStatus);
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/logs/subagents?${params}`);
      setItems(res.data?.items || []);
      setTotal(res.data?.pagination?.total_items || 0);
    } catch (e: any) { message.error(e.message); }
    setLoading(false);
  }, [token, page, pageSize, filterUser, filterName, filterStatus, filterDates]);

  const openDetail = async (row: SubAgentItem) => {
    setDetailLoading(true);
    try {
      const res = await configFetch(token, `/v1/admin/logs/subagents/${row.id}`);
      setDetail(res.data);
    } catch (e: any) { message.error(e.message); }
    setDetailLoading(false);
  };

  useEffect(() => { loadFilters(); }, [loadFilters]);
  useEffect(() => { loadItems(page, pageSize); /* eslint-disable-next-line */ }, [
    page, pageSize, filterUser, filterName, filterStatus, filterDates,
  ]);

  const { runningCount, failedCount, avgDuration } = useMemo(() => {
    let running = 0, failed = 0, durSum = 0, durN = 0;
    for (const i of items) {
      if (i.status === 'running') running++;
      else if (i.status === 'failed') failed++;
      if (i.duration_ms) { durSum += i.duration_ms; durN++; }
    }
    return {
      runningCount: running,
      failedCount: failed,
      avgDuration: durN > 0 ? Math.round(durSum / durN) : 0,
    };
  }, [items]);

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
    { title: '子智能体', dataIndex: 'subagent_name', key: 'subagent_name', width: 200,
      render: (_: any, r: SubAgentItem) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.3 }}>
          <Text strong>{r.subagent_name}</Text>
          {r.subagent_type && <Tag>{r.subagent_type}</Tag>}
        </Space>
      ),
    },
    { title: '模型', dataIndex: 'model', key: 'model', width: 140,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
    { title: '用户', dataIndex: 'user_name', key: 'user_name', width: 110 },
    { title: '会话', dataIndex: 'session_title', key: 'session_title', width: 180, ellipsis: true },
    { title: '工具/技能', key: 'counts', width: 120,
      render: (_: any, r: SubAgentItem) => (
        <Space size={4}>
          <Tag color="blue">{r.tool_calls_count} 工具</Tag>
          <Tag color="cyan">{r.skill_calls_count} 技能</Tag>
        </Space>
      ) },
    { title: 'Tokens', dataIndex: 'token_usage', key: 'token_usage', width: 110, align: 'right' as const,
      render: (u: any) => u?.total_tokens ? u.total_tokens.toLocaleString() : '-' },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 90, align: 'right' as const,
      render: (v: number | null) => v ? `${(v / 1000).toFixed(1)} s` : '-' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag> },
    { title: '操作', key: 'action', width: 80, fixed: 'right' as const,
      render: (_: any, r: SubAgentItem) => (
        <Button size="small" type="link" onClick={() => openDetail(r)}>详情</Button>
      ) },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small">
        <Space wrap>
          <Select placeholder="用户" allowClear style={{ width: 160 }}
            value={filterUser} onChange={setFilterUser} showSearch optionFilterProp="label"
            options={users.map(u => ({ value: u.user_id, label: u.username }))}/>
          <Select placeholder="子智能体" allowClear style={{ width: 200 }}
            value={filterName} onChange={setFilterName} showSearch
            options={subagentNames.map(n => ({ value: n, label: n }))}/>
          <Select placeholder="状态" allowClear style={{ width: 120 }}
            value={filterStatus} onChange={setFilterStatus}
            options={[
              { value: 'running', label: '运行中' },
              { value: 'success', label: '成功' },
              { value: 'failed', label: '失败' },
              { value: 'cancelled', label: '已取消' },
            ]}/>
          <DatePicker.RangePicker value={filterDates}
            onChange={(v) => setFilterDates(v as [Dayjs, Dayjs] | null)}/>
          <Button type="primary" onClick={() => { setPage(1); loadItems(1, pageSize); }}>查询</Button>
          <Button onClick={() => {
            setFilterUser(undefined); setFilterName(undefined); setFilterStatus(undefined);
            setFilterDates(null); setPage(1);
          }}>重置</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadItems(page, pageSize)} />
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={6}><Card size="small"><Statistic title="运行总数" value={total} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="本页 · 运行中" value={runningCount} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="本页 · 失败数" value={failedCount}
          valueStyle={failedCount > 0 ? { color: '#FC5D5D' } : undefined} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="本页 · 均耗时" value={avgDuration} suffix=" ms" /></Card></Col>
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
        title={detail ? `${detail.subagent_name} · 详情` : '子智能体详情'}
        width={900}
        open={!!detail}
        onClose={() => setDetail(null)}
        loading={detailLoading}
      >
        {detail && (
          <Tabs items={[
            {
              key: 'overview',
              label: '概览',
              children: (
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Descriptions size="small" column={2} bordered>
                    <Descriptions.Item label="名称">{detail.subagent_name}</Descriptions.Item>
                    <Descriptions.Item label="类型">{detail.subagent_type || '-'}</Descriptions.Item>
                    <Descriptions.Item label="状态">
                      <Tag color={STATUS_COLORS[detail.status]}>{detail.status}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="耗时">
                      {detail.duration_ms ? `${detail.duration_ms} ms` : '-'}
                    </Descriptions.Item>
                    <Descriptions.Item label="模型">{detail.model || '-'}</Descriptions.Item>
                    <Descriptions.Item label="plan_id">{detail.plan_id || '-'}</Descriptions.Item>
                    <Descriptions.Item label="用户">{detail.user_name || '-'}</Descriptions.Item>
                    <Descriptions.Item label="会话">{detail.session_title || '-'}</Descriptions.Item>
                    <Descriptions.Item label="工具调用数">{detail.tool_calls_count}</Descriptions.Item>
                    <Descriptions.Item label="技能调用数">{detail.skill_calls_count}</Descriptions.Item>
                    <Descriptions.Item label="trace_id" span={2}>
                      <Text code copyable>{detail.trace_id || '-'}</Text>
                    </Descriptions.Item>
                    {detail.token_usage && (
                      <Descriptions.Item label="Token" span={2}>
                        prompt: {detail.token_usage.prompt_tokens || 0}
                        completion: {detail.token_usage.completion_tokens || 0}
                        total: {detail.token_usage.total_tokens || 0}
                        LLM calls: {detail.token_usage.llm_call_count || 0}
                      </Descriptions.Item>
                    )}
                    {detail.error_message && (
                      <Descriptions.Item label="错误" span={2}>
                        <Text type="danger">{detail.error_message}</Text>
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                  <Card size="small" title="输入">
                    <pre style={{ maxHeight: 200, overflow: 'auto', margin: 0 }}>
                      {JSON.stringify(detail.input_messages, null, 2)}
                    </pre>
                  </Card>
                  <Card size="small" title="输出">
                    <Paragraph style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto' }}>
                      {detail.output_content || '—'}
                    </Paragraph>
                  </Card>
                </Space>
              ),
            },
            {
              key: 'steps',
              label: `子步骤 (${detail.child_steps.length})`,
              children: detail.child_steps.length === 0 ? <Text type="secondary">—</Text> : (
                <Timeline
                  items={detail.child_steps.map(s => ({
                    color: STATUS_COLORS[s.status] === 'success' ? 'green'
                         : STATUS_COLORS[s.status] === 'error' ? 'red' : 'blue',
                    children: (
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        <Text strong>
                          {s.step_index !== null ? `步骤 ${s.step_index}：` : ''}{s.step_title || s.subagent_name}
                        </Text>
                        <Space size={8} wrap>
                          <Tag color={STATUS_COLORS[s.status]}>{s.status}</Tag>
                          {s.duration_ms && <Tag>{s.duration_ms} ms</Tag>}
                          <Tag color="blue">{s.tool_calls_count} 工具</Tag>
                        </Space>
                        {s.output_content && (
                          <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#555' }}>
                            {s.output_content.slice(0, 400)}{s.output_content.length > 400 ? '…' : ''}
                          </Paragraph>
                        )}
                      </Space>
                    ),
                  }))}
                />
              ),
            },
            {
              key: 'tools',
              label: `内部工具调用 (${detail.tool_calls.length})`,
              children: detail.tool_calls.length === 0 ? <Text type="secondary">—</Text> : (
                <Table
                  size="small"
                  dataSource={detail.tool_calls}
                  rowKey="id"
                  pagination={false}
                  columns={[
                    { title: '时间', dataIndex: 'created_at', width: 170,
                      render: (v: string) => new Date(v).toLocaleString('zh-CN') },
                    { title: '工具', dataIndex: 'tool_name' },
                    { title: '状态', dataIndex: 'status', width: 80,
                      render: (v: string) => <Tag color={v === 'success' ? 'success' : 'error'}>{v}</Tag> },
                    { title: '耗时', dataIndex: 'duration_ms', width: 90,
                      render: (v: number) => v !== null ? `${v} ms` : '-' },
                  ]}
                  expandable={{
                    expandedRowRender: (r: any) => (
                      <pre style={{ maxHeight: 200, overflow: 'auto', margin: 0, fontSize: 12 }}>
                        {JSON.stringify({ args: r.tool_args, result: r.tool_result }, null, 2)}
                      </pre>
                    ),
                  }}
                />
              ),
            },
            {
              key: 'skills',
              label: `内部技能调用 (${detail.skill_calls.length})`,
              children: detail.skill_calls.length === 0 ? <Text type="secondary">—</Text> : (
                <Table
                  size="small"
                  dataSource={detail.skill_calls}
                  rowKey="id"
                  pagination={false}
                  columns={[
                    { title: '时间', dataIndex: 'created_at', width: 170,
                      render: (v: string) => new Date(v).toLocaleString('zh-CN') },
                    { title: '技能', dataIndex: 'skill_name' },
                    { title: '脚本', dataIndex: 'script_name' },
                    { title: '方式', dataIndex: 'invocation_type', width: 100 },
                    { title: '状态', dataIndex: 'status', width: 80,
                      render: (v: string) => <Tag color={v === 'success' ? 'success' : 'error'}>{v}</Tag> },
                  ]}
                />
              ),
            },
          ]} />
        )}
      </Drawer>
    </Space>
  );
}
