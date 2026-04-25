# 对象存储使用指南

本文档介绍 Jingxin-Agent 的对象存储系统的架构、配置和使用方法。

## 目录

- [架构概述](#架构概述)
- [存储后端](#存储后端)
- [配置指南](#配置指南)
- [API 使用](#api-使用)
- [最佳实践](#最佳实践)
- [故障排查](#故障排查)

## 架构概述

### 设计理念

Jingxin-Agent 的存储系统采用抽象层设计，支持多种存储后端：

```
┌─────────────────────────────────────────┐
│         Application Layer               │
│  (KB Upload, File Download, etc.)       │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│      Storage Abstraction Layer          │
│         (src/backend/core/storage.py)               │
└─────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
┌──────────────────┐   ┌──────────────────┐
│ LocalStorage     │   │  S3Storage       │
│ Backend          │   │  Backend         │
│  (Development)   │   │  (Production)    │
└──────────────────┘   └──────────────────┘
        │                       │
        ▼                       ▼
┌──────────────────┐   ┌──────────────────┐
│ Local Filesystem │   │ S3-Compatible    │
│                  │   │ Object Storage   │
└──────────────────┘   └──────────────────┘
```

### 核心组件

1. **StorageBackend** (抽象基类)
   - 定义统一的存储接口
   - 支持上传、下载、删除、存在性检查
   - 生成预签名 URL

2. **LocalStorageBackend** (开发环境)
   - 使用本地文件系统
   - 快速开发和测试
   - 无需外部依赖

3. **S3StorageBackend** (生产环境)
   - 使用 S3 兼容对象存储
   - 支持 AWS S3、MinIO、阿里云 OSS 等
   - CDN 加速支持
   - 生命周期策略

## 存储后端

### LocalStorageBackend (本地存储)

**适用场景**:
- 本地开发环境
- 单元测试
- 小规模部署

**特点**:
- ✅ 无需配置，开箱即用
- ✅ 零成本
- ✅ 快速访问
- ❌ 不支持分布式
- ❌ 无自动备份
- ❌ 无生命周期管理

**配置**:
```bash
STORAGE_TYPE=local
STORAGE_PATH=./storage  # 存储根目录
```

**目录结构**:
```
./storage/
├── dev/
│   ├── artifacts/
│   │   └── user_001/
│   │       └── chat_123/
│   │           └── 20260213120000_report.pdf
│   └── kb_documents/
│       └── user_002/
│           └── 20260213120000_manual.pdf
└── temp/
    └── ...
```

### S3StorageBackend (对象存储)

**适用场景**:
- 生产环境
- 分布式部署
- 需要 CDN 加速
- 大规模文件存储

**特点**:
- ✅ 高可用性和持久性
- ✅ 自动备份和冗余
- ✅ 生命周期策略
- ✅ CDN 加速
- ✅ 无限扩展
- ❌ 需要配置
- ❌ 有成本

**支持的服务**:
- AWS S3
- MinIO (开源 S3 兼容)
- 阿里云 OSS
- 腾讯云 COS
- 其他 S3 兼容服务

**配置**:
```bash
STORAGE_TYPE=s3
S3_BUCKET=jingxin-prod              # 存储桶名称
S3_REGION=us-east-1                 # 区域
S3_ENDPOINT=https://s3.amazonaws.com # 可选，用于 S3 兼容服务
S3_ACCESS_KEY=AKIA...               # 访问密钥
S3_SECRET_KEY=***                    # 密钥
S3_CDN_DOMAIN=cdn.example.com       # 可选，CDN 域名
S3_PRESIGNED_URL_EXPIRY=900         # 预签名 URL 有效期(秒)
```

## 配置指南

### 环境变量

所有配置通过环境变量设置，支持 `.env` 文件加载。

#### 必需配置

```bash
# 环境标识
ENVIRONMENT=dev|staging|prod

# 存储类型
STORAGE_TYPE=local|s3
```

#### 本地存储配置

```bash
# 存储根目录 (默认: ./storage)
STORAGE_PATH=/path/to/storage
```

#### S3 存储配置

```bash
# 存储桶名称 (必需)
S3_BUCKET=your-bucket-name

# AWS 区域 (默认: us-east-1)
S3_REGION=us-east-1

# S3 端点 (可选，用于 MinIO 等 S3 兼容服务)
S3_ENDPOINT=https://s3.amazonaws.com

# 访问凭证 (可选，未设置时使用 AWS 凭证链)
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=***

# CDN 域名 (可选，用于加速文件访问)
S3_CDN_DOMAIN=cdn.example.com

# 预签名 URL 有效期 (默认: 900 秒 = 15 分钟)
S3_PRESIGNED_URL_EXPIRY=900
```

### 不同环境配置示例

#### 开发环境 (`.env.dev`)

```bash
ENVIRONMENT=dev
STORAGE_TYPE=local
STORAGE_PATH=./storage
```

#### 测试环境 (`.env.staging`)

```bash
ENVIRONMENT=staging
STORAGE_TYPE=s3
S3_BUCKET=jingxin-staging
S3_REGION=us-east-1
S3_ACCESS_KEY=***
S3_SECRET_KEY=***
S3_PRESIGNED_URL_EXPIRY=3600  # 1 小时
```

#### 生产环境 (`.env.prod`)

```bash
ENVIRONMENT=prod
STORAGE_TYPE=s3
S3_BUCKET=jingxin-prod
S3_REGION=us-east-1
S3_ACCESS_KEY=***
S3_SECRET_KEY=***
S3_CDN_DOMAIN=cdn.jingxin.example.com
S3_PRESIGNED_URL_EXPIRY=900  # 15 分钟
```

## API 使用

### 基本用法

```python
from core.storage import get_storage, generate_storage_key

# 获取存储后端实例 (单例)
storage = get_storage()

# 生成标准化存储键
storage_key = generate_storage_key(
    env="prod",
    user_id="user_001",
    category="artifacts",
    filename="report.pdf",
    chat_id="chat_123"  # 可选
)
# 结果: prod/artifacts/user_001/chat_123/20260213120000_report.pdf
```

### 上传文件

#### 上传字节内容

```python
from core.storage import get_storage, generate_storage_key

storage = get_storage()

# 准备数据
content = b"File content here"
storage_key = generate_storage_key(
    env="prod",
    user_id="user_001",
    category="kb_documents",
    filename="document.pdf"
)

# 上传
try:
    url = storage.upload_bytes(content, storage_key)
    print(f"Uploaded to: {url}")
except StorageError as e:
    print(f"Upload failed: {e}")
```

#### 上传本地文件

```python
from core.storage import get_storage

storage = get_storage()
local_file = "/tmp/report.pdf"
storage_key = "prod/artifacts/user_001/report.pdf"

try:
    url = storage.upload(local_file, storage_key)
    print(f"Uploaded to: {url}")
except StorageError as e:
    print(f"Upload failed: {e}")
```

### 下载文件

#### 下载为字节

```python
from core.storage import get_storage

storage = get_storage()
storage_key = "prod/artifacts/user_001/report.pdf"

try:
    content = storage.download_bytes(storage_key)
    # 处理 content
except StorageError as e:
    print(f"Download failed: {e}")
```

#### 下载到本地文件

```python
from core.storage import get_storage

storage = get_storage()
storage_key = "prod/artifacts/user_001/report.pdf"
local_path = "/tmp/downloaded_report.pdf"

try:
    storage.download(storage_key, local_path)
    print(f"Downloaded to: {local_path}")
except StorageError as e:
    print(f"Download failed: {e}")
```

### 生成预签名 URL

预签名 URL 允许客户端直接从存储下载文件，无需经过应用服务器。

```python
from core.storage import get_storage

storage = get_storage()
storage_key = "prod/artifacts/user_001/report.pdf"

try:
    # 生成 15 分钟有效的下载链接
    url = storage.generate_presigned_url(storage_key, expires_in=900)

    # 返回给客户端
    return {"download_url": url, "expires_in": 900}
except StorageError as e:
    print(f"Failed to generate URL: {e}")
```

**S3 示例返回**:
```
https://jingxin-prod.s3.amazonaws.com/prod/artifacts/user_001/report.pdf?
  X-Amz-Algorithm=AWS4-HMAC-SHA256&
  X-Amz-Credential=AKIA.../20260213/us-east-1/s3/aws4_request&
  X-Amz-Date=20260213T120000Z&
  X-Amz-Expires=900&
  X-Amz-SignedHeaders=host&
  X-Amz-Signature=...
```

**本地存储返回**:
```
file:///path/to/storage/prod/artifacts/user_001/report.pdf
```

### 其他操作

#### 检查文件是否存在

```python
from core.storage import get_storage

storage = get_storage()
storage_key = "prod/artifacts/user_001/report.pdf"

if storage.exists(storage_key):
    print("File exists")
else:
    print("File not found")
```

#### 删除文件

```python
from core.storage import get_storage

storage = get_storage()
storage_key = "prod/artifacts/user_001/report.pdf"

try:
    storage.delete(storage_key)
    print("File deleted")
except StorageError as e:
    print(f"Delete failed: {e}")
```

### 在路由中使用

#### 知识库文档上传

```python
from fastapi import APIRouter, UploadFile, File
from core.storage import get_storage, generate_storage_key
from core.auth import get_current_user

router = APIRouter()

@router.post("/v1/catalog/kb/{kb_id}/documents")
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    user = Depends(get_current_user)
):
    # 读取文件内容
    content = await file.read()

    # 生成存储键
    storage_key = generate_storage_key(
        env=os.getenv("ENVIRONMENT", "dev"),
        user_id=user.user_id,
        category="kb_documents",
        filename=file.filename
    )

    # 上传到存储
    storage = get_storage()
    storage_url = storage.upload_bytes(content, storage_key)

    # 保存元数据到数据库
    document = {
        "kb_id": kb_id,
        "filename": file.filename,
        "storage_key": storage_key,
        "size": len(content),
        "mime_type": file.content_type
    }
    # ... save to database

    return {"document_id": "...", "storage_key": storage_key}
```

#### 文件下载

```python
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from core.storage import get_storage

router = APIRouter()

@router.get("/files/{file_id}")
async def download_file(file_id: str, mode: str = "direct"):
    # 从数据库获取文件信息
    file_info = get_file_info_from_db(file_id)
    storage_key = file_info["storage_key"]

    storage = get_storage()

    if mode == "presigned":
        # 返回预签名 URL
        url = storage.generate_presigned_url(storage_key)
        return JSONResponse({
            "url": url,
            "expires_in": 900,
            "filename": file_info["filename"]
        })
    else:
        # 直接下载
        content = storage.download_bytes(storage_key)
        return Response(
            content=content,
            media_type=file_info["mime_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{file_info["filename"]}"'
            }
        )
```

## 存储键命名规范

### 标准格式

```
{environment}/{category}/{user_id}/[{chat_id}/]{timestamp}_{filename}
```

### 各部分说明

- **environment**: 环境标识 (`dev`, `staging`, `prod`)
- **category**: 文件分类
  - `artifacts`: 生成的报告、图表等
  - `kb_documents`: 知识库文档
  - `uploads`: 用户上传的文件
  - `exports`: 导出的文件
  - `temp`: 临时文件
- **user_id**: 用户标识
- **chat_id**: 会话标识 (可选)
- **timestamp**: UTC 时间戳 (格式: `YYYYMMDDHHmmss`)
- **filename**: 原始文件名 (经过安全处理)

### 示例

```python
# 带会话 ID
"prod/artifacts/user_001/chat_123/20260213120000_report.pdf"

# 不带会话 ID
"prod/kb_documents/user_002/20260213120000_manual.pdf"

# 临时文件
"dev/temp/user_003/20260213120000_upload.tmp"
```

### 安全性

文件名会通过 `werkzeug.secure_filename()` 处理，防止路径遍历攻击：

```python
# 危险输入
filename = "../../../etc/passwd"

# 安全输出
safe_filename = secure_filename(filename)
# 结果: "etc_passwd"
```

## 最佳实践

### 1. 选择合适的存储后端

```python
# 开发环境: 使用本地存储
STORAGE_TYPE=local

# 生产环境: 使用 S3
STORAGE_TYPE=s3
```

### 2. 使用环境标识

```python
env = os.getenv("ENVIRONMENT", "dev")
storage_key = generate_storage_key(
    env=env,  # 确保环境隔离
    user_id=user_id,
    category="artifacts",
    filename=filename
)
```

### 3. 文件上传前验证

```python
# 大小限制
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
if len(content) > MAX_FILE_SIZE:
    raise FileTooLargeError(...)

# MIME 类型检查
ALLOWED_TYPES = ["application/pdf", "text/plain", ...]
if mime_type not in ALLOWED_TYPES:
    raise InvalidFileTypeError(...)

# 计算 checksum
import hashlib
checksum = hashlib.sha256(content).hexdigest()
```

### 4. 元数据持久化

始终将文件元数据保存到数据库：

```python
document = {
    "document_id": generate_uuid(),
    "storage_key": storage_key,
    "filename": filename,
    "size_bytes": len(content),
    "mime_type": mime_type,
    "checksum": checksum,
    "uploaded_at": datetime.utcnow()
}
db.save(document)
```

### 5. 错误处理

```python
from core.exceptions import StorageError

try:
    storage.upload_bytes(content, storage_key)
except StorageError as e:
    logger.error(f"Storage upload failed: {e}")
    # 返回友好错误给用户
    raise HTTPException(
        status_code=500,
        detail="Failed to upload file, please try again"
    )
```

### 6. 使用预签名 URL 减轻服务器负载

```python
# 不推荐: 所有下载都经过应用服务器
@app.get("/download/{file_id}")
async def download(file_id: str):
    content = storage.download_bytes(storage_key)
    return Response(content=content)  # 占用服务器带宽

# 推荐: 返回预签名 URL，客户端直接从 S3 下载
@app.get("/download/{file_id}")
async def download(file_id: str):
    url = storage.generate_presigned_url(storage_key)
    return {"url": url}  # 客户端直接从 S3 下载
```

### 7. 定期清理临时文件

```python
# 清理 7 天前的临时文件
from datetime import datetime, timedelta

temp_prefix = f"{env}/temp/"
cutoff_date = datetime.utcnow() - timedelta(days=7)

# 使用 S3 生命周期策略自动清理
# 或编写定时任务
```

### 8. 监控存储使用量

```python
# 记录存储操作到日志
logger.info(f"File uploaded", extra={
    "storage_key": storage_key,
    "size_bytes": len(content),
    "user_id": user_id
})

# 定期统计
# SELECT SUM(size_bytes) FROM kb_documents WHERE user_id = ?
```

## 故障排查

### 问题 1: 本地存储文件找不到

**症状**:
```
StorageError: File not found: dev/artifacts/user_001/report.pdf
```

**排查步骤**:
1. 检查 `STORAGE_PATH` 配置
2. 确认文件路径存在: `ls -la ./storage/dev/artifacts/user_001/`
3. 检查文件权限

**解决方案**:
```bash
# 确保存储目录存在且可写
mkdir -p ./storage
chmod 755 ./storage
```

### 问题 2: S3 连接失败

**症状**:
```
StorageError: Storage init failed: Could not connect to the endpoint URL
```

**排查步骤**:
1. 检查 `S3_ENDPOINT` 配置是否正确
2. 检查网络连通性: `curl https://s3.amazonaws.com`
3. 检查凭证是否有效

**解决方案**:
```bash
# 测试 AWS 凭证
aws s3 ls s3://your-bucket --region us-east-1

# 如果使用 MinIO
aws s3 ls s3://your-bucket --endpoint-url=https://minio.example.com
```

### 问题 3: 权限不足

**症状**:
```
StorageError: An error occurred (AccessDenied) when calling the PutObject operation
```

**排查步骤**:
1. 检查 IAM 权限
2. 检查 Bucket 策略
3. 检查 CORS 设置 (如果从浏览器访问)

**解决方案**:

IAM 策略示例:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket",
        "arn:aws:s3:::your-bucket/*"
      ]
    }
  ]
}
```

### 问题 4: 预签名 URL 无法访问

**症状**:
客户端访问预签名 URL 返回 403 Forbidden

**排查步骤**:
1. 检查 URL 是否过期
2. 检查系统时间是否同步
3. 检查 Bucket 的公开访问设置

**解决方案**:
```bash
# 检查系统时间
date -u

# 同步时间
sudo ntpdate pool.ntp.org

# 检查 Bucket 的 CORS 配置
aws s3api get-bucket-cors --bucket your-bucket
```

### 问题 5: CDN 域名配置无效

**症状**:
预签名 URL 仍使用 S3 域名而不是 CDN 域名

**排查步骤**:
1. 检查 `S3_CDN_DOMAIN` 配置
2. 检查 CDN 是否正确指向 S3

**解决方案**:
```bash
# 确保配置正确
S3_CDN_DOMAIN=cdn.example.com  # 不要包含 https://

# 测试 CDN
curl -I https://cdn.example.com/prod/artifacts/test.pdf
```

### 问题 6: 文件上传后无法下载

**症状**:
文件上传成功，但下载时找不到

**排查步骤**:
1. 检查数据库中的 `storage_key` 是否正确
2. 使用 S3 CLI 验证文件是否存在
3. 检查下载接口的存储键拼接逻辑

**解决方案**:
```bash
# 列出 S3 中的文件
aws s3 ls s3://your-bucket/prod/artifacts/user_001/ --recursive

# 检查数据库记录
SELECT storage_key FROM kb_documents WHERE document_id = '...'
```

## 参考资料

- [AWS S3 文档](https://docs.aws.amazon.com/s3/)
- [MinIO 文档](https://min.io/docs/)
- [Boto3 文档](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [存储生命周期策略](./storage-lifecycle-policy.md)
- [环境配置清单](./environment-config.md)

## 变更历史

- **2026-02-13**: 初始版本，完成存储抽象层设计和实现
