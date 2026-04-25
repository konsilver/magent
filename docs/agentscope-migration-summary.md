# LangChain → AgentScope 迁移总结

> 迁移日期：2026-03-17 | 分支：`agentscope` | 详细迁移指南：[langchain-to-agentscope-migration.md](./langchain-to-agentscope-migration.md)

## 一、迁移概述

本次迁移将后端 AI Agent 框架从 **LangChain 全家桶**（langchain、langchain-openai、langgraph、langchain-mcp-adapters 等 5 个包）替换为 **AgentScope** 单一框架（`agentscope>=1.0.0`）。

核心成果：
- **前端零改动** —— SSE 事件格式在 `workflow.py` 层完全兼容
- **依赖大幅简化** —— 5 个 LangChain 包 → 1 个 AgentScope 包
- **约 2,800 行代码变更**，其中新建 4 个模块、重写 3 个核心文件

---

## 二、核心改动对照

### 2.1 依赖变更

| 移除 | 新增 |
|------|------|
| `langchain>=0.3.0` | `agentscope>=1.0.0` |
| `langchain-openai>=0.2.0` | — |
| `langgraph>=0.2.0` | — |
| `langchain-mcp-adapters>=0.1.0` | — |
| `langchain-experimental>=0.3.0` | — |

### 2.2 组件映射

| 功能 | LangChain 方案 | AgentScope 方案 | 所在文件 |
|------|---------------|----------------|---------|
| LLM 模型调用 | `ChatOpenAI` | `OpenAIChatModel` | `core/llm/chat_models.py` |
| Agent 创建 | `create_agent()` 工厂函数 | `ReActAgent` 类实例化 | `core/llm/agent_factory.py` |
| MCP 工具加载 | `MultiServerMCPClient` | `StdIOStatefulClient` + `Toolkit` | `core/llm/mcp_manager.py` |
| 中间件 / 预处理 | `AgentMiddleware` 链 | Hook 系统 (`pre_reply`) | `core/llm/hooks.py` |
| 流式输出 | `agent.astream(stream_mode=...)` | `msg_queue` + `StreamingAgent` | `routing/streaming.py` |
| 上下文压缩 | `SummarizationMiddleware` | `CompressionConfig` | `core/llm/agent_factory.py` |
| 消息格式 | Python dict | `Msg` 对象（内部）/ dict（外部） | `core/llm/message_compat.py` |

### 2.3 新建文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `core/llm/hooks.py` | ~183 | 3 个 Hook 工厂：动态模型切换、文件上下文注入、技能元数据渲染 |
| `core/llm/mcp_manager.py` | ~115 | MCP 客户端连接池、工具加载、Schema 缓存（TTL 300s） |
| `core/llm/message_compat.py` | ~81 | dict ↔ Msg 转换、会话历史加载、思考块剥离 |
| `routing/streaming.py` | ~290 | StreamingAgent 封装：累计文本→增量 delta、工具事件提取、MCP 清理 |

### 2.4 重写文件

| 文件 | 变更程度 | 说明 |
|------|---------|------|
| `core/llm/agent_factory.py` | 完全重写 | `create_agent()` → `ReActAgent` + `Toolkit` + Hook 注册 |
| `core/llm/chat_models.py` | 完全重写 | `ChatOpenAI` → `OpenAIChatModel`，参数映射 |
| `routing/workflow.py` | 大幅重写 | 集成 `StreamingAgent`、mem0、引用提取 |
| `core/llm/middleware.py` | 简化为 shim | 仅 re-export `ModelContext`，旧中间件类全部删除 |
| `core/llm/summarization.py` | 适配 | 从中间件改为独立函数，适配 `Msg` 对象 |

### 2.5 未改动模块

以下模块 **零改动**，由 `workflow.py` 隔离了框架差异：

- 全部前端代码（`src/frontend/`）
- API 路由层（`api/routes/v1/`）
- 数据库（`core/db/`）
- 认证（`core/auth/`）
- 存储（`core/storage/`）
- 配置（`core/config/`、`configs/`）

---

## 三、架构差异详解

### 3.1 Agent 创建

```python
# ── LangChain ──
agent = create_agent(
    default_model=ChatOpenAI(model="qwen3_80b", base_url="..."),
    tools=tools,
    middleware=[DynamicModelMiddleware(), FileContextMiddleware(), ...],
    system_prompt=prompt,
)
result = await agent.invoke({"messages": session_messages})

# ── AgentScope ──
agent = ReActAgent(
    name="jingxin_agent",
    sys_prompt=prompt,
    model=OpenAIChatModel(model_name="qwen3_80b", ...),
    toolkit=toolkit,
    memory=InMemoryMemory(),
    max_iters=10,
)
agent._instance_pre_reply_hooks["dynamic_model"] = make_dynamic_model_hook()
agent._instance_pre_reply_hooks["file_context"] = make_file_context_hook()
result = await agent.reply(Msg(role="user", content="...", name="user"))
```

