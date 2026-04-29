import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Modal, Switch, Button, Tag, List, Typography, Slider, message,
} from 'antd';
import { LogoutOutlined, DeleteOutlined, EditOutlined, ExclamationCircleFilled } from '@ant-design/icons';
import { useSettingsStore, useAuthStore, useCatalogStore, useUIStore } from '../../stores';
import type { MemoryItem } from '../../types';
import { resolveAvatarUrl } from '../../utils/avatar';

const AVATAR_CROP_SIZE = 320;
const AVATAR_OUTPUT_SIZE = 256;
const MIN_ZOOM = 1;
const MAX_ZOOM = 3;

const DEFAULT_AVATARS = Array.from({ length: 8 }, (_, i) => ({
  id: `default-avatar-${i + 1}`,
  url: `/icons/avatar/avatar-${i + 1}.png`,
}));

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function getCropBounds(imageWidth: number, imageHeight: number, zoom: number) {
  const baseScale = Math.max(AVATAR_CROP_SIZE / imageWidth, AVATAR_CROP_SIZE / imageHeight);
  const scale = baseScale * zoom;
  const displayWidth = imageWidth * scale;
  const displayHeight = imageHeight * scale;
  const maxOffsetX = Math.max(0, (displayWidth - AVATAR_CROP_SIZE) / 2);
  const maxOffsetY = Math.max(0, (displayHeight - AVATAR_CROP_SIZE) / 2);
  return {
    scale,
    displayWidth,
    displayHeight,
    maxOffsetX,
    maxOffsetY,
  };
}

