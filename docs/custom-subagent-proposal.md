# 自定义子智能体模块设计方案 v2

> 版本: v2.0 | 日期: 2026-03-26

## 一、调研总结

### 1.1 OpenClaw

OpenClaw 是一个开源 AI 智能体平台，采用 hub-and-spoke 架构。

**核心设计：**

| 概念 | 说明 |
|---|---|
| Agent 配置 | `~/.openclaw/openclaw.json` 中 `agents.list[]` 数组定义多个智能体 |
| 工作空间隔离 | 每个 agent 有独立的 `workspace`、`agentDir`、`sessions` |
| 工具权限 | per-agent `tools.allow` / `tools.deny` 控制可用工具 |
| 沙箱 | per-agent `sandbox.mode` + `sandbox.scope` 控制执行隔离 |
| 技能系统 | SKILL.md + YAML frontmatter，三层加载优先级：workspace > local > bundled |
| Agent-to-Agent | 默认禁用，通过 `tools.agentToAgent.enabled + allow[]` 白名单控制 |
| Binding 路由 | 基于 channel/accountId/peer 的确定性路由（非 LLM 判断） |
| 身份文件 | 每个 agent 有独立的 `SOUL.md`(角色)、`AGENTS.md`(协作规则)、`USER.md`(用户偏好) |

**对我们的启发：**
- 每个智能体独立工作空间 → 我们的独立 ChatSession
- per-agent 工具 allow/deny → 我们的 `mcp_server_ids` / `skill_ids`
- Agent-to-Agent 默认禁用 → 子智能体之间隔离
- SOUL.md 定义角色 → 我们的 `system_prompt`

### 1.2 Claude Code

Claude Code 的 Agent 工具实现了「主智能体 → 子智能体」模式：

**核心设计：**

| 概念 | 说明 |
|---|---|
| Agent 工具 | 主 agent 通过 `Agent` tool 生成子进程级别的子 agent |
| 类型化 agent | `subagent_type`: general-purpose / Explore / Plan 等，每种有不同工具权限 |
| 工具隔离 | 不同 agent 类型有不同的可用工具集（如 Explore 不能 Edit/Write） |
| 上下文隔离 | 每个子 agent 独立上下文窗口，完成后返回摘要结果给主 agent |
| 并行执行 | 主 agent 可以在一条消息中同时启动多个子 agent |
| 后台运行 | `run_in_background: true` 允许子 agent 后台执行，主 agent 继续工作 |
| Worktree 隔离 | `isolation: "worktree"` 给子 agent 一个独立的 git worktree |
| 结果回传 | 子 agent 完成后返回单条消息给主 agent，主 agent 汇总展示给用户 |

**对我们的启发：**
- **子智能体作为工具**：主智能体通过 tool_call 调用子智能体 → 我们的 LLM Router + @ 触发
- **并行 + 后台**：多个子 agent 可同时工作 → LLM 自主决定串行/并行
- **上下文隔离**：每个子 agent 独立 context → 独立 ChatSession
- **结果汇总**：子 agent 结果回传给主 agent → 主智能体汇总多个子智能体结果

### 1.3 Coze (扣子)

**Multi-Agent 模式核心设计：**

| 概念 | 说明 |
|---|---|
| BotMode | `single_agent` / `multi_agent` / `single_agent_workflow` 三种模式 |
| Hub-and-Spoke | Main Menu Bot（中央调度器）→ 多个专业 Bot（子智能体） |
| Jump Conditions | 基于关键词的跳转条件，控制从调度器到子 Bot 的路由 |
| 全局跳转 | Global Jump Conditions 允许从任意子 Bot 返回主菜单 |
| 独立配置 | 每个子 Bot 有独立的 Prompt、Plugins、Workflows、Knowledge |
| Workspace 共享 | 同一 Workspace 内的 Bot 对 workspace 成员可见 |
| 插件绑定 | `plugin_info_list` — 通过 ID 列表引用绑定的插件 |
| 知识库绑定 | `knowledge.dataset_ids` + `auto_call` + `search_strategy` |

**对我们的启发：**
- **Main Menu Bot 调度** → 我们的主智能体 + LLM Router
- **独立配置** → 每个子智能体有独立的 prompt、工具、知识库
- **Workspace 共享** → admin 创建的子智能体绑定所有用户

### 1.4 各平台对比（更新版）

