# 统一错误码定义

## 错误码规范

所有错误码采用 **5位数字** 格式，分为以下类别：

```
1xxxx - 成功状态
2xxxx - 客户端请求错误
3xxxx - 鉴权与权限错误
4xxxx - 资源相关错误
5xxxx - 服务器与上游错误
```

## 1xxxx - 成功状态

| 错误码 | HTTP状态码 | 含义 | 说明 |
|--------|------------|------|------|
| 10000 | 200 | Success | 请求成功 |
| 10001 | 201 | Created | 资源创建成功 |
| 10002 | 200 | Partial Success | 批量操作部分成功 |
| 10003 | 202 | Accepted | 请求已接受,异步处理中 |

## 2xxxx - 客户端请求错误

### 20xxx - 通用参数错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 20001 | 400 | Invalid Parameters | 请求参数不合法 |
| 20002 | 400 | Missing Required Field | 缺少必填字段 |
| 20003 | 400 | Invalid Field Format | 字段格式错误 |
| 20004 | 400 | Invalid Field Value | 字段值不在允许范围内 |
| 20005 | 400 | Request Too Large | 请求体过大 |
| 20006 | 400 | Invalid JSON | JSON 格式错误 |
| 20007 | 400 | Invalid Query String | URL 查询参数错误 |

**示例**:
```json
{
  "code": 20001,
  "message": "Invalid request parameters",
  "data": {
    "errors": [
      {"field": "message", "reason": "Message cannot be empty"},
      {"field": "model_name", "reason": "Model 'gpt5' is not supported"}
    ]
  }
}
```

### 21xxx - 文件上传错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 21001 | 400 | File Too Large | 文件大小超过限制 |
| 21002 | 400 | Invalid File Type | 文件类型不支持 |
| 21003 | 400 | File Name Too Long | 文件名过长 |
| 21004 | 400 | Malformed File | 文件损坏或格式错误 |
| 21005 | 400 | Virus Detected | 文件包含病毒 |

**示例**:
```json
{
  "code": 21001,
  "message": "File too large",
  "data": {
    "max_size": 10485760,
    "actual_size": 15728640,
    "unit": "bytes"
  }
}
```

### 22xxx - 业务逻辑错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 22001 | 400 | Invalid Operation | 操作不合法 |
| 22002 | 400 | Operation Not Allowed | 当前状态不允许此操作 |
| 22003 | 400 | Duplicate Operation | 重复操作 |
| 22004 | 400 | Circular Dependency | 循环依赖 |

**示例**:
```json
{
  "code": 22002,
  "message": "Operation not allowed",
  "data": {
    "reason": "Cannot delete a chat session with active streaming",
    "chat_id": "chat_001",
    "current_state": "streaming"
  }
}
```

## 3xxxx - 鉴权与权限错误

### 30xxx - 鉴权错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 30001 | 401 | Authentication Required | 缺少或无效的鉴权凭证 |
| 30002 | 401 | Invalid Token | Token 无效 |
| 30003 | 401 | Token Expired | Token 已过期 |
| 30004 | 401 | Token Revoked | Token 已被撤销 |
| 30005 | 401 | User Not Found | 用户不存在 |
| 30006 | 401 | User Disabled | 用户已被禁用 |

**示例**:
```json
{
  "code": 30003,
  "message": "Authentication token expired",
  "data": {
    "expired_at": "2026-02-13T09:00:00Z",
    "hint": "Please login again to get a new token"
  }
}
```

### 31xxx - 权限错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 31001 | 403 | Access Denied | 无权访问资源 |
| 31002 | 403 | Insufficient Permissions | 权限不足 |
| 31003 | 403 | Resource Ownership Required | 仅资源所有者可访问 |
| 31004 | 403 | Admin Only | 仅管理员可访问 |
| 31005 | 403 | IP Not Allowed | IP 地址不在白名单 |

**示例**:
```json
{
  "code": 31003,
  "message": "Access denied",
  "data": {
    "reason": "Only the resource owner can perform this operation",
    "resource_type": "chat_session",
    "resource_id": "chat_001",
    "resource_owner": "user_002",
    "current_user": "user_001"
  }
}
```

## 4xxxx - 资源相关错误

