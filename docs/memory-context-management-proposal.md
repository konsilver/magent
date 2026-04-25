# 记忆与上下文管理优化方案

本文档对 Claude Code、CoPaw/ReMe 的记忆与上下文管理机制进行深入调研，
结合 Jingxin-Agent 项目现状和行业最佳实践，提出系统性的改进方案。

> **Note:** 本方案基于 2026 年 3 月对各系统的调研结果。部分引用的外部项目
> 功能可能随版本更新而变化。

## 一、调研对象概览

本次调研覆盖三个代表性系统及行业趋势：

- **Claude Code** — Anthropic 官方 CLI 工具，代表"开发者个人助手"场景，
  侧重文件级持久记忆和自动上下文压缩。
- **CoPaw / ReMe** — AgentScope 生态的编码助手及其记忆管理库，代表
  "框架原生记忆系统"，侧重分层记忆和工具结果压缩。
- **行业实践** — mem0 最新能力、AgentScope CompressionConfig、
  Factory.ai 锚定式迭代摘要等前沿方案。
- **Jingxin-Agent** — 本项目，政务智能助手，侧重 mem0 向量记忆和
  多工具编排场景。

## 二、Claude Code 记忆与上下文机制

Claude Code 采用多层次的记忆和上下文管理架构，是当前最成熟的
开发者 AI 助手记忆系统之一。

### 2.1 自动记忆系统 (Auto Memory)

Claude Code 的自动记忆以文件系统为载体，实现跨会话的知识持久化：

| 层级 | 文件 | 加载时机 |
|------|------|----------|
| 项目指令 | `CLAUDE.md` | 每次会话完整加载 |
| 自动记忆索引 | `~/.claude/projects/<path>/memory/MEMORY.md` | 每次会话加载前 200 行 |
| 主题文件 | `memory/debugging.md` 等 | 按需加载（Claude 判断需要时） |
| 路径规则 | `.claude/rules/*.md` | 匹配文件路径时加载 |

**200 行限制机制**：这是 Claude Code 记忆系统的核心设计 —
`MEMORY.md` 仅前 200 行被加载到每次会话上下文中，超过部分不加载。
主题文件（如 `debugging.md`）也不在启动时加载，而是 Claude
判断需要时按需读取。这一机制迫使记忆保持简洁，将详细内容移至
独立文件。

**记忆写入策略**（按优先级）：

1. 用户明确要求记住的内容 — 立即写入，无需等待多次交互确认
2. 跨多次交互确认的稳定模式 — 架构决策、关键路径、用户偏好
3. 重复出现的问题解决方案 — 调试洞察、recurring issues

**不写入的内容**：

- 当前会话的临时状态（进行中的任务、临时上下文）
- 未经验证的推测性结论
- 代码中已经能看出的信息
- 与 CLAUDE.md 重复或矛盾的信息

**记忆组织原则**：

- 按**语义主题**组织，不按时间线
- `MEMORY.md` 作为索引文件，保持简洁（≤200 行），链接到详细主题文件
- 发现记忆过时或错误时主动更新或删除
- 去重合并，不简单追加

### 2.2 上下文窗口管理

Claude Code 的上下文管理具有明确的优先级和压缩策略。

**上下文组成**（按优先级从高到低）：

1. 系统提示（固定）
2. CLAUDE.md 文件（完整加载，压缩不可删除）
3. 自动记忆 MEMORY.md（前 200 行）
4. Skill 描述（始终加载）+ Skill 内容（按需）
5. MCP 工具定义（每次请求携带）
6. 对话历史（可压缩）
7. 文件内容和命令输出（可清除）

**自动压缩策略**（当接近上下文限制时）：

1. **优先清除旧工具输出** — token 消耗大、重要性最低
2. **对话历史摘要** — 保留请求和关键代码片段
3. **CLAUDE.md 和自动记忆** — 不可压缩，在 `/compact` 后重新注入
4. **对话早期指令可能丢失** — 这是设计决策，鼓励将持久规则写入
   CLAUDE.md

**手动控制**：

```bash
/compact                            # 自动摘要整个对话
/compact focus on API changes       # 带方向的定向摘要
/clear                             # 完全重置上下文
Esc + Esc → "Summarize from here"  # 选择性压缩
```

### 2.3 项目上下文层级 (CLAUDE.md)

Claude Code 的 CLAUDE.md 形成三层优先级体系：

| 优先级 | 位置 | 作用 |
|--------|------|------|
| 最高 | 系统策略目录 (`/etc/claude-code/`) | 组织级强制规则 |
| 中等 | 项目根 `./CLAUDE.md` | 团队共享，版本控制 |
| 最低 | 用户目录 `~/.claude/CLAUDE.md` | 个人偏好 |

