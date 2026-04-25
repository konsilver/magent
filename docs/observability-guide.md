# Jingxin-Agent 可观测性指南

本文档介绍 Jingxin-Agent 的可观测性功能，包括日志、指标、追踪、告警和限流等。

## 目录

- [日志系统](#日志系统)
- [指标监控](#指标监控)
- [分布式追踪](#分布式追踪)
- [告警配置](#告警配置)
- [限流与熔断](#限流与熔断)
- [健康检查](#健康检查)
- [故障排查](#故障排查)

## 日志系统

### 配置

Jingxin-Agent 使用 structlog 提供结构化日志。

#### 环境变量

```bash
# 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
LOG_LEVEL=INFO

# 运行环境（dev, staging, prod）
ENV=prod
```

#### 日志格式

**开发环境**：彩色文本输出
```
2024-01-15 10:30:45 [info     ] request_started method=POST path=/chat/stream
2024-01-15 10:30:46 [info     ] request_completed status_code=200 latency=1.234
```

**生产环境**：JSON 格式
```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "level": "info",
  "event": "request_completed",
  "service": "jingxin-agent",
  "trace_id": "abc-123-def",
  "user_id": "user-456",
  "chat_id": "chat-789",
  "method": "POST",
  "path": "/chat/stream",
  "status_code": 200,
  "latency": 1.234
}
```

### 使用示例

```python
from core.logging_config import get_logger, LogContext

logger = get_logger(__name__)

# 基础日志
logger.info("operation_completed", result="success")

# 带上下文的日志
with LogContext(trace_id="abc-123", user_id="user-456"):
    logger.info("user_action", action="create_chat")
    # 此作用域内的所有日志都会包含 trace_id 和 user_id

# 错误日志
try:
    result = some_operation()
except Exception as e:
    logger.error("operation_failed", error=str(e), exc_info=True)
```

### 日志字段

所有日志包含以下标准字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| timestamp | ISO 8601 时间戳 | 2024-01-15T10:30:45.123Z |
| level | 日志级别 | info, error, warning |
| event | 事件名称 | request_completed |
| service | 服务名称 | jingxin-agent |
| trace_id | 追踪 ID | abc-123-def |
| user_id | 用户 ID（如有） | user-456 |
| chat_id | 会话 ID（如有） | chat-789 |

### 敏感信息脱敏

日志系统自动脱敏以下字段：
- password
- token
- secret
- api_key
- authorization

示例：
```
"password": "***"
"Authorization": "Bearer ***"
```

## 指标监控

### Prometheus 指标

Jingxin-Agent 暴露 Prometheus 格式的指标。

#### 指标端点

```bash
# 仅内网可访问
GET /metrics
```

#### 关键指标

**HTTP 请求指标**

```promql
# 请求总数
http_requests_total{method="POST", endpoint="/chat/stream", status="2xx"}

# 请求延迟（P95）
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# 错误率
rate(http_requests_total{status="5xx"}[5m]) / rate(http_requests_total[5m])
```

**SSE 连接指标**

```promql
# 活跃连接数
sse_active_connections{endpoint="/chat/stream"}

# SSE 错误率
rate(sse_errors_total[5m])

# 连接时长
sse_connection_duration_seconds
```

**模型 API 指标**

```promql
# API 调用次数
model_api_requests_total{model="gpt-4", provider="openai", status="success"}

# API 延迟
model_api_duration_seconds{model="gpt-4"}

# Token 使用量
model_api_tokens_total{model="gpt-4", token_type="completion"}

# API 错误
rate(model_api_errors_total[5m])
```

**数据库指标**

```promql
# 连接池状态
db_connections{state="active"}
db_connections{state="idle"}

# 查询延迟
db_query_duration_seconds{operation="select", table="chat_messages"}

# 查询错误率
rate(db_errors_total[5m])
```

**业务指标**

```promql
# 创建的会话数
rate(chats_created_total[1h])

# 发送的消息数
rate(messages_sent_total{role="user"}[1h])

# 生成的制品数
rate(artifacts_created_total[1h])
```

### 代码中记录指标

```python
from core.metrics import (
    record_http_request,
    record_model_request,
    record_db_query,
    update_db_connection_pool
)

# 记录 HTTP 请求
record_http_request(
    method="POST",
    endpoint="/chat/stream",
    status_code=200,
    duration=1.234
)

# 记录模型调用
record_model_request(
    model="gpt-4",
    provider="openai",
    status="success",
    duration=2.5,
    prompt_tokens=100,
    completion_tokens=50
)

# 记录数据库查询
record_db_query(
    operation="select",
    table="chat_messages",
    status="success",
    duration=0.05
)

# 更新连接池状态
update_db_connection_pool(active=10, idle=5, total=15)
```

## 分布式追踪

### 配置

```bash
# 启用追踪
TRACING_ENABLED=true

# Jaeger 配置
JAEGER_HOST=jaeger
JAEGER_PORT=6831

# 服务名称
SERVICE_NAME=jingxin-agent
```

### 使用追踪

#### 自动追踪

使用 `@traced` 装饰器：

```python
from core.tracing import traced

@traced("authenticate_user")
async def authenticate_user(token: str):
    # 自动创建 span
    result = await user_center.verify(token)
    return result

@traced("database_query", attributes={"db.system": "postgresql"})
def query_users(query: str):
    # 带额外属性的 span
    return db.query(query)
```

#### 手动追踪

使用上下文管理器：

```python
from core.tracing import trace_span, add_span_attribute

with trace_span("complex_operation", {"operation": "batch_process"}):
    # 添加属性
    add_span_attribute("batch_size", 100)

    # 嵌套 span
    with trace_span("sub_operation"):
        do_something()
```

#### 追踪特定操作

```python
from core.tracing import trace_http_request, trace_db_query, trace_model_call

# HTTP 请求
trace_http_request(
    method="POST",
    url="https://api.openai.com/v1/chat/completions",
    status_code=200
)

# 数据库查询
trace_db_query(
    operation="SELECT",
    table="users",
    query="SELECT * FROM users WHERE id = ?"
)

# 模型调用
trace_model_call(
    model="gpt-4",
    provider="openai",
    prompt_tokens=100,
    completion_tokens=50
)
```

### 查看追踪

访问 Jaeger UI：`http://jaeger-host:16686`

## 告警配置

### Prometheus 告警规则

告警规则定义在 `configs/alerting/prometheus_rules.yml`。

#### 严重级别

| 级别 | 说明 | 响应时间 |
|------|------|----------|
| P0 | 关键：服务中断 | 立即（页面呼叫） |
| P1 | 高：功能受影响 | 30 分钟内 |
| P2 | 中：性能下降 | 2 小时内 |
| P3 | 低：轻微问题 | 1 天内 |

#### 关键告警

**服务可用性**

```yaml
# 服务宕机
- alert: ServiceDown
  expr: up{job="jingxin-agent"} == 0
  for: 1m
  severity: P0

# 高 5xx 错误率
- alert: HighServerErrorRate
  expr: rate(http_requests_total{status="5xx"}[5m]) / rate(http_requests_total[5m]) > 0.01
  for: 5m
  severity: P1
```

**性能**

```yaml
# 高延迟
- alert: HighRequestLatency
  expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 5
  for: 5m
  severity: P2
```

**资源**

```yaml
# 数据库连接池耗尽
- alert: DatabaseConnectionPoolExhausted
  expr: db_connections{state="active"} > 45
  for: 1m
  severity: P0
```

### Alertmanager 配置

告警路由和通知配置在 `configs/alerting/alertmanager.yml`。

#### 通知渠道

- **PagerDuty**：P0 告警
- **Slack**：P1/P2 告警
- **Email**：P3 告警

#### 抑制规则

某些告警会抑制其他告警，避免告警风暴：

- 服务宕机 → 抑制错误率告警
- 熔断器打开 → 抑制集成失败告警
- 连接池耗尽 → 抑制慢查询告警

## 限流与熔断

### 限流

使用 slowapi 实现 API 限流。

#### 全局限流

```bash
# 环境变量
RATE_LIMIT_ENABLED=true
RATE_LIMIT_STORAGE=memory://  # 或 redis://host:port
```

#### 端点限流

```python
from core.rate_limit import limiter
from fastapi import FastAPI

app = FastAPI()
app.state.limiter = limiter

@app.post("/chat/stream")
@limiter.limit("10/minute")  # 每分钟最多 10 次
async def chat_stream(...):
    ...
```

#### 限流配置

常见限流策略：

| 端点 | 限制 | 说明 |
|------|------|------|
| /chat/stream | 10/分钟/IP | 防止流式接口滥用 |
| /v1/chats | 100/分钟/用户 | 会话创建限流 |
| /v1/catalog/kb | 20/分钟/用户 | 知识库操作限流 |

### 熔断器

熔断器保护下游服务，防止级联故障。

#### 配置

```bash
# 用户中心熔断器
CB_USER_CENTER_THRESHOLD=5  # 失败阈值
CB_USER_CENTER_TIMEOUT=60   # 重试超时（秒）

# 模型 API 熔断器
CB_MODEL_API_THRESHOLD=10
CB_MODEL_API_TIMEOUT=30

# 存储熔断器
CB_STORAGE_THRESHOLD=5
CB_STORAGE_TIMEOUT=60
```

#### 使用熔断器

```python
from core.rate_limit import get_circuit_breaker, CircuitBreakerOpenError

# 获取熔断器
breaker = get_circuit_breaker("user_center")

# 同步调用
try:
    result = breaker.call(user_center.verify, token)
except CircuitBreakerOpenError:
    # 熔断器打开，使用降级逻辑
    return mock_response()

# 异步调用
try:
    result = await breaker.call_async(user_center.verify_async, token)
except CircuitBreakerOpenError:
    return mock_response()
```

#### 熔断器状态

- **CLOSED（关闭）**：正常工作，请求通过
- **OPEN（打开）**：失败过多，快速失败
- **HALF_OPEN（半开）**：测试恢复，限制请求

状态转换：
```
CLOSED --[失败阈值]--> OPEN --[超时]--> HALF_OPEN --[成功]--> CLOSED
                                              |
                                           [失败]
                                              |
                                              v
                                            OPEN
```

## 健康检查

Jingxin-Agent 提供三个健康检查端点。

### /health

基础健康检查，快速响应服务状态。

```bash
GET /health

# 响应
{
  "status": "healthy",
  "service": "jingxin-agent",
  "timestamp": "2024-01-15T10:30:45.123Z"
}
```

**用途**：负载均衡器健康检查

### /ready

就绪检查，检查所有依赖。

```bash
GET /ready

# 响应
{
  "ready": true,
  "checks": {
    "database": true,
    "storage": true,
    "user_center": true
  },
  "timestamp": "2024-01-15T10:30:45.123Z"
}
```

**用途**：Kubernetes readiness probe

### /live

存活检查，最简单的响应。

```bash
GET /live

# 响应
{
  "alive": true,
  "timestamp": "2024-01-15T10:30:45.123Z"
}
```

**用途**：Kubernetes liveness probe

### Kubernetes 配置示例

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: jingxin-agent
spec:
  containers:
  - name: app
    image: jingxin-agent:latest
    livenessProbe:
      httpGet:
        path: /live
        port: 3001
      initialDelaySeconds: 30
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 3

    readinessProbe:
      httpGet:
        path: /ready
        port: 3001
      initialDelaySeconds: 10
      periodSeconds: 5
      timeoutSeconds: 3
      failureThreshold: 2
```

## 故障排查

### 查看日志

**查看实时日志**：
```bash
# Docker
docker logs -f jingxin-agent

# Kubernetes
kubectl logs -f pod/jingxin-agent-xxx
```

**搜索特定 trace_id**：
```bash
# JSON 格式日志
cat app.log | grep "trace_id\":\"abc-123" | jq .

# 或使用日志聚合系统（ELK, Grafana Loki）
```

### 查看指标

**访问 Prometheus**：
```
http://prometheus-host:9090
```

**常用查询**：

```promql
# 查看当前 QPS
rate(http_requests_total[1m])

# 查看错误率
rate(http_requests_total{status="5xx"}[5m]) / rate(http_requests_total[5m])

# 查看 P95 延迟
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# 查看活跃连接
sse_active_connections
```

### 查看追踪

**访问 Jaeger**：
```
http://jaeger-host:16686
```

**查找慢请求**：
1. 选择服务：jingxin-agent
2. 设置最小延迟：如 >5s
3. 查看 trace 详情

### 常见问题

#### 1. 高延迟

**检查**：
- 查看 P95/P99 延迟指标
- 查看慢查询日志
- 检查数据库连接池
- 查看模型 API 调用耗时

**排查**：
```promql
# 查看哪个端点慢
topk(5, histogram_quantile(0.95,
  sum by (endpoint, le) (rate(http_request_duration_seconds_bucket[5m]))
))

# 查看数据库慢查询
topk(5, histogram_quantile(0.95,
  sum by (table, le) (rate(db_query_duration_seconds_bucket[5m]))
))
```

#### 2. 高错误率

**检查**：
- 查看错误日志
- 查看告警
- 检查上游服务状态

**排查**：
```promql
# 查看错误率
rate(http_requests_total{status="5xx"}[5m])

# 查看模型 API 错误
rate(model_api_errors_total[5m])

# 查看数据库错误
rate(db_errors_total[5m])
```

#### 3. 连接池耗尽

**检查**：
```promql
# 查看连接池状态
db_connections{state="active"}
db_connections{state="idle"}
```

**解决**：
- 增加连接池大小
- 检查是否有连接泄漏
- 优化长时间持有连接的查询

#### 4. 熔断器打开

**检查**：
```promql
# 查看熔断器状态
circuit_breaker_state{service="user_center"}

# 查看熔断器打开次数
rate(circuit_breaker_opened_total[1h])
```

**解决**：
- 检查下游服务健康状态
- 查看下游服务日志和指标
- 等待熔断器自动恢复或手动重启服务

## 仪表盘

### Grafana 仪表盘

推荐创建以下仪表盘：

**1. 服务概览**
- QPS（按端点）
- 错误率（按状态码）
- P95/P99 延迟
- 活跃连接数

**2. 模型 API**
- API 调用次数
- API 延迟
- Token 使用量
- API 错误率

**3. 数据库**
- 连接池状态
- 查询延迟
- 查询错误率
- 慢查询 Top 5

**4. 业务指标**
- 新建会话数
- 消息发送量
- 制品生成量
- 用户活跃度

### 告警面板

在 Grafana 中配置告警：
- 关联 Alertmanager
- 设置通知渠道
- 配置告警规则

## 最佳实践

### 日志

1. **使用结构化日志**：避免字符串拼接，使用键值对
2. **添加上下文**：使用 `LogContext` 设置 trace_id、user_id 等
3. **适当的日志级别**：
   - DEBUG：详细调试信息
   - INFO：常规操作
   - WARNING：警告但不影响功能
   - ERROR：错误但系统仍可运行
   - CRITICAL：严重错误需立即处理

### 指标

1. **命名规范**：使用 Prometheus 命名规范
   - 使用 `_total` 后缀表示计数器
   - 使用 `_seconds` 表示时间
   - 使用清晰的标签
2. **避免高基数**：标签值不要太多（如不要用 user_id 作为标签）
3. **记录关键业务指标**：不只是技术指标

### 追踪

1. **追踪关键路径**：入口 → 鉴权 → 业务 → 数据库 → 响应
2. **添加有用的属性**：如查询参数、结果大小等
3. **采样率**：生产环境可降低采样率（如 10%）

### 告警

1. **避免告警疲劳**：设置合理的阈值和持续时间
2. **告警可操作**：每个告警都应有对应的 runbook
3. **分级管理**：不同严重级别使用不同通知渠道

### 限流与熔断

1. **合理设置阈值**：根据实际容量和 SLA 设置
2. **降级策略**：熔断器打开时使用降级逻辑
3. **监控熔断器状态**：及时发现和处理熔断事件

## 相关文档

- [运维手册](runbook.md)
- [架构文档](architecture-current.md)
- [生产实施清单](production-implementation-todo.md)
