# Jingxin-Agent 记忆系统分层优化方案

> 作者：Claude（Opus 4.7, 1M context）
> 日期：2026-04-19
> 定位：对当前 `mem0 + Milvus + Neo4j` 单层长期记忆做**分层重构**，并给出每一层的**自定义提示词分层优化稿**。
> 对标：
> - Hermes Agent（NousResearch）—— bounded markdown + 会话冻结快照 + 插件化 memory provider
> - Claude Code（Anthropic CLI）—— `user / feedback / project / reference` 四类型 + `MEMORY.md` 索引 + 拒绝清单
> - OpenClaw（社区 CC clone）—— Tier 1 常驻 / Tier 2 日志 / Tier 3 深度检索 + `before_memory_commit` 钩子
> - 姊妹篇：`docs/jingxin-agent-optimization-based-on-hermes-agent.md`（总体优化路线，本文只深挖「记忆」一块）

---

## 0. TL;DR

现状一句话总结：**Jingxin 把所有记忆扔进 mem0/Milvus 一口大锅**，用同一段 prompt 抽取、同一种方式注入、同一个 `user_id` 标识，没有分层、没有租户隔离、没有敏感性分级、没有审计。

这对一个服务于**政务+企业**、用户身份带编制、对话内容涉密等级可能达到内部 / 敏感的 Agent，是明显的工程欠账。

本方案给出：

1. **六层记忆模型**（L0 机构 / L1 用户档案 / L2 会话工作集 / L3 事实向量 / L4 关系图谱 / L5 审计归档）
2. **四类抽取提示词**（身份型 / 偏好型 / 事实型 / 任务型），替代现有单一 `custom_fact_extraction_prompt`
3. **三档注入提示词**（常驻 / 会话冻结 / 临时 ephemeral），对齐 Anthropic `cache_control`
4. **政企场景必须硬编码的拒绝/脱敏/审计清单**（身份证、社保号、红头文件号、领导批示等）
5. **3 阶段落地路线**，第一阶段两周可见收益，不破坏现网。

---

## 1. 现状诊断

### 1.1 代码路径

| 模块 | 文件：行号 | 当前职责 |
| --- | --- | --- |
| 抽取+存储 | `core/llm/memory.py:63-156` | 一段 140 行的 `custom_fact_extraction_prompt` 负责抽取四类信息（身份 / 偏好 / 查询数据 / 关注领域） |
| 检索 | `core/llm/memory.py:221-310` | limit=10 → `min_score=0.4` 过滤 → `_apply_time_decay` 半衰期 70 天 → top-5 |
| 注入 | `routing/memory_integration.py:46-82` | 以 `user` 角色塞 `<system_memory_context>` 包裹块（因为 Qwen 不允许第二个 system） |
| Agent 绑定 | `core/llm/agent_factory.py:593-638` | `Mem0LongTermMemory(user_name=user_id)` + `long_term_memory_mode="static_control"` |
| 保存 | `routing/memory_integration.py:84-98` | 每轮 `asyncio.create_task(save_conversation)` 无条件背景写 |
| API | `api/routes/v1/memories.py` | list / get_settings / patch_settings / delete / delete_all |
| 前端 | `stores/settingsStore.ts` + `components/settings/SettingsModal.tsx` | 开关 + 列表 + 删除 |

### 1.2 四个核心问题

**P1：一锅端的抽取 prompt**
`custom_fact_extraction_prompt` 把用户身份、偏好、查询过的数据、关注领域混在一条 prompt 里让模型一次抽。模型实际抽取时噪声很大：把「查询过宁波市 2025 年 GDP 为 16530 亿元」这种**瞬时事实**和「在市财政局预算处工作」这种**稳定身份**等权保存，下次检索时噪声干扰准度。

**P2：没有租户与敏感性维度**
全局只有 `user_id`。但 Jingxin 的用户 = 「张三（市财政局预算处）」而不是「张三（个人）」。同一个人在不同机构岗位上的记忆应当隔离，且涉密级别不同。目前的 API 调用既没有 `tenant_id`，也没有 `confidentiality_level`，**记忆是任何登录态请求都能读**的。

**P3：注入方式破坏 prompt cache**
记忆作为 `user` 消息每轮重新检索、重新注入，内容随 query 变化 → Anthropic `cache_control` 完全无法命中。Hermes 的做法是「**会话开始瞬间冻结一次**」，整个 session 内保持不变；我们每 turn 都打一次向量库 + 重新拼字符串。

**P4：always-save + 无审计**
`save_memories_background` 每轮都触发保存（`line 84-98`），不管用户说「你好」还是「帮我查一下市长的讲话稿」都写入。既有成本问题（每轮 LLM 抽取调用），也有合规问题——**敏感信息可能被无声无息写进向量库、失去管控**。同时没有任何「谁在什么时候写入/读取了哪条记忆」的审计链路，政企审计场景直接不合规。

