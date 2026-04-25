import { Popover } from 'antd';
import type { CitationItem } from '../../types';

const CITATION_ICON: Record<string, string> = {
  internet:       '/icons/互联网.png',
  knowledge_base: '/icons/知识库.png',
  database:       '/icons/数据库.png',
  industry_news:  '/icons/资讯.png',
  ai_news:        '/icons/AI资讯.png',
  chain_info:        '/icons/产业链.png',
  company_profile:   '/icons/产业链.png',
};

const CITATION_LABEL: Record<string, string> = {
  internet:       '互联网',
  knowledge_base: '知识库',
  database:       '数据库',
  industry_news:  '产业资讯',
  ai_news:        'AI 动态',
  chain_info:        '产业链',
  company_profile:   '企业画像',
};

export { CITATION_ICON, CITATION_LABEL };

export default function CitationBadge({
  citId,
  citations,
  onCitationAction,
}: {
  citId: string;
  citations: CitationItem[];
  onCitationAction?: (citation: CitationItem) => void;
}) {
  const cit = citations.find(c => c.id === citId);
  const iconPath = cit ? (CITATION_ICON[cit.source_type] || null) : null;
  const label = cit ? (CITATION_LABEL[cit.source_type] || '来源') : '来源';
  const iconEl = (size: number) => iconPath
    ? <img src={iconPath} alt={label} style={{ width: size, height: size, verticalAlign: 'middle', objectFit: 'contain' }} />
    : <span style={{ fontSize: size }}>📄</span>;
  const indexPart = citId.split('-').pop() || '';
  const isInternet = cit?.source_type === 'internet';
  const canOpenDetail = !!cit && !!onCitationAction;
  const openDetail = () => {
    if (!cit || !onCitationAction) return;
    onCitationAction(cit);
  };

  const hoverContent = cit ? (
    <div
      style={{ maxWidth: 300, fontSize: 13, cursor: canOpenDetail ? 'pointer' : 'default' }}
      role={canOpenDetail ? 'button' : undefined}
      tabIndex={canOpenDetail ? 0 : undefined}
      onClick={canOpenDetail ? openDetail : undefined}
      onKeyDown={canOpenDetail ? (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openDetail();
        }
      } : undefined}
      title={canOpenDetail ? (isInternet ? '点击打开原文链接' : '点击查看全文') : undefined}
    >
      <div style={{ fontWeight: 600, marginBottom: 4, color: '#808080', fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
        {iconEl(14)} {label}
      </div>
      <div style={{ marginBottom: cit.snippet ? 6 : 0, fontWeight: 600, color: isInternet ? '#126DFF' : '#262626' }}>
        {cit.title}
      </div>
      {cit.snippet && (
        <div style={{ fontSize: 12, color: '#808080', lineHeight: 1.6, borderLeft: '3px solid #DBE9FF', paddingLeft: 8 }}>
          {cit.snippet.length > 160 ? cit.snippet.slice(0, 160) + '…' : cit.snippet}
        </div>
      )}
      {isInternet && cit.url && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#B3B3B3' }}>
          🔗 {(() => { try { return new URL(cit.url).hostname; } catch { return cit.url.slice(0, 40); } })()}
        </div>
      )}
      {(cit.snippet || cit.url) && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#126DFF' }}>
          {isInternet ? '点击此卡片打开原文 →' : '点击此卡片查看全文 →'}
        </div>
      )}
    </div>
  ) : (
    <div style={{ color: '#B3B3B3', fontSize: 12 }}>引用 {citId} 未找到</div>
  );

  const badgeEl = (
    <sup
      style={{
        cursor: 'pointer',
        color: '#808080',
        fontWeight: 400,
        fontSize: '0.72em',
        background: '#F5F6F7',
        border: '1px solid #E3E6EA',
        borderRadius: 4,
        padding: '0 4px',
        margin: '0 1px',
        userSelect: 'none',
        verticalAlign: 'super',
        lineHeight: 1,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
      }}
    >
      {iconEl(11)}{indexPart}
    </sup>
  );

  return (
    <Popover content={hoverContent} trigger="hover" placement="top" overlayStyle={{ zIndex: 9999 }}>
      {badgeEl}
    </Popover>
  );
}
