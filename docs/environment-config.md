# 环境配置清单

## 环境分层定义

| 环境 | 用途 | 访问控制 | 数据持久化 |
|------|------|----------|------------|
| `dev` | 本地开发 | 无限制 | 临时/可清除 |
| `staging` | 预发布测试 | 团队内部 | 保留7天 |
| `prod` | 生产环境 | 外部用户 | 永久保留 |

## 配置差异矩阵

### 数据库配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 数据库类型 | SQLite/PostgreSQL | PostgreSQL | PostgreSQL (HA) |
| 连接池大小 | 5 | 20 | 50 |
| 连接超时 | 30s | 10s | 5s |
| 查询超时 | 无限制 | 30s | 10s |
| SSL 模式 | disable | prefer | require |
| 备份策略 | 无 | 每日 | 每小时 + WAL |

**环境变量**:
```bash
# dev
DATABASE_URL=sqlite:///./dev.db
# 或
DATABASE_URL=postgresql://user:pass@localhost:5432/jingxin_dev

# staging
DATABASE_URL=postgresql://user:pass@staging-db.internal:5432/jingxin_staging?sslmode=prefer

# prod
DATABASE_URL=postgresql://user:pass@prod-db-cluster.internal:5432/jingxin_prod?sslmode=require
```

### 对象存储配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 存储类型 | 本地文件系统 | S3 Compatible | S3 Compatible |
| Bucket 名称 | - | jingxin-staging | jingxin-prod |
| 区域 | - | us-east-1 | us-east-1 |
| 访问方式 | 直接路径 | 签名 URL (1h) | 签名 URL (15m) |
| CDN | 否 | 否 | 是 (CloudFront) |
| 生命周期 | 无 | 30天归档 | 90天归档, 1年删除 |

**环境变量**:
```bash
# dev - 本地文件存储
ENVIRONMENT=dev
STORAGE_TYPE=local
STORAGE_PATH=./storage

# staging - S3 存储
ENVIRONMENT=staging
STORAGE_TYPE=s3
S3_ENDPOINT=https://s3.amazonaws.com  # 可选，S3兼容服务需要
S3_BUCKET=jingxin-staging
S3_REGION=us-east-1
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=***
S3_PRESIGNED_URL_EXPIRY=3600  # 1小时

# prod - S3 存储 + CDN
ENVIRONMENT=prod
STORAGE_TYPE=s3
S3_ENDPOINT=https://s3.amazonaws.com
S3_BUCKET=jingxin-prod
S3_REGION=us-east-1
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=***
S3_PRESIGNED_URL_EXPIRY=900  # 15分钟
S3_CDN_DOMAIN=cdn.jingxin.example.com  # CDN 域名，用于加速访问
```

**存储键命名规范**:

存储文件使用标准化的目录层级结构：

```
{environment}/{category}/{user_id}/[{chat_id}/]{timestamp}_{filename}

示例：
- prod/artifacts/user_001/chat_123/20260213120000_report.pdf
- prod/kb_documents/user_002/20260213120000_manual.pdf
- staging/uploads/user_003/20260213120000_data.csv
```

这种结构的优势：
1. 环境隔离：不同环境的文件物理分离
2. 类别组织：按文件用途分类管理
3. 用户隔离：支持基于用户的访问控制
4. 时间排序：时间戳前缀便于排序和清理
5. 生命周期策略：支持按前缀配置自动归档和删除

### 用户中心配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 模式 | Mock | 测试用户中心 | 生产用户中心 |
| API 端点 | - | https://auth-staging.internal/api | https://auth.internal/api |
| Token 验证 | 跳过 | 每次验证 | 每次验证 + 缓存5分钟 |
| 超时时间 | - | 5s | 3s |
| 重试次数 | - | 3 | 2 |
| 降级策略 | - | 返回错误 | 返回错误 + 告警 |

**环境变量**:
```bash
# dev
AUTH_MODE=mock
AUTH_MOCK_USER_ID=dev_user_001
AUTH_MOCK_USERNAME=Developer

# staging
AUTH_MODE=remote
AUTH_API_URL=https://auth-staging.internal/api
AUTH_API_TIMEOUT=5
AUTH_RETRY_COUNT=3
AUTH_CACHE_TTL=300

# prod
AUTH_MODE=remote
AUTH_API_URL=https://auth.internal/api
AUTH_API_TIMEOUT=3
AUTH_RETRY_COUNT=2
AUTH_CACHE_TTL=300
```

