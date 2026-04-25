# 私有知识库实施规格

> **状态**：实现中
> **日期**：2026-03-11

---

## 架构概览

```
enabled_kbs 列表（前端传入）
  ├── UUID / 非 kb_ 前缀 → retrieve_dataset_content MCP（Dify 公有库，现有）
  └── kb_ 前缀          → retrieve_local_kb MCP（本地 Milvus 私有库，新增）

Milvus 实例（复用现有）
  ├── jingxin_memories       ← mem0 记忆（不动）
  └── jingxin_kb_private     ← 私有库向量（新增）
        row_type="chunk"     ← 子块向量（用于检索）
        row_type="question"  ← 问题向量（多表面索引）
        parent_chunk_id → PostgreSQL kb_chunks.content（父块原文，给 LLM）
```

---

## Milvus Collection Schema（`jingxin_kb_private`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | VARCHAR(64) PK | chunk 行：`child_id`；question 行：`q_{parent_id}_{N}` |
| `parent_chunk_id` | VARCHAR(64) | 指向 PostgreSQL `kb_chunks.chunk_id` |
| `row_type` | VARCHAR(16) | `"chunk"` \| `"question"` |
| `user_id` | VARCHAR(64) | 行级隔离，所有查询强制过滤 |
| `kb_id` | VARCHAR(64) | 所属私有库，格式 `kb_xxxxxxxxxxxxxxxx` |
| `document_id` | VARCHAR(64) | 所属文档 |
| `title` | VARCHAR(500) | 文档标题 |
| `content` | VARCHAR(4096) | chunk 行：子块文本；question 行：问题文本 |
| `tags_text` | VARCHAR(1000) | 标签拼接字符串，增强 BM25 |
| `chunk_index` | INT64 | 块序号 |
| `dense_embedding` | FloatVector(1024) | 语义向量，维度=`MEM0_EMBED_DIMS` |
| `sparse_embedding` | SparseFloatVector | BM25 稀疏向量（Milvus 2.4+） |

**索引：**
- `dense_embedding`：IVF_FLAT + IP
- `sparse_embedding`：SPARSE_INVERTED_INDEX + BM25
- `user_id`, `kb_id`, `document_id`, `row_type`：INVERTED 标量索引

---

## PostgreSQL 新增表

### `KBChunk`（`src/backend/core/db_models.py`）

```python
class KBChunk(Base):
    __tablename__ = "kb_chunks"
    chunk_id    = Column(String(64), primary_key=True)   # 即 parent_id
    kb_id       = Column(String(64), FK kb_spaces)
    document_id = Column(String(64), FK kb_documents)
    chunk_index = Column(Integer)
    content     = Column(Text)       # 父块原文，检索命中后返回 LLM
    tags        = Column(JSONB)      # ["数字化转型", "申报条件"]
    questions   = Column(JSONB)      # ["数字化项目怎么申报？", ...]
    char_start  = Column(Integer)
    char_end    = Column(Integer)
    created_at  = Column(TIMESTAMP)
    updated_at  = Column(TIMESTAMP)
```

### `KBSpace` 新增字段

```python
visibility   = Column(String(16), default="private")   # "public"|"private"
chunk_method = Column(String(32), default="semantic")  # 分块策略
```

---

## 新增文件

```
src/backend/
  utils/
    kb_vector.py                          ← Milvus collection + 混合检索
    kb_parser.py                          ← 文档解析 + 父子分块
  mcp_servers/
    retrieve_local_kb_mcp/
      __init__.py
      server.py                           ← MCP 工具定义
      impl.py                             ← 检索逻辑
```

### `kb_vector.py` 关键接口

```python
get_or_create_collection()               # 幂等创建 collection
embed_text(text) -> list[float]          # 调 MEM0_EMBED_URL
embed_batch(texts) -> list[list[float]]
upsert_rows(rows: list[dict])            # 批量写 Milvus
hybrid_search(user_id, kb_ids, query, query_vec, top_k) -> list[dict]
delete_by_document(document_id, user_id)
delete_by_kb(kb_id, user_id)
build_sparse_text(content, tags) -> str  # content + "[tag1] [tag2]"
reindex_chunk_tags(chunk_id, content, tags)    # 标签变更后重写 sparse_embedding
upsert_question_rows(parent_chunk_id, questions, ...)  # 问题变更后重写 question 行
```

### `kb_parser.py` 关键接口

```python
parse_and_chunk(file_bytes, mime_type, chunk_method, parent_size=1024,
                child_size=128, overlap=20) -> list[ParentChunk]

# ParentChunk.children → ChildChunk（写 Milvus）
# ParentChunk.content  → 父块原文（写 PostgreSQL kb_chunks）
```