| 维度 | OpenClaw | Claude Code | Coze | **我们的方案** |
|---|---|---|---|---|
| 子智能体定义 | JSON config | 代码内置 agent type | Bot entity | **DB UserAgent 表** |
| 路由方式 | Binding 确定性路由 | LLM 自主调用 Agent tool | 关键词 Jump Conditions | **LLM Router + @ 触发** |
| 智能体间隔离 | 独立 workspace | 独立上下文窗口 | 独立配置 | **独立 ChatSession** |
| 智能体间通信 | agentToAgent (默认禁用) | 结果回传给主 agent | 全局跳转回主菜单 | **仅主智能体可调度** |
| 工具权限 | allow/deny 列表 | 按 type 预设 | plugin_ids 绑定 | **mcp_ids + skill_ids** |
| 多智能体协作 | 无 | 并行+后台子 agent | Hub-and-Spoke | **主agent orchestrate** |
| 管理权限 | 本地配置文件 | 无 admin 概念 | Workspace admin | **admin + user 两层** |

---

## 二、核心需求（明确版）

根据沟通确认：

### 2.1 智能体层级

```
┌─────────────────────────────────────────────┐
│              主智能体 (Main Agent)            │
│  所有用户默认使用，系统级配置                    │
│  可以路由到任意子智能体                         │
│  可以同时调度多个子智能体（LLM 自主决定）         │
└────────┬──────────────┬──────────────┬───────┘
         │              │              │
    ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
    │ 子智能体A │   │ 子智能体B │   │ 子智能体C │
    │ (admin) │   │ (admin) │   │ (user)  │
    │ 全员可见 │   │ 全员可见 │   │ 仅创建者 │
    └─────────┘   └─────────┘   └─────────┘
         ✗ 相互隔离，不能互相调用 ✗
```

### 2.2 访问规则

| 规则 | 说明 |
|---|---|
| 主智能体 | 所有用户可访问，系统默认 |
| Admin 子智能体 | admin 在管理后台创建，自动对**所有用户**可见可用 |
| 用户子智能体 | 用户自行创建，**仅创建者自己**可见可用，用户之间隔离 |
| 主→子路由 | 主智能体可以路由到用户可见的任意子智能体 |
| 子→子隔离 | 子智能体之间**完全隔离**，不能互相调用 |

### 2.3 交互方式

| 方式 | 说明 |
|---|---|
| 直接对话 | 用户点击某个子智能体，开启**独立对话**，走该子智能体配置 |
| LLM 自动路由 | 主智能体根据用户意图，自动判断是否需要调度某个子智能体 |
| @ 显式调用 | 用户在主智能体对话中 `@子智能体名称` 显式调用 |
| 多智能体协作 | 主智能体可同时调度多个子智能体，LLM 自主决定串行/并行 |

### 2.4 对话模型

| 场景 | ChatSession |
|---|---|
| 与主智能体对话 | 独立 session，`agent_id = null` |
| 直接与子智能体对话 | 独立 session，`agent_id = "ua_xxx"` |
| 主智能体调度子智能体 | 主 session 不变，子智能体执行在主 agent 上下文内（类似 Claude Code） |

---

## 三、方案设计

### 3.1 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         前端 (React)                             │
│                                                                  │
│  ┌───────────┐  ┌────────────┐  ┌──────────────────────────┐   │
│  │ AgentPanel │  │ AgentForm  │  │  ChatArea                │   │
│  │ 侧边栏入口 │  │ 创建/编辑  │  │  ┌──────────────────┐   │   │
│  │ admin+user │  │ Modal      │  │  │ AgentPicker      │   │   │
│  │ 分区展示   │  │            │  │  │ 选择/切换智能体   │   │   │
│  └───────────┘  └────────────┘  │  └──────────────────┘   │   │
│                                  │  InputArea 支持 @mention │   │
│                                  └──────────────────────────┘   │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐ │
│  │ AdminApp.tsx      │  │ agentStore.ts (Zustand)              │ │
│  │ 管理后台入口      │  │ agents[], current, CRUD actions     │ │
│  └──────────────────┘  └──────────────────────────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │ REST API + SSE
┌─────────────────────────────▼───────────────────────────────────┐
│                        后端 (FastAPI)                             │
│                                                                  │
│  api/routes/v1/                                                  │
│  ├── user_agents.py      # 用户端 CRUD                          │
│  └── admin_agents.py     # Admin 端 CRUD (ADMIN_TOKEN)          │
│                                                                  │
│  core/services/                                                  │
│  └── user_agent_service.py  # 业务逻辑 + 权限校验               │
│                                                                  │
│  core/db/models.py                                               │
│  └── UserAgent 表 (owner_type: admin | user)                    │
│                                                                  │
│  routing/                                                        │
│  ├── workflow.py          # context["agent_id"] → 子智能体模式   │
│  ├── strategy.py          # LLM Router 增强                     │
│  └── subagent_executor.py # 主→子调度执行器 (新增)               │
│                                                                  │
│  core/llm/                                                       │
│  ├── agent_factory.py     # 支持 user_agent 参数                │
│  └── subagent_tool.py     # 子智能体注册为 MCP-like tool (新增)  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 数据模型

