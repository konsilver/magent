import { useState, useEffect, useCallback } from 'react';
import {
  Button, Card, Col, DatePicker, Descriptions, Drawer, Row, Select,
  Space, Statistic, Table, Tag, Typography, message,
} from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import { configFetch } from '../../utils/adminApi';

const { Text } = Typography;

interface UserOption { user_id: string; username: string }

interface ToolLogItem {
  id: string;
  trace_id: string | null;
  chat_id: string | null;
  session_title: string | null;
  message_id: string | null;
  user_id: string | null;
  user_name: string | null;
  tool_name: string;
  tool_display_name: string | null;
  tool_call_id: string | null;
  mcp_server: string | null;
  tool_args: any;
  tool_result: any;
  result_truncated: boolean;
  status: string;
  error_message: string | null;
  duration_ms: number | null;
  source: string;
  subagent_log_id: string | null;
  skill_log_id: string | null;
  created_at: string | null;
}

interface Summary {
  tool_name: string;
  total: number;
  success: number;
  success_rate: number;
  avg_duration_ms: number | null;
}

const STATUS_COLORS: Record<string, string> = {
  success: 'success',
  failed: 'error',
  timeout: 'warning',
};

const SOURCE_COLORS: Record<string, string> = {
  main_agent: 'blue',
  subagent: 'purple',
  skill: 'cyan',
  automation: 'gold',
};