---

## 2. 政企场景的硬性约束（决定架构形态）

**A. 多租户 / 多岗位**
一个 Jingxin 实例要服务多个委办局 + 多个处室。同一个自然人在「市财政局预算处」和「市发改委综合处」登录时，记忆**必须隔离**。

**B. 涉密等级**
记忆条目必须可标注：
- 公开（PUBLIC）
- 内部（INTERNAL）—— 机构内可见
- 敏感（SENSITIVE）—— 仅用户本人可见
- 涉密（RESTRICTED）—— 不存储，仅会话内使用后销毁

**C. 拒绝清单（不得进记忆的内容）**
- 身份证号、社保号、银行卡号、手机号（以明文形式）
- 红头文件号、内部发文号
- 领导批示原文、会议纪要原文
- 密码、Token、Cookie
- 源代码、SQL、配置文件
- 任何来自「文档上传」或「爬虫」通道的**原始长文本**（只允许抽取后的短摘要）

**D. 审计 / 可追溯**
所有记忆的读、写、改、删都要留审计 trail：`{who, when, layer, memory_id, action, reason, tenant}`。与现有可观测日志（commit `b74d48f`）对齐。

**E. 遗忘权**
用户要能要求「删除与我有关的全部记忆」（GDPR-style right-to-be-forgotten 的国内对应物）。删除操作必须级联 Milvus + Neo4j + 审计表。

**F. 本地化**
mem0 的字段名是英文，抽取 prompt 必须中文（已做）；所有面向模型和用户的记忆层展示必须中文；审计报表必须中文导出。

---

## 3. 六层记忆模型

> 层号越小：生命周期越长、变化越慢、越靠近 prompt cache 前缀。
> 层号越大：生命周期越短或越模糊、越靠近 ephemeral 区域或外部存储。

### 3.1 总览

| 层 | 名称 | 存储 | 生命周期 | 注入方式 | 写入者 | 典型条目 |
| --- | --- | --- | --- | --- | --- | --- |
| **L0** | 机构常识 | DB: `org_memory` | 手工维护，季度更新 | 系统 prompt 前缀层（cache #1） | 管理员 | 「本机构是 xx 市财政局，职责是……」「本系统不得对外披露预算编制过程数据」 |
| **L1** | 用户档案 | DB: `user_profile_memory`（受限 markdown, ≤1500 字符） | 长期，自进化 | 系统 prompt cache #2（会话开始冻结） | 模型 + 用户 + 审核 | 「姓名：张三」「处室：预算处」「沟通偏好：表格优先」 |
| **L2** | 会话工作集 | DB: `session_memory`（本 chat 维度） | 单会话，会话结束转储或丢弃 | 系统 prompt cache #3 | 模型 | 「本轮对话任务：查询 2025 年三季度财政收入」 |
| **L3** | 事实向量记忆 | Milvus `jingxin_memories`（已有） | 中期，半衰期 90 天 | 会话开始检索一次，冻结注入 | 模型（受过滤） | 「2025 年 Q3 一般公共预算收入为 XX 亿元，数据来自 XX 报告」 |
| **L4** | 关系图谱 | Neo4j（已有） | 长期，关系驱动 | 按需工具调用检索（不默认注入） | 模型（关系级别） | 「张三 → 属于 → 预算处」「预算处 → 主管 → 部门预算编制」 |
| **L5** | 审计归档 | PostgreSQL: `memory_audit`（新增） | 永久 | 不注入 | 系统自动 | 所有读写操作 trail |

### 3.2 各层独立详解

#### L0 机构常识（ORG_MEMORY）

**动机**：Hermes 的 `SOUL.md` 管「Agent 人格」，Jingxin 场景里**机构身份比 Agent 人格重要 100 倍** —— 一个 Agent 给预算处用和给统计局用，应当说完全不同的话。

**数据**：
- 机构全称、简称、上级主管单位
- 核心职责（5 条以内）
- 红线条款（5 条以内，自然语言）
- 数据合规要求（例如：不得在对话里输出红头文件号明文）

**约束**：
- 单租户上限 800 中文字符
- 只能由 `admin` 角色写
- 版本化（每次更新存 history，可回滚）
- 会话启动时按 `tenant_id` 查 1 条，拼到系统 prompt 最顶层（紧跟 `00_time_role`）

**DB schema**:
```sql
CREATE TABLE org_memory (
    tenant_id         VARCHAR(64) PRIMARY KEY,
    content_md        TEXT NOT NULL,         -- <= 800 chars
    version           INT NOT NULL DEFAULT 1,
    updated_at        TIMESTAMPTZ DEFAULT now(),
    updated_by        VARCHAR(64),
    confidentiality   VARCHAR(16) NOT NULL DEFAULT 'INTERNAL'
);
CREATE TABLE org_memory_history (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         VARCHAR(64),
    content_md        TEXT,
    version           INT,
    updated_at        TIMESTAMPTZ,
    updated_by        VARCHAR(64)
);
```

