# Jingxin-Agent 文档索引

## 项目结构

```
src/
├── backend/    ← 后端代码（Python/FastAPI，core/ 按 9 个子模块组织）
└── frontend/   ← 前端代码（React/Vite，组件化 + Zustand 状态管理）
```

详见 [architecture-current.md](./architecture-current.md)

---

## 文档目录

### 部署与运维
| 文档 | 说明 |
|------|------|
| [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) | 完整部署指南（本地/Docker/Kubernetes） |
| [PRE_DEPLOYMENT_CHECKLIST.md](./PRE_DEPLOYMENT_CHECKLIST.md) | 部署前检查清单 |
| [runbook.md](./runbook.md) | 故障处理运维手册 |
| [environment-config.md](./environment-config.md) | 各环境配置说明（dev/staging/prod） |

### 架构与设计
| 文档 | 说明 |
|------|------|
| [architecture-current.md](./architecture-current.md) | 当前架构说明（入口/路由/prompt/tools） |
| [api-contract.yaml](./api-contract.yaml) | API 接口规范（OpenAPI） |
| [database-schema.sql](./database-schema.sql) | 数据库表结构参考 |
| [error-codes.md](./error-codes.md) | 错误码说明 |
| [capability-guide.md](./capability-guide.md) | 能力中心使用说明 |
| [citation.md](./citation.md) | 引用系统实现文档 |
| [agentscope-migration-summary.md](./agentscope-migration-summary.md) | AgentScope 迁移总结 |

### 存储与安全
| 文档 | 说明 |
|------|------|
| [storage-guide.md](./storage-guide.md) | 对象存储使用指南（本地/S3/OSS） |
| [secrets-management.md](./secrets-management.md) | 密钥管理规范 |
| [security-checklist.md](./security-checklist.md) | 安全检查清单 |
| [compliance-guide.md](./compliance-guide.md) | 合规指南 |

### 监控
| 文档 | 说明 |
|------|------|
| [observability-guide.md](./observability-guide.md) | 可观测性配置（Prometheus/Grafana/Jaeger） |

### 功能设计与集成
| 文档 | 说明 |
|------|------|
| [mem0-integration-plan.md](./mem0-integration-plan.md) | mem0 记忆系统设计与集成 |
| [private-kb-integration-plan.md](./private-kb-integration-plan.md) | 私有知识库（Milvus）集成 |

### 前端集成
| 文档 | 说明 |
|------|------|
| [frontend-integration/migration-guide.md](./frontend-integration/migration-guide.md) | 前端对接迁移指南 |
| [frontend-integration/api-client.ts](./frontend-integration/api-client.ts) | API 客户端示例代码 |
| [frontend-integration/api-types.ts](./frontend-integration/api-types.ts) | API 类型定义 |
| [frontend-integration/react-hooks-examples.tsx](./frontend-integration/react-hooks-examples.tsx) | React Hooks 使用示例 |

### 版本管理与发布
| 文档 | 说明 |
|------|------|
| [versioning-guide.md](./versioning-guide.md) | 版本管理规范 |
