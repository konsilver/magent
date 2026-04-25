import { Modal } from 'antd';
import { useAuthStore } from '../../stores';

export default function AuthExpiredModal() {
  const authExpiredUrl = useAuthStore((s) => s.authExpiredUrl);

  return (
    <Modal
      title="登录已失效"
      open={!!authExpiredUrl}
      closable={false}
      maskClosable={false}
      keyboard={false}
      cancelButtonProps={{ style: { display: 'none' } }}
      okText="重新登录"
      onOk={() => {
        window.location.href = authExpiredUrl!;
      }}
    >
      <p>您的登录会话已过期，请重新登录。</p>
    </Modal>
  );
}