### 40xxx - 资源状态错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 40001 | 404 | Resource Not Found | 资源不存在 |
| 40002 | 404 | Endpoint Not Found | API 端点不存在 |
| 40003 | 410 | Resource Deleted | 资源已被删除 |
| 40004 | 410 | Resource Expired | 资源已过期 |

**示例**:
```json
{
  "code": 40001,
  "message": "Resource not found",
  "data": {
    "resource_type": "chat_session",
    "resource_id": "chat_999",
    "hint": "The chat session may have been deleted"
  }
}
```

### 41xxx - 资源冲突错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 41001 | 409 | Resource Already Exists | 资源已存在 |
| 41002 | 409 | Concurrent Modification | 并发修改冲突 |
| 41003 | 409 | Version Conflict | 版本冲突 |
| 41004 | 409 | State Conflict | 状态冲突 |

**示例**:
```json
{
  "code": 41002,
  "message": "Concurrent modification detected",
  "data": {
    "resource_type": "chat_session",
    "resource_id": "chat_001",
    "expected_version": 5,
    "actual_version": 7,
    "hint": "Please refresh and try again"
  }
}
```

### 42xxx - 限流与配额错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 42001 | 429 | Rate Limit Exceeded | 请求频率超限 |
| 42002 | 429 | Quota Exceeded | 配额用尽 |
| 42003 | 429 | Concurrent Limit Exceeded | 并发数超限 |
| 42004 | 429 | Daily Limit Exceeded | 每日限额用尽 |

**示例**:
```json
{
  "code": 42001,
  "message": "Rate limit exceeded",
  "data": {
    "limit": "50 requests per minute",
    "current_usage": 51,
    "retry_after": 45,
    "reset_at": "2026-02-13T10:15:00Z"
  }
}
```

## 5xxxx - 服务器与上游错误

### 50xxx - 服务器内部错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 50001 | 500 | Internal Server Error | 服务器内部错误 |
| 50002 | 500 | Database Error | 数据库错误 |
| 50003 | 500 | Configuration Error | 配置错误 |
| 50004 | 500 | Unexpected Error | 未预期的错误 |

**示例**:
```json
{
  "code": 50002,
  "message": "Database error",
  "data": {
    "error_type": "ConnectionError",
    "hint": "Please try again later or contact support"
  }
}
```

### 51xxx - 存储错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 51001 | 500 | Storage Error | 对象存储错误 |
| 51002 | 500 | Storage Upload Failed | 上传失败 |
| 51003 | 500 | Storage Download Failed | 下载失败 |
| 51004 | 500 | Storage Quota Exceeded | 存储空间不足 |
| 51005 | 503 | Storage Unavailable | 存储服务不可用 |

**示例**:
```json
{
  "code": 51002,
  "message": "Storage upload failed",
  "data": {
    "storage_type": "s3",
    "bucket": "jingxin-prod",
    "error": "Connection timeout",
    "hint": "Please try uploading again"
  }
}
```

### 52xxx - 上游服务错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 52001 | 502 | User Center Error | 用户中心错误 |
| 52002 | 502 | User Center Unavailable | 用户中心不可用 |
| 52101 | 502 | Model API Error | 模型服务错误 |
| 52102 | 502 | Model API Unavailable | 模型服务不可用 |
| 52103 | 400 | Model API Rate Limited | 模型服务限流 |
| 52104 | 400 | Invalid Model Parameters | 模型参数错误 |
| 52201 | 502 | MCP Server Error | MCP 服务错误 |
| 52202 | 502 | MCP Server Unavailable | MCP 服务不可用 |

**示例**:
```json
{
  "code": 52101,
  "message": "Model API error",
  "data": {
    "model": "gpt-4",
    "provider": "openai",
    "error": "insufficient_quota",
    "hint": "Model quota exceeded, please try a different model"
  }
}
```

### 53xxx - 超时错误

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 53001 | 504 | Request Timeout | 请求超时 |
| 53002 | 504 | Database Timeout | 数据库查询超时 |
| 53003 | 504 | Model API Timeout | 模型服务超时 |
| 53004 | 504 | User Center Timeout | 用户中心超时 |
| 53005 | 504 | Storage Timeout | 存储服务超时 |

