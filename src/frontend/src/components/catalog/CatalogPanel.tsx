import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Empty, Input, Modal, Pagination, Popconfirm, Select, Switch, Tag, Typography,
  Upload, Collapse, InputNumber, message,
} from 'antd';
import {
  ArrowLeftOutlined, CloseOutlined, DeleteOutlined, EditOutlined, EyeOutlined,
  InboxOutlined, LoadingOutlined, PlusOutlined, SafetyCertificateOutlined,
  ReloadOutlined, SearchOutlined, StarFilled, ThunderboltOutlined, UploadOutlined,
} from '@ant-design/icons';
import { getFileIconSrc, getFolderIconSrc } from '../../utils/fileIcon';
import { useCatalogStore, useKbStore } from '../../stores';
import {
  createKBSpace,
  deleteKBDocument,
  deleteKBSpace,
  getKBChunks,
  getKBDocumentDetail,
  getKBDocuments,
  polishKBDescription,
  previewChunks,
  updateKBSpace,
  updateKBChunk,
  uploadKBDocument,
} from '../../api';
import type { IndexingConfig, KBDocumentsResponse } from '../../api';
import type { KBDocument, KBItem } from '../../types';
import { formatDateTime } from '../../utils/date';
import { mdToHtml } from '../../utils/markdown';

type KBTabKey = 'public' | 'private';
const KB_TAB_STORAGE_KEY = 'jingxin_kb_active_tab';

type UploadChunkMethodOption = {
  value: string;
  label: string;
  desc: string;
  recommended?: boolean;
};

const UPLOAD_CHUNK_METHOD_OPTIONS: UploadChunkMethodOption[] = [
  { value: 'structured', label: '结构感知（按标题和段落）', desc: '适合结构清晰的报告、通知、制度文档' },
  { value: 'recursive', label: '递归分块（多级分隔符）', desc: '按文本层级切分，适合通用长文档' },
  { value: 'embedding_semantic', label: '语义分块（基于嵌入相似度）', desc: '更关注语义完整性，适合复杂内容', recommended: true },
  { value: 'laws', label: '法律文书', desc: '按条款和层级组织，更适合法律法规类文本' },
  { value: 'qa', label: '问答对', desc: '适合 FAQ、客服问答、知识问答数据' },
];

const KB_DOC_PAGE_SIZE = 20;
const EMPTY_DOCS: KBDocument[] = [];

const DOC_FILTERS = [
  { key: 'all', label: '全部', countKey: 'total' },
  { key: 'indexed', label: '已索引', countKey: 'indexed' },
  { key: 'processing', label: '索引中', countKey: 'processing' },
] as const;
type DocStatusFilter = typeof DOC_FILTERS[number]['key'];

function loadActiveKbTab(): KBTabKey {
  if (typeof window === 'undefined') return 'public';
  const raw = window.localStorage.getItem(KB_TAB_STORAGE_KEY);
  return raw === 'private' ? 'private' : 'public';
}

function saveActiveKbTab(tab: KBTabKey) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(KB_TAB_STORAGE_KEY, tab);
}

function resolveVisibility(item: KBItem): KBTabKey {
  if (item.visibility === 'public' || item.visibility === 'private') return item.visibility;
  return item.is_public ? 'public' : 'private';
}

function parseDocumentTimestamp(rawValue: unknown): number | undefined {
  if (rawValue === null || rawValue === undefined || rawValue === '') return undefined;

  if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
    return rawValue < 1e12 ? rawValue * 1000 : rawValue;
  }

  if (typeof rawValue === 'string') {
    const trimmed = rawValue.trim();
    if (!trimmed) return undefined;

    if (/^\d+$/.test(trimmed)) {
      const numericValue = Number(trimmed);
      if (!Number.isFinite(numericValue)) return undefined;
      return numericValue < 1e12 ? numericValue * 1000 : numericValue;
    }

    const parsedValue = Date.parse(trimmed);
    return Number.isNaN(parsedValue) ? undefined : parsedValue;
  }

  return undefined;
}

function mapDocument(raw: any): KBDocument {
  const createdAtRaw = raw?.created_at ?? raw?.uploaded_at ?? raw?.createdAt ?? raw?.uploadedAt;
  const createdAt = parseDocumentTimestamp(createdAtRaw);
  const wordCount = typeof raw?.word_count === 'number'
    ? raw.word_count
    : Number.isFinite(Number(raw?.word_count))
      ? Number(raw.word_count)
      : undefined;
  const sizeBytes = typeof raw?.size_bytes === 'number'
    ? raw.size_bytes
    : typeof raw?.size === 'number'
      ? raw.size
      : Number.isFinite(Number(raw?.size_bytes))
        ? Number(raw.size_bytes)
        : Number.isFinite(Number(raw?.size))
          ? Number(raw.size)
          : undefined;

  return {
    id: String(raw?.id ?? raw?.document_id ?? ''),
    title: String(raw?.title ?? raw?.name ?? raw?.filename ?? ''),
    desc: raw?.desc ?? raw?.filename ?? undefined,
    content: typeof raw?.content === 'string' ? raw.content : undefined,
    indexing_status: raw?.indexing_status ?? undefined,
    word_count: wordCount,
    size_bytes: sizeBytes,
    created_at: createdAt && !Number.isNaN(createdAt) ? createdAt : undefined,
  };
}

function formatCount(value?: number, unit = '') {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--';
  return `${value}${unit}`;
}

function formatDocWordCount(doc: KBDocument) {
  return formatCount(doc.word_count, '字');
}

function getDocumentBadge(name: string) {
  return (
    <img
      className="jx-kbDocType"
      src={getFileIconSrc(name)}
      width="20"
      height="20"
      alt=""
      aria-hidden="true"
    />
  );
}

function formatKbDisplayName(name?: string) {
  return name || '';
}

function formatKbSummaryDesc(description?: string) {
  if (!description) return '';
  const [summary] = description.split('规则说明：');
  return summary.trim();
}

/** Filter out technical/internal tags that shouldn't be shown to users. */
function filterDisplayTags(tags?: string[]): string[] {
  if (!Array.isArray(tags)) return [];
  return tags.filter((tag) => {
    if (!tag || typeof tag !== 'string') return false;
    // Hide Dify provider paths like "langgenius/openai_api_compatible/..."
    if (tag.includes('/')) return false;
    // Hide technical quality flags
    if (tag === 'high_quality' || tag === 'economy') return false;
    return true;
  });
}

