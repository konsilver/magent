# 秘钥管理策略

## 概述

本文档定义了 Jingxin-Agent 项目的秘钥管理策略、最佳实践和操作规范。

**核心原则**:
- 永不将秘钥硬编码到源代码
- 永不将秘钥提交到 Git 仓库
- 使用环境变量或密钥管理服务
- 定期轮转秘钥
- 最小权限原则

---

## 1. 禁止的做法

### ❌ 硬编码秘钥到代码

**错误示例**:
```python
# 不要这样做！
DATABASE_URL = "postgresql://user:password123@localhost:5432/mydb"
API_KEY = "sk-1234567890abcdef"
SECRET_KEY = "my-secret-key-12345"
```

**正确做法**:
```python
import os

DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
```

### ❌ 将秘钥提交到 Git

**风险**:
- 秘钥永久存在于 Git 历史中
- 任何能访问仓库的人都能看到
- 删除提交后仍可从历史中恢复

**预防措施**:
1. 使用 `.gitignore` 忽略秘钥文件
2. 使用 `.env.example` 作为模板（不含实际值）
3. 使用 `scripts/check_secrets.py` 在提交前检查
4. 配置 pre-commit hook 自动检查

### ❌ 在日志中输出秘钥

**错误示例**:
```python
logger.info(f"Connecting to database: {DATABASE_URL}")  # 可能包含密码
logger.debug(f"API Key: {API_KEY}")  # 暴露秘钥
```

**正确做法**:
```python
from core.data_masking import mask_api_key

logger.info(f"Connecting to database: {DATABASE_URL.split('@')[1]}")  # 只记录主机
logger.debug(f"API Key: {mask_api_key(API_KEY)}")  # 记录掩码后的值
```

---

## 2. 推荐的做法

### ✅ 使用环境变量

**开发环境** - 使用 `.env` 文件:

1. 复制 `.env.example` 到 `.env`:
   ```bash
   cp .env.example .env
   ```

2. 编辑 `.env` 填入实际值:
   ```bash
   DATABASE_URL="postgresql://user:password@localhost:5432/mydb"
   API_KEY="sk-your-actual-api-key"
   ```

3. 确保 `.env` 在 `.gitignore` 中:
   ```gitignore
   # .gitignore
   .env
   .env.local
   .env.*.local
   ```

4. 应用代码使用 `python-dotenv` 加载:
   ```python
   from dotenv import load_dotenv
   import os

   load_dotenv()  # 从 .env 文件加载环境变量
   DATABASE_URL = os.getenv("DATABASE_URL")
   ```

**生产环境** - 使用系统环境变量:

1. Docker 容器环境变量:
   ```yaml
   # docker-compose.yml
   services:
     app:
       environment:
         - DATABASE_URL=${DATABASE_URL}
         - API_KEY=${API_KEY}
   ```

2. Kubernetes Secret:
   ```yaml
   # k8s-secret.yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: jingxin-agent-secrets
   type: Opaque
   data:
     database_url: <base64-encoded-value>
     api_key: <base64-encoded-value>
   ```

3. systemd 服务文件:
   ```ini
   # /etc/systemd/system/jingxin-agent.service
   [Service]
   Environment="DATABASE_URL=postgresql://..."
   Environment="API_KEY=sk-..."
   ```

### ✅ 使用密钥管理服务

**AWS Secrets Manager**:

```python
import boto3
import json

def get_secret(secret_name):
    """从 AWS Secrets Manager 获取秘钥"""
    client = boto3.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])

# 使用
secrets = get_secret('jingxin-agent/prod')
DATABASE_URL = secrets['database_url']
API_KEY = secrets['api_key']
```

**HashiCorp Vault**:

```python
import hvac

def get_secret_from_vault(path):
    """从 HashiCorp Vault 获取秘钥"""
    client = hvac.Client(url='https://vault.example.com')
    client.token = os.getenv('VAULT_TOKEN')
    secret = client.secrets.kv.v2.read_secret_version(path=path)
    return secret['data']['data']

# 使用
secrets = get_secret_from_vault('jingxin-agent/prod')
DATABASE_URL = secrets['database_url']
```

**Azure Key Vault**:

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

def get_azure_secret(vault_url, secret_name):
    """从 Azure Key Vault 获取秘钥"""
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    return client.get_secret(secret_name).value

# 使用
DATABASE_URL = get_azure_secret(
    'https://jingxin-vault.vault.azure.net',
    'database-url'
)
```

### ✅ 使用 `.env.example` 作为模板

`.env.example` 文件应该:
- ✅ 包含所有需要的环境变量名称
- ✅ 包含描述性的占位符值
- ✅ 包含注释说明每个变量的用途
- ❌ 不包含任何实际的秘钥值
- ✅ 提交到 Git 仓库

**示例**:
```bash
# Database Configuration
DATABASE_URL="postgresql://user:password@localhost:5432/jingxin_agent"

