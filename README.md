# Jingxin-Agent

Jingxin-Agent 是一个面向业务分析与报告生成场景的全栈 AI 智能体项目。
仓库同时包含 FastAPI 后端、React 前端、MCP 工具服务、知识库管理、
SSO 会话能力、可观测性组件、隔离代码执行 sidecar，以及可选的 mem0
长期记忆基础设施。

当前代码以 `src/backend` 和 `src/frontend` 为主，默认通过
`docker-compose.yml` 启动完整开发环境。后端默认端口为 `3001`，前端默认
端口为 `3002`。

## 项目概览

这个仓库不只是一个聊天接口，还包含了一套围绕经信分析场景的完整工作流。
你可以把它理解为一个支持多工具、多会话、多知识源、多种认证方式以及
可编程技能包的业务智能体平台。

- 流式与非流式对话接口，支持多轮会话持久化、跨轮文件摘要注入。
- 能力中心，支持按用户维度启用或禁用 skills、agents、MCP 和知识库。
- 本地知识库与 Dify 数据集两种知识库模式，支持 Milvus 向量检索。
- 文件上传、文件解析、图表生成、Word / Excel / PDF / PPT 导出、附件
  下载与 Canvas 在线编辑（Univer 电子表格）。
- 计划模式（Plan Mode）：AI 拆解任务→分步执行→结果聚合。
- 定时自动化任务（Automation）：croniter 驱动的 Prompt / Plan 调度。
- 代码执行 Lab：通过隔离 sidecar 运行 Python/JS/Bash，结果以
  artifact 形式呈现并可再执行。
- 我的空间（MySpace）：统一管理用户上传文件、生成文件、收藏会话与
  任务执行通知。
- 子智能体（User Agent）：用户可自建带独立系统提示词与可见能力范围
  的智能体，在对话中通过 @提及 调用。
- 会话分享：生成 3 天 / 15 天 / 永久有效的对话分享链接。
- `mock`、`remote`、`session` 三种认证模式，以及可选的 Mock SSO。
- Prometheus、Grafana、Jaeger 和告警规则。
- 可选的 mem0 记忆系统，依赖 Milvus 与 Neo4j。
- 后台双平台：`/admin` 内容运营平台 + `/config` 系统配置平台，覆盖
  版本说明、能力中心、Skill/Prompt/MCP/模型/定价/用量/账单管理。

## 技术栈

项目采用前后端分离结构，核心依赖如下。

| 层级 | 技术 |
| --- | --- |
| 后端 API | FastAPI、Uvicorn、Pydantic v2 |
| 智能体编排 | AgentScope 1.0+（ReActAgent、Toolkit、StdIOStatefulClient） |
| 数据层 | PostgreSQL、SQLAlchemy、Alembic、Redis |
| 工具协议 | MCP（stdio + streamable_http） |
| 任务调度 | croniter、APScheduler 风格的 AutomationScheduler |
| 前端 | React 19、Vite 7、TypeScript、Ant Design、Zustand |
| 电子表格 | Univer（Canvas 面板内嵌） |
| 存储 | 本地存储、S3、阿里云 OSS |
| 文档与导出 | python-docx、openpyxl、pptxgenjs、Playwright、LibreOffice、.NET 8 OpenXML SDK |
| 代码执行 | Script Runner Sidecar（Python / Node 20 / Bash / .NET runtime / Chromium） |
| 监控 | Prometheus、Grafana、Jaeger、OpenTelemetry |
| 可选记忆 | mem0、Milvus、Neo4j |
| 容器化 | Docker、Docker Compose |

## 目录结构

仓库里有几块内容最关键，理解这些目录基本就能理解项目全貌。

