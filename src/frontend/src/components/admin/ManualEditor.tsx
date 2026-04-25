import { useState, useEffect, useCallback } from 'react';
import { Button, Card, Space, Typography, Upload, message } from 'antd';
import { InboxOutlined, FilePdfOutlined, EyeOutlined } from '@ant-design/icons';
import { API_BASE } from '../../utils/adminApi';
import { formatDateTime } from '../../utils/date';

const { Text } = Typography;

interface ManualInfo {
  exists: boolean;
  filename?: string;
  size?: number;
  uploaded_at?: string;
  url?: string;
}

export function ManualEditor({ token }: { token: string }) {
  const [info, setInfo] = useState<ManualInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const loadInfo = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/content/manual`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setInfo(data.data as ManualInfo);
    } catch {
      message.error('加载操作手册信息失败');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { loadInfo(); }, [loadInfo]);

  const handleUpload = async (file: File) => {
    if (file.type !== 'application/pdf') {
      message.error('仅支持 PDF 文件');
      return false;
    }
    if (file.size > 50 * 1024 * 1024) {
      message.error('文件大小不能超过 50MB');
      return false;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch(`${API_BASE}/v1/content/manual/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      message.success('操作手册上传成功');
      loadInfo();
    } catch (e) {
      message.error(`上传失败：${(e as Error).message}`);
    } finally {
      setUploading(false);
    }
    return false;
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <Card bordered={false} style={{ boxShadow: '0 2px 8px rgba(0,0,0,.06)' }} loading={loading}>
      {info?.exists && (
        <Card
          size="small"
          style={{ marginBottom: 24, border: '1px solid #E3E6EA' }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space>
              <FilePdfOutlined style={{ fontSize: 24, color: '#FC5D5D' }} />
              <div>
                <Text strong>{info.filename}</Text>
                <br />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {formatSize(info.size!)} | 上传于 {formatDateTime(info.uploaded_at, '--')}
                </Text>
              </div>
            </Space>
            <Button
              icon={<EyeOutlined />}
              onClick={() => window.open(info.url!, '_blank')}
            >
              预览
            </Button>
          </div>
        </Card>
      )}

      <Upload.Dragger
        accept=".pdf"
        showUploadList={false}
        beforeUpload={handleUpload}
        disabled={uploading}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">
          {uploading ? '上传中...' : '点击或拖拽 PDF 文件到此处上传'}
        </p>
        <p className="ant-upload-hint">
          仅支持 PDF 格式，大小不超过 50MB。上传后将替换现有操作手册。
        </p>
      </Upload.Dragger>
    </Card>
  );
}