# API Keys
TAVILY_API_KEY="your-tavily-api-key"
DIFY_API_KEY="your-dify-api-key"

# User Center Authentication
AUTH_API_URL="https://user-center.example.com/api"
AUTH_API_TIMEOUT="5"

# S3 Storage (for production)
# S3_BUCKET="your-bucket-name"
# S3_ACCESS_KEY="your-access-key"
# S3_SECRET_KEY="your-secret-key"
```

---

## 3. 秘钥轮转策略

### 为什么要轮转秘钥？

- **降低泄露风险**: 即使秘钥泄露，影响时间窗口有限
- **合规要求**: 许多安全标准要求定期轮转秘钥
- **限制访问**: 离职员工的秘钥自动失效

### 轮转周期

| 秘钥类型 | 轮转周期 | 优先级 |
|---------|---------|-------|
| 数据库密码 | 90 天 | 高 |
| API 密钥 | 90 天 | 高 |
| 加密密钥 | 180 天 | 中 |
| 服务账号密钥 | 90 天 | 高 |
| 开发环境密钥 | 180 天 | 低 |

### 轮转步骤

**1. 准备阶段**:
- [ ] 确认新秘钥生成方法
- [ ] 准备回滚计划
- [ ] 通知相关团队成员
- [ ] 选择低峰时段执行

**2. 执行轮转**:

以数据库密码为例:

```bash
# 1. 在数据库中创建新密码
ALTER USER jingxin_agent WITH PASSWORD 'new_password_here';

# 2. 更新密钥管理服务中的秘钥（如果使用）
aws secretsmanager update-secret \
  --secret-id jingxin-agent/prod/database-password \
  --secret-string 'new_password_here'

# 3. 更新环境变量（根据部署方式）
# - Kubernetes: 更新 Secret 并重启 Pod
# - Docker: 更新 docker-compose.yml 并重启容器
# - systemd: 更新服务文件并重启服务

# 4. 验证新秘钥工作正常
curl -H "Authorization: Bearer $NEW_TOKEN" https://api.example.com/health

# 5. 等待观察期（24-48小时）
# 6. 撤销旧秘钥（如果可能）
```

**3. 验证阶段**:
- [ ] 确认服务正常运行
- [ ] 检查错误日志
- [ ] 验证所有依赖服务连接正常
- [ ] 测试关键业务流程

**4. 清理阶段**:
- [ ] 撤销旧秘钥（如果服务支持）
- [ ] 更新文档记录
- [ ] 归档轮转日志

### 回滚计划

如果轮转后出现问题:

```bash
# 1. 立即恢复旧秘钥
aws secretsmanager update-secret \
  --secret-id jingxin-agent/prod/database-password \
  --secret-string 'old_password_here'

# 2. 重启服务加载旧秘钥
kubectl rollout restart deployment/jingxin-agent

# 3. 记录回滚原因
# 4. 调查失败原因
# 5. 修复问题后重新尝试
```

---

## 4. 秘钥泄露应急响应

### 发现秘钥泄露时的处理流程

**立即行动** (< 1 小时):
1. **撤销泄露的秘钥**
   - 立即在上游服务撤销
   - 生成新秘钥
   - 更新所有使用该秘钥的服务

2. **评估影响范围**
   - 检查访问日志，确认是否有未授权访问
   - 确定泄露时间和可能的受影响数据
   - 通知安全团队

3. **更换秘钥**
   - 按照轮转流程更换秘钥
   - 加急处理，跳过观察期

**短期行动** (< 24 小时):
1. **清理泄露源**
   - 如果是 Git 提交，使用 `git-filter-branch` 或 `BFG Repo-Cleaner` 清理
   - 如果是日志，删除相关日志文件
   - 如果是第三方，联系对方删除

2. **审计访问**
   - 检查所有使用该秘钥的访问记录
   - 确认是否有数据泄露
   - 生成审计报告

3. **通知相关方**
   - 通知管理层
   - 如果涉及用户数据，准备通知用户（根据合规要求）

**长期改进** (< 1 周):
1. **根因分析**
   - 分析泄露原因
   - 识别流程漏洞
   - 制定改进措施

2. **预防措施**
   - 更新开发流程
   - 加强培训
   - 改进工具和自动化

---

## 5. 秘钥检查工具

### 使用 `check_secrets.py` 脚本

在提交代码前运行:

```bash
python scripts/check_secrets.py
```

输出示例:
```
🔍 Checking for hardcoded secrets in Python files...

