import { useState } from 'react';
import { Button, Card, Input, Typography, message } from 'antd';
import { configFetch, CONFIG_STORAGE_KEY } from '../../utils/adminApi';

const { Title, Text } = Typography;

export function ConfigLoginView({ onLogin }: { onLogin: (token: string) => void }) {
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!token.trim()) return;
    setLoading(true);
    try {
      await configFetch(token.trim(), '/v1/config/verify');
      localStorage.setItem(CONFIG_STORAGE_KEY, token.trim());
      onLogin(token.trim());
    } catch {
      message.error('Token 验证失败，请检查 CONFIG_TOKEN 是否正确');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F5F6F7' }}>
      <Card style={{ width: 380, boxShadow: '0 4px 20px rgba(0,0,0,.08)' }}>
        <Title level={4} style={{ marginBottom: 4 }}>经信智能体 — 系统配置中心</Title>
        <Text type="secondary" style={{ display: 'block', marginBottom: 24 }}>请输入配置管理 Token 以继续</Text>
        <Input.Password
          placeholder="CONFIG_TOKEN"
          value={token}
          onChange={e => setToken(e.target.value)}
          onPressEnter={handleLogin}
          size="large"
        />
        <Button
          type="primary"
          block
          size="large"
          style={{ marginTop: 12 }}
          loading={loading}
          onClick={handleLogin}
        >
          验证并进入
        </Button>
      </Card>
    </div>
  );
}
