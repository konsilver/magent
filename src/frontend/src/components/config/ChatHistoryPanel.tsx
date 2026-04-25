import { useState, useEffect, useCallback } from 'react';
import {
  Card, Collapse, DatePicker, Empty, Input, List, Pagination,
  Select, Space, Tag, Typography, message,
} from 'antd';
import { configFetch } from '../../utils/adminApi';
import type { AdminChatSession, AdminChatMessage } from '../../types';
import type { Dayjs } from 'dayjs';

const { Text } = Typography;

interface UserOption { user_id: string; username: string }

const ROLE_COLORS: Record<string, string> = {
  user: 'blue',
  assistant: 'green',
  system: 'orange',
  tool: 'purple',
};

export function ChatHistoryPanel({ token }: { token: string }) {
  const [sessions, setSessions] = useState<AdminChatSession[]>([]);
  const [messages, setMessages] = useState<AdminChatMessage[]>([]);
  const [users, setUsers] = useState<UserOption[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(30);

  // Filters
  const [filterUser, setFilterUser] = useState<string | undefined>();
  const [filterSearch, setFilterSearch] = useState('');
  const [filterDates, setFilterDates] = useState<[Dayjs, Dayjs] | null>(null);

  const loadUsers = useCallback(async () => {
    try {
      const res = await configFetch(token, '/v1/admin/chat-history/users');
      setUsers(res.data || []);
    } catch { /* ignore */ }
  }, [token]);

  const loadSessions = useCallback(async (p = page) => {
    setSessionsLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(pageSize) });
      if (filterUser) params.set('user_id', filterUser);
      if (filterSearch) params.set('search', filterSearch);
      if (filterDates) {
        params.set('date_from', filterDates[0].startOf('day').toISOString());
        params.set('date_to', filterDates[1].endOf('day').toISOString());
      }
      const res = await configFetch(token, `/v1/admin/chat-history/sessions?${params}`);
      setSessions(res.data?.items || []);
      setTotal(res.data?.pagination?.total_items || 0);
    } catch (e: any) { message.error(e.message); }
    setSessionsLoading(false);
  }, [token, page, pageSize, filterUser, filterSearch, filterDates]);

  const loadMessages = useCallback(async (chatId: string) => {
    setMessagesLoading(true);
    try {
      const res = await configFetch(token, `/v1/admin/chat-history/sessions/${chatId}/messages`);
      setMessages(res.data || []);
    } catch (e: any) { message.error(e.message); }
    setMessagesLoading(false);
  }, [token]);

  useEffect(() => { loadUsers(); }, [loadUsers]);
  useEffect(() => { loadSessions(page); }, [page, filterUser, filterDates, loadSessions]);

  const handleSelectSession = (chatId: string) => {
    setSelectedChatId(chatId);
    loadMessages(chatId);
  };

  const handleSearch = () => { setPage(1); };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 180px)', background: '#fff', borderRadius: 8, overflow: 'hidden' }}>
      {/* Left panel: Session list */}
      <div style={{ width: 400, borderRight: '1px solid #E3E6EA', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 16, borderBottom: '1px solid #E3E6EA' }}>
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            <Select
              placeholder="选择用户" allowClear style={{ width: '100%' }}
              value={filterUser} onChange={(v) => { setFilterUser(v); setPage(1); }}
              showSearch optionFilterProp="label"
              options={users.map(u => ({ value: u.user_id, label: u.username }))}
            />
            <Input.Search
              placeholder="搜索会话标题"
              value={filterSearch}
              onChange={e => setFilterSearch(e.target.value)}
              onSearch={handleSearch}
              allowClear
            />
            <DatePicker.RangePicker
              value={filterDates} style={{ width: '100%' }}
              onChange={(v) => { setFilterDates(v as [Dayjs, Dayjs] | null); setPage(1); }}
            />
          </Space>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          <List
            loading={sessionsLoading}
            dataSource={sessions}
            renderItem={(session) => (
              <List.Item
                key={session.chat_id}
                onClick={() => handleSelectSession(session.chat_id)}
                style={{
                  padding: '12px 16px',
                  cursor: 'pointer',
                  background: selectedChatId === session.chat_id ? '#E6F4FF' : undefined,
                  borderBottom: '1px solid #f0f0f0',
                }}
              >
                <List.Item.Meta
                  title={
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text ellipsis style={{ maxWidth: 240 }}>{session.title}</Text>
                      {session.deleted_at && <Tag color="red" style={{ marginLeft: 4 }}>已删除</Tag>}
                    </div>
                  }
                  description={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {session.username} · {session.message_count} 条消息 · {session.created_at ? new Date(session.created_at).toLocaleDateString('zh-CN') : ''}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        </div>
        <div style={{ padding: '8px 16px', borderTop: '1px solid #E3E6EA', textAlign: 'center' }}>
          <Pagination
            simple current={page} pageSize={pageSize} total={total}
            onChange={setPage}
          />
        </div>
      </div>

      {/* Right panel: Message viewer */}
      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {selectedChatId ? (
          messagesLoading ? (
            <div style={{ textAlign: 'center', padding: 60 }}><Text type="secondary">加载中...</Text></div>
          ) : messages.length === 0 ? (
            <Empty description="该会话暂无消息" />
          ) : (
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              {messages.map(msg => (
                <Card key={msg.message_id} size="small" style={{ borderLeft: `3px solid ${msg.role === 'user' ? '#126DFF' : msg.role === 'assistant' ? '#02B589' : '#F8AB42'}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <Tag color={ROLE_COLORS[msg.role] || 'default'}>{msg.role}</Tag>
                    {msg.model && <Tag>{msg.model}</Tag>}
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto' }}>
                      {msg.created_at ? new Date(msg.created_at).toLocaleString('zh-CN') : ''}
                    </Text>
                  </div>
                  <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 400, overflow: 'auto' }}>
                    {msg.content}
                  </div>
                  {msg.tool_calls != null && (
                    <Collapse ghost size="small" style={{ marginTop: 8 }}
                      items={[{
                        key: 'tools',
                        label: <Text type="secondary" style={{ fontSize: 12 }}>工具调用详情</Text>,
                        children: <pre style={{ fontSize: 12, maxHeight: 300, overflow: 'auto', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                          {JSON.stringify(msg.tool_calls, null, 2)}
                        </pre>,
                      }]}
                    />
                  )}
                  {msg.usage && (
                    <div style={{ marginTop: 4 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        Token: {msg.usage.prompt_tokens ?? 0} 输入 / {msg.usage.completion_tokens ?? 0} 输出
                      </Text>
                    </div>
                  )}
                </Card>
              ))}
            </Space>
          )
        ) : (
          <Empty description="请选择一个会话查看详情" style={{ marginTop: 120 }} />
        )}
      </div>
    </div>
  );
}
