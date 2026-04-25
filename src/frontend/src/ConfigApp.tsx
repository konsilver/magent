import { useEffect, useState } from 'react';
import { Button, Divider, Layout, Menu, Typography, message } from 'antd';
import {
  SettingOutlined, ApiOutlined, RobotOutlined, FileTextOutlined,
  BarChartOutlined, DollarOutlined, MessageOutlined, LogoutOutlined, AppstoreOutlined,
  ToolOutlined, DeploymentUnitOutlined, BulbOutlined,
} from '@ant-design/icons';
import { CONFIG_AUTH_EXPIRED_EVENT, CONFIG_STORAGE_KEY, configFetch } from './utils/adminApi';
import {
  ConfigLoginView, UsageLogsPanel, TokenBillingPanel, ChatHistoryPanel,
  ToolCallLogsPanel, SubAgentLogsPanel, SkillCallLogsPanel,
} from './components/config';
import { ModelsEditor, ServiceConfigsEditor, McpServersEditor, PromptsEditor } from './components/admin';

const { Title, Text } = Typography;
const { Header, Sider, Content } = Layout;

export default function ConfigApp() {
  const [token, setToken] = useState<string | null>(localStorage.getItem(CONFIG_STORAGE_KEY));
  const [activeKey, setActiveKey] = useState('service-configs');

  useEffect(() => {
    const handler = () => {
      localStorage.removeItem(CONFIG_STORAGE_KEY);
      setToken(null);
      message.warning('配置管理登录已失效，请重新输入 CONFIG_TOKEN');
    };
    window.addEventListener(CONFIG_AUTH_EXPIRED_EVENT, handler);
    return () => window.removeEventListener(CONFIG_AUTH_EXPIRED_EVENT, handler);
  }, []);

  if (!token) {
    return <ConfigLoginView onLogin={setToken} />;
  }

  const handleLogout = () => {
    localStorage.removeItem(CONFIG_STORAGE_KEY);
    setToken(null);
  };

  const menuItems = [
    {
      type: 'group' as const,
      label: '基础配置',
      children: [
        { key: 'service-configs', icon: <SettingOutlined />, label: '系统配置' },
        { key: 'models', icon: <RobotOutlined />, label: '模型管理' },
        { key: 'mcp-servers', icon: <ApiOutlined />, label: 'MCP 工具' },
        { key: 'prompts', icon: <FileTextOutlined />, label: '提示词管理' },
      ],
    },
    {
      type: 'group' as const,
      label: '数据监控',
      children: [
        { key: 'usage-logs', icon: <BarChartOutlined />, label: '用户调用日志' },
        { key: 'billing', icon: <DollarOutlined />, label: 'Token 计费' },
        { key: 'chat-history', icon: <MessageOutlined />, label: '用户聊天记录' },
        { key: 'tool-logs', icon: <ToolOutlined />, label: '工具调用日志' },
        { key: 'subagent-logs', icon: <DeploymentUnitOutlined />, label: '子智能体调用日志' },
        { key: 'skill-logs', icon: <BulbOutlined />, label: '技能调用日志' },
      ],
    },
  ];

  const renderContent = () => {
    switch (activeKey) {
      case 'service-configs':
        return <ServiceConfigsEditor token={token} fetchFn={configFetch} />;
      case 'models':
        return <ModelsEditor token={token} fetchFn={configFetch} />;
      case 'mcp-servers':
        return <McpServersEditor token={token} fetchFn={configFetch} />;
      case 'prompts':
        return <PromptsEditor token={token} fetchFn={configFetch} />;
      case 'usage-logs':
        return <UsageLogsPanel token={token} />;
      case 'billing':
        return <TokenBillingPanel token={token} />;
      case 'chat-history':
        return <ChatHistoryPanel token={token} />;
      case 'tool-logs':
        return <ToolCallLogsPanel token={token} />;
      case 'subagent-logs':
        return <SubAgentLogsPanel token={token} />;
      case 'skill-logs':
        return <SkillCallLogsPanel token={token} />;
      default:
        return null;
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{
        background: '#fff',
        borderBottom: '1px solid #E3E6EA',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 32px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Title level={5} style={{ margin: 0 }}>经信智能体</Title>
          <Divider type="vertical" />
          <Text type="secondary">系统配置中心</Text>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button icon={<AppstoreOutlined />} onClick={() => { window.location.href = '/admin'; }} size="small">
            内容管理
          </Button>
          <Button icon={<LogoutOutlined />} onClick={handleLogout} size="small">
            退出
          </Button>
        </div>
      </Header>
      <Layout>
        <Sider width={220} style={{ background: '#fff', borderRight: '1px solid #E3E6EA' }}>
          <Menu
            mode="inline"
            selectedKeys={[activeKey]}
            onClick={({ key }) => setActiveKey(key)}
            items={menuItems}
            style={{ borderRight: 'none', paddingTop: 8 }}
          />
        </Sider>
        <Content style={{ padding: 24, background: '#F5F6F7', overflow: 'auto' }}>
          <div style={{ maxWidth: 1400 }}>
            {renderContent()}
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