#### 3.2.1 新增 DB 表: `user_agents`

```python
class UserAgent(Base):
    """自定义子智能体 (admin 创建 或 用户创建)"""
    __tablename__ = "user_agents"

    agent_id     = Column(String(64), primary_key=True)          # nanoid
    owner_type   = Column(String(10), nullable=False)            # "admin" | "user"
    user_id      = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"))
                   # admin 创建时 user_id 为 null 或 admin 的 user_id
    name         = Column(String(100), nullable=False)           # 智能体名称
    avatar       = Column(String(500))                           # 头像 URL/emoji
    description  = Column(Text, default="")                      # 简短描述

    # ── 核心配置 ──
    system_prompt    = Column(Text, nullable=False, default="")  # 角色设定
    welcome_message  = Column(Text, default="")                  # 开场白
    suggested_questions = Column(JSONB, default=list)            # ["问题1", "问题2"]

    # ── 能力绑定 ──
    mcp_server_ids = Column(JSONB, default=list)                 # ["internet_search", ...]
    skill_ids      = Column(JSONB, default=list)                 # ["quick-material-analysis", ...]
    kb_ids         = Column(JSONB, default=list)                 # ["kb_xxx", ...]

    # ── 模型配置 ──
    model_provider_id = Column(String(64), ForeignKey("model_providers.provider_id", ondelete="SET NULL"))
    temperature    = Column(Numeric(3,2), default=0.7)
    max_tokens     = Column(Integer)

    # ── 运行时控制 ──
    max_iters      = Column(Integer, default=10)
    timeout        = Column(Integer, default=120)
    is_enabled     = Column(Boolean, default=True)
    sort_order     = Column(Integer, default=0)

    # ── 高级配置 ──
    extra_config   = Column(JSONB, default=dict)
    # 扩展字段包括:
    #   prompt_parts: [...]          自定义 prompt 片段组合
    #   enable_memory: bool          是否启用记忆
    #   routing_keywords: [...]      触发路由的关键词 (供 LLM Router 参考)
    #   routing_description: str     LLM Router 用的一句话描述 (何时调用此智能体)

    # ── 元数据 ──
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(64))

    # Relationships
    user = relationship("UserShadow", backref="user_agents")
    model_provider = relationship("ModelProvider")

    __table_args__ = (
        CheckConstraint("owner_type IN ('admin', 'user')", name="user_agents_owner_type_check"),
        Index("idx_user_agents_owner_type", "owner_type"),
        Index("idx_user_agents_user_id", "user_id"),
        Index("idx_user_agents_is_enabled", "is_enabled"),
    )
```

**关键设计决策：**

1. **`owner_type` 区分 admin vs user**：
   - `admin`：管理员创建，所有用户可见
   - `user`：用户创建，仅创建者可见

2. **`extra_config.routing_description`**：
   给 LLM Router 用的一句话说明，如 "当用户需要分析产业链数据时调用此智能体"。主智能体的 system prompt 中会注入所有可用子智能体的 `name + routing_description`，让 LLM 自主判断路由。

3. **不增加 `is_public` 字段**：admin 创建的自动全员可见，user 创建的自动仅自己可见，由 `owner_type` 直接决定，避免歧义。

#### 3.2.2 ChatSession 扩展

在现有 `ChatSession.extra_data` (JSONB) 中增加字段：

```json
{
  "agent_id": "ua_xxxx",           // 绑定的子智能体 (null = 主智能体)
  "agent_name": "产业链分析师"       // 冗余显示名，避免额外查询
}
```

### 3.3 后端 API 设计

#### 3.3.1 REST 端点

