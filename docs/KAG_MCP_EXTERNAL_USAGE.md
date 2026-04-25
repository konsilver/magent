# KAG MCP 外部接入说明

- 用什么地址接入
- 能调用什么能力
- 怎么验证接入成功

## 1. 接入地址

公网 MCP SSE 地址：

```text
https://ai-zhilian.quant-chi.com/mcp/sse
```

外部网络如果只能通过域名访问，请直接使用这个地址。

## 2. 当前可用能力

当前 MCP 服务提供两个工具：

- `qa-pipeline`
  用于直接对知识库发起问答，返回最终答案。
- `kb-retrieve`
  用于检索知识库中的证据与引用，返回摘要和参考结果。

## 3. 客户端配置示例

### Cursor

```json
{
  "mcpServers": {
    "zhilian-kag": {
      "url": "https://ai-zhilian.quant-chi.com/mcp/sse"
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "zhilian-kag": {
      "url": "https://ai-zhilian.quant-chi.com/mcp/sse"
    }
  }
}
```

## 4. 最小验证方法

### 方法一：先验证 SSE 入口

执行：

```bash
curl -i --http1.1 --no-buffer --max-time 10 https://ai-zhilian.quant-chi.com/mcp/sse
```

看到下面这类结果就说明入口是通的：

```text
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8

event: endpoint
data: /messages/?session_id=...
```

说明：

- `curl` 最后因为 `--max-time` 超时退出是正常的
- SSE 是长连接，不会主动结束
- 关键是要拿到 `200 + text/event-stream + event: endpoint`

### 方法二：验证回调路径可达

执行：

```bash
curl -i --http1.1 --max-time 8 'https://ai-zhilian.quant-chi.com/messages/?session_id=test'
```

如果返回类似：

```text
HTTP/1.1 400 Bad Request
Invalid session ID
```

说明 `/messages/` 这条路径已经通到 MCP 服务，而不是被静态页面或其他代理层拦截。

### 方法三：在 MCP 客户端里验证

接入 MCP 后，建议按下面顺序验证：

1. 查看工具列表
   预期能看到：
   `qa-pipeline`
   `kb-retrieve`
2. 实际发起一次简单调用

可以测试：

```text
请根据知识库回答：当前系统提供哪些 MCP 工具？
```

或者：

```text
请检索“新能源电池材料”相关知识，并返回引用证据。
```

## 5. 成功标准

满足下面条件即可认为外部接入成功：

- `https://ai-zhilian.quant-chi.com/mcp/sse` 能返回 `200 OK`
- 返回头里有 `Content-Type: text/event-stream`
- 返回体里有 `event: endpoint`
- MCP 客户端能看到 `qa-pipeline` 与 `kb-retrieve`
- 至少一个工具能成功执行并返回结果

## 6. 常见现象

### `curl` 超时是不是失败

不一定。

如果已经收到了：

- `200 OK`
- `text/event-stream`
- `event: endpoint`

那就说明 SSE 已经建立，`curl` 的超时通常只是因为它到达了你设置的 `--max-time`。

### 为什么会返回 `/messages/?session_id=...`

这是正常的。

MCP SSE 会先建立一个长连接，再通过对应的 `/messages/` 路径继续完成会话消息交互。

## 7. 推荐对接方式

如果你是外部系统或外部智能体，直接按下面这个地址配置即可：

```text
https://ai-zhilian.quant-chi.com/mcp/sse
```
