# 生产安全基线检查清单

## 文档说明

**目的**: 确保 Jingxin-Agent 在生产环境部署前满足所有安全要求

**使用方式**:
1. 在每次生产部署前完整执行此检查清单
2. 每项检查都必须打勾确认
3. 不符合项必须整改后才能上线
4. 检查结果需留档备查

**检查人员**: ________________
**检查日期**: ________________
**目标环境**: ☐ Staging  ☐ Production
**版本号**: ________________

---

## 1. 认证与授权

### 1.1 Token 验证

- [ ] **Token 验证已启用**
  - 验证方法: 使用无效 Token 访问 API，应返回 401
  - 测试命令: `curl -H "Authorization: Bearer invalid" https://api.example.com/v1/chats`
  - 预期结果: `{"code": 30002, "message": "Invalid or expired token"}`

- [ ] **用户中心集成测试通过**
  - 验证方法: 使用真实 Token 访问用户中心验证接口
  - 配置检查: `AUTH_API_URL` 已配置且可访问
  - 测试命令: `python -m pytest src/backend/tests/::test_user_center_integration`
  - 预期结果: 测试通过

- [ ] **生产环境使用 HTTPS**
  - 验证方法: 检查 `AUTH_API_URL` 是否以 https:// 开头
  - 配置检查: `AUTH_MODE` != "mock" (生产环境)
  - 检查结果: ________________

### 1.2 接口鉴权

- [ ] **所有敏感接口都有鉴权**
  - 验证方法: 检查以下接口是否使用 `Depends(get_current_user)`
    - `/v1/chats` ✓
    - `/v1/chats/{chat_id}` ✓
    - `/v1/chats/{chat_id}/messages` ✓
    - `/v1/catalog` ✓
    - `/v1/catalog/kb` ✓
    - `/files/{file_id}` ✓
  - 代码检查: 使用 `grep -r "Depends(get_current_user)" src/backend/api/routes/v1/`

- [ ] **无 Token 访问敏感接口返回 401**
  - 测试命令: `curl https://api.example.com/v1/chats`
  - 预期结果: `{"code": 30001, "message": "Missing authentication token"}`

### 1.3 权限验证

- [ ] **会话归属检查已实施**
  - 验证方法: 尝试访问其他用户的会话
  - 测试: 使用用户 A 的 Token 访问用户 B 的 chat_id
  - 预期结果: 404 Not Found

- [ ] **文件归属检查已实施**
  - 验证方法: 尝试下载其他用户的文件
  - 测试: 使用用户 A 的 Token 访问用户 B 的 artifact_id
  - 预期结果: 404 Not Found

- [ ] **KB 空间归属检查已实施**
  - 验证方法: 尝试操作其他用户的 KB 空间
  - 测试: 使用用户 A 的 Token 上传文档到用户 B 的 kb_id
  - 预期结果: 403 Forbidden

- [ ] **越权访问测试通过**
  - 测试命令: `python -m pytest src/backend/tests/::test_authorization`
  - 预期结果: 所有测试通过

---

## 2. 数据安全

### 2.1 传输加密

- [ ] **生产环境强制使用 HTTPS**
  - 验证方法: 检查 Web 服务器配置
  - Nginx 配置: 监听 443 端口，配置 SSL 证书
  - 重定向配置: HTTP (80) 自动重定向到 HTTPS (443)

- [ ] **数据库连接使用 SSL**
  - 验证方法: 检查 `DATABASE_URL` 是否包含 `sslmode=require`
  - 示例: `postgresql://user:pass@host:5432/db?sslmode=require`
  - 检查结果: ________________

### 2.2 存储加密

- [ ] **敏感数据加密存储**
  - 验证方法: 检查数据库中的密码字段是否加密/哈希
  - 用户中心负责: 用户密码由用户中心管理，不存储在本服务
  - API 密钥: 存储在环境变量/密钥管理服务，不存储在数据库

- [ ] **日志中敏感信息已脱敏**
  - 验证方法: 检查日志文件，搜索 "password", "token", "secret"
  - 测试命令: `grep -i "password" logs/*.log | grep -v "***"`
  - 预期结果: 无匹配结果（所有敏感字段已被 *** 替换）

- [ ] **备份数据加密**
  - 验证方法: 检查数据库备份脚本
  - 配置检查: 备份文件使用加密存储（如 S3 server-side encryption）
  - 检查结果: ________________