分块策略（`chunk_method`）：
- `semantic`（默认）：按段落 + token 限制
- `laws`：按"第X条/章/节"边界
- `qa`：每对问答为一块

---

## 改动文件

### `core/factory.py`

`_effective_mcp_server_keys()`：
- `enabled_kb_ids` 中无 `kb_` 前缀 → discard `retrieve_local_kb`
- `enabled_kb_ids` 中全为 `kb_` 前缀 → discard `retrieve_dataset_content`

`_apply_runtime_kb_constraints(enabled_servers, enabled_kb_ids, current_user_id)`：
- `dify_ids`（非 `kb_` 前缀）→ 注入 `retrieve_dataset_content` 的 `DIFY_ALLOWED_DATASET_IDS`
- `local_ids`（`kb_` 前缀）→ 注入 `retrieve_local_kb` 的 `LOCAL_KB_ALLOWED_IDS` + `CURRENT_USER_ID`

`create_agent_executor()` 新增参数 `current_user_id`，透传到 `_apply_runtime_kb_constraints`。

### `routing/workflow.py`

两处 `create_agent_executor` 调用均追加 `current_user_id=str(context.get("user_id", ""))`。

### `configs/mcp_config.py`

新增 `retrieve_local_kb` server 配置，env 包含：
`MILVUS_URL`, `MILVUS_TOKEN`, `MEM0_EMBED_*`, `DATABASE_URL`,
`LOCAL_KB_ALLOWED_IDS`（运行时注入）, `CURRENT_USER_ID`（运行时注入）。

### `configs/catalog.json`

`mcp` 数组新增 `retrieve_local_kb` 条目，`enabled: true`。

### `api/routes/v1/kb.py`

1. `POST /{kb_id}/documents`：上传后触发 `background_tasks.add_task(_vectorise_document_background, ...)`
   流水线：`parse_and_chunk` → `embed_batch` → `upsert_rows(Milvus)` + `KBChunk(PostgreSQL)`
2. 新增 `GET /{kb_id}/chunks`：列出父块（含 tags/questions）
3. 新增 `PATCH /{kb_id}/chunks/{chunk_id}`：更新 tags/questions，同步重写 Milvus

### `api/routes/v1/catalog.py`

`GET /v1/catalog` 返回公有库 + 私有库合并列表：
- 公有库（Dify）：附加 `visibility="public"`, `is_public=True`
- 私有库（本地）：附加 `visibility="private"`, `is_public=False`

### `prompts/…/65_citations.system.md`

工具表格新增行：`retrieve_local_kb` | 用户私有知识库检索

---

## 运行时环境变量

| 变量 | 来源 | 说明 |
|---|---|---|
| `MILVUS_URL` | `.env` | Milvus 连接地址 |
| `MILVUS_TOKEN` | `.env` | Milvus 鉴权 |
| `MEM0_EMBED_URL` | `.env` | Embedding 服务地址（复用 mem0） |
| `MEM0_EMBED_MODEL` | `.env` | Embedding 模型名 |
| `MEM0_EMBED_API_KEY` | `.env` | Embedding API Key |
| `MEM0_EMBED_DIMS` | `.env` | 向量维度（默认 1024） |
| `KB_PARENT_CHUNK_SIZE` | `.env` 可选 | 父块 token 数（默认 1024） |
| `KB_CHILD_CHUNK_SIZE` | `.env` 可选 | 子块 token 数（默认 128） |
| `KB_DETAIL_CONTENT_MAX_CHARS` | `.env` 可选 | 返回内容截断（默认 50000） |
| `LOCAL_KB_ALLOWED_IDS` | factory 运行时注入 | 当前请求允许访问的私有库 ID |
| `CURRENT_USER_ID` | factory 运行时注入 | 当前用户 ID，Milvus 查询隔离 |

---

## 安全隔离

1. **MCP 层**：`LOCAL_KB_ALLOWED_IDS` 每次请求动态生成，MCP 拒绝列表外的 `kb_id`
2. **Milvus 层**：所有查询 `expr` 强制包含 `user_id == "{CURRENT_USER_ID}"`
3. **API 层**：`KBService` 在 CRUD 前校验 `space.user_id == current_user.user_id`

---

## DB 迁移

```bash
make migrate-new msg="add kb_chunks table and visibility chunk_method to kb_spaces"
alembic upgrade head
```

**Milvus 版本确认（≥ 2.4.0 必需）：**
```bash
docker exec milvus-standalone milvus --version
```