**路径规则 (`.claude/rules/`)**：支持 YAML frontmatter 声明路径匹配，
只在 Claude 读取匹配文件时才加载，避免无关规则浪费上下文空间。

**@import 机制**：支持 `@README.md`、`@docs/guide.md` 引用外部文件，
最大嵌套深度 5 层，首次引用需用户确认。

### 2.4 会话持久化与检查点

Claude Code 会为每个编辑和用户提示创建检查点：

- 检查点在会话间持久化（默认 30 天清理）
- `claude --continue` 恢复最近会话
- `claude --resume` 从历史会话列表选择
- 恢复会话时重新加载完整对话历史
- 会话间权限不继承，需重新授权

### 2.5 关键设计理念

| 理念 | 实现 |
|------|------|
| 记忆即文件，文件即记忆 | 所有记忆以可读、可编辑的 Markdown 存储 |
| 渐进式积累 | 不急于写入，等模式稳定后再持久化 |
| 用户可控 | `/memory` 命令可浏览、编辑、删除记忆 |
| 降级安全 | 记忆丢失不影响核心功能 |
| 上下文经济 | 200 行限制 + 按需加载 = 最小上下文占用 |

## 三、CoPaw / ReMe 记忆与上下文机制

CoPaw 是基于 AgentScope 的本地编码助手，其记忆系统由独立库
[ReMe](https://github.com/agentscope-ai/ReMe) 提供支持。
ReMe 是目前开源生态中上下文管理最精密的系统之一。

### 3.1 ReMe 架构概览

ReMe 在 AgentScope 内存原语之上构建了完整的记忆层级：

```
AgentScope InMemoryMemory (基础)
  → ReMeInMemoryMemory (Token 感知扩展，原始对话持久化)
    → ReMeLight (文件级长期记忆 + 自动压缩)
      → CoPaw MemoryManager (扩展 ReMeLight，绑定 Agent 配置)
        → MemoryCompactionHook (pre_reasoning 钩子，自动压缩)
```

**两种后端**：

| 后端 | 核心类 | 检索方式 | 适用场景 |
|------|--------|----------|----------|
| 文件系统 | `ReMeLight` | 文件读取 + 全文搜索 | 轻量级个人助手 |
| 向量数据库 | `ReMeVectorBased` | 向量 70% + BM25 30% | 企业级应用 |

**目录结构**（ReMeLight）：

```
working_dir/
├── MEMORY.md                    # 长期持久信息（AI 自主决定写入什么）
├── memory/YYYY-MM-DD.md         # 每日自动摘要
├── dialog/YYYY-MM-DD.jsonl      # 原始对话记录（压缩前保留）
└── tool_result/<uuid>.txt       # 缓存的工具输出（TTL 3 天自动清理）
```

### 3.2 上下文窗口管理（核心能力）

ReMe 的上下文管理是所有调研对象中最精密的，具有多级阈值和结构化压缩。

**触发阈值配置**：

```python
class AgentsRunningConfig:
    max_input_length = 131_072      # 128K tokens
    memory_compact_ratio = 0.75     # 75% 时触发压缩
    memory_reserve_ratio = 0.10     # 压缩后保留 10% 余量
    token_count_estimate_divisor = 3.75  # 字符/token 比
```

**压缩通过 `MemoryCompactionHook` 实现，注册为 `pre_reasoning` 钩子**：

在每次推理步骤前自动执行：

1. **Token 计数** — 估算 system prompt + 所有消息的总 token 数
2. **阈值检查** — 超过 `memory_compact_threshold`（75%）时触发
3. **消息分组** — 分为 `[系统提示] + [可压缩内容] + [保留的最近消息]`，
   保持 user-assistant 轮次完整性，不拆分 tool_call/tool_result 对
4. **结构化摘要** — 使用 ReActAgent 生成包含以下结构的摘要：

   ```
   - Goal: 当前任务目标
   - Constraints: 约束条件
   - Progress: 已完成进度
   - Key Decisions: 关键决策
   - Next Steps: 下一步计划
   - Critical Context: 必须保留的上下文
   ```

5. **增量更新** — 如果已有先前摘要，增量合并而非全量重写
6. **标记压缩** — 标记为 compressed，检索时自动跳过
7. **优雅降级** — 如果压缩后消息验证失败，逐步减少保留的最近消息数

**压缩效果**：223,838 tokens → 1,105 tokens（99.5% 压缩率）。

### 3.3 工具结果压缩 (Tool Result Compaction)

ReMe 对工具结果实施**分级压缩策略**：

| 消息新旧 | 阈值 | 策略 |
|----------|------|------|
| 最近 1 条 (`tool_result_compact_recent_n`) | 30,000 字符 | 宽松保留 |
| 历史消息 | 1,000 字符 | 积极压缩 |
| 超过 3 天 (`tool_result_compact_retention_days`) | 0 | 自动清理 |

**超大工具输出处理**：

- 完整内容存入 `tool_result/<uuid>.txt`
- 消息中替换为文件路径引用（可回溯）
- 过期文件自动清理

### 3.4 持久记忆 (Persistent Memory)

ReMe 通过 **ReAct + 文件工具** 组合实现 AI 自主记忆管理：

- 调用 `summary_memory()` 时，启动一个 ReActAgent
- 该 Agent 配备 read/write/edit 文件工具
- AI 阅读当前 `MEMORY.md`，自主决定如何合并新信息
- 支持增量更新，避免覆盖已有重要记忆
- **后台异步执行**，不阻塞主推理流程

### 3.5 记忆检索

混合检索策略（向量后端）：

- **向量嵌入语义搜索**（权重 70%）
- **BM25 关键词匹配**（权重 30%）
- 结果去重 + 加权融合排序
- 注册为 `memory_search` 工具，Agent 可在对话中主动调用

### 3.6 个人记忆 (Personal Memory)

ReMe 的个人记忆模块包含**检索**和**摘要**两条管道：

**检索管道**：
```
set_query_op >> (extract_time_op | (retrieve_memory_op >> semantic_rank_op))
              >> fuse_rerank_op
```

**摘要管道**：
```
info_filter_op >> (get_observation_op | get_observation_with_time_op
                 | load_today_memory_op)
              >> contra_repeat_op >> update_vector_store_op
```

关键能力：时间感知检索、矛盾信息自动消解、每日记忆自动整理。

### 3.7 AgentScope 内存原语

CoPaw 利用的 AgentScope 原语及其扩展：

| 原语 | CoPaw 使用方式 |
|------|----------------|
| `InMemoryMemory` | 基础会话消息存储 |
| `ReActAgent` | 主推理循环 + 压缩/摘要内部 Agent |
| `pre_reasoning` hooks | `MemoryCompactionHook` + `BootstrapHook` |
| `Toolkit` | `memory_search` 注册为可调用工具 |
| `long_term_memory` 参数 | 通过 ReMeLight 扩展 |
| `msg_queue` | 压缩状态通过 SSE 流式推送 |
| `CompressionConfig` | 触发阈值、保留数、压缩模型、摘要 Schema |

**值得注意的是**，AgentScope 1.0 原生提供了 `CompressionConfig`：

```python
from agentscope.memory import InMemoryMemory, CompressionConfig

compression_config = CompressionConfig(
    trigger_threshold=4000,      # Token 阈值
    keep_recent=5,               # 保留最近 N 条
    compression_model=model,     # 可用独立小模型
    summary_schema=SummaryModel, # Pydantic schema 定义摘要结构
)

agent = ReActAgent(
    memory=InMemoryMemory(),
    compression_config=compression_config,
    long_term_memory=Mem0LongTermMemory(...),
    long_term_memory_mode="both",  # agent_control | static_control | both
)
```

这是 **Jingxin-Agent 可以直接利用的框架能力**，无需从零实现。

## 四、行业最佳实践

### 4.1 mem0 最新能力（v1.1+, 48K+ Stars）

mem0 作为 Jingxin-Agent 已集成的记忆框架，近期有重要更新：

**两阶段管道**：
- **提取阶段**：从用户-助手交换中提取候选事实
- **更新阶段**：对每个事实检索语义相似的已有记忆，
  LLM 决定 ADD / UPDATE / DELETE / NOOP

**图记忆 (mem0g)**：
- 将记忆存储为有向标签图 G=(V,E,L)
- 实体提取 → 关系推断 → 冲突检测 → 更新决策
- 支持 6 种图后端（Neo4j、Memgraph、Neptune 等）
- LOCOMO 基准测试：比 OpenAI 方案提升 26%

**多级记忆作用域**：
- `user_id` — 用户级记忆（跨会话）
- `run_id` — 会话级记忆（单次对话）
- `agent_id` — Agent 级记忆（特定 Agent 实例）

### 4.2 上下文管理主流模式

行业在 2025-2026 年收敛到以下几种主导模式：

**模式 A：滑动窗口 + 摘要混合**（最主流）

保留最近轮次原文，压缩旧上下文为 LLM 摘要。关键细节来自 Manus
团队分享：保留最近工具调用的原始格式以维持模型"节奏"，永远不要
压缩掉错误追踪信息。

**模式 B：锚定式迭代摘要**（Factory.ai, 最高精度）

不重新生成全量摘要，只对新丢弃的跨度做摘要。锚点结构：
intent（意图）、changes made（已变更）、decisions taken（已决策）、
next steps（下一步）。Factory 评测显示：准确性得分 4.04（Anthropic
3.74, OpenAI 3.43），在保留技术细节方面表现最佳。

**模式 C：子 Agent 架构**（Claude Code 模式）

为每个聚焦任务启动独立子 Agent，各自拥有干净的上下文窗口。每个
子 Agent 可能消耗数万 token，但只返回 1,000-2,000 token 的压缩结果。

**模式 D：上下文路由**

先分类查询，定向到正确的上下文源，避免无关信息进入窗口。

**关键统计**：2025 年约 65% 的企业 AI 失败归因于上下文漂移或记忆丢失，
而非原始上下文窗口不足。

### 4.3 多层记忆架构（认知科学启发）

现代 AI Agent 架构借鉴认知科学的四层记忆模型：

| 记忆类型 | 类比 | 存储内容 | 持久性 |
|----------|------|----------|--------|
| 工作记忆 | 注意焦点 | 当前上下文窗口 | 单轮/会话 |
| 情景记忆 | "发生了什么" | 具体事件、结果、时间戳 | 长期 |
| 语义记忆 | "我知道什么" | 事实、概念、偏好 | 长期 |
| 程序记忆 | "怎么做" | 成功模式、策略 | 长期 |

**关键演进趋势**：
- **情景→语义整合**：将具体经历持续转化为通用知识
- **分层压缩**：最近=全保真，较旧=摘要，最旧=仅事实
- **遗忘机制**：确定何时、何物应永久删除仍是未解决难题
  （ICLR 2026 MemAgents Workshop 重点研究方向）

### 4.4 生产系统参考

| 系统 | 核心策略 |
|------|----------|
| ChatGPT | 显式保存记忆 + 隐式历史引用 + 情景/语义双轨 |
| MemGPT/Letta | LLM 即 OS，自主管理"RAM"(上下文)+"硬盘"(外部存储) |
| Claude Memory | 2025 年 10 月推出付费用户记忆，2026 年 3 月支持记忆导入 |

## 五、Jingxin-Agent 当前状态分析

### 5.1 现有记忆系统 (mem0)

Jingxin-Agent 通过 mem0 框架实现跨会话记忆：

**已实现的能力**：

| 能力 | 实现 | 文件 |
|------|------|------|
| 事实提取 | 中文定制提示词 | `core/llm/memory.py:89-126` |
| 向量检索 | Milvus (limit=5) | `core/llm/memory.py:retrieve_memories()` |
| 图检索 | Neo4j (可选) | `core/llm/memory.py` (graph_store) |
| 记忆注入 | user role 消息前置 | `routing/memory_integration.py:inject_memories()` |
| 后台保存 | asyncio.create_task | `routing/memory_integration.py:save_memories_background()` |
| 记忆管理 API | GET/DELETE CRUD | `api/routes/v1/memories.py` |
| 用户级开关 | DB metadata 字段 | `users_shadow.metadata.memory_enabled` |
| 前端 UI | 设置面板 + 记忆列表 | `settingsStore.ts` + `SettingsModal.tsx` |
| qwen3 兼容 | 移除 dimensions 参数 | `core/llm/memory.py:165-172` |

**记忆检索流程**：
```
用户发送消息
  → 并发启动 memory_retrieval (与 agent 创建并行)
  → mem0.Memory.search(query, user_id, limit=5)
  → 格式化: "## 关于该用户的已知背景信息" + 图关系 (≤5条)
  → 以 user role 消息注入 session_messages[0]
```

**记忆保存流程**：
```
流式响应完成后 (yield meta 之后)
  → save_memories_background() fire-and-forget
  → mem0.Memory.add([user_msg, assistant_msg])
  → mem0 内部: LLM 事实提取 → 语义去重 → ADD/UPDATE/DELETE/NOOP
  → Milvus 向量写入 + Neo4j 图写入 (可选)
```

### 5.2 现有上下文管理

**会话消息加载**（`workflow.py` + `message_compat.py`）：

```python
# 全量加载，无 token 限制
history = list(session_messages)
if history and history[-1].get("role") in ("user", "human"):
    history.pop()
if history:
    await load_session_into_memory(history, agent.memory)
```

**Agent 内存**（`agent_factory.py:459`）：

```python
agent = ReActAgent(
    memory=InMemoryMemory(),  # 每次请求新建，无上下文限制
    max_iters=10,
)
```

**工具结果压缩**（`core/llm/summarization.py`，已实现）：

```python
# 50% token 使用率触发，保留最近 2 条工具结果
compress_in_turn_tool_results(messages, model,
    trigger_fraction=0.5, keep=2, model_max_tokens=128_000)
```

**会话标题摘要**（`core/llm/summarizer.py`）：

- 仅生成 ≤20 字的中文标题
- 不用于上下文管理
- 由 DB 配置的 summarizer 模型执行

**DB 消息存储**（`core/db/models.py`）：

- `chat_messages` 表，`content` 最大 100,000 字符
- 支持分页加载（默认 page_size=50），但 workflow 全量加载
- 索引：`chat_id + created_at`，支持高效时间排序

### 5.3 现有系统的局限性

经过深入代码分析和与调研对象对比，识别出以下关键缺陷：

#### 缺陷 1：无上下文窗口管理（P0 严重）

当前系统将**全部历史消息**加载到 `InMemoryMemory`，没有任何限制。
`PostgreSQLSessionStore.get_or_create()` 一次加载 page_size=1000 条消息。
长对话场景（>50 轮、多工具调用）必然触及模型上下文限制。

**对比**：Claude Code 自动压缩 + `/compact` 命令；ReMe 75% 阈值触发；
AgentScope 原生 `CompressionConfig`。

#### 缺陷 2：工具结果压缩未集成（P1）

`summarization.py` 中的 `compress_in_turn_tool_results()` 已完整实现，
包含 Token 估算、分组压缩、并行 LLM 摘要、已压缩标记检测等能力，
但在 `workflow.py` 和 `StreamingAgent` 中**完全没有调用入口**。

**对比**：ReMe 的 `MemoryCompactionHook` 自动在每次推理步骤前执行。

#### 缺陷 3：无跨轮次对话摘要（P1）

`ConversationSummarizer` 仅生成会话标题（≤20 字），不参与上下文管理。
没有 ReMe 风格的结构化摘要（Goal/Progress/Decisions），也没有
Factory 风格的锚定式迭代摘要。

#### 缺陷 4：mem0 检索缺乏精细控制（P2）

- 固定返回 top-5，无相关性评分阈值（低相关结果也会注入）
- 无时间衰减（半年前的记忆和今天的权重相同）
- 直接使用用户原始消息作为检索 query，无查询改写
- 未利用 mem0 的 `run_id` 做会话级记忆隔离

#### 缺陷 5：记忆注入角色不够精确（P2）

以 `user` role 注入记忆，代码注释说明是为兼容 Qwen 的 system 消息位置
限制。但这可能让模型误将记忆上下文当作用户输入进行回复。

#### 缺陷 6：未利用 AgentScope 原生能力（P1）

当前项目基于 AgentScope 构建，但未利用框架原生的：
- `CompressionConfig`（自动压缩）
- `long_term_memory` + `long_term_memory_mode`（长期记忆集成）
- `RedisMemory`（分布式记忆后端）

#### 缺陷 7：无 Token 预算管理（P1）

缺少对系统提示、记忆注入、历史消息、当前轮次的 Token 预算分配。
当系统提示随 KB 和技能增多而膨胀时，留给历史消息的空间被无感压缩。

## 六、改进方案

基于调研和分析，提出以下七个改进方向，按优先级排列。

### 方案 1：利用 AgentScope CompressionConfig（P0，快速见效）

**目标**：以最小改动量获得基本的上下文窗口保护。

**原理**：AgentScope ReActAgent 已原生支持 `CompressionConfig`，
只需在 `agent_factory.py` 中配置即可获得自动压缩能力。

**变更文件**：`src/backend/core/llm/agent_factory.py`（~20 行改动）

```python
from agentscope.memory import InMemoryMemory, CompressionConfig

# 创建压缩配置
compression_config = CompressionConfig(
    trigger_threshold=96_000,     # 128K 的 75%
    keep_recent=6,                # 保留最近 3 轮对话
    compression_model=get_summarize_model(stream=False),
)

agent = ReActAgent(
    name="jingxin_agent",
    sys_prompt=system_prompt,
    model=default_model,
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
    compression_config=compression_config,   # 新增
    max_iters=10,
)
```

**效果**：长对话自动压缩，零新增文件，框架级保障。

### 方案 2：上下文预算管理器 (Context Budget Manager)

**优先级**：P0 — 解决消息加载失控问题

**目标**：在消息加载到 Agent Memory 之前，按预算裁剪。

**新增文件**：`src/backend/core/llm/context_manager.py`

```python
from dataclasses import dataclass

@dataclass
class ContextBudget:
    """Token 预算分配，各分区独立管控"""
    model_context_window: int = 128_000
    system_prompt_reserve: int = 10_000   # 系统提示（含技能描述）
    memory_reserve: int = 2_000           # mem0 记忆注入
    output_reserve: int = 4_096           # 模型输出预留
    tool_reserve: int = 20_000            # 工具调用 + 结果
    safety_margin: float = 0.10           # 10% 安全边际

    @property
    def history_budget(self) -> int:
        """留给历史消息的可用 Token 数"""
        used = (self.system_prompt_reserve + self.memory_reserve
                + self.output_reserve + self.tool_reserve)
        available = self.model_context_window - used
        return int(available * (1 - self.safety_margin))


class ContextWindowManager:
    """加载历史消息前的预算管控"""

    CHARS_PER_TOKEN = 2.5  # 中文平均估算

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()

    def estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))

    def trim_history(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
    ) -> list[dict]:
        """从最新消息向前保留，直到用完预算。
        保持 user-assistant 轮次完整性。"""
        budget = max_tokens or self.budget.history_budget
        result = []
        used = 0
        # 从后向前扫描，按轮次保留
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            tokens = self.estimate_tokens(msg.get("content", ""))
            if used + tokens > budget:
                break
            result.insert(0, msg)
            used += tokens
            i -= 1
        return result
```

**修改文件**：`src/backend/routing/workflow.py`

```python
# 在 load_session_into_memory 之前
from core.llm.context_manager import ContextWindowManager
ctx_mgr = ContextWindowManager()
session_messages = ctx_mgr.trim_history(session_messages)
```

### 方案 3：历史对话结构化摘要 (History Summarization)

**优先级**：P1 — 与方案 2 配合，将截断变为智能摘要

**目标**：对超出预算的历史消息生成结构化摘要，而非简单丢弃。

**新增文件**：`src/backend/core/llm/history_summarizer.py`

**摘要模板**（借鉴 ReMe 结构化摘要 + Factory 锚定式摘要）：

```python
SUMMARY_TEMPLATE = """请对以下对话历史生成结构化摘要，用于保持对话连续性：

## 用户意图
用户在这段对话中的主要目标和需求

## 关键信息与决策
- 提到的重要数据、事实、结论
- 已做出的关键决策

## 已完成操作
- 调用的工具及其核心结果（保留数据，省略冗余）

## 当前状态
- 待处理的问题或后续步骤
- 需要保持的上下文约束

对话内容：
{conversation}

请使用以上结构直接输出摘要。保持简洁，重点保留对后续对话有价值的信息。"""
```

**核心能力**：

| 能力 | 说明 |
|------|------|
| 增量摘要 | 基于已有摘要 + 新消息更新，不全量重算 |
| 结构化输出 | 固定 Intent/Info/Actions/State 四段式 |
| 异步预计算 | 首次请求用截断，后台生成摘要供下次用 |
| DB 缓存 | `chat_sessions.context_summary` 字段 |

**DB Schema 变更**（需 Alembic migration）：

```python
# core/db/models.py ChatSession 表新增
context_summary = Column(Text, nullable=True)
summary_up_to_msg_id = Column(String(36), nullable=True)
```

**集成方式**：

```python
# context_manager.py
async def prepare_context(self, session_messages, chat_id):
    if self.history_exceeds_budget(session_messages):
        summary = await self.get_or_create_summary(chat_id, session_messages)
        split = self.find_budget_split(session_messages)
        return [
            {"role": "system", "content": f"[历史对话摘要]\n{summary}"},
            *session_messages[split:]
        ]
    return session_messages
```

### 方案 4：工具结果压缩集成 (Tool Result Compaction)

**优先级**：P1 — 已有实现，仅需集成

**目标**：将 `compress_in_turn_tool_results()` 接入主流程。

**修改文件**：`src/backend/routing/streaming.py`

**方案 A（推荐）：使用 AgentScope hook**

```python
# 在 agent_factory.py 中注册 post-tool hook
async def tool_result_compaction_hook(agent, kwargs):
    """工具调用后检查是否需要压缩旧工具结果"""
    from core.llm.summarization import compress_in_turn_tool_results
    messages = await agent.memory.get_all()
    compressed = await compress_in_turn_tool_results(
        messages,
        model=get_summarize_model(stream=False),
    )
    if len(compressed) < len(messages):
        agent.memory.clear()
        await agent.memory.add(compressed)

agent._instance_pre_reply_hooks["tool_compaction"] = tool_result_compaction_hook
```

**增强 `summarization.py`**：

- 添加分级阈值（借鉴 ReMe：最近 30K，历史 1K）
- 添加外部缓存选项（大结果写文件，消息中保留摘要 + 引用路径）

### 方案 5：mem0 检索优化

**优先级**：P2 — 提升注入质量

**修改文件**：`src/backend/core/llm/memory.py`,
`src/backend/routing/memory_integration.py`

**5.1 相关性过滤 + 时间衰减**：

```python
async def retrieve_memories(
    user_id: str,
    query: str,
    limit: int = 10,           # 扩大召回范围
    min_score: float = 0.55,   # 最低相关性阈值
) -> str:
    ...
    items = result.get("results", [])

    # 相关性过滤
    items = [m for m in items if m.get("score", 0) >= min_score]

    # 时间衰减 (半衰期 ~70 天)
    for m in items:
        age_days = days_since(m.get("updated_at", ""))
        decay = math.exp(-0.01 * age_days)
        m["adjusted_score"] = m.get("score", 0) * (0.7 + 0.3 * decay)

    # 按调整后分数重排，取 top-5
    items.sort(key=lambda m: m.get("adjusted_score", 0), reverse=True)
    items = items[:5]
```

**5.2 记忆注入方式改进**：

```python
# 使用更明确的边界标记
memory_msg = {
    "role": "user",
    "content": (
        "<system_memory_context>\n"
        f"{memory_context}\n"
        "</system_memory_context>\n"
        "（以上为系统检索到的用户历史记忆，作为回答参考背景，"
        "不是用户当前提问的一部分。）"
    )
}
```

**5.3 记忆作用域说明**：

mem0 长期记忆只按 `user_id` 维度存取，**不传 `chat_id` / `run_id`**。
原因：传入 `run_id` 后 mem0 只返回该 run 内保存的记忆，跨会话记忆
反而检索不到。会话级上下文由消息历史 + 结构化摘要（方案 2/3）承载，
不需要 mem0 重复管理。

### 方案 6：AgentScope 长期记忆原生集成

**优先级**：P2 — 替代当前手动集成方式

**目标**：利用 AgentScope 原生的 `long_term_memory` 参数替代手动
记忆注入逻辑。

```python
from agentscope.memory import Mem0LongTermMemory

long_term_mem = Mem0LongTermMemory(
    config=_build_mem0_config(),
    user_id=current_user_id,
)

agent = ReActAgent(
    memory=InMemoryMemory(),
    compression_config=compression_config,
    long_term_memory=long_term_mem,
    long_term_memory_mode="both",  # 框架自动 + Agent 自主
)
```

**优势**：
- 消除 `memory_integration.py` 中的手动注入逻辑
- 框架统一管理记忆检索/保存时机
- Agent 可主动决定何时存取长期记忆

### 方案 7：多层记忆架构 (Multi-Level Memory)

**优先级**：P3 — 长期演进方向

**目标**：构建完整的短期/中期/长期/程序性四层记忆体系。

```
┌─────────────────────────────────────────────────────┐
│                    记忆检索与组装层                    │
│  (向量语义 70% + BM25 关键词 30% + 时间衰减)         │
└──────┬──────────────┬──────────────┬───────────────┘
       │              │              │
┌──────▼─────┐ ┌──────▼─────┐ ┌─────▼──────────────┐
│ 工作记忆    │ │ 会话记忆    │ │ 长期记忆            │
│ (L1 短期)  │ │ (L2 中期)  │ │ (L3 持久)          │
│            │ │            │ │                    │
│ InMemory   │ │ DB summary │ │ 语义记忆: mem0     │
│ Memory     │ │ + Redis    │ │ (Milvus+Neo4j)    │
│            │ │            │ │                    │
│ 当前对话   │ │ 结构化摘要  │ │ 情景记忆: 对话归档 │
│ 完整消息   │ │ 增量更新    │ │ (JSONL 日志)      │
│            │ │            │ │                    │
│ 生命周期:  │ │ 生命周期:   │ │ 程序记忆: 模式库  │
│ 单次请求   │ │ 会话级      │ │ (成功策略)         │
└────────────┘ └────────────┘ └────────────────────┘
```

**记忆类型分工**：

| 记忆类型 | 存储 | 内容示例 | 更新时机 |
|----------|------|----------|----------|
| 工作记忆 | InMemoryMemory | 当前对话完整消息 | 实时 |
| 会话记忆 | DB context_summary | "用户在分析宁波GDP数据" | 异步增量 |
| 语义记忆 | mem0 Milvus | "用户是财政局预算处张三" | 对话后提取 |
| 情景记忆 | 对话归档日志 | 具体交互记录及结果 | 压缩时归档 |
| 图记忆 | Neo4j | "张三 → 所属 → 财政局" | 实体关系提取 |
| 程序记忆 | 模式库 (未来) | "生成报告最佳工具链" | 成功模式提取 |

## 七、实施路线图

### 第一阶段：基础保障（1-2 周）

| 序号 | 任务 | 方案 | 改动量 | 预期效果 |
|------|------|------|--------|----------|
| 1 | 启用 CompressionConfig | 方案 1 | ~20 行 | 自动压缩保护 |
| 2 | 加载前预算裁剪 | 方案 2 | ~100 行新增 | 防止上下文溢出 |
| 3 | 集成工具结果压缩 | 方案 4 | ~30 行改动 | 工具密集场景可控 |
| 4 | 测试长对话场景 | — | — | 验证保护有效 |

**交付物**：长对话不再报错，工具密集场景上下文可控。
**风险**：最低，纯增量改动。

### 第二阶段：质量提升（2-3 周）

| 序号 | 任务 | 方案 | 改动量 | 预期效果 |
|------|------|------|--------|----------|
| 5 | 历史对话结构化摘要 | 方案 3 | ~200 行新增 | 摘要替代截断 |
| 6 | DB schema + 摘要缓存 | 方案 3 | migration | 减少重复计算 |
| 7 | mem0 检索优化 | 方案 5 | ~50 行改动 | 记忆注入更精准 |
| 8 | 记忆注入标记改进 | 方案 5 | ~10 行 | 减少模型混淆 |

**交付物**：历史信息不再简单丢失，记忆注入质量提升。

### 第三阶段：框架演进（2-4 周）

| 序号 | 任务 | 方案 | 改动量 | 预期效果 |
|------|------|------|--------|----------|
| 9 | AgentScope 长期记忆原生集成 | 方案 6 | 重构 | 简化代码 |
| 10 | 混合检索（向量+BM25） | 方案 7 | 新增 | 检索召回提升 |
| 11 | 记忆衰减与清理策略 | 方案 5/7 | 新增 | 防止记忆膨胀 |
| 12 | 性能基准测试 | — | — | 量化改进效果 |

**交付物**：完整的多层记忆体系，可量化的上下文管理效果。

## 八、各系统对比总结

| 能力 | Claude Code | CoPaw/ReMe | Jingxin 现状 | 第一阶段后 | 全部完成后 |
|------|-------------|------------|-------------|-----------|-----------|
| 上下文窗口管理 | 自动压缩 | 75%阈值+结构化摘要 | **无** | CompressionConfig | 预算管理+摘要 |
| Token 预算 | 内置分区 | 精确配置 | **无** | 预算裁剪 | 多分区管控 |
| 历史摘要 | 自动压缩 | 增量摘要+每日归档 | 仅标题 | 自动压缩 | 结构化摘要+缓存 |
| 工具结果压缩 | 优先清除旧输出 | 分级阈值+外部缓存 | **已写未用** | hook 集成 | 分级+缓存 |
| 长期记忆 | 文件 MEMORY.md | 文件+向量 | mem0 向量+图 | 不变 | 原生集成 |
| 记忆检索 | 文件读取 | 向量70%+BM25 30% | 纯向量 top-5 | 不变 | 混合检索+衰减 |
| 多层记忆 | 2层(文件+上下文) | 3层完整架构 | 2层(内存+向量) | 不变 | 4层完整架构 |
| 框架利用度 | N/A | 深度利用 AgentScope | **低** | CompressionConfig | 原生长期记忆 |

## 九、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 摘要丢失关键信息 | 对话连续性中断 | 第一阶段用截断作 fallback，摘要作增强 |
| Token 估算偏差 | 上下文溢出或浪费 | 预留 10% 安全边际；后续引入 tiktoken |
| 摘要增加延迟 | 首 token 延迟变长 | 异步预计算+缓存；首次用截断不等摘要 |
| DB schema 变更 | 迁移失败 | `DEFAULT NULL` 确保向后兼容 |
| 模型 role 兼容性 | Qwen 不支持多 system | 保持 user role + 明确边界标记 |
| AgentScope 版本依赖 | API 变更 | 锁定 agentscope>=1.0.0 |

## 十、参考资料

### 项目与框架

- [ReMe: Memory Management Kit for Agents](https://github.com/agentscope-ai/ReMe)
- [CoPaw: Personal AI Assistant](https://github.com/agentscope-ai/CoPaw)
- [mem0: AI Memory Layer](https://github.com/mem0ai/mem0)
- [AgentScope](https://github.com/agentscope-ai/agentscope)

### 技术文档

- [Claude Code Memory Documentation](https://code.claude.com/docs/en/memory.md)
- [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices.md)
- [Effective Context Engineering for AI Agents - Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [The Context Window Problem - Factory.ai](https://factory.ai/news/context-window-problem)
- [AgentScope Memory Management Patterns](https://deepwiki.com/modelscope/agentscope/9.5-memory-management-patterns)

### 研究论文

- [mem0 Research Paper (ECAI)](https://arxiv.org/abs/2504.19413)
- [Memory in the Age of AI Agents Survey](https://arxiv.org/abs/2512.13564)
- [AgentScope 1.0 Paper](https://arxiv.org/html/2508.16279v1)