### 2.3 对象存储安全

- [ ] **S3 Bucket 访问控制正确**
  - 验证方法: 检查 Bucket 策略，确保非公开访问
  - 测试: 尝试无签名访问 S3 对象
  - 预期结果: Access Denied

- [ ] **预签名 URL 有过期时间**
  - 验证方法: 检查 `S3_PRESIGNED_URL_EXPIRY` 配置
  - 推荐值: 900 秒 (15 分钟)
  - 当前配置: ________________

---

## 3. 输入验证

### 3.1 参数验证

- [ ] **所有用户输入使用 Pydantic 验证**
  - 验证方法: 检查所有 API 端点的参数定义
  - 代码检查: 所有 POST/PUT/PATCH 请求使用 Pydantic 模型
  - 抽查端点: `/v1/chats`, `/v1/catalog/kb`, `/files/upload`

- [ ] **文件上传大小限制**
  - 验证方法: 上传超大文件测试
  - 配置检查: `MAX_REQUEST_SIZE` 已设置（默认 10MB）
  - 测试命令: `curl -X POST -d @large_file.bin https://api.example.com/files/upload`
  - 预期结果: 413 Request Entity Too Large

### 3.2 文件类型验证

- [ ] **文件类型白名单已配置**
  - 验证方法: 检查文件上传接口的 MIME 类型验证
  - 代码位置: `src/backend/core/storage.py` 或上传接口
  - 状态: ⚠️ **待实施** (参考 security-review.md P1-1)

- [ ] **可执行文件上传被阻止**
  - 验证方法: 尝试上传 .exe, .sh, .bat 文件
  - 预期结果: 400 Bad Request (Invalid file type)
  - 状态: ⚠️ **待实施**

### 3.3 注入防护

- [ ] **SQL 注入防护（使用 ORM）**
  - 验证方法: 代码审查，确认没有 SQL 字符串拼接
  - 检查命令: `grep -r "execute(" src/backend/core/ | grep -v "# nosec"`
  - 预期结果: 所有查询都使用参数化或 ORM

- [ ] **NoSQL 注入防护**
  - 验证方法: 检查 JSON 字段查询，确保使用安全方法
  - 代码检查: PostgreSQL JSONB 字段查询使用 SQLAlchemy 方法

- [ ] **XSS 防护（CSP headers）**
  - 验证方法: 检查响应头
  - 测试命令: `curl -I https://api.example.com/`
  - 期望头: `Content-Security-Policy: default-src 'self'`
  - 状态: ⚠️ **待实施** (参考 security-review.md P1-2)

- [ ] **命令注入防护**
  - 验证方法: 代码审查，确认没有使用 `os.system`, `subprocess` 执行用户输入
  - 检查命令: `grep -r "os.system\|subprocess.call" --include="*.py" .`
  - 预期结果: 无匹配或仅在安全上下文中使用

- [ ] **路径遍历防护**
  - 验证方法: 尝试使用 `../` 访问文件
  - 测试: `GET /files/../../../etc/passwd`
  - 预期结果: 404 或 400 (无法通过路径访问，仅通过 ID)

---

## 4. 配置安全

### 4.1 秘钥管理

- [ ] **秘钥未硬编码**
  - 验证方法: 运行秘钥检查脚本
  - 测试命令: `python src/backend/scripts/check_secrets.py`
  - 预期结果: ✅ 未发现硬编码的秘钥

- [ ] **秘钥未提交到 Git**
  - 验证方法: 检查 Git 历史
  - 检查命令: `git log --all --full-history --source -- .env`
  - 预期结果: 无 .env 文件提交记录

- [ ] **`.gitignore` 包含秘钥文件**
  - 验证方法: 检查 .gitignore 文件
  - 必须包含: `.env`, `.env.local`, `.env.*.local`
  - 检查命令: `git check-ignore .env`
  - 预期输出: `.env`

### 4.2 环境配置

- [ ] **生产环境 DEBUG=False**
  - 验证方法: 检查环境变量或配置文件
  - 配置检查: `ENV=prod`, 无 `DEBUG=True`
  - FastAPI: `app = FastAPI(debug=False)`

