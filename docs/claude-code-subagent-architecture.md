# Claude Code Subagent 交互与上下文管理架构分析

> 基于 Claude Code v2.1.88 源码的深度分析

## 目录

- [一、整体架构概览](#一整体架构概览)
- [二、Agent Tool 调用流程](#二agent-tool-调用流程)
- [三、内置子 Agent 全景图](#三内置子-agent-全景图)
- [四、上下文隔离机制](#四上下文隔离机制)
- [五、消息传递方式](#五消息传递方式)
- [六、Fork Agent 与 Prompt Cache 共享机制](#六fork-agent-与-prompt-cache-共享机制)
- [七、Agent 工具集管理](#七agent-工具集管理)
- [八、生命周期管理](#八生命周期管理)
- [九、关键设计洞察](#九关键设计洞察)

---

## 一、整体架构概览

Claude Code 的多智能体系统有三种模式：

| 模式 | 实现方式 | 场景 |
|---|---|---|
| **Subagent** (Agent Tool) | 进程内，通过 `runAgent()` 执行 | 快速委派任务（Explore、Plan 等） |
| **Teammate** (Swarm) | 进程内或 tmux/iTerm2 独立进程 | 团队协作，多 agent 并行 |
| **Fork** | 进程内，继承父对话完整上下文 | 分支执行，共享 prompt cache |

### 核心文件索引

| 文件 | 职责 |
|---|---|
| `src/tools/AgentTool/AgentTool.tsx` | Agent Tool 主入口，路由分发 |
| `src/tools/AgentTool/runAgent.ts` | Agent 执行引擎 |
| `src/tools/AgentTool/forkSubagent.ts` | Fork 子 agent 特性 |
| `src/utils/forkedAgent.ts` | 子 agent 上下文创建、forked query 循环 |
| `src/utils/agentContext.ts` | AsyncLocalStorage 上下文隔离 |
| `src/tools/AgentTool/built-in/` | 内置 agent 定义 |
| `src/tools/AgentTool/builtInAgents.ts` | 内置 agent 注册表 |
| `src/services/api/claude.ts` | API 调用层，prompt cache 标记 |

---

## 二、Agent Tool 调用流程

核心入口在 `AgentTool.tsx` 的 `call()` 方法：

```
用户(父 Agent) 调用 Agent Tool
  → 选择 agent 类型 (subagent_type 参数)
  → 解析为 AgentDefinition
  → 决定同步 or 异步 (run_in_background)
  → 组装 promptMessages + systemPrompt
  → 调用 runAgent() 启动子 agent
  → 子 agent 通过 query() 与 LLM API 交互
  → 收集结果消息返回给父 agent
```

### 路由决策逻辑

```typescript
// AgentTool.tsx:322-323
const effectiveType = subagent_type
  ?? (isForkSubagentEnabled() ? undefined : GENERAL_PURPOSE_AGENT.agentType);
const isForkPath = effectiveType === undefined;
```

- `subagent_type` 已指定 → 使用指定类型
- `subagent_type` 未指定 + fork 功能开启 → Fork 路径
- `subagent_type` 未指定 + fork 功能关闭 → 默认 `general-purpose`

---

## 三、内置子 Agent 全景图

Claude Code 共有 7 类子 Agent（6 个内置 + 1 个隐式 Fork）。

### 3.1 `general-purpose` — 通用全能型

| 属性 | 值 |
|---|---|
| 角色 | 万金油，处理复杂多步骤任务 |
| 模型 | 默认子 agent 模型 |
| 工具 | `['*']` — 全部工具 |
| 上下文 | 不继承父对话历史，只接收任务 prompt |
| CLAUDE.md | 保留 |

**定义文件**: `src/tools/AgentTool/built-in/generalPurposeAgent.ts`

**System Prompt 核心指令**:
> "Complete the task fully—don't gold-plate, but don't leave it half-done."

**使用时机**: 搜索关键词/文件不确定能否一次找到时、需要多步骤研究的复杂任务。是 `subagent_type` 未指定时的默认选择（Fork 功能关闭时）。

### 3.2 `Explore` — 代码探索专家

| 属性 | 值 |
|---|---|
| 角色 | 只读的文件搜索/代码分析 specialist |
| 模型 | 外部用户用 `haiku`（快），内部用 `inherit` |
| 工具 | 禁止 Agent, ExitPlanMode, FileEdit, FileWrite, NotebookEdit |
| 上下文 | 省略 CLAUDE.md、省略 gitStatus |
| 特点 | 只读模式，强调速度 |

**定义文件**: `src/tools/AgentTool/built-in/exploreAgent.ts`

**System Prompt 核心指令**:
> "READ-ONLY MODE... STRICTLY PROHIBITED from creating/modifying/deleting files"
> "You are meant to be a fast agent... spawn multiple parallel tool calls"

**使用时机**: 快速搜索文件模式、代码关键词搜索、回答代码库相关问题。支持指定 thoroughness level（quick / medium / very thorough）。

**上下文优化**:
```typescript
// runAgent.ts:387-398 — 省略 CLAUDE.md 节省 ~5-15 Gtok/week
omitClaudeMd: true,

// runAgent.ts:404-409 — 省略 gitStatus 节省 ~1-3 Gtok/week
if (agentType === 'Explore' || agentType === 'Plan') {
  resolvedSystemContext = systemContextNoGit
}
```

### 3.3 `Plan` — 架构规划师

| 属性 | 值 |
|---|---|
| 角色 | 只读的软件架构师，设计实现方案 |
| 模型 | `inherit`（继承父 agent 模型） |
| 工具 | 与 Explore 相同的禁止列表 |
| 上下文 | 省略 CLAUDE.md、省略 gitStatus |
| 特点 | 只读模式，输出结构化实施计划 |

**定义文件**: `src/tools/AgentTool/built-in/planAgent.ts`

**System Prompt 四步流程**:
1. 理解需求
2. 深入探索（Glob/Grep/Read + 只读 Bash）
3. 设计方案（考虑权衡和架构决策）
4. 详细计划（步骤、依赖、挑战）

**必须输出**: "Critical Files for Implementation"（3-5 个关键文件路径）

### 3.4 `verification` — 验证专家（红队角色）

| 属性 | 值 |
|---|---|
| 角色 | 对实现进行破坏性测试，找出 bug |
| 模型 | `inherit` |
| 工具 | 禁止写入工具，但可在 /tmp 写临时测试脚本 |
| 颜色 | 红色 |
| 运行模式 | `background: true`（强制后台） |
| 特点 | 有 `criticalSystemReminder`（每轮注入提醒） |

**定义文件**: `src/tools/AgentTool/built-in/verificationAgent.ts`

这是设计最精巧的 agent。其 prompt 是**反直觉的**：

> "Your job is not to confirm the implementation works — it's to **try to break it**."

**内置两个自我反思机制**:
1. **验证回避模式**: 识别自己"读代码 → 写 PASS → 跳过"的倾向
2. **被 80% 诱惑**: 警惕 UI 漂亮/测试通过就觉得 OK

**内置借口识别系统**:
- "The code looks correct based on my reading" → 阅读不是验证，运行它
- "The implementer's tests already pass" → 实现者也是 LLM，独立验证
- "I don't have a browser" → 你检查过有没有 playwright MCP 吗？
- "This would take too long" → 不是你来决定

**输出格式**: 每个 check 必须有 `Command run` + `Output observed`，没有命令输出的 PASS 会被拒绝。最终必须输出 `VERDICT: PASS / FAIL / PARTIAL`。

### 3.5 `claude-code-guide` — 使用指南助手

| 属性 | 值 |
|---|---|
| 角色 | 帮用户了解 Claude Code / Agent SDK / Claude API |
| 模型 | `haiku`（快速回答） |
| 工具 | Glob, Grep, Read, WebFetch, WebSearch |
| 权限模式 | `dontAsk`（完全自主，不弹权限确认） |
| 特点 | 会读取用户当前配置作为上下文 |

**定义文件**: `src/tools/AgentTool/built-in/claudeCodeGuideAgent.ts`

**三大知识领域**:
1. Claude Code CLI — 功能、hooks、slash commands、MCP、设置
2. Claude Agent SDK — 构建自定义 agent
3. Claude API — API 使用、tool use、SDK 用法

**动态上下文注入**: 会将用户当前配置的 custom skills、custom agents、MCP servers、plugin commands、settings.json 注入 system prompt。

### 3.6 `statusline-setup` — 状态栏配置器

| 属性 | 值 |
|---|---|
| 角色 | 配置 Claude Code 的终端状态栏 |
| 模型 | `sonnet` |
| 工具 | Read, Edit |
| 颜色 | 橙色 |

**定义文件**: `src/tools/AgentTool/built-in/statuslineSetup.ts`

功能非常专一：读取用户 shell 配置 → 转换 PS1 → 更新 `~/.claude/settings.json`。

### 3.7 `fork` — 隐式分支（实验性）

| 属性 | 值 |
|---|---|
| 角色 | 继承父对话完整上下文的分支执行 |
| 模型 | `inherit`（必须与父一致，为了 prompt cache） |
| 工具 | `['*']` + `useExactTools: true`（与父完全一致） |
| 权限模式 | `bubble`（权限弹窗冒泡到父终端） |
| 触发方式 | 省略 `subagent_type` 参数 |
| 特点 | 强制后台运行、防递归 fork |

**定义文件**: `src/tools/AgentTool/forkSubagent.ts`

不可被用户显式指定，不出现在 agent 列表中。详见[第六节](#六fork-agent-与-prompt-cache-共享机制)。

### 对比总表

| Agent | 读 | 写 | 模型 | 继承上下文 | 后台 | CLAUDE.md |
|---|---|---|---|---|---|---|
| general-purpose | Yes | Yes | 默认 | No | 可选 | Yes |
| Explore | Yes | No | haiku | No | 可选 | No |
| Plan | Yes | No | inherit | No | 可选 | No |
| verification | Yes | /tmp | inherit | No | 强制 | Yes |
| claude-code-guide | Yes+Web | No | haiku | No | 可选 | Yes |
| statusline-setup | Yes | ~/.claude | sonnet | No | 可选 | Yes |
| fork | Yes | Yes | inherit | Yes(全部) | 强制 | Yes |

---

## 四、上下文隔离机制

### 4.1 createSubagentContext — 核心隔离函数

每个子 agent 通过 `createSubagentContext()` 获得隔离的 `ToolUseContext`：

```typescript
// src/utils/forkedAgent.ts:345-462
export function createSubagentContext(
  parentContext: ToolUseContext,
  overrides?: SubagentContextOverrides,
): ToolUseContext {
  return {
    // === 隔离的状态（默认） ===

    // 文件状态缓存：从父 agent 克隆，互不干扰
    readFileState: cloneFileStateCache(parentContext.readFileState),

    // 全新集合：嵌套记忆触发器、技能追踪等
    nestedMemoryAttachmentTriggers: new Set<string>(),
    discoveredSkillNames: new Set<string>(),

    // AbortController：默认创建子控制器（父 abort 会传播到子）
    abortController: createChildAbortController(parentContext.abortController),

    // AppState 访问：默认包装为 shouldAvoidPermissionPrompts=true
    getAppState: () => ({
      ...state,
      toolPermissionContext: {
        ...state.toolPermissionContext,
        shouldAvoidPermissionPrompts: true,
      },
    }),

    // 所有 UI 回调：设为 undefined（子 agent 不能控制父 UI）
    addNotification: undefined,
    setToolJSX: undefined,

    // 变更回调：默认 no-op（不影响父 agent 状态）
    setAppState: () => {},
    setInProgressToolUseIDs: () => {},

    // 查询追踪链：新的 chainId + depth 递增
    queryTracking: {
      chainId: randomUUID(),
      depth: (parentContext.queryTracking?.depth ?? -1) + 1,
    },

    // === 可选共享的状态 ===
    setAppState: overrides?.shareSetAppState
      ? parentContext.setAppState : () => {},
    setResponseLength: overrides?.shareSetResponseLength
      ? parentContext.setResponseLength : () => {},
  }
}
```

### 4.2 runAgent 中的实际配置

```typescript
// runAgent.ts:700-714
const agentToolUseContext = createSubagentContext(toolUseContext, {
  options: agentOptions,
  agentId,
  messages: initialMessages,
  readFileState: agentReadFileState,
  abortController: agentAbortController,
  getAppState: agentGetAppState,
  // 同步 agent: 共享 setAppState（用于写入 hook 等）
  // 异步 agent: 完全隔离
  shareSetAppState: !isAsync,
  // 所有 agent 都共享响应长度追踪
  shareSetResponseLength: true,
});
```

### 4.3 AsyncLocalStorage 上下文隔离

`src/utils/agentContext.ts` 使用 Node.js 的 `AsyncLocalStorage` 实现并发 agent 的上下文隔离：

```typescript
const agentContextStorage = new AsyncLocalStorage<AgentContext>()

// 每个 agent 在独立的 ALS 上下文中运行
runWithAgentContext(asyncAgentContext, () => {
  // 这里面所有异步操作都能通过 getAgentContext() 获取正确的身份
  runAgent(...)
})
```

**为什么不用 AppState？** 当多个 agent 并发运行时（比如用户按 ctrl+b 后台化），AppState 是共享的单一状态，Agent A 的事件会错误地使用 Agent B 的上下文。AsyncLocalStorage 隔离了每个异步执行链。

上下文有两种类型（discriminated union）：

```typescript
// SubagentContext — Agent Tool 创建的子 agent
type SubagentContext = {
  agentId: string
  agentType: 'subagent'
  subagentName?: string       // e.g., "Explore", "Plan"
  isBuiltIn?: boolean
  invokingRequestId?: string  // 发起者的 request_id
  invocationKind?: 'spawn' | 'resume'
}

// TeammateAgentContext — Swarm 团队成员
type TeammateAgentContext = {
  agentId: string
  agentType: 'teammate'
  agentName: string           // e.g., "researcher"
  teamName: string
  parentSessionId: string
  isTeamLead: boolean
}
```

---

## 五、消息传递方式

### 5.1 普通 Subagent（无上下文继承）

父 agent 只传递一条 user message 作为任务指令：

```typescript
// AgentTool.tsx:538-540
promptMessages = [createUserMessage({ content: prompt })]
```

子 agent 拿到的初始消息 = `[]` + `[userMessage]`，不继承父对话历史。

### 5.2 Fork Subagent（完整上下文继承）

Fork 路径继承父 agent 的**完整对话上下文**：

```typescript
// AgentTool.tsx:630
forkContextMessages: isForkPath ? toolUseContext.messages : undefined

// runAgent.ts:370-373
const contextMessages = forkContextMessages
  ? filterIncompleteToolCalls(forkContextMessages)
  : []
const initialMessages = [...contextMessages, ...promptMessages]
```

`filterIncompleteToolCalls()` 过滤掉没有对应 `tool_result` 的 `tool_use` 消息，防止 API 报错。

### 5.3 上下文裁剪策略

```typescript
// runAgent.ts:387-398 — Explore/Plan 省略 CLAUDE.md
if (agentDefinition.omitClaudeMd) {
  const { claudeMd: _, ...userContextNoClaudeMd } = baseUserContext
  resolvedUserContext = userContextNoClaudeMd  // 节省 ~5-15 Gtok/week
}

// runAgent.ts:404-409 — Explore/Plan 省略 gitStatus
if (agentType === 'Explore' || agentType === 'Plan') {
  resolvedSystemContext = systemContextNoGit  // 节省 ~1-3 Gtok/week
}

// runAgent.ts:680-684 — 子 agent 默认关闭 thinking
thinkingConfig: useExactTools
  ? toolUseContext.options.thinkingConfig  // fork 继承
  : { type: 'disabled' }                   // 普通子 agent 关闭
```

---

## 六、Fork Agent 与 Prompt Cache 共享机制

### 6.1 背景：为什么需要 Fork Agent？

Fork Agent 解决的核心问题：**当父 agent 需要将一个大任务拆成多个子任务并行执行时，每个子 agent 都需要理解完整的对话上下文。**

传统 subagent（如 Explore、Plan）只接收一条简短的 prompt 消息，不知道之前的对话。而 Fork Agent 继承了父 agent 的全部对话历史——但这意味着每个 fork 子 agent 都要向 API 发送巨大的消息前缀。

Prompt cache 共享机制的目标：**让多个 fork 请求共享同一份 KV cache，只计费一次。**

### 6.2 Anthropic Prompt Cache 工作原理

Anthropic API 的 prompt cache 是**前缀匹配**的：

```
API 请求 = [system_prompt, tools, messages...]
Cache Key = 上述内容的字节级哈希（prefix match）
```

只要两个请求的前缀字节完全相同，第二个请求就能命中第一个的 cache。`cache_read_input_tokens` 比正常 input 便宜约 10 倍。

**关键约束：只要有一个字节不同，从不同位置开始，cache 就 miss。**

### 6.3 六层 Cache 工程

Fork 的整个设计围绕一个目标：**让所有 fork 子 agent 的 API 请求前缀字节级相同，只有最后一小段不同。**

#### 第一层：System Prompt 字节级一致

```typescript
// AgentTool.tsx:495-497
if (isForkPath) {
  if (toolUseContext.renderedSystemPrompt) {
    // 直接使用父 agent 已渲染的 system prompt 字节
    forkParentSystemPrompt = toolUseContext.renderedSystemPrompt;
  }
}
```

不重新生成 system prompt，因为构建过程包含 GrowthBook feature flag 等动态因素，两次调用可能产生不同字符串：

> "Reconstructing by re-calling getSystemPrompt() can diverge (GrowthBook cold→warm) and bust the prompt cache; threading the rendered bytes is byte-exact."

#### 第二层：Tool 定义字节级一致

```typescript
// AgentTool.tsx:627
availableTools: isForkPath
  ? toolUseContext.options.tools  // 直接使用父的工具数组
  : workerTools,                  // 普通 subagent 重新组装

// useExactTools 跳过 resolveAgentTools 过滤
// runAgent.ts:500-502
const resolvedTools = useExactTools
  ? availableTools          // 原样使用，不过滤
  : resolveAgentTools(...)  // 根据 agent 定义过滤
```

普通 subagent 的 `workerTools` 在不同 permissionMode 下组装，序列化结果与父不同。

#### 第三层：Thinking Config 一致

```typescript
// runAgent.ts:680-684
thinkingConfig: useExactTools
  ? toolUseContext.options.thinkingConfig  // fork: 继承父的
  : { type: 'disabled' },                  // 普通: 关闭 thinking
```

Thinking config（含 `budget_tokens`）也是 cache key 的一部分。

#### 第四层：消息前缀字节级一致（最精巧）

`buildForkedMessages()` 的设计：

```typescript
// forkSubagent.ts:92-93
// 所有 fork 子 agent 使用完全相同的占位符
const FORK_PLACEHOLDER_RESULT = 'Fork started — processing in background'

// forkSubagent.ts:141-168
const toolResultBlocks = toolUseBlocks.map(block => ({
  type: 'tool_result',
  tool_use_id: block.id,
  content: [{ type: 'text', text: FORK_PLACEHOLDER_RESULT }],  // 所有 fork 相同
}))

const toolResultMessage = createUserMessage({
  content: [
    ...toolResultBlocks,                                         // 所有 fork 相同
    { type: 'text', text: buildChildMessage(directive) },        // 每个 fork 不同
  ],
})
```

**效果**: N 个 fork 子 agent 的请求前 99%+ 的 token 字节完全相同，只有最后的 directive 不同。

消息结构示意：

```
Fork A 的 API 请求:
  [对话历史 msg1..msgN]                    ← 所有 fork 相同
  [assistant: 所有 tool_use blocks]         ← 所有 fork 相同
  [user: placeholder results...]            ← 所有 fork 相同
  [user: "Your directive: 分析认证模块"]     ← Fork A 独有

Fork B 的 API 请求:
  [对话历史 msg1..msgN]                    ← Cache HIT
  [assistant: 所有 tool_use blocks]         ← Cache HIT
  [user: placeholder results...]            ← Cache HIT
  [user: "Your directive: 重构存储层"]       ← Fork B 独有
```

#### 第五层：contentReplacementState 克隆

```typescript
// forkedAgent.ts:395-403
contentReplacementState:
  overrides?.contentReplacementState ??
  (parentContext.contentReplacementState
    ? cloneContentReplacementState(parentContext.contentReplacementState)
    : undefined),
```

Claude Code 有 tool result 内容替换机制（大结果用占位符替代）。Fork 子 agent 必须做出与父**相同的替换决策**，否则消息字节不一致。

#### 第六层：Cache 写入优化

```typescript
// claude.ts:3084-3089
const markerIndex = skipCacheWrite
  ? messages.length - 2   // fork: cache_control 标记在共享前缀的最后
  : messages.length - 1   // 正常: 标记在最后一条消息
```

Fork 子 agent 不把自己的独特尾部写入 cache，避免浪费存储：

> "For fire-and-forget forks we shift the marker to the second-to-last message: that's the last shared-prefix point, so the write is a no-op merge on mycro."

### 6.4 完整数据流图

```
父 Agent 第 N 轮 API 调用
  → system_prompt + tools + messages[0..N] 写入 Cache
  → 返回 assistant message（包含 3 个 Agent tool_use）

Fork 子 Agent A:
  ┌─────────────────────────────────────────────┐
  │ system_prompt    ← 父的渲染字节（字节相同） │  Cache HIT
  │ tools            ← 父的工具数组（字节相同） │  Cache HIT
  │ messages[0..N]   ← 父的对话历史（字节相同） │  Cache HIT
  │ assistant{all tool_uses}                     │  Cache HIT
  │ user{placeholder results}                    │  Cache HIT
  ├─────────────────────────────────────────────┤
  │ text: "Your directive: 分析认证模块"         │  新 token
  └─────────────────────────────────────────────┘

Fork 子 Agent B:
  ┌─────────────────────────────────────────────┐
  │         （完全相同的前缀）                    │  Cache HIT
  ├─────────────────────────────────────────────┤
  │ text: "Your directive: 重构存储层"           │  新 token
  └─────────────────────────────────────────────┘
```

**成本效果**: N 个 fork 的 input 成本约等于 `1 次完整 + (N-1) 次 cache_read`。

### 6.5 Fork Agent 的三个核心作用

1. **并行分治大任务**: 每个 fork 都有完整上下文，理解项目背景，不需要父 agent 重复解释
2. **极致的成本优化**: 通过 6 层 cache 工程，N 个 fork 的成本约为串行的 1/N + 小额 cache_read 费用
3. **统一的异步交互模型**: fork 功能开启时所有 agent spawn 都变成异步，通过 `<task-notification>` 通知

### 6.6 防护机制

**递归 fork 防护**: fork 子 agent 保留 Agent tool（为了 cache 一致性），但在 call time 阻止嵌套 fork。

```typescript
// AgentTool.tsx:332-334 — 双重检测
if (toolUseContext.options.querySource === `agent:builtin:fork`
    || isInForkChild(toolUseContext.messages)) {
  throw new Error('Fork is not available inside a forked worker.');
}
```

- 主检测: `querySource`（抗 autocompact，设在 context.options 上，不受消息重写影响）
- 回退检测: 消息扫描 `<fork-boilerplate>` tag

**严格输出格式**:
```
Scope: <回显任务范围>
Result: <发现或成果>
Key files: <相关文件路径>
Files changed: <修改文件 + commit hash>
Issues: <问题标记>
```

---

## 七、Agent 工具集管理

每种 agent 定义了允许/禁止的工具：

```typescript
// Explore Agent: 只读
{
  disallowedTools: ['Agent', 'ExitPlanMode', 'FileEdit', 'FileWrite', 'NotebookEdit'],
}

// StatusLine Agent: 极窄工具集
{
  tools: ['Read', 'Edit'],
}

// General Purpose: 全部工具
{
  tools: ['*'],
}
```

工具池通过 `resolveAgentTools()` 解析：
- `tools: ['*']` → 使用所有可用工具
- `tools: ['Bash', 'Read']` → 只使用指定工具
- `disallowedTools: ['Agent']` → 从全部工具中排除指定工具

Worker agent 独立组装工具池，不受父 agent 权限限制：

```typescript
// AgentTool.tsx:573-577
const workerPermissionContext = {
  ...appState.toolPermissionContext,
  mode: selectedAgent.permissionMode ?? 'acceptEdits'
};
const workerTools = assembleToolPool(workerPermissionContext, appState.mcp.tools);
```

---

## 八、生命周期管理

### 8.1 启动阶段

```
分配 agentId (createAgentId())
  → 注册 AsyncLocalStorage 上下文 (runWithAgentContext)
  → 初始化 agent 专属 MCP servers (initializeAgentMcpServers)
  → 注册 frontmatter hooks (registerFrontmatterHooks)
  → 预加载 skills
  → 记录初始 transcript (recordSidechainTranscript)
  → 进入 query() 循环
```

### 8.2 执行阶段

```
query() 循环
  → 每条消息 yield 给父 agent
  → 记录到 sidechain transcript
  → 转发 API metrics（TTFT/OTPS）到父
  → 检测 max turns 限制
```

### 8.3 清理阶段（finally 块）

```typescript
// runAgent.ts:816-858
finally {
  await mcpCleanup()                         // 清理 agent MCP servers
  clearSessionHooks(rootSetAppState, agentId) // 清理 session hooks
  cleanupAgentTracking(agentId)              // 清理 prompt cache tracking
  agentToolUseContext.readFileState.clear()   // 释放文件缓存内存
  initialMessages.length = 0                  // 释放 fork context messages
  unregisterPerfettoAgent(agentId)           // 释放 perfetto tracing
  clearAgentTranscriptSubdir(agentId)        // 释放 transcript subdir
  // 清理 todos 条目（防止长会话内存泄漏）
  rootSetAppState(prev => {
    const { [agentId]: _removed, ...todos } = prev.todos
    return { ...prev, todos }
  })
  // 杀死 agent 启动的后台 bash 任务
  killShellTasksForAgent(agentId, ...)
}
```

---

## 九、关键设计洞察

### 9.1 最小权限原则

每个 agent 只获得完成任务所需的最少工具和上下文：
- 读操作用小模型（haiku）节省成本
- 写操作用强模型（inherit/sonnet）保证质量
- 只读 agent 移除编辑工具从根本上防止误操作

### 9.2 Prompt Cache 是一等公民

Fork 机制的 6 层工程全部服务于 prompt cache 命中率。代码注释中反复出现 "bust the prompt cache" 的警告，说明 cache miss 是一个被高度重视的性能问题。

### 9.3 隔离优先，共享可选

`createSubagentContext()` 默认完全隔离，需要显式 opt-in 才能共享状态。这种"默认安全"的设计防止了并发 agent 互相干扰。

### 9.4 Token 优化无处不在

- Explore/Plan 省略 CLAUDE.md（~5-15 Gtok/week fleet-wide）
- Explore/Plan 省略 gitStatus（~1-3 Gtok/week）
- 子 agent 默认关闭 thinking
- Fork 的 6 层 cache 工程

### 9.5 防御性编程

- 递归 fork 有双重检测（querySource + 消息扫描）
- `filterIncompleteToolCalls()` 防止孤立 tool_use 导致 API 400
- 异步 agent 有独立 AbortController，不随父 ESC 取消
- 内存泄漏防护：finally 块清理 todos、bash tasks、file state cache
