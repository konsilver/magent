  ---
  详细实现方案

  文件 1：65_citations.system.md（新建）

  ## 引用格式规范（Source Citation）

  当你在回答中引用了工具返回的特定数据或文字时，**必须**在引用处紧跟引用标记，取代原来的`（来源：...）`格式。

  **标记格式：`[ref:工具名-序号]`**

  - `工具名` 使用英文工具名称（见下表）
  - `序号` 从 1 开始，代表该工具 `items`/`results` 列表中的第 N 条
  - 同一处多条引用：直接并列写 `[ref:internet_search-1][ref:retrieve_dataset_content-2]`
  - `query_database` 每次调用整体视为一条，写 `[ref:query_database-1]`

  | 工具名 | 说明 |
  |---|---|
  | `internet_search` | 互联网搜索 |
  | `retrieve_dataset_content` | 知识库/文档检索 |
  | `query_database` | 数据库查询 |
  | `get_industry_news` | 产业资讯 |
  | `get_latest_ai_news` | AI 动态 |
  | `get_chain_information` | 产业链分析 |

  **示例：**
  > 宁波规上工业增加值增速为 5.2%[ref:query_database-1]，其中新能源汽车产业增速最高[ref:get_industry_news-3]。
  > 根据政策文件[ref:retrieve_dataset_content-1]，制造业数字化转型将是重点方向。

  ---
  文件 2：src/backend/routing/citations.py（新建）

  """Per-request citation registry: extract & track citations from tool results."""

  from __future__ import annotations

  import json
  from dataclasses import asdict, dataclass, field
  from typing import Any, Dict, List, Optional


  @dataclass
  class CitationItem:
      id: str              # 如 "internet_search-1"
      tool_name: str
      tool_id: Optional[str]
      title: str
      url: str
      snippet: str
      source_type: str     # internet | knowledge_base | database | industry_news | ai_news | chain_info

      def to_dict(self) -> Dict[str, Any]:
          return asdict(self)


  _SOURCE_TYPE_MAP: Dict[str, str] = {
      "internet_search": "internet",
      "retrieve_dataset_content": "knowledge_base",
      "query_database": "database",
      "get_industry_news": "industry_news",
      "get_latest_ai_news": "ai_news",
      "get_chain_information": "chain_info",
  }


  def extract_citations(
      tool_name: str,
      tool_id: Optional[str],
      result: Any,
  ) -> List[CitationItem]:
      """
      从工具结果中提取 CitationItem 列表。
      各工具的输出格式不同，分别处理。
      """
      source_type = _SOURCE_TYPE_MAP.get(tool_name, "unknown")
      items: List[CitationItem] = []

      if isinstance(result, str):
          try:
              result = json.loads(result)
          except Exception:
              result = {"result": result}

      if not isinstance(result, dict):
          result = {"result": str(result)}

      try:
          if tool_name == "internet_search":
              items = _extract_internet_search(tool_id, source_type, result)
          elif tool_name == "retrieve_dataset_content":
              items = _extract_dataset_content(tool_id, source_type, result)
          elif tool_name == "query_database":
              items = _extract_database(tool_id, source_type, result)
          elif tool_name in {"get_industry_news", "get_latest_ai_news"}:
              items = _extract_news(tool_name, tool_id, source_type, result)
          elif tool_name == "get_chain_information":
              items = _extract_chain_info(tool_id, source_type, result)
      except Exception:
          pass

      return items


  def _extract_internet_search(tool_id, source_type, data) -> List[CitationItem]:
      sr = data.get("result") or data
      if isinstance(sr, dict):
          results = sr.get("results", [])
      elif isinstance(sr, list):
          results = sr
      else:
          return []
      out = []
      for i, item in enumerate(results, 1):
          if not isinstance(item, dict):
              continue
          title = item.get("title") or item.get("url") or "互联网搜索结果"
          url = item.get("url", "")
          snippet = (item.get("content") or item.get("snippet") or "")[:300]
          out.append(CitationItem(
              id=f"internet_search-{i}",
              tool_name="internet_search",
              tool_id=tool_id,
              title=str(title)[:120],
              url=str(url),
              snippet=str(snippet),
              source_type=source_type,
          ))
      return out


  def _extract_dataset_content(tool_id, source_type, data) -> List[CitationItem]:
      items = data.get("items", [])
      out = []
      for i, item in enumerate(items, 1):
          if not isinstance(item, dict):
              continue
          doc = item.get("document") or {}
          seg = item.get("segment") or {}
          title = (doc.get("name") or doc.get("title") or "知识库文档")
          snippet = (seg.get("content") or item.get("content") or "")[:300]
          out.append(CitationItem(
              id=f"retrieve_dataset_content-{i}",
              tool_name="retrieve_dataset_content",
              tool_id=tool_id,
              title=str(title)[:120],
              url="",
              snippet=str(snippet),
              source_type=source_type,
          ))
      return out


  def _extract_database(tool_id, source_type, data) -> List[CitationItem]:
      res = data.get("result", data)
      snippet = (str(res) if not isinstance(res, str) else res)[:300]
      return [CitationItem(
          id="query_database-1",
          tool_name="query_database",
          tool_id=tool_id,
          title="数据库查询结果",
          url="",
          snippet=snippet,
          source_type=source_type,
      )]


  def _extract_news(tool_name, tool_id, source_type, data) -> List[CitationItem]:
      items = data.get("items", [])
      out = []
      for i, item in enumerate(items, 1):
          if not isinstance(item, dict):
              continue
          title = str(item.get("标题") or item.get("title") or "资讯")[:120]
          url = str(item.get("url") or item.get("链接") or "")
          summary = str(item.get("摘要") or item.get("summary") or "")
          time_str = str(item.get("时间") or "")
          snippet = f"[{time_str}] {summary}" if time_str else summary
          out.append(CitationItem(
              id=f"{tool_name}-{i}",
              tool_name=tool_name,
              tool_id=tool_id,
              title=title,
              url=url,
              snippet=snippet[:300],
              source_type=source_type,
          ))
      return out


  def _extract_chain_info(tool_id, source_type, data) -> List[CitationItem]:
      return [CitationItem(
          id="get_chain_information-1",
          tool_name="get_chain_information",
          tool_id=tool_id,
          title="产业链分析报告",
          url="",
          snippet="产业链深度全景分析数据",
          source_type=source_type,
      )]

  ---
  文件 3：workflow.py 修改点

  # 在 astream_chat_workflow 函数顶部，添加：
  from routing.citations import extract_citations

  # ...（已有代码）
  all_citations: List[Dict[str, Any]] = []  # 全量 citation 注册表

  # 在处理 tool_result 的块中，现有代码后添加：
  if node_type == "tools" and message_like:
      # ... (已有的提取 tool_result_json 的代码) ...

      # ★ 新增：提取 citations
      citations = extract_citations(tool_name, tool_id, tool_result_json)
      citation_dicts = [c.to_dict() for c in citations]
      all_citations.extend(citation_dicts)

      yield {
          "type": "tool_result",
          "tool_name": tool_name,
          "tool_args": ...,
          "result": tool_result_json,
          "tool_id": tool_id,
          "citations": citation_dicts,  # ★ 新增字段
      }

  # 在末尾的 meta 事件中：
  yield {
      "type": "meta",
      "route": "main",
      "is_markdown": _looks_markdown(full_response),
      "sources": _resolve_sources_conflict([]),
      "artifacts": [],
      "warnings": warnings,
      "citations": all_citations,  # ★ 新增字段
  }

  ---
  文件 4：types.ts 新增类型

  export interface CitationItem {
    id: string;           // "internet_search-1"
    tool_name: string;
    tool_id?: string;
    title: string;
    url: string;
    snippet: string;
    source_type: 'internet' | 'knowledge_base' | 'database'
               | 'industry_news' | 'ai_news' | 'chain_info' | 'unknown';
  }

  // ChatMessage 增加字段
  export interface ChatMessage {
    // ...现有字段...
    citations?: CitationItem[];   // ← 新增
  }

  ---
  文件 5：App.tsx 前端变更

  5.1 收集 citations（SSE 解析层）

  // 在 SSE 流变量区声明
  let allCitations: CitationItem[] = [];

  // 在 tool_result 事件处理中
  if (eventType === 'tool_result') {
    // ...已有处理...
    const newCitations = Array.isArray(eventObj.citations)
      ? (eventObj.citations as CitationItem[]) : [];
    allCitations = [...allCitations, ...newCitations];
  }

  // 在 meta 事件处理中
  if (eventType === 'meta') {
    if (Array.isArray(eventObj.citations)) {
      allCitations = eventObj.citations as CitationItem[];
    }
    // ...
  }

  // 在最终 setStore 时保存到消息
  msgs[msgs.length - 1] = {
    ...last,
    content: full,
    citations: allCitations.length > 0 ? allCitations : undefined,
    // ...
  };

  5.2 CitationBadge 组件

  import { Popover } from 'antd';
  import { LinkOutlined } from '@ant-design/icons';

  function CitationBadge({ citId, citations }: { citId: string; citations: CitationItem[] }) {
    const cit = citations.find(c => c.id === citId);
    if (!cit) return <sup style={{ color: '#aaa' }}>[?]</sup>;

    const typeLabel: Record<string, string> = {
      internet: '🌐 互联网',
      knowledge_base: '📚 知识库',
      database: '🗄️  数据库',
      industry_news: '📰 产业资讯',
      ai_news: '🤖 AI 动态',
      chain_info: '🔗 产业链',
    };
    const num = citId.split('-').pop();
    const icon = { internet: '🌐', knowledge_base: '📚', database: '🗄️ ',
                   industry_news: '📰', ai_news: '🤖', chain_info: '🔗' }[cit.source_type] || '📄';

    const card = (
      <div style={{ maxWidth: 320 }}>
        <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 13 }}>
          {typeLabel[cit.source_type] || '来源'} · {icon}
        </div>
        <div style={{ fontSize: 13, color: '#222', marginBottom: 4 }}>
          {cit.url
            ? <a href={cit.url} target="_blank" rel="noopener noreferrer">{cit.title}</a>
            : <span>{cit.title}</span>}
        </div>
        {cit.snippet && (
          <div style={{ fontSize: 12, color: '#666', lineHeight: 1.5, borderLeft: '2px solid #ddd', paddingLeft: 8 }}>
            {cit.snippet.length > 200 ? cit.snippet.slice(0, 200) + '…' : cit.snippet}
          </div>
        )}
        {cit.url && (
          <div style={{ marginTop: 6, fontSize: 11, color: '#999' }}>
            <LinkOutlined /> {new URL(cit.url).hostname}
          </div>
        )}
      </div>
    );

    return (
      <Popover content={card} trigger={['hover', 'click']} placement="top">
        <sup style={{
          cursor: 'pointer', color: '#1677ff', fontWeight: 600,
          fontSize: '0.75em', background: '#e8f4ff',
          borderRadius: 3, padding: '0 3px', margin: '0 1px',
        }}>
          {icon}{num}
        </sup>
      </Popover>
    );
  }

  5.3 渲染层：解析 [ref:tool-N] 标记

  这是最关键的渲染逻辑。由于当前用 dangerouslySetInnerHTML 渲染 markdown，需要将文本先经 marked 渲染，再分割出 citation 占位符：

  const CITATION_PATTERN = /\[ref:([\w]+-\d+)\]/g;

  function renderTextWithCitations(
    text: string,
    isMarkdown: boolean,
    citations: CitationItem[],
    extraClass?: string,
  ) {
    if (!citations?.length || !CITATION_PATTERN.test(text)) {
      // 无引用标记，走原路径
      return (
        <span
          className={extraClass}
          {...(isMarkdown
            ? { dangerouslySetInnerHTML: { __html: mdToHtml(text) } }
            : {})}
        >
          {isMarkdown ? undefined : text}
        </span>
      );
    }

    // 有引用标记：先将 [ref:xxx] 替换为独特占位符，再 markdown 渲染
    const PLACEHOLDER = '___CITREF___';
    const citRefs: string[] = [];
    const textWithPlaceholders = text.replace(CITATION_PATTERN, (_, id) => {
      citRefs.push(id);
      return `<span data-citref="${citRefs.length - 1}"></span>`;
    });

    const html = isMarkdown ? mdToHtml(textWithPlaceholders) : textWithPlaceholders;

    // 按占位符 span 分割 HTML，插入 React 组件
    const parts = html.split(/<span data-citref="(\d+)"><\/span>/);
    // parts: [text0, idx0, text1, idx1, text2, ...]

    return (
      <>
        {parts.map((part, i) => {
          if (i % 2 === 0) {
            // HTML 文本部分
            return part
              ? <span key={i} className={extraClass}
                  dangerouslySetInnerHTML={{ __html: part }} />
              : null;
          } else {
            // Citation badge
            const citId = citRefs[parseInt(part, 10)];
            return citId
              ? <CitationBadge key={i} citId={citId} citations={citations} />
              : null;
          }
        })}
      </>
    );
  }

  5.4 在 segment 文本渲染处使用新函数

  替换原来的：
  // 原来
  <div key={segKey} className={`jx-bubble ...`}>
    {m.isMarkdown
      ? <span dangerouslySetInnerHTML={{ __html: mdToHtml(textContent) }} />
      : textContent}
  </div>

  改为：
  // 新版
  <div key={segKey} className={`jx-bubble ...`}>
    {renderTextWithCitations(textContent, m.isMarkdown ?? false, m.citations ?? [])}
    {/* streaming indicator */}
  </div>

  ---
  多工具并发时的引用处理

  多个工具同时（或先后）调用时，由于 id 格式包含工具名（如 internet_search-1、retrieve_dataset_content-2），不会冲突。all_citations 是 flat list，按工具调用顺序追加：

  [internet_search-1, internet_search-2,   ← 第一个工具
   retrieve_dataset_content-1,             ← 第二个工具
   get_industry_news-1, get_industry_news-2] ← 第三个工具

  LLM 生成文本时可以同时引用多个来源：
  "据最新动态[ref:get_industry_news-1]及政策文件[ref:retrieve_dataset_content-1]，..."

  ---
  各工具引用卡片样式说明

  ┌──────────────────────────┬──────┬────────┬─────────────────┐
  │           工具           │ 图标 │ 有 URL │    卡片内容     │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ internet_search          │ 🌐   │ ✅     │ 标题+摘要+域名  │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ retrieve_dataset_content │ 📚   │ ❌     │ 文档名+段落内容 │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ query_database           │ 🗄️    │ ❌     │ 查询结果摘要    │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ get_industry_news        │ 📰   │ 可能有 │ 标题+时间+摘要  │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ get_latest_ai_news       │ 🤖   │ ❌     │ 时间+标题+摘要  │
  ├──────────────────────────┼──────┼────────┼─────────────────┤
  │ get_chain_information    │ 🔗   │ ❌     │ 产业链分析标识  │
  └──────────────────────────┴──────┴────────┴─────────────────┘

  ---
  实施顺序建议

  1. Step 1（纯后端，无前端改动）：先建 citations.py + 修改 workflow.py 让 tool_result 事件带 citations 字段，用 curl/日志验证数据格式
  2. Step 2（系统 prompt）：新建 65_citations.system.md，测试 LLM 是否生成 [ref:...] 标记
  3. Step 3（前端类型 + SSE 收集）：修改 types.ts，在 App.tsx SSE 解析中收集 citations
  4. Step 4（前端渲染）：实现 CitationBadge + renderTextWithCitations，接入现有渲染路径