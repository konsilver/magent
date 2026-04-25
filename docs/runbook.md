# Jingxin-Agent 运维手册 (Runbook)

本文档提供 Jingxin-Agent 的运维操作指南和故障处理流程。

## 目录

- [服务信息](#服务信息)
- [日常运维](#日常运维)
- [告警响应](#告警响应)
- [故障处理](#故障处理)
- [性能优化](#性能优化)
- [应急预案](#应急预案)

## 服务信息

### 基本信息

| 项目 | 信息 |
|------|------|
| 服务名称 | Jingxin-Agent |
| 服务端口 | 3001 |
| 健康检查 | /health, /ready, /live |
| 指标端点 | /metrics (仅内网) |
| 文档 | /docs |

### 依赖服务

| 服务 | 用途 | 影响 |
|------|------|------|
| PostgreSQL | 数据存储 | 核心功能 |
| S3 兼容存储 | 文件存储 | 文件上传/下载 |
| 用户中心 | 身份认证 | 鉴权功能 |
| OpenAI API | 模型调用 | 聊天功能 |

### 监控信息

- **Prometheus**: http://prometheus:9090
- **Grafana**: http://grafana:3000
- **Jaeger**: http://jaeger:16686
- **Alertmanager**: http://alertmanager:9093

### 联系人

| 角色 | 联系方式 | 责任 |
|------|----------|------|
| On-call 工程师 | PagerDuty | P0/P1 告警响应 |
| 开发团队 | Slack #jingxin-dev | 技术支持 |
| 运维团队 | Slack #jingxin-ops | 基础设施 |
| 产品经理 | Email | 业务决策 |

## 日常运维

### 服务启动

**Docker 方式**：
```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f jingxin-agent

# 检查状态
docker-compose ps
```

**Kubernetes 方式**：
```bash
# 应用部署
kubectl apply -f k8s/

# 查看 Pod 状态
kubectl get pods -l app=jingxin-agent

# 查看日志
kubectl logs -f deployment/jingxin-agent

# 查看事件
kubectl get events --sort-by=.metadata.creationTimestamp
```

### 服务停止

**Docker 方式**：
```bash
# 优雅停止
docker-compose stop jingxin-agent

# 强制停止
docker-compose kill jingxin-agent
```

**Kubernetes 方式**：
```bash
# 缩容到 0
kubectl scale deployment jingxin-agent --replicas=0

# 删除部署
kubectl delete deployment jingxin-agent
```

### 服务重启

**Docker 方式**：
```bash
# 重启服务
docker-compose restart jingxin-agent
```

**Kubernetes 方式**：
```bash
# 滚动重启
kubectl rollout restart deployment/jingxin-agent

# 查看重启状态
kubectl rollout status deployment/jingxin-agent
```

### 日志查看

**实时日志**：
```bash
# Docker
docker-compose logs -f jingxin-agent --tail=100

# Kubernetes
kubectl logs -f deployment/jingxin-agent --tail=100
```

**搜索日志**：
```bash
# 搜索特定 trace_id
kubectl logs deployment/jingxin-agent | grep "trace_id\":\"abc-123"

# 搜索错误日志
kubectl logs deployment/jingxin-agent | grep "\"level\":\"error\""

# 搜索特定用户
kubectl logs deployment/jingxin-agent | grep "user_id\":\"user-456"
```

### 健康检查

```bash
# 基础健康检查
curl http://localhost:3001/health

# 就绪检查
curl http://localhost:3001/ready

# 存活检查
curl http://localhost:3001/live

# 查看指标（仅内网）
curl http://localhost:3001/metrics
```

### 数据库维护

**连接数据库**：
```bash
# Docker
docker-compose exec postgres psql -U postgres -d jingxin

# 直接连接
psql postgresql://username:password@host:5432/jingxin
```

**常用查询**：
```sql
-- 查看活跃连接
SELECT count(*) FROM pg_stat_activity WHERE state = 'active';

-- 查看慢查询
SELECT query, calls, mean_exec_time, max_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;

-- 查看表大小
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- 查看最近的会话
SELECT id, user_id, title, created_at
FROM chat_sessions
ORDER BY created_at DESC
LIMIT 10;
```

**备份**：
```bash
# 备份数据库
docker-compose exec postgres pg_dump -U postgres jingxin > backup_$(date +%Y%m%d).sql

# 恢复数据库
docker-compose exec -T postgres psql -U postgres jingxin < backup_20240115.sql
```

### 配置更新

**更新环境变量**：
```bash
# 1. 编辑 .env 文件
vim .env

# 2. 重启服务使配置生效
docker-compose restart jingxin-agent

# Kubernetes: 更新 ConfigMap 或 Secret
kubectl edit configmap jingxin-agent-config
kubectl rollout restart deployment/jingxin-agent
```

**热更新配置**：
某些配置支持热更新（无需重启）：
- 日志级别
- 限流策略
- 熔断器阈值

```python
# 通过 API 更新配置（如有提供）
curl -X PATCH http://localhost:3001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"log_level": "DEBUG"}'
```

## 告警响应

### 告警级别与响应时间

| 级别 | 说明 | 响应时间 | 通知方式 |
|------|------|----------|----------|
| P0 | 服务中断 | 立即 | PagerDuty |
| P1 | 功能受影响 | 30 分钟 | Slack 紧急频道 |
| P2 | 性能下降 | 2 小时 | Slack 告警频道 |
| P3 | 轻微问题 | 1 天 | Email |

### P0: 服务宕机

**告警**: `ServiceDown`

**现象**：
- 服务无法访问
- 健康检查失败
- 所有请求返回 502/503

**立即行动**：

1. **确认告警**：
   ```bash
   # 检查服务状态
   kubectl get pods -l app=jingxin-agent

   # 查看最近日志
   kubectl logs deployment/jingxin-agent --tail=100

   # 查看事件
   kubectl get events --field-selector involvedObject.name=jingxin-agent
   ```

2. **快速恢复**：
   ```bash
   # 重启服务
   kubectl rollout restart deployment/jingxin-agent

   # 或回滚到上一版本
   kubectl rollout undo deployment/jingxin-agent
   ```

3. **通知团队**：
   - 在 Slack #incidents 频道发布事故通知
   - 说明当前状态和预计恢复时间

4. **持续监控**：
   - 确认服务恢复
   - 检查是否有遗留问题

**根因分析**：
- 查看部署历史
- 检查资源使用（CPU、内存、磁盘）
- 查看依赖服务状态
- 分析崩溃日志

### P0: 数据库连接池耗尽

**告警**: `DatabaseConnectionPoolExhausted`

**现象**：
- 请求超时或失败
- 日志显示 "connection pool exhausted"
- 数据库活跃连接数接近上限

**立即行动**：

1. **检查连接池状态**：
   ```promql
   # Prometheus
   db_connections{state="active"}
   db_connections{state="idle"}
   ```

2. **临时扩容**：
   ```bash
   # 增加连接池大小（需要重启）
   # 编辑环境变量
   DB_POOL_SIZE=100  # 从 50 增加到 100

   # 重启服务
   kubectl rollout restart deployment/jingxin-agent
   ```

3. **查找连接泄漏**：
   ```sql
   -- 查看长时间活跃的连接
   SELECT pid, usename, application_name, state, query_start, query
   FROM pg_stat_activity
   WHERE state = 'active' AND query_start < NOW() - INTERVAL '5 minutes'
   ORDER BY query_start;

   -- 终止异常连接（谨慎操作）
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE state = 'active' AND query_start < NOW() - INTERVAL '10 minutes';
   ```

4. **监控恢复**：
   - 观察连接池指标
   - 确认请求成功率恢复

**根因分析**：
- 检查代码是否正确关闭连接
- 查看是否有慢查询占用连接
- 分析流量是否异常增长

### P1: 高 5xx 错误率

**告警**: `HighServerErrorRate`

**现象**：
- 5xx 错误率 > 1%
- 用户反馈服务异常
- 部分功能不可用

**响应步骤**：

1. **定位问题端点**：
   ```promql
   # 查看哪个端点错误多
   topk(5, rate(http_requests_total{status="5xx"}[5m])) by (endpoint)
   ```

2. **查看错误日志**：
   ```bash
   # 查看最近的错误
   kubectl logs deployment/jingxin-agent | grep "\"level\":\"error\"" | tail -50
   ```

3. **检查依赖服务**：
   ```promql
   # 用户中心错误率
   rate(user_center_errors_total[5m])

   # 模型 API 错误率
   rate(model_api_errors_total[5m])

   # 数据库错误率
   rate(db_errors_total[5m])
   ```

4. **应用修复**：
   - 如果是代码问题：修复并部署
   - 如果是配置问题：更新配置
   - 如果是依赖问题：联系相关团队或启用降级

5. **验证恢复**：
   ```promql
   # 确认错误率下降
   rate(http_requests_total{status="5xx"}[5m]) / rate(http_requests_total[5m])
   ```

### P1: 模型 API 错误

**告警**: `ModelAPIErrors`

**现象**：
- 模型调用失败率高
- 聊天功能不可用
- 日志显示 API 错误

**响应步骤**：

1. **检查 API 状态**：
   ```bash
   # 查看 OpenAI 状态页
   curl https://status.openai.com/api/v2/summary.json
   ```

2. **查看错误类型**：
   ```promql
   # 按错误类型分组
   rate(model_api_errors_total[5m]) by (error_type)
   ```

3. **检查熔断器**：
   ```promql
   # 熔断器状态
   circuit_breaker_state{service="model_api"}
   ```

4. **应对措施**：
   - **限流错误 (429)**：降低请求频率或增加 API 配额
   - **认证错误 (401)**：检查 API Key 是否过期
   - **服务错误 (5xx)**：等待 OpenAI 恢复或切换备用模型
   - **超时错误**：增加超时时间或优化 prompt

5. **临时方案**：
   ```python
   # 切换到备用模型
   # 在环境变量中设置
   FALLBACK_MODEL=gpt-3.5-turbo  # 从 gpt-4 降级
   ```

### P2: 高请求延迟

**告警**: `HighRequestLatency`

**现象**：
- P95 延迟 > 5 秒
- 用户反馈响应慢
- 请求排队

**响应步骤**：

1. **定位慢端点**：
   ```promql
   # 查看最慢的端点
   topk(5, histogram_quantile(0.95,
     rate(http_request_duration_seconds_bucket[5m])
   )) by (endpoint)
   ```

2. **检查资源使用**：
   ```bash
   # CPU 使用率
   kubectl top pods -l app=jingxin-agent

   # 内存使用率
   kubectl top pods -l app=jingxin-agent
   ```

3. **分析慢查询**：
   ```promql
   # 数据库查询延迟
   topk(5, histogram_quantile(0.95,
     rate(db_query_duration_seconds_bucket[5m])
   )) by (table)
   ```

4. **查看追踪**：
   - 在 Jaeger 中搜索慢请求（> 5s）
   - 分析耗时分布

5. **优化措施**：
   - **CPU 高**：扩容或优化代码
   - **内存高**：检查内存泄漏或扩容
   - **慢查询**：优化 SQL 或添加索引
   - **模型 API 慢**：并发调用或使用更快的模型

### P2: SSE 连接异常

**告警**: `HighSSEConnections` 或 `HighSSEErrorRate`

**现象**：
- 活跃 SSE 连接数过高（> 1000）
- SSE 错误率高
- 用户反馈聊天中断

**响应步骤**：

1. **检查连接数**：
   ```promql
   sse_active_connections{endpoint="/chat/stream"}
   ```

2. **查看错误类型**：
   ```promql
   rate(sse_errors_total[5m]) by (error_type)
   ```

3. **检查是否有连接泄漏**：
   ```bash
   # 查看连接持续时间
   # 在日志中搜索长时间未关闭的连接
   kubectl logs deployment/jingxin-agent | grep "sse_connection" | grep -v "closed"
   ```

4. **应对措施**：
   - **连接过多**：限制并发连接数或增加资源
   - **连接中断**：检查网络稳定性或负载均衡器配置
   - **连接泄漏**：修复代码确保连接正确关闭

5. **临时限流**：
   ```python
   # 在代码中添加 SSE 连接限制
   MAX_SSE_CONNECTIONS = 500
   ```

## 故障处理

### 常见故障场景

#### 1. 内存溢出 (OOM)

**现象**：
- Pod 被 Kubernetes 重启
- 日志显示 "Killed" 或 OOM
- 内存使用持续增长

**排查**：
```bash
# 查看内存使用趋势
kubectl top pods -l app=jingxin-agent

# 查看 Pod 事件
kubectl describe pod <pod-name> | grep -A 10 "Events"
```

**解决**：
```bash
# 临时：增加内存限制
kubectl set resources deployment jingxin-agent \
  --limits=memory=4Gi --requests=memory=2Gi

# 长期：优化代码，修复内存泄漏
# 1. 使用内存分析工具（memory_profiler）
# 2. 检查是否有大对象未释放
# 3. 优化缓存策略
```

#### 2. CPU 节流

**现象**：
- 请求延迟高
- CPU 使用率接近限制
- 服务响应慢

**排查**：
```promql
# CPU 使用率
rate(container_cpu_usage_seconds_total{pod=~"jingxin-agent.*"}[5m])

# CPU 节流
rate(container_cpu_cfs_throttled_seconds_total{pod=~"jingxin-agent.*"}[5m])
```

**解决**：
```bash
# 临时：增加 CPU 限制
kubectl set resources deployment jingxin-agent \
  --limits=cpu=2000m --requests=cpu=1000m

# 长期：
# 1. 优化 CPU 密集型操作
# 2. 使用异步处理
# 3. 水平扩容
kubectl scale deployment jingxin-agent --replicas=5
```

#### 3. 数据库死锁

**现象**：
- 某些请求永久挂起
- 日志显示死锁错误
- 数据库查询超时

**排查**：
```sql
-- 查看当前锁
SELECT * FROM pg_locks WHERE NOT granted;

-- 查看阻塞查询
SELECT
  blocked_locks.pid AS blocked_pid,
  blocked_activity.usename AS blocked_user,
  blocking_locks.pid AS blocking_pid,
  blocking_activity.usename AS blocking_user,
  blocked_activity.query AS blocked_statement,
  blocking_activity.query AS current_statement_in_blocking_process
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
  ON blocking_locks.locktype = blocked_locks.locktype
  AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
  AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
  AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
  AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
  AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
  AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
  AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
  AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
  AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
  AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

**解决**：
```sql
-- 终止阻塞查询（谨慎操作）
SELECT pg_terminate_backend(<blocking_pid>);

-- 或取消查询
SELECT pg_cancel_backend(<blocking_pid>);
```

**预防**：
- 使用合适的隔离级别
- 缩短事务时间
- 按固定顺序获取锁
- 使用乐观锁代替悲观锁

#### 4. 磁盘空间不足

**现象**：
- 写入失败
- 日志显示 "No space left on device"
- 服务无法启动

**排查**：
```bash
# 检查磁盘使用
df -h

# 查看大文件
du -sh /* | sort -h
du -sh /var/lib/docker/* | sort -h

# 查看 inode 使用
df -i
```

**解决**：
```bash
# 清理 Docker
docker system prune -a --volumes -f

# 清理旧日志
find /var/log -name "*.log" -mtime +30 -delete

# 清理备份文件
find /backups -name "*.sql" -mtime +7 -delete

# 临时：扩容磁盘
# 长期：配置日志轮转和归档策略
```

#### 5. 网络问题

**现象**：
- 服务间调用超时
- DNS 解析失败
- 连接被拒绝

**排查**：
```bash
# 测试网络连通性
kubectl exec -it <pod-name> -- ping postgres

# 测试 DNS
kubectl exec -it <pod-name> -- nslookup postgres

# 测试端口
kubectl exec -it <pod-name> -- telnet postgres 5432

# 查看网络策略
kubectl get networkpolicies
```

**解决**：
- 检查防火墙规则
- 验证服务发现配置
- 检查网络策略
- 验证 DNS 配置

## 性能优化

### 数据库优化

**添加索引**：
```sql
-- 查看缺失索引
SELECT * FROM pg_stat_user_tables WHERE seq_scan > 100;

-- 添加索引
CREATE INDEX CONCURRENTLY idx_chat_messages_chat_id ON chat_messages(chat_id);
CREATE INDEX CONCURRENTLY idx_chat_sessions_user_id_updated ON chat_sessions(user_id, updated_at DESC);
```

**查询优化**：
```sql
-- 使用 EXPLAIN ANALYZE
EXPLAIN ANALYZE SELECT * FROM chat_messages WHERE chat_id = 'xxx';

-- 启用查询统计
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 查看慢查询
SELECT query, calls, mean_exec_time, max_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
```

**连接池调优**：
```bash
# 根据实际负载调整
DB_POOL_SIZE=50        # 连接池大小
DB_POOL_MAX_OVERFLOW=10  # 最大溢出连接
DB_POOL_TIMEOUT=30     # 获取连接超时（秒）
```

### 应用优化

**并发处理**：
```python
# 使用异步并发
import asyncio

async def process_batch(items):
    tasks = [process_item(item) for item in items]
    results = await asyncio.gather(*tasks)
    return results
```

**缓存优化**：
```python
# 添加缓存层
from functools import lru_cache

@lru_cache(maxsize=1000)
def get_user_preferences(user_id):
    # 缓存用户偏好
    return db.query(...)
```

**批量操作**：
```python
# 批量插入代替单条插入
db.bulk_insert_mappings(Message, messages)
db.commit()
```

### 资源扩容

**水平扩容**：
```bash
# 增加副本数
kubectl scale deployment jingxin-agent --replicas=5

# 配置自动扩缩容
kubectl autoscale deployment jingxin-agent \
  --min=3 --max=10 --cpu-percent=70
```

**垂直扩容**：
```bash
# 增加资源限制
kubectl set resources deployment jingxin-agent \
  --limits=cpu=2000m,memory=4Gi \
  --requests=cpu=1000m,memory=2Gi
```

## 应急预案

### 场景 1: 全站故障

**触发条件**：
- 所有 Pod 崩溃
- 数据库不可访问
- 大规模服务中断

**应急流程**：

1. **立即通知**：
   - 触发 P0 告警
   - 通知所有相关人员
   - 在状态页更新事故状态

2. **快速评估**：
   - 检查所有依赖服务状态
   - 确定影响范围
   - 估计恢复时间

3. **紧急恢复**：
   ```bash
   # 1. 回滚到稳定版本
   kubectl rollout undo deployment/jingxin-agent

   # 2. 或从备份恢复
   kubectl apply -f k8s/stable/

   # 3. 重启依赖服务
   kubectl rollout restart deployment/postgres
   ```

4. **验证恢复**：
   - 健康检查通过
   - 核心功能可用
   - 错误率恢复正常

5. **事后分析**：
   - 收集日志和指标
   - 编写事故报告
   - 制定改进措施

### 场景 2: 数据丢失

**触发条件**：
- 数据库损坏
- 误删除数据
- 存储故障

**应急流程**：

1. **停止写入**：
   ```bash
   # 立即停止服务防止进一步损坏
   kubectl scale deployment jingxin-agent --replicas=0
   ```

2. **评估损失**：
   ```sql
   -- 检查数据完整性
   SELECT count(*) FROM chat_sessions;
   SELECT count(*) FROM chat_messages;

   -- 查找最后的有效数据
   SELECT max(created_at) FROM chat_sessions;
   ```

3. **从备份恢复**：
   ```bash
   # 恢复最近的备份
   pg_restore -U postgres -d jingxin backup_latest.dump

   # 或时间点恢复（如果配置了 WAL）
   pg_restore --target-time="2024-01-15 10:00:00"
   ```

4. **数据验证**：
   - 检查恢复的数据完整性
   - 验证关键业务数据
   - 与业务团队确认

5. **恢复服务**：
   ```bash
   kubectl scale deployment jingxin-agent --replicas=3
   ```

### 场景 3: 安全事件

**触发条件**：
- 检测到攻击
- 数据泄露
- 未授权访问

**应急流程**：

1. **隔离影响**：
   ```bash
   # 阻止可疑 IP
   kubectl exec -it <pod-name> -- iptables -A INPUT -s <malicious-ip> -j DROP

   # 或使用网络策略
   kubectl apply -f k8s/network-policy-lockdown.yaml
   ```

2. **收集证据**：
   ```bash
   # 保存日志
   kubectl logs deployment/jingxin-agent > incident_logs_$(date +%Y%m%d_%H%M%S).log

   # 导出审计日志
   psql -c "COPY (SELECT * FROM audit_logs WHERE created_at > NOW() - INTERVAL '1 hour') TO STDOUT CSV" > audit_export.csv
   ```

3. **通知相关方**：
   - 安全团队
   - 法务团队（如需要）
   - 受影响用户（如需要）

4. **修复漏洞**：
   - 更新受影响组件
   - 修改泄露的密钥
   - 强制用户重新登录

5. **加强监控**：
   - 增加安全告警
   - 审查访问日志
   - 持续观察异常行为

### 场景 4: 第三方服务中断

**触发条件**：
- OpenAI API 不可用
- 用户中心故障
- S3 存储故障

**应急流程**：

1. **启用降级**：
   ```python
   # 启用模型降级
   USE_FALLBACK_MODEL=true
   FALLBACK_MODEL=gpt-3.5-turbo

   # 启用缓存响应
   ENABLE_CACHE_FALLBACK=true

   # 跳过非关键功能
   SKIP_NON_CRITICAL_FEATURES=true
   ```

2. **通知用户**：
   - 在 UI 显示服务降级提示
   - 发布状态更新
   - 预估恢复时间

3. **监控恢复**：
   - 持续检查第三方服务状态
   - 准备立即切换回正常模式

4. **恢复正常**：
   ```bash
   # 恢复正常配置
   kubectl set env deployment/jingxin-agent \
     USE_FALLBACK_MODEL=false \
     ENABLE_CACHE_FALLBACK=false
   ```

## 维护窗口

### 定期维护任务

**每日**：
- [ ] 检查服务健康状态
- [ ] 查看告警和错误日志
- [ ] 监控资源使用趋势

**每周**：
- [ ] 清理旧日志和临时文件
- [ ] 查看慢查询和优化机会
- [ ] 检查数据库备份
- [ ] 更新监控仪表盘

**每月**：
- [ ] 数据库维护（VACUUM, ANALYZE）
- [ ] 更新依赖包和安全补丁
- [ ] 审查和优化告警规则
- [ ] 容量规划和成本优化
- [ ] 事故回顾和改进

**每季度**：
- [ ] 灾难恢复演练
- [ ] 全面性能测试
- [ ] 安全审计
- [ ] 架构评审

### 计划性维护

**数据库维护**：
```sql
-- 定期 VACUUM（建议在低峰期）
VACUUM ANALYZE chat_sessions;
VACUUM ANALYZE chat_messages;

-- 重建索引
REINDEX TABLE chat_messages;

-- 更新统计信息
ANALYZE;
```

**日志轮转**：
```bash
# 配置 logrotate
cat > /etc/logrotate.d/jingxin-agent << EOF
/var/log/jingxin-agent/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
EOF
```

**备份验证**：
```bash
# 定期验证备份可恢复性
# 在测试环境恢复最近的备份
pg_restore -U postgres -d jingxin_test backup_latest.dump

# 验证数据完整性
psql -d jingxin_test -c "SELECT count(*) FROM chat_sessions;"
```

## 工具和脚本

### 常用运维脚本

**健康检查脚本**：
```bash
#!/bin/bash
# health_check.sh

echo "=== Jingxin-Agent Health Check ==="

# 检查服务状态
echo "1. Service Status:"
kubectl get pods -l app=jingxin-agent

# 检查健康端点
echo -e "\n2. Health Endpoint:"
curl -s http://localhost:3001/health | jq .

# 检查就绪端点
echo -e "\n3. Ready Endpoint:"
curl -s http://localhost:3001/ready | jq .

# 检查关键指标
echo -e "\n4. Key Metrics:"
curl -s http://localhost:3001/metrics | grep -E "(http_requests_total|db_connections|sse_active_connections)"

echo -e "\n=== Health Check Complete ==="
```

**日志分析脚本**：
```bash
#!/bin/bash
# analyze_logs.sh

HOURS=${1:-1}

echo "=== Analyzing logs from last ${HOURS} hour(s) ==="

# 错误统计
echo "1. Error Count:"
kubectl logs deployment/jingxin-agent --since=${HOURS}h | grep -c "\"level\":\"error\""

# Top 错误
echo -e "\n2. Top Errors:"
kubectl logs deployment/jingxin-agent --since=${HOURS}h | grep "\"level\":\"error\"" | jq -r '.event' | sort | uniq -c | sort -rn | head -10

# 慢请求
echo -e "\n3. Slow Requests (>5s):"
kubectl logs deployment/jingxin-agent --since=${HOURS}h | grep "request_completed" | jq 'select(.latency > 5) | {path, latency, status_code}' | head -10

echo -e "\n=== Analysis Complete ==="
```

## 相关文档

- [可观测性指南](observability-guide.md)
- [架构文档](architecture-current.md)
- [API 文档](baseline_api.md)
- [安全检查清单](security-checklist.md)（待创建）

## 联系支持

遇到无法解决的问题时：

1. **查看文档**：先查阅相关文档和 FAQ
2. **搜索已知问题**：在 GitHub Issues 中搜索
3. **咨询团队**：在 Slack #jingxin-dev 频道提问
4. **创建工单**：通过内部工单系统提交
5. **紧急情况**：直接联系 On-call 工程师

---

**最后更新**: 2024-01-15
**维护者**: Jingxin-Agent 开发团队