export function CatalogPanel() {
  const {
    catalog, catalogLoading,
    manageQuery, setManageQuery,
    selectedId, setSelectedId,
    fetchCatalog, toggleItem,
  } = useCatalogStore();

  const {
    kbDocQuery, setKbDocQuery,
    activeKbDoc, setActiveKbDoc,
    kbDocumentsMap, setKbDocumentsMap,
    kbDocsLoadingId, setKbDocsLoadingId,
    kbDocDetailLoadingId, setKbDocDetailLoadingId,
    uploadDocModalOpen, uploadDocLoading,
    uploadDocFileList, setUploadDocFileList,
    openUploadDocModal, closeUploadDocModal,
    setUploadDocLoading,
    uploadParentChunkSize, setUploadParentChunkSize,
    uploadChildChunkSize, setUploadChildChunkSize,
    uploadOverlapTokens, setUploadOverlapTokens,
    uploadParentChildIndexing, setUploadParentChildIndexing,
    uploadAutoKeywordsCount, setUploadAutoKeywordsCount,
    uploadAutoQuestionsCount, setUploadAutoQuestionsCount,
    uploadStep, setUploadStep,
    uploadChunkMethod, setUploadChunkMethod,
    chunkPreviewData, setChunkPreviewData,
    chunkPreviewLoading, setChunkPreviewLoading,
    expandedChunkIndex, setExpandedChunkIndex,
    openReindexModal,
    docDetailTab, setDocDetailTab,
    docChunks, setDocChunks,
    docChunksLoading, setDocChunksLoading,
    chunkSaving, setChunkSaving,
  } = useKbStore();

  const [activeTab, setActiveTab] = useState<KBTabKey>(() => loadActiveKbTab());
  const [detailDescExpanded, setDetailDescExpanded] = useState(false);
  const [detailDescOverflow, setDetailDescOverflow] = useState(false);
  const [kbDocPage, setKbDocPage] = useState(1);
  const [kbDocTotal, setKbDocTotal] = useState(0);
  const [docStatusFilter, setDocStatusFilter] = useState<DocStatusFilter>('all');
  const [kbEditorOpen, setKbEditorOpen] = useState(false);
  const [kbEditorMode, setKbEditorMode] = useState<'create' | 'edit'>('create');
  const [kbEditorName, setKbEditorName] = useState('');
  const [kbEditorDesc, setKbEditorDesc] = useState('');
  const [kbEditorLoading, setKbEditorLoading] = useState(false);
  const [kbEditorPolishing, setKbEditorPolishing] = useState(false);
  const detailDescRef = useRef<HTMLParagraphElement | null>(null);
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const tabButtonRefs = useRef<Partial<Record<KBTabKey, HTMLButtonElement | null>>>({});
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState<{ left: number; width: number; ready: boolean }>({
    left: 0,
    width: 0,
    ready: false,
  });

  const kbItems = catalog.kb as KBItem[];

  const counts = useMemo(() => {
    let publicCount = 0;
    let privateCount = 0;
    kbItems.forEach((item) => {
      if (resolveVisibility(item) === 'public') publicCount += 1;
      else privateCount += 1;
    });
    return { public: publicCount, private: privateCount };
  }, [kbItems]);

  const selectedItem = useMemo(
    () => kbItems.find((item) => item.id === selectedId) || null,
    [kbItems, selectedId],
  );

  useEffect(() => {
    if (selectedItem) {
      setActiveTab(resolveVisibility(selectedItem));
    }
  }, [selectedItem]);

  useEffect(() => {
    setKbDocPage(1);
    setKbDocTotal(selectedItem?.document_count || 0);
  }, [selectedItem?.id, selectedItem?.document_count]);

  useEffect(() => {
    saveActiveKbTab(activeTab);
  }, [activeTab]);

  useEffect(() => {
    const updateIndicator = () => {
      const tabsEl = tabsRef.current;
      const activeEl = tabButtonRefs.current[activeTab];
      if (!tabsEl || !activeEl) return;
      const tabsRect = tabsEl.getBoundingClientRect();
      const activeRect = activeEl.getBoundingClientRect();
      setTabIndicatorStyle({
        left: activeRect.left - tabsRect.left,
        width: activeRect.width,
        ready: true,
      });
    };

    updateIndicator();
    window.addEventListener('resize', updateIndicator);
    return () => window.removeEventListener('resize', updateIndicator);
  }, [activeTab, counts.public, counts.private]);

  useEffect(() => {
    setDetailDescExpanded(false);
  }, [selectedItem?.id]);

  useEffect(() => {
    const measureOverflow = () => {
      const el = detailDescRef.current;
      if (!el || detailDescExpanded) return;
      setDetailDescOverflow(el.scrollWidth > el.clientWidth + 1);
    };

    measureOverflow();
    window.addEventListener('resize', measureOverflow);
    return () => window.removeEventListener('resize', measureOverflow);
  }, [selectedItem?.desc, detailDescExpanded]);

  useEffect(() => {
    if (selectedId && !selectedItem) {
      setSelectedId(null);
    }
  }, [selectedId, selectedItem, setSelectedId]);

  useEffect(() => {
    setDocStatusFilter('all');
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const kbId = selectedId;
    setKbDocsLoadingId(kbId);
    void (async () => {
      try {
        const result: KBDocumentsResponse = await getKBDocuments(kbId, kbDocPage, KB_DOC_PAGE_SIZE);
        const mappedItems = result.items.map(mapDocument);
        const totalPages = result.total > 0 ? Math.ceil(result.total / result.page_size) : 0;

        if (result.total > 0 && totalPages > 0 && kbDocPage > totalPages) {
          setKbDocPage(totalPages);
          return;
        }

        setKbDocumentsMap((prev) => ({ ...prev, [kbId]: mappedItems }));
        setKbDocTotal(result.total);
      } catch {
        setKbDocumentsMap((prev) => ({ ...prev, [kbId]: [] }));
        setKbDocTotal(0);
      } finally {
        setKbDocsLoadingId(null);
      }
    })();
  }, [selectedId, kbDocPage, setKbDocumentsMap, setKbDocsLoadingId]);

  const filteredLibraries = useMemo(() => {
    const query = manageQuery.trim().toLowerCase();
    return kbItems
      .filter((item) => resolveVisibility(item) === activeTab)
      .filter((item) => {
        if (!query) return true;
        return `${item.name} ${item.desc} ${item.id}`.toLowerCase().includes(query);
      })
      .sort((a, b) => {
        const pinDelta = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
        if (pinDelta !== 0) return pinDelta;
        return (a.name || '').localeCompare(b.name || '', 'zh-CN');
      });
  }, [kbItems, activeTab, manageQuery]);

  const documents = useMemo(
    () => (selectedItem ? (kbDocumentsMap[selectedItem.id] ?? EMPTY_DOCS) : EMPTY_DOCS),
    [selectedItem, kbDocumentsMap],
  );

  const isIndexedStatus = (status: string | undefined) =>
    status !== 'processing' && status !== 'failed';

  const filteredDocuments = useMemo(() => {
    const query = kbDocQuery.trim().toLowerCase();
    return documents.filter((doc) => {
      if (docStatusFilter === 'processing' && doc.indexing_status !== 'processing') return false;
      if (docStatusFilter === 'indexed' && !isIndexedStatus(doc.indexing_status)) return false;
      if (!query) return true;
      return `${doc.title} ${doc.desc || ''} ${doc.content || ''}`.toLowerCase().includes(query);
    });
  }, [documents, kbDocQuery, docStatusFilter]);

  const detailStats = useMemo(() => {
    const total = kbDocTotal || selectedItem?.document_count || 0;
    const indexed = documents.filter((doc) => isIndexedStatus(doc.indexing_status)).length;
    const processing = documents.filter((doc) => doc.indexing_status === 'processing').length;
    return { total, indexed, processing };
  }, [documents, kbDocTotal, selectedItem]);

  const docEmptyDescription = (() => {
    if (documents.length === 0) return '该知识库暂无文档';
    if (kbDocQuery) return '没有匹配的文档';
    if (docStatusFilter === 'processing') return '当前没有索引中的文档';
    if (docStatusFilter === 'indexed') return '当前没有已索引的文档';
    return '没有匹配的文档';
  })();

  const refreshCatalog = async () => {
    await fetchCatalog();
  };

  const closeKbEditor = () => {
    setKbEditorOpen(false);
    setKbEditorMode('create');
    setKbEditorName('');
    setKbEditorDesc('');
    setKbEditorLoading(false);
    setKbEditorPolishing(false);
  };

  const openCreateKbEditor = () => {
    setKbEditorMode('create');
    setKbEditorName('');
    setKbEditorDesc('');
    setKbEditorOpen(true);
  };

  const openEditKbEditor = (item: KBItem) => {
    setKbEditorMode('edit');
    setKbEditorName(item.name || '');
    setKbEditorDesc(item.desc || '');
    setKbEditorOpen(true);
  };

  const handleKbEditorSubmit = async () => {
    const name = kbEditorName.trim();
    const description = kbEditorDesc.trim();

    if (!name) {
      message.warning('请输入知识库名称');
      return;
    }

    setKbEditorLoading(true);
    try {
      if (kbEditorMode === 'create') {
        await createKBSpace(name, description || undefined);
        message.success('私有知识库已创建');
      } else if (selectedItem) {
        await updateKBSpace(selectedItem.id, {
          name,
          description: description || '',
        });
        message.success('知识库信息已更新');
      }
      await refreshCatalog();
      closeKbEditor();
    } catch (err: any) {
      message.error(err?.message || (kbEditorMode === 'create' ? '创建失败' : '更新失败'));
    } finally {
      setKbEditorLoading(false);
    }
  };

  const handlePolishKbDescription = async () => {
    const name = kbEditorName.trim();
    if (!name) {
      message.warning('请先输入知识库名称');
      return;
    }

    setKbEditorPolishing(true);
    try {
      const polished = await polishKBDescription(name, kbEditorDesc.trim() || undefined);
      if (!polished) {
        message.warning('未生成知识库简介，请稍后重试');
        return;
      }
      setKbEditorDesc(polished);
      message.success('已生成知识库简介');
    } catch (err: any) {
      message.error(err?.message || '生成知识库简介失败');
    } finally {
      setKbEditorPolishing(false);
    }
  };

  const refreshSelectedLibrary = async () => {
    if (!selectedItem || kbDocsLoadingId === selectedItem.id) return;
    setKbDocsLoadingId(selectedItem.id);
    try {
      const result = await getKBDocuments(selectedItem.id, kbDocPage, KB_DOC_PAGE_SIZE);
      const latestDocs = result.items.map(mapDocument);
      setKbDocumentsMap((prev) => ({ ...prev, [selectedItem.id]: latestDocs }));
      setKbDocTotal(result.total);
      await refreshCatalog();
    } catch (err: any) {
      message.error(err?.message || '刷新文档列表失败');
    } finally {
      setKbDocsLoadingId(null);
    }
  };

  const openKbDocumentDetail = async (doc: KBDocument) => {
    setActiveKbDoc(doc);
    if (!selectedId || doc.content) return;
    if (kbDocDetailLoadingId === doc.id) return;

    setKbDocDetailLoadingId(doc.id);
    try {
      const detail = await getKBDocumentDetail(selectedId, doc.id);
      const detailedDoc: KBDocument = {
        ...doc,
        title: detail.title || doc.title,
        desc: detail.desc ?? doc.desc,
        content: detail.content,
      };
      setActiveKbDoc((prev) => (prev && prev.id === doc.id ? { ...prev, ...detailedDoc } : prev));
      setKbDocumentsMap((prev) => ({
        ...prev,
        [selectedId]: (prev[selectedId] || []).map((item) => (item.id === doc.id ? { ...item, ...detailedDoc } : item)),
      }));
    } catch {
      message.error('加载文档详情失败');
    } finally {
      setKbDocDetailLoadingId(null);
    }
  };

  const handleUpload = async (isPreview = false) => {
    if (uploadDocFileList.length === 0) {
      message.warning('请选择文件');
      return;
    }
    if (!selectedItem) return;

    if (isPreview) {
      setChunkPreviewLoading(true);
      try {
        const result = await previewChunks(
          uploadDocFileList[0], uploadChunkMethod, uploadParentChunkSize,
          uploadChildChunkSize, uploadOverlapTokens, uploadParentChildIndexing,
        );
        setChunkPreviewData(result);
        setUploadStep('preview');
        setExpandedChunkIndex(null);
      } catch (err: any) {
        message.error(err.message || '预览失败');
      } finally {
        setChunkPreviewLoading(false);
      }
      return;
    }

    setUploadDocLoading(true);
    try {
      const idxCfg: IndexingConfig = {
        parent_chunk_size: uploadParentChunkSize,
        child_chunk_size: uploadChildChunkSize,
        overlap_tokens: uploadOverlapTokens,
        parent_child_indexing: uploadParentChildIndexing,
        auto_keywords_count: uploadAutoKeywordsCount,
        auto_questions_count: uploadAutoQuestionsCount,
      };
      for (const file of uploadDocFileList) {
        await uploadKBDocument(selectedItem.id, file, undefined, idxCfg, uploadChunkMethod);
      }
      setKbDocPage(1);
      const result = await getKBDocuments(selectedItem.id, 1, KB_DOC_PAGE_SIZE);
      const latestDocs = result.items.map(mapDocument);
      setKbDocumentsMap((prev) => ({ ...prev, [selectedItem.id]: latestDocs }));
      setKbDocTotal(result.total);
      await refreshCatalog();
      closeUploadDocModal();
      message.success(`${uploadDocFileList.length} 个文档已上传，正在后台索引`);
    } catch (err: any) {
      message.error(err.message || '上传失败');
    } finally {
      setUploadDocLoading(false);
    }
  };

  const emptyLibraries = !catalogLoading && filteredLibraries.length === 0;
  const isPrivateLibrary = selectedItem ? resolveVisibility(selectedItem) === 'private' : false;
  const selectedItemDisplayName = formatKbDisplayName(selectedItem?.name);
  const currentDocCount = kbDocTotal || selectedItem?.document_count || 0;
  const libraryLoadingCards = Array.from({ length: 40 }, (_, index) => index);
  const docLoadingRows = Array.from({ length: 40 }, (_, index) => index);

  return (
    <>
      <div className="jx-kbView">
        {!selectedItem ? (
          <>
            <section className="jx-kbTabsWrap">
              <div className="jx-kbTabs" ref={tabsRef}>
                {(['public', 'private'] as KBTabKey[]).map((tab) => {
                  const tabLabel = tab === 'public' ? '公共知识库' : '私有知识库';
                  return (
                    <button
                      key={tab}
                      ref={(el) => {
                        tabButtonRefs.current[tab] = el;
                      }}
                      className={`jx-kbTab${activeTab === tab ? ' active' : ''}`}
                      onClick={() => {
                        setActiveTab(tab);
                        setManageQuery('');
                      }}
                    >
                      <span>{tabLabel}</span>
                      <span className="jx-kbTabCount">{counts[tab]}</span>
                    </button>
                  );
                })}
                <span
                  className={`jx-kbTabIndicator${tabIndicatorStyle.ready ? ' is-ready' : ''}`}
                  style={{ transform: `translateX(${tabIndicatorStyle.left}px)`, width: tabIndicatorStyle.width }}
                  aria-hidden="true"
                />
              </div>
            </section>

            <section className="jx-kbToolbar">
              <Input
                allowClear
                value={manageQuery}
                onChange={(e) => setManageQuery(e.target.value)}
                prefix={<SearchOutlined />}
                placeholder={activeTab === 'public' ? '搜索公共知识库' : '搜索私有知识库'}
                className="jx-kbToolbarSearch"
              />
              <div className="jx-kbToolbarMeta">
                <span>
                  共 {counts[activeTab]} 个知识库
                  {activeTab === 'public' ? ' · 由管理员统一维护' : ' · 仅自己可见与维护'}
                </span>
                <Button icon={<ReloadOutlined />} onClick={() => void refreshCatalog()} disabled={catalogLoading}>
                  刷新
                </Button>
                {activeTab === 'private' && (
                  <Button type="primary" icon={<PlusOutlined />} onClick={openCreateKbEditor}>
                    新增私有知识库
                  </Button>
                )}
              </div>
            </section>

            <section className="jx-kbLibraryGrid">
              {catalogLoading ? (
                libraryLoadingCards.map((item) => (
                  <div key={item} className="jx-kbLibraryCard jx-kbLibraryCardSkeleton" aria-hidden="true">
                    <div className="jx-kbLibraryCardTop">
                      <div className="jx-skeletonBlock jx-kbSkIcon" />
                      <div className="jx-kbLibraryMain">
                        <div className="jx-kbLibraryTitleRow">
                          <div className="jx-skeletonBlock jx-kbSkTitle" />
                          <div className="jx-skeletonBlock jx-kbSkArrow" />
                        </div>
                        <div className="jx-skeletonBlock jx-kbSkDesc" />
                      </div>
                    </div>
                    <div className="jx-kbLibraryTags">
                      <div className="jx-skeletonBlock jx-kbSkTag" />
                      <div className="jx-skeletonBlock jx-kbSkTag" />
                      <div className="jx-skeletonBlock jx-kbSkMeta" />
                    </div>
                  </div>
                ))
              ) : emptyLibraries ? (
                <div className="jx-kbLibraryEmpty">
                  <Empty
                    description={activeTab === 'public' ? '暂无公共知识库' : '暂无私有知识库'}
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  >
                    {activeTab === 'private' && (
                      <Button type="primary" onClick={openCreateKbEditor}>
                        创建第一个私有知识库
                      </Button>
                    )}
                  </Empty>
                </div>
              ) : (
                filteredLibraries.map((item) => {
                  const visibility = resolveVisibility(item);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className="jx-kbLibraryCard"
                      onClick={() => {
                        setSelectedId(item.id);
                        setKbDocQuery('');
                      }}
                    >
                      <div className="jx-kbLibraryCardTop">
                        <div className="jx-kbLibraryIcon"><img src={getFolderIconSrc()} width="44" height="44" alt="" aria-hidden="true" /></div>
                        <div className="jx-kbLibraryMain">
                        <div className="jx-kbLibraryTitleRow">
                            <h3 className="jx-kbLibraryTitle">{formatKbDisplayName(item.name)}</h3>
                          </div>
                          <p className="jx-kbLibraryDesc">{formatKbSummaryDesc(item.desc) || '暂无知识库说明'}</p>
                        </div>
                      </div>
                      <div className="jx-kbLibraryTags">
                        <span className="jx-kbPill jx-kbPill-blue">{visibility === 'public' ? '公共' : '私有'}</span>
                        <Tag
                          className="jx-kbEnabledTag"
                          style={item.enabled
                            ? { background: '#DBE9FF', color: '#126DFF', border: 'none' }
                            : { background: '#F5F6F7', color: '#B3B3B3', border: 'none' }
                          }
                        >
                          {item.enabled ? '已启用' : '未启用'}
                        </Tag>
                        {filterDisplayTags(item.tags).map((tag) => (
                          <span
                            key={`${item.id}-${tag}`}
                            className={`jx-kbPill${tag === '系统托管' ? ' jx-kbPill-orange' : ''}`}
                          >
                            {tag}
                          </span>
                        ))}
                        <span className="jx-kbLibraryDocCount">{`共${formatCount(item.document_count)}个文档`}</span>
                      </div>
                    </button>
                  );
                })
              )}
            </section>
          </>
        ) : (
          <>
            <section className="jx-kbDetailHeader">
              <div className="jx-kbDetailHeaderMain">
                <Button
                  icon={<ArrowLeftOutlined />}
                  className="jx-kbBackBtn"
                  type="text"
                  aria-label="返回列表"
                  title="返回列表"
                  onClick={() => {
                    setSelectedId(null);
                    setKbDocQuery('');
                  }}
                />
                <div className="jx-kbDetailDivider" />
                <div className="jx-kbDetailIcon"><img src={getFolderIconSrc()} width="24" height="24" alt="" aria-hidden="true" /></div>
                <div className="jx-kbDetailIntro">
                  <div className="jx-kbDetailTitleRow">
                    <h2 className="jx-kbDetailTitle">{selectedItemDisplayName}</h2>
                    <span className="jx-kbPill jx-kbPill-blue">{isPrivateLibrary ? '私有' : '公共'}</span>
                    <Tag
                      className="jx-kbEnabledTag"
                      style={selectedItem.enabled
                        ? { background: '#DBE9FF', color: '#126DFF', border: 'none' }
                        : { background: '#F5F6F7', color: '#B3B3B3', border: 'none' }
                      }
                    >
                      {selectedItem.enabled ? '已启用' : '未启用'}
                    </Tag>
                    {filterDisplayTags(selectedItem.tags).map((tag) => (
                      <span
                        key={`${selectedItem.id}-${tag}`}
                        className={`jx-kbPill${tag === '系统托管' ? ' jx-kbPill-orange' : ''}`}
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="jx-kbDetailDescRow">
                    <p
                      ref={detailDescRef}
                      className={`jx-kbDetailDesc${detailDescExpanded ? ' is-expanded' : ''}`}
                    >
                      {formatKbSummaryDesc(selectedItem.desc) || '暂无知识库说明'}
                    </p>
                    {detailDescOverflow && (
                      <Button
                        type="link"
                        className="jx-kbDetailDescToggle"
                        onClick={() => setDetailDescExpanded((prev) => !prev)}
                      >
                        {detailDescExpanded ? '收起' : '展开'}
                      </Button>
                    )}
                  </div>
                </div>
              </div>
              <div className="jx-kbDetailActions">
                <div className="jx-kbEnableRow">
                  <span className="jx-kbEnableLabel">启用</span>
                  <Switch
                    checked={selectedItem.enabled}
                    onChange={(checked) => void toggleItem('kb', selectedItem.id, checked)}
                  />
                </div>
                {!isPrivateLibrary ? (
                  <span className="jx-kbDetailBadge"><SafetyCertificateOutlined /> 由管理员维护</span>
                ) : (
                  <>
                    <Button
                      icon={<UploadOutlined />}
                      type="primary"
                      onClick={() => openUploadDocModal()}
                      disabled={!selectedItem.uploadable}
                    >
                      上传文档
                    </Button>
                    <div className="jx-kbDetailIconGroup">
                      <Button
                        type="text"
                        className="jx-kbEditIconBtn"
                        icon={<EditOutlined />}
                        aria-label="编辑知识库"
                        title="编辑知识库"
                        disabled={!selectedItem.editable}
                        onClick={() => selectedItem && openEditKbEditor(selectedItem)}
                      />
                      <Popconfirm
                        title="确定删除此知识库？"
                        description="删除后该知识库及其所有文档将不可恢复。"
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        disabled={!selectedItem.deletable}
                        onConfirm={async () => {
                          try {
                            await deleteKBSpace(selectedItem.id);
                            message.success('知识库已删除');
                            setSelectedId(null);
                            await refreshCatalog();
                          } catch (err: any) {
                            message.error(err.message || '删除失败');
                          }
                        }}
                      >
                        <Button
                          type="text"
                          className="jx-kbDeleteIconBtn"
                          icon={<DeleteOutlined />}
                          aria-label="删除知识库"
                          title="删除知识库"
                          disabled={!selectedItem.deletable}
                        />
                      </Popconfirm>
                    </div>
                  </>
                )}
              </div>
            </section>

            <section className="jx-kbDocPanel">
              <div className="jx-kbDocPanelHeader">
                <div className="jx-kbDocFilterTabs" role="tablist" aria-label="文档状态筛选">
                  {DOC_FILTERS.map(({ key, label, countKey }) => {
                    const active = docStatusFilter === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        className={`jx-kbDocFilterTab${active ? ' is-active' : ''}`}
                        onClick={() => setDocStatusFilter(key)}
                      >
                        <span className="jx-kbDocFilterTabLabel">{label}</span>
                        <span className="jx-kbDocFilterTabCount">{detailStats[countKey]}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="jx-kbDocPanelTools">
                  <Input
                    allowClear
                    value={kbDocQuery}
                    onChange={(e) => setKbDocQuery(e.target.value)}
                    prefix={<SearchOutlined />}
                    placeholder="搜索文档..."
                    className="jx-kbDocSearch"
                  />
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => void refreshSelectedLibrary()}
                    disabled={kbDocsLoadingId === selectedItem.id}
                  >
                    刷新
                  </Button>
                </div>
              </div>

              <div className="jx-kbDocTable">
                <div className="jx-kbDocTableHead">
                  <div>文件名</div>
                  <div>字符数</div>
                  <div>上传时间</div>
                  <div>状态</div>
                  <div>操作</div>
                </div>

                {kbDocsLoadingId === selectedItem.id ? (
                  <div className="jx-kbDocLoadingWrap" aria-hidden="true">
                    {docLoadingRows.map((item) => (
                      <div key={item} className="jx-kbDocRow jx-kbDocRowSkeleton">
                        <div className="jx-kbDocNameCell">
                          <div className="jx-skeletonBlock jx-kbSkDocType" />
                          <div className="jx-kbDocNameMain">
                            <div className="jx-skeletonBlock jx-kbSkDocName" />
                            <div className="jx-skeletonBlock jx-kbSkDocDesc" />
                          </div>
                        </div>
                        <div className="jx-skeletonBlock jx-kbSkDocMeta" />
                        <div className="jx-skeletonBlock jx-kbSkDocMeta" />
                        <div className="jx-skeletonBlock jx-kbSkDocStatus" />
                        <div className="jx-kbDocActions">
                          <div className="jx-skeletonBlock jx-kbSkDocAction" />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : filteredDocuments.length === 0 ? (
                  <div className="jx-kbDocEmpty">
                    <Empty
                      description={docEmptyDescription}
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                    >
                      {isPrivateLibrary && documents.length === 0 && (
                        <Button type="primary" onClick={() => openUploadDocModal()}>
                          上传第一份文档
                        </Button>
                      )}
                    </Empty>
                  </div>
                ) : (
                  filteredDocuments.map((doc) => (
                    <div key={doc.id} className="jx-kbDocRow">
                      <div className="jx-kbDocNameCell">
                        {getDocumentBadge(doc.title || doc.id)}
                        <div className="jx-kbDocNameMain">
                          <div className="jx-kbDocName">{doc.title || doc.id}</div>
                        </div>
                      </div>
                      <div className="jx-kbDocCellMuted">{formatDocWordCount(doc)}</div>
                      <div className="jx-kbDocCellMuted">{formatDateTime(doc.created_at)}</div>
                      <div>
                        {doc.indexing_status === 'processing' ? (
                          <span className="jx-kbStatusPill jx-kbStatusPill-processing">
                            <LoadingOutlined /> 索引中
                          </span>
                        ) : doc.indexing_status === 'failed' ? (
                          <span className="jx-kbStatusPill jx-kbStatusPill-failed">索引失败</span>
                        ) : (
                          <span className="jx-kbStatusPill jx-kbStatusPill-success">索引完成</span>
                        )}
                      </div>
                      <div className="jx-kbDocActions">
                        <span className="jx-kbDocActionSlot">
                          {isPrivateLibrary && doc.indexing_status === 'failed' ? (
                            <Button
                              type="text"
                              icon={<ThunderboltOutlined />}
                              onClick={() => openReindexModal(doc.id, selectedItem.id)}
                              aria-label="重新索引文档"
                              title="重新索引文档"
                            />
                          ) : (
                            <span className="jx-kbDocActionPlaceholder" aria-hidden="true" />
                          )}
                        </span>
                        <span className="jx-kbDocActionSlot">
                          <Button
                            type="text"
                            icon={<EyeOutlined />}
                            onClick={() => void openKbDocumentDetail(doc)}
                            aria-label="查看索引分块情况"
                            title="查看索引分块情况"
                          />
                        </span>
                        <span className="jx-kbDocActionSlot">
                          {isPrivateLibrary ? (
                            <Popconfirm
                              title="确定删除此文档？"
                              okText="删除"
                              cancelText="取消"
                              okButtonProps={{ danger: true }}
                              onConfirm={async () => {
                                try {
                                  await deleteKBDocument(selectedItem.id, doc.id);
                                  const nextTotal = Math.max(0, currentDocCount - 1);
                                  const nextTotalPages = nextTotal > 0 ? Math.ceil(nextTotal / KB_DOC_PAGE_SIZE) : 1;
                                  if (kbDocPage > nextTotalPages) {
                                    setKbDocPage(nextTotalPages);
                                  } else {
                                    await refreshSelectedLibrary();
                                  }
                                  await refreshCatalog();
                                  message.success('文档已删除');
                                } catch (err: any) {
                                  message.error(err.message || '删除失败');
                                }
                              }}
                            >
                              <Button
                                type="text"
                                danger
                                icon={<DeleteOutlined />}
                                aria-label="删除文档"
                                title="删除文档"
                              />
                            </Popconfirm>
                          ) : (
                            <span className="jx-kbDocActionPlaceholder" aria-hidden="true" />
                          )}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
              {!kbDocsLoadingId && currentDocCount > 0 && (
                <div className="jx-kbDocPagination">
                  <Pagination
                    className="jx-kbPager"
                    current={kbDocPage}
                    pageSize={KB_DOC_PAGE_SIZE}
                    total={currentDocCount}
                    showSizeChanger={false}
                    showTotal={(total) => `共 ${total} 条`}
                    onChange={(page) => setKbDocPage(page)}
                  />
                </div>
              )}
            </section>
          </>
        )}
      </div>

      <Modal
        title={kbEditorMode === 'create' ? '创建私有知识库' : '编辑私有知识库'}
        open={kbEditorOpen}
        onCancel={closeKbEditor}
        maskClosable={false}
        width={520}
        className="jx-kbEditorModal"
        footer={(
          <div className="jx-kbEditorFooter">
            <Button onClick={closeKbEditor}>取消</Button>
            <Button type="primary" loading={kbEditorLoading} onClick={() => void handleKbEditorSubmit()}>
              {kbEditorMode === 'create' ? '创建' : '保存'}
            </Button>
          </div>
        )}
      >
        <div className="jx-kbEditorBody">
          <div className="jx-kbEditorField">
            <div className="jx-kbEditorLabel">知识库名称</div>
            <Input
              value={kbEditorName}
              onChange={(e) => setKbEditorName(e.target.value)}
              placeholder="请输入知识库名称"
              maxLength={255}
            />
          </div>
          <div className="jx-kbEditorField">
            <div className="jx-kbEditorLabel">知识库简介</div>
            <div className="jx-kbEditorTextareaWrap">
              <Input.TextArea
                value={kbEditorDesc}
                onChange={(e) => setKbEditorDesc(e.target.value)}
                placeholder="请输入知识库简介"
                autoSize={{ minRows: 4, maxRows: 6 }}
                maxLength={500}
                className="jx-kbEditorTextarea"
              />
              <div className="jx-kbEditorTextareaMeta">
                <Button
                  size="small"
                  className="jx-kbEditorPolishBtn"
                  loading={kbEditorPolishing}
                  onClick={() => void handlePolishKbDescription()}
                  icon={<ThunderboltOutlined />}
                >
                  AI润色
                </Button>
                <span className="jx-kbEditorCount">{kbEditorDesc.length} / 500</span>
              </div>
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title={
          <div className="jx-kbDocModalTitle">
            <span>{activeKbDoc?.title || activeKbDoc?.id || '文档详情'}</span>
            {activeKbDoc && selectedId && isPrivateLibrary && (
              <Button size="small" icon={<ThunderboltOutlined />} onClick={() => openReindexModal(activeKbDoc.id, selectedId)}>
                重新索引
              </Button>
            )}
          </div>
        }
        open={!!activeKbDoc}
        onCancel={() => { setActiveKbDoc(null); setDocDetailTab('content'); setDocChunks([]); }}
        footer={[<Button key="close" onClick={() => { setActiveKbDoc(null); setDocDetailTab('content'); setDocChunks([]); }}>关闭</Button>]}
        width={920}
      >
        {activeKbDoc && (
          <div>
            {activeKbDoc.desc && <div className="jx-kbDocDesc">{activeKbDoc.desc}</div>}
            <div className="jx-kbDocTabs">
              <button className={`jx-kbDocTab${docDetailTab === 'content' ? ' active' : ''}`} onClick={() => setDocDetailTab('content')}>内容预览</button>
              <button
                className={`jx-kbDocTab${docDetailTab === 'chunks' ? ' active' : ''}`}
                onClick={async () => {
                  setDocDetailTab('chunks');
                  if (docChunks.length === 0 && selectedId && activeKbDoc) {
                    setDocChunksLoading(true);
                    try {
                      const chunks = await getKBChunks(selectedId, activeKbDoc.id);
                      setDocChunks(chunks);
                    } catch {
                      message.error('加载分块失败');
                    } finally {
                      setDocChunksLoading(false);
                    }
                  }
                }}
              >
                分块列表{docChunks.length > 0 ? ` (${docChunks.length})` : ''}
              </button>
            </div>
            {docDetailTab === 'content' ? (
              <div className="jx-kbDocModalBody">
                {kbDocDetailLoadingId === activeKbDoc.id ? (
                  <div className="jx-kbDocLoading"><LoadingOutlined /> 正在加载文档正文…</div>
                ) : activeKbDoc.content ? (
                  <div className="jx-md jx-kbDocModalMarkdown" dangerouslySetInnerHTML={{ __html: mdToHtml(activeKbDoc.content) }} />
                ) : (
                  <Typography.Text type="secondary">当前文档暂无正文内容。</Typography.Text>
                )}
              </div>
            ) : (
              <div style={{ maxHeight: '60vh', overflow: 'auto' }}>
                {docChunksLoading ? (
                  <div className="jx-kbDocLoading"><LoadingOutlined /> 正在加载分块列表…</div>
                ) : docChunks.length === 0 ? (
                  <Typography.Text type="secondary">暂无分块数据。</Typography.Text>
                ) : (
                  <div className="jx-chunkList">
                    {docChunks.map((chunk) => (
                      <div key={chunk.chunk_id} className="jx-chunkCard">
                        <div className="jx-chunkHeader">
                          <div className="jx-chunkIndex"><span className="jx-chunkIndexNum">{chunk.chunk_index + 1}</span></div>
                          <div className="jx-chunkHeaderRight">
                            <span className="jx-chunkContentLen">{chunk.content.length} 字</span>
                            <Button
                              size="small"
                              type="primary"
                              loading={chunkSaving === chunk.chunk_id}
                              style={{ borderRadius: 6, fontSize: 12, height: 28 }}
                              onClick={async () => {
                                setChunkSaving(chunk.chunk_id);
                                try {
                                  await updateKBChunk(selectedId!, chunk.chunk_id, { tags: chunk.tags, questions: chunk.questions });
                                  message.success('分块已保存');
                                } catch (err: any) {
                                  message.error(err.message || '保存失败');
                                } finally {
                                  setChunkSaving(null);
                                }
                              }}
                            >
                              保存
                            </Button>
                          </div>
                        </div>
                        <div className="jx-chunkContent">{chunk.content}</div>
                        {(chunk.tags.length > 0 || chunk.questions.length > 0) && (
                          <div className="jx-chunkMeta">
                            {chunk.tags.length > 0 && (
                              <div className="jx-chunkSection">
                                <div className="jx-chunkSectionLabel"><span className="jx-chunkSectionIcon">🏷</span>标签</div>
                                <div className="jx-chunkTagsWrap">
                                  {chunk.tags.map((tag, ti) => (
                                    <Tag
                                      key={ti}
                                      closable
                                      className="jx-chunkTag"
                                      onClose={() => setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                        ? { ...item, tags: item.tags.filter((_, index) => index !== ti) }
                                        : item)))}
                                    >
                                      {tag}
                                    </Tag>
                                  ))}
                                  <Input
                                    size="small"
                                    placeholder="+ 标签"
                                    className="jx-chunkAddInput"
                                    onPressEnter={(e) => {
                                      const value = (e.target as HTMLInputElement).value.trim();
                                      if (!value) return;
                                      setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                        ? { ...item, tags: [...item.tags, value] }
                                        : item)));
                                      (e.target as HTMLInputElement).value = '';
                                    }}
                                  />
                                </div>
                              </div>
                            )}
                            {chunk.questions.length > 0 && (
                              <div className="jx-chunkSection">
                                <div className="jx-chunkSectionLabel"><span className="jx-chunkSectionIcon">💬</span>关联问题</div>
                                <div className="jx-chunkQuestions">
                                  {chunk.questions.map((question, qi) => (
                                    <div key={qi} className="jx-chunkQuestion">
                                      <span className="jx-chunkQuestionText">{question}</span>
                                      <Button
                                        type="text"
                                        size="small"
                                        className="jx-chunkQuestionDel"
                                        icon={<CloseOutlined style={{ fontSize: 10 }} />}
                                        onClick={() => setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                          ? { ...item, questions: item.questions.filter((_, index) => index !== qi) }
                                          : item)))}
                                      />
                                    </div>
                                  ))}
                                  <Input
                                    size="small"
                                    placeholder="+ 问题"
                                    className="jx-chunkAddInput"
                                    style={{ marginTop: 4 }}
                                    onPressEnter={(e) => {
                                      const value = (e.target as HTMLInputElement).value.trim();
                                      if (!value) return;
                                      setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                        ? { ...item, questions: [...item.questions, value] }
                                        : item)));
                                      (e.target as HTMLInputElement).value = '';
                                    }}
                                  />
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                        {chunk.tags.length === 0 && chunk.questions.length === 0 && (
                          <div className="jx-chunkMeta">
                            <div className="jx-chunkSection">
                              <div className="jx-chunkTagsWrap">
                                <Input
                                  size="small"
                                  placeholder="+ 标签"
                                  className="jx-chunkAddInput"
                                  onPressEnter={(e) => {
                                    const value = (e.target as HTMLInputElement).value.trim();
                                    if (!value) return;
                                    setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                      ? { ...item, tags: [...item.tags, value] }
                                      : item)));
                                    (e.target as HTMLInputElement).value = '';
                                  }}
                                />
                                <Input
                                  size="small"
                                  placeholder="+ 问题"
                                  className="jx-chunkAddInput"
                                  onPressEnter={(e) => {
                                    const value = (e.target as HTMLInputElement).value.trim();
                                    if (!value) return;
                                    setDocChunks((prev) => prev.map((item) => (item.chunk_id === chunk.chunk_id
                                      ? { ...item, questions: [...item.questions, value] }
                                      : item)));
                                    (e.target as HTMLInputElement).value = '';
                                  }}
                                />
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal
        title={`上传文档到「${selectedItem?.name || ''}」`}
        open={uploadDocModalOpen}
        onCancel={() => closeUploadDocModal()}
        maskClosable={false}
        width={uploadStep === 'preview' ? 720 : 520}
        className="jx-kbUploadModal"
        footer={uploadStep === 'config' ? (
          <div className="jx-kbUploadModalFooter">
            <Button onClick={() => closeUploadDocModal()}>取消</Button>
            <Button loading={chunkPreviewLoading} disabled={uploadDocFileList.length === 0} onClick={() => void handleUpload(true)}>预览分块</Button>
            <Button type="primary" loading={uploadDocLoading} disabled={uploadDocFileList.length === 0} onClick={() => void handleUpload(false)}>直接上传</Button>
          </div>
        ) : (
          <div className="jx-kbUploadModalFooter">
            <Button onClick={() => { setUploadStep('config'); setChunkPreviewData(null); setExpandedChunkIndex(null); }}>返回修改</Button>
            <Button type="primary" loading={uploadDocLoading} onClick={() => void handleUpload(false)}>确认上传</Button>
          </div>
        )}
      >
        {uploadStep === 'config' ? (
          <div className="jx-kbUploadModalBody">
            <Upload.Dragger
              className="jx-kbUploadDragger"
              multiple
              accept=".pdf,.docx,.doc,.txt,.md,.csv,.json,.xlsx,.xls"
              beforeUpload={(file) => { setUploadDocFileList((prev) => [...prev, file]); return false; }}
              onRemove={(file) => setUploadDocFileList((prev) => prev.filter((item) => item.name !== file.name || item.size !== file.size))}
              fileList={uploadDocFileList.map((file) => ({ uid: `${file.name}-${file.size}`, name: file.name, size: file.size, status: 'done' as const }))}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽文件到此区域</p>
              <p className="ant-upload-hint">支持 PDF、Word、Excel、TXT、Markdown、CSV、JSON，单文件最大 100MB</p>
            </Upload.Dragger>
            <div className="jx-kbUploadSection">
              <div className="jx-kbUploadFieldLabel">分块方法</div>
              <Select
                value={uploadChunkMethod}
                onChange={setUploadChunkMethod}
                className="jx-kbUploadSelect"
                popupClassName="jx-kbUploadSelectDropdown"
                options={UPLOAD_CHUNK_METHOD_OPTIONS.map((option) => ({
                  value: option.value,
                  label: option.label,
                  desc: option.desc,
                  recommended: option.recommended,
                }))}
                optionRender={(option) => {
                  const data = option.data as {
                    label: string;
                    desc?: string;
                    recommended?: boolean;
                  };
                  return (
                    <div className="jx-kbUploadOption">
                      <div className="jx-kbUploadOptionTop">
                        <span className="jx-kbUploadOptionTitle">{data.label}</span>
                        {data.recommended && (
                          <span className="jx-kbUploadOptionBadge">
                            <StarFilled />
                            <span>推荐</span>
                          </span>
                        )}
                      </div>
                      {data.desc && <div className="jx-kbUploadOptionDesc">{data.desc}</div>}
                    </div>
                  );
                }}
              />
            </div>
            <div className="jx-kbUploadToggleRow">
              <div className="jx-kbUploadToggleCopy">
                <Typography.Text className="jx-kbUploadToggleTitle">启用父子分块</Typography.Text>
                <Typography.Text type="secondary" className="jx-kbUploadToggleDesc">
                  {uploadParentChildIndexing ? '父块存储完整上下文，子块用于向量检索' : '关闭后仅按块索引，不拆分子块'}
                </Typography.Text>
              </div>
              <Switch size="small" checked={uploadParentChildIndexing} onChange={setUploadParentChildIndexing} />
            </div>
            <Collapse
              className="jx-kbUploadCollapse"
              ghost
              items={[{
                key: 'advanced',
                label: <Typography.Text type="secondary" className="jx-kbUploadCollapseLabel">高级索引设置</Typography.Text>,
                children: (
                  <div className="jx-kbUploadAdvanced">
                    <div className="jx-kbUploadAdvancedRow">
                      <div className="jx-kbUploadAdvancedCol">
                        <div className="jx-kbUploadFieldLabel">{uploadParentChildIndexing ? '父块大小（字符）' : '块大小（字符）'}</div>
                        <InputNumber min={256} max={4096} step={128} value={uploadParentChunkSize} onChange={(v) => setUploadParentChunkSize(v ?? 1024)} style={{ width: '100%' }} />
                      </div>
                      {uploadParentChildIndexing && (
                        <div className="jx-kbUploadAdvancedCol">
                          <div className="jx-kbUploadFieldLabel">子块大小（字符）</div>
                          <InputNumber min={64} max={512} step={32} value={uploadChildChunkSize} onChange={(v) => setUploadChildChunkSize(v ?? 128)} style={{ width: '100%' }} />
                        </div>
                      )}
                      {uploadParentChildIndexing && (
                        <div className="jx-kbUploadAdvancedCol">
                          <div className="jx-kbUploadFieldLabel">重叠 token</div>
                          <InputNumber min={0} max={100} value={uploadOverlapTokens} onChange={(v) => setUploadOverlapTokens(v ?? 20)} style={{ width: '100%' }} />
                        </div>
                      )}
                    </div>
                    <div className="jx-kbUploadAdvancedRow">
                      <div className="jx-kbUploadAdvancedCol">
                        <div className="jx-kbUploadFieldLabel">自动关键词数（0=关闭）</div>
                        <InputNumber min={0} max={10} value={uploadAutoKeywordsCount} onChange={(v) => setUploadAutoKeywordsCount(v ?? 0)} style={{ width: '100%' }} />
                      </div>
                      <div className="jx-kbUploadAdvancedCol">
                        <div className="jx-kbUploadFieldLabel">自动问题数（0=关闭）</div>
                        <InputNumber min={0} max={10} value={uploadAutoQuestionsCount} onChange={(v) => setUploadAutoQuestionsCount(v ?? 0)} style={{ width: '100%' }} />
                      </div>
                    </div>
                  </div>
                ),
              }]}
            />
          </div>
        ) : (
          <div className="jx-kbChunkPreview">
            <div className="jx-kbChunkPreviewSummary">
              共预览 {chunkPreviewData?.total_chunks || 0} 个分块
              {uploadParentChildIndexing && ` / ${chunkPreviewData?.total_children || 0} 个子块`}
            </div>
            <div className="jx-kbChunkPreviewList">
              {(chunkPreviewData?.chunks || []).map((chunk) => (
                <div key={chunk.index} className="jx-kbChunkPreviewCard">
                  <button
                    className="jx-kbChunkPreviewHeader"
                    onClick={() => setExpandedChunkIndex(expandedChunkIndex === chunk.index ? null : chunk.index)}
                  >
                    <span>分块 {chunk.index + 1}</span>
                    <span>{chunk.token_count} tokens / {chunk.children_count} 子块</span>
                  </button>
                  <div className="jx-kbChunkPreviewBody">{chunk.content}</div>
                  {expandedChunkIndex === chunk.index && chunk.children_preview.length > 0 && (
                    <div className="jx-kbChunkPreviewChildren">
                      {chunk.children_preview.map((child) => (
                        <div key={child.index} className="jx-kbChunkPreviewChild">
                          <div className="jx-kbChunkPreviewChildIndex">子块 {child.index + 1}</div>
                          <div>{child.content}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