### 日志配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 日志级别 | DEBUG | INFO | WARNING |
| 日志格式 | 彩色文本 | JSON | JSON |
| 输出目标 | 控制台 | 控制台 + 文件 | 控制台 + 文件 + 远程 |
| 文件轮转 | - | 每日 | 每小时 |
| 保留时间 | - | 7天 | 30天 |
| trace_id | 可选 | 必须 | 必须 |

**环境变量**:
```bash
# dev
LOG_LEVEL=DEBUG
LOG_FORMAT=text
LOG_OUTPUT=console

# staging
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_OUTPUT=console,file
LOG_FILE_PATH=/var/log/jingxin/app.log
LOG_ROTATION=daily
LOG_RETENTION_DAYS=7

# prod
LOG_LEVEL=WARNING
LOG_FORMAT=json
LOG_OUTPUT=console,file,remote
LOG_FILE_PATH=/var/log/jingxin/app.log
LOG_ROTATION=hourly
LOG_RETENTION_DAYS=30
LOG_REMOTE_ENDPOINT=https://log-collector.internal/ingest
```

### CORS 配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 允许来源 | `*` | 具体域名列表 | 具体域名列表 |
| 允许凭证 | true | true | true |
| 允许方法 | ALL | GET,POST,PUT,PATCH,DELETE | GET,POST,PUT,PATCH,DELETE |
| 允许头部 | ALL | 标准头 + Authorization | 标准头 + Authorization |
| 暴露头部 | ALL | Content-Range, X-Total-Count | Content-Range, X-Total-Count |
| 最大年龄 | 3600 | 3600 | 7200 |

**环境变量**:
```bash
# dev
CORS_ORIGINS=*
CORS_ALLOW_CREDENTIALS=true

# staging
CORS_ORIGINS=http://localhost:3000,https://staging.jingxin.example.com
CORS_ALLOW_CREDENTIALS=true

# prod
CORS_ORIGINS=https://jingxin.example.com,https://www.jingxin.example.com
CORS_ALLOW_CREDENTIALS=true
```

### 限流配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 全局限流 | 无 | 1000 req/min | 500 req/min |
| 用户限流 | 无 | 100 req/min | 50 req/min |
| 聊天限流 | 无 | 20 req/min | 10 req/min |
| 上传限流 | 无 | 10 req/min | 5 req/min |
| 惩罚时间 | - | 60s | 300s |

**环境变量**:
```bash
# dev
RATE_LIMIT_ENABLED=false

# staging
RATE_LIMIT_ENABLED=true
RATE_LIMIT_GLOBAL=1000/minute
RATE_LIMIT_PER_USER=100/minute
RATE_LIMIT_CHAT=20/minute
RATE_LIMIT_UPLOAD=10/minute

# prod
RATE_LIMIT_ENABLED=true
RATE_LIMIT_GLOBAL=500/minute
RATE_LIMIT_PER_USER=50/minute
RATE_LIMIT_CHAT=10/minute
RATE_LIMIT_UPLOAD=5/minute
RATE_LIMIT_BAN_DURATION=300
```

### 模型服务配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 默认模型 | qwen | qwen | gpt-4 |
| 超时时间 | 300s | 120s | 60s |
| 重试次数 | 1 | 2 | 3 |
| 降级模型 | 无 | qwen | gpt-3.5-turbo |
| 并发限制 | 无 | 20 | 50 |

**环境变量**:
```bash
# dev
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=qwen
MODEL_TIMEOUT=300
MODEL_RETRY=1

# staging
OPENAI_API_KEY=sk-...
QWEN_API_KEY=sk-...
DEFAULT_MODEL=qwen
MODEL_TIMEOUT=120
MODEL_RETRY=2
MODEL_FALLBACK=qwen

# prod
OPENAI_API_KEY=sk-...
QWEN_API_KEY=sk-...
CLAUDE_API_KEY=sk-...
DEFAULT_MODEL=gpt-4
MODEL_TIMEOUT=60
MODEL_RETRY=3
MODEL_FALLBACK=gpt-3.5-turbo
MODEL_CONCURRENCY=50
```

### 监控与告警配置

| 配置项 | dev | staging | prod |
|--------|-----|---------|------|
| 指标收集 | 否 | 是 | 是 |
| 链路追踪 | 否 | 基础 | 完整 |
| 告警通知 | 否 | Slack | Slack + PagerDuty |
| 健康检查 | /health | /health + /ready | /health + /ready + /live |
| 指标端点 | - | /metrics (内网) | /metrics (内网) |

