import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import 'antd/dist/reset.css'
import './index.css'
import './styles'
import App from './App.tsx'
import AdminApp from './AdminApp.tsx'
import ConfigApp from './ConfigApp.tsx'
import SharePreviewApp from './SharePreviewApp.tsx'

const isAdmin = window.location.pathname.startsWith('/admin')
const isConfig = window.location.pathname.startsWith('/config')
const isSharePreview = new URLSearchParams(window.location.search).has('share')

/** Ant Design theme tokens aligned with UI design spec (img/设计规范/) */
const theme = {
  token: {
    colorPrimary: '#126DFF',
    colorSuccess: '#02B589',
    colorWarning: '#F8AB42',
    colorError: '#FC5D5D',
    colorInfo: '#126DFF',
    colorText: '#262626',
    colorTextSecondary: '#4D4D4D',
    colorTextTertiary: '#808080',
    colorTextQuaternary: '#B3B3B3',
    colorBorder: '#E3E6EA',
    colorBorderSecondary: '#D8DBE2',
    colorBgContainer: '#FFFFFF',
    colorBgLayout: '#F5F6F7',
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 4,
    fontFamily: '"PingFang SC", "Microsoft YaHei", "微软雅黑", sans-serif',
    fontSize: 14,
    fontSizeSM: 12,
    fontSizeLG: 16,
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider theme={theme} locale={zhCN}>
      {isSharePreview ? <SharePreviewApp /> : isConfig ? <ConfigApp /> : isAdmin ? <AdminApp /> : <App />}
    </ConfigProvider>
  </StrictMode>,
)