```text
.
├── app.py
├── Dockerfile                       # 多阶段镜像：backend / script-runner
├── docker-compose.yml
├── Makefile
├── alembic.ini
├── requirements.txt
├── requirements-script-runner.txt   # sidecar 镜像依赖
├── docs/
├── scripts/                         # DB 迁移与内容迁移脚本
├── src/
│   ├── backend/
│   │   ├── api/                     # FastAPI 应用与路由
│   │   │   ├── app.py               # FastAPI 入口、中间件、路由注册
│   │   │   ├── deps.py              # 依赖注入（认证、admin/config 授权）
│   │   │   ├── health.py            # 健康检查端点
│   │   │   ├── schemas.py           # 请求/响应 Pydantic 模型
│   │   │   ├── middleware/          # CORS、错误处理、日志中间件
│   │   │   └── routes/v1/           # 全部 /v1/* 端点（30+ 路由文件）
│   │   ├── core/                    # 业务逻辑层（模块化拆分为 9 个子模块）
│   │   │   ├── auth/                # 认证后端、会话管理、SSO
│   │   │   ├── chat/                # 聊天智能体、上下文、会话
│   │   │   ├── config/              # 应用配置、模型配置、MCP/Skills 服务
│   │   │   ├── content/             # 内容块、文件解析、知识库处理、Artifact
│   │   │   │                         # 摘要缓存与按需读取
│   │   │   ├── db/                  # 数据库引擎、ORM 模型、数据访问层
│   │   │   ├── infra/               # 异常、日志、响应、追踪、指标、限流、Redis
│   │   │   ├── llm/                 # Agent 工厂、Hooks、MCP 管理、工具注册、
│   │   │   │                         # 分类器、历史摘要、子智能体工具等
│   │   │   ├── services/            # 高层业务服务：user / chat / catalog / kb /
│   │   │   │                         # artifact / plan / automation / user-agent
│   │   │   └── storage/             # 存储抽象 + local / S3 / OSS 三种实现
│   │   ├── routing/                 # 工作流编排、流式输出、Plan 模式、
│   │   │                             # 定时任务调度器、追问生成器
│   │   ├── prompts/                 # 系统提示词（default / v1 / v2 / v3 / v4
│   │   │                             # / code_exec 多版本）
│   │   ├── configs/                 # MCP、监控、能力目录、显示名映射
│   │   ├── mcp_servers/             # 各个 MCP stdio/http server（7 个工具服务）
│   │   ├── agent_skills/            # 本地技能包 + 脚本 Runner 客户端
│   │   │   └── skills/              # 15 个内置技能（含 minimax-* 全家桶）
│   │   ├── artifacts/               # 生成物注册与下载辅助
│   │   ├── script_runner_service/   # sidecar HTTP 服务（由 script-runner 镜像启动）
│   │   ├── scripts/                 # 启动、导入导出、初始化脚本
│   │   └── tests/                   # 后端测试与 selftest
│   └── frontend/
│       └── src/
│           ├── App.tsx              # 主聊天界面
│           ├── AdminApp.tsx         # /admin 后台内容运营平台
│           ├── components/          # 17+ 组 UI 组件：
│           │                         # chat/catalog/sidebar/citation/tool/
│           │                         # file/kb/settings/docs/admin/common/
│           │                         # agent/canvas/code-artifact/config/
│           │                         # lab/myspace/share
│           ├── hooks/                # useChatActions/useChatInit/
│           │                         # useStreaming/usePlanMode
│           ├── stores/               # Zustand 状态管理（12 个 store）
│           ├── utils/                # citations/markdown/fileParser/
│           │                         # codeExecParser/highlight/history...
│           └── styles/               # CSS 模块（15 个）
└── logs/
```

## 核心能力

下面这些能力是当前代码里已经落地的部分，也是 README 最值得保留的事实。

### 对话与会话

聊天请求必须携带 `chat_id`，服务端会自动创建或校验该会话，并将用户消息、
助手回复、来源、附件、反馈等信息持久化到数据库。Hook 体系会在每轮对话
自动向系统上下文注入本会话历史文件清单（用户上传 + AI 生成），仅包含
文件名、来源与摘要；Agent 需要全文时通过 `read_artifact` 工具按字符
分页拉取。

- 流式对话：`POST /v1/chats/stream`
- 非流式对话：`POST /v1/chats/send`
- 会话管理：`GET/POST/PATCH/DELETE /v1/chats`
- 消息列表：`GET /v1/chats/{chat_id}/messages`
- 消息反馈：`POST /v1/chats/messages/{message_id}/feedback`
- 追问推荐：`GET /v1/chats/{chat_id}/followups`（异步生成，前端轮询拉取）
- 会话分享：`POST /v1/chat-shares`、`GET /v1/chat-shares/{token}`

### 计划模式（Plan Mode）

