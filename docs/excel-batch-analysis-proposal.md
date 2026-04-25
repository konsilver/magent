# Excel 批量分析应用 — 设计方案

## 1. 背景与目标

在"应用中心"中新增 **Excel 批量分析** 应用。核心场景：用户上传 Excel 表格（可能 500+ 行），对每行数据进行 AI 分析（打分、分类、提取、摘要、信息补全等），输出一个包含分析结果的新 Excel。

**核心挑战**：Excel 行数多，LLM 上下文无法一次加载全部内容，需要逐批处理。

**设计决策**：

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 执行模式 | MCP 工具内部循环调用 LLM | Agent 自身迭代会导致上下文爆炸，500+ 行需要 100+ 轮对话 |
| 应用场景 | 通用批量分析 | 不限于打分，支持分类、提取、摘要、信息补全等 |
| 进度展示 | 前端实时进度条 | 批量处理耗时长，用户需要知道处理状态 |

---

## 2. 架构设计

### 2.1 整体数据流

```
Agent 调用 MCP 工具            MCP 子进程内部执行                 前端轮询进度
─────────────────            ──────────────────                 ────────────
preview_excel(file_id)     → 读取 Excel 结构 → 返回摘要

start_batch_analysis()     → 创建 task
                             → 启动 asyncio 后台任务
                             → 立即返回 task_id
                               │
                               ├─ 批次1: 组装prompt → 调LLM     GET /v1/batch/{task_id}
                               │         → 解析JSON → 写进度       ← 读进度文件 → 进度条
                               ├─ 批次2: ...
                               ├─ 批次N: ...
                               └─ 生成结果Excel
                                  → save_artifact
                                  → 更新进度为completed

get_batch_status(task_id)  → 读内存中的task状态 → 返回进度/结果
```

### 2.2 进度共享机制

MCP 服务作为 stdio 子进程运行，无法直接访问主进程的数据库。进度共享采用**双通道**方案：

1. **Agent 侧**：MCP 子进程在内存中维护任务状态，Agent 通过 `get_batch_status` 工具查询
2. **前端侧**：MCP 子进程将进度写入 `{STORAGE_PATH}/batch_progress/{task_id}.json`，后端 REST API 读取该文件供前端轮询

### 2.3 LLM 调用策略

**按批调用**（非逐行），每批默认 5 行，以平衡效率和 token 限制：

```
System: 你是数据分析助手。按照指令逐行分析以下数据，以 JSON 数组格式返回结果。
        每行数据独立分析，返回数组中的元素顺序必须与输入行顺序一致。

User:
## 分析指令
{用户定义的 prompt_template，支持 {列名} 占位符}

## 需要输出的列
{output_columns, 如 ["评分", "评分理由", "风险等级"]}

## 待处理数据（共 5 行）
第1行: {"企业名称": "XX科技有限公司", "营收(万元)": "5000", "员工数": "120", ...}
第2行: {"企业名称": "YY制造股份公司", "营收(万元)": "12000", "员工数": "450", ...}
...

请严格返回一个 JSON 数组，每个元素包含上述输出列对应的 key，不要包含其他内容。
```

### 2.4 错误处理

| 场景 | 策略 |
|------|------|
| 单批 LLM 调用失败 | 重试 1 次 |
| 重试仍失败 | 该批行的输出列标记 `"处理失败"` ，继续处理后续批次 |
| LLM 返回非 JSON | 尝试从响应中提取 JSON 块；失败则标记错误 |
| Excel 文件损坏 | `preview_excel` 阶段即报错，不进入批量处理 |
| 进度文件写入失败 | 仅影响前端进度展示，不中断处理 |

---

## 3. 组件设计

### 3.1 MCP Server — `excel_batch_mcp`

**位置**：`src/backend/mcp_servers/excel_batch_mcp/`

#### 工具定义 (`server.py`)

| 工具名 | 参数 | 返回 | 说明 |
|--------|------|------|------|
| `preview_excel` | `file_id: str` | `{sheet_names, columns, row_count, sample_rows}` | 预览 Excel 结构，返回前 5 行样本 |
| `start_batch_analysis` | `file_id, prompt_template, output_columns, batch_size=5, sheet_name="", model_name=""` | `{task_id}` | 启动异步批量处理，立即返回 |
| `get_batch_status` | `task_id: str` | `{status, total_rows, processed_rows, percent, result_file_id, error}` | 查询处理进度和结果 |

#### 核心实现 (`impl.py`)

