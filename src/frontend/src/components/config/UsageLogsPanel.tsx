import { useState, useEffect, useCallback } from 'react';
import { Button, Card, Col, DatePicker, Row, Select, Space, Statistic, Table, Tag, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { configFetch } from '../../utils/adminApi';
import type { UsageLogEntry, UsageSummaryItem } from '../../types';
import type { Dayjs } from 'dayjs';

interface UserOption { user_id: string; username: string }

export function UsageLogsPanel({ token }: { token: string }) {
  const [logs, setLogs] = useState<UsageLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [summary, setSummary] = useState<UsageSummaryItem[]>([]);

  // Filters
  const [filterUser, setFilterUser] = useState<string | undefined>();
  const [filterModel, setFilterModel] = useState<string | undefined>();
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);
  const [filterError, setFilterError] = useState<boolean | undefined>();

  const loadFilters = useCallback(async () => {
    try {
      const [usersRes, modelsRes] = await Promise.all([
        configFetch(token, '/v1/admin/chat-history/users'),
        configFetch(token, '/v1/admin/usage-logs/models'),
      ]);
      setUsers(usersRes.data || []);
      setModels(modelsRes.data || []);
    } catch (e: any) { message.error(e.message); }
  }, [token]);

  const loadLogs = useCallback(async (p = page, ps = pageSize) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(ps) });
      if (filterUser) params.set('user_id', filterUser);
      if (filterModel) params.set('model', filterModel);
      if (filterError !== undefined) params.set('has_error', String(filterError));
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/usage-logs?${params}`);
      setLogs(res.data?.items || []);
      setTotal(res.data?.pagination?.total_items || 0);
    } catch (e: any) { message.error(e.message); }
    setLoading(false);
  }, [token, page, pageSize, filterUser, filterModel, filterDates, filterError]);

  const loadSummary = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/usage-logs/summary?${params}`);
      setSummary(res.data || []);
    } catch { /* ignore */ }
  }, [token, filterDates]);

  useEffect(() => { loadFilters(); }, [loadFilters]);
  useEffect(() => { loadLogs(page, pageSize); loadSummary(); }, [page, pageSize, filterUser, filterModel, filterDates, filterError]);

  const handleSearch = () => { setPage(1); };
  const handleReset = () => {
    setFilterUser(undefined); setFilterModel(undefined); setFilterDates(null); setFilterError(undefined);
    setPage(1);
  };

  // Summary stats
  const totalRequests = summary.reduce((s, i) => s + i.total_requests, 0);
  const totalTokens = summary.reduce((s, i) => s + i.total_tokens, 0);
  const avgTokens = totalRequests > 0 ? Math.round(totalTokens / totalRequests) : 0;
  const errorCount = logs.filter(l => l.has_error).length;

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 180,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-' },
    { title: '用户', dataIndex: 'username', key: 'username', width: 120 },
    { title: '会话标题', dataIndex: 'session_title', key: 'session_title', width: 200, ellipsis: true },
    { title: '模型', dataIndex: 'model', key: 'model', width: 150,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : '-' },
    { title: '输入 Token', dataIndex: 'prompt_tokens', key: 'prompt_tokens', width: 110, align: 'right' as const,
      render: (v: number) => v.toLocaleString() },
    { title: '输出 Token', dataIndex: 'completion_tokens', key: 'completion_tokens', width: 110, align: 'right' as const,
      render: (v: number) => v.toLocaleString() },
    { title: '总 Token', dataIndex: 'total_tokens', key: 'total_tokens', width: 110, align: 'right' as const,
      render: (v: number) => v.toLocaleString() },
    { title: '状态', dataIndex: 'has_error', key: 'has_error', width: 80,
      render: (v: boolean) => v ? <Tag color="error">失败</Tag> : <Tag color="success">成功</Tag> },
  ];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small">
        <Space wrap>
          <Select
            placeholder="选择用户" allowClear style={{ width: 160 }}
            value={filterUser} onChange={setFilterUser}
            showSearch optionFilterProp="label"
            options={users.map(u => ({ value: u.user_id, label: u.username }))}
          />
          <Select
            placeholder="选择模型" allowClear style={{ width: 180 }}
            value={filterModel} onChange={setFilterModel}
            options={models.map(m => ({ value: m, label: m }))}
          />
          <DatePicker.RangePicker
            value={filterDates}
            onChange={(v) => setFilterDates(v as [Dayjs, Dayjs] | null)}
          />
          <Select
            placeholder="状态" allowClear style={{ width: 100 }}
            value={filterError} onChange={setFilterError}
            options={[{ value: false, label: '成功' }, { value: true, label: '失败' }]}
          />
          <Button type="primary" onClick={handleSearch}>查询</Button>
          <Button onClick={handleReset}>重置</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadLogs(page, pageSize)} />
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={6}><Card size="small"><Statistic title="总请求数" value={totalRequests} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="总 Token 数" value={totalTokens} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="平均 Token/请求" value={avgTokens} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="错误数" value={errorCount} valueStyle={errorCount > 0 ? { color: '#FC5D5D' } : undefined} /></Card></Col>
      </Row>

      <Table
        dataSource={logs}
        columns={columns}
        rowKey="message_id"
        loading={loading}
        size="small"
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />
    </Space>
  );
}