✅ Checked: api/app.py
✅ Checked: core/auth.py
⚠️  Found potential secret in: config.py
   Line 15: api_key = "sk-1234567890abcdef"
   Pattern: api_key\s*=\s*["'][A-Za-z0-9]{20,}["']

❌ FAILED: Found 1 potential secret(s)

Please review and remove any hardcoded secrets before committing.
```

### 配置 Git Pre-commit Hook

创建 `.git/hooks/pre-commit`:

```bash
#!/bin/bash

# 运行秘钥检查
python scripts/check_secrets.py

# 如果检查失败，阻止提交
if [ $? -ne 0 ]; then
    echo "❌ Commit blocked: Secrets detected"
    exit 1
fi

echo "✅ No secrets detected, proceeding with commit"
```

使脚本可执行:

```bash
chmod +x .git/hooks/pre-commit
```

### 使用 git-secrets 工具

安装:
```bash
# macOS
brew install git-secrets

# Linux
git clone https://github.com/awslabs/git-secrets.git
cd git-secrets
sudo make install
```

配置:
```bash
cd /path/to/jingxin-agent
git secrets --install
git secrets --register-aws  # AWS 秘钥模式
git secrets --add 'sk-[A-Za-z0-9]{20,}'  # OpenAI API 密钥
git secrets --add 'password\s*=\s*["\'][^"\']+["\']'
```

---

## 6. 各环境秘钥管理

### 开发环境

**方法**: `.env` 文件

**配置**:
```bash
# 1. 复制模板
cp .env.example .env

# 2. 填入开发环境的秘钥
# 使用测试账号、沙箱 API 等

# 3. 绝不提交 .env 文件
git check-ignore .env  # 应该输出 .env
```

### Staging 环境

**方法**: Docker Compose + 环境变量文件

**配置**:
```bash
# 1. 创建 .env.staging（不提交到 Git）
DATABASE_URL="postgresql://..."
API_KEY="..."

# 2. 在 docker-compose.staging.yml 中引用
services:
  app:
    env_file:
      - .env.staging
```

### 生产环境

**方法**: Kubernetes Secrets + External Secrets Operator

**配置**:
```yaml
# 1. 创建 Kubernetes Secret
apiVersion: v1
kind: Secret
metadata:
  name: jingxin-agent-secrets
  namespace: production
type: Opaque
stringData:
  database-url: "postgresql://..."
  api-key: "..."

---
# 2. 在 Deployment 中使用
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jingxin-agent
spec:
  template:
    spec:
      containers:
      - name: app
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: jingxin-agent-secrets
              key: database-url
```

---

## 7. 最佳实践总结

### ✅ DO

- ✅ 使用环境变量存储秘钥
- ✅ 使用密钥管理服务（AWS Secrets Manager, Vault）
- ✅ 定期轮转秘钥（90 天周期）
- ✅ 使用 `.env.example` 作为模板
- ✅ 在日志中掩码秘钥
- ✅ 使用最小权限原则
- ✅ 提交前检查秘钥泄露
- ✅ 为不同环境使用不同秘钥

### ❌ DON'T

- ❌ 硬编码秘钥到源代码
- ❌ 将秘钥提交到 Git
- ❌ 在日志中输出完整秘钥
- ❌ 通过邮件或聊天工具发送秘钥
- ❌ 在生产环境使用默认密码
- ❌ 多个环境共用同一秘钥
- ❌ 忽略秘钥轮转
- ❌ 离职员工仍可访问秘钥

---

## 8. 相关资源

### 工具

- [git-secrets](https://github.com/awslabs/git-secrets) - 防止提交秘钥
- [detect-secrets](https://github.com/Yelp/detect-secrets) - 检测代码中的秘钥
- [truffleHog](https://github.com/trufflesecurity/truffleHog) - 扫描 Git 历史中的秘钥
- [gitleaks](https://github.com/gitleaks/gitleaks) - 快速秘钥扫描工具

### 密钥管理服务

- [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/)
- [HashiCorp Vault](https://www.vaultproject.io/)
- [Azure Key Vault](https://azure.microsoft.com/en-us/services/key-vault/)
- [Google Cloud Secret Manager](https://cloud.google.com/secret-manager)

### 文档

- [OWASP 秘钥管理指南](https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html)
- [CWE-798: 硬编码凭据](https://cwe.mitre.org/data/definitions/798.html)

---

## 附录: 秘钥清单

以下是 Jingxin-Agent 使用的所有秘钥：

| 秘钥名称 | 环境变量 | 用途 | 轮转周期 | 优先级 |
|---------|---------|------|---------|-------|
| 数据库密码 | `DATABASE_URL` | PostgreSQL 连接 | 90天 | P0 |
| Dify API Key | `DIFY_API_KEY` | Dify 服务调用 | 90天 | P0 |
| Tavily API Key | `TAVILY_API_KEY` | 搜索服务 | 90天 | P1 |
| 行业数据 Token | `INDUSTRY_AUTH_TOKEN` | 行业数据 API | 90天 | P1 |
| S3 Access Key | `S3_ACCESS_KEY` | 对象存储访问 | 90天 | P0 |
| S3 Secret Key | `S3_SECRET_KEY` | 对象存储访问 | 90天 | P0 |
| 用户中心 Token | `AUTH_API_URL` | 用户认证 | 由用户中心管理 | P0 |

**注**: P0 = 关键，P1 = 重要，P2 = 一般