**环境变量**:
```bash
# dev
METRICS_ENABLED=false
TRACING_ENABLED=false

# staging
METRICS_ENABLED=true
METRICS_PORT=9090
TRACING_ENABLED=true
TRACING_ENDPOINT=http://jaeger-staging:14268/api/traces
ALERT_WEBHOOK=https://hooks.slack.com/services/T.../B.../...

# prod
METRICS_ENABLED=true
METRICS_PORT=9090
TRACING_ENABLED=true
TRACING_ENDPOINT=http://jaeger-prod:14268/api/traces
ALERT_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
PAGERDUTY_KEY=***
```

## 完整环境变量模板

### `.env.dev`
```bash
# Application
ENV=dev
DEBUG=true
SECRET_KEY=dev-secret-key-change-in-prod

# Database
DATABASE_URL=sqlite:///./dev.db

# Storage
ENVIRONMENT=dev
STORAGE_TYPE=local
STORAGE_PATH=./storage

# Auth
AUTH_MODE=mock
AUTH_MOCK_USER_ID=dev_user_001

# Logging
LOG_LEVEL=DEBUG
LOG_FORMAT=text

# CORS
CORS_ORIGINS=*

# Models
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=qwen
```

### `.env.staging`
```bash
# Application
ENV=staging
DEBUG=false
SECRET_KEY=*** # 使用密钥管理服务

# Database
DATABASE_URL=postgresql://user:***@staging-db:5432/jingxin_staging?sslmode=prefer

# Storage
ENVIRONMENT=staging
STORAGE_TYPE=s3
S3_BUCKET=jingxin-staging
S3_REGION=us-east-1
S3_ACCESS_KEY=***
S3_SECRET_KEY=***
S3_PRESIGNED_URL_EXPIRY=3600

# Auth
AUTH_MODE=remote
AUTH_API_URL=https://auth-staging.internal/api

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE_PATH=/var/log/jingxin/app.log

# CORS
CORS_ORIGINS=http://localhost:3000,https://staging.jingxin.example.com

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_USER=100/minute

# Models
OPENAI_API_KEY=***
QWEN_API_KEY=***
DEFAULT_MODEL=qwen
```

### `.env.prod`
```bash
# Application
ENV=prod
DEBUG=false
SECRET_KEY=*** # 从密钥管理服务加载

# Database
DATABASE_URL=postgresql://user:***@prod-db-cluster:5432/jingxin_prod?sslmode=require

# Storage
ENVIRONMENT=prod
STORAGE_TYPE=s3
S3_BUCKET=jingxin-prod
S3_REGION=us-east-1
S3_ACCESS_KEY=***
S3_SECRET_KEY=***
S3_PRESIGNED_URL_EXPIRY=900
S3_CDN_DOMAIN=cdn.jingxin.example.com

# Auth
AUTH_MODE=remote
AUTH_API_URL=https://auth.internal/api
AUTH_CACHE_TTL=300

# Logging
LOG_LEVEL=WARNING
LOG_FORMAT=json
LOG_FILE_PATH=/var/log/jingxin/app.log
LOG_REMOTE_ENDPOINT=https://log-collector.internal/ingest

# CORS
CORS_ORIGINS=https://jingxin.example.com,https://www.jingxin.example.com

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_USER=50/minute
RATE_LIMIT_BAN_DURATION=300

# Models
OPENAI_API_KEY=***
QWEN_API_KEY=***
CLAUDE_API_KEY=***
DEFAULT_MODEL=gpt-4
MODEL_FALLBACK=gpt-3.5-turbo

# Monitoring
METRICS_ENABLED=true
TRACING_ENABLED=true
ALERT_WEBHOOK=***
PAGERDUTY_KEY=***
```

## 配置加载优先级

1. 环境变量 (最高优先级)
2. `.env.{ENV}` 文件
3. `.env` 文件
4. 代码默认值 (最低优先级)

## 敏感配置管理

**禁止**:
- ❌ 将密钥提交到代码仓库
- ❌ 在日志中打印密钥
- ❌ 在错误消息中暴露密钥

**推荐**:
- ✅ 使用密钥管理服务 (AWS Secrets Manager, HashiCorp Vault)
- ✅ 在 CI/CD 中通过环境变量注入
- ✅ 在本地使用 `.env` 文件 (已加入 `.gitignore`)
- ✅ 定期轮转密钥