#### L1 用户档案（USER_PROFILE）

**动机**：对应 Hermes `USER.md`、Claude Code `type=user` 记忆。问题：当前 mem0 把「姓名是张三」和「查询过 2025 年 GDP」塞一个 collection，每次都做向量检索很浪费。这种**稳定、低维度、高复用**的信息应当是一份**短小的 markdown**，每次会话开始**一次性加载 + 冻结**。

**数据**：
- 身份四元组：姓名 / 单位 / 处室 / 岗位
- 沟通偏好：回答风格 / 输出格式 / 语言风格（正式 / 口语化）
- 长期关注领域（3 条以内）
- 已掌握的系统操作水平（新手 / 熟练）

**约束**：
- 单用户上限 1200 中文字符（遵循 Hermes USER.md 1375 chars 约束，中文字符更短）
- 由模型维护，但通过工具 `user_profile_patch` 调用，不能直接改 Milvus
- 写入必须走审计 hook（L5）
- 用户自助可在前端**只读查看 + 明文编辑 + 清空**
- 敏感性 = `SENSITIVE`（仅本人 + 审计员）

**DB schema**:
```sql
CREATE TABLE user_profile_memory (
    user_id           VARCHAR(64),
    tenant_id         VARCHAR(64),
    content_md        TEXT NOT NULL,      -- <= 1200 chars
    last_compacted_at TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY(user_id, tenant_id)
);
```

复合主键保证「同一人在不同机构岗位」的档案物理隔离 —— 这是政企必须的。

#### L2 会话工作集（SESSION_MEMORY）

**动机**：当前一个会话长到一定程度，上下文被压缩（AgentScope `CompressionConfig`），压缩后**会话级事实**丢失。例如「本次对话是来做三季度预算分析的」这种工作集级事实应当独立存在。

**数据**：
- 本会话任务概述（1 句话）
- 已经调用过的工具与核心产出（列表）
- 还未完成的子任务（列表）

**约束**：
- 单 chat 上限 600 中文字符
- 模型通过 `session_memory_update(task_summary, pending, done)` 工具维护
- 会话结束时，用一段**低温 LLM** 压缩后：
  - 如果对未来有用 → 升级到 L3（存 Milvus）
  - 否则 → 只留 L5 审计，L2 条目删除

**DB schema**: 借用现有 `chats.metadata` 即可，加一个 `session_memory` 子键。不需要新表。

#### L3 事实向量记忆（FACT_VECTOR）

**动机**：保留现在的 mem0/Milvus 链路，但**收窄职责** —— 只存「事实型、可向量化、可时间衰减」的内容：
- 用户查询过的数据（含来源）
- 用户提到过的重要业务实体
- 长文档抽取后的要点（不是原文）

**约束**：
- **不再承担**「身份信息 / 偏好 / 任务工作集」—— 那些全部去 L1/L2
- 每条记忆增加 metadata：`layer=L3`, `tenant_id`, `confidentiality`, `source`（conversation / document / tool）
- 写入前过文档长度闸门：单条 ≤ 300 字符；超长 → 拒收（让模型自己摘要后再写）

#### L4 关系图谱（GRAPH_MEMORY）

**动机**：保留现有 Neo4j。但**不再默认注入**（现在 `retrieve_memories` 里强制拼 relations），改为一个工具 `graph_memory_query(entity, relation_type, depth)`，让模型按需调用。

**理由**：图关系 90% 情况下是无关噪声，全量拼到 system prompt 是污染 cache；让模型决定「我现在需要知道 XX 和 YY 的关系」再调用。

**约束**：
- 工具调用必走审计
- 关系写入速率限制：每轮 ≤ 5 条关系
- 敏感关系（人-人、人-职务）加 `confidentiality=SENSITIVE` 标签

#### L5 审计归档（AUDIT）

**动机**：政企必需。Hermes / Claude Code 都没有这一层，是中国政企场景的**增量**。

**数据**：每次 L0-L4 读写的 `{actor, action, layer, memory_id, tenant_id, user_id, chat_id, confidentiality, content_hash, ts, reason}`。