对复杂任务，前端可以切到 Plan 模式：AI 先输出结构化计划，用户确认后
逐步执行，每个 step 可绑定特定 Skill / Agent / MCP 工具。计划、步骤
以及执行历史都持久化到 `plans` / `plan_steps` 表。

- 生成计划：`POST /v1/plans/generate`（SSE 流式）
- 执行计划：`POST /v1/plans/{plan_id}/execute`（SSE 流式）
- 计划 CRUD：`GET/PATCH/DELETE /v1/plans`

### 定时自动化任务（Automation）

基于 croniter 的调度器 `routing/automation_scheduler.py` 会在后台轮询
`scheduled_tasks` 表，按 cron 表达式触发 Prompt 任务或 Plan 任务，
执行结果写入 `scheduled_task_runs`，并通过 MySpace 通知面板推送给用户。

- CRUD：`GET/POST/PATCH/DELETE /v1/automations`
- 启停：`POST /v1/automations/{id}/toggle`
- 立即执行：`POST /v1/automations/{id}/run`
- 历史：`GET /v1/automations/{id}/runs`

### 能力中心与路由

前端能力面板和后端运行时配置都由 `src/backend/configs/catalog.json`
驱动。系统内置多种 skills、若干 MCP 工具服务、用户自建的子智能体，
以及内置 Plan 模式子智能体。用户可以通过 `/v1/catalog` 读取能力
目录，并通过 `PATCH /v1/catalog/{kind}/{id}` 做个人级开关。

当前内置技能（default catalog 默认 enabled）：

- `capability-guide-brief` — 能力清单速答
- `quick-material-analysis` — 材料极速分析
- `process-guidance` — 办事流程引导
- `industry-chain-target-recommendation` — 产业链招商目标推荐
- `industry-chain-structure-analysis` — 产业链结构解析
- `enterprise-profile-query` — 企业画像一键查询
- `report-summary-generation` — 报告总结生成
- `policy-matching-diagnosis` — 政策匹配诊断
- `policy-search-interpretation` — 政策检索与解读
- `material-comparison` — 材料智能对比
- `economic-indicator-query` — 运行指标数据查询

随仓库发布、可按环境启用的增强技能包：

- `minimax-docx` — 基于 .NET 8 OpenXML SDK 的结构化 Word 文档生成 / 编辑 /
  模板套用 pipeline（CREATE / FILL-EDIT / FORMAT-APPLY 三种）
- `minimax-xlsx` — OOXML 低阶 .xlsx 创建 / 编辑 CLI（含 LibreOffice 公式重算）
- `minimax-pdf` — Playwright 渲染的封面 / 正文 / 合并 PDF pipeline
- `pptx-generator` — 基于 pptxgenjs 的结构化 PPT 生成脚本

子智能体分两类：系统内置子智能体（Plan 模式执行器、报告写作助手等）
与用户自建子智能体（`/v1/agents`），后者可在对话框中通过 `@` 提及直接调用。

### MCP 工具

MCP 工具通过 `src/backend/configs/mcp_config.py` 注册，大多数以 stdio
子进程运行，知识库检索使用 streamable_http 走独立进程。默认注册的
MCP 服务器如下。

| MCP ID | 作用 |
| --- | --- |
| `query_database` | 查询结构化业务数据 |
| `retrieve_dataset_content` | 检索本地/Dify 知识库（含混合检索与重排） |
| `internet_search` | 互联网搜索（Tavily），支持中文站点偏好 |
| `ai_chain_information_mcp` | 产业链分析、产业资讯、AI 热点、企业画像 |
| `generate_chart_tool` | 生成柱状图 / 折线图 / 饼图等可视化图表 |
| `report_export_mcp` | 一键将 Markdown 导出为 Word / Excel（轻量场景） |
| `web_fetch` | 抓取指定 URL 的网页正文（text / markdown / html） |

系统还为 Agent 额外注入若干内置工具：`read_artifact`（分页读取历史文件
全文）、`run_skill_script`（通过 sidecar 运行技能脚本）、`list_skills`
（查看可用技能）等，具体在 `core/llm/tool.py` 中注册。

### 知识库与文件能力

项目同时支持知识库空间管理和通用文件处理能力。知识库接口位于
`/v1/catalog/kb`，既支持本地知识库空间（Milvus 向量检索），也支持在
配置了 Dify 后直接把 Dify dataset 当作知识库使用。