**示例**:
```json
{
  "code": 53003,
  "message": "Model API timeout",
  "data": {
    "model": "gpt-4",
    "timeout": "60s",
    "hint": "The model took too long to respond, please try again"
  }
}
```

### 54xxx - 服务不可用

| 错误码 | HTTP状态码 | 含义 | 场景 |
|--------|------------|------|------|
| 54001 | 503 | Service Unavailable | 服务不可用 |
| 54002 | 503 | Service Maintenance | 服务维护中 |
| 54003 | 503 | Service Overloaded | 服务过载 |
| 54004 | 503 | Circuit Breaker Open | 熔断器开启 |

**示例**:
```json
{
  "code": 54002,
  "message": "Service under maintenance",
  "data": {
    "scheduled_maintenance": true,
    "estimated_completion": "2026-02-13T12:00:00Z",
    "hint": "Service will be back online soon"
  }
}
```

## 错误码使用指南

### 1. 选择合适的错误码
```python
# ❌ 错误 - 使用通用错误码
raise APIError(code=50001, message="Error")

# ✅ 正确 - 使用具体错误码
raise ResourceNotFoundError(
    code=40001,
    message="Chat session not found",
    data={"chat_id": chat_id}
)
```

### 2. 提供详细的 data 字段
```python
# ❌ 错误 - 缺少上下文
{"code": 31003, "message": "Access denied"}

# ✅ 正确 - 包含详细信息
{
    "code": 31003,
    "message": "Access denied",
    "data": {
        "reason": "Only the resource owner can perform this operation",
        "resource_id": "chat_001",
        "current_user": "user_001",
        "resource_owner": "user_002"
    }
}
```

### 3. 一致的错误响应
```python
from fastapi import HTTPException

def handle_error(error: Exception, trace_id: str):
    if isinstance(error, ResourceNotFoundError):
        return {
            "code": 40001,
            "message": str(error),
            "data": error.data,
            "trace_id": trace_id
        }, 404
    # ...
```

### 4. 日志记录
```python
logger.error(
    "Resource not found",
    extra={
        "code": 40001,
        "trace_id": trace_id,
        "user_id": user_id,
        "resource_type": "chat_session",
        "resource_id": chat_id
    }
)
```

## 降级与兜底策略

### 用户中心降级
```python
# 用户中心不可用时
if user_center_error:
    return {
        "code": 52002,
        "message": "User center temporarily unavailable",
        "data": {
            "fallback_mode": True,
            "hint": "Some features may be limited"
        }
    }
```

### 模型服务降级
```python
# 主模型不可用，切换到备用模型
if primary_model_error:
    logger.warning(f"Primary model {model} failed, fallback to {fallback_model}")
    # 使用 fallback_model，不抛出错误
```

### 存储服务降级
```python
# S3 不可用，暂存到本地
if storage_error:
    save_to_local_temp(file)
    schedule_upload_retry(file_id)
    # 返回成功，后台异步重试上传
```

## 客户端错误处理建议

```typescript
async function handleApiError(response: Response) {
  const error = await response.json();

  switch (error.code) {
    case 30003: // Token expired
      await refreshToken();
      return retry();

    case 31003: // Access denied
      showPermissionDialog(error.data);
      break;

    case 40001: // Not found
      redirectTo404Page();
      break;

    case 42001: // Rate limited
      const retryAfter = error.data.retry_after;
      await sleep(retryAfter * 1000);
      return retry();

    case 52101: // Model error
      showModelErrorDialog(error.data.hint);
      break;

    default:
      showGenericError(error.message);
  }
}
```

## 监控与告警

### 错误率监控
- **5xxxx 错误率** > 1%: P1 告警
- **4xxxx 错误率** > 10%: P2 告警
- **特定错误码** (如 52101) 突增: P1 告警

### 告警规则示例
```yaml
- name: high_5xx_error_rate
  expr: rate(api_errors{code=~"5.*"}[5m]) > 0.01
  severity: P1
  message: "5xx error rate exceeded 1%"

- name: model_api_errors
  expr: rate(api_errors{code="52101"}[5m]) > 0.05
  severity: P1
  message: "Model API error rate exceeded 5%"
```
