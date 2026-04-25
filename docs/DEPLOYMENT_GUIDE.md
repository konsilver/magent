# Jingxin-Agent 完整部署指南

## 📋 目录

1. [部署前准备](#部署前准备)
2. [环境要求](#环境要求)
3. [快速开始](#快速开始)
4. [本地开发部署](#本地开发部署)
5. [Staging 环境部署](#staging-环境部署)
6. [Production 环境部署](#production-环境部署)
7. [Docker 部署](#docker-部署)
8. [Kubernetes 部署](#kubernetes-部署)
9. [数据库迁移](#数据库迁移)
10. [监控配置](#监控配置)
11. [故障排查](#故障排查)
12. [安全检查清单](#安全检查清单)

---

## 部署前准备

### 1. 系统要求

#### 硬件要求

**最低配置（开发环境）：**
- CPU: 2 核心
- 内存: 4 GB RAM
- 磁盘: 20 GB SSD
- 网络: 10 Mbps

**推荐配置（生产环境）：**
- CPU: 4+ 核心
- 内存: 8+ GB RAM
- 磁盘: 100+ GB SSD
- 网络: 100 Mbps

#### 软件要求

| 软件 | 版本 | 必需 | 说明 |
|------|------|------|------|
| Python | 3.11+ | ✅ | 后端运行时 |
| PostgreSQL | 15+ | ✅ 生产 | 生产数据库 |
| SQLite | 3.x | ✅ 开发 | 开发数据库 |
| Docker | 20.10+ | 推荐 | 容器化部署 |
| Docker Compose | 2.0+ | 推荐 | 服务编排 |
| Node.js | 18+ | 前端 | 前端构建 |
| Git | 2.x | ✅ | 版本控制 |

### 2. 依赖服务

| 服务 | 用途 | 必需 | 替代方案 |
|------|------|------|----------|
| PostgreSQL | 主数据库 | ✅ 生产 | SQLite (开发) |
| S3/MinIO | 对象存储 | ✅ 生产 | 本地文件系统 (开发) |
| Redis | 缓存/会话 | 可选 | 内存缓存 |
| User Center API | 用户认证 | ✅ 生产 | Mock 模式 (开发) |
| OpenAI API | AI 模型 | ✅ | 其他兼容 API |
| Prometheus | 监控 | 推荐 | - |
| Grafana | 可视化 | 推荐 | - |
| Jaeger | 链路追踪 | 推荐 | - |

### 3. 网络要求

**出站连接：**
- OpenAI API: `api.openai.com:443`
- User Center: 自定义 URL
- S3/Object Storage: 自定义 URL
- PyPI: `pypi.org:443` (安装依赖)

**入站端口：**
- HTTP API: `8000` (可配置)
- Prometheus Metrics: `8000/metrics`
- Health Check: `8000/health`

---

## 环境要求

### 环境变量清单

创建对应环境的 `.env` 文件：

```bash
# .env.dev (开发环境)
# .env.staging (预发环境)
# .env.production (生产环境)
```

#### 核心配置

```bash
# 环境标识
ENV=production                    # dev / staging / production

# 服务配置
HOST=0.0.0.0
PORT=8000
WORKERS=4                         # Uvicorn workers (生产建议 CPU 核心数)

# 日志配置
LOG_LEVEL=INFO                    # DEBUG / INFO / WARNING / ERROR
LOG_FORMAT=json                   # colored / json
```

#### 数据库配置

```bash
# PostgreSQL (生产)
DATABASE_URL=postgresql://user:password@host:5432/jingxin_prod

# SQLite (开发)
# DATABASE_URL=sqlite:///./jingxin_dev.db
```

#### 认证配置

```bash
# 认证模式
AUTH_MODE=remote                  # mock / remote

# User Center API
AUTH_API_URL=https://auth.example.com
AUTH_API_TIMEOUT=5                # 秒
AUTH_RETRY_COUNT=2

# Mock 模式 (仅开发)
AUTH_MOCK_USER_ID=dev_user_001
AUTH_MOCK_USERNAME=Developer
```

#### 存储配置

```bash
# 存储后端
STORAGE_BACKEND=s3                # local / s3

# 本地存储 (开发)
STORAGE_LOCAL_BASE_DIR=./storage

# S3 存储 (生产)
STORAGE_S3_BUCKET=jingxin-production
STORAGE_S3_REGION=us-east-1
STORAGE_S3_ENDPOINT_URL=          # 可选，用于 MinIO
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

#### CORS 配置

```bash
# CORS 允许的源
CORS_ORIGINS=https://app.example.com,https://www.example.com

# 开发环境可以使用
# CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

#### AI 模型配置

```bash
# OpenAI API
OPENAI_API_KEY=sk-...
OPENAI_API_BASE=https://api.openai.com/v1
DEFAULT_MODEL=gpt-4

# 其他模型 (可选)
# ANTHROPIC_API_KEY=...
# GOOGLE_API_KEY=...
```

#### 监控配置

```bash
# OpenTelemetry
OTEL_ENABLED=true
OTEL_EXPORTER_JAEGER_ENDPOINT=http://jaeger:14268/api/traces
OTEL_SERVICE_NAME=jingxin-agent

# Metrics
METRICS_ENABLED=true
```

#### 限流配置

```bash
# 速率限制
RATE_LIMIT_ENABLED=true
RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_BURST=20

# 熔断器
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=5       # 失败次数阈值
CIRCUIT_BREAKER_TIMEOUT=60        # 熔断超时（秒）
```

---

## 快速开始

### 方式 1: 使用 Makefile (推荐)

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/jingxin-agent.git
cd jingxin-agent

# 2. 安装依赖
make install

# 3. 创建开发环境配置
make env-dev

# 4. 运行数据库迁移
make migrate

# 5. 加载测试数据 (可选)
make db-seed

# 6. 启动开发服务器
make dev
```

### 方式 2: 使用 Docker Compose (推荐)

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/jingxin-agent.git
cd jingxin-agent

# 2. 创建环境配置
cp .env.example .env
# 编辑 .env 文件

# 3. 启动所有服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 健康检查
curl http://localhost:8000/health
```

访问服务：
- API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- 前端: http://localhost:3000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001
- Jaeger: http://localhost:16686

---

## 本地开发部署

### 步骤 1: 环境准备

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 开发工具
```

### 步骤 2: 配置环境

```bash
# 创建开发配置
cat > .env.dev << 'EOF'
ENV=dev
DATABASE_URL=sqlite:///./jingxin_dev.db
AUTH_MODE=mock
AUTH_MOCK_USER_ID=dev_user_001
AUTH_MOCK_USERNAME=Developer
STORAGE_BACKEND=local
STORAGE_LOCAL_BASE_DIR=./storage
LOG_LEVEL=DEBUG
LOG_FORMAT=colored
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
OTEL_ENABLED=false
OPENAI_API_KEY=sk-your-key-here
EOF

# 使用配置
export $(cat .env.dev | xargs)
```

### 步骤 3: 数据库初始化

```bash
# 运行迁移
alembic upgrade head

# 验证迁移
alembic current

# 加载测试数据
python src/backend/scripts/seed_data.py --environment dev
```

### 步骤 4: 启动服务

```bash
# 方式 1: 使用 Uvicorn (推荐)
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

# 方式 2: 使用 Python
python app.py

# 方式 3: 使用 Makefile
make dev
```

### 步骤 5: 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 预期响应
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2024-01-15T10:30:00Z",
  "checks": {
    "database": "ok",
    "storage": "ok"
  }
}

# API 文档
open http://localhost:8000/docs
```

### 开发工具

```bash
# 代码格式化
make format

# 代码检查
make lint

# 类型检查
make type-check

# 运行测试
make test

# 安全扫描
make security-scan
```

---

## Staging 环境部署

### 前置条件

- [ ] Staging 服务器已准备
- [ ] PostgreSQL 数据库已创建
- [ ] S3 存储桶已配置
- [ ] User Center API 可访问
- [ ] 域名和 SSL 证书已配置

### 步骤 1: 服务器准备

```bash
# SSH 登录服务器
ssh user@staging-server

# 安装系统依赖
sudo apt update
sudo apt install -y python3.11 python3.11-venv postgresql-client git

# 创建应用目录
sudo mkdir -p /opt/jingxin-agent
sudo chown $USER:$USER /opt/jingxin-agent
cd /opt/jingxin-agent
```

### 步骤 2: 部署代码

```bash
# 克隆代码
git clone -b develop https://github.com/your-org/jingxin-agent.git .

# 创建虚拟环境
python3.11 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 步骤 3: 配置环境

```bash
# 创建 Staging 配置
cat > .env.staging << 'EOF'
ENV=staging
DATABASE_URL=postgresql://jingxin:PASSWORD@db-staging.example.com:5432/jingxin_staging
AUTH_MODE=remote
AUTH_API_URL=https://staging-auth.example.com
STORAGE_BACKEND=s3
STORAGE_S3_BUCKET=jingxin-staging
STORAGE_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
LOG_LEVEL=INFO
LOG_FORMAT=json
CORS_ORIGINS=https://staging.jingxin.example.com
OTEL_ENABLED=true
OTEL_EXPORTER_JAEGER_ENDPOINT=http://jaeger-staging:14268/api/traces
RATE_LIMIT_ENABLED=true
OPENAI_API_KEY=sk-...
EOF

# 设置权限
chmod 600 .env.staging
```

### 步骤 4: 数据库迁移

```bash
# 测试数据库连接
export $(cat .env.staging | xargs)
psql $DATABASE_URL -c "SELECT version();"

# 备份数据库
./src/backend/scripts/db_backup_restore.sh backup staging

# 运行迁移演练
./src/backend/scripts/db_migration_rehearsal.sh staging

# 应用迁移
alembic upgrade head

# 验证迁移
alembic current
```

### 步骤 5: 使用 Systemd 配置服务

```bash
# 创建 systemd 服务文件
sudo cat > /etc/systemd/system/jingxin-agent.service << 'EOF'
[Unit]
Description=Jingxin Agent API Service
After=network.target postgresql.service

[Service]
Type=notify
User=jingxin
Group=jingxin
WorkingDirectory=/opt/jingxin-agent
Environment="PATH=/opt/jingxin-agent/venv/bin"
EnvironmentFile=/opt/jingxin-agent/.env.staging
ExecStart=/opt/jingxin-agent/venv/bin/uvicorn api.app:app --host 0.0.0.0 --port 8000 --workers 4
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=jingxin-agent

[Install]
WantedBy=multi-user.target
EOF

# 重载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start jingxin-agent

# 设置开机启动
sudo systemctl enable jingxin-agent

# 查看状态
sudo systemctl status jingxin-agent

# 查看日志
sudo journalctl -u jingxin-agent -f
```

### 步骤 6: 配置 Nginx 反向代理

```bash
# 安装 Nginx
sudo apt install -y nginx

# 创建配置
sudo cat > /etc/nginx/sites-available/jingxin-agent << 'EOF'
upstream jingxin_backend {
    server 127.0.0.1:8000;
    keepalive 64;
}

server {
    listen 80;
    server_name staging.jingxin.example.com;

    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name staging.jingxin.example.com;

    # SSL 配置
    ssl_certificate /etc/ssl/certs/jingxin.crt;
    ssl_certificate_key /etc/ssl/private/jingxin.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # 日志
    access_log /var/log/nginx/jingxin-access.log;
    error_log /var/log/nginx/jingxin-error.log;

    # 请求大小限制
    client_max_body_size 100M;

    # Proxy 配置
    location / {
        proxy_pass http://jingxin_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # SSE 特殊配置
    location /chat/stream {
        proxy_pass http://jingxin_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    # Health check
    location /health {
        proxy_pass http://jingxin_backend;
        access_log off;
    }
}
EOF

# 启用配置
sudo ln -s /etc/nginx/sites-available/jingxin-agent /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重载 Nginx
sudo systemctl reload nginx
```

### 步骤 7: 验证部署

```bash
# 健康检查
curl https://staging.jingxin.example.com/health

# 测试 API
curl -X POST https://staging.jingxin.example.com/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TEST_TOKEN" \
  -d '{"message": "Hello", "session_id": "test"}'

# 查看监控
curl https://staging.jingxin.example.com/metrics
```

---

## Production 环境部署

### 前置条件检查清单

#### 基础设施
- [ ] 生产服务器已准备 (至少 2 台，用于 Blue-Green)
- [ ] 负载均衡器已配置
- [ ] PostgreSQL 高可用集群已配置
- [ ] S3/对象存储已配置
- [ ] 备份策略已制定
- [ ] 灾备方案已准备

#### 网络与安全
- [ ] 域名和 SSL 证书已配置
- [ ] 防火墙规则已配置
- [ ] VPN/跳板机访问已配置
- [ ] DDoS 防护已启用
- [ ] WAF 已配置

#### 监控与告警
- [ ] Prometheus 已部署
- [ ] Grafana 已配置
- [ ] Jaeger 已部署
- [ ] AlertManager 已配置
- [ ] PagerDuty/Slack 通知已配置
- [ ] 日志聚合已配置

#### 团队准备
- [ ] On-call 轮值已安排
- [ ] 紧急联系方式已确认
- [ ] Runbook 已审阅
- [ ] 回滚预案已演练
- [ ] 客服团队已培训

### 部署策略：Blue-Green 部署

#### 架构说明

```
                    ┌──────────────┐
                    │ Load Balancer│
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
         ┌────▼────┐               ┌────▼────┐
         │  Blue   │               │  Green  │
         │ (旧版本) │               │ (新版本) │
         └────┬────┘               └────┬────┘
              │                         │
         ┌────▼─────────────────────────▼────┐
         │      PostgreSQL (共享)             │
         └────────────────────────────────────┘
```

#### 部署步骤

**Phase 1: 准备 Green 环境**

```bash
# 1. 在 Green 服务器上部署新版本
ssh user@green-server

cd /opt/jingxin-agent

# 拉取新代码
git fetch origin
git checkout v1.0.0  # 替换为实际版本

# 安装依赖
source venv/bin/activate
pip install -r requirements.txt

# 更新配置
cp .env.production .env
# 确认配置正确

# 运行自检
PYTHONPATH=src/backend pytest src/backend/tests/ -v
```

**Phase 2: 数据库迁移**

```bash
# 2. 备份生产数据库
./src/backend/scripts/db_backup_restore.sh backup production

# 验证备份
ls -lh backups/

# 3. 在测试数据库上演练迁移
./src/backend/scripts/db_migration_rehearsal.sh production

# 4. 应用生产迁移
alembic upgrade head

# 5. 验证迁移成功
alembic current
psql $DATABASE_URL -c "SELECT COUNT(*) FROM users_shadow;"
```

**Phase 3: 启动 Green 环境**

```bash
# 启动 Green 服务 (端口 8001)
export PORT=8001
uvicorn api.app:app --host 0.0.0.0 --port 8001 --workers 4 &

# 等待启动
sleep 10

# 健康检查
curl http://localhost:8001/health
curl http://localhost:8001/ready
curl http://localhost:8001/live

# 冒烟测试
./src/backend/scripts/smoke_test.sh http://localhost:8001
```

**Phase 4: 切换流量**

```bash
# 更新负载均衡器配置，将流量切到 Green
# (具体命令取决于你的负载均衡器)

# Nginx 示例:
sudo sed -i 's/server 127.0.0.1:8000/server 127.0.0.1:8001/' /etc/nginx/upstream.conf
sudo nginx -s reload

# HAProxy 示例:
# echo "set server jingxin/backend1 state drain" | sudo socat stdio /var/run/haproxy.sock
# echo "set server jingxin/backend2 state ready" | sudo socat stdio /var/run/haproxy.sock
```

**Phase 5: 监控观察期**

```bash
# 监控 30 分钟
# 1. 检查错误率
curl http://prometheus:9090/api/v1/query?query='rate(http_requests_total{status=~"5.."}[5m])'

# 2. 检查延迟
curl http://prometheus:9090/api/v1/query?query='histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))'

# 3. 检查告警
curl http://prometheus:9090/api/v1/alerts

# 4. 查看日志
tail -f /var/log/jingxin-agent/app.log | grep ERROR
```

**Phase 6: 下线 Blue 环境**

```bash
# 如果 Green 运行正常，停止 Blue
ssh user@blue-server
sudo systemctl stop jingxin-agent

# 保留 Blue 环境 24 小时以便快速回滚
```

### 回滚流程

如果 Green 环境出现问题：

```bash
# 1. 立即切换流量回 Blue
sudo sed -i 's/server 127.0.0.1:8001/server 127.0.0.1:8000/' /etc/nginx/upstream.conf
sudo nginx -s reload

# 2. 验证 Blue 健康
curl http://localhost:8000/health

# 3. 回滚数据库 (如需要)
alembic downgrade -1

# 4. 通知团队
# 发送 Slack/PagerDuty 通知

# 5. 启动事后分析
# 记录故障时间线和原因
```

---

## Docker 部署

### 使用 Docker Compose (推荐)

#### 步骤 1: 准备配置

```bash
# 创建生产 docker-compose 配置
cat > docker-compose.production.yml << 'EOF'
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: jingxin_prod
      POSTGRES_USER: jingxin_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jingxin_user"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    image: ghcr.io/your-org/jingxin-backend:${VERSION:-latest}
    environment:
      DATABASE_URL: postgresql://jingxin_user:${DB_PASSWORD}@postgres:5432/jingxin_prod
      ENV: production
      # ... 其他环境变量
    volumes:
      - storage_data:/app/storage
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  frontend:
    image: ghcr.io/your-org/jingxin-frontend:${VERSION:-latest}
    ports:
      - "3000:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  postgres_data:
  storage_data:
EOF
```

#### 步骤 2: 部署

```bash
# 设置版本
export VERSION=v1.0.0
export DB_PASSWORD=your-secure-password

# 拉取镜像
docker-compose -f docker-compose.production.yml pull

# 启动服务
docker-compose -f docker-compose.production.yml up -d

# 查看状态
docker-compose -f docker-compose.production.yml ps

# 查看日志
docker-compose -f docker-compose.production.yml logs -f backend
```

#### 步骤 3: 数据库迁移

```bash
# 运行迁移
docker-compose -f docker-compose.production.yml exec backend alembic upgrade head

# 验证
docker-compose -f docker-compose.production.yml exec backend alembic current
```

### 使用 Docker Swarm

```bash
# 初始化 Swarm
docker swarm init

# 部署 Stack
docker stack deploy -c docker-compose.production.yml jingxin

# 查看服务
docker stack services jingxin

# 查看日志
docker service logs -f jingxin_backend

# 扩展服务
docker service scale jingxin_backend=3
```

---

## Kubernetes 部署

### 步骤 1: 准备 Kubernetes 清单

创建 `k8s/` 目录并添加以下文件：

**k8s/namespace.yaml**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: jingxin-agent
```

**k8s/configmap.yaml**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: jingxin-config
  namespace: jingxin-agent
data:
  ENV: "production"
  LOG_LEVEL: "INFO"
  LOG_FORMAT: "json"
  STORAGE_BACKEND: "s3"
  OTEL_ENABLED: "true"
```

**k8s/secret.yaml**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: jingxin-secrets
  namespace: jingxin-agent
type: Opaque
stringData:
  DATABASE_URL: "postgresql://user:password@postgres:5432/jingxin"
  OPENAI_API_KEY: "sk-..."
  AWS_ACCESS_KEY_ID: "AKIA..."
  AWS_SECRET_ACCESS_KEY: "..."
```

**k8s/deployment.yaml**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jingxin-backend
  namespace: jingxin-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: jingxin-backend
  template:
    metadata:
      labels:
        app: jingxin-backend
    spec:
      containers:
      - name: backend
        image: ghcr.io/your-org/jingxin-backend:v1.0.0
        ports:
        - containerPort: 8000
        envFrom:
        - configMapRef:
            name: jingxin-config
        - secretRef:
            name: jingxin-secrets
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
```

**k8s/service.yaml**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: jingxin-backend-service
  namespace: jingxin-agent
spec:
  selector:
    app: jingxin-backend
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer
```

**k8s/ingress.yaml**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: jingxin-ingress
  namespace: jingxin-agent
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - api.jingxin.example.com
    secretName: jingxin-tls
  rules:
  - host: api.jingxin.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: jingxin-backend-service
            port:
              number: 80
```

### 步骤 2: 部署到 Kubernetes

```bash
# 创建 namespace
kubectl apply -f k8s/namespace.yaml

# 创建 ConfigMap 和 Secret
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml

# 部署应用
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml

# 查看状态
kubectl get pods -n jingxin-agent
kubectl get svc -n jingxin-agent
kubectl get ingress -n jingxin-agent

# 查看日志
kubectl logs -f deployment/jingxin-backend -n jingxin-agent

# 扩展副本
kubectl scale deployment/jingxin-backend --replicas=5 -n jingxin-agent
```

### 步骤 3: 滚动更新

```bash
# 更新镜像
kubectl set image deployment/jingxin-backend \
  backend=ghcr.io/your-org/jingxin-backend:v1.1.0 \
  -n jingxin-agent

# 查看更新状态
kubectl rollout status deployment/jingxin-backend -n jingxin-agent

# 回滚
kubectl rollout undo deployment/jingxin-backend -n jingxin-agent
```

---

## 数据库迁移

### 迁移最佳实践

#### 1. 迁移前备份

```bash
# 自动备份
./src/backend/scripts/db_backup_restore.sh backup production

# 手动备份
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d_%H%M%S).sql
gzip backup_*.sql
```

#### 2. 迁移演练

```bash
# 在 Staging 环境演练
./src/backend/scripts/db_migration_rehearsal.sh staging

# 检查输出日志
cat backups/migration_rehearsal_*.log
```

#### 3. 生产迁移

```bash
# 应用迁移
alembic upgrade head

# 验证结果
alembic current
alembic history --verbose
```

#### 4. 回滚迁移

```bash
# 回滚一个版本
alembic downgrade -1

# 回滚到特定版本
alembic downgrade <revision_id>

# 完全回滚
alembic downgrade base
```

### 常见迁移场景

#### 添加新列

```bash
# 生成迁移
alembic revision --autogenerate -m "add email column to users"

# 编辑迁移文件添加默认值
# alembic/versions/xxx_add_email_column.py

# 应用迁移
alembic upgrade head
```

#### 大表迁移

```bash
# 分批迁移大表
# 1. 添加新列（允许 NULL）
# 2. 批量更新数据
# 3. 添加 NOT NULL 约束
```

---

## 监控配置

### Prometheus 配置

**configs/monitoring/prometheus.yml**

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
  - static_configs:
    - targets:
      - alertmanager:9093

rule_files:
  - "/etc/prometheus/rules.yml"

scrape_configs:
  - job_name: 'jingxin-backend'
    static_configs:
    - targets: ['backend:8000']
    metrics_path: '/metrics'
```

### Grafana Dashboard

导入预配置的 Dashboard:

```bash
# 使用 Grafana UI
# 1. 访问 http://localhost:3001
# 2. Login (admin/admin)
# 3. Import Dashboard
# 4. 上传 configs/monitoring/grafana/dashboards/jingxin-overview.json
```

### 告警规则

关键告警已配置在 `configs/alerting/prometheus_rules.yml`:

- 高错误率 (> 5%)
- 高延迟 (P95 > 2s)
- SSE 中断率高
- 数据库连接池耗尽
- 磁盘空间不足

---

## 故障排查

### 常见问题

#### 1. 服务无法启动

```bash
# 检查日志
journalctl -u jingxin-agent -n 50

# 检查端口占用
sudo netstat -tlnp | grep 8000

# 检查配置
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('DATABASE_URL'))"
```

#### 2. 数据库连接失败

```bash
# 测试连接
psql $DATABASE_URL -c "SELECT version();"

# 检查防火墙
telnet db-host 5432

# 检查凭据
echo $DATABASE_URL
```

#### 3. API 返回 500 错误

```bash
# 查看应用日志
tail -f /var/log/jingxin-agent/app.log

# 查看 Trace ID
curl https://api.example.com/health -v | grep trace_id

# 在 Jaeger 中查找 trace
```

#### 4. SSE 流式响应中断

```bash
# 检查 Nginx 配置
sudo nginx -T | grep proxy_read_timeout

# 增加超时
# proxy_read_timeout 3600s;

# 检查应用日志
grep "SSE" /var/log/jingxin-agent/app.log
```

### 调试工具

```bash
# 1. 健康检查脚本
./src/backend/scripts/health_check.sh

# 2. 端点测试
./src/backend/scripts/smoke_test.sh https://api.example.com

# 3. 压力测试
make performance-test

# 4. 数据库诊断
./src/backend/scripts/db_diagnostics.sh
```

---

## 安全检查清单

### 部署前检查

- [ ] 所有秘钥通过环境变量注入，无明文
- [ ] 数据库连接使用 SSL
- [ ] API 仅通过 HTTPS 访问
- [ ] CORS 配置为白名单模式
- [ ] 速率限制已启用
- [ ] SQL 注入防护已验证
- [ ] XSS 防护已验证
- [ ] 文件上传大小限制已配置
- [ ] 审计日志已启用
- [ ] 敏感数据脱敏已配置

### 部署后验证

```bash
# 运行安全测试
make security-test

# 扫描漏洞
make security-scan

# 检查 SSL
openssl s_client -connect api.example.com:443 -tls1_2

# 检查 Headers
curl -I https://api.example.com | grep -E "X-Frame-Options|X-Content-Type-Options"
```

---

## 附录

### A. 环境变量完整清单

参见 `docs/environment-config.md`

### B. API 端点清单

参见 `docs/api-contract.yaml`

### C. 错误码对照表

参见 `docs/error-codes.md`

### D. 运维手册

参见 `docs/runbook.md`

### E. 发布流程

参见 `docs/release-playbook.md`

---

## 支持与帮助

- **文档**: `docs/`
- **Issue 追踪**: GitHub Issues
- **On-call 团队**: [Slack Channel]
- **紧急联系**: [Emergency Contact]

---

**文档版本**: v1.0
**最后更新**: 2024-01-XX
**维护者**: DevOps Team
