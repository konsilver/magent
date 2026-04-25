import { LoadingOutlined, DownloadOutlined, CloseOutlined } from '@ant-design/icons';
import { getFileIconSrc } from '../../utils/fileIcon';

const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']);

export default function FileAttachmentCard({
  name,
  downloadHref,
  onClose,
  loading,
  previewUrl,
}: {
  name: string;
  downloadHref?: string;
  onClose?: () => void;
  loading?: boolean;
  previewUrl?: string;
}) {
  const ext = (name.split('.').pop() ?? '').toLowerCase();
  const isImage = IMAGE_EXTS.has(ext);

  let label = '文档';
  if (ext === 'pdf')                           { label = 'PDF 文档'; }
  else if (ext === 'docx' || ext === 'doc')    { label = 'Word 文档'; }
  else if (ext === 'xlsx' || ext === 'xls')    { label = 'Excel 表格'; }
  else if (ext === 'pptx' || ext === 'ppt')    { label = 'PPT 幻灯片'; }
  else if (ext === 'wps')                      { label = 'WPS 文档'; }
  else if (ext === 'csv')                      { label = 'CSV 表格'; }
  else if (ext === 'txt')                      { label = '文本文件'; }
  else if (isImage)                            { label = '图片'; }

  const inner = (
    <>
      <div className="jx-fileCard-icon">
        {isImage && previewUrl ? (
          <img src={previewUrl} alt={name} className="jx-fileCard-imgThumb" />
        ) : (
          <img src={getFileIconSrc(name)} width="24" height="24" alt="" aria-hidden="true" />
        )}
      </div>
      <div className="jx-fileCard-info">
        <div className="jx-fileCard-name" title={name}>{name}</div>
        <div className="jx-fileCard-type">
          {loading ? <><LoadingOutlined style={{ marginRight: 4 }} />上传中…</> : label}
        </div>
      </div>
      {!loading && downloadHref && (
        <DownloadOutlined className="jx-fileCard-dlIcon" aria-hidden="true" />
      )}
      {onClose && (
        <button className="jx-fileCard-close" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClose(); }} aria-label="移除文件" title="移除">
          <CloseOutlined style={{ fontSize: 9 }} />
        </button>
      )}
    </>
  );

  if (downloadHref) {
    return (
      <a
        className="jx-fileCard jx-fileCard--link"
        href={downloadHref}
        download={name}
        title={`下载 ${name}`}
        onClick={(e) => {
          e.preventDefault();
          const a = document.createElement('a');
          a.href = downloadHref;
          a.download = name;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }}
      >
        {inner}
      </a>
    );
  }

  return <div className="jx-fileCard">{inner}</div>;
}
