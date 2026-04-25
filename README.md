# Jingxin-Agent

面向业务分析与报告生成场景的全栈 AI 智能体平台，基于 FastAPI + AgentScope + React 构建。

## 功能概览

**对话与会话**
- 流式/非流式多轮对话，支持会话持久化与文件摘要跨轮注入
- 会话分享（3 天 / 15 天 / 永久链接）
- 子智能体（User Agent）：用户自建带独立系统提示词的智能体，通过 `@提及` 调用

**任务执行**
- 计划模式（Plan Mode）：AI 拆解任务 → 用户确认 → 分步执行 → 结果聚合
- 定时自动化（Automation）：croniter 驱动的 Prompt / Plan 周期调度

**工具与能力**
- 能力中心：按用户维度启用/禁用 Skills、Agents、MCP 工具、知识库
- 内置 MCP 工具：结构化数据查询、知识库检索、联网搜索、图表生成、文档导出（Word/Excel/PPT）、网页抓取、代码执行
- 代码执行 Lab：隔离 sidecar 运行 Python / JS / Bash，结果以 Artifact 形式持久化

**知识库**
- 支持本地（Milvus 向量检索）与 Dify 数据集两种模式
- 文件上传、解析、向量索引、混合检索与重排

**文件与资源**
- 文件上传/解析、生成文件下载
- Canvas 面板内嵌 Univer 电子表格在线编辑
- MySpace：统一管理上传文件、生成文件、收藏会话与任务通知

**平台管理**
- `/admin` 内容运营平台：Skill/Prompt/MCP/Agent 元数据管理
- `/config` 系统配置平台：模型、定价、用量、账单管理
- `mock` / `remote` / `session` 三种认证模式

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 API | FastAPI、Uvicorn、Pydantic v2 |
| 智能体编排 | AgentScope 1.0+（ReActAgent、MCP Toolkit） |
| 数据层 | PostgreSQL、SQLAlchemy、Alembic、Redis |
| 工具协议 | MCP（stdio + streamable_http） |
| 任务调度 | croniter |
| 前端 | React 19、Vite 7、TypeScript、Ant Design、Zustand |
| 存储 | 本地 / S3 / 阿里云 OSS |
| 可观测性 | Prometheus、Grafana、Jaeger |
| 长期记忆（可选） | mem0、Milvus、Neo4j |

## 快速启动

**Docker Compose（推荐）**

```bash
cp .env.example .env   # 填写模型 API Key 等必要配置
docker-compose up -d --build
```

后端默认端口 `3001`，前端默认端口 `3002`。

**本地开发**

```bash
# 后端
make install
make dev

# 前端
cd src/frontend
npm install
npm run dev
```

主要 Makefile 命令：

| 命令 | 说明 |
| --- | --- |
| `make install` | 安装 Python 依赖 |
| `make dev` | 启动后端开发服务器 |
| `make up` | Docker Compose 启动 |
| `make migrate` | 运行数据库迁移 |
| `make test` | 运行测试 |

## 项目结构

```
src/
├── backend/
│   ├── api/          # FastAPI 路由与依赖注入
│   ├── core/         # 业务逻辑（chat、llm、services、db、storage 等）
│   ├── routing/      # 工作流编排与自动化调度
│   ├── mcp_servers/  # MCP 工具服务（8 个）
│   ├── agent_skills/ # 内置技能包
│   └── prompts/      # 系统提示词
└── frontend/
    └── src/
        ├── components/ # UI 组件
        └── stores/     # Zustand 状态管理
```

代码导航建议从 `src/backend/api/app.py` → `routing/workflow.py` → `core/services/` 展开。