```
# ── 用户端 ──
GET    /v1/agents                          # 列表 (admin的 + 自己的)
POST   /v1/agents                          # 创建 (owner_type=user)
GET    /v1/agents/:agent_id                # 详情
PUT    /v1/agents/:agent_id                # 更新 (仅自己的)
DELETE /v1/agents/:agent_id                # 删除 (仅自己的)
GET    /v1/agents/available-resources      # 可绑定的 MCP/Skill/KB

# ── Admin 端 ──
GET    /v1/admin/agents                    # Admin 智能体列表
POST   /v1/admin/agents                    # 创建 (owner_type=admin)
PUT    /v1/admin/agents/:agent_id          # 更新
DELETE /v1/admin/agents/:agent_id          # 删除
POST   /v1/admin/agents/:agent_id/clone    # 复制为模板
```

#### 3.3.2 列表查询逻辑

```python
async def list_agents_for_user(user_id: str) -> list[UserAgent]:
    """返回用户可见的所有子智能体"""
    return db.query(UserAgent).filter(
        UserAgent.is_enabled == True,
        or_(
            UserAgent.owner_type == "admin",       # admin 创建的全员可见
            and_(
                UserAgent.owner_type == "user",
                UserAgent.user_id == user_id,      # user 创建的仅自己可见
            ),
        ),
    ).order_by(UserAgent.owner_type.desc(), UserAgent.sort_order).all()
```

### 3.4 核心改造：主智能体调度子智能体

这是最关键的设计点。参考 Claude Code 的 "Agent as Tool" 模式：

#### 3.4.1 子智能体注册为主智能体的工具

新增 `core/llm/subagent_tool.py`：

```python
"""将用户可见的子智能体注册为主智能体的可调用工具。

类似 Claude Code 的 Agent tool —— 主智能体通过 tool_call
调用子智能体，子智能体独立执行后返回结果。
"""

async def call_subagent(
    agent_id: str,
    task: str,
    context_summary: str = "",
) -> ToolResponse:
    """调用指定的子智能体执行任务。

    Args:
        agent_id: 子智能体 ID
        task: 需要子智能体完成的任务描述
        context_summary: 当前对话上下文的摘要 (可选)

    Returns:
        子智能体的执行结果
    """
    # 1. 从 DB 加载子智能体配置
    user_agent = await UserAgentService.get_by_id(agent_id)

    # 2. 构建子智能体 (独立的 ReActAgent)
    agent, mcp_clients = await create_agent_executor(
        user_agent=user_agent,
        current_user_id=current_user_id,
    )

    try:
        # 3. 执行任务
        prompt = f"{context_summary}\n\n用户任务：{task}"
        user_msg = Msg(name="user", content=prompt, role="user")
        result = await agent.reply(user_msg)
        return ToolResponse(content=[TextBlock(
            type="text",
            text=result.get_text_content(),
        )])
    finally:
        await close_clients(mcp_clients)
```

#### 3.4.2 主智能体 System Prompt 注入

在主智能体的 system prompt 中动态注入可用子智能体列表：

```markdown
## 可用子智能体

你可以通过 call_subagent 工具调用以下子智能体来协助完成任务。
每个子智能体有特定的专长领域，请根据用户需求选择合适的子智能体。
子智能体之间相互独立，你可以同时调用多个子智能体。

| ID | 名称 | 适用场景 |
|---|---|---|
| ua_001 | 产业链分析师 | 当用户需要分析产业链结构、上下游关系时 |
| ua_002 | 政策解读专家 | 当用户需要解读政策条款、匹配政策时 |
| ua_003 | 数据可视化师 | 当用户需要生成图表、数据可视化时 |

当用户通过 @名称 指定子智能体时，请直接调用对应的子智能体。
```

这个列表在 `agent_factory.py` 中动态生成，基于当前用户可见的子智能体。

#### 3.4.3 @ 触发解析

在 `routing/workflow.py` 中增加 @ mention 解析：

```python
import re

def parse_agent_mentions(message: str, available_agents: list) -> tuple[list[str], str]:
    """解析消息中的 @智能体名 提及。

    Returns:
        (mentioned_agent_ids, cleaned_message)
    """
    mentioned = []
    cleaned = message
    for agent in available_agents:
        pattern = f"@{re.escape(agent.name)}"
        if pattern in message:
            mentioned.append(agent.agent_id)
            cleaned = cleaned.replace(pattern, "").strip()
    return mentioned, cleaned
```

当检测到 @ mention 时，在 system prompt 中加入强制指令：