- `POST /v1/catalog/kb`：创建知识库空间
- `POST /v1/catalog/kb/{kb_id}/documents`：上传知识库文档（后台向量化）
- `GET /v1/catalog/kb/{kb_id}/documents`：查看文档列表
- `GET /v1/catalog/kb/{kb_id}/documents/{document_id}`：查看文档详情
- `POST /v1/file/upload`：上传用户附件并持久化
- `POST /v1/file/parse`：解析上传文件内容（调用 FILE_PARSER 服务）
- `GET /files/{file_id}`：下载生成物或上传附件（local/S3/OSS 统一入口）

### 我的空间与 Canvas

- `/v1/artifacts`：按用户筛选文件、图片、收藏会话与任务通知
- `/v1/artifacts/favorites`：收藏的会话列表
- `DELETE /v1/artifacts/{id}`：软删除资源
- Canvas 面板（`components/canvas/`）内嵌 Univer 电子表格，可直接编辑
  生成的 .xlsx 并原地覆盖保存到 artifact 存储

### 代码执行（Lab）

Lab 面板允许用户触发后端通过 Script Runner Sidecar 隔离执行 Python /
JavaScript / Bash 代码。脚本的输入输出都挂载在内存 tmpfs，镜像带
`no-new-privileges`、`read_only`、`pids_limit`、`mem_limit`、`cpus` 等
安全约束。

- 执行代码：`POST /v1/code/execute`
- 执行结果以 artifact 形式持久化，可在我的空间和对话引用面板中再次打开

### 认证、会话与用户配置

认证逻辑集中在 `src/backend/core/auth/`，当前支持三种模式：

- `AUTH_MODE=mock`：本地开发默认模式
- `AUTH_MODE=remote`：沿用 Bearer Token + 远端用户中心
- `AUTH_MODE=session`：基于 Redis 的 Cookie 会话模式

当 `SSO_MOCK_ENABLED=true` 时，还会额外注册 Mock SSO 路由：

- `GET /mock-sso/login`
- `POST /mock-sso/ticket/exchange`

正式的会话相关接口位于 `/v1/auth`：

- `POST /v1/auth/ticket/exchange`
- `GET /v1/auth/session/check`
- `POST /v1/auth/logout`

### 后台平台（/admin 与 /config）

前端将后台拆分为两套独立页面：

- **`/admin` — 内容运营平台**：依赖 `ADMIN_TOKEN`，通过
  `/v1/content/docs` 接口读写"版本说明"与"能力中心"展示内容，还负责
  Skill / Prompt / MCP / 子智能体元信息的编辑。
- **`/config` — 系统配置平台**：依赖 `CONFIG_TOKEN`，提供模型提供商、
  定价、服务配置、使用日志、Token 账单、管理员会话历史等高权限视图。

仓库里还提供了一对导入导出脚本：

- `src/backend/scripts/export_content.py`
- `src/backend/scripts/import_content.py`

### 记忆系统（mem0，可选）

记忆相关逻辑封装在 `src/backend/core/llm/memory.py` 与
`src/backend/routing/memory_integration.py`。只有在同时满足基础设施可用
且环境变量开启时，mem0 相关接口才真正生效。

- `GET /v1/memories`
- `GET /v1/memories/settings`
- `PATCH /v1/memories/settings`
- `DELETE /v1/memories`
- `DELETE /v1/memories/{memory_id}`

## 快速开始

如果你只是想先把项目跑起来，优先使用 Docker Compose。当前编排文件已经
包含后端、前端、PostgreSQL、Redis、脚本执行 sidecar、Prometheus、
Grafana、Jaeger 以及告警相关服务。

### 前置依赖

本地开发和构建至少需要以下环境。

- Python 3.11 或更高版本
- Node.js 20 或更高版本
- Docker Engine 与 Docker Compose v2

### 使用 Docker Compose 启动

下面是最直接的启动方式。后端容器启动时会自动执行
`alembic upgrade head`。

1. 复制环境变量模板。

   ```bash
   cp .env.example .env
   ```

2. 根据你的环境至少补齐以下配置。
   
   ```bash
   MODEL_URL=http://your-model-host:3001/v1
   API_KEY=your-api-key
   BASE_MODEL_NAME=deepseek-chat
   AUTH_MODE=mock
   BACKEND_PORT=3001
   FRONTEND_PORT=3002
   ```

