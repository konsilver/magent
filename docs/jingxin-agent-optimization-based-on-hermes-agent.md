# 基于 Hermes Agent 的 Jingxin-Agent 优化方案

> 作者：Claude（Opus 4.7, 1M context）
> 日期：2026-04-19
> 定位：对齐 NousResearch/hermes-agent 开源项目的工程实践，提出可落地的 Jingxin-Agent 优化稿。
> 参考对象：
> - [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)（主仓库）
> - [NousResearch/hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution)（离线自进化管线）
> - [mudrii/hermes-agent-docs](https://github.com/mudrii/hermes-agent-docs)（按版本快照的详细文档）
> - Hermes 版本：0.7.0 / 0.8.0（2026 年 2 月首发）

---

## 0. 执行摘要（TL;DR）

Hermes Agent 是 NousResearch 于 2026 年 2 月开源的「会自我成长」的 Agent harness，重点提供三层自我迭代能力：

1. **In-session 自维护**：让模型自行改写 `MEMORY.md`、`USER.md`、`SKILL.md` 等 Markdown 文件，配合周期性 nudges。
2. **Skills 过程性记忆**：5+ 工具调用后自动调用 `skill_manage create` 沉淀新技能，运行中若技能过时则用 `skill_manage patch` 增量修补。
3. **Offline 进化管线**：独立仓库使用 DSPy + GEPA（ICLR 2026）从真实 / 合成 trajectory 进化出新的 prompt / skill，只生成 PR，不自动提交。

Jingxin-Agent 的当前形态是「**已具备可观测、模块化的工程骨架，但仍是一个只产出日志、不自我学习的 Agent**」：

- 强项：ReActAgent + Hooks + MCP 双传输、prompt 分段 + DB override、mem0 长期记忆（Milvus + 可选 Neo4j）、75% 上下文压缩、Plan-Mode、可观测日志系统。
- 缺口：
  - 压缩逻辑较裸：没有 Hermes 风格的「首尾保留 + tool_call 配对保护 + 孤儿消息清理 + 谱系链」。
  - Memory 单向注入：mem0 记忆进得去、出不来；没有质量反馈、谱系回溯、短时 frozen 快照。
  - Skills 是「静态资产」：既不会自动沉淀新技能，也不会基于执行情况自我修补。
  - Prompt 迭代全靠人：admin panel 手工写，没有 A/B、trajectory、评测集驱动的进化管线。
  - Prompt cache：目前是 300s 本地 TTL，没有利用 Anthropic/OpenRouter cache_control 断点。

本文给出 **9 项借鉴清单 + 3 阶段落地路线图**，并指到具体文件和可修改位点。

---

## 1. Hermes Agent 架构快速梳理

### 1.1 核心 Loop（`agent/run_agent.py::AIAgent`）

一次对话 turn 的内部过程：

1. 追加 user message
2. 通过 `prompt_builder.py` 构建（或复用缓存）system prompt
3. 压缩预检：如果估算 tokens > 上下文窗口 50%，或 gateway 压力 > 85%，调用 `ContextCompressor`
4. 根据 provider 自动适配三种 API 模式：`chat_completions` / `codex_responses` / `anthropic_messages`
5. 后台线程发请求，可中断
6. 解析响应；`tool_calls` 通过 `model_tools.handle_function_call()` 派发：
   - `_NEVER_PARALLEL_TOOLS`（如 `clarify`）串行
   - 其余在「全部只读」或「命中非重叠路径」时进 `ThreadPoolExecutor(max_workers=8)` 并行
7. 强制 message 交替不变式，`IterationBudget`（父 90 / 子 50）防无限循环，70%/90% 预算插入 nudge 引导收尾
8. 无 tool_calls → 持久化 + 返回

### 1.2 Prompt 10 层堆栈（`agent/prompt_builder.py`）

严格分层以保留 prompt cache 命中：

1. Agent identity（`~/.hermes/SOUL.md` 或 `DEFAULT_AGENT_IDENTITY`）
2. 行为常量：`MEMORY_GUIDANCE` / `SESSION_SEARCH_GUIDANCE` / `SKILLS_GUIDANCE`
3. Honcho 集成块
4. 可选用户 override
5. **会话开始瞬间冻结**的 MEMORY 快照
6. **会话开始瞬间冻结**的 USER profile
7. 技能 index（按 platform / toolset / fallback 条件过滤）
8. `.hermes.md` / `AGENTS.md` / `.cursorrules` 自动发现的上下文文件
9. 时区感知时间戳 + session id
10. 平台提示（Telegram、Discord、CLI…）

**Ephemeral 部分**（`ephemeral_system_prompt`、单轮 Honcho 上下文、prefill、gateway 会话覆盖）**故意放在缓存块之外**，保证 provider cache 长期有效。

> 注入前每份 context 文件都会走 `_scan_context_content()` 扫描：不可见 Unicode（U+200B–U+200F）、已知 prompt-injection 短语、curl 外传命令等，命中替换为 `[BLOCKED: …]`。

### 1.3 Prompt Caching（`agent/prompt_caching.py`）

`system_and_3` 策略：4 个 Anthropic `cache_control` 断点 —— 1 个打在 system prompt 尾，3 个滚动打在最近 3 条非 system 消息。`api_mode == "anthropic_messages"` 或 OpenRouter + Claude 模型时启用。官方声称多轮对话输入成本下降 ~75%。

### 1.4 Context Compressor（`agent/context_compressor.py`）

- 触发：50% 预检 或 gateway 85%
- 保留：首 10 条 + 尾 20 条
- 对齐 `tool_call` / `tool_result` 成对边界
- 中段调用辅助 LLM（temperature=0.3）生成摘要
- 摘要前缀 `[CONTEXT COMPACTION]` + `SUMMARY_PREFIX`，明确告诉模型「前面工作可能已经做过，不要重复执行」
- 清理孤儿：被摘要吞掉调用的 tool_result 丢弃；失去 result 的 tool_call 改 stub
- 压缩后可派生子会话 `parent_session_id`，**压缩谱系在 DB 里是一等公民**

### 1.5 Memory：内建 + 插件双层

**内建**（模型自己维护的 Markdown）：

| 文件 | 用途 | 硬上限 |
| --- | --- | --- |
| `~/.hermes/MEMORY.md` | 事实（项目、技术决策、偏好） | ~2200 字符 |
| `~/.hermes/USER.md` | 身份 / 沟通风格 | ~1375 字符 |
| `~/.hermes/SOUL.md` | 人格 / identity | 自由 |

- 会话开始冻结快照 → prompt cache 不失效
- `MEMORY_GUIDANCE` 指令 + 周期性 nudge 让模型自己更新
- 字符级硬上限（两文件合计 ~1.3k tokens）是刻意设计

**会话 DB**（`hermes_state.py::SessionDB`，SQLite WAL schema v6）：

- `sessions`（含 `parent_session_id` 构成压缩谱系）
- `messages`（v6 新增 `reasoning` / `reasoning_details` / `codex_reasoning_items` 列，专门保留推理轨迹）
- `messages_fts`（FTS5 虚表 + 自动触发器，全局全文搜索 + snippet）
- `maybe_auto_title()` 后台低温生成 3–7 词标题

**插件 provider**（`plugins/memory/<name>/`）：

- 生命周期钩子：`initialize()` / `get_tool_schemas()` / `sync_turn()` / `on_session_end()` / `system_prompt_block()`
- 同一时间只能启用一个 provider
- 数据流：system prompt 静态注入 → 每 turn 前预取 → 每次响应后 `sync_turn()`（非阻塞）→ 会话结束 `on_session_end()` 提纯
- 官方内建 8 家：**Honcho / OpenViking / Mem0 / Hindsight / Holographic / RetainDB / ByteRover / Supermemory**

### 1.6 Self-Iteration 三个尺度

**A. In-session（秒级）**：`MEMORY_GUIDANCE` + `SKILLS_GUIDANCE` + 周期 nudge，让模型自改自己的记忆/技能/soul。

**B. Skill 过程性记忆（天级）**：
- 路径：`~/.hermes/skills/<category>/<name>/SKILL.md`
- Frontmatter：`name` / `description` / `version` / `platforms` / `requires_tools` / `requires_toolsets` / `fallback_for_toolsets`
- 三级渐进披露：Level 0 是 ~3k token 的 name+description 索引打进 system prompt；Level 1 按需加载完整 SKILL.md；Level 2 按需加载引用的辅助文件
- **任意一个复杂任务（5+ tool calls）结束后，agent 会自己调用 `skill_manage create`** 沉淀技能
- 使用中发现技能过时，调 `skill_manage patch`（增量 diff，省 token）
- 条件激活：一个 skill 可以只在某个 toolset 不可用时作为 fallback 出现

**C. Offline 进化（周级）**：独立仓库 `hermes-agent-self-evolution`：
- **DSPy + GEPA**（Genetic-Pareto Prompt Evolution，ICLR 2026 Oral）
- 不是随机变异 —— GEPA 基于 trajectory 的失败原因生成「定向变异」
- 辅助：MIPROv2（贝叶斯 few-shot 优化）、Darwinian Evolver（AGPL 外置的代码级进化）
- 5 阶段：SKILL.md → tool descriptions → system prompt sections → 实现代码 → 连续管线
- 守门：2550+ pytest 必须全绿、skill ≤15KB、description ≤500 字、语义保留校验、基准评测（TBLite 快评 / TerminalBench2 完评）
- **永远只开 PR，不 auto-commit**；单次运行 $2–$10

### 1.7 Trajectory 收集（`agent/trajectory.py`）

每次运行序列化 JSONL：`{conversations, timestamp, model, completed}`。Reasoning 规范化成 `<think>…</think>`，tool call 变结构化区域；成功进 `trajectory_samples.jsonl`，失败进 `failed_trajectories.jsonl`，**`ephemeral_system_prompt` 被显式剔除**（保证数据集干净）。`TrajectoryCompressor` 以 `moonshotai/Kimi-K2-Thinking` 分词，目标 ~15250 tokens/条（首轮 + 末 4 轮），直接喂 SFT / RL。

### 1.8 Hooks（观察者唯一）

10 种 plugin hook：`on_session_start` / `pre_llm_call` / `pre_api_request` / `post_api_request` / `pre_tool_call` / `post_tool_call` / `post_llm_call` / `on_session_end` / `on_session_finalize` / `on_session_reset`。

**所有 hook 都是 observer-only**，**唯一例外是 `pre_llm_call`** —— 允许返回 `{"context": "..."}` 临时追加一轮 system prompt，不入缓存、不持久化。Hook 异常全捕获走 WARN，不会传到主管线。

### 1.9 值得直接抄的小点

1. 70%/90% token 预算自动插入 closure nudge
2. `parent_session_id` 构成压缩谱系
3. `SUMMARY_PREFIX = "前面工作可能已经做过"` 的显式反内耗提示
4. 会话开始冻结 memory 快照，保留 prompt cache
5. `pre_llm_call` 钩子返回 `{"context": ...}` 做一次性动态注入
6. Markdown memory 硬字符上限（MEMORY 2200 / USER 1375）
7. 入口文件的 prompt-injection 扫描（`_scan_context_content`）
8. 5+ tool_call 自动 `skill_manage create`
9. DSPy + GEPA 只开 PR 不 auto-commit 的离线进化范式
10. `SessionDB` v6 的 reasoning 列（保留 `<think>` 未来做 SFT）

---

## 2. Jingxin-Agent 现状扫描

> 所有行号来自当前 `aaronzhu` 分支。

### 2.1 Agent 构建（`src/backend/core/llm/agent_factory.py:203-664`）

- Phase 1 预热 skill metadata（`264-280`）
- Phase 2 MCP toolkit（`282-365`）：stdio 按需/走连接池；HTTP/SSE 直连 `HttpStatefulClient`
- Phase 3 skill + 工具注册（`367-419`）：沙箱 `register_sandboxed_view_text_file()`
- Phase 4 prompt 组装（`420-469`）：config parts + DB override + subagent 分支
- Phase 5 可选 mem0（`593-607`）
- Final ReActAgent（`628-646`）：
  - `CompressionConfig` 阈值 75%（按模型名调整）
  - `parallel_tool_calls=True`
  - 两个 pre-reply hooks
- `agent._jx_context = ModelContext()`（`649`）作为 hooks 的运行时数据载体

**问题**：压缩只是「触发后保留最后 6 turn」，没有 tool_call 对齐、没有孤儿清理、没有 parent 链。

### 2.2 Hooks（`src/backend/core/llm/hooks.py:1-324`）

- `make_dynamic_model_hook()`（`71-82`）：按 `enable_thinking` 热切主/快模型
- `make_file_context_hook()`（`214-323`）：首 turn 一次性注入；历史文件只注入「摘要 + `read_artifact` 工具」；图片转多模态块；50KB/文件截断

**问题**：
- 全是 pre-reply，没有 post-reply、post-tool、session-end
- 没有观察者接口给日志/评测/自进化用
- 没有像 Hermes `pre_llm_call` 那种**允许一次性注入但不污染 cache** 的口子

### 2.3 Mem0 长期记忆（`core/llm/memory.py`, `routing/memory_integration.py`）

- 按 `user_id` 跨会话检索（不是按 chat）
- 自定义事实抽取 prompt（`119-156`）
- 相关性 0.4 阈值 + 70% 基础 + 30% 时间衰减（~70 天半衰）
- Top-5 带 `<system_memory_context>…</system_memory_context>` 包裹注入
- 保存异步 `asyncio.create_task()`，失败双重试（Milvus gRPC reset）

**问题**：
- 没有质量反馈闭环，抽错无法修正
- 没有按 session 冻结快照的机制（每次请求都重取）
- 没有图谱 / 事件级 / 人物级分层（Hermes 的 Mem0 + Honcho + Hindsight 是多 provider 并存）
- 没有 Hermes 风格的「短小 bounded Markdown」辅助层（完全依赖向量库）

### 2.4 Prompt 组装（`src/backend/prompts/prompt_runtime.py:1-624`）

- 段落：`00_time_role` → `05_anti_hallucination` → `10_abilities` → `20_tools_policy` → `30_multiagent` → `35_skills` → `40_flow` → `50_data_rules` → `60_format` → `65_citations` → `70_chart` → `80_special`
- 300s 本地 TTL，key 包含工具名 / MCP key / DB 版本 / KB
- DB `AdminPromptPart` override 按 `sort_order` 合成
- Tool routing hints **硬编码 dict**（`TOOL_ROUTING_HINTS`）

**问题**：
- cache 只是本地 in-process TTL，没有利用 Anthropic `cache_control` 断点（每轮 system prompt 仍然算 full input tokens）
- Routing hints 不会随执行数据进化
- Subagent prompt（`304-385`）没有 multiagent 段，但也没有分层索引 / frontmatter 过滤

### 2.5 Routing / Workflow（`routing/workflow.py`, `routing/strategy.py`）

- `strategy.py` 实际永远返回 `"main"`（`24-37`），LLM router 是占位
- `astream_chat_workflow()`（`503-819`）：mem0 并发拉取 + 上下文窗口裁剪 + 文件注入 + 子 agent 解析 + SSE 五种事件
- 引用解析 `extract_citations()`

**问题**：没有基于历史命中率的路由自学习；`LLM_ROUTER` 是 TODO。

### 2.6 Plan Mode（`routing/subagents/plan_mode.py:1-1055`）

- Phase 1 生成 JSON plan（`214-331`）
- Phase 2 逐 step 执行（`364-891`）：独立 agent + 工具/技能/agent 约束 + 15s 心跳 + 取消检查 + DB 工具调用日志
- 失败自动继续下一步（`821`）
- 结束清理 MCP 子进程（`894-927`）

**问题**：
- **不做 reactive replanning**：某 step 失败只是跳过
- 没有 plan 执行情况的历史统计，下次同类任务不会更聪明
- 没有 plan 执行后的「技能沉淀」动作

### 2.7 Skills（`agent_skills/loader.py`, `agent_factory.py:183-200, 368-408`, `workflow.py:73-108`）

- **三级渐进披露已具备**：
  - Level 0 — `loader.py:116-153 load_all_metadata()` 只读 frontmatter 的 `AgentSkillMetadata`
  - Level 1 — `loader.py:155-241 load_skill_full()` 按需加载完整 SKILL.md
  - Level 2 — 每个 skill 下的 `references/*.md` 由沙箱 `view_text_file` 按需读
- Frontmatter 已规范化：`name / display_name / description / license / tags / allowed_tools / metadata.{version, category}`
- `_scripts.json` 提供脚本执行白名单；不存在时按扩展名自动检测（`_auto_detect_scripts`）
- 多源加载（`MultiSourceSkillLoader`）：built-in / user / project 多源合并 + 优先级覆盖
- DB skill 通过 `_materialize_skill_files` 落到 `~/.cache/jingxin-agent/skills/<id>/`
- 沙箱 `view_text_file` + 条件 `run_skill_script`；注入用 `<skill_instructions skill="...">…</skill_instructions>`

**仍然缺的（相对 Hermes）**：
- Frontmatter 没有 `requires_tools / requires_toolsets / fallback_for_toolsets / platforms`，Level 0 索引无法按工具可用性或平台过滤
- Level 0 索引进 system prompt 时没有 token 预算上限，技能多了线性膨胀
- 没有 fallback 机制（主 toolset 缺失时才浮现降级 skill）
- 最关键的：所有 skill 靠人类手写提交到 repo 或 admin DB，**没有从成功 trajectory 自动蒸馏 / 自动修补**的闭环

### 2.8 自进化 / 反思

整个仓库既没有 `reflect*`、也没有 `self_improve*`、也没有 `trajectory_sample*`、也没有 `prompt_evolve*` —— **一个也没有**。可观测系统（b74d48f 提交）仅产出日志，**没有任何消费端**把日志喂回模型 / prompt / skill。

---

## 3. 差距清单（9 项优先级建议）

| # | 借鉴项 | Hermes 对应位点 | Jingxin 落地位置 | 难度 | 预期收益 |
| --- | --- | --- | --- | --- | --- |
| P0-1 | Anthropic `cache_control` 分层 prompt cache | `agent/prompt_caching.py` system_and_3 | `core/llm/agent_factory.py` + `chat_models.py` 调用层 | 中 | 输入成本 -50%~75% |
| P0-2 | Context Compressor 的 tool_call 对齐 + 孤儿清理 + SUMMARY_PREFIX | `agent/context_compressor.py` | `core/llm/agent_factory.py` CompressionConfig（或自实现前置 hook）| 中 | 压缩后事实稳定性↑，避免重复执行 |
| P0-3 | Token 预算 70/90% 自动 closure nudge | `IterationBudget` + pressure nudges | `routing/workflow.py` astream 循环 | 低 | 抑制无限工具链、降尾 token 成本 |
| P1-1 | Markdown 「bounded memory」辅助层（MEMORY.md / USER.md） | `memory_manager.py` + 字符硬限 | `core/llm/memory.py` 新增 `bounded_memory.py` + DB 表 | 中 | 给 mem0 补上低延迟 / cache 友好的「头脑字典」 |
| P1-2 | 会话开始冻结 memory 快照 | `prompt_builder.py` 第 5/6 层 | `routing/workflow.py` 请求入口处 freeze 一次；hooks 只读 | 低 | 保证 prompt cache 有效 + 会话内记忆一致 |
| P1-3 | Skills frontmatter + 三级渐进披露 + 条件激活 | `skill_utils.py` / `skill_commands.py` | `core/llm/skills/` 新增 loader 扩展 + `prompts/prompt_text/.../35_skills.system.md` | 中 | 省 system prompt tokens + 让 skill 索引更智能 |
| P1-4 | Prompt-injection 扫描入口守卫 | `_scan_context_content` | `core/content/file_parser.py` 或 hooks | 低 | 安全硬化，政务场景刚需 |
| P2-1 | 5+ tool_call 自动生成 skill；`skill_manage patch` 增量修补 | `skill_commands.py` | `routing/workflow.py` 结束事件 + 新 MCP server `skill_mcp` | 高 | 真正的「过程性记忆」 |
| P2-2 | Trajectory 采集 + 离线 DSPy+GEPA 进化（仅 PR）| `hermes-agent-self-evolution` | 新增 `src/backend/evolution/` + GitHub Actions | 高 | prompt / skill 性能-驱动迭代 |
| P2-3 | Plan 失败的反应式重规划 + plan 谱系 | —（Hermes 用 session 谱系代偿）| `routing/subagents/plan_mode.py` | 中 | Plan 成功率↑，失败透明度↑ |

（上表仅列 10 项，第 11+ 项如 Memory provider 多家并存、多 API 模式适配器、code-connect / skill store 等不在本轮）

---

## 4. 详细优化方案

以下每节给出：**动机 → 设计 → 代码落点 → 风险 / 回滚**。

---

### 4.1 【P0-1】Prompt cache：启用 Anthropic `cache_control` 分层断点

**动机**：当前 `prompt_runtime.py` 的 TTL 缓存只避免重复**组装字符串**，一旦请求发到 provider，每轮 system prompt 仍然全量计费。Hermes `system_and_3` 方案声称输入成本降 ~75%，在 Jingxin 的「长 system + 多轮对话」场景里收益极大。

**设计**：

1. Prompt 分层重构（保证前缀稳定性）：
   - **冻结前缀层**（打 cache_control 断点 #1）：
     - 所有 `prompt_text/<version>/system/*.md` 合成
     - 工具 routing hints（按工具清单变化但 turn 内不变）
     - KB 轻量目录
     - 时区字符串里 `{now}` 留作占位符，**不**写入缓存前缀，而是放到末尾的 ephemeral 块
   - **冻结记忆层**（断点 #2）：
     - 会话启动时冻结的 bounded memory 快照（4.2 节）
     - mem0 top-k 快照（只取一次，后续 turn 复用）
   - **滚动 3-turn 断点**（#3/#4）：
     - 最近 3 条非 system 消息各打一个断点
2. 改造 `core/llm/chat_models.py` 的请求构造层：
   - 判断 provider/model：`anthropic_messages` 或 `openrouter + claude-*` 启用；`deepseek-r1`、`qwen*` 等暂不启用（provider 不支持）
   - 将以上四个断点以 `{"type": "ephemeral"}` 写到 Anthropic 请求体

3. Hooks 的临时注入改走 **ephemeral 通道**：
   - 现有 `make_file_context_hook` 会把首 turn 的文件内容塞进 system prompt，这会破坏 cache；改为塞成 `additional_system_messages` 并打断点 #3 之前

**代码落点**：

- `src/backend/core/llm/agent_factory.py:628-646` Agent 创建处增加 `prompt_cache_config`
- 新增 `src/backend/core/llm/prompt_caching.py`（参考 `hermes/agent/prompt_caching.py`）
- 改造 `src/backend/prompts/prompt_runtime.py` 的 `build_system_prompt()` 返回 `(prefix, ephemeral_suffix)` 二元组，而非拼好的单串

**风险**：
- provider fallback 链路（比如 deepseek）需要保留「不打断点」的路径
- 跨 model 切换（如 `enable_thinking` hook 热切）会让 cache 失效；应在切换时重建 cache 签名

**回滚**：默认 feature flag `PROMPT_CACHE_ENABLED=false`。

---

### 4.2 【P0-2】Context Compressor：对齐 tool_call + 孤儿清理 + 反内耗前缀

**动机**：当前 `CompressionConfig` 只是 AgentScope 自带的「保留最后 6 turn + LLM 摘要」，容易把 `tool_call` 和配对的 `tool_result` 切断，模型看到半吊子消息会重复执行（政务场景经常调大模型跑查询，这意味着重复计费 + 数据错位）。

**设计**：

实现一个 `core/llm/jx_context_compressor.py`，结构参照 Hermes：

```python
class JxContextCompressor:
    protect_first_n = 3
    protect_last_n = 10
    trigger_ratio = 0.5
    summary_temperature = 0.3
    SUMMARY_PREFIX = (
        "[CONTEXT COMPACTION] 以下为此前对话的摘要。"
        "请注意：摘要中提到的调研、工具调用、文件读取等工作**可能已完成**，"
        "不需要再次执行。如需原始细节请从最近消息中引用或调用对应工具。"
    )

    async def maybe_compress(self, messages, model_ctx_window):
        if estimate_tokens(messages) / model_ctx_window < self.trigger_ratio:
            return messages
        head = messages[:self.protect_first_n]
        tail = messages[-self.protect_last_n:]
        middle = messages[self.protect_first_n:-self.protect_last_n]
        middle = self._align_tool_pairs(middle, head, tail)
        summary = await self._summarize(middle)
        cleaned_tail = self._drop_orphan_tool_results(tail, summary_ids)
        cleaned_tail = self._stub_orphan_tool_calls(cleaned_tail, summary_ids)
        return head + [system(self.SUMMARY_PREFIX + summary)] + cleaned_tail
```

`_align_tool_pairs` 的核心是：**middle 的起始若落在 `tool_call` 之前的 assistant 消息，要把对应 `tool_result` 一起拉进来；middle 的结束若落在 `tool_call` 之后但 `tool_result` 之前，要把 tool_result 一起拉进来**。这样摘要过程不会看见单腿 tool_call。

**代码落点**：

- 新增 `src/backend/core/llm/jx_context_compressor.py`
- `agent_factory.py:579-591` 的 `CompressionConfig` 改走我们的压缩器（AgentScope 支持自定义 hook-based 压缩）
- 子 agent（`_astream_subagent_direct`）和 Plan Mode 共用同一个压缩器

**可选增强 / Hermes 同款**：
- **压缩谱系**：每次压缩写一条 `chat_sessions.parent_session_id → 当前 chat_id`，配合一个「展开父会话原始消息」的新工具，允许模型按需回溯原始细节。这个需要 DB schema 小改（alembic 新 revision）。

**风险**：错误摘要可能让模型偏离事实；在 prompt 里强调「摘要可能不精确，如有冲突以最近消息为准」。

---

### 4.3 【P0-3】IterationBudget + 70/90% Closure Nudge

**动机**：当前 `routing/workflow.py` 的 agent loop 没有 tool-call 预算。模型卡死时最坏会无限打 MCP，拖到 SSE 超时。

**设计**：

```python
@dataclass
class IterationBudget:
    max_iterations: int = 50
    soft_warn_ratio: float = 0.7
    hard_warn_ratio: float = 0.9
    used: int = 0

    def consume(self) -> Optional[str]:
        self.used += 1
        ratio = self.used / self.max_iterations
        if self.used == self.max_iterations:
            return "HARD_STOP"
        if ratio >= self.hard_warn_ratio:
            return (
                "[系统提示] 已使用 90% 工具调用预算，"
                "请立即基于现有信息给出最终回答。"
            )
        if ratio >= self.soft_warn_ratio:
            return (
                "[系统提示] 已使用 70% 工具调用预算，"
                "请开始收敛，避免不必要的工具调用。"
            )
        return None
```

Subagent / Plan-mode step 的预算是父预算的一半（Hermes：父 90 / 子 50）。

**代码落点**：
- `src/backend/core/llm/agent_factory.py` 创建 agent 时挂 `agent._jx_budget = IterationBudget()`
- `src/backend/core/llm/hooks.py` 新增 `pre_tool_call` hook（AgentScope 支持 tool-level hooks），消费预算并注入 nudge
- 超 hard 限制则直接让 agent 进入「只回答不工具」模式（可通过临时覆盖 tool schema 为空）

**风险**：低；关键是 soft 警告是「system 角色的临时消息」，避免污染下一轮 prompt cache 前缀 —— 应走 ephemeral 层。

---

### 4.4 【P1-1/P1-2】Bounded Markdown Memory + 会话开始冻结快照

**动机**：mem0 + Milvus 很强，但每次 turn 都要做向量检索，**既慢又不保证命中**；对于「这个用户是谁 / 偏好什么 / 正在做什么」这类高频、低变异的信息，Hermes 的 `MEMORY.md`（2200 字符）+ `USER.md`（1375 字符）是一套**低延迟 + cache 友好**的「热记忆」。Jingxin 现在全压在向量库上，有点重。

**设计**：

1. DB 新表（alembic revision）：

```sql
CREATE TABLE bounded_memories (
    user_id VARCHAR(64) PRIMARY KEY,
    memory_md TEXT,          -- <= 2200 chars (soft)
    user_md TEXT,            -- <= 1375 chars (soft)
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by VARCHAR(32)   -- 'agent' / 'user' / 'admin'
);
```

2. Session start 冻结：

`astream_chat_workflow` 入口处读一次 bounded_memories，放进 `ModelContext.frozen_memory_snapshot`。这一轮 session 内所有 turn 共享同一份快照，保证 prompt cache 前缀稳定。

3. 模型自维护工具：新增两个 MCP tool（或直接集成到 toolkit）：

- `memory_md_append(text, reason)`：追加事实；超 2200 字符时自动触发 `memory_md_compact`
- `memory_md_compact()`：让辅助模型（低温、同 `summary_temperature=0.3`）压缩回 2200 字符
- `user_md_update(patch, reason)`：更新用户画像

4. System prompt 增加 `MEMORY_GUIDANCE` 段（抄 Hermes 的描述 + 本地化）：

```
当你发现用户有明确、长期、可复用的事实（偏好、身份、背景、项目信息），
调用 memory_md_append 保存。保存的内容必须是**事实**，不是**推测**，
不能包含隐私/敏感数据（身份证、手机号、密码）。
```

5. 周期 nudge：每 10 turn 注入一次 ephemeral「检查记忆是否需要更新」的提示（走 ephemeral 层，不污染 cache）。

**代码落点**：
- `src/backend/core/llm/bounded_memory.py`（新建）
- `src/backend/core/db/models.py` 新增 ORM
- `src/backend/alembic/versions/xxxx_bounded_memories.py`
- `src/backend/prompts/prompt_text/v1/system/15_memory_guidance.system.md`（新增段）
- `src/backend/mcp_servers/bounded_memory_mcp/`（新 MCP server）
- `src/backend/routing/workflow.py` session 入口 freeze + 注入冻结快照

**与 mem0 的关系**：
- mem0 依然是**冷长期记忆**，放大规模、全量事实
- bounded_memory 是**热短档**，优先展示在 system prompt 里给模型直接看
- 事实抽取优先落 mem0；当某条事实被连续 3 个会话检索命中 → 提升到 bounded_memory

---

### 4.5 【P1-3】Skills 条件激活 + 索引预算 + fallback 机制（补差）

> **现状澄清**：Jingxin **已经实现三级渐进披露**：
> - **Level 0** — `agent_skills/loader.py:116-153 load_all_metadata()`，只读 frontmatter 返回 `AgentSkillMetadata(id/name/description/version/tags/allowed_tools)`，不解析正文。
> - **Level 1** — `loader.py:155-241 load_skill_full()` 按需加载完整 SKILL.md。
> - **Level 2** — 每个 skill 目录下的 `references/*.md`，由模型通过沙箱 `view_text_file` 按需读取。
>
> 且 frontmatter 已规范化（`name / display_name / description / license / tags / allowed_tools / metadata.{version, category}`），`_scripts.json` 提供脚本白名单。
>
> 因此**本节不是「新建三级披露」，而是在现有三级披露之上补 Hermes 相对 Jingxin 多出来的三件事**。

**动机**：Hermes 与 Jingxin 在「三级披露」这个骨架上是一致的，但 Hermes 还有三层 Jingxin 尚未覆盖的差异：

1. **条件激活**：frontmatter 声明 `requires_tools` / `requires_toolsets` / `platforms`，不满足条件的 skill 在 Level 0 index 阶段就被过滤，连 name+description 都不进 system prompt。
2. **Fallback 模式**：`fallback_for_toolsets` 让 skill 只在某个主 toolset **缺失时**才浮现（例：免费层缺失「企业付费 API」时才暴露降级 skill）。
3. **Level 0 索引预算**：Hermes 给 Level 0 一个 ~3k token 硬上限，超限截断；Jingxin 当前在 `35_skills.system.md` + `agent_factory.py:367-419` 的注入路径没有显式预算，技能多了线性膨胀。

**当前 Jingxin 的缺口定位**：
- `agent_skills/registry.py` 的 `AgentSkillMetadata` 只有 `id/name/description/version/tags/allowed_tools`，**没有** `requires_tools / requires_toolsets / platforms / fallback_for_toolsets`。
- `agent_skills/selector.py`（若存在筛选逻辑）+ `agent_factory.py:367-419` 的 `register_skills_to_toolkit(toolkit, skill_ids=…)` 只按 user 启用列表过滤，不做工具可用性联动过滤。
- `register_skills_to_toolkit` 会把**所有**启用 skill 注册进 toolkit；Level 0 的 name+description 究竟如何拼进 system prompt 依赖 AgentScope 的 `register_agent_skill` 实现，目前没有 token 预算上限。

**设计**：

1. 扩展 frontmatter（向后兼容，无这些字段时等价于「总是启用」）：

```markdown
---
name: minimax-xlsx
description: "…"
tags: spreadsheet,xlsx,excel
allowed_tools: [run_skill_script, view_text_file]
metadata:
  version: "1.0"
  category: productivity
# 新增字段
activation:
  requires_tools: [run_skill_script]         # 这些工具全启用才激活
  requires_toolsets: []                      # 这些 toolset 全启用才激活
  fallback_for_toolsets: [export_table_excel] # 当主 toolset 缺失时才出现
  platforms: [linux]                         # 平台白名单
---
```

2. `AgentSkillMetadata` 增加可选字段 `activation: Optional[SkillActivation]`；`_load_skill_metadata_from_file` / `_load_skill_metadata_from_str` 解析时填充。

3. 在 Level 0 → system prompt 的路径上加一个**过滤 + 预算**阶段（建议放在 `agent_skills/selector.py` 或 `agent_factory.py:367-419` 的注册前）：

```python
def filter_by_activation(
    skills: dict[str, AgentSkillMetadata],
    enabled_tools: set[str],
    enabled_toolsets: set[str],
    platform: str,
) -> tuple[list[str], list[str]]:
    primary, fallback = [], []
    for sid, meta in skills.items():
        act = meta.activation or SkillActivation()
        if act.platforms and platform not in act.platforms:
            continue
        if act.requires_tools and not set(act.requires_tools).issubset(enabled_tools):
            continue
        if act.requires_toolsets and not set(act.requires_toolsets).issubset(enabled_toolsets):
            continue
        if act.fallback_for_toolsets:
            if any(ts not in enabled_toolsets for ts in act.fallback_for_toolsets):
                fallback.append(sid)
            continue
        primary.append(sid)
    return primary, fallback


def build_level0_index(
    primary: list[str],
    fallback: list[str],
    metadata: dict[str, AgentSkillMetadata],
    token_budget: int = 3000,
) -> str:
    lines: list[str] = ["# 可用技能索引（完整内容请用 view_text_file 加载 SKILL.md）"]
    used = estimate_tokens(lines[0])
    for sid in primary + fallback:
        m = metadata[sid]
        tag = "[fallback] " if sid in fallback else ""
        line = f"- {tag}`{sid}` — {m.name}：{truncate(m.description, 120)}"
        t = estimate_tokens(line)
        if used + t > token_budget:
            lines.append(f"- …（共 {len(primary)+len(fallback)} 技能，已截断以控制长度）")
            break
        lines.append(line)
        used += t
    return "\n".join(lines)
```

4. `prompts/prompt_text/v1/system/35_skills.system.md` 改动点：
   - 说明 Level 0 展示的是「索引」，不是完整内容；
   - 明确 `[fallback]` 前缀的含义（仅主工具缺失时使用）；
   - 明确在执行前必须先用 `view_text_file(<skill_path>/SKILL.md)` 加载 Level 1。

**代码落点**（精确到现有文件）：
- `src/backend/agent_skills/registry.py`：`AgentSkillMetadata` + `AgentSkillSpec` 增加 `activation` 字段；`_load_skill_metadata_from_*` 解析 `activation` 子树。
- `src/backend/agent_skills/selector.py`（若已存在则扩展；否则新建）：`filter_by_activation()` + `build_level0_index()`。
- `src/backend/core/llm/agent_factory.py:367-419`：`register_skills_to_toolkit` 前先 `filter_by_activation`，只注册 primary + fallback。
- `src/backend/prompts/prompt_runtime.py`：`35_skills` 段组装处调用 `build_level0_index` 并尊重 `token_budget`。
- `src/backend/prompts/prompt_text/v1/system/35_skills.system.md`：文案说明 fallback 语义与 Level 1 加载约定。

**风险**：
- 现有 SKILL.md **不需要全量改**：无 `activation` 字段的 skill 默认「总是激活」，完全兼容。
- `enabled_tools` 与 `enabled_toolsets` 的口径需要统一（从 `catalog.json` 的 `is_enabled()` 抽一次快照传给 filter）。
- `token_budget` 首版建议 3000；观察实际压缩率再调整。

---

### 4.6 【P1-4】Prompt-Injection 入口扫描

**动机**：政务场景允许用户上传 Word / Excel / 网页 URL 抓取。Hermes 对 context 文件做 `_scan_context_content()` 扫描：隐形 Unicode（U+200B–U+200F）、`ignore previous instructions`、`curl … $KEY` 等。Jingxin 的 `core/content/file_parser.py` 目前没有注入检测。

**设计**：

实现 `core/infra/prompt_guard.py`：

```python
SUSPICIOUS_UNICODE = re.compile(r"[\u200b-\u200f\u202a-\u202e]")
INJECTION_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"system prompt override",
    r"you are now",
    r"disregard the above",
    r"新的系统提示",
]
EXFIL_PATTERNS = [
    r"curl .*\$\{?[A-Z_]+\}?",
    r"wget .*\$\{?[A-Z_]+\}?",
]

def scan_and_redact(text: str) -> tuple[str, list[str]]:
    warnings = []
    text = SUSPICIOUS_UNICODE.sub(lambda m: "[BLOCKED: U+%04X]" % ord(m.group()), text)
    for pat in INJECTION_PATTERNS + EXFIL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            warnings.append(pat)
    return text, warnings
```

**代码落点**：
- 新建 `src/backend/core/infra/prompt_guard.py`
- `core/content/file_parser.py` 每个 parser 返回前过滤
- `core/llm/hooks.py` 的 `make_file_context_hook` 注入前再过滤一次
- 命中时写 `security_audit_log` 表（可观测层已有）

**风险**：误报；所以是**标注 + 记录**，不是硬拒；如果命中 `EXFIL_PATTERNS` 这种明显恶意才拒绝。

---

### 4.7 【P2-1】自动 Skill 沉淀：5+ Tool Calls 触发 `skill_manage create/patch`

**动机**：这是 Hermes 最有意思的自学习机制 —— **每个成功的复杂任务都会自动沉淀为一条技能**。Jingxin 目前每次复杂请求都是「从零开始」。

**设计**：

1. Trajectory 采集（在 `routing/workflow.py` SSE 结束时）：

```python
@dataclass
class Trajectory:
    chat_id: str
    user_id: str
    user_query: str
    tool_calls: list[dict]   # [{name, args, result_summary, duration_ms}]
    final_answer: str
    success: bool            # 启发式：最后一条不是 error + 没触发 hard-stop
    agent_route: str
```

2. 判决规则（简版，无需模型）：
   - `len(tool_calls) >= 5` 且 `success=True`
   - 无人工负反馈（下一轮用户没有「不对/重来/错了」等关键词）
   - 未命中敏感白名单（政务数据查询、批量导出等需要人工审核）

3. 后台异步发起「技能蒸馏」子 agent（低优先级 queue）：

```python
SKILL_DISTILLER_PROMPT = """
你将阅读一段成功完成的复杂任务 trajectory，判断：
1. 这个任务是否存在可复用的「解法步骤」？
2. 如果存在，写一份 SKILL.md（严格 frontmatter + 步骤 + 样例输入输出）
3. 如果步骤已经被某个现有 skill 覆盖，仅产出一个 patch.md（增量）
4. 如果判断无价值，输出 `{"decision": "skip"}`

Trajectory:
{trajectory}

已有技能索引：
{existing_skills_index}
"""
```

4. 产出 SKILL.md / patch.md 走**草稿状态**，进 `skills/_drafts/`，**不自动启用**；admin panel 审核后移入正式目录。

**代码落点**：
- 新增 `src/backend/evolution/skill_distiller.py`
- 新增 `src/backend/api/routes/v1/admin_skills.py`（审核接口）
- `src/frontend/src/components/admin/` 新增草稿审批页面
- `routing/workflow.py` SSE 结束 hook 里排队蒸馏任务（使用已有的异步任务基础设施）

**风险**：
- 数据隐私：trajectory 里可能包含 PII/敏感 query；入库前走 4.6 的 prompt_guard + mem0 的 fact 过滤
- 成本控制：蒸馏本身是 LLM call，设全局日预算（默认 $1/day），超限 skip

---

### 4.8 【P2-2】Trajectory + DSPy/GEPA 离线进化（只开 PR）

**动机**：Hermes 把 prompt/skill 进化完全解耦在独立仓库里，用 trajectory 作为评测数据，用 DSPy + GEPA 做定向变异。这是最工程化的 prompt 迭代方案，Jingxin 目前 prompt 改动全靠人感觉。

**设计**：

1. Trajectory JSONL 采集（同 4.7，区分 success/failure，剔除 ephemeral）：

```
data/trajectories/trajectory_samples.jsonl
data/trajectories/failed_trajectories.jsonl
```

脱敏：所有用户原文走 `prompt_guard` + 按政务数据安全规范做 token 替换（手机号 → `<PHONE>` 等）。

2. 独立目录 `src/backend/evolution/`：

```
evolution/
  collector.py         # 从可观测日志 + SSE 结束 hook 收集 trajectory
  datasets/
    build_eval_set.py  # 按 route/skill 切分构造评测集
  evolvers/
    prompt_evolver.py  # 包装 DSPy + GEPA
    skill_evolver.py
  evaluators/
    task_success.py    # 任务成功率
    citation_recall.py # 引用准确性
    cost_efficiency.py # tokens / call
  run_evolution.py     # CLI 入口
```

3. GitHub Actions（`.github/workflows/prompt-evolution.yml`）每周跑一次，产物 = 开 PR 到 `prompts/prompt_text/` 或 `skills/`。PR 附：
   - 新旧 prompt diff
   - 评测集分数对比
   - Top-10 改进 trajectory / Top-10 仍失败 trajectory
   - 成本报表

4. 守门（Hermes 做法直接抄）：
   - 运行前 `make test` 全绿
   - Skill ≤ 15KB，description ≤ 500 字
   - 语义保留校验（用原 prompt 的关键指令 regex 做命中检测）
   - 至少一条评测维度不能退化（Pareto）
   - **永不 auto-commit**

**代码落点**：全新目录，不侵入现有代码。

**风险**：
- 政务合规：trajectory 里含真实业务数据，**必须**在脱敏之后存储，存储位置限内网
- 预算：建议首阶段只跑 `prompt_text/v3/system/` 的 3–5 个关键段，不碰 skill 代码

---

### 4.9 【P2-3】Plan Mode 反应式重规划 + 谱系

**动机**：当前 `plan_mode.py:821` 是「step 失败 → 继续下一步」，结果是失败在报告末尾不声不响。Hermes 的会话谱系机制可以直接迁移：plan 的每次失败 / 重规划产生一个新的 plan 节点，父节点指针指回原 plan。

**设计**：

1. DB 表 `plan_instances` 新增 `parent_plan_id`、`trigger_reason`（`user_submit` / `step_failure_reprompt` / `user_revision`）
2. Step 执行 catch 到错误时，**不是继续**，而是：
   - 收集错误摘要
   - 调用一次轻量 LLM：基于原 plan + 已完成 step + 错误，判定是否需要重规划（或仅跳过）
   - 若重规划：新建子 plan，从当前 step 之后的序列开始替换
3. 前端 Plan 详情页渲染谱系树（已有 plan 详情页，小改即可）

**代码落点**：
- `src/backend/routing/subagents/plan_mode.py` 新增 `_reactive_replan()`
- `src/backend/core/db/models.py` Plan ORM 补字段 + alembic revision
- 前端 `AdminAgentManager` / plan 详情组件

---

## 5. 三阶段路线图

### 阶段 I：零侵入提效（1–2 周）

**目标**：不动大架构，快速拿到性能 + 成本 + 稳定性红利。

| 任务 | 负责模块 | 验收 |
| --- | --- | --- |
| 4.1 Prompt cache（先启 anthropic_messages 路径） | `core/llm/chat_models.py` + `prompt_caching.py` | Anthropic 请求 cache_read_tokens > 50% |
| 4.2 `JxContextCompressor`（tool_call 对齐 + SUMMARY_PREFIX） | `core/llm/jx_context_compressor.py` | 压缩后 3 条内不再出现孤儿 tool_* |
| 4.3 IterationBudget + 70/90 nudge | `hooks.py` + `workflow.py` | 人为构造死循环场景能在 50 iter 内停 |
| 4.6 Prompt-injection 扫描 | `core/infra/prompt_guard.py` | 注入样本 10 条全命中 |

### 阶段 II：记忆与技能深化（2–4 周）

| 任务 | 负责模块 | 验收 |
| --- | --- | --- |
| 4.4 Bounded memory + session 冻结快照 | `core/llm/bounded_memory.py` + MCP | 连续 5 次会话能记住用户名/偏好 |
| 4.5 Skills frontmatter + 三级披露 | `core/llm/skills/loader.py` | system prompt token 从 X → X-2k |
| 4.9 Plan 反应式重规划 | `plan_mode.py` | 注入失败 step 后 plan 能自动调整 |

### 阶段 III：真正的自进化（4–8 周）

| 任务 | 负责模块 | 验收 |
| --- | --- | --- |
| 4.7 自动 skill 蒸馏（草稿态） | `evolution/skill_distiller.py` + admin 审核页 | 日均产出 >= 1 条高质量 draft |
| 4.8 DSPy+GEPA 离线进化管线 | `evolution/*` + GH Actions | 首次 PR 在任一段 prompt 上提升评测 ≥3% |

---

## 6. 与现有文档的关系

- `docs/jingxin-agent-optimization-based-on-claude-code.md`：从 Claude Code harness 视角提了 skill、hook、permission 等建议；本文更聚焦 **self-iteration + memory**，两者不冲突但有叠加，**P0-1 / P0-3 / 4.5 是共同项**，实施时按本文方案即可。
- `docs/mem0-integration-plan.md`：mem0 定位为「冷长期」；本文 4.4 的 bounded memory 是「热短档」，应作为 mem0 的补充层，**不替换**。
- `docs/runtime-prompts-and-tools.md`：记录 runtime prompt / 工具现状；阶段 II 完成后需要刷新 Skill 加载章节。
- `docs/observability-guide.md`：阶段 III 直接依赖可观测系统的 trajectory 抓取能力，需要在可观测 schema 里增加 `is_success` / `redaction_level` 两列。

---

## 7. 不建议照搬的点

有些 Hermes 设计不适合 Jingxin 的政务场景：

1. **Honcho / Supermemory 等云端 memory provider**：数据出境风险，不采纳。
2. **`~/.hermes/*.md` 的用户家目录架构**：Jingxin 是多租户 SaaS，必须走 DB，不能落本地文件。
3. **Darwinian Evolver 做代码级进化**：AGPL 许可 + 对生产 Python 做结构变异，风险极高，**只做 prompt / skill 的 GEPA 进化即可**。
4. **6 种终端 backend（Daytona / Modal / Singularity 等）**：Jingxin 已有自己的 code sandbox 方案（见 `docs/code-sandbox-proposal.md`），不需要多 backend。
5. **Hermes 的 `SOUL.md` 人格层**：政务场景对「个性」的需求弱，改为 `ORGANIZATION.md`（机构身份与合规指引）更合适。

---

## 8. 总结

Hermes Agent 的价值**不在具体算法**，而在于它把一套「**可观测的执行 → 结构化的 trajectory → 自动化的记忆与技能沉淀 → 带守门的离线进化**」的闭环，工程化落地成了可以运行、可以度量、可以开 PR 的系统。

Jingxin-Agent 的工程骨架（AgentScope + ReActAgent + Hooks + MCP + mem0 + Plan Mode + 可观测日志）**已经非常接近**这个闭环的前半段，唯一缺的是：

1. **Prompt cache / 压缩 / 预算**这三处「基础效率优化」（阶段 I，一两周即可拿下）
2. 让 mem0 旁边长一层「cache 友好的 bounded markdown」，把快照真正冻结住（阶段 II）
3. 把日志真正接到 skill / prompt 的消费端（阶段 III）

最难但最有价值的是阶段 III —— **让 Agent 的记忆从「只记录」变成「会修改自己」**。这一步走完，Jingxin 就会从「AgentScope 壳 + MCP 工具」升级成一个「有过程性记忆 + 周级自进化」的成熟 harness，在政务场景里带来的复利远超一次性 prompt 工程。

---

## 附录 A：文件速查索引

| 主题 | Hermes 源文件 | Jingxin 对应 |
| --- | --- | --- |
| Agent loop | `agent/run_agent.py::AIAgent` | `core/llm/agent_factory.py` + `routing/workflow.py` |
| Prompt 组装 | `agent/prompt_builder.py` | `prompts/prompt_runtime.py` |
| Prompt cache | `agent/prompt_caching.py` | （新建）`core/llm/prompt_caching.py` |
| Context 压缩 | `agent/context_compressor.py` | （新建）`core/llm/jx_context_compressor.py` |
| 会话 DB | `hermes_state.py::SessionDB` | `core/db/models.py`（chats / messages） |
| Memory（bounded md） | `memory_manager.py` + `MEMORY.md/USER.md` | （新建）`core/llm/bounded_memory.py` + `bounded_memories` 表 |
| Memory（plugin） | `agent/memory_provider.py` + `plugins/memory/*` | `core/llm/memory.py` + `routing/memory_integration.py` |
| Skills | `skill_commands.py` / `skill_utils.py` | `core/llm/skills/`（新扩展 loader） |
| Hooks | `agent/hooks.py`（10 种 plugin hooks） | `core/llm/hooks.py`（现 2 种） |
| 工具并行 | `model_tools.handle_function_call` | AgentScope 自带 `parallel_tool_calls=True` |
| Trajectory | `agent/trajectory.py` | （新建）`evolution/collector.py` |
| 自进化 | `hermes-agent-self-evolution/` 独立仓库 | （新建）`src/backend/evolution/` |
| Plan / Subagent | —（Hermes 靠 session 谱系代偿） | `routing/subagents/plan_mode.py` |

## 附录 B：关键参数对齐表

| 参数 | Hermes 默认 | Jingxin 现状 | 建议 |
| --- | --- | --- | --- |
| 压缩触发比 | 0.5 | 0.75 | **0.6**（政务对话偏长） |
| 压缩首保留 | 10 | 最后 6 turn | 首 3 + 尾 10 |
| IterationBudget 父 | 90 | 无 | 50 |
| IterationBudget 子 | 50 | 无 | 25 |
| Closure nudge 阈值 | 70% / 90% | 无 | 70% / 90% |
| MEMORY.md 字符上限 | 2200 | 无 | 2000（中文字符） |
| USER.md 字符上限 | 1375 | 无 | 1200 |
| Skill index token 预算 | ~3000 | 无（全量注入） | 2500 |
| Skill 沉淀阈值（tool_calls） | 5 | 无 | 5 |
| Prompt cache TTL（provider 层） | 依赖 Anthropic 5min | 无 provider cache | 启用 ephemeral |
| Prompt cache TTL（本地） | —— | 300s | 保留 |

## 附录 C：里程碑验收指标

| 指标 | 现状基线 | 阶段 I 目标 | 阶段 II 目标 | 阶段 III 目标 |
| --- | --- | --- | --- | --- |
| 单会话平均输入 token | X | X × 0.5 | X × 0.4 | X × 0.35 |
| 工具调用数 P99 | Y | ≤ 50 | ≤ 40 | ≤ 30 |
| 用户身份记忆命中（连续 5 会话） | 0% | 0% | ≥ 90% | ≥ 95% |
| 失败 trajectory 自动转 issue | 0 | 0 | 0 | ≥ 5/周 |
| Prompt PR（GEPA 自动开） | 0 | 0 | 0 | ≥ 1/月 |
| 技能草稿（自动蒸馏） | 0 | 0 | 0 | ≥ 1/天 |

---

（以上。如需针对任一节做 PoC 代码、alembic 迁移脚本或评测集初版，请继续指派。）