| 函数 | 职责 |
|------|------|
| `_download_artifact(file_id)` | 从 artifact store 下载文件字节 |
| `_read_excel(file_bytes, sheet_name)` | openpyxl `read_only=True` 读取 → `(headers, rows_as_dicts)` |
| `_render_batch_prompt(template, headers, batch, output_columns)` | 组装含行数据的 LLM prompt |
| `_call_llm(system_prompt, user_prompt, model_name)` | httpx 调用 OpenAI 兼容 API |
| `_parse_llm_json(response_text, expected_count)` | 提取并解析 JSON 数组 |
| `_build_result_excel(headers, rows, output_columns, results)` | 生成带样式的输出 Excel |
| `_write_progress(task_id, task)` | 写进度 JSON 到共享存储路径 |
| `_process_batch_task(task, ...)` | asyncio 后台任务：批次循环主体 |

**参考实现**：
- LLM 调用模式：`mcp_servers/generate_chart_tool_mcp/chart.py` — `make_chat_model()`
- Artifact 存储：`mcp_servers/report_export_mcp/impl.py` — `save_artifact_bytes()`
- sys.path 设置：chart.py — `sys.path.insert(0, ...)` 引入 `core/` 和 `artifacts/`

### 3.2 后端进度查询 API

**新建** `src/backend/api/routes/v1/batch.py`

```python
router = APIRouter(prefix="/v1/batch", tags=["batch"])

@router.get("/{task_id}", summary="获取批量分析任务进度")
async def get_batch_progress(task_id: str):
    """读取 {STORAGE_PATH}/batch_progress/{task_id}.json 返回进度信息"""
    # 返回: {status, total_rows, processed_rows, percent, result_file_id, error}
```

**修改** `src/backend/api/app.py` — 注册 `batch_router`

### 3.3 Catalog 注册

**修改** `src/backend/configs/catalog.json`

MCP 条目：
```json
{
  "id": "excel_batch_mcp",
  "kind": "mcp_server",
  "name": "Excel批量分析",
  "description": "对Excel文件进行批量AI分析，支持逐行评分、分类、提取、摘要等。MCP工具内部循环调用LLM，处理500+行不占用对话上下文。",
  "enabled": true,
  "version": "v1"
}
```

Skill 条目：
```json
{
  "id": "excel-batch-analysis",
  "kind": "tool_bundle",
  "name": "Excel批量分析",
  "description": "当用户上传Excel文件并要求批量分析（评分、分类、提取、摘要、信息补全等）时使用。先预览文件结构，帮用户制定分析模板，然后启动批量处理并跟踪进度。",
  "enabled": true,
  "version": "1.0.0",
  "config": { "tags": ["excel", "batch-analysis", "data-processing", "jingxin-scenario"] }
}
```

**修改** `src/backend/configs/display_names.py` — 添加中文名映射

### 3.4 Skill 指令文件

**新建** `src/backend/agent_skills/skills/excel-batch-analysis/SKILL.md`

```yaml
---
name: excel-batch-analysis
display_name: Excel批量分析
description: 对用户上传的Excel文件逐行进行AI分析（评分、分类、提取、补全等），支持大文件（500+行）批量处理
version: 1.0.0
tags: excel,batch-analysis,data-processing,jingxin-scenario
allowed_tools: preview_excel start_batch_analysis get_batch_status
---
```

**Agent 工作流指令**：

1. 用户上传 Excel 后，调用 `preview_excel` 展示文件结构（列名、行数、样本行）
2. 引导用户描述分析需求，帮助拟定 `prompt_template`
   - 解释 `{列名}` 占位符语法：`{企业名称}` 会被替换为每行的实际值
   - 提供常见模板示例（打分、分类、摘要提取）
3. 与用户确认 `output_columns`（新增的输出列名列表）
4. 调用 `start_batch_analysis` 启动处理
5. 每 10-15 秒调用 `get_batch_status` 向用户汇报进度
6. 完成后提供下载链接，并展示前几行结果摘要

### 3.5 前端进度组件

**新建** `src/frontend/src/components/tool/BatchProgressBar.tsx`

- 使用 Ant Design `Progress` 组件 + 状态文字
- `useEffect` + `setInterval` 每 3 秒轮询 `GET /api/v1/batch/{task_id}`
- 三种状态展示：
  - `running` → 进度条 + "已处理 50/200 行 (25%)"
  - `completed` → 完成提示 + 下载结果按钮
  - `failed` → 错误信息展示

**修改** `src/frontend/src/components/tool/ToolOutputRenderer.tsx`

在 `renderToolOutputBody` 函数中添加 `start_batch_analysis` 工具的渲染分支：
```tsx
if (toolName === 'start_batch_analysis') {
  const data = (typeof out === 'object' && out !== null ? out : {}) as any;
  const taskId = data?.task_id;
  if (taskId) return <BatchProgressBar taskId={taskId} />;
}
```