3. 启动服务。

   ```bash
   docker-compose up -d --build

   # 使用不同 .env 文件
   docker compose --env-file .env.dev up -d --build
   docker compose --env-file .env.prod up -d --build
   ```

4. 检查健康状态。

   ```bash
   curl http://localhost:3001/health
   curl http://localhost:3001/ready
   ```

默认访问地址如下。

| 服务 | 地址 |
| --- | --- |
| 前端 | `http://localhost:3001` |
| 内容运营平台 | `http://localhost:3001/admin` |
| 系统配置平台 | `http://localhost:3001/config` |
| 后端 API | `http://localhost:3002` |
| Swagger | `http://localhost:3001/docs` |
| ReDoc | `http://localhost:3001/redoc` |
| Script Runner | `http://jingxin-script-runner:8900`（仅容器内访问） |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3003` |
| Jaeger | `http://localhost:16686` |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6380` |

如果你要联调 session 模式，可以把 `SSO_MOCK_ENABLED=true` 打开，然后
访问 `http://localhost:3001/mock-sso/login?redirect=/`。

### 启用 mem0 基础设施

mem0 相关容器在 Compose 中挂在 `mem0` profile 下。只有在需要记忆能力时
再启动即可。

1. 启动可选基础设施。

   ```bash
   docker-compose --profile mem0 up -d etcd minio milvus neo4j
   ```

2. 在 `.env` 中启用相关开关。

   ```bash
   MEM0_ENABLED=true
   MEM0_GRAPH_ENABLED=true
   ```

### 启用脚本执行 sidecar

Script Runner 默认参与常规 `docker-compose up`，但只有当
`SKILL_SCRIPT_ENABLED=true` 时，后端才会把脚本调用转发给 sidecar。
sidecar 镜像内置 Python / Node 20 / .NET 8 runtime / Chromium /
LibreOffice / pandoc，足以支撑 minimax-* 全家桶技能包。

```bash
SKILL_SCRIPT_ENABLED=true
SKILL_SCRIPT_RUNNER_URL=http://jingxin-script-runner:8900
SKILL_SCRIPT_TIMEOUT=30
SKILL_SCRIPT_MAX_TIMEOUT=120
```

### 本地开发

如果你不想使用 Docker 跑前后端，可以分开本地启动。最小可用路径通常是
后端直连本地或远程 PostgreSQL，前端走 Vite 开发服务器。

1. 安装后端依赖。

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 准备环境变量。

   ```bash
   cp .env.example .env
   ```

3. 应用数据库迁移。

   ```bash
   alembic upgrade head
   ```

4. 启动后端。

   ```bash
   make dev
   ```

5. 在另一个终端启动前端。

   ```bash
   cd src/frontend
   npm install
   npm run dev
   ```

本地前端默认使用 `VITE_API_BASE_URL`，如果没有显式配置，则回落到 `/api`。
在 Docker 场景下，前端 Nginx 会把 `/api` 代理到后端容器。使用 Vite 本地
开发时，仓库当前没有内置 dev proxy，因此建议在 `.env` 中显式设置
`VITE_API_BASE_URL=http://localhost:3001`。

## 常用命令

仓库根目录的 `Makefile` 已经封装了大部分常见操作。下面这些命令最常用。

```bash
make install         # 安装运行时依赖
make dev             # 本地启动后端开发服务器
make test            # 运行后端测试并生成 coverage
make selftest        # 快速自测
make lint            # black/isort 检查
make format          # black/isort 格式化
make type-check      # mypy 检查
make security-scan   # bandit + safety
make migrate         # alembic upgrade head
make build           # 构建 compose 镜像
make up              # 启动容器
make down            # 停止容器
make logs-backend    # 查看后端日志
make health-check    # 检查 /health 和 /ready
```

## 关键配置项

`.env.example` 已经覆盖了项目的大多数配置项。下面是最常需要关心的变量组。

### 模型与摘要

模型服务配置直接决定聊天、摘要和部分工具的可用性。