**DB schema**:
```sql
CREATE TABLE memory_audit (
    id               BIGSERIAL PRIMARY KEY,
    ts               TIMESTAMPTZ DEFAULT now(),
    actor            VARCHAR(64),          -- user_id / 'system' / admin_id
    action           VARCHAR(16),          -- 'read' | 'write' | 'update' | 'delete'
    layer            VARCHAR(16),          -- 'L0' .. 'L4'
    memory_id        VARCHAR(128),
    tenant_id        VARCHAR(64),
    user_id          VARCHAR(64),
    chat_id          VARCHAR(64),
    confidentiality  VARCHAR(16),
    content_hash     VARCHAR(64),          -- SHA256，不存原文
    reason           TEXT                  -- 可选，写入触发原因
);
CREATE INDEX idx_audit_user_ts ON memory_audit(user_id, ts DESC);
CREATE INDEX idx_audit_tenant_ts ON memory_audit(tenant_id, ts DESC);
```

**调用**：统一通过 `core/llm/memory_audit.py::record(...)` 写入，所有 L0–L4 读写都调它。

---

## 4. 自定义抽取提示词的分层优化（核心）

> 现状：一条 `custom_fact_extraction_prompt`（140 行）通吃所有类型 —— 这正是抽取准度上不去的根本原因。
>
> 重构：拆分为 **4 条独立抽取 prompt**，每条职责单一、示例专注、拒绝清单强，路由时**按事件类型分发**而不是每轮全跑。

### 4.1 路由逻辑

会话末尾触发抽取时，先用轻量规则（关键词 + 消息长度）把当前轮对话分到以下 0–N 个类别：

```python
def classify_conversation(user_msg: str, assistant_msg: str) -> set[str]:
    classes = set()
    if re.search(r"(我叫|我是|我在|我负责|我的岗位|我的职级|我的联系方式)", user_msg):
        classes.add("IDENTITY")
    if re.search(r"(喜欢|更倾向|请用|回答时|输出格式|不要|少说)", user_msg):
        classes.add("PREFERENCE")
    if re.search(r"(查询|数据|统计|指标|数值|占比|同比|环比|GDP|财政收入)", user_msg + assistant_msg):
        classes.add("FACT")
    if re.search(r"(帮我|接下来|然后|计划|分析|写一份|做一版)", user_msg):
        classes.add("TASK")
    return classes
```

**只对命中类别跑对应抽取 prompt**。一个普通「你好」命中空集 → 直接跳过抽取（等同于现在很多空 LLM 调用的优化）。

### 4.2 四条抽取提示词

#### 4.2.1 IDENTITY_EXTRACTOR（→ 写入 L1）

```
你是一个严格的用户身份信息抽取器。从下面的对话中抽取**用户自述**的身份信息，写入用户档案。

【必须抽取】
- 姓名（仅在用户明确自我介绍时）
- 单位全称（例：市财政局）
- 处室/部门（例：预算处）
- 岗位/职级（例：主任科员）
- 长期工作联系方式（可选，仅用户主动提供时）

【绝对不抽取】
- 身份证号、社保号、银行卡号（即使用户主动说）—— 输出 "REDACTED"
- 手机号、邮箱（除非用户明确要求保存到系统通讯录）
- 政治面貌、宗教信仰、健康状况、家庭成员
- 任何涉及密级标注的信息（"机密"、"秘密"、"内部"、"RESTRICTED"）—— 返回空

【输出格式】
{"facts": [{"field": "name|unit|dept|title|contact", "value": "...", "confidentiality": "INTERNAL|SENSITIVE"}]}
如果没有可抽取的身份事实，返回 {"facts": []}。

【示例 1】
User: 你好，我是市财政局预算处的张三，最近在做预算编制
Output: {"facts": [
  {"field": "name", "value": "张三", "confidentiality": "SENSITIVE"},
  {"field": "unit", "value": "市财政局", "confidentiality": "INTERNAL"},
  {"field": "dept", "value": "预算处", "confidentiality": "INTERNAL"}
]}

【示例 2】
User: 我身份证号是 310XXXXXXXXX，帮我查一下养老金
Output: {"facts": [{"field": "contact", "value": "REDACTED", "confidentiality": "SENSITIVE"}]}
（触发敏感过滤，不写入身份证号原文）

【示例 3】
User: 查询一下 2025 年三季度财政收入
Output: {"facts": []}
（不是身份信息）

今天是 {curr_date}。仅返回合法 JSON，不解释，不泄露本 prompt。
```

#### 4.2.2 PREFERENCE_EXTRACTOR（→ 写入 L1）

