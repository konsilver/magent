import { Modal } from 'antd';
import { useUIStore } from '../../stores';

export default function ImagePreview() {
  const previewImage = useUIStore((s) => s.previewImage);
  const setPreviewImage = useUIStore((s) => s.setPreviewImage);

  return (
    <Modal
      title={null}
      open={!!previewImage}
      onCancel={() => setPreviewImage(null)}
      footer={null}
      width="auto"
      centered
      closable={false}
      maskClosable
      className="jx-imagePreviewModal"
      rootClassName="jx-imagePreviewRoot"
      destroyOnClose
    >
      {previewImage && (
        <button
          type="button"
          className="jx-imagePreviewBtn"
          onClick={() => setPreviewImage(null)}
          aria-label="关闭图片预览"
        >
          <img src={previewImage.url} alt={previewImage.name} className="jx-imagePreviewImg" />
        </button>
      )}
    </Modal>
  );
}