**修改** `src/frontend/src/api.ts` — 添加 `getBatchProgress(taskId)` 函数

---

## 4. 文件清单

| 操作 | 文件路径 | 说明 |
|------|----------|------|
| 新建 | `src/backend/mcp_servers/excel_batch_mcp/__init__.py` | 空文件 |
| 新建 | `src/backend/mcp_servers/excel_batch_mcp/server.py` | FastMCP 服务，3 个工具 |
| 新建 | `src/backend/mcp_servers/excel_batch_mcp/impl.py` | 核心业务逻辑 |
| 新建 | `src/backend/mcp_servers/excel_batch_mcp/_selftest.py` | 自测脚本 |
| 新建 | `src/backend/api/routes/v1/batch.py` | 进度查询 REST API |
| 新建 | `src/backend/agent_skills/skills/excel-batch-analysis/SKILL.md` | Agent 工作流指令 |
| 新建 | `src/frontend/src/components/tool/BatchProgressBar.tsx` | 前端进度组件 |
| 修改 | `src/backend/api/app.py` | 注册 batch_router |
| 修改 | `src/backend/configs/catalog.json` | 添加 mcp + skill 条目 |
| 修改 | `src/backend/configs/display_names.py` | 添加中文名映射 |
| 修改 | `src/frontend/src/components/tool/ToolOutputRenderer.tsx` | 添加批量进度渲染 |
| 修改 | `src/frontend/src/api.ts` | 添加 getBatchProgress |

---

## 5. 用户交互示例

```
用户: [上传 企业评分表.xlsx] 帮我对这些企业进行综合评分

Agent: 我来先预览一下你上传的 Excel 文件。
       [调用 preview_excel]

       文件结构如下：
       - Sheet: Sheet1
       - 列名: 企业名称 | 注册资本(万元) | 营收(万元) | 员工数 | 专利数 | 所属行业
       - 总行数: 326 行
       - 样本数据（前3行）:
         | 企业名称 | 注册资本(万元) | 营收(万元) | 员工数 | 专利数 | 所属行业 |
         |----------|----------------|------------|--------|--------|----------|
         | XX科技   | 5000           | 12000      | 120    | 15     | 软件     |
         | YY制造   | 8000           | 25000      | 450    | 8      | 制造业   |
         | ZZ生物   | 3000           | 6000       | 80     | 22     | 生物医药 |

       请告诉我你的评分标准，我来帮你制定分析模板。

用户: 按照以下标准打分（总分100分）：
      - 营收规模 30分：5亿以上满分，1亿以上20分，5000万以上10分
      - 创新能力 30分：专利数20个以上满分，10个以上20分
      - 企业规模 20分：员工500以上满分，200以上15分
      - 资本实力 20分：注册资本1亿以上满分，5000万以上15分

Agent: 好的，我已整理好分析模板：

       分析指令：根据以下评分标准对企业进行综合评分...
       输出列：["营收规模得分", "创新能力得分", "企业规模得分", "资本实力得分", "总分", "评级", "评分说明"]

       共 326 行数据，预计处理时间约 5-8 分钟。确认开始？

用户: 开始

Agent: [调用 start_batch_analysis]
       已启动批量分析任务，正在处理中...

       [前端自动展示进度条：已处理 15/326 行 (4.6%)]

       ... (每15秒汇报一次进度) ...

       已处理 326/326 行 (100%)！

       处理完成！结果文件已生成：[下载 企业评分结果.xlsx]

       结果摘要：
       - A级（80分以上）：42 家
       - B级（60-79分）：128 家
       - C级（40-59分）：112 家
       - D级（40分以下）：44 家
```

---

## 6. 验证方案

1. **MCP 自测**：`PYTHONPATH=src/backend python -m mcp_servers.excel_batch_mcp._selftest`
2. **后端重建**：`docker-compose up -d --build backend`
3. **前端重建**：`docker-compose up -d --build frontend`
4. **端到端测试**：
   - 上传 10-20 行的测试 Excel → 验证基本流程
   - 上传 200+ 行的 Excel → 验证批量处理稳定性
   - 验证进度条实时更新
   - 验证结果 Excel 内容正确、样式合理
   - 验证错误处理（上传非 Excel 文件、空表格等）

---

## 7. 后续扩展

- **模板库**：预设常用分析模板（企业评分、项目评审、风险评估等），用户可直接选用
- **断点续传**：处理中断后可从上次进度继续
- **多 Sheet 支持**：同时处理多个 Sheet，合并结果
- **并行处理**：多批次并行调用 LLM，缩短处理时间（需注意 API 并发限制）
- **结果校验**：处理完成后自动抽样检查结果一致性