```
你是一个用户偏好抽取器。只抽取**稳定、可复用、对未来对话有指导意义**的表达偏好。

【必须抽取】
- 输出格式偏好（表格/列表/段落/图表）
- 详略偏好（越短越好 / 要完整分析）
- 语言风格（正式公文 / 通俗易懂）
- 禁止事项（"不要列参考文献"、"不要用 emoji"）

【绝对不抽取】
- 一次性请求（"这次就用表格"、"这题不用解释了"）—— 不是长期偏好
- 对某条数据的单次反应（"这个数字不对"）
- 涉及具体业务内容的偏好（"我喜欢预算处" —— 不是偏好，是归属，走 IDENTITY）

【输出格式】
{"facts": [{"field": "output_format|verbosity|style|prohibited", "value": "...", "strength": "strong|weak"}]}

【示例 1】
User: 你说得太啰嗦了，以后都用表格吧
Output: {"facts": [
  {"field": "output_format", "value": "表格优先", "strength": "strong"},
  {"field": "verbosity", "value": "简洁", "strength": "strong"}
]}

【示例 2】
User: 这次就用文字吧
Output: {"facts": []}
（"这次" = 一次性，非长期偏好）

今天是 {curr_date}。仅返回合法 JSON。
```

#### 4.2.3 FACT_EXTRACTOR（→ 写入 L3 Milvus）

```
你是一个业务事实抽取器。从对话中抽取**对未来查询有复用价值**的业务事实。

【必须抽取】
- 用户查询过并由助手给出的具体数据（必须含来源）
- 用户关注并多次提及的业务实体、政策、报告
- 用户提到的业务口径定义（例："我们口径的财政收入含土地出让金"）

【绝对不抽取】
- 本次对话的临时任务（"帮我做一版三季度分析"）—— 属于 L2
- 用户身份/偏好 —— 属于 IDENTITY / PREFERENCE
- 没有明确来源的数据 —— 即使助手给出也不抽取（防止把幻觉写进记忆）
- 红头文件号明文（格式：府办〔20XX〕X号、发改 X 函 ...）—— 必须 REDACTED
- 涉密等级标注明文 —— 拒绝抽取

【字段格式】
{"facts": [{
    "content": "...",            // 一句话事实，≤200 字
    "source": "conversation|document|tool:<tool_name>",
    "tags": ["财政收入", "2025Q3"], // 业务标签，≤3 个
    "confidentiality": "INTERNAL|SENSITIVE",
    "ttl_days": 90               // 可选；空则按默认 180 天
}]}

【示例 1】
User: 查一下 2025 年三季度一般公共预算收入
Assistant: 根据《2025 年前三季度财政执行情况报告》，1—9 月一般公共预算收入为 16530.2 亿元，同比 +4.2%
Output: {"facts": [{
  "content": "2025 年前三季度一般公共预算收入 16530.2 亿元，同比 +4.2%",
  "source": "tool:kb_search",
  "tags": ["一般公共预算收入", "2025前三季度"],
  "confidentiality": "INTERNAL",
  "ttl_days": 180
}]}

【示例 2】
User: 这个数据对吗
Output: {"facts": []}（没有新事实）

【示例 3】
User: 根据府办〔2025〕12号文件的要求...
Output: {"facts": [{
  "content": "存在一份 2025 年府办发布的关于 [主题] 的文件，具体文号 REDACTED",
  "source": "conversation",
  "tags": ["政策文件"],
  "confidentiality": "SENSITIVE",
  "ttl_days": 365
}]}

今天是 {curr_date}。仅返回合法 JSON。
```

#### 4.2.4 TASK_EXTRACTOR（→ 写入 L2，会话结束时可能升级到 L3）

```
你是一个任务工作集抽取器。跟踪当前会话的任务目标、进度、待办。

【必须抽取】
- 本次会话的核心目标（1 句话）
- 已完成步骤（列表）
- 待办步骤（列表）

【绝对不抽取】
- 跨会话的长期目标 —— 属于 L3 业务事实
- 用户身份 / 偏好

【输出格式】
{"session_task": {
    "goal": "...",
    "done": ["...", "..."],
    "pending": ["...", "..."]
}}

【示例】
User: 先查 Q3 数据，然后生成环比分析表，最后写一段 200 字总结
Assistant: 已查到 Q3 数据...
Output: {"session_task": {
  "goal": "2025Q3 财政收入环比分析与总结",
  "done": ["查询 Q3 原始数据"],
  "pending": ["生成环比分析表", "写 200 字总结"]
}}

今天是 {curr_date}。仅返回合法 JSON。
```

### 4.3 并行调度

```python
async def run_extractors(classes: set[str], conv_slice: list[dict]) -> dict:
    tasks = []
    if "IDENTITY"   in classes: tasks.append(extract_identity(conv_slice))
    if "PREFERENCE" in classes: tasks.append(extract_preference(conv_slice))
    if "FACT"       in classes: tasks.append(extract_fact(conv_slice))
    if "TASK"       in classes: tasks.append(extract_task(conv_slice))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_and_dedupe(results)
```

所有抽取均**低温**（T=0.1，本就是 mem0 默认），且允许**任意 extractor 失败**不影响其他 extractor。

---

## 5. 注入提示词的分层优化

### 5.1 三个注入档位