```bash
MODEL_URL=http://your-model-host:3001/v1
API_KEY=your-api-key
BASE_MODEL_NAME=deepseek-chat
QWEN_MODEL_NAME=qwen3_80b
SUMMARIZE_MODEL_NAME=qwen3_80b
ENABLE_SUMMARY=true
SUMMARY_MAX_ROUNDS=3
```

### 数据库、存储与会话

数据库和对象存储分别负责业务数据持久化和附件落盘，session 模式还依赖
Redis。

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/jingxin
REDIS_URL=redis://localhost:6379/0
STORAGE_TYPE=local            # local / s3 / oss
STORAGE_PATH=./storage
AUTH_MODE=mock
SESSION_STORE=redis
ADMIN_TOKEN=your-secret-admin-token
CONFIG_TOKEN=your-secret-config-token
```

### 外部集成

MCP 工具依赖的外部服务基本都从环境变量注入。

```bash
QUERY_DATABASE_URL=http://your-database-api-host:6200
KNOWLEDGE_BASE=dify           # 留空 → 本地知识库；dify → Dify 数据集
DIFY_URL=http://your-dify-host:3001/v1
DIFY_API_KEY=your-dify-api-key
DIFY_ALLOWED_DATASET_IDS=dataset-id-1,dataset-id-2
TAVILY_API_KEY=your-tavily-api-key
INDUSTRY_URL=https://your-industry-api.com/api
INDUSTRY_AUTH_TOKEN=your-industry-auth-token
FILE_PARSER_API_URL=http://your-parser-service
```

### 脚本执行 sidecar

只有当 `SKILL_SCRIPT_ENABLED=true` 时，Skills 里定义的脚本（包括
minimax-* 全家桶）才会真正被转发到 sidecar 执行。

```bash
SKILL_SCRIPT_ENABLED=false
SKILL_SCRIPT_RUNNER_URL=http://jingxin-script-runner:8900
SKILL_SCRIPT_TIMEOUT=30
SKILL_SCRIPT_MAX_TIMEOUT=120
SKILL_SCRIPT_MAX_MEMORY=256
```

### mem0

如果不需要长期记忆，不要开启这一组配置。

```bash
MEM0_ENABLED=false
MEM0_GRAPH_ENABLED=false
MEMORY_MODEL_URL=http://your-model-host:3001/v1
MEMORY_API_KEY=your-api-key
MEM0_EMBED_URL=http://your-embedding-host:3001/v1
MEM0_EMBED_API_KEY=your-embedding-key
MILVUS_URL=http://milvus:19530
NEO4J_URL=bolt://neo4j:7687
```

更完整的说明见 `docs/environment-config.md`。

## API 入口

如果你要接前端或做联调，优先关注下面这些入口。

| 分类 | 入口 |
| --- | --- |
| 基础状态 | `/`、`/health`、`/ready`、`/live` |
| 对话 | `/v1/chats/send`、`/v1/chats/stream` |
| 会话管理 | `/v1/chats`、`/v1/chats/{chat_id}/messages` |
| 会话分享 | `/v1/chat-shares` |
| 能力目录 | `/v1/catalog`、`/v1/catalog/{kind}/{id}` |
| 知识库 | `/v1/catalog/kb` |
| 文件 | `/v1/file/upload`、`/v1/file/parse`、`/files/{file_id}` |
| 我的空间 / Artifact | `/v1/artifacts`、`/v1/artifacts/favorites` |
| 计划模式 | `/v1/plans`、`/v1/plans/{plan_id}/execute` |
| 自动化任务 | `/v1/automations`、`/v1/automations/{id}/run` |
| 代码执行 | `/v1/code/execute` |
| 子智能体 | `/v1/agents` |
| 用户与偏好 | `/v1/me`、`/v1/users/{user_id}/preferences` |
| 认证与 SSO | `/v1/auth/*`、`/mock-sso/*` |
| 内容管理 | `/v1/content/docs` |
| 管理员（Admin） | `/v1/admin/skills`、`/v1/admin/prompts`、`/v1/admin/mcp-servers`、`/v1/admin/agents` |
| 管理员（Config） | `/v1/admin/billing`、`/v1/admin/usage-logs`、`/v1/admin/chat-history`、`/v1/service-configs`、`/v1/models` |
| 记忆 | `/v1/memories` |
| 工具名称配置 | `/v1/config/tool-names` |
| 指标 | `/metrics`（仅内网可访问） |

下面是一个最小流式调用示例。

```bash
curl -N -X POST http://localhost:3001/v1/chats/stream \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": "demo-001",
    "message": "请总结宁波人工智能产业现状",
    "model_name": "qwen"
  }'
```

### 数据导入与导出

仓库提供了一对脚本用于在不同环境之间迁移前端展示数据（版本说明、能力中心）。
导出支持 API（推荐）和直连数据库两种方式。

**导出**

```bash
# 从运行中的后端导出全部数据
python scripts/export_content.py --api-url http://localhost:3000/api

# 直接从数据库导出
python scripts/export_content.py --database-url postgresql://user:pass@host/db

# 仅导出版本说明 / 仅导出能力中心
python scripts/export_content.py --api-url http://localhost:3000/api --only docs
python scripts/export_content.py --api-url http://localhost:3000/api --only catalog
```

输出文件保存在 `scripts/exported/` 目录：
- `docs_snapshot_<timestamp>.json` — 版本说明（content_blocks）
- `catalog_snapshot_<timestamp>.json` — 能力中心（catalog.json + catalog_overrides）

**导入**

```bash
# 导入版本说明到生产环境
python scripts/import_content.py --api-url http://<PROD_HOST>/api \
    --docs scripts/exported/docs_snapshot_20260310_143000.json

# 同时导入版本说明和能力中心
python scripts/import_content.py --api-url http://<PROD_HOST>/api \
    --docs docs_snapshot.json --catalog catalog_snapshot.json

# 试运行（不实际写入）
python scripts/import_content.py --api-url http://<PROD_HOST>/api \
    --docs docs_snapshot.json --dry-run

# 不覆盖已有数据
python scripts/import_content.py --api-url http://<PROD_HOST>/api \
    --docs docs_snapshot.json --no-overwrite
```

## 文档

仓库里还有一批比 README 更细的专题文档，适合在部署、排障或二次开发时查阅。

- `docs/README.md` — 文档索引
- `docs/architecture-current.md` — 系统架构概览
- `docs/DEPLOYMENT_GUIDE.md` — 部署指南
- `docs/PRE_DEPLOYMENT_CHECKLIST.md` — 上线前检查清单
- `docs/environment-config.md` — 环境变量完整说明
- `docs/storage-guide.md` — 存储配置指南
- `docs/observability-guide.md` — 监控与可观测性
- `docs/runbook.md` — 运维手册
- `docs/security-checklist.md` — 安全检查清单
- `docs/capability-guide.md` — 能力中心使用说明
- `docs/citation.md` — 引用系统实现文档
- `docs/mem0-integration-plan.md` — mem0 记忆系统设计
- `docs/private-kb-integration-plan.md` — 私有知识库集成
- `docs/code-sandbox-proposal.md` — 代码沙箱（sidecar）设计
- `docs/scheduled-task-proposal.md` — 定时任务设计
- `docs/claude-code-subagent-architecture.md` — 子智能体架构
- `docs/custom-subagent-proposal.md` — 用户自建子智能体设计
- `docs/agentscope-migration-summary.md` — LangChain→AgentScope 迁移记录
- `docs/report-rendering-design-spec.md` — 报告渲染规范
- `docs/memory-context-management-proposal.md` — 跨轮上下文管理设计

## Next steps

如果你接下来要继续推进这个项目，建议按下面顺序读代码和文档。

1. 先看 `src/backend/api/app.py` 和 `src/backend/api/routes/v1/`，理解接口面。
2. 再看 `src/backend/routing/workflow.py`、`src/backend/core/llm/agent_factory.py`
   和 `src/backend/configs/mcp_config.py`，理解智能体与工具装配逻辑。
3. 看 `src/backend/routing/subagents/plan_mode.py` 与
   `src/backend/routing/automation_scheduler.py`，理解 Plan 模式与定时
   任务的编排方式。
4. 看 `src/backend/core/services/` 理解业务逻辑层的分层设计。
5. 前端联调时优先看 `src/frontend/src/App.tsx`、`src/frontend/src/api.ts`、
   `src/frontend/src/stores/` 和 `src/frontend/src/hooks/`。
6. 部署前阅读 `docs/DEPLOYMENT_GUIDE.md`、`docs/runbook.md` 和
   `docs/security-checklist.md`。