export function ToolCallLogsPanel({ token }: { token: string }) {
  const [items, setItems] = useState<ToolLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [toolNames, setToolNames] = useState<string[]>([]);
  const [summary, setSummary] = useState<Summary[]>([]);
  const [detail, setDetail] = useState<ToolLogItem | null>(null);

  const [filterUser, setFilterUser] = useState<string | undefined>();
  const [filterTool, setFilterTool] = useState<string | undefined>();
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [filterSource, setFilterSource] = useState<string | undefined>();
  const [filterTrace, setFilterTrace] = useState<string>('');
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);

  const loadFilters = useCallback(async () => {
    try {
      const [usersRes, filterRes] = await Promise.all([
        configFetch(token, '/v1/admin/chat-history/users'),
        configFetch(token, '/v1/admin/logs/tools/filters'),
      ]);
      setUsers(usersRes.data || []);
      setToolNames(filterRes.data?.tool_names || []);
    } catch (e: any) { message.error(e.message); }
  }, [token]);

  const loadItems = useCallback(async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(ps) });
      if (filterUser) params.set('user_id', filterUser);
      if (filterTool) params.set('tool_name', filterTool);
      if (filterStatus) params.set('status', filterStatus);
      if (filterSource) params.set('source', filterSource);
      if (filterTrace.trim()) params.set('trace_id', filterTrace.trim());
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/logs/tools?${params}`);
      setItems(res.data?.items || []);
      setTotal(res.data?.pagination?.total_items || 0);
    } catch (e: any) { message.error(e.message); }
    setLoading(false);
  }, [token, page, pageSize, filterUser, filterTool, filterStatus, filterSource, filterTrace, filterDates]);

  const loadSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/logs/tools/summary?${params}`);
      setSummary(res.data || []);
    } catch { /* ignore */ }
  }, [token, filterDates]);

  useEffect(() => { loadFilters(); }, [loadFilters]);
  useEffect(() => {
    loadItems(page, pageSize);
    loadSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, filterUser, filterTool, filterStatus, filterSource, filterDates]);

  const totalCalls = summary.reduce((s, i) => s + i.total, 0);
  const totalSuccess = summary.reduce((s, i) => s + i.success, 0);
  const successRate = totalCalls > 0 ? ((totalSuccess / totalCalls) * 100).toFixed(1) : '—';
  const uniqueTools = summary.length;

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
    { title: '工具', dataIndex: 'tool_name', key: 'tool_name', width: 180,
      render: (_: any, r: ToolLogItem) => (
        <Space size={4} direction="vertical" style={{ lineHeight: 1.3 }}>
          <Text strong>{r.tool_display_name || r.tool_name}</Text>
          {r.mcp_server && <Text type="secondary" style={{ fontSize: 12 }}>{r.mcp_server}</Text>}
        </Space>
      ),
    },
    { title: '用户', dataIndex: 'user_name', key: 'user_name', width: 120,
      render: (v: string | null) => v || '-' },
    { title: '会话', dataIndex: 'session_title', key: 'session_title', width: 200, ellipsis: true,
      render: (v: string | null) => v || '-' },
    { title: '来源', dataIndex: 'source', key: 'source', width: 110,
      render: (v: string) => <Tag color={SOURCE_COLORS[v] || 'default'}>{v}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => <Tag color={STATUS_COLORS[v] || 'default'}>{v}</Tag> },
    { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 90, align: 'right' as const,
      render: (v: number | null) => v !== null ? `${v} ms` : '-' },
    { title: '操作', key: 'action', width: 80, fixed: 'right' as const,
      render: (_: any, r: ToolLogItem) => (
        <Button size="small" type="link" onClick={() => setDetail(r)}>详情</Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small">
        <Space wrap>
          <Select placeholder="用户" allowClear style={{ width: 160 }}
            value={filterUser} onChange={setFilterUser} showSearch optionFilterProp="label"
            options={users.map(u => ({ value: u.user_id, label: u.username }))}/>
          <Select placeholder="工具" allowClear style={{ width: 200 }}
            value={filterTool} onChange={setFilterTool} showSearch
            options={toolNames.map(n => ({ value: n, label: n }))}/>
          <Select placeholder="状态" allowClear style={{ width: 120 }}
            value={filterStatus} onChange={setFilterStatus}
            options={[
              { value: 'success', label: '成功' },
              { value: 'failed', label: '失败' },
              { value: 'timeout', label: '超时' },
            ]}/>
          <Select placeholder="来源" allowClear style={{ width: 130 }}
            value={filterSource} onChange={setFilterSource}
            options={[
              { value: 'main_agent', label: '主智能体' },
              { value: 'subagent', label: '子智能体' },
              { value: 'skill', label: '技能' },
              { value: 'automation', label: '自动化' },
            ]}/>
          <DatePicker.RangePicker value={filterDates}
            onChange={(v) => setFilterDates(v as [Dayjs, Dayjs] | null)}/>
          <Button type="primary" onClick={() => { setPage(1); loadItems(1, pageSize); }}>查询</Button>
          <Button onClick={() => {
            setFilterUser(undefined); setFilterTool(undefined); setFilterStatus(undefined);
            setFilterSource(undefined); setFilterTrace(''); setFilterDates(null); setPage(1);
          }}>重置</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadItems(page, pageSize)} />
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={6}><Card size="small"><Statistic title="调用总数" value={totalCalls} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="成功率" value={successRate} suffix="%" /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="工具种类" value={uniqueTools} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="当页条数" value={items.length} /></Card></Col>
      </Row>

      <Table
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        scroll={{ x: 1200 }}
        pagination={{
          current: page, pageSize, total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />

      <Drawer
        title={detail ? `${detail.tool_display_name || detail.tool_name} · 调用详情` : '调用详情'}
        width={760}
        open={!!detail}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="工具名">{detail.tool_name}</Descriptions.Item>
              <Descriptions.Item label="MCP Server">{detail.mcp_server || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLORS[detail.status]}>{detail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="耗时">
                {detail.duration_ms !== null ? `${detail.duration_ms} ms` : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                <Tag color={SOURCE_COLORS[detail.source]}>{detail.source}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="调用时间">
                {detail.created_at ? new Date(detail.created_at).toLocaleString('zh-CN') : '-'}
              </Descriptions.Item>
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
                <Descriptions.Item label="错误信息" span={2}>
                  <Text type="danger">{detail.error_message}</Text>
                </Descriptions.Item>
              )}
            </Descriptions>
            <Card size="small" title="入参">
              <pre style={{ maxHeight: 300, overflow: 'auto', margin: 0 }}>
                {JSON.stringify(detail.tool_args, null, 2)}
              </pre>
            </Card>
            <Card size="small" title={`输出${detail.result_truncated ? ' (已截断)' : ''}`}>
              <pre style={{ maxHeight: 400, overflow: 'auto', margin: 0 }}>
                {JSON.stringify(detail.tool_result, null, 2)}
              </pre>
            </Card>
          </Space>
        )}
      </Drawer>
    </Space>
  );
}