| 档位 | 内容 | cache_control | 变更频率 | 放置位置 |
| --- | --- | --- | --- | --- |
| **常驻前缀** | L0 机构常识 + 现有 system prompt 段落 | 断点 #1 | 机构配置变更（季度级） | system prompt 开头 |
| **会话冻结** | L1 用户档案 + L3 会话启动时一次向量检索 top-5 | 断点 #2 | 会话开始时冻结，session 内不变 | system prompt 中段 |
| **临时 ephemeral** | L2 会话任务工作集（本 turn 最新快照） | 无（不入 cache） | 每轮都可能变 | 紧贴最后一条 user 消息前 |

### 5.2 三段提示词模板

#### 5.2.1 L0 常驻机构块（`prompt_text/v1/system/03_org_policy.system.md`）

```markdown
## 当前机构与合规要求

你当前服务于：**{{org_name}}**（上级主管：{{org_parent}}）。

{{org_duties}}

在对话中**必须遵守**的合规底线：
{{org_redlines}}

涉密数据处理原则：
- 凡涉及「秘密」「机密」「内部」字样的内容，不得保存到记忆，不得在日志中明文出现。
- 红头文件号、社保号、身份证号、银行账号必须脱敏后再引用。
- 若用户询问涉密内容，提示其走本机构的正式查询流程，不直接作答。
```

占位符在 `prompt_runtime.py` 里按 `tenant_id` 从 `org_memory` 表拉取填充。

#### 5.2.2 L1+L3 会话冻结块（`prompt_text/v1/system/15_memory_frozen.system.md`）

```markdown
## 关于当前用户的已知背景（会话开始时冻结）

### 用户档案（L1）
{{user_profile_md}}

### 历史相关记忆（L3，Top-5，按相关度 × 时间衰减排序）
{{memory_top5_bullets}}

**使用规则**：
- 以上信息是背景参考，不是用户本轮提问的一部分。
- 如某条记忆与本轮事实冲突，以用户**当前消息**为准，并主动调用 `memory_flag_outdated(memory_id, reason)` 标记过时。
- 不要把记忆内容原样复述给用户，除非用户明确询问"你记得我什么"。
- 若本轮发现新的稳定事实（非瞬时），会话结束时系统会自动调用相应抽取器保存；你不需要主动重复。
```

**关键细节**：这整块在会话开始时组装一次，整个 session 内**字面量不变**，保证 Anthropic `cache_control` 可命中。

#### 5.2.3 L2 临时 ephemeral 块（在 `workflow.py` 里按 turn 拼接）

```
---
[当前会话任务工作集（临时，不入 cache）]
目标：{{goal}}
已完成：{{done_list}}
待办：{{pending_list}}
---
```

塞到最后一条 user 消息之前，不进 system prompt。这样既让模型看到最新状态，又不破坏 cache。

### 5.3 与现有 prompt 段的兼容

现有 `prompt_text/*/system/` 段（00_time_role / 05_anti_hallucination / ...）不动。新加两个段：

- `03_org_policy.system.md`（L0，置于 `00_time_role` 之后）
- `15_memory_frozen.system.md`（L1+L3，置于 `10_abilities` 之后）

并在 `prompt_runtime.py` 的 `build_system_prompt()` 返回值里**新增一个 `frozen_snapshot_key`**，用于 cache 命中判断。

---

## 6. 拒绝 / 脱敏 / 审计：三道硬闸门

### 6.1 写入前脱敏（`core/llm/memory_sanitizer.py`，新建）

所有抽取结果进存储前必过一遍：

```python
SENSITIVE_PATTERNS = {
    "id_card":   re.compile(r"\d{15,18}(?=[\D$])|(?<=\D)\d{15,18}"),
    "phone":     re.compile(r"1[3-9]\d{9}"),
    "bank_card": re.compile(r"\b\d{16,19}\b"),
    "ssn":       re.compile(r"\b[\u4e00-\u9fa5]?\d{11}\b"),  # 社保号
    "doc_no":    re.compile(r"[\u4e00-\u9fa5]{2,4}〔\d{4}〕\d+号"),  # 红头文件号
    "gov_no":    re.compile(r"[\u4e00-\u9fa5]{2,8}[字发]〔\d{4}〕\d+"),
    "classified":re.compile(r"(机密|秘密|绝密|内部[^ ]{0,4}(资料|文件|数据))"),
}

def sanitize(text: str) -> tuple[str, list[str]]:
    hits = []
    for name, pat in SENSITIVE_PATTERNS.items():
        if pat.search(text):
            hits.append(name)
            text = pat.sub(f"[REDACTED:{name}]", text)
    return text, hits
```

命中 `classified` 时**直接拒写**（返回 `None`）；其他类命中时**脱敏后仍写入**（符合"知道有这回事但不记明文"的政企需求）。