### 3.2 中间件 → Hook

6 个 LangChain 中间件被替换为 3 个 AgentScope Hook：

| 原中间件 | 新 Hook | 说明 |
|---------|---------|------|
| `DynamicModelMiddleware` | `make_dynamic_model_hook()` | 根据 DB 配置动态切换模型 |
| `FileContextMiddleware` | `make_file_context_hook()` | 将上传文件内容注入 Agent memory |
| `SkillsMiddleware` | `make_skills_hook(...)` | 将技能元数据追加到系统提示词 |
| `InTurnSummarizationMiddleware` | AgentScope `CompressionConfig` | 内置压缩，无需中间件 |
| `SummarizationMiddleware` | AgentScope `CompressionConfig` | 内置压缩 |
| `SystemMessageSanitizer` | 不再需要 | AgentScope 内部处理 |

Hook 相比中间件的优势：
- **显式注册**：按名称注册，无隐式执行顺序
- **直接访问**：可直接修改 `agent.model`、`agent.memory`、`agent._sys_prompt`
- **异步原生**：所有 Hook 均为 `async` 函数

### 3.3 MCP 工具管理

```python
# ── LangChain（无状态） ──
async with MultiServerMCPClient(mcp_servers) as client:
    tools = await client.get_tools()

# ── AgentScope（有状态 + 生命周期管理） ──
toolkit = Toolkit()
clients = []
for name, config in mcp_servers.items():
    client = StdIOStatefulClient(name=name, command=config["command"], ...)
    await client.connect()
    await toolkit.register_mcp_client(client, namesake_strategy="rename")
    clients.append(client)
# 使用后需手动清理
```

### 3.4 流式输出

```
LangChain:  agent.astream() → (stream_mode, chunk) 事件
AgentScope: agent.msg_queue  → 累计文本（需计算 delta）
```

`StreamingAgent` 封装层负责：
1. 消费 `msg_queue`，将累计文本转换为增量 delta
2. 从消息内容块中提取 `tool_call` / `tool_result` 事件
3. 支持 `<think>...</think>` 思考块的透传或剥离
4. SSE 事件格式与前端完全兼容

### 3.5 消息格式

AgentScope 内部使用 `Msg` 对象，外部（API、前端）仍使用 dict。`message_compat.py` 负责转换：

```python
# 会话历史加载（排除最后一条用户消息，避免 agent.reply() 重复添加）
history = session_messages[:-1]
await load_session_into_memory(history, agent.memory)
user_msg = Msg(role="user", content=last_user_content, name="user")
result = await agent.reply(user_msg)
```

---

## 四、数据流对比

### LangChain 时代

```
Session messages (dict[])
  → Agent.astream(payload, stream_mode=["messages", "custom"])
    ├─ Middleware chain（隐式顺序执行）
    ├─ Tool registry（全局注册表）
    ├─ Tool callbacks (on_tool_start / on_tool_end)
    └─ Stream events → SSE
```

### AgentScope 时代

```
Session messages (dict[])
  → load_session_into_memory() → Msg[]
  → Agent.reply(user_msg)
    ├─ pre_reply hooks（显式注册、直接修改 agent 属性）
    ├─ ReAct loop + MCP Toolkit
    ├─ msg_queue 事件流
    └─ StreamingAgent.stream() → (event_type, payload)
       ├─ text_delta（累计→增量）
       ├─ tool_call / tool_result
       └─ error
  → workflow.py SSE 映射 → 前端（格式不变）
```

---

## 五、AgentScope 框架优势

### 5.1 相比 LangChain 的改进

| 维度 | LangChain | AgentScope | 优势说明 |
|------|-----------|-----------|---------|
| **依赖复杂度** | 5+ 包，版本冲突频繁 | 1 个包 | 大幅降低维护成本 |
| **透明度** | 链式执行，调试困难 | 每步可见（Prompt、API 调用、Memory） | 问题排查效率提升 |
| **学习曲线** | 需理解 Chain / Graph / Node 抽象 | 直觉式的 Agent + Hook 模式 | 更快上手 |
| **异步设计** | 部分异步，需 `async` 适配 | 全异步原生 | 更好的并发性能 |
| **MCP 支持** | 需第三方适配器 | 原生内置 | 无额外依赖 |
| **思考模型** | 不支持 `<think>` 块处理 | 内置思考块剥离/透传 | 支持 DeepSeek R1 等模型 |
| **上下文压缩** | 手动中间件实现 | 内置 `CompressionConfig` | 更少样板代码 |

### 5.2 AgentScope 独有能力

#### 1) 原生 MCP 集成

