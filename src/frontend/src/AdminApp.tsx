import { useEffect, useState } from 'react';
import { Button, Card, Divider, Layout, Tabs, Typography, message } from 'antd';
import { LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { ADMIN_AUTH_EXPIRED_EVENT, STORAGE_KEY } from './utils/adminApi';
import {
  LoginView,
  UpdatesEditor,
  CapsEditor,
  ManualEditor,
  SkillsEditor,
  PromptHubEditor,
  AdminAgentManager,
} from './components/admin';

const { Title, Text } = Typography;
const { Header, Content } = Layout;

export default function AdminApp() {
  const [token, setToken] = useState<string | null>(localStorage.getItem(STORAGE_KEY));

  useEffect(() => {
    const handleAdminAuthExpired = () => {
      localStorage.removeItem(STORAGE_KEY);
      setToken(null);
      message.warning('管理员登录已失效，请重新输入 ADMIN_TOKEN');
    };

    window.addEventListener(ADMIN_AUTH_EXPIRED_EVENT, handleAdminAuthExpired);
    return () => {
      window.removeEventListener(ADMIN_AUTH_EXPIRED_EVENT, handleAdminAuthExpired);
    };
  }, []);

  if (!token) {
    return <LoginView onLogin={setToken} />;
  }

  const handleLogout = () => {
    localStorage.removeItem(STORAGE_KEY);
    setToken(null);
  };

  return (
    <Layout style={{ minHeight: '100vh', background: '#F5F6F7' }}>
      <Header style={{ background: '#fff', borderBottom: '1px solid #E3E6EA', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 32px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Title level={5} style={{ margin: 0 }}>经信智能体</Title>
          <Divider type="vertical" />
          <Text type="secondary">后台管理</Text>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button icon={<SettingOutlined />} onClick={() => { window.location.href = '/config'; }} size="small">系统配置</Button>
          <Button icon={<LogoutOutlined />} onClick={handleLogout} size="small">退出</Button>
        </div>
      </Header>

      <Content style={{ padding: '32px 40px', maxWidth: 1200, margin: '0 auto', width: '100%' }}>
        <Tabs
          size="large"
          defaultActiveKey="updates"
          items={[
            {
              key: 'updates',
              label: '功能更新',
              children: (
                <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
                  <UpdatesEditor token={token} />
                </Card>
              ),
            },
            {
              key: 'capabilities',
              label: '能力中心',
              children: (
                <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
                  <CapsEditor token={token} />
                </Card>
              ),
            },
            {
              key: 'skills',
              label: '技能管理',
              children: (
                <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
                  <SkillsEditor token={token} />
                </Card>
              ),
            },
            {
              key: 'prompt-hub',
              label: '提示词中心',
              children: (
                <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
                  <PromptHubEditor token={token} />
                </Card>
              ),
            },
            {
              key: 'agents',
              label: '子智能体',
              children: <AdminAgentManager token={token} />,
            },
            {
              key: 'manual',
              label: '操作手册',
              children: <ManualEditor token={token} />,
            },
          ]}
        />
      </Content>
    </Layout>
  );
}