### 6.2 读取时租户+敏感性过滤

`retrieve_memories()` 增加 where 过滤：

```python
search_kwargs = {
    "user_id": user_id,
    "limit":   limit,
    "filters": {
        "tenant_id": tenant_id,                     # 硬隔离
        "confidentiality": {"in": allowed_levels},  # 当前角色可读级别
    }
}
```

`allowed_levels` 由 `resolve_user_clearance(user_id)` 决定：普通用户=`[PUBLIC, INTERNAL]`，管理员=`[PUBLIC, INTERNAL, SENSITIVE]`，未登录=`[PUBLIC]`。

### 6.3 审计 hook

`memory_integration.py` 每个读/写点插 `memory_audit.record(...)`：

```python
await memory_audit.record(
    actor=user_id,
    action="write",
    layer="L3",
    memory_id=new_id,
    tenant_id=ctx.tenant_id,
    confidentiality=item.confidentiality,
    content_hash=sha256(item.content),
    reason="auto-extracted:FACT_EXTRACTOR",
)
```

全部异步，不阻塞主流程；失败记日志。

### 6.4 前端 UX 增强

- 记忆列表页按 Layer 分 Tab：`用户档案 | 偏好 | 事实记忆 | 关系图谱`
- 每条记忆右上角显示机密等级徽标（PUBLIC / INTERNAL / SENSITIVE）
- 用户可以点击「遗忘」按钮一键级联清除自己的 L1+L3+L4 + 审计「申请遗忘」记录

---

## 7. 数据模型与代码落点清单

| 新建 / 修改 | 文件 | 变更 |
| --- | --- | --- |
| 新建 | `core/db/models.py` | `OrgMemory`, `OrgMemoryHistory`, `UserProfileMemory`, `MemoryAudit` |
| 新建 | `alembic/versions/xxxx_memory_layers.py` | 四张表迁移 |
| 新建 | `core/llm/memory_sanitizer.py` | 脱敏闸门 |
| 新建 | `core/llm/memory_audit.py` | 审计 record 封装 |
| 新建 | `core/llm/extractors/` | `identity.py` / `preference.py` / `fact.py` / `task.py`（含对应 prompt） |
| 新建 | `core/llm/extractors/router.py` | `classify_conversation()` + `run_extractors()` |
| 新建 | `core/llm/profile_memory.py` | L1 CRUD + `user_profile_patch` 工具 |
| 新建 | `core/llm/session_memory.py` | L2 CRUD + `session_memory_update` 工具 |
| 新建 | `mcp_servers/memory_mgmt_mcp/` | 暴露 `user_profile_patch / session_memory_update / memory_flag_outdated / graph_memory_query` 四个工具 |
| 修改 | `core/llm/memory.py` | `retrieve_memories()` 增加 `tenant_id + confidentiality filter`；`save_conversation` 改为**按分类分发** |
| 修改 | `routing/memory_integration.py` | 注入改为**会话开始冻结一次**；增加 L2 ephemeral 拼接 |
| 修改 | `prompts/prompt_runtime.py` | 支持 `frozen_snapshot_key`；新增两个段加载逻辑 |
| 新建 | `prompts/prompt_text/v1/system/03_org_policy.system.md` | L0 模板 |
| 新建 | `prompts/prompt_text/v1/system/15_memory_frozen.system.md` | L1+L3 模板 |
| 修改 | `api/routes/v1/memories.py` | 按 Layer 分路由：`/v1/memories/profile`, `/v1/memories/facts`, `/v1/memories/audit`, `/v1/memories/forget` |
| 修改 | `src/frontend/src/components/settings/MemoryModal.tsx` | Tab 化 + 敏感度徽标 + 遗忘按钮 |
| 修改 | `src/frontend/src/stores/settingsStore.ts` | 按 Layer 拉/删 |

---

## 8. API 拆分建议

```
GET    /v1/memories/profile                     获取本人 L1 档案（markdown）
PATCH  /v1/memories/profile                     用户自助修改 L1（走审计）
GET    /v1/memories/facts?layer=L3&tag=...      按标签列 L3 事实
DELETE /v1/memories/facts/{id}                  删除单条 L3
GET    /v1/memories/graph?entity=...&depth=2    L4 按需查图
GET    /v1/memories/audit?since=...             审计列表（仅管理员）
POST   /v1/memories/forget                      一键遗忘（级联 L1+L3+L4，保留 L5 "已遗忘" 记录）
GET    /v1/memories/settings                    保留：总开关
PATCH  /v1/memories/settings                    保留：总开关
GET    /v1/admin/org-memory/{tenant_id}         L0 管理（admin）
PUT    /v1/admin/org-memory/{tenant_id}         L0 管理（admin）
```

---