- [ ] **CORS 使用域名白名单**
  - 验证方法: 检查 CORS 配置
  - 配置检查: `CORS_ORIGINS` 不是 `*`
  - 代码位置: `src/backend/api/app.py` 的 `CORSMiddleware` 配置
  - 当前配置: ________________

### 4.3 限流与熔断

- [ ] **限流已启用**
  - 验证方法: 检查限流配置
  - 配置检查: `RATE_LIMIT_ENABLED=true`
  - 测试: 快速发送大量请求，应触发 429 Too Many Requests

- [ ] **熔断器已配置**
  - 验证方法: 检查熔断器配置
  - 配置检查: 用户中心、模型 API、存储服务的熔断阈值已设置
  - 配置项: `CB_USER_CENTER_THRESHOLD`, `CB_MODEL_API_THRESHOLD`, `CB_STORAGE_THRESHOLD`

---

## 5. 审计与合规

### 5.1 审计日志

- [ ] **审计日志覆盖所有关键操作**
  - 验证方法: 检查 `audit_logs` 表是否记录以下操作
    - ✓ 用户登录成功/失败
    - ✓ 会话创建/更新/删除
    - ✓ 文件上传/下载
    - ✓ KB 空间创建/删除
    - ✓ 文档上传/删除
    - ✓ 能力启用/禁用

- [ ] **日志保留策略已配置**
  - 验证方法: 检查数据库备份和归档策略
  - 要求: 审计日志至少保留 90 天
  - 软删除数据至少保留 30 天后归档
  - 检查结果: ________________

- [ ] **可追溯到具体用户操作**
  - 验证方法: 查询审计日志，确认包含 user_id 和 trace_id
  - 测试查询: `SELECT * FROM audit_logs WHERE action = 'chat.session.deleted' LIMIT 10`
  - 预期结果: 每条记录都有 user_id 和 trace_id

### 5.2 异常检测

- [ ] **异常访问告警已启用**
  - 验证方法: 检查告警配置
  - 配置位置: `configs/alerts/`
  - 告警规则: 失败登录 > 5次/分钟, 403 错误率 > 1%, 等
  - 检查结果: ________________

- [ ] **审计日志查询 API 可用**
  - 验证方法: 访问审计日志 API
  - 测试命令: `curl -H "Authorization: Bearer $TOKEN" https://api.example.com/v1/audit/logs`
  - 预期结果: 200 OK, 返回审计日志列表

---

## 6. 依赖安全

### 6.1 依赖管理

- [ ] **所有依赖包版本固定**
  - 验证方法: 检查 requirements.txt
  - 格式检查: 使用 `==` 而非 `>=` 固定版本
  - 示例: `fastapi==0.109.0` ✓,  `fastapi>=0.109.0` ✗

- [ ] **已扫描已知漏洞**
  - 验证方法: 运行安全扫描工具
  - 测试命令: `pip install safety && safety check`
  - 预期结果: 无已知高危漏洞

- [ ] **定期更新依赖包计划**
  - 验证方法: 检查是否有依赖更新流程
  - 要求: 每月检查依赖更新，每季度升级
  - 责任人: ________________

### 6.2 包源安全

- [ ] **使用受信任的包源**
  - 验证方法: 检查 pip 配置
  - 配置检查: 使用官方 PyPI 或私有镜像
  - 配置文件: `~/.pip/pip.conf` 或 `pip.conf`

---

## 7. 网络安全

### 7.1 端口管理

- [ ] **生产环境关闭不必要端口**
  - 验证方法: 端口扫描
  - 测试命令: `nmap -p- <server-ip>`
  - 预期结果: 仅开放 80 (HTTP), 443 (HTTPS), 必要的内网端口

- [ ] **内网端点有 IP 白名单**
  - 验证方法: 尝试从外网访问内网端点
  - 检查端点: `/metrics`, `/health/db`, `/debug`
  - 预期结果: Access Denied (403)

### 7.2 DDoS 防护

- [ ] **DDoS 防护已启用**
  - 验证方法: 检查 WAF 或 CDN 配置
  - 服务提供商: Cloudflare / AWS WAF / Azure Front Door
  - 检查结果: ________________

- [ ] **WAF 规则已配置**
  - 验证方法: 检查 WAF 规则集
  - 基础规则: OWASP Top 10, 地域限制, IP 黑名单
  - 检查结果: ________________

---

## 8. 运维安全

### 8.1 最小权限原则