```
用户指定调用子智能体 [产业链分析师]，请直接使用 call_subagent 工具调用该智能体。
```

#### 3.4.4 Workflow 流程（完整版）

```python
async def astream_chat_workflow(*, session_messages, user_message, context):

    agent_id = context.get("agent_id")  # ChatSession 绑定的子智能体

    # ── 场景 1: 用户直接与子智能体对话 ──
    if agent_id:
        user_agent = await UserAgentService.get_by_id(agent_id)
        # 权限校验
        assert_agent_accessible(user_agent, context["user_id"])
        # 直接用子智能体配置构建 agent
        agent, mcp_clients = await create_agent_executor(
            user_agent=user_agent,
            current_user_id=context["user_id"],
        )
        # 流式执行 (与现有 main 路径一致)
        streaming_agent = StreamingAgent(agent, mcp_clients)
        async for event_type, payload in streaming_agent.stream(...):
            yield ...
        return

    # ── 场景 2: 主智能体对话 (可能路由到子智能体) ──

    # 2a. 获取用户可见的子智能体列表
    visible_agents = await UserAgentService.list_for_user(context["user_id"])

    # 2b. 解析 @ mention
    mentioned_ids, cleaned_message = parse_agent_mentions(
        user_message, visible_agents
    )

    # 2c. 构建主智能体 (注入子智能体工具)
    agent, mcp_clients = await create_agent_executor(
        agent_spec=None,  # 主智能体
        enabled_mcp_ids=...,
        enabled_skill_ids=...,
        # 新增：可调度的子智能体列表
        available_subagents=visible_agents,
        mentioned_agent_ids=mentioned_ids,
        current_user_id=context["user_id"],
    )

    # 2d. 流式执行 (主智能体可能通过 tool_call 调用子智能体)
    streaming_agent = StreamingAgent(agent, mcp_clients)
    async for event_type, payload in streaming_agent.stream(...):
        if event_type == "tool_call" and payload.get("name") == "call_subagent":
            # 子智能体调用事件 — 前端可显示特殊 UI
            yield {"type": "subagent_call", "agent_id": ..., "task": ...}
        elif event_type == "tool_result" and ...:
            yield {"type": "subagent_result", "agent_id": ..., "result": ...}
        else:
            yield ...  # 正常事件
```

### 3.5 Agent Factory 改造

```python
async def create_agent_executor(
    agent_spec: Optional[AgentSpec] = None,
    user_agent: Optional[UserAgent] = None,        # 新增：子智能体直接对话
    available_subagents: list = None,               # 新增：可调度的子智能体
    mentioned_agent_ids: list[str] = None,          # 新增：@ 提及的子智能体
    # ... 其余参数不变
) -> Tuple[ReActAgent, List[StdIOStatefulClient]]:

    # ── 子智能体直接对话模式 ──
    if user_agent is not None:
        # 使用子智能体自己的配置
        enabled_mcp_ids = user_agent.mcp_server_ids
        enabled_skill_ids = user_agent.skill_ids
        enabled_kb_ids = user_agent.kb_ids
        # system_prompt = 标准框架 + 用户自定义 prompt
        system_prompt = _build_subagent_system_prompt(user_agent, cfg)
        # 不注入 call_subagent 工具 (子智能体不能调度其他子智能体)

    # ── 主智能体模式 (可能有子智能体工具) ──
    else:
        # 现有逻辑...
        # 额外：注册 call_subagent 工具
        if available_subagents:
            _register_subagent_tools(toolkit, available_subagents, context)
            # 注入子智能体列表到 system prompt
            system_prompt += _build_subagent_prompt_section(
                available_subagents, mentioned_agent_ids
            )
```

### 3.6 System Prompt 组装策略

#### 子智能体直接对话时的 prompt 结构

```
[00_time_role]        ← 标准时间/角色框架
[20_tools_policy]     ← 标准工具使用规范
[65_citations]        ← 标准引用规则
────────────────
[UserAgent.system_prompt]   ← 用户/admin 自定义的角色设定
────────────────
[60_format]           ← 标准输出格式
[Agent Skills section] ← 绑定的技能清单
```

#### 主智能体调度模式的 prompt 附加段

```
## 可用子智能体

你可以通过 call_subagent 工具调用以下专业子智能体...
[动态生成的子智能体列表表格]

### 使用规则
- 根据用户意图自主判断是否需要调用子智能体
- 可以同时调用多个子智能体处理不同方面的任务
- 子智能体返回结果后，你负责汇总和整合
- 用户通过 @名称 指定时，直接调用对应子智能体
- 如果不确定是否需要调用子智能体，优先自己处理
```