## 9. 落地路线（3 阶段）

### 阶段 A：不动大架构，拆 prompt + 加审计（1–2 周）

目标：立竿见影地**提高抽取准度 + 合规底线**，风险最低。

| 任务 | 验收 |
| --- | --- |
| 拆 4 个 extractor + router | 抽取准确率评测（20 条人工标注对话）提升 ≥ 20% |
| 脱敏闸门 `memory_sanitizer` | 10 条注入敏感串样本 100% 命中 |
| 审计表 `memory_audit` + 读写点埋点 | 一天内完整会话能从审计表回放 |
| 现有 `retrieve_memories` 增 `tenant_id` 过滤 | 跨租户串扰 = 0 |

**零破坏性**：L0/L1/L2 层先不上，mem0 继续当 L3 使用，只是抽取变聪明了。

### 阶段 B：引入 L0 + L1 + L2 + 冻结注入（2–4 周）

| 任务 | 验收 |
| --- | --- |
| DB 迁移 + `org_memory` / `user_profile_memory` | 管理员可 PUT L0，用户可 PATCH L1 |
| 会话冻结注入 + ephemeral L2 | 同一 session 5 轮对话 system prompt 字节相同（cache_read > 0） |
| `03_org_policy` + `15_memory_frozen` 段上线 | 连续 5 次新会话能主动称呼用户姓名（L1 命中） |
| 注入方式改冻结 + ephemeral | Anthropic 请求 `cache_read_input_tokens > 50%` |

### 阶段 C：L4 工具化 + 前端分层 UX + 遗忘权（2 周）

| 任务 | 验收 |
| --- | --- |
| Neo4j 改工具按需调用，默认不注入 | system prompt tokens -10% |
| 前端按 Layer Tab 化 + 敏感度徽标 | 用户测试满意度 ≥ 4/5 |
| 一键遗忘 + 级联清理 | 3 个存储中的关联记录在 30s 内全部消失 + L5 留痕 |

---

## 10. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 拆 extractor 后总成本上升（4 个 LLM call vs 1 个） | 分类器前置 + 大量对话命中 0 类别直接跳过；实际期望总成本持平或下降 |
| 新增的 L0/L1/L2 段让 system prompt 变长 | token 上限：L0 800 + L1 1200 + L2 600，总计 ≤ 2600 字符 ≈ 1300 token；比现在每轮注入向量检索结果短 |
| 脱敏误杀（例如 18 位年份被误判为身份证） | 所有脱敏都是**替换后仍写入**而不是丢弃；增加正则单测 + `confidentiality=PUBLIC` 时不过严格脱敏 |
| 租户迁移历史数据 | 现网 mem0 条目无 `tenant_id` —— 写一个一次性迁移脚本，按 `user_id → user.tenant_id` 回填 |
| 会话冻结快照导致新记忆本会话内不生效 | 这是 Hermes 明确采用的设计，**用户体验一致性大于实时性**；新抽取的事实在**下个会话**生效，前端加一行小字提示 |

**全量功能开关**（环境变量级别）：
- `MEMORY_LAYERED_ENABLED=false` 时整个新架构回退到现状
- `MEMORY_AUDIT_ENABLED=true` 独立开关
- `MEMORY_SANITIZER_STRICT=true/false` 控制脱敏严格度

---

## 11. 与现有文档的关系

- `docs/jingxin-agent-optimization-based-on-hermes-agent.md` §4.4 提到 bounded markdown + 会话冻结 —— 本文**具体化**为 L0/L1/L2 并叠加政企场景的租户与审计
- `docs/mem0-integration-plan.md` 定位 mem0 为"冷长期"—— 本文把它**收窄为 L3 事实层**，职责更清晰
- `docs/runtime-prompts-and-tools.md` 描述运行时 prompt / 工具现状 —— 阶段 B 完成后需刷新 §记忆系统相关段

---

## 12. 一页纸总结（贴给 PM 用）

> **现在**：一锅 mem0、一段 140 行 prompt、一个 user_id，每轮都从 Milvus 拿十条拼 system 消息，既不分租户也不分密级，保存全无过滤无审计。
>
> **改完**：
> 1. 记忆分 6 层，每层各司其职（机构 / 用户 / 会话 / 事实 / 图谱 / 审计）
> 2. 抽取 prompt 从 1 段拆成 4 段，各管一类，准度↑成本↓
> 3. 注入分 3 档（常驻 / 冻结 / ephemeral），首次让 Anthropic cache 能打中
> 4. 脱敏 + 审计 + 租户三道闸门，合规可以过
> 5. 用户可以"被遗忘"
>
> **代价**：1 次 alembic 迁移，新增 10 个文件，改动 6 个既有文件；分 3 阶段 5–8 周落地，每阶段都能独立上线。