export default function SettingsPage() {
  const { dispatchProcessVisible, setDispatchProcessVisible } = useUIStore();
  const {
    memoryEnabled,
    memoryWriteEnabled,
    memoryItems,
    memoryPanelOpen,
    memoryLoading,
    setMemoryPanelOpen,
    toggleMemory,
    toggleMemoryWrite,
    loadMemories,
    removeMemory,
    clearMemories,
    clearUserProfileMemories,
  } = useSettingsStore();

  const { authUser, doLogout, setAvatarUrl } = useAuthStore();
  const { catalog } = useCatalogStore();
  const [messageApi, contextHolder] = message.useMessage();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dragStartRef = useRef<{ x: number; y: number; offsetX: number; offsetY: number } | null>(null);
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false);
  const [cropModalOpen, setCropModalOpen] = useState(false);
  const [selectedImageUrl, setSelectedImageUrl] = useState<string | null>(null);
  const [selectedImageName, setSelectedImageName] = useState('');
  const [imageNaturalSize, setImageNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  // Group enabled items by kind for display
  const enabledSkills = catalog.skills.filter((i) => i.enabled);
  const enabledAgents = catalog.agents.filter((i) => i.enabled);
  const enabledMcp = catalog.mcp.filter((i) => i.enabled);
  const avatarUrl = resolveAvatarUrl(authUser?.avatar_url);
  const defaultAvatars = DEFAULT_AVATARS;

  const cropBounds = useMemo(() => {
    if (!imageNaturalSize) return null;
    return getCropBounds(imageNaturalSize.width, imageNaturalSize.height, zoom);
  }, [imageNaturalSize, zoom]);

  useEffect(() => () => {
    if (selectedImageUrl?.startsWith('blob:')) {
      URL.revokeObjectURL(selectedImageUrl);
    }
  }, [selectedImageUrl]);

  const closeCropModal = () => {
    setCropModalOpen(false);
    setImageNaturalSize(null);
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    if (selectedImageUrl?.startsWith('blob:')) {
      URL.revokeObjectURL(selectedImageUrl);
    }
    setSelectedImageUrl(null);
    setSelectedImageName('');
  };

  const handleAvatarFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      messageApi.error('请上传图片文件');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      messageApi.error('图片请控制在 8MB 以内');
      return;
    }

    if (selectedImageUrl?.startsWith('blob:')) {
      URL.revokeObjectURL(selectedImageUrl);
    }

    setSelectedImageUrl(URL.createObjectURL(file));
    setSelectedImageName(file.name);
    setImageNaturalSize(null);
    setZoom(1);
    setOffset({ x: 0, y: 0 });
    setAvatarPickerOpen(false);
    setCropModalOpen(true);
  };

  const handleUseDefaultAvatar = (url: string) => {
    setAvatarUrl(url);
    setAvatarPickerOpen(false);
    messageApi.success('头像已更新');
  };

  const handleImageLoad = (event: React.SyntheticEvent<HTMLImageElement>) => {
    const { naturalWidth, naturalHeight } = event.currentTarget;
    setImageNaturalSize({ width: naturalWidth, height: naturalHeight });
    setOffset({ x: 0, y: 0 });
  };

  const updateOffset = (nextX: number, nextY: number) => {
    if (!cropBounds) {
      setOffset({ x: nextX, y: nextY });
      return;
    }
    setOffset({
      x: clamp(nextX, -cropBounds.maxOffsetX, cropBounds.maxOffsetX),
      y: clamp(nextY, -cropBounds.maxOffsetY, cropBounds.maxOffsetY),
    });
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!cropBounds) return;
    dragStartRef.current = {
      x: event.clientX,
      y: event.clientY,
      offsetX: offset.x,
      offsetY: offset.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStartRef.current) return;
    const nextX = dragStartRef.current.offsetX + (event.clientX - dragStartRef.current.x);
    const nextY = dragStartRef.current.offsetY + (event.clientY - dragStartRef.current.y);
    updateOffset(nextX, nextY);
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    dragStartRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleZoomChange = (value: number) => {
    const nextZoom = Array.isArray(value) ? value[0] : value;
    setZoom(nextZoom);
    if (!imageNaturalSize) return;
    const nextBounds = getCropBounds(imageNaturalSize.width, imageNaturalSize.height, nextZoom);
    setOffset((prev) => ({
      x: clamp(prev.x, -nextBounds.maxOffsetX, nextBounds.maxOffsetX),
      y: clamp(prev.y, -nextBounds.maxOffsetY, nextBounds.maxOffsetY),
    }));
  };

  const handleSaveAvatar = async () => {
    if (!selectedImageUrl || !imageNaturalSize || !cropBounds) return;
    const image = new Image();
    image.src = selectedImageUrl;
    await image.decode();

    const canvas = document.createElement('canvas');
    canvas.width = AVATAR_OUTPUT_SIZE;
    canvas.height = AVATAR_OUTPUT_SIZE;
    const context = canvas.getContext('2d');
    if (!context) {
      messageApi.error('头像处理失败，请重试');
      return;
    }

    const left = (AVATAR_CROP_SIZE - cropBounds.displayWidth) / 2 + offset.x;
    const top = (AVATAR_CROP_SIZE - cropBounds.displayHeight) / 2 + offset.y;
    const sourceX = Math.max(0, (0 - left) / cropBounds.scale);
    const sourceY = Math.max(0, (0 - top) / cropBounds.scale);
    const sourceSize = AVATAR_CROP_SIZE / cropBounds.scale;

    context.imageSmoothingEnabled = true;
    context.drawImage(
      image,
      sourceX,
      sourceY,
      sourceSize,
      sourceSize,
      0,
      0,
      AVATAR_OUTPUT_SIZE,
      AVATAR_OUTPUT_SIZE,
    );

    setAvatarUrl(canvas.toDataURL('image/png'));
    messageApi.success('头像已更新');
    closeCropModal();
  };

  return (
    <>
      {contextHolder}
      <div className="jx-settings-page">
        <h2 className="jx-settings-title">系统设置</h2>

        {/* ── 个人信息 ─────────────────────────────────── */}
        <h3 className="jx-settings-section-title">个人信息</h3>
        <div className="jx-settings-card">
          <div className="jx-settings-userInfo">
            <div className="jx-settings-avatarWrap">
              <button type="button" className="jx-settings-avatarHoverBtn" onClick={() => setAvatarPickerOpen(true)} title="更换头像">
                <img src={avatarUrl} alt="" className="jx-settings-avatar" />
                <div className="jx-settings-avatarOverlay" aria-hidden="true">
                  <EditOutlined style={{ fontSize: 18, color: '#fff' }} />
                </div>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handleAvatarFileChange}
              />
            </div>
            <div className="jx-settings-userMeta">
              <span className="jx-settings-userName">
                {authUser?.username || '未登录'}
              </span>
              {authUser?.user_id && (
                <span className="jx-settings-userId">
                  ID: {authUser.user_id}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* ── 会话设置 ─────────────────────────────────── */}
        <h3 className="jx-settings-section-title">会话设置</h3>
        <div className="jx-settings-card">
          <div className="jx-settings-row">
            <div className="jx-settings-rowLeft">
              <span className="jx-settings-rowLabel">显示调度过程</span>
              <span className="jx-settings-rowDesc">
                控制对话中是否显示智能体的调度子智能体、MCP工具、技能等组件
              </span>
            </div>
            <Switch
              checked={dispatchProcessVisible}
              onChange={(checked) => setDispatchProcessVisible(checked)}
            />
          </div>
        </div>

        {/* ── 记忆设置 ─────────────────────────────────── */}
        <h3 className="jx-settings-section-title">记忆设置</h3>
        <div className="jx-settings-card">
          {/* 写入记忆 */}
          <div className="jx-settings-row">
            <div className="jx-settings-rowLeft">
              <span className="jx-settings-rowLabel">写入记忆</span>
              <span className="jx-settings-rowDesc">
                开启后智能体会在每次对话结束后自动判断是否写入记忆
              </span>
            </div>
            <Switch
              checked={memoryWriteEnabled}
              onChange={(checked) => toggleMemoryWrite(checked)}
            />
          </div>

          <div className="jx-settings-divider" />

          {/* 永久记忆 */}
          <div className="jx-settings-row">
            <div className="jx-settings-rowLeft">
              <span className="jx-settings-rowLabel">永久记忆</span>
              <span className="jx-settings-rowDesc">
                开启后 AI 将记住您跨会话的偏好和背景信息
              </span>
            </div>
            <Switch
              checked={memoryEnabled}
              onChange={(checked) => toggleMemory(checked)}
            />
          </div>

          {memoryEnabled && (
            <div className="jx-settings-memoryDetail">
              <span className="jx-settings-memoryCount">
                当前记忆条数：{memoryItems.length}
              </span>
              <a
                className="jx-settings-memoryLink"
                onClick={async () => {
                  await loadMemories();
                  setMemoryPanelOpen(true);
                }}
              >
                {memoryLoading ? '加载中...' : '查看详情'}
              </a>
            </div>
          )}

          {memoryEnabled && (
            <>
              <div className="jx-settings-divider" />
              <div className="jx-settings-row">
                <div className="jx-settings-rowLeft">
                  <span className="jx-settings-rowLabel">用户特征记忆</span>
                  <span className="jx-settings-rowDesc">
                    清空 AI 记录的您的偏好习惯、认知水平等用户特征信息
                  </span>
                </div>
                <Button
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => {
                    Modal.confirm({
                      title: '确认清空用户特征记忆？',
                      icon: <ExclamationCircleFilled style={{ color: '#F8AB42' }} />,
                      content: '此操作将删除 AI 记录的所有用户偏好和特征信息，无法恢复。',
                      okText: '确认清空',
                      cancelText: '取消',
                      okButtonProps: { danger: true },
                      onOk: () => void clearUserProfileMemories(),
                    });
                  }}
                >
                  清空
                </Button>
              </div>
            </>
          )}
        </div>

        {/* ── 已启用清单 ────────────────────────────────── */}
        <h3 className="jx-settings-section-title">已启用清单</h3>
        <div className="jx-settings-card">
          {enabledSkills.length > 0 && (
            <div className="jx-settings-enabledGroup">
              <span className="jx-settings-enabledLabel">技能</span>
              <div className="jx-settings-tagWrap">
                {enabledSkills.map((s) => (
                  <Tag key={s.id} className="jx-settings-tag">{s.name}</Tag>
                ))}
              </div>
            </div>
          )}

          {enabledAgents.length > 0 && (
            <div className="jx-settings-enabledGroup">
              <span className="jx-settings-enabledLabel">智能体</span>
              <div className="jx-settings-tagWrap">
                {enabledAgents.map((a) => (
                  <Tag key={a.id} className="jx-settings-tag">{a.name}</Tag>
                ))}
              </div>
            </div>
          )}

          {enabledMcp.length > 0 && (
            <div className="jx-settings-enabledGroup">
              <span className="jx-settings-enabledLabel">系统工具</span>
              <div className="jx-settings-tagWrap">
                {enabledMcp.map((m) => (
                  <Tag key={m.id} className="jx-settings-tag">{m.name}</Tag>
                ))}
              </div>
            </div>
          )}

          {enabledSkills.length === 0 && enabledAgents.length === 0 && enabledMcp.length === 0 && (
            <div className="jx-settings-emptyHint">当前未启用任何项</div>
          )}
        </div>

        {/* ── 退出登录 ──────────────────────────────────── */}
        <Button
          className="jx-settings-logoutBtn"
          icon={<LogoutOutlined />}
          onClick={() => {
            Modal.confirm({
              title: '确认退出登录？',
              icon: <ExclamationCircleFilled style={{ color: '#F8AB42' }} />,
              content: '退出登录不会丢失任何数据，你仍可以登录此账号。',
              okText: '退出登录',
              cancelText: '取消',
              okButtonProps: { danger: true },
              onOk: () => void doLogout(),
            });
          }}
          block
        >
          退出登录
        </Button>
      </div>

      {/* Memory view modal */}
      <Modal
        title="选择头像"
        open={avatarPickerOpen}
        onCancel={() => setAvatarPickerOpen(false)}
        footer={null}
        destroyOnHidden
        width={560}
      >
        <div className="jx-settings-avatarPicker">
          <div className="jx-settings-avatarPickerHead">
            <div>
              <div className="jx-settings-avatarPickerTitle">默认头像</div>
              <div className="jx-settings-avatarPickerDesc">你可以先从 8 个默认头像中选择，也可以继续从本地上传图片。</div>
            </div>
            <Button onClick={() => fileInputRef.current?.click()}>从本地上传</Button>
          </div>
          <div className="jx-settings-avatarGrid">
            {defaultAvatars.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`jx-settings-avatarOption${avatarUrl === item.url ? ' active' : ''}`}
                onClick={() => handleUseDefaultAvatar(item.url)}
              >
                <img src={item.url} alt="默认头像" className="jx-settings-avatarOptionImage" />
              </button>
            ))}
          </div>
        </div>
      </Modal>

      <Modal
        title="裁剪头像"
        open={cropModalOpen}
        onCancel={closeCropModal}
        onOk={() => void handleSaveAvatar()}
        okText="保存头像"
        cancelText="取消"
        destroyOnHidden
        width={520}
      >
        <div className="jx-settings-avatarCrop">
          <div
            className="jx-settings-avatarCropStage"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            {selectedImageUrl ? (
              <img
                src={selectedImageUrl}
                alt={selectedImageName}
                className="jx-settings-avatarCropImage"
                onLoad={handleImageLoad}
                style={cropBounds ? {
                  width: `${cropBounds.displayWidth}px`,
                  height: `${cropBounds.displayHeight}px`,
                  transform: `translate(${offset.x}px, ${offset.y}px)`,
                } : undefined}
              />
            ) : null}
            <div className="jx-settings-avatarCropMask" aria-hidden="true" />
          </div>
          <div className="jx-settings-avatarCropHint">
            拖动图片调整位置，使用滑块缩放，保存后会同步更新头像。
          </div>
          <div className="jx-settings-avatarZoomRow">
            <span className="jx-settings-avatarZoomLabel">缩放</span>
            <Slider min={MIN_ZOOM} max={MAX_ZOOM} step={0.01} value={zoom} onChange={handleZoomChange} />
          </div>
        </div>
      </Modal>

      <Modal
        title="我的记忆"
        open={memoryPanelOpen}
        onCancel={() => setMemoryPanelOpen(false)}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, color: '#B3B3B3' }}>共 {memoryItems.length} 条</span>
            <Button danger onClick={() => clearMemories()} disabled={memoryItems.length === 0}>
              删除全部记忆
            </Button>
          </div>
        }
        width={600}
      >
        <List
          dataSource={memoryItems}
          renderItem={(item: MemoryItem) => (
            <List.Item
              actions={[
                <Button
                  key="del"
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => removeMemory(item.id)}
                />,
              ]}
            >
              <List.Item.Meta
                description={
                  <Typography.Text style={{ fontSize: 13 }}>{item.memory}</Typography.Text>
                }
              />
            </List.Item>
          )}
          locale={{ emptyText: '暂无记忆' }}
        />
      </Modal>
    </>
  );
}