AgentScope 是首批原生支持 MCP（Model Context Protocol）的 Agent 框架之一：
- 支持 **StdIO**（本地 MCP 服务器）和 **SSE/HTTP**（远程服务器）两种传输
- 有状态客户端（`StdIOStatefulClient`）保持持久会话
- 细粒度工具管理：服务器级、函数级、可组合为复合函数
- Python 和 Java 双语言实现

#### 2) 多 Agent 协作

AgentScope 的多 Agent 能力远超 LangChain 原生支持：
- **灵活拓扑**：层级式、对等式、协调者-工作者架构
- **MsgHub**：高效消息路由的多 Agent 对话中心
- **A2A 协议**：内置 Agent 间通信
- **并行执行**：Agent 间互不阻塞
- **分布式机制**：基于 Actor 模型的分布式工作流，自动并行优化

#### 3) 工具智能分组

解决 LangChain 的"工具过多"问题：
- `create_tool_group()` 将相关工具捆绑
- `update_tool_groups()` 动态激活/停用工具集
- **分组策略**显著减少工具搜索空间，提升 Agent 效率和准确率

#### 4) 并行工具执行

单个推理步骤内的多工具调用可并行执行：
- 使用 `asyncio.gather()` 并行分发
- 对 I/O 密集型操作（如 MCP 工具调用）大幅降低延迟
- LangChain 默认串行执行工具调用

#### 5) 流式工具响应

AgentScope 支持工具返回流式结果（同步/异步生成器），LangChain 不支持：
- 同步和异步工具函数均支持
- 流式工具中途中断时，已产生的结果自动保留
- 统一通过异步生成器接口处理

#### 6) 长期记忆系统

AgentScope 集成 **ReMe**（Remember Me）框架，提供三层长期记忆：
- **个人记忆**：学习用户偏好、背景信息
- **任务记忆**：提升 Agent 任务执行能力
- **工具记忆**：实现更智能的工具选择
- 跨会话持久化，自动压缩旧对话，新会话继承历史上下文

#### 7) 思考模型支持

- `enable_thinking` / `disable_thinking` 参数动态控制推理块
- 支持 DeepSeek R1、Qwen3 等思考模型
- 前端可选择显示或隐藏思考过程
- LangChain 无此内置能力

#### 8) Studio 可视化监控

AgentScope Studio 提供：
- 实时可视化 Agent 执行过程
- Prompt、API 调用、Memory 内容全程可见
- 支持人在环路（Human-in-the-Loop）实时干预
- 比 LangSmith 更轻量，无需独立部署

### 5.3 生产部署能力

| 能力 | AgentScope | LangChain |
|------|-----------|-----------|
| 本地部署 | 原生支持 | 原生支持 |
| Serverless 云部署 | 内置支持 | 需额外配置 |
| K8s 部署 | 内置支持 | 需额外配置 |
| OpenTelemetry | 原生集成 | 需 LangSmith |
| 分布式评估 | Ray-based | 需自行实现 |
| 多语言 | Python + Java | 仅 Python（JS 版生态较弱） |

---

## 六、迁移验证

所有测试用例均已通过：

| 测试文件 | 验证内容 |
|---------|---------|
| `agent_factory_middleware_selftest.py` | Skills Hook 集成 |
| `middleware_selftest.py` | Hook 行为 |
| `workflow_streaming_selftest.py` | 端到端流式输出 |
| `report_subagent_streaming_selftest.py` | 报告子 Agent |
| `agentscope_poc.py` | 6 项 PoC 验证（Model、Agent、MCP、Streaming、Hooks、Memory） |
| `intra_turn_summarization_selftest.py` | 上下文压缩 |
| `use_skill_tool_selftest.py` | 技能系统 |

---

## 七、已知问题与解决方案

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| MCP 客户端 cancel scope 泄露 | `StdIOStatefulClient.close()` 的 anyio cancel scope 跨任务泄露 | 直接终止子进程（`proc.terminate()`）替代 `close()` |
| Hook 上下文类型安全 | `agent._jx_context` 无类型检查 | 使用 `ModelContext` dataclass 定义 schema |
| 累计流式文本 | AgentScope 每个 chunk 包含全部已生成文本 | `StreamingAgent` 跟踪 `_previous_text` 计算 delta |
| BaseHTTPMiddleware 阻塞 SSE | Starlette 中间件包装响应体导致事件丢失 | 改用纯 ASGI 中间件 |

---

## 八、参考资料

- [AgentScope 官方文档](https://doc.agentscope.io/)
- [AgentScope GitHub](https://github.com/agentscope-ai/agentscope)
- [AgentScope 1.0 论文](https://arxiv.org/html/2508.16279v1)
- [MCP 工具文档](https://doc.agentscope.io/tutorial/task_mcp.html)
- [详细迁移指南](./langchain-to-agentscope-migration.md)