### 3.7 前端设计

#### 3.7.1 组件结构

```
src/frontend/src/
├── components/
│   └── agent/
│       ├── AgentPanel.tsx             # 侧边栏智能体列表面板
│       │                               # 分两区: "系统智能体" (admin) + "我的智能体" (user)
│       ├── AgentCard.tsx              # 智能体卡片 (头像+名称+描述+操作)
│       ├── AgentFormModal.tsx         # 创建/编辑表单
│       ├── AgentToolSelector.tsx      # MCP/Skill 多选器
│       ├── AgentKbSelector.tsx        # 知识库多选器
│       ├── AgentPromptEditor.tsx      # 提示词编辑器 (带模板)
│       ├── AgentPicker.tsx            # 对话区顶部的智能体选择器
│       └── AgentMentionPopup.tsx      # 输入框 @ 提及的弹出选择器
│
├── components/admin/
│   └── AdminAgentManager.tsx          # Admin 后台智能体管理页
│
├── stores/
│   └── agentStore.ts                  # Zustand store
│       # state: agents[], adminAgents[], currentAgent, loading
│       # actions: fetchAgents, createAgent, updateAgent, deleteAgent
│
└── api.ts                             # 新增 agent 相关 API
```

#### 3.7.2 交互流程

**1. 侧边栏入口**
```
┌──────────────────┐
│ 📋 对话列表       │
│ ──────────────── │
│ 🤖 智能体         │  ← 新增入口
│   ├ 系统智能体     │  ← admin 创建，带"官方"标签
│   │  ├ 产业链分析师 │
│   │  └ 政策解读专家 │
│   └ 我的智能体     │  ← 用户创建
│      ├ 日报助手    │
│      └ ＋ 创建智能体│
│ ──────────────── │
│ ⚙️ 设置          │
└──────────────────┘
```

**2. 创建新对话时选择智能体**
```
┌─────────────────────────────────────┐
│ 新建对话                             │
│                                      │
│ 选择智能体:                           │
│ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐   │
│ │ 🌐  │ │ 📊  │ │ 📋  │ │ 🔬  │   │
│ │主智能│ │产业链│ │政策  │ │日报  │   │
│ │  体  │ │分析师│ │专家  │ │助手  │   │
│ └─────┘ └─────┘ └─────┘ └─────┘   │
│                                      │
│ 或直接输入开始与主智能体对话...        │
└─────────────────────────────────────┘
```

**3. 对话中的 @ 提及**
```
┌─────────────────────────────────────┐
│ 🌐 主智能体                          │
│                                      │
│ User: @产业链分析师 帮我分析新能源…     │
│                                      │
│ ┌ 📊 调用子智能体: 产业链分析师 ──┐   │
│ │ 正在分析新能源产业链结构...       │   │
│ └──────────────────────────────┘   │
│                                      │
│ AI: 根据产业链分析师的分析结果...      │
└─────────────────────────────────────┘
```

**4. SSE 新增事件类型**

```typescript
// 子智能体调用事件
{ type: "subagent_call", agent_id: "ua_001", agent_name: "产业链分析师", task: "..." }

// 子智能体返回结果事件
{ type: "subagent_result", agent_id: "ua_001", agent_name: "产业链分析师", result: "..." }
```

前端收到 `subagent_call` 时显示"正在调用 XXX 子智能体..."的 UI 提示；
收到 `subagent_result` 时显示子智能体返回结果的折叠面板。

### 3.8 Admin 后台设计

在现有 `AdminApp.tsx` 中新增「子智能体管理」Tab：

