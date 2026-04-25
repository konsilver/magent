# 基于 Claude Code Agent 机制的 Jingxin-Agent 优化方案

> 通过深度分析 Claude Code v2.1.88 源码与 Jingxin-Agent 现有架构的差异，提出可落地的优化建议。

## 目录

- [一、现状对比](#一现状对比)
- [二、提示词对比分析：技能调用](#二提示词对比分析技能调用)
- [三、提示词对比分析：子智能体调用](#三提示词对比分析子智能体调用)
- [四、优化方向一：子智能体调用提示词优化](#四优化方向一子智能体调用提示词优化)
- [五、优化方向二：技能调用提示词优化](#五优化方向二技能调用提示词优化)
- [六、优化方向三：借鉴 Claude Code 的内置专用 Agent 设计思路](#六优化方向三借鉴-claude-code-的内置专用-agent-设计思路)
- [七、优化方向四：上下文隔离与追踪](#七优化方向四上下文隔离与追踪)
- [八、优化方向五：最小权限工具集](#八优化方向五最小权限工具集)
- [九、优化方向六：Agent 验证机制](#九优化方向六agent-验证机制)
- [十、优化方向七：Prompt Cache 友好设计](#十优化方向七prompt-cache-友好设计)
- [十一、优化方向八：Agent 生命周期管理](#十一优化方向八agent-生命周期管理)
- [十二、实施路线图](#十二实施路线图)

---

## 一、现状对比

### 架构差异总览

| 维度 | Claude Code | Jingxin-Agent | 差距分析 |
|---|---|---|---|
| **子 Agent 类型** | 7 种内置（Explore/Plan/Verification 等） + 自定义 | 1 个 report_generator + 用户自建 | 缺少场景化内置 Agent |
| **上下文隔离** | AsyncLocalStorage + createSubagentContext() | ThreadPoolExecutor 线程隔离 | 缺少调用链追踪 |
| **工具权限** | 每个 Agent 独立 tools/disallowedTools | 子 Agent 通过 `mcp_server_ids` 过滤 | 已有基础，可增强 |
| **并行执行** | 前台/后台 + fork 并行分治 | `parallel_tool_calls=True` + ThreadPool（已支持并行） | 机制已有，提示词引导可增强 |
| **验证机制** | verification agent 自动红队测试 | 无 | 缺少质量保障 |
| **Prompt Cache** | 6 层 cache 工程，fork 共享前缀 | 有 TTL 缓存（300s），无 API 级 cache 优化 | 可增加 API 层 cache 标记 |
| **技能提示词** | 阻断式要求 + 预算控制 + 防绕过 | 优先级决策 + 常见误判场景（v4 更强） | v4 已较好，可借鉴阻断式 |
| **Agent 调用提示词** | 详细的 prompt 编写指南 + 反模式提示 | 简洁的使用规则表格 | 提示词质量差距最大 |

### Jingxin-Agent 当前 Agent 调用链

```
用户消息 → strategy.py (关键词路由)
  ├─ report_generator → 固定 MCP + 固定 prompt (max_iters=10)
  ├─ user_created_agent (via agent_id) → 自定义 MCP/Skill/Prompt
  └─ main_agent (parallel_tool_calls=True)
       └─ call_subagent tool → ThreadPoolExecutor(max_workers=8) → 新事件循环
            └─ 多个 call_subagent 可在同一轮并行执行
```

> **注**：Jingxin-Agent 的 `call_subagent` 通过 `loop.run_in_executor(_subagent_pool, ...)` 实现了并行执行能力。当 LLM 在同一轮回复中生成多个 `call_subagent` tool call 时，AgentScope（`parallel_tool_calls=True`）会并发调用它们，每个在线程池独立线程中执行。

---

## 二、提示词对比分析：技能调用

### Claude Code 的技能提示词设计

**来源**：`src/tools/SkillTool/prompt.ts`

```
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match.
Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>" (e.g., "/commit",
"/review-pr"), they are referring to a skill. Use this tool to invoke it.

Important:
- Available skills are listed in system-reminder messages in the conversation
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT:
  invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- If you see a <command-name> tag in the current conversation turn, the skill has
  ALREADY been loaded - follow the instructions directly instead of calling this tool again
```

**关键设计特点**：
1. **阻断式强制**（BLOCKING REQUIREMENT）：匹配到技能时必须先调用，不能跳过
2. **防重复调用**：检测 `<command-name>` tag 避免重复加载
3. **Token 预算控制**：技能列表占上下文窗口的 1%（`SKILL_BUDGET_CONTEXT_PERCENT = 0.01`），每条描述最多 250 字符

### Jingxin-Agent 的技能提示词设计

**default 版本**（`35_skills.system.md`）：
```
## 技能使用规范

1. **匹配即加载** - 当用户请求匹配某个技能时，使用 view_text_file 读取 SKILL.md
2. **严格遵循指令** - 加载后按工作流程执行
3. **不要跳过加载** - 即使已知内容，每次仍需读取 SKILL.md
4. **技能不是工具** - 不要把技能名当作工具函数调用
5. **一次一个** - 同一轮只加载一个技能
6. **对用户透明** - 不需要告知用户在读技能文件
```

**v4 版本**（`20_tools.system.md`）—— 更成熟的分层设计：
```
### 决策优先级（严格按此顺序）

第一步：检查技能列表。收到问题后，立即浏览 # Agent Skills 部分，逐一比对。
  > 常见误判场景（必须走技能而非直接调工具）：
  > - "搜索公众号" / "搜XX公众号" → 匹配"中文网页搜索"技能
  > - "查股票" / "查A股" / "查财报数据" → 匹配"金融数据查询"技能
  > 以上场景不要直接调用 internet_search，必须先加载对应技能

第二步：工具直用。若没技能匹配且是简单查询，直接调最匹配的 MCP 工具。
第三步：多工具协同。需要不同类型数据时分别调用不同工具后整合。
第四步：兜底。都不足以回答时才使用 internet_search。
```

### 对比分析

| 维度 | Claude Code | Jingxin (v4) | 评估 |
|---|---|---|---|
| **强制性** | "BLOCKING REQUIREMENT" 阻断式 | "第一步：检查技能列表" 优先级式 | Jingxin v4 的优先级决策链更自然 |
| **防误判** | 无具体场景列举 | 列举了"搜索公众号→中文搜索技能"等误判 | **Jingxin 更好**——针对业务场景 |
| **防绕过** | "NEVER mention a skill without calling" | "不要跳过加载""不要把技能当工具" | CC 更强制 |
| **Token 控制** | 1% 上下文窗口预算 + 250 字符硬上限 | 无预算限制 | CC 更节省 |
| **防重复** | 检测 `<command-name>` tag | "一次一个" | CC 更精确 |

**结论**：Jingxin v4 版本的技能提示词已经较好，特别是"常见误判场景"列举是 Claude Code 没有的业务优势。可以从 Claude Code 借鉴阻断式表达和 Token 预算控制。

---

## 三、提示词对比分析：子智能体调用

### Claude Code 的 Agent 调用提示词

**来源**：`src/tools/AgentTool/prompt.ts`

Claude Code 的 Agent 提示词包含以下精心设计的段落：

#### 3.1 Agent 列表展示

每个 agent 以一行格式列出，包含工具描述：
```
- Explore: Fast agent specialized for exploring codebases... (Tools: All tools except Agent, ExitPlanMode, Edit, Write, NotebookEdit)
- Plan: Software architect agent for designing implementation plans... (Tools: All tools except Agent, ExitPlanMode, Edit, Write, NotebookEdit)
```

**关键**：**展示了每个 agent 可用的工具列表**，帮助 LLM 判断是否匹配任务需求。

#### 3.2 何时不使用 Agent（反模式列表）

```
When NOT to use the Agent tool:
- If you want to read a specific file path, use the Read tool instead
- If you are searching for a specific class definition like "class Foo", use Glob instead
- If you are searching for code within a specific file or set of 2-3 files, use Read instead
```

**设计意图**：防止 LLM 对简单任务过度使用 Agent，直接用工具更快更省。

#### 3.3 并行调用指导

```
- Launch multiple agents concurrently whenever possible, to maximize performance;
  to do that, use a single message with multiple tool uses
- If the user specifies that they want you to run agents "in parallel", you MUST
  send a single message with multiple Agent tool use content blocks.
```

#### 3.4 Prompt 编写指南（最有价值的部分）

```
## Writing the prompt

Brief the agent like a smart colleague who just walked into the room — it hasn't
seen this conversation, doesn't know what you've tried, doesn't understand why
this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make
  judgment calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question —
  prescribed steps become dead weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug"
or "based on the research, implement it." Those phrases push synthesis onto the agent
instead of doing it yourself. Write prompts that prove you understood: include file
paths, line numbers, what specifically to change.
```

#### 3.5 具体示例

Claude Code 提供了详细的示例，包含 `<commentary>` 解释每步决策：

```xml
<example>
user: "Can you get a second opinion on whether this migration is safe?"
<commentary>
A subagent_type is specified, so the agent starts fresh. It needs full context
in the prompt. The briefing explains what to assess and why.
</commentary>
Agent({
  subagent_type: "code-reviewer",
  prompt: "Review migration 0042_user_schema.sql for safety. Context: we're
  adding a NOT NULL column to a 50M-row table. Existing rows get a backfill
  default. I want a second opinion on whether the backfill approach is safe
  under concurrent writes..."
})
</example>
```

### Jingxin-Agent 的子智能体调用提示词

**来源**：`src/backend/core/llm/subagent_tool.py` 的 `build_subagent_prompt_section()`

```
## 可用子智能体

你可以通过 `call_subagent` 工具调用以下子智能体来协助完成专业任务。
需要并行调用多个子智能体时，在同一轮回复中生成多个 call_subagent 调用即可，系统会自动并行执行。

| ID | 名称 | 适用场景 |
|---|---|---|
| xxx | 数据分析师 | 数据查询和分析 |

### 使用规则
- 根据用户意图自主判断是否需要调用子智能体
- 需要同时调用多个子智能体时，在同一轮生成多个 call_subagent 工具调用
- 子智能体返回结果后，你负责汇总和整合
- 用户通过 @名称 指定时，直接调用对应子智能体
- 如果不确定是否需要调用子智能体，优先自己处理
```

### 对比分析

| 维度 | Claude Code | Jingxin-Agent | 差距 |
|---|---|---|---|
| **Agent 列表信息量** | 类型 + whenToUse + 可用工具列表 | ID + 名称 + 适用场景 | JX 缺少工具列表信息 |
| **反模式指导** | "When NOT to use" 明确列出 | "如果不确定优先自己处理" | CC 更具体 |
| **Prompt 编写指导** | 完整的编写方法论 + 反模式 | 无 | **最大差距** |
| **并行指导** | 有 | 有 | 持平 |
| **示例** | 含 commentary 的多场景示例 | 无示例 | 缺少示例 |
| **结果处理指导** | "result is not visible to user, send a text message with summary" | "你负责汇总和整合" | CC 更明确 |
| **上下文传递指导** | "provide a complete task description" + "Never delegate understanding" | `context_summary` 可选参数 | CC 的编写指南更重要 |

**核心发现**：最大的提升空间在于 **prompt 编写指南**。Claude Code 教会 LLM 如何写出好的子 agent 调用 prompt，而 Jingxin 只告诉 LLM 有哪些子 agent 可用。

---

## 四、优化方向一：子智能体调用提示词优化

### 4.1 当前问题

Jingxin 的子智能体提示词只是一个"使用说明书"，缺少：
1. 如何写出高质量的 `task` 描述
2. 何时不应该调用子智能体（反模式）
3. 调用后如何处理结果
4. 具体的调用示例

### 4.2 建议优化后的提示词

修改 `subagent_tool.py` 的 `build_subagent_prompt_section()`：

```python
def build_subagent_prompt_section(
    visible_agents: List[Dict[str, Any]],
    mentioned_agent_ids: Optional[List[str]] = None,
) -> str:
    if not visible_agents:
        return ""

    # Agent 列表（增加工具信息）
    rows = []
    for a in visible_agents:
        routing_desc = (a.get("extra_config") or {}).get("routing_description", "")
        desc = routing_desc or a.get("description", "")
        tools = ", ".join(a.get("mcp_server_ids", []) or []) or "默认工具集"
        rows.append(f"| {a['agent_id']} | {a['name']} | {desc} | {tools} |")

    table = "| ID | 名称 | 适用场景 | 可用工具 |\n|---|---|---|---|\n" + "\n".join(rows)

    section = (
        "## 可用子智能体\n\n"
        "你可以通过 `call_subagent` 工具将专业任务分派给子智能体处理。\n\n"
        + table + "\n\n"

        "### 何时使用子智能体\n"
        "- 任务需要子智能体拥有的专业工具（如数据库查询、知识库检索）\n"
        "- 用户通过 @名称 明确指定时\n"
        "- 需要多个独立信息源时，在同一轮并行调用多个子智能体\n\n"

        "### 何时不使用子智能体\n"
        "- 简单问答或你已有足够信息直接回答的问题\n"
        "- 你自己的工具已能完成的单步操作\n"
        "- 不确定是否需要时，优先自己处理\n\n"

        "### 编写 task 描述的要求\n"
        "子智能体看不到当前对话历史，你需要像给一个刚加入的同事布置任务一样：\n"
        "- 说明你要完成什么以及为什么\n"
        "- 描述你已经了解到或排除了什么\n"
        "- 提供足够的背景信息让子智能体能做判断，而非死板地执行指令\n"
        "- 如果需要简短回复，请明确说明（如"200字以内"）\n"
        "- **不要委托理解**——不要写"根据你的发现帮我总结"，"
        "而是说明具体要分析什么、对比什么、回答什么问题\n\n"

        "### 处理结果\n"
        "- 子智能体的回复对用户不可见，你必须汇总整合后呈现给用户\n"
        "- 多个子智能体的结果需要你做综合分析，而非简单拼接\n"
    )

    if mentioned_agent_ids:
        agent_map = {a["agent_id"]: a["name"] for a in visible_agents}
        names = [agent_map.get(aid, aid) for aid in mentioned_agent_ids if aid in agent_map]
        if names:
            section += (
                f"\n**用户已指定调用子智能体：{'、'.join(names)}。"
                "请直接使用 call_subagent 工具调用指定的子智能体。**\n"
            )

    return section
```

### 4.3 关键改进点

1. **增加工具列表列**：帮助 LLM 判断子 agent 是否匹配任务需求
2. **增加"何时不使用"**：防止过度委派（Claude Code 的 "When NOT to use" 模式）
3. **增加 Prompt 编写指南**：借鉴 Claude Code 的 "Writing the prompt" 段落，教 LLM 如何写好任务描述
4. **增加"不要委托理解"**：Claude Code 的 "Never delegate understanding" 是最有价值的反模式提醒
5. **明确结果不可见**：子 agent 的回复对用户不可见，主 agent 必须汇总

---

## 五、优化方向二：技能调用提示词优化

### 5.1 当前 v4 版本的优势

Jingxin v4 的技能提示词已经相当成熟：
- **决策优先级链**（技能 → 工具直用 → 多工具协同 → 兜底搜索）
- **常见误判场景**（"搜索公众号" → 中文搜索技能，Claude Code 没有这种业务级防误判）
- **反重复调用**（"同一工具不要重复调用"）

### 5.2 可从 Claude Code 借鉴的改进

#### 5.2.1 增加阻断式表达

Claude Code 的 "BLOCKING REQUIREMENT" 比 v4 的 "第一步" 更有强制性。建议在关键位置增强：

```markdown
**第一步：检查技能列表（强制）。** 收到用户问题后，**必须**先浏览 # Agent Skills 部分。
若有匹配技能，**在生成任何回复内容之前**先加载该技能。禁止跳过此步骤。
```

#### 5.2.2 增加防重复加载机制

借鉴 Claude Code 的 `<command-name>` tag 检测：

```markdown
- 如果当前对话轮次中已经看到技能加载的结果，不要重复加载——直接按已加载的指令执行
```

#### 5.2.3 技能列表 Token 预算

Claude Code 限制技能描述占上下文的 1%，每条最多 250 字符。Jingxin 可以在 `agent_factory.py` 注册技能时做类似截断：

```python
# agent_factory.py - 技能描述截断
MAX_SKILL_DESC_CHARS = 250
for skill in skills_to_register:
    if len(skill.description) > MAX_SKILL_DESC_CHARS:
        skill.description = skill.description[:MAX_SKILL_DESC_CHARS] + "..."
```

---

## 六、优化方向三：借鉴 Claude Code 的内置专用 Agent 设计思路

### 6.1 Claude Code 的设计哲学

Claude Code 的内置 Agent 不是简单地"按任务类型分类"，而是围绕以下原则设计：

1. **能力边界清晰**：每个 Agent 只有完成任务所需的最少工具
2. **成本分层**：只读/检索用快模型（haiku），需要推理的用强模型
3. **防越权**：只读 Agent 从工具层面禁止写操作（不是靠 prompt 提醒）
4. **上下文裁剪**：不需要的信息不发（Explore 省略 CLAUDE.md、gitStatus）
5. **proactive 触发**：Verification Agent 会被主 Agent 主动调用（whenToUse 说明了触发条件）

### 6.2 Jingxin 可借鉴的内置 Agent 设计

结合 Jingxin 的政务数据分析场景，不应简单复制 Claude Code 的 Agent 类型，而应借鉴其**设计原则**：

#### 6.2.1 数据校验 Agent（借鉴 Verification Agent）

**最值得借鉴的设计**。Claude Code 的 Verification Agent 用"红队思维"验证实现：

> "Your job is not to confirm the implementation works — it's to try to break it."

映射到 Jingxin 场景——报告数据校验：

```python
DATA_VERIFIER = AgentSpec(
    name="数据校验专家",
    system_prompt="""你是数据校验专家。你的任务不是确认报告正确，而是尝试找出数据问题。

## 你的两个失败模式

1. **校验回避**：你看到数据格式正确就标 PASS，没有回源验证。
2. **被表面迷惑**：数据有图有表看起来专业，你就放过了明显的逻辑矛盾。

## 验证步骤

对报告中的每个关键数字：
1. 使用 query_database 重新查询该数字的原始数据
2. 对比报告值 vs 查询值
3. 检查：单位是否正确？时间范围是否匹配？同比/环比计算是否正确？

## 识别你自己的借口

- "数据看起来合理" → 合理不等于正确。查询验证。
- "计算逻辑应该没问题" → 你没算就不知道。用计算器验证。
- "这个数据源应该是可靠的" → 可靠不代表没有用错。回源核实。

## 输出格式

每个校验项：
### 校验：[指标名称]
**查询命令**：[实际执行的查询]
**报告中的值**：[报告写的数据]
**查询到的值**：[实际查询结果]
**判定**：PASS / FAIL（附差异说明）

最终：VERDICT: PASS / FAIL / PARTIAL
""",
    mcp_server_ids=["query_database", "retrieve_dataset_content"],
    disallowed_mcp_ids=["report_export_mcp"],  # 只读
    max_iters=8,
    model_role="main_agent",
)
```

#### 6.2.2 快速检索 Agent（借鉴 Explore Agent 的"快+只读"理念）

Explore Agent 的核心不是"探索代码"，而是**用快模型做只读检索**的设计模式。Jingxin 可以在用户自建 Agent 中增加这种**模板**：

```python
# 提供给用户创建 Agent 的预设模板
QUICK_RETRIEVAL_TEMPLATE = {
    "name": "快速检索模板",
    "description": "用快模型做只读知识检索，成本低、速度快",
    "model_role": "fast_agent",
    "system_prompt_template": (
        "你是{domain}领域的检索专家。你的任务是从知识库中快速定位相关信息。\n\n"
        "=== 只读模式 ===\n"
        "你只能检索和分析信息，不能生成文件或导出报告。\n\n"
        "工作方式：\n"
        "1. 使用检索工具搜索相关文档\n"
        "2. 提取关键信息并整理\n"
        "3. 用简洁的格式返回结果\n"
    ),
    "mcp_server_ids": ["retrieve_dataset_content"],
}
```

#### 6.2.3 在 Admin 界面增加 Agent 创建模板

比起硬编码内置 Agent，更适合 Jingxin 的方式是在用户创建 Agent 时提供**预设模板**，让管理员基于模板快速创建：

| 模板 | 借鉴来源 | 核心特征 |
|---|---|---|
| 数据校验 | Verification Agent | 只读 + 红队思维 + 强制格式 |
| 快速检索 | Explore Agent | 快模型 + 只读 + 简短 prompt |
| 研究分析 | General Purpose | 全工具 + 强模型 + 完整上下文 |
| 报告生成 | 现有 report_generator | 可写 + 多工具 + 长流程 |

---

## 七、优化方向四：上下文隔离与追踪

### 7.1 问题

当前子 Agent 通过 `ThreadPoolExecutor` 在独立线程执行，已经实现了基础隔离。但缺少：
1. 调用链追踪（谁调用了谁）
2. Token 统计归因（子 agent 消耗计入哪个请求）
3. 嵌套深度限制

### 7.2 借鉴 Claude Code 的 AsyncLocalStorage 模式

Claude Code 使用 `agentContext.ts` 通过 AsyncLocalStorage 实现：
- 每个 agent 有唯一 `agentId`
- `chainId` 在整个调用链中传递
- `depth` 递增防止无限嵌套
- `invokingRequestId` 关联到发起者

### 7.3 建议方案

使用 Python `contextvars`（等价于 Node.js AsyncLocalStorage）：

```python
# src/backend/core/llm/agent_context.py

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional
import uuid

_current_agent_context: ContextVar[Optional["AgentContext"]] = ContextVar(
    "agent_context", default=None
)

@dataclass
class AgentContext:
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent_type: str = "main"           # main | subagent | builtin
    agent_name: str = ""
    parent_agent_id: Optional[str] = None
    chain_depth: int = 0
    chain_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # 运行时统计
    tool_use_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

def get_current_context() -> Optional[AgentContext]:
    return _current_agent_context.get()

def create_subagent_context(parent: AgentContext, **kwargs) -> AgentContext:
    return AgentContext(
        parent_agent_id=parent.agent_id,
        chain_depth=parent.chain_depth + 1,
        chain_id=parent.chain_id,
        **kwargs,
    )
```

> **注意**：由于 Jingxin 的 `call_subagent` 在新线程 + 新事件循环中执行，`contextvars` 需要手动传递到子线程（Python 3.12+ 的 `ThreadPoolExecutor` 自动继承 contextvars，低版本需用 `copy_context().run()`）。

---

## 八、优化方向五：最小权限工具集

### 8.1 现状

Jingxin 的 UserAgent 模型已有 `mcp_server_ids` 字段，用户创建的子 Agent 已经可以指定工具范围。但：
1. 缺少 `disallowed_mcp_ids`（黑名单模式）
2. 没有"只读"快捷配置
3. 内置 report_generator 的工具集是硬编码的

### 8.2 借鉴 Claude Code 的工具过滤

Claude Code 每个 Agent 定义中同时支持 `tools`（白名单）和 `disallowedTools`（黑名单）：

```typescript
// Explore Agent: 禁止写操作
disallowedTools: ['Agent', 'ExitPlanMode', 'FileEdit', 'FileWrite', 'NotebookEdit']

// StatusLine Agent: 只允许读写配置
tools: ['Read', 'Edit']
```

并且 prompt 中会展示每个 agent 的工具信息：`(Tools: All tools except Agent, Edit, Write, NotebookEdit)`

### 8.3 建议方案

在 UserAgent ORM 模型中增加字段：

```python
# core/db/models.py - UserAgent
disallowed_mcp_ids = Column(JSON, default=[])  # 黑名单
readonly = Column(Boolean, default=False)        # 只读模式快捷开关
```

在 `agent_factory.py` 中增加过滤逻辑：

```python
if user_agent and user_agent.readonly:
    WRITE_TOOLS = {"report_export_mcp", "generate_chart_tool"}
    enabled_mcp_ids = [id for id in enabled_mcp_ids if id not in WRITE_TOOLS]

if user_agent and user_agent.disallowed_mcp_ids:
    enabled_mcp_ids = [id for id in enabled_mcp_ids
                       if id not in user_agent.disallowed_mcp_ids]
```

---

## 九、优化方向六：Agent 验证机制

### 9.1 Claude Code Verification Agent 的核心设计

Claude Code 的 Verification Agent 是最精巧的 Agent 设计，其核心不在于"验证"本身，而在于**对抗 LLM 天然的确认偏差**：

**两个自我反思机制**：
1. 识别自己"读代码 → 写 PASS → 跳过"的倾向
2. 警惕 UI 漂亮/测试通过就觉得 OK

**借口识别系统**（这是最有启发的设计）：
```
- "The code looks correct based on my reading" → 阅读不是验证，运行它
- "The implementer's tests already pass" → 实现者也是 LLM，独立验证
- "I don't have a browser" → 你检查过有没有 playwright MCP 吗？
- "This would take too long" → 不是你来决定
```

**强制输出格式**：每个 check 必须有 `Command run` + `Output observed`，没有命令执行的 PASS 被拒绝。

### 9.2 Jingxin 的数据校验 Agent

见 [6.2.1 节](#621-数据校验-agent借鉴-verification-agent) 的详细设计。

核心思路：将 Claude Code 的"红队测试代码"映射为"红队验证数据"——都是对抗 LLM 的确认偏差。

---

## 十、优化方向七：Prompt Cache 友好设计

### 10.1 当前状况

Jingxin 的 `prompt_runtime.py` 有 TTL 缓存（300s 文件内容缓存、30s 版本检查），但这是**构建侧缓存**——减少重复的文件读取和 DB 查询。API 层面的 prompt cache（减少重复的 token 计费）没有优化。

### 10.2 Claude Code 的 API 级 Cache 设计

Claude Code 的 prompt cache 优化是在 API 请求层面：
1. System prompt 中添加 `cache_control: { type: "ephemeral" }` 标记
2. 相同前缀的请求共享 KV cache，`cache_read` 比正常 input 便宜 10 倍
3. **Agent 列表从 tool description 中剥离到 attachment message**（避免 MCP 连接变化导致 cache bust）

### 10.3 建议方案

#### 10.3.1 System Prompt 稳定性优化

当前 Jingxin 的 system prompt 中，时间戳每秒变化会导致 cache miss。建议：

```python
# 时间粒度从秒降为小时
def get_time_context():
    now = datetime.now()
    return f"当前日期：{now.strftime('%Y年%m月%d日')}（{['周一','周二','周三','周四','周五','周六','周日'][now.weekday()]}）"
    # 不要包含小时/分钟/秒
```

#### 10.3.2 动态内容后置

将子智能体列表、技能列表等动态内容放在 system prompt 的**最后**，使前缀尽可能稳定：

```python
def build_system_prompt(cfg, ctx):
    # 稳定前缀（session 内不变）
    parts = [
        load_prompt_part("00_role"),
        load_prompt_part("05_anti_hallucination"),
        load_prompt_part("10_abilities"),
        load_prompt_part("20_tools"),
        load_prompt_part("30_workflow"),
        load_prompt_part("60_format"),
        load_prompt_part("65_citations"),
    ]
    # 动态后缀（可能变化）
    parts.append(build_subagent_section(ctx.visible_subagents))
    parts.append(build_skill_section(ctx.skills))
    parts.append(get_time_context())
    return "\n\n".join(parts)
```

---

## 十一、优化方向八：Agent 生命周期管理

### 11.1 当前状况

Jingxin 的子 Agent 通过 `_run_subagent_in_thread` 中的 `try/finally` 清理 MCP 连接。基本的清理已有，但缺少：
- 统一的日志格式（启动/完成/耗时/token统计）
- 超时保护
- 嵌套深度限制

### 11.2 建议方案

```python
# src/backend/core/llm/agent_lifecycle.py

import time
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

MAX_AGENT_DEPTH = 3  # 最多嵌套 3 层（借鉴 Claude Code 的 chain_depth）

@asynccontextmanager
async def agent_lifecycle(agent_id: str, agent_name: str, timeout: float = 300):
    """Agent 生命周期管理器"""
    start_time = time.time()

    # 嵌套深度检查
    parent_ctx = get_current_context()
    if parent_ctx and parent_ctx.chain_depth >= MAX_AGENT_DEPTH:
        raise RuntimeError(
            f"Agent 嵌套深度超限（max={MAX_AGENT_DEPTH}），"
            "请减少嵌套层数或重构任务分解方式"
        )

    ctx = create_subagent_context(parent_ctx, agent_name=agent_name) if parent_ctx \
        else AgentContext(agent_name=agent_name)
    ctx_token = run_with_context(ctx)

    logger.info("[Agent:%s] started (id=%s, depth=%d)", agent_name, ctx.agent_id, ctx.chain_depth)

    mcp_clients = []
    agent = None

    try:
        yield {"set_agent": lambda a: nonlocal_set("agent", a),
               "set_clients": lambda c: nonlocal_set("mcp_clients", c)}
    finally:
        elapsed = time.time() - start_time

        # 清理 MCP 连接
        for client in mcp_clients:
            try:
                await client.close()
            except Exception:
                pass

        # 清理 agent 内存
        if agent and hasattr(agent, "memory"):
            agent.memory.clear()

        # 重置上下文
        _current_agent_context.reset(ctx_token)

        # 统计日志
        logger.info(
            "[Agent:%s] finished | %.1fs | tools=%d | tokens_in=%d | tokens_out=%d",
            agent_name, elapsed, ctx.tool_use_count,
            ctx.total_input_tokens, ctx.total_output_tokens,
        )
```

---

## 十二、实施路线图

### 阶段一：提示词优化（1 周，改动最小，收益最高）

| 优先级 | 任务 | 文件变更 | 预期收益 |
|---|---|---|---|
| **P0** | 子智能体调用提示词重写 | `subagent_tool.py` build_subagent_prompt_section (~50行) | LLM 写出更好的 task 描述 |
| **P0** | 增加"何时不使用"反模式 | 同上 | 减少不必要的子 agent 调用 |
| P1 | 技能提示词增加阻断式表达 | `35_skills.system.md` 或 v4 `20_tools.system.md` (~5行) | 提高技能匹配率 |
| P1 | 技能描述 Token 预算控制 | `agent_factory.py` 技能注册逻辑 (~10行) | 节省 prompt token |

### 阶段二：基础设施（1-2 周）

| 优先级 | 任务 | 文件变更 | 预期收益 |
|---|---|---|---|
| P0 | AgentContext 上下文追踪 | 新增 `core/llm/agent_context.py` (~80行) | 调用链可观测 |
| P1 | AgentLifecycle 管理器 | 新增 `core/llm/agent_lifecycle.py` (~60行) | 资源清理 + 超时保护 |
| P1 | Agent 工具过滤增强 | `agent_factory.py` + UserAgent 模型 (~30行) | 最小权限 |

### 阶段三：Agent 模板与验证（2-3 周）

| 优先级 | 任务 | 文件变更 | 预期收益 |
|---|---|---|---|
| P1 | 数据校验 Agent 模板 | 新增 `subagents/data_verifier.py` | 报告数据自动校验 |
| P2 | Admin 界面 Agent 创建模板 | 前端 + API | 管理员快速创建专用 Agent |
| P2 | Prompt Cache 稳定性优化 | `prompt_runtime.py` 时间粒度 + 顺序调整 | API 成本降低 |

### 阶段四：高级特性（未来）

| 优先级 | 任务 | 预期收益 |
|---|---|---|
| P3 | LLM Router（语义路由替代关键词匹配） | 路由准确性提升 |
| P3 | 上下文继承（子 Agent 继承父对话历史） | 减少重复背景解释 |
| P3 | Agent 间通信（SendMessage 模式） | 多 Agent 协作 |

---

## 附录 A：Claude Code 与 Jingxin 代码结构映射

| Claude Code 文件 | Jingxin 对应文件 | 差异 |
|---|---|---|
| `AgentTool/prompt.ts` | `subagent_tool.py` build_subagent_prompt_section | CC 有编写指南/反模式/示例；JX 只有规则表 |
| `SkillTool/prompt.ts` | `35_skills.system.md` / v4 `20_tools.system.md` | CC 阻断式；JX v4 优先级链（各有优势） |
| `AgentTool.tsx` (call) | `subagent_tool.py` (call_subagent) | 执行机制类似，CC 有更多模式 |
| `runAgent.ts` | `agent_factory.py` | CC 有上下文裁剪；JX 统一模板 |
| `agentContext.ts` | 无对应 | JX 缺少 contextvars 上下文追踪 |
| `built-in/*.ts` (7种) | `registry.py` (1种) + UserAgent DB | CC 内置多种；JX 靠用户自建 |
| `verificationAgent.ts` | 无对应 | JX 缺少验证机制 |
| `forkSubagent.ts` | 无对应 | JX 缺少 fork 机制 |

## 附录 B：Claude Code Agent 提示词完整原文

### Agent Tool Prompt（完整）

```
Launch a new agent to handle complex, multi-step tasks autonomously.

The Agent tool launches specialized agents (subprocesses) that autonomously handle
complex tasks. Each agent type has specific capabilities and tools available to it.

Available agent types and the tools they have access to:
- {agentType}: {whenToUse} (Tools: {toolsDescription})
...

When using the Agent tool, specify a subagent_type parameter to select which agent
type to use. If omitted, the general-purpose agent is used.

When NOT to use the Agent tool:
- If you want to read a specific file path, use the Read tool instead
- If you are searching for a specific class definition like "class Foo", use Glob
- If you are searching for code within a specific file or set of 2-3 files, use Read
- Other tasks that are not related to the agent descriptions above

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance;
  to do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result
  returned by the agent is not visible to the user. To show the user the result,
  you should send a text message back to the user with a concise summary.
- You can optionally run agents in the background using the run_in_background parameter.
- To continue a previously spawned agent, use SendMessage with the agent's ID.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research
- If the agent description mentions that it should be used proactively, try your best
  to use it without the user having to ask for it first.
- If the user specifies that they want you to run agents "in parallel", you MUST
  send a single message with multiple Agent tool use content blocks.

## Writing the prompt

Brief the agent like a smart colleague who just walked into the room — it hasn't
seen this conversation, doesn't know what you've tried, doesn't understand why this
task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment
  calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug"
or "based on the research, implement it." Those phrases push synthesis onto the agent
instead of doing it yourself.
```

### Skill Tool Prompt（完整）

```
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match.
Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>", they are referring to a skill.

How to invoke:
- Use this tool with the skill name and optional arguments
- Examples:
  - skill: "pdf" - invoke the pdf skill
  - skill: "commit", args: "-m 'Fix bug'" - invoke with arguments

Important:
- Available skills are listed in system-reminder messages in the conversation
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT:
  invoke the relevant Skill tool BEFORE generating any other response
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- If you see a <command-name> tag in the current conversation turn, the skill
  has ALREADY been loaded - follow the instructions directly
```