- [ ] **容器以非 root 用户运行**
  - 验证方法: 检查 Dockerfile
  - 检查命令: `grep "USER" Dockerfile`
  - 预期结果: `USER app` 或其他非 root 用户

- [ ] **数据库用户仅有必要权限**
  - 验证方法: 检查数据库用户权限
  - 要求: 应用账号仅有 SELECT, INSERT, UPDATE, DELETE 权限，无 DROP, CREATE USER
  - 检查命令: `\du` (PostgreSQL)

- [ ] **S3 访问策略最小化**
  - 验证方法: 检查 IAM 策略
  - 要求: 仅允许访问特定 Bucket，仅必要的操作（GetObject, PutObject）

### 8.2 访问控制

- [ ] **生产环境访问需要 VPN**
  - 验证方法: 尝试从公网直接 SSH 到生产服务器
  - 预期结果: Connection refused 或 Timeout

- [ ] **操作有审计记录**
  - 验证方法: 检查服务器操作日志
  - 要求: SSH 登录记录, sudo 操作记录, 数据库操作记录
  - 日志位置: `/var/log/auth.log`, CloudTrail, Azure Activity Log

### 8.3 应急响应

- [ ] **应急响应流程已建立**
  - 验证方法: 检查运维手册
  - 文档位置: `docs/runbook.md`, `docs/incident-response.md`
  - 包含内容: 联系人列表, 升级流程, 回滚步骤

- [ ] **备份恢复已测试**
  - 验证方法: 执行备份恢复演练
  - 频率: 每季度一次
  - 上次演练日期: ________________
  - 恢复时间目标 (RTO): ________________
  - 恢复点目标 (RPO): ________________

---

## 9. 安全响应头

- [ ] **X-Content-Type-Options: nosniff**
  - 验证方法: `curl -I https://api.example.com/ | grep X-Content-Type-Options`
  - 预期结果: `X-Content-Type-Options: nosniff`

- [ ] **X-Frame-Options: DENY**
  - 验证方法: `curl -I https://api.example.com/ | grep X-Frame-Options`
  - 预期结果: `X-Frame-Options: DENY`

- [ ] **Strict-Transport-Security (HSTS)**
  - 验证方法: `curl -I https://api.example.com/ | grep Strict-Transport-Security`
  - 预期结果: `Strict-Transport-Security: max-age=31536000; includeSubDomains`

- [ ] **Content-Security-Policy (CSP)**
  - 验证方法: `curl -I https://api.example.com/ | grep Content-Security-Policy`
  - 预期结果: `Content-Security-Policy: default-src 'self'`

---

## 10. 安全测试

### 10.1 自动化测试

- [ ] **安全自测通过**
  - 测试命令: `python -m pytest src/backend/tests/ -v`
  - 预期结果: 所有测试通过

### 10.2 渗透测试

- [ ] **渗透测试已完成**
  - 执行时间: ________________
  - 测试团队: ________________
  - 发现问题数: ________________
  - 已修复问题数: ________________

---

## 检查结果汇总

### 统计

- 总检查项: ______ 项
- 已通过: ______ 项
- 不适用: ______ 项
- 未通过: ______ 项
- 通过率: ______ %

### 不符合项整改

| 序号 | 检查项 | 问题描述 | 整改措施 | 责任人 | 期限 | 状态 |
|-----|-------|---------|---------|-------|------|------|
| 1   |       |         |         |       |      |      |
| 2   |       |         |         |       |      |      |
| 3   |       |         |         |       |      |      |

### 上线决策

- [ ] **所有 P0 级检查项已通过**
- [ ] **P1 级问题已有整改计划**
- [ ] **风险已评估并接受**

### 签字确认

| 角色 | 姓名 | 签字 | 日期 |
|------|------|------|------|
| 安全负责人 | ___ | ___ | ___ |
| 技术负责人 | ___ | ___ | ___ |
| 产品负责人 | ___ | ___ | ___ |
| 运维负责人 | ___ | ___ | ___ |

---

## 附录

### 相关文档

- [安全评审报告](./security-review.md)
- [秘钥管理策略](./secrets-management.md)
- [合规性指南](./compliance-guide.md)
- [运维手册](./runbook.md)

### 修订历史

| 版本 | 日期 | 修订内容 | 修订人 |
|------|------|---------|-------|
| 1.0  | 2026-02-13 | 初始版本 | System |