```
┌─────────────────────────────────────────────────────┐
│ Admin 管理后台                                        │
│ ┌────┐ ┌────┐ ┌─────┐ ┌────┐ ┌──────────┐         │
│ │模型│ │工具│ │提示词│ │配置│ │子智能体管理│ ← 新增  │
│ └────┘ └────┘ └─────┘ └────┘ └──────────┘         │
│                                                      │
│ ┌───────────────────────────────────────────────┐   │
│ │ + 创建子智能体                                  │   │
│ ├───────────────────────────────────────────────┤   │
│ │ 📊 产业链分析师    [启用] [编辑] [删除] [复制]  │   │
│ │ 📋 政策解读专家    [启用] [编辑] [删除] [复制]  │   │
│ │ 📈 数据可视化师    [禁用] [编辑] [删除] [复制]  │   │
│ └───────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

Admin 创建的子智能体的编辑表单与用户端一致，额外多一个 `sort_order` 排序字段。

### 3.9 安全与权限

| 规则 | 说明 |
|---|---|
| 用户 CRUD 权限 | 用户只能 CRUD `owner_type=user AND user_id=自己` 的智能体 |
| Admin CRUD 权限 | Admin 通过 ADMIN_TOKEN 创建 `owner_type=admin` 的智能体 |
| 工具绑定约束 | 只能绑定 `admin_mcp_servers.is_enabled=True` 的 MCP server |
| Prompt 注入防护 | 自定义 prompt 拼接在标准安全规则**之后**，不可覆盖 |
| 子智能体隔离 | 子智能体的 toolkit 中**不注册** `call_subagent`，禁止子→子调用 |
| 数量限制 | 每用户最多 20 个子智能体，admin 无限制 |
| @ mention 校验 | 只能 @ 用户可见的子智能体（admin的 + 自己的） |

---

## 四、实施计划

### Phase 1: 数据层 + CRUD API

1. `core/db/models.py` — 新增 `UserAgent` 模型
2. Alembic migration — 创建 `user_agents` 表
3. `core/services/user_agent_service.py` — CRUD + 权限逻辑 + 列表查询
4. `api/routes/v1/agents.py` — 用户端端点
5. `api/routes/v1/admin_agents.py` — Admin 端点
6. `api/schemas.py` — Pydantic schemas

### Phase 2: 子智能体直接对话

7. `core/llm/agent_factory.py` — 支持 `user_agent` 参数，构建子智能体 agent
8. `routing/workflow.py` — `context["agent_id"]` 判断，走子智能体分支
9. System prompt 拼接逻辑
10. `api/routes/v1/chats.py` — ChatRequest 增加 `agent_id` 字段

### Phase 3: 主智能体调度子智能体

11. `core/llm/subagent_tool.py` — `call_subagent` 工具实现
12. `agent_factory.py` — 注入 `call_subagent` 到主智能体 toolkit
13. System prompt 动态注入子智能体列表
14. @ mention 解析逻辑
15. SSE 新增 `subagent_call` / `subagent_result` 事件

### Phase 4: 前端 - 用户端

16. `stores/agentStore.ts` — Zustand store
17. `api.ts` — agent CRUD API
18. `components/agent/AgentPanel.tsx` — 侧边栏智能体列表
19. `components/agent/AgentCard.tsx` — 智能体卡片
20. `components/agent/AgentFormModal.tsx` — 创建/编辑表单
21. `components/agent/AgentPicker.tsx` — 新建对话时选择智能体
22. `components/agent/AgentMentionPopup.tsx` — @ 提及弹出选择器
23. `hooks/useChatActions.ts` — 发送消息时传递 `agent_id`
24. 对话区 subagent_call / subagent_result 事件渲染

### Phase 5: 前端 - Admin 端

25. `components/admin/AdminAgentManager.tsx` — Admin 智能体管理
26. Admin 创建/编辑/删除/排序 UI

### Phase 6: 高级功能 (后续迭代)

27. 智能体模板 / 预设（admin 预置常用场景模板供用户一键复制）
28. 智能体导入导出（JSON 格式）
29. 子智能体执行进度的实时流式展示
30. 主智能体调度结果的结构化展示（多子智能体结果对比 / 汇总面板）
31. 子智能体使用统计（调用次数、平均耗时）

---

## 五、技术风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 子智能体执行延迟 | 主→子调度增加一层推理 | 子智能体复用连接池，限制 max_iters；支持超时中断 |
| LLM Router 准确率 | 可能误路由或漏路由 | `routing_description` 精准描述 + @ 显式调用作为兜底 |
| Prompt 注入 | 用户 prompt 可能绕过安全规则 | 标准安全规则在前，自定义 prompt 在后；admin 审核 |
| Token 消耗 | 主→子调度时 system prompt 膨胀 | 子智能体列表用精简格式；子智能体独立 context |
| 并发压力 | 同时调度多个子智能体 | 限制单次最多 3 个并行子智能体；共享 MCP 连接池 |
| 前端复杂度 | 新增组件和状态较多 | 复用 CatalogPanel 模式；渐进式发布 |
