# 定时任务 (Scheduled Task) 功能实施方案

> 文档版本: v1.0 | 日期: 2026-04-09

## 1. 背景与目标

### 1.1 需求描述

在 Jingxin-Agent 中实现类似 Claude Coworker / OpenAI Codex 的定时任务功能：

- 用户可以指定时间（一次性或周期性）创建后台 AI 任务
- AI 智能体在指定时间自动执行任务
- 完成后通过网页消息栏实时通知用户
- 任务结果自动保存到"我的空间"(My Space)

### 1.2 竞品调研

#### Claude Code — 进程内 Cron 调度器

| 项目 | 实现细节 |
|------|----------|
| 核心文件 | `src/utils/cronScheduler.ts`, `src/tools/ScheduleCronTool/` |
| 调度机制 | 进程内 1 秒轮询，检查每个 Job 是否到达触发时间 |
| 持久化 | 两级存储：session-only（内存 `STATE.sessionCronTasks`）+ durable（`.claude/scheduled_tasks.json`） |
| 工具接口 | `CronCreate`（5 字段 cron + prompt + recurring + durable）、`CronDelete`、`CronList` |
| 防并发 | `.claude/scheduled_tasks.lock` 锁文件，O_EXCL 原子创建，只有 lock owner 执行 file-backed 任务 |
| Jitter | 确定性 jitter（基于 taskId hash），recurring 任务延迟 ≤ interval × frac（上限 15 分钟），one-shot 在 :00/:30 分钟提前 0-90s |
| 自动过期 | recurring 任务 7 天后自动过期（最后执行一次后删除），`permanent: true` 可豁免 |
| 错过检测 | 启动时检测 missed one-shot 任务，通过 `AskUserQuestion` 提示用户是否执行 |
| 触发方式 | 将 prompt 以 `later` 优先级入队到 REPL command queue，REPL 空闲时消费 |
| 特殊功能 | Teammate 路由（`agentId` 字段）、GrowthBook 远程配置、fleet-wide killswitch |

**关键数据结构：**

```typescript
// 文件持久化格式 (.claude/scheduled_tasks.json)
{
  "tasks": [{
    "id": "a1b2c3d4",        // 8位 hex UUID
    "cron": "0 9 * * 1-5",   // 5字段 cron
    "prompt": "Check PRs",    // 执行提示词
    "createdAt": 1706789000,  // Epoch ms
    "lastFiredAt": 1706789000,// 上次触发时间
    "recurring": true,        // true=循环, false/undefined=一次性
    "permanent": false        // true=不自动过期
  }]
}
```

**调度器核心流程：**

```
每 1 秒 check():
  ├─ 检查 isKilled() killswitch
  ├─ 检查 isLoading()（活跃查询时跳过，assistantMode 除外）
  ├─ 获取 jitter 配置（GrowthBook 远程配置，每 tick 刷新）
  ├─ 遍历 file-backed 任务（仅 lock owner）
  │   ├─ 计算 nextFireTime = jitteredNextCronRunMs(cron, lastFiredAt/createdAt)
  │   ├─ 若 now >= nextFireTime → 触发
  │   │   ├─ recurring: 更新 lastFiredAt → 写盘
  │   │   └─ one-shot 或 aged-out: 删除任务
  │   └─ 否则跳过
  └─ 遍历 session-only 任务（同逻辑，内存操作）
```

#### Claude Coworker — 本地 + 云端混合执行

| 项目 | 实现细节 |
|------|----------|
| 执行模式 | 本地（Desktop 应用开着时）+ 云端（CCR 云基础设施，独立于本地机器） |
| 调度 | cron 表达式，最小间隔 1 小时 |
| 通知 | 手机推送通知（完成/需审批/跳过重执行） |
| 时区 | 用户输入本地时间 → 自动转 UTC cron |
| 远程 Agent | `/schedule` skill → CCR session，含 Git repo + 工具白名单 + 可选 MCP 连接 |
| API | `RemoteTriggerTool` — `list/get/create/update/run` actions |
| 安全 | 沙盒环境，独立 git checkout，无本地文件/环境变量访问 |

**远程触发配置：**

```json
{
  "action": "create",
  "body": {
    "name": "Daily PR Review",
    "cron_expression": "0 9 * * 1-5",
    "enabled": true,
    "job_config": {
      "model": "claude-sonnet-4-6",
      "git_sources": ["repo_url"],
      "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
      "mcp_connections": []
    }
  }
}
```

#### OpenAI Codex — 多 Agent 并行 + Automations

| 项目 | 实现细节 |
|------|----------|
| 执行时间 | 单个 Agent 可独立工作最长 30 分钟 |
| 多 Agent | 支持并行监督多个 Agent，任务可跨小时/天/周 |
| Automations | instructions + skills + 用户定义 schedule |
| Skills | 捆绑 instructions + resources + scripts 的可复用包 |
| 典型用例 | 每日 issue 分类、CI 失败摘要、发布简报、Bug 检查 |
| 平台 | macOS (Apple Silicon) 桌面端 + Web + CLI |

### 1.3 现有基础设施

| 已有能力 | 文件位置 | 可复用性 |
|----------|----------|----------|
| Agent Factory（可独立创建 Agent） | `core/llm/agent_factory.py` | 直接复用 `create_agent_executor()` |
| SSE 流式通信 | `routing/streaming.py` | 可参考模式，通知用新 SSE endpoint |
| Artifact Service（我的空间存储） | `core/services/artifact_service.py` | 直接复用 |
| Redis（会话/缓存） | `core/infra/redis.py` | 复用做 pub/sub 通知推送 |
| PostgreSQL + SQLAlchemy | `core/db/engine.py`, `core/db/models.py` | 新增表即可 |
| Zustand 状态管理 | `stores/*.ts` | 新增 store 即可 |
| Ant Design 组件库 | `notification`, `message`, `Modal`, `Table` 等 | 直接使用 |

| 缺少能力 | 解决方案 |
|----------|----------|
| 任务调度器 | 引入 APScheduler (AsyncIOScheduler) |
| 后台执行器 | 新建 `core/scheduler/executor.py` |
| 通知系统 | 新建 Notification 模型 + Redis pub/sub + SSE endpoint |

---

## 2. 架构设计

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                          Frontend                                │
│                                                                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ Sidebar      │  │ ScheduledTasks   │  │ NotificationBell │   │
│  │ (定时任务入口) │  │ Panel + Modal    │  │ (通知铃铛+Popover)│   │
│  └──────┬───────┘  └───────┬──────────┘  └───────┬──────────┘   │
│         │                  │                      │              │
│  ┌──────┴──────────────────┴──────────────────────┴──────────┐  │
│  │              scheduledTaskStore (Zustand)                   │  │
│  │  tasks[] | runs[] | notifications[] | unreadCount          │  │
│  │  notifSSE (EventSource → /v1/notifications/stream)         │  │
│  └─────────────────────────┬──────────────────────────────────┘  │
│                            │ api.ts                              │
└────────────────────────────┼─────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────┼─────────────────────────────────────┐
│                          Backend                                  │
│                            │                                      │
│  ┌─────────────────────────┴──────────────────────────────────┐  │
│  │              FastAPI Routes (v1/)                            │  │
│  │  /scheduled-tasks (CRUD)     /notifications (SSE + REST)    │  │
│  └──────────┬─────────────────────────────────┬───────────────┘  │
│             │                                  │                  │
│  ┌──────────┴──────────┐          ┌────────────┴──────────────┐  │
│  │ ScheduledTaskService│          │  NotificationService      │  │
│  │ (CRUD + 调度辅助)    │          │  (CRUD + Redis pub/sub)   │  │
│  └──────────┬──────────┘          └────────────┬──────────────┘  │
│             │                                  │                  │
│  ┌──────────┴──────────────────────────────────┴──────────────┐  │
│  │              APScheduler (AsyncIOScheduler)                  │  │
│  │  SQLAlchemyJobStore (PostgreSQL)                            │  │
│  │  CronTrigger / DateTrigger                                  │  │
│  │  coalesce=True | max_instances=1 | misfire_grace=3600      │  │
│  └──────────┬─────────────────────────────────────────────────┘  │
│             │ 到达触发时间                                        │
│  ┌──────────┴─────────────────────────────────────────────────┐  │
│  │              executor.execute_scheduled_task(task_id)        │  │
│  │  1. 加载任务配置                                              │  │
│  │  2. create_agent_executor(isolated=True) → Agent            │  │
│  │  3. asyncio.wait_for(agent.reply(prompt), timeout)          │  │
│  │  4. ArtifactService → 保存到"我的空间"                        │  │
│  │  5. NotificationService → 创建通知                            │  │
│  │  6. Redis PUBLISH → SSE 实时推送                              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌─────────────┐  ┌──────────┐  ┌──────────┐                    │
│  │ PostgreSQL  │  │  Redis   │  │ Storage  │                    │
│  │ (任务/通知表) │  │ (pub/sub)│  │ (文件存储) │                    │
│  └─────────────┘  └──────────┘  └──────────┘                    │
└───────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

**创建任务流程：**
```
用户填写表单 → POST /v1/scheduled-tasks
  → 校验参数（cron 语法、时间范围、权限）
  → ScheduledTaskService.create_task() 写入 DB
  → scheduler.register_job(task) 注册到 APScheduler
  → 返回 task 对象（含 next_run_at）
```

**任务执行流程：**
```
APScheduler 触发 → execute_scheduled_task(task_id)
  → DB: 加载 ScheduledTask 配置
  → DB: 创建 ScheduledTaskRun(status=running)
  → Agent: create_agent_executor(isolated=True, enabled_mcp_ids, enabled_skill_ids)
  → Agent: asyncio.wait_for(agent.reply(Msg(content=prompt)), timeout=timeout_seconds)
  → Storage: 上传输出文本为 markdown 文件
  → DB: ArtifactService.create_artifact(type="document", title="定时任务: {title}")
  → DB: complete_run(status=success, artifact_id, output_text, usage, duration_ms)
  → DB: 更新 ScheduledTask (total_runs++, success_runs++, last_run_at)
  → DB: NotificationService.create(type="task_complete", ref_id=run_id)
  → Redis: PUBLISH notifications:{user_id} → JSON payload
  → (一次性任务) DB: task.status = "completed"
```

**通知推送流程：**
```
前端 App 挂载 → EventSource(/v1/notifications/stream)
  → 后端订阅 Redis channel notifications:{user_id}
  → 有消息时 → SSE data 推送
  → 前端 onmessage → 更新 unreadCount + antd notification.success()
  → 用户点击 → 跳转到任务详情 or 我的空间查看结果
  → 断线 → 回退到 60s 轮询 GET /v1/notifications/unread-count
```

---

## 3. 数据库设计

### 3.1 ScheduledTask 表

新增位置: `src/backend/core/db/models.py`

```python
class ScheduledTask(Base):
    """定时任务主表"""
    __tablename__ = "scheduled_tasks"

    task_id         = Column(String(64), primary_key=True)
    user_id         = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    title           = Column(String(500), nullable=False)
    prompt          = Column(Text, nullable=False)
    schedule_type   = Column(String(10), nullable=False)        # "once" | "cron"
    cron_expr       = Column(String(100))                       # 5字段cron (cron类型必填)
    run_at          = Column(TIMESTAMP(timezone=True))           # 一次性任务执行时间
    timezone        = Column(String(50), nullable=False, default="Asia/Shanghai")
    status          = Column(String(20), nullable=False, default="active")

    # Agent 配置快照
    agent_id        = Column(String(64), ForeignKey("user_agents.agent_id", ondelete="SET NULL"))
    enabled_mcp_ids = Column(JSONType, default=list)
    enabled_skill_ids = Column(JSONType, default=list)
    enabled_kb_ids  = Column(JSONType, default=list)
    max_iters       = Column(Integer, default=30)
    timeout_seconds = Column(Integer, default=300)

    # 执行统计
    total_runs      = Column(Integer, default=0)
    success_runs    = Column(Integer, default=0)
    last_run_at     = Column(TIMESTAMP(timezone=True))
    next_run_at     = Column(TIMESTAMP(timezone=True))

    extra_data      = Column("metadata", JSONType, default={})
    created_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at      = Column(TIMESTAMP(timezone=True))

    # 关系
    user = relationship("UserShadow")
    runs = relationship("ScheduledTaskRun", back_populates="task",
                        cascade="all, delete-orphan",
                        order_by="ScheduledTaskRun.started_at.desc()")

    __table_args__ = (
        CheckConstraint("schedule_type IN ('once', 'cron')", name="schtask_type_check"),
        CheckConstraint("status IN ('active', 'paused', 'completed', 'cancelled')", name="schtask_status_check"),
        Index("idx_schtask_user_id", "user_id"),
        Index("idx_schtask_status", "status"),
        Index("idx_schtask_next_run", "next_run_at"),
    )
```

### 3.2 ScheduledTaskRun 表

```python
class ScheduledTaskRun(Base):
    """定时任务执行记录"""
    __tablename__ = "scheduled_task_runs"

    run_id       = Column(String(64), primary_key=True)
    task_id      = Column(String(64), ForeignKey("scheduled_tasks.task_id", ondelete="CASCADE"), nullable=False)
    status       = Column(String(20), nullable=False, default="pending")
    started_at   = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    duration_ms  = Column(BigInteger)

    output_text    = Column(Text)
    artifact_id    = Column(String(64), ForeignKey("artifacts.artifact_id", ondelete="SET NULL"))
    error_message  = Column(Text)
    tool_calls_log = Column(JSONType, default=list)
    usage          = Column(JSONType)
    extra_data     = Column("metadata", JSONType, default={})

    task     = relationship("ScheduledTask", back_populates="runs")
    artifact = relationship("Artifact")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'timeout')",
            name="schtask_run_status_check",
        ),
        Index("idx_schtask_run_task_id", "task_id"),
        Index("idx_schtask_run_started", "started_at"),
    )
```

### 3.3 Notification 表

```python
class Notification(Base):
    """用户通知表"""
    __tablename__ = "notifications"

    notification_id = Column(String(64), primary_key=True)
    user_id         = Column(String(64), ForeignKey("users_shadow.user_id", ondelete="CASCADE"), nullable=False)
    type            = Column(String(50), nullable=False)        # "task_complete" | "task_failed" | "system"
    title           = Column(String(500), nullable=False)
    body            = Column(Text, default="")
    is_read         = Column(Boolean, default=False, nullable=False)
    ref_type        = Column(String(50))                        # "scheduled_task_run"
    ref_id          = Column(String(64))                        # run_id
    extra_data      = Column("metadata", JSONType, default={})
    created_at      = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        Index("idx_notif_user_id", "user_id"),
        Index("idx_notif_user_read", "user_id", "is_read"),
    )
```

---

## 4. 后端实现

### 4.1 依赖

```
# requirements.txt 新增
apscheduler>=3.10.0
```

### 4.2 调度器模块 `src/backend/core/scheduler/`

#### `scheduler.py` — APScheduler 生命周期

```python
"""APScheduler integration for scheduled tasks.

使用 AsyncIOScheduler + SQLAlchemyJobStore (复用 PostgreSQL)，
确保服务重启后 Job 状态自动恢复。
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

_scheduler: AsyncIOScheduler | None = None

def get_scheduler() -> AsyncIOScheduler:
    """获取全局调度器实例（懒初始化）。"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=DATABASE_URL)},
            job_defaults={
                "coalesce": True,           # 合并错过的执行为一次
                "max_instances": 1,         # 同一任务不并行执行
                "misfire_grace_time": 3600, # 1小时内的错过可补执行
            },
            timezone="Asia/Shanghai",
        )
    return _scheduler

async def start_scheduler():
    """启动调度器，从 DB 加载所有 active 任务并注册 Job。"""
    scheduler = get_scheduler()
    await _reload_tasks_from_db(scheduler)
    scheduler.start()

async def shutdown_scheduler():
    """优雅关闭调度器。"""
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)

def register_job(task: ScheduledTask):
    """将一个 ScheduledTask 注册为 APScheduler Job。"""
    scheduler = get_scheduler()
    job_id = f"schtask_{task.task_id}"

    if task.schedule_type == "cron" and task.cron_expr:
        parts = task.cron_expr.split()
        trigger = CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
            timezone=task.timezone,
        )
    elif task.schedule_type == "once" and task.run_at:
        trigger = DateTrigger(run_date=task.run_at, timezone=task.timezone)
    else:
        return

    scheduler.add_job(
        "core.scheduler.executor:execute_scheduled_task",
        trigger=trigger, args=[task.task_id],
        id=job_id, replace_existing=True, name=task.title,
    )

def remove_job(task_id: str):
    """移除指定 Job（暂停/取消/删除时调用）。"""
    try:
        get_scheduler().remove_job(f"schtask_{task_id}")
    except Exception:
        pass
```

#### `executor.py` — 后台 Agent 执行核心

```python
async def execute_scheduled_task(task_id: str) -> None:
    """由 APScheduler 调用的后台执行函数。

    完整流程：
    1. 从 DB 加载任务配置
    2. 创建执行记录 ScheduledTaskRun(status=running)
    3. 通过 agent_factory 创建隔离 Agent
    4. 执行 Agent 并收集结果
    5. 保存结果为 Artifact（进入"我的空间"）
    6. 创建 Notification
    7. 通过 Redis pub/sub 推送实时通知
    8. 一次性任务自动标记完成
    """
    mcp_clients = []
    try:
        # 1. 加载任务
        with SessionLocal() as db:
            svc = ScheduledTaskService(db)
            task = svc.get_task_internal(task_id)
            if not task or task.status != "active":
                return
            run = svc.create_run(task_id)
            run_id = run.run_id

        # 2. 创建 Agent (isolated=True 避免共享 MCP 池问题)
        agent, mcp_clients = await create_agent_executor(
            enabled_mcp_ids=task.enabled_mcp_ids or [],
            enabled_skill_ids=task.enabled_skill_ids or [],
            enabled_kb_ids=task.enabled_kb_ids or [],
            current_user_id=task.user_id,
            max_iters=task.max_iters,
            isolated=True,
        )

        # 3. 执行（带超时）
        start_time = time.monotonic()
        result = await asyncio.wait_for(
            agent.reply(Msg(content=task.prompt, role="user")),
            timeout=task.timeout_seconds,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)
        output_text = extract_text_from_chat_response(result)

        # 4. 保存为 Artifact
        artifact_id = None
        if output_text:
            storage = get_storage()
            aid = f"artifact_{uuid.uuid4().hex[:16]}"
            key = f"scheduled_tasks/{task_id}/{run_id}.md"
            storage.upload_bytes(key, output_text.encode("utf-8"))

            with SessionLocal() as db:
                asvc = ArtifactService(db)
                artifact = asvc.create_artifact(
                    user_id=task.user_id,
                    artifact_type="document",
                    title=f"定时任务: {task.title}",
                    filename=f"task_{run_id}.md",
                    size_bytes=len(output_text.encode("utf-8")),
                    mime_type="text/markdown",
                    storage_key=key,
                )
                artifact_id = artifact["artifact_id"]

        # 5. 更新执行记录
        with SessionLocal() as db:
            svc = ScheduledTaskService(db)
            svc.complete_run(run_id, status="success",
                             output_text=output_text, artifact_id=artifact_id,
                             duration_ms=duration_ms)
            svc.increment_stats(task_id, success=True)

        # 6. 创建通知 + 推送
        await _notify(task, run_id, success=True, output_text=output_text)

        # 7. 一次性任务标记完成
        if task.schedule_type == "once":
            with SessionLocal() as db:
                svc = ScheduledTaskService(db)
                svc.update_task_internal(task_id, status="completed")

    except asyncio.TimeoutError:
        duration_ms = task.timeout_seconds * 1000
        with SessionLocal() as db:
            svc = ScheduledTaskService(db)
            svc.complete_run(run_id, status="timeout",
                             error_message=f"超时：超过 {task.timeout_seconds} 秒限制",
                             duration_ms=duration_ms)
            svc.increment_stats(task_id, success=False)
        await _notify(task, run_id, success=False, error_message="执行超时")

    except Exception as exc:
        with SessionLocal() as db:
            svc = ScheduledTaskService(db)
            svc.complete_run(run_id, status="failed",
                             error_message=str(exc)[:2000])
            svc.increment_stats(task_id, success=False)
        await _notify(task, run_id, success=False, error_message=str(exc)[:200])

    finally:
        # 始终清理 MCP 客户端
        for client in mcp_clients:
            try:
                await client.close()
            except Exception:
                pass


async def _notify(task, run_id, success, output_text=None, error_message=None):
    """创建 DB 通知 + Redis pub/sub 推送。"""
    with SessionLocal() as db:
        nsvc = NotificationService(db)
        notif = nsvc.create(
            user_id=task.user_id,
            type="task_complete" if success else "task_failed",
            title=f"定时任务「{task.title}」{'执行完成' if success else '执行失败'}",
            body=(output_text or error_message or "")[:200],
            ref_type="scheduled_task_run",
            ref_id=run_id,
        )

    # Redis 实时推送
    redis = await get_redis()
    await redis.publish(
        f"notifications:{task.user_id}",
        json.dumps({
            "notification_id": notif.notification_id,
            "type": notif.type,
            "title": notif.title,
            "ref_id": run_id,
            "task_id": task.task_id,
        }),
    )
```

### 4.3 服务层

#### `scheduled_task_service.py`

遵循现有 `ArtifactService` 模式：

```python
class ScheduledTaskService:
    def __init__(self, db: Session):
        self.db = db

    def create_task(self, *, user_id, title, prompt, schedule_type,
                    cron_expr=None, run_at=None, timezone="Asia/Shanghai",
                    agent_id=None, enabled_mcp_ids=None, enabled_skill_ids=None,
                    enabled_kb_ids=None, max_iters=30, timeout_seconds=300) -> ScheduledTask

    def get_task(self, task_id: str, user_id: str) -> Optional[ScheduledTask]
    def get_task_internal(self, task_id: str) -> Optional[ScheduledTask]  # 无权限检查
    def list_tasks(self, user_id: str, status=None, page=1, page_size=20) -> tuple[list, int]
    def update_task(self, task_id: str, user_id: str, **kwargs) -> Optional[ScheduledTask]
    def update_task_internal(self, task_id: str, **kwargs) -> None  # 调度器内部用

    def pause_task(self, task_id: str, user_id: str) -> bool
    def resume_task(self, task_id: str, user_id: str) -> bool
    def cancel_task(self, task_id: str, user_id: str) -> bool
    def delete_task(self, task_id: str, user_id: str) -> bool  # 软删除

    def create_run(self, task_id: str) -> ScheduledTaskRun
    def complete_run(self, run_id: str, *, status, **kwargs) -> ScheduledTaskRun
    def list_runs(self, task_id: str, user_id: str, limit=10) -> list
    def increment_stats(self, task_id: str, success: bool) -> None

    def get_active_tasks(self) -> list[ScheduledTask]  # 调度器启动时加载
    def compute_next_run(self, task: ScheduledTask) -> Optional[datetime]
```

#### `notification_service.py`

```python
class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, user_id, type, title, body="",
               ref_type=None, ref_id=None) -> Notification
    def list_unread(self, user_id: str, limit=50) -> list
    def count_unread(self, user_id: str) -> int
    def mark_read(self, notification_id: str, user_id: str) -> bool
    def mark_all_read(self, user_id: str) -> int
    def list_all(self, user_id: str, page=1, page_size=20) -> tuple[list, int]
```

### 4.4 API 端点

#### 定时任务 `api/routes/v1/scheduled_tasks.py`

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/v1/scheduled-tasks` | 创建任务（校验 cron 语法，注册 APScheduler Job） |
| `GET` | `/v1/scheduled-tasks` | 列表（`?status=active&page=1&page_size=20`） |
| `GET` | `/v1/scheduled-tasks/{task_id}` | 详情（含最近 runs） |
| `PATCH` | `/v1/scheduled-tasks/{task_id}` | 更新配置（重新注册 Job） |
| `POST` | `/v1/scheduled-tasks/{task_id}/pause` | 暂停（移除 Job） |
| `POST` | `/v1/scheduled-tasks/{task_id}/resume` | 恢复（重新注册 Job） |
| `POST` | `/v1/scheduled-tasks/{task_id}/cancel` | 永久取消 |
| `DELETE` | `/v1/scheduled-tasks/{task_id}` | 软删除 |
| `POST` | `/v1/scheduled-tasks/{task_id}/run-now` | 手动立即执行（测试用） |
| `GET` | `/v1/scheduled-tasks/{task_id}/runs` | 执行历史 |
| `GET` | `/v1/scheduled-tasks/{task_id}/runs/{run_id}` | 单次执行详情 |

**Request Schema 示例：**

```python
class CreateScheduledTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    prompt: str = Field(..., min_length=1, max_length=5000)
    schedule_type: Literal["once", "cron"]
    cron_expr: Optional[str] = None
    run_at: Optional[datetime] = None
    timezone: str = "Asia/Shanghai"
    agent_id: Optional[str] = None
    enabled_mcp_ids: Optional[List[str]] = None
    enabled_skill_ids: Optional[List[str]] = None
    enabled_kb_ids: Optional[List[str]] = None
    max_iters: int = Field(30, ge=1, le=100)
    timeout_seconds: int = Field(300, ge=30, le=1800)
```

#### 通知 `api/routes/v1/notifications.py`

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/v1/notifications/stream` | SSE 实时推送（Redis pub/sub 订阅） |
| `GET` | `/v1/notifications` | 分页列表 |
| `GET` | `/v1/notifications/unread-count` | 未读数量 `{ count: N }` |
| `POST` | `/v1/notifications/{id}/read` | 标记已读 |
| `POST` | `/v1/notifications/read-all` | 全部已读 |

**SSE endpoint 实现要点：**

```python
@router.get("/stream")
async def notification_stream(user = Depends(get_current_user)):
    """Redis pub/sub → SSE 推送。30s 心跳保活。"""
    async def _gen():
        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"notifications:{user.user_id}")
        try:
            while True:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=30,
                )
                if msg and msg["type"] == "message":
                    yield f"data: {msg['data'].decode()}\n\n"
                else:
                    yield ": heartbeat\n\n"
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
```

### 4.5 生命周期挂载

修改 `src/backend/api/app.py`：

```python
# _startup_preload() 末尾添加:
try:
    from core.scheduler.scheduler import start_scheduler
    await start_scheduler()
    logger.info("[startup] Task scheduler started")
except Exception as exc:
    logger.warning("[startup] Scheduler start failed: %s", exc)

# _shutdown_pools() 添加:
try:
    from core.scheduler.scheduler import shutdown_scheduler
    await shutdown_scheduler()
except Exception as e:
    logger.warning("scheduler_shutdown_error", error=str(e))
```

---

## 5. 前端详细设计

> 本章节包含完整的前端实现方案，涵盖视觉设计规范、组件交互细节、状态管理、动画效果、
> 以及自动化结果传送到"我的空间"的完整数据流。

### 5.1 设计系统适配

所有新增组件必须遵循现有 CSS 变量体系（`styles/variables.css`）：

| Token | 值 | 用途 |
|-------|-----|------|
| `--color-primary` | `#126DFF` | 主操作色 (active 状态、按钮) |
| `--color-primary-bg` | `#DBE9FF` | 选中行/卡片背景 |
| `--color-primary-light` | `#EBF2FF` | 悬浮高亮 |
| `--color-success` | `#02B589` | 成功状态 |
| `--color-warning` | `#F8AB42` | 运行中/暂停 |
| `--color-error` | `#FC5D5D` | 失败/超时 |
| `--color-fill-hover` | `#EBEDEE` | 行悬浮 |
| `--color-bg-gray` | `#F5F6F7` | 卡片背景 |
| `--color-border` | `#E3E6EA` | 分割线 |
| `--color-text` | `#262626` | 主文本 |
| `--color-text-secondary` | `#4D4D4D` | 次要文本 |
| `--color-text-tertiary` | `#808080` | 辅助/时间文本 |
| `--shadow-card` | `0 2px 8px rgba(0,0,0,.06)` | 卡片阴影 |
| `--shadow-card-hover` | `0 8px 24px rgba(0,0,0,.10)` | 悬浮阴影 |
| `--radius-sm` | `8px` | 卡片圆角 |
| `--radius-md` | `12px` | 面板圆角 |

CSS 类名命名规则: `.jx-auto-{element}[--modifier]` (BEM 风格, `auto` 为功能前缀)

---

### 5.2 侧边栏入口设计

#### 导航项定义

修改 `src/frontend/src/components/sidebar/Sidebar.tsx` 中的 `NAV_ITEMS` 数组：

```typescript
const NAV_ITEMS = [
  { key: 'agents',         label: '子智能体', icon: '/home/子智能体.svg',   targetPanel: 'agents',         activePanels: ['agents'] },
  { key: 'kb',             label: '知识库',   icon: '/home/知识库.svg',     targetPanel: 'kb',             activePanels: ['kb'] },
  { key: 'app_center',     label: '应用中心', icon: '/home/应用中心.svg',   targetPanel: 'app_center',     activePanels: ['app_center'] },
  { key: 'ability_center', label: '能力中心', icon: '/home/能力中心.svg',   targetPanel: 'ability_center', activePanels: ['ability_center', 'skills', 'mcp'] },
  // ── 新增 ──
  { key: 'automation',     label: '自动化',   icon: '/home/自动化.svg',     targetPanel: 'automation',     activePanels: ['automation'] },
  { key: 'my_space',       label: '我的空间', icon: '/home/我的空间.svg',   targetPanel: 'my_space',       activePanels: ['my_space'] },
];
```

> 自动化 SVG 图标需要新增 `public/home/自动化.svg`，设计为时钟+闪电的组合图标，
> 风格与现有 SVG 图标保持一致（24x24, stroke: currentColor, 2px stroke-width）。

#### 侧边栏 Active 状态样式

复用现有 `.jx-navItem.active` 样式：
- 背景: `#EBF2FF` (--color-primary-light)
- 文字/图标: `#126DFF` (--color-primary)
- 过渡: `background 0.15s`

#### 类型扩展

`types.ts` 中 `PanelKey` 新增 `'automation'`：

```typescript
export type PanelKey = 'chat' | 'skills' | 'agents' | 'mcp' | 'kb' | 'docs'
  | 'app_center' | 'settings' | 'share_records' | 'my_space' | 'ability_center'
  | 'automation';  // ← 新增
```

---

### 5.3 自动化面板总体布局 (AutomationPanel)

文件: `src/frontend/src/components/automation/AutomationPanel.tsx`

```
┌─────────────────────────────────────────────────────────────┐
│  自动化面板 (jx-auto)                                        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  HEADER (jx-auto-header)                             │   │
│  │  ┌─────────────────────────────────────────────────┐ │   │
│  │  │ 自动化                              [+ 新建任务] │ │   │
│  │  │ 创建定时任务，AI 将在指定时间自动执行           │ │   │
│  │  └─────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  STATS BAR (jx-auto-stats)                           │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │   │
│  │  │ 活跃 3  │ │ 暂停 1  │ │ 完成 12 │ │ 失败 2  │  │   │
│  │  │  ●      │ │  ◐      │ │  ✓      │ │  ✕      │  │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  FILTER BAR (jx-auto-filterBar)                      │   │
│  │  [全部▼] [搜索...                        ] [刷新 ↻]  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TASK LIST (jx-auto-taskList)                        │   │
│  │                                                      │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  TaskCard #1                                  │   │   │
│  │  │  ┌──────────────────────────────────────────┐│   │   │
│  │  │  │ ⚡ 每日新闻摘要           [active] 活跃  ││   │   │
│  │  │  │ 每天 09:00 执行 · 下次: 2h 后            ││   │   │
│  │  │  │ 共执行 23 次 · 成功 22 · 失败 1          ││   │   │
│  │  │  │ ────────────────────────────────────────  ││   │   │
│  │  │  │ [暂停] [立即执行] [详情→]       [⋮ 更多] ││   │   │
│  │  │  └──────────────────────────────────────────┘│   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │                                                      │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  TaskCard #2                                  │   │   │
│  │  │  ...                                          │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │                                                      │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  EMPTY STATE (when no tasks)                  │   │   │
│  │  │       ┌─────────┐                             │   │   │
│  │  │       │  ⏰     │                             │   │   │
│  │  │       └─────────┘                             │   │   │
│  │  │  还没有自动化任务                              │   │   │
│  │  │  创建你的第一个定时任务，让 AI 自动为你工作     │   │   │
│  │  │       [+ 创建任务]                             │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │                                                      │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

#### 核心样式规范

```css
/* ── 面板容器 ── */
.jx-auto {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  padding: 0;
}

/* ── 面板标题区 ── */
.jx-auto-header {
  padding: 24px 24px 0;
  flex-shrink: 0;
}
.jx-auto-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.jx-auto-title {
  font-size: var(--font-size-lg);  /* 18px */
  font-weight: 700;
  color: var(--color-text);
}
.jx-auto-subtitle {
  font-size: var(--font-size-xs);  /* 12px */
  color: var(--color-text-tertiary);
  margin-top: 4px;
}
.jx-auto-createBtn {
  height: 36px;
  border-radius: 8px;
  background: var(--color-primary);
  color: #fff;
  font-weight: 600;
  font-size: 14px;
  border: none;
  padding: 0 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: background 0.2s, box-shadow 0.2s;
}
.jx-auto-createBtn:hover {
  background: var(--color-primary-hover);
  box-shadow: 0 4px 12px rgba(18, 109, 255, 0.3);
}

/* ── 统计条 ── */
.jx-auto-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 16px 24px;
}
.jx-auto-statCard {
  background: var(--color-bg-gray);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
}
.jx-auto-statCard:hover {
  background: #fff;
  box-shadow: var(--shadow-card);
}
.jx-auto-statCard--selected {
  background: var(--color-primary-light);
  box-shadow: inset 0 0 0 1px var(--color-primary);
}
.jx-auto-statCard-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}
.jx-auto-statCard-icon--active   { background: rgba(18,109,255,0.1); color: var(--color-primary); }
.jx-auto-statCard-icon--paused   { background: rgba(248,171,66,0.1); color: var(--color-warning); }
.jx-auto-statCard-icon--completed { background: rgba(2,181,137,0.1); color: var(--color-success); }
.jx-auto-statCard-icon--failed   { background: rgba(252,93,93,0.1);  color: var(--color-error); }

.jx-auto-statCard-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.jx-auto-statCard-value {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--color-text);
}
```

---

### 5.4 任务卡片设计 (TaskCard)

文件: `src/frontend/src/components/automation/TaskCard.tsx`

#### 卡片布局

```
┌────────────────────────────────────────────────────────────────┐
│  jx-auto-card                                                   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ROW 1: 标题行                                           │   │
│  │  ┌────┐                                                  │   │
│  │  │ ⚡ │  每日新闻摘要生成          [Tag: 活跃]  [⋮]     │   │
│  │  └────┘                                                  │   │
│  │  schedule-icon                                           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ROW 2: 调度信息行                                       │   │
│  │  🕐 每天 09:00 执行 (Asia/Shanghai)                      │   │
│  │  下次执行: 2小时14分后                                    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ROW 3: 指令预览                                         │   │
│  │  "请搜索今天的经信领域重要新闻，整理成简报..."            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ROW 4: 执行统计                                         │   │
│  │  ┌──────────────────────────────────────────┐           │   │
│  │  │ ████████████████░░░░ 95.6% 成功率        │           │   │
│  │  └──────────────────────────────────────────┘           │   │
│  │  共 23 次 · 成功 22 · 失败 1 · 最近: 今天 09:02         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  ROW 5: 操作栏 (分割线上方)                              │   │
│  │  ─────────────────────────────────────────────           │   │
│  │  [⏸ 暂停]  [▶ 立即执行]               [查看详情 →]     │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

#### 状态 Tag 色彩映射

| 状态 | Tag 背景 | Tag 文字 | Tag 文本 |
|------|----------|----------|----------|
| `active` | `rgba(18,109,255,0.08)` | `#126DFF` | 活跃 |
| `paused` | `rgba(248,171,66,0.08)` | `#D4910A` | 已暂停 |
| `completed` | `rgba(2,181,137,0.08)` | `#02B589` | 已完成 |
| `cancelled` | `rgba(140,148,162,0.08)` | `#8C94A2` | 已取消 |

#### 卡片样式

```css
.jx-auto-card {
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);    /* 12px */
  padding: 20px;
  margin-bottom: 12px;
  transition: box-shadow 0.2s, border-color 0.2s;
  cursor: pointer;
}
.jx-auto-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow-card-hover);
}

/* 标题行 */
.jx-auto-card-titleRow {
  display: flex;
  align-items: center;
  gap: 10px;
}
.jx-auto-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #E8F0FE 0%, #D6E4FF 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.jx-auto-card-title {
  flex: 1;
  font-size: var(--font-size-sm);  /* 14px */
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jx-auto-card-tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 500;
}

/* 调度信息 */
.jx-auto-card-schedule {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-xs);
  color: var(--color-text-tertiary);
}
.jx-auto-card-nextRun {
  font-size: var(--font-size-xs);
  color: var(--color-primary);
  font-weight: 500;
}

/* 指令预览 */
.jx-auto-card-prompt {
  margin-top: 8px;
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  background: var(--color-bg-gray);
  border-radius: 6px;
  padding: 8px 12px;
}

/* 成功率进度条 */
.jx-auto-card-progressBar {
  margin-top: 12px;
  height: 4px;
  background: var(--color-fill-hover);
  border-radius: 2px;
  overflow: hidden;
}
.jx-auto-card-progressBar-fill {
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(90deg, var(--color-success), #0AD9A5);
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
}
.jx-auto-card-statsText {
  margin-top: 6px;
  font-size: 11px;
  color: var(--color-text-tertiary);
  display: flex;
  gap: 8px;
}

/* 操作栏 */
.jx-auto-card-actions {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 8px;
}
.jx-auto-card-actionBtn {
  height: 28px;
  font-size: 12px;
  border-radius: 6px;
  padding: 0 10px;
  display: flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--color-border);
  background: #fff;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.jx-auto-card-actionBtn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--color-primary-light);
}
.jx-auto-card-actionBtn--primary {
  border-color: var(--color-primary);
  background: var(--color-primary);
  color: #fff;
}
.jx-auto-card-actionBtn--primary:hover {
  background: var(--color-primary-hover);
}
.jx-auto-card-detailLink {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-primary);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
}
```

#### 动画效果

```css
/* 卡片入场动画 (staggered) */
@keyframes jx-auto-card-in {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
.jx-auto-card {
  animation: jx-auto-card-in 0.35s ease both;
}
.jx-auto-card:nth-child(1) { animation-delay: 0ms; }
.jx-auto-card:nth-child(2) { animation-delay: 60ms; }
.jx-auto-card:nth-child(3) { animation-delay: 120ms; }
.jx-auto-card:nth-child(4) { animation-delay: 180ms; }
.jx-auto-card:nth-child(5) { animation-delay: 240ms; }

/* 删除卡片动画 */
@keyframes jx-auto-card-out {
  to { opacity: 0; transform: translateX(-20px); height: 0; padding: 0; margin: 0; border: none; }
}
.jx-auto-card--removing {
  animation: jx-auto-card-out 0.4s ease forwards;
  pointer-events: none;
}

/* "下次执行"倒计时闪烁 (即将执行时) */
@keyframes jx-auto-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.jx-auto-card-nextRun--imminent {
  animation: jx-auto-pulse 1.5s ease-in-out infinite;
  color: var(--color-warning);
  font-weight: 600;
}

/* 运行中状态 spinner */
.jx-auto-card--running .jx-auto-card-icon {
  background: linear-gradient(135deg, #FFF3E0, #FFE0B2);
}
@keyframes jx-auto-spin {
  to { transform: rotate(360deg); }
}
.jx-auto-card-runningDot {
  width: 8px;
  height: 8px;
  border: 2px solid var(--color-warning);
  border-top-color: transparent;
  border-radius: 50%;
  animation: jx-auto-spin 0.8s linear infinite;
}
```

---

### 5.5 创建任务对话框 (TaskCreateModal)

文件: `src/frontend/src/components/automation/TaskCreateModal.tsx`

使用 Ant Design `Modal` + `Form` 组件，宽度 640px。

#### 表单布局

```
┌──────────────────────────────────────────────────────────────┐
│  创建自动化任务                                      [✕]    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  任务名称 *                                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 每日经信领域新闻摘要                                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  执行指令 *                                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 请搜索今天经信领域的重要新闻，包括政策动态、行业趋势  │   │
│  │ 和企业信息，整理成不超过500字的简报。                  │   │
│  │                                                       │   │
│  │                                          (0/5000字)   │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  执行方式                                                    │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │  ○ 定时执行      │  │  ● 周期执行      │                 │
│  │    指定一个时间   │  │    按 Cron 周期   │                 │
│  └──────────────────┘  └──────────────────┘                 │
│                                                              │
│  ═══════════════════════════════════════════════════════     │
│  ▼ 周期执行设置 (CronBuilder 组件)                          │
│                                                              │
│  快捷选择:                                                   │
│  [每小时] [每天9点] [工作日9点] [每周一9点] [自定义]         │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Cron 表达式:  0 9 * * 1-5                             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  接下来 5 次执行时间:                                        │
│  · 04/10 (周四) 09:00                                       │
│  · 04/11 (周五) 09:00                                       │
│  · 04/14 (周一) 09:00                                       │
│  · 04/15 (周二) 09:00                                       │
│  · 04/16 (周三) 09:00                                       │
│  ═══════════════════════════════════════════════════════     │
│                                                              │
│  ▸ 高级设置 (折叠面板)                                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 指定智能体   [默认智能体 ▼]                           │   │
│  │ 启用工具     [□ 网络搜索] [□ 知识库检索] [□ 代码沙箱] │   │
│  │ 启用知识库   [□ 政策法规库] [□ 行业数据库]            │   │
│  │ 超时时间     [────○──────── 5分钟]                    │   │
│  │ 最大迭代     [────○──────── 30次]                     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                              [取消]  [创建任务]              │
└──────────────────────────────────────────────────────────────┘
```

#### CronBuilder 组件

文件: `src/frontend/src/components/automation/CronBuilder.tsx`

```typescript
interface CronBuilderProps {
  value: string;                      // cron 表达式
  onChange: (cron: string) => void;
}

// 预设选项
const PRESETS = [
  { label: '每小时',       cron: '0 * * * *',     desc: '每个整点执行' },
  { label: '每天 9:00',    cron: '0 9 * * *',     desc: '每天上午 9 点' },
  { label: '工作日 9:00',  cron: '0 9 * * 1-5',   desc: '周一至周五上午 9 点' },
  { label: '每周一 9:00',  cron: '0 9 * * 1',     desc: '每周一上午 9 点' },
  { label: '每月1日 9:00', cron: '0 9 1 * *',     desc: '每月第一天' },
  { label: '自定义',       cron: '',               desc: '输入自定义 Cron 表达式' },
];
```

预设按钮使用 pill 形状 (Radio.Group)，选中态 `--color-primary-bg` 背景 + `--color-primary` 边框。

自定义模式下显示：
- 5 个输入框（分、时、日、月、周），各带 label 和 placeholder
- 或一个完整的 cron 表达式输入框
- 实时解析并展示下 5 次执行时间

**下次执行时间预览** 使用 `cron-parser` 库（需要安装 `npm i cron-parser`）：

```css
.jx-auto-cronPreview {
  margin-top: 12px;
  padding: 12px;
  background: var(--color-bg-gray);
  border-radius: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.jx-auto-cronPreview-item {
  padding: 4px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.jx-auto-cronPreview-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-primary);
}
```

---

### 5.6 任务详情抽屉 (TaskDetailDrawer)

文件: `src/frontend/src/components/automation/TaskDetailDrawer.tsx`

使用 Ant Design `Drawer`，从右侧滑出，宽度 520px。

```
┌─────────────────────────────────────────────────┐
│  Drawer (jx-auto-drawer)                  [✕]   │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │  任务信息 (jx-auto-drawer-info)            │ │
│  │                                            │ │
│  │  ┌────┐  每日新闻摘要                      │ │
│  │  │ ⚡ │  创建于 2026/04/08  [Tag: 活跃]   │ │
│  │  └────┘                                    │ │
│  │                                            │ │
│  │  执行指令                                  │ │
│  │  ┌──────────────────────────────────────┐  │ │
│  │  │ 请搜索今天经信领域的重要新闻...      │  │ │
│  │  └──────────────────────────────────────┘  │ │
│  │                                            │ │
│  │  调度配置                                  │ │
│  │  类型: 周期执行                            │ │
│  │  Cron: 0 9 * * 1-5 (工作日 09:00)         │ │
│  │  时区: Asia/Shanghai                       │ │
│  │  超时: 300秒                               │ │
│  │                                            │ │
│  │  启用工具: 网络搜索, 知识库检索            │ │
│  │  启用知识库: 政策法规库                    │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │  执行历史 (jx-auto-drawer-runs)            │ │
│  │                                            │ │
│  │  ┌────────────────────────────────────┐    │ │
│  │  │ #23  ✅ 成功  04/09 09:02  12.3s  │→   │ │
│  │  ├────────────────────────────────────┤    │ │
│  │  │ #22  ✅ 成功  04/08 09:01  10.8s  │→   │ │
│  │  ├────────────────────────────────────┤    │ │
│  │  │ #21  ❌ 失败  04/07 09:00  --     │→   │ │
│  │  │      错误: Agent 超时               │    │ │
│  │  ├────────────────────────────────────┤    │ │
│  │  │ #20  ✅ 成功  04/04 09:02  11.5s  │→   │ │
│  │  └────────────────────────────────────┘    │ │
│  │                                            │ │
│  │  [加载更多...]                             │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │  操作                                      │ │
│  │  [编辑任务]  [暂停]  [删除]                │ │
│  └────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

#### 执行历史行样式

```css
.jx-auto-runRow {
  display: grid;
  grid-template-columns: 48px 60px 1fr 60px 32px;  /* # | status | time | duration | arrow */
  align-items: center;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s;
}
.jx-auto-runRow:hover {
  background: var(--color-fill-hover);
}
.jx-auto-runRow-num {
  font-size: 12px;
  color: var(--color-text-tertiary);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.jx-auto-runRow-status {
  font-size: 12px;
  font-weight: 500;
}
.jx-auto-runRow-status--success { color: var(--color-success); }
.jx-auto-runRow-status--failed  { color: var(--color-error); }
.jx-auto-runRow-status--timeout { color: var(--color-warning); }
.jx-auto-runRow-status--running { color: var(--color-primary); }

/* 失败行展开错误信息 */
.jx-auto-runRow-error {
  grid-column: 2 / -1;
  font-size: 11px;
  color: var(--color-error);
  margin-top: 4px;
  padding: 6px 8px;
  background: rgba(252,93,93,0.05);
  border-radius: 4px;
}
```

#### 点击执行记录 → 查看结果

点击某行打开 `RunResultModal`，展示：
1. Agent 输出的 Markdown 内容 (使用 `react-markdown` 渲染)
2. 执行元信息 (耗时、token 用量)
3. **"在我的空间中查看"** 按钮 (若有 artifact_id)

---

### 5.7 通知铃铛系统 (NotificationBell)

文件: `src/frontend/src/components/common/NotificationBell.tsx`

#### 放置位置

放在 `App.tsx` 中 sidebar 展开按钮旁或 header 右侧。具体方案：
- 当侧边栏展开时: 铃铛位于侧边栏底部用户头像行左侧
- 当侧边栏收起时: 铃铛悬浮在左上角展开按钮下方

推荐方案: **放在侧边栏底部 footer 用户头像行中**，与用户下拉菜单同级。

修改 `Sidebar.tsx` 的 footer 区域：

```
┌─────────────────────────────────┐
│  ...sidebar content...          │
│                                 │
│  ┌─────────────────────────┐   │
│  │  [🔔 3]  👤 张三  [▼]  │   │  ← footer 行
│  └─────────────────────────┘   │
└─────────────────────────────────┘
```

#### 铃铛 UI 设计

```css
/* 铃铛按钮 */
.jx-notifBell {
  position: relative;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s;
  border: none;
  background: none;
  color: var(--color-fill-heavy);
  font-size: 18px;
}
.jx-notifBell:hover {
  background: var(--color-fill-hover);
  color: var(--color-text);
}

/* 角标 (红点/数字) */
.jx-notifBell-badge {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: var(--color-error);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  line-height: 1;
  box-shadow: 0 0 0 2px #F7F7F7;  /* 与侧边栏背景色匹配的白圈 */
}
/* 数量 > 99 显示 99+ */
.jx-notifBell-badge--overflow {
  font-size: 9px;
}

/* 新通知到达时的 shake 动画 */
@keyframes jx-notifBell-shake {
  0%   { transform: rotate(0); }
  15%  { transform: rotate(12deg); }
  30%  { transform: rotate(-10deg); }
  45%  { transform: rotate(8deg); }
  60%  { transform: rotate(-6deg); }
  75%  { transform: rotate(3deg); }
  100% { transform: rotate(0); }
}
.jx-notifBell--shaking {
  animation: jx-notifBell-shake 0.6s ease-in-out;
}
```

#### 通知弹出层 (Popover)

使用 Ant Design `Popover`，placement `topRight`：

```css
.jx-notifPopover {
  width: 360px;
  max-height: 480px;
}
.jx-notifPopover .ant-popover-inner {
  padding: 0;
  border-radius: 12px;
  box-shadow: var(--shadow-dropdown);
}

/* 通知列表头 */
.jx-notifPopover-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--color-border);
}
.jx-notifPopover-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
}
.jx-notifPopover-readAll {
  font-size: 12px;
  color: var(--color-primary);
  cursor: pointer;
}

/* 通知列表 */
.jx-notifPopover-list {
  max-height: 380px;
  overflow-y: auto;
  padding: 8px 0;
}

/* 单条通知 */
.jx-notifPopover-item {
  display: flex;
  gap: 12px;
  padding: 12px 16px;
  cursor: pointer;
  transition: background 0.12s;
  position: relative;
}
.jx-notifPopover-item:hover {
  background: var(--color-fill-hover);
}
.jx-notifPopover-item--unread {
  background: rgba(18,109,255,0.03);
}
.jx-notifPopover-item--unread::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
}

/* 通知图标 */
.jx-notifPopover-itemIcon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 16px;
}
.jx-notifPopover-itemIcon--success {
  background: rgba(2,181,137,0.1);
  color: var(--color-success);
}
.jx-notifPopover-itemIcon--error {
  background: rgba(252,93,93,0.1);
  color: var(--color-error);
}

/* 通知内容 */
.jx-notifPopover-itemContent {
  flex: 1;
  min-width: 0;
}
.jx-notifPopover-itemTitle {
  font-size: 13px;
  color: var(--color-text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jx-notifPopover-itemBody {
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-top: 2px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.jx-notifPopover-itemTime {
  font-size: 11px;
  color: var(--color-text-placeholder);
  margin-top: 4px;
}

/* 空状态 */
.jx-notifPopover-empty {
  padding: 40px 16px;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 13px;
}
.jx-notifPopover-emptyIcon {
  font-size: 40px;
  opacity: 0.3;
  margin-bottom: 8px;
}

/* 底部 */
.jx-notifPopover-footer {
  padding: 10px 16px;
  border-top: 1px solid var(--color-border);
  text-align: center;
}
.jx-notifPopover-footer a {
  font-size: 12px;
  color: var(--color-primary);
}
```

#### SSE 连接管理

```typescript
// 在 automationStore 中实现
connectNotifSSE() {
  const apiUrl = getApiUrl();
  const es = new EventSource(`${apiUrl}/v1/notifications/stream`, {
    withCredentials: true,
  });

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    set((s) => ({
      unreadCount: s.unreadCount + 1,
      notifications: [
        { ...data, is_read: false, created_at: new Date().toISOString() },
        ...s.notifications,
      ].slice(0, 50),
    }));

    // 触发铃铛 shake 动画
    // 显示 Ant Design 全局通知
    notification[data.type === 'task_complete' ? 'success' : 'error']({
      message: data.title,
      description: '点击查看详情',
      placement: 'bottomRight',
      duration: 5,
      onClick: () => { /* 导航到任务详情 */ },
    });
  };

  es.onerror = () => {
    // 断线重连: 关闭当前 SSE，启动 60s 轮询
    es.close();
    const pollId = setInterval(() => get().fetchUnreadCount(), 60_000);
    set({ notifSSE: null, pollIntervalId: pollId });

    // 30s 后尝试重新建立 SSE
    setTimeout(() => get().connectNotifSSE(), 30_000);
  };

  set({ notifSSE: es });
},
```

#### Toast 通知样式

当 SSE 推送到达时，使用 `antd notification` 显示全局 Toast：

```
┌─────────────────────────────────────┐
│  ✅ 定时任务「每日新闻摘要」执行完成 │
│  点击查看详情                       │
│                              刚刚   │
└─────────────────────────────────────┘
```

成功用 `notification.success`，失败用 `notification.error`。
placement 为 `bottomRight`，duration 5s，可点击跳转。

---

### 5.8 空状态设计

当用户首次进入自动化面板，没有任何任务时：

```css
.jx-auto-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 24px;
  text-align: center;
}
.jx-auto-empty-icon {
  width: 120px;
  height: 120px;
  margin-bottom: 24px;
  /* 大型时钟+闪电 SVG 插画，柔和的蓝灰色 */
  opacity: 0.6;
}
.jx-auto-empty-title {
  font-size: var(--font-size-md);
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 8px;
}
.jx-auto-empty-desc {
  font-size: var(--font-size-sm);
  color: var(--color-text-tertiary);
  max-width: 320px;
  line-height: 1.6;
  margin-bottom: 24px;
}
.jx-auto-empty-action {
  /* 同 .jx-auto-createBtn 样式 */
}
```

显示文案：
- 标题: "还没有自动化任务"
- 描述: "创建你的第一个定时任务，AI 将按照你设定的时间自动执行，结果会保存到你的空间中"
- 按钮: "+ 创建任务"

---

### 5.9 自动化结果传送到"我的空间"的完整数据流

这是本功能的关键链路 —— 定时任务执行完成后，结果如何出现在用户的"我的空间"中。

#### 5.9.1 后端数据写入链路

```
execute_scheduled_task(task_id)
  │
  ├─ 1. Agent 执行完毕，获得 output_text (Markdown 格式)
  │
  ├─ 2. 上传到 Storage (local/S3/OSS)
  │     key = f"scheduled_tasks/{task_id}/{run_id}.md"
  │     storage.upload_bytes(key, output_text.encode("utf-8"))
  │
  ├─ 3. 创建 Artifact 记录
  │     ArtifactService.create_artifact(
  │       user_id     = task.user_id,
  │       artifact_type = "document",
  │       title       = f"[自动化] {task.title} - {timestamp}",
  │       filename    = f"auto_{run_id}.md",
  │       size_bytes  = len(output_text),
  │       mime_type   = "text/markdown",
  │       storage_key = key,
  │       chat_id     = None,  # 无关联对话
  │     )
  │     → artifact_id 写入 ScheduledTaskRun.artifact_id
  │
  ├─ 4. Artifact.metadata 标记来源:
  │     {
  │       "source": "scheduled_task",
  │       "task_id": "schtask_xxx",
  │       "task_title": "每日新闻摘要",
  │       "run_id": "run_xxx",
  │       "schedule_type": "cron",
  │       "cron_expr": "0 9 * * 1-5"
  │     }
  │
  └─ 5. Notification 中携带 artifact_id:
        {
          "type": "task_complete",
          "title": "定时任务「每日新闻摘要」执行完成",
          "ref_type": "scheduled_task_run",
          "ref_id": "run_xxx",
          "metadata": { "artifact_id": "artifact_xxx" }
        }
```

#### 5.9.2 前端数据消费链路

```
SSE 推送到达
  │
  ├─ 1. automationStore 接收 notification 事件
  │     → unreadCount++
  │     → notifications 数组 prepend
  │
  ├─ 2. antd notification.success() 显示 Toast
  │     "定时任务「每日新闻摘要」执行完成"
  │     "点击查看详情"
  │
  ├─ 3. 用户点击 Toast 或 通知铃铛中的通知项
  │     │
  │     ├─ 路径A: 打开 TaskDetailDrawer → 展示最新 Run 结果
  │     │         └─ RunResultModal 中显示 "在我的空间查看" 按钮
  │     │
  │     └─ 路径B: 直接导航到我的空间
  │               useCatalogStore.setPanel('my_space')
  │               → MySpacePanel 自动 refresh
  │               → 最新 Artifact 出现在列表顶部
  │
  └─ 4. 我的空间中的展示
        MySpacePanel → fetchResources()
        → GET /v1/artifacts?page=1&page_size=20
        → 返回包含 source:"scheduled_task" 的 Artifact
        → ResourceCard 根据 source 字段渲染特殊标识
```

#### 5.9.3 我的空间 ResourceCard 增强

修改 `src/frontend/src/components/myspace/ResourceCard.tsx`，在文件名旁边增加来源标识：

```
┌─────┬──────────────────────────────────────┬─────────┬──────────────┬────────┐
│ ☐   │ ⚡ [自动化] 每日新闻摘要 - 04/09    │ AI 生成 │ 2026-04-09  │ ⋮      │
│     │    来自定时任务 · 工作日 09:00       │         │              │        │
└─────┴──────────────────────────────────────┴─────────┴──────────────┴────────┘
```

样式增强：

```css
/* 自动化来源标记 */
.jx-mySpace-sourceTag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--color-icon-purple);
  background: rgba(118,85,250,0.06);
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 6px;
}
.jx-mySpace-sourceTag-icon {
  font-size: 10px;
}

/* 来源描述行 */
.jx-mySpace-sourceDesc {
  font-size: 11px;
  color: var(--color-text-placeholder);
  margin-top: 2px;
}
```

逻辑：
```typescript
// ResourceCard.tsx 中判断来源
const isFromAutomation = item.metadata?.source === 'scheduled_task';
const cronDesc = isFromAutomation ? item.metadata?.cron_expr : null;

// 渲染
{isFromAutomation && (
  <span className="jx-mySpace-sourceTag">
    <ThunderboltOutlined className="jx-mySpace-sourceTag-icon" />
    自动化
  </span>
)}
{isFromAutomation && cronDesc && (
  <div className="jx-mySpace-sourceDesc">
    来自定时任务 · {cronToHumanReadable(cronDesc)}
  </div>
)}
```

#### 5.9.4 我的空间筛选增强

在 MySpacePanel 的 source filter 下拉中增加选项：

```typescript
const SOURCE_OPTIONS = [
  { label: '全部来源', value: 'all' },
  { label: '用户上传', value: 'user_upload' },
  { label: 'AI 生成',  value: 'ai_generated' },
  { label: '自动化任务', value: 'scheduled_task' },  // ← 新增
];
```

后端 `GET /v1/artifacts` 接口需要支持 `source` 查询参数。

---

### 5.10 Zustand Store 完整设计

文件: `src/frontend/src/stores/automationStore.ts`

```typescript
import { create } from 'zustand';
import { notification } from 'antd';
import type { ScheduledTask, TaskRun, NotificationItem } from '../types';
import * as api from '../api';

const PAGE_SIZE = 20;

interface AutomationState {
  // ── 任务列表 ──
  tasks: ScheduledTask[];
  tasksLoading: boolean;
  page: number;
  total: number;
  hasMore: boolean;
  statusFilter: string;            // 'all' | 'active' | 'paused' | 'completed'
  searchKeyword: string;

  // ── 统计 ──
  stats: {
    active: number;
    paused: number;
    completed: number;
    failed: number;                 // last_run failed 的任务数
  };

  // ── 选中/详情 ──
  selectedTask: ScheduledTask | null;
  drawerOpen: boolean;
  runs: TaskRun[];
  runsLoading: boolean;

  // ── 创建/编辑 ──
  createModalOpen: boolean;
  editingTask: ScheduledTask | null;  // null=创建, 有值=编辑
  creating: boolean;

  // ── 通知 ──
  notifications: NotificationItem[];
  unreadCount: number;
  notifSSE: EventSource | null;
  notifPopoverOpen: boolean;

  // ── 任务 Actions ──
  fetchTasks: (reset?: boolean) => Promise<void>;
  loadMore: () => Promise<void>;
  setStatusFilter: (status: string) => void;
  setSearchKeyword: (kw: string) => void;
  fetchStats: () => Promise<void>;

  createTask: (data: any) => Promise<void>;
  updateTask: (taskId: string, data: any) => Promise<void>;
  pauseTask: (taskId: string) => Promise<void>;
  resumeTask: (taskId: string) => Promise<void>;
  cancelTask: (taskId: string) => Promise<void>;
  deleteTask: (taskId: string) => Promise<void>;
  runNow: (taskId: string) => Promise<void>;

  selectTask: (task: ScheduledTask) => void;
  closeDrawer: () => void;
  fetchRuns: (taskId: string) => Promise<void>;

  openCreateModal: (editTask?: ScheduledTask) => void;
  closeCreateModal: () => void;

  // ── 通知 Actions ──
  connectNotifSSE: () => void;
  disconnectNotifSSE: () => void;
  fetchUnreadCount: () => Promise<void>;
  fetchNotifications: () => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
  setNotifPopoverOpen: (v: boolean) => void;
}

export const useAutomationStore = create<AutomationState>((set, get) => ({
  // ... 初始值省略

  fetchTasks: async (reset = false) => {
    const { statusFilter, searchKeyword, page, tasks } = get();
    const currentPage = reset ? 1 : page;
    set({ tasksLoading: true });
    try {
      const res = await api.getScheduledTasks({
        status: statusFilter === 'all' ? undefined : statusFilter,
        keyword: searchKeyword || undefined,
        page: currentPage,
        page_size: PAGE_SIZE,
      });
      const items = res.items || [];
      set({
        tasks: reset ? items : [...tasks, ...items],
        total: res.total,
        page: currentPage,
        hasMore: res.has_more,
      });
    } finally {
      set({ tasksLoading: false });
    }
  },

  // ... 其他 actions 按此模式实现
}));
```

---

### 5.11 App.tsx 集成

修改 `src/frontend/src/App.tsx`：

```typescript
// 1. 新增 import
import { AutomationPanel } from './components/automation';

// 2. header title 和 hint 扩展 (约 170-190 行)
const title = panel === 'automation' ? '自动化' : /* ... 现有逻辑 */;
const hint = panel === 'automation' ? '创建定时任务，AI 将在指定时间自动执行并将结果保存到你的空间'
  : /* ... 现有逻辑 */;

// 3. showHeader 排除 (约 193 行)
const showHeader = panel !== 'chat'
  && panel !== 'settings'
  && /* ... 现有排除 */
  && panel !== 'automation';   // 自动化面板自带 header

// 4. panel 渲染 (约 285-294 行)
{panel === 'automation' && <AutomationPanel />}
```

---

### 5.12 CSS 文件注册

新建: `src/frontend/src/styles/automation.css`

在 `src/frontend/src/styles/index.ts` 中添加:

```typescript
import './automation.css';
```

---

### 5.13 Store 导出注册

修改 `src/frontend/src/stores/index.ts`：

```typescript
export { useAutomationStore } from './automationStore';
```

---

### 5.14 完整文件清单 (前端)

| # | 文件路径 | 类型 | 说明 |
|---|----------|------|------|
| 1 | `src/frontend/src/types.ts` | 修改 | PanelKey + 3 个接口 |
| 2 | `src/frontend/src/api.ts` | 修改 | 14 个 API 函数 |
| 3 | `src/frontend/src/stores/automationStore.ts` | 新建 | Zustand store (~250行) |
| 4 | `src/frontend/src/stores/index.ts` | 修改 | export automationStore |
| 5 | `src/frontend/src/styles/automation.css` | 新建 | 全部样式 (~400行) |
| 6 | `src/frontend/src/styles/index.ts` | 修改 | import automation.css |
| 7 | `src/frontend/src/components/automation/AutomationPanel.tsx` | 新建 | 主面板 (~280行) |
| 8 | `src/frontend/src/components/automation/TaskCard.tsx` | 新建 | 任务卡片 (~180行) |
| 9 | `src/frontend/src/components/automation/TaskCreateModal.tsx` | 新建 | 创建对话框 (~320行) |
| 10 | `src/frontend/src/components/automation/TaskDetailDrawer.tsx` | 新建 | 详情抽屉 (~250行) |
| 11 | `src/frontend/src/components/automation/CronBuilder.tsx` | 新建 | Cron 构建器 (~200行) |
| 12 | `src/frontend/src/components/automation/RunResultModal.tsx` | 新建 | 结果查看 (~120行) |
| 13 | `src/frontend/src/components/automation/index.ts` | 新建 | barrel export |
| 14 | `src/frontend/src/components/common/NotificationBell.tsx` | 新建 | 通知铃铛 (~180行) |
| 15 | `src/frontend/src/components/sidebar/Sidebar.tsx` | 修改 | NAV_ITEMS 新增 |
| 16 | `src/frontend/src/App.tsx` | 修改 | panel 渲染 + import |
| 17 | `src/frontend/src/components/myspace/ResourceCard.tsx` | 修改 | 自动化来源标识 |
| 18 | `src/frontend/src/components/myspace/MySpacePanel.tsx` | 修改 | source filter 选项 |
| 19 | `public/home/自动化.svg` | 新建 | 侧边栏图标 |

#### 依赖安装

```bash
cd src/frontend
npm install cron-parser    # Cron 表达式解析 (预览下次执行时间)
```

---

### 5.15 交互细节总结

| 交互场景 | 行为 |
|----------|------|
| 首次进入面板 | 空状态插画 + "创建任务"按钮 |
| 点击"新建任务" | 打开 TaskCreateModal (640px Modal) |
| 选择"周期执行" | 展示 CronBuilder 组件 (预设 pills + 自定义输入) |
| 输入 Cron 表达式 | 实时预览下 5 次执行时间 |
| 展开"高级设置" | 折叠面板，展示 Agent/MCP/KB/超时配置 |
| 提交创建 | 乐观更新列表 + Loading 状态 + 成功 Toast |
| 点击任务卡片 | 打开 TaskDetailDrawer (520px 右侧抽屉) |
| 抽屉中点击执行记录 | 打开 RunResultModal (Markdown 渲染) |
| RunResultModal 中"在我的空间查看" | 关闭 Modal → setPanel('my_space') |
| 任务完成 SSE 推送 | 铃铛 shake + 角标+1 + Toast 通知 (5s) |
| 点击 Toast | 打开 TaskDetailDrawer 到对应任务 |
| 点击铃铛 | Popover 展示通知列表 (最近 50 条) |
| "全部已读" | 清除角标 + 标记所有 is_read=true |
| 点击通知项 | 关闭 Popover → 打开对应任务的 DetailDrawer |
| 暂停任务 | 卡片状态 Tag 变为"已暂停" (黄色) + 下次执行时间隐藏 |
| 恢复任务 | 卡片状态恢复"活跃" (蓝色) + 下次执行时间重新计算 |
| 删除任务 | Popconfirm → 卡片 slide-out 动画 (0.4s) |
| 立即执行 | Button loading → 成功 Toast "已触发执行" |
| 统计卡片点击 | 筛选对应状态的任务列表 |
| 搜索 | 300ms debounce → 重新拉取列表 |
| 我的空间 source filter "自动化任务" | 仅展示来自定时任务的 Artifact |
| 我的空间中自动化 Artifact | 显示紫色 ⚡自动化 标签 + cron 描述 |

---

## 6. 文件清单

### 新建文件（后端 8 个）

| # | 文件路径 | 说明 |
|---|----------|------|
| 1 | `src/backend/core/scheduler/__init__.py` | 包初始化 |
| 2 | `src/backend/core/scheduler/scheduler.py` | APScheduler 生命周期管理 |
| 3 | `src/backend/core/scheduler/executor.py` | 后台 Agent 执行核心 |
| 4 | `src/backend/core/services/scheduled_task_service.py` | 任务 CRUD 服务 |
| 5 | `src/backend/core/services/notification_service.py` | 通知 CRUD 服务 |
| 6 | `src/backend/api/routes/v1/scheduled_tasks.py` | REST 端点 |
| 7 | `src/backend/api/routes/v1/notifications.py` | REST + SSE 端点 |
| 8 | `src/backend/alembic/versions/xxx_add_scheduled_tasks.py` | 数据库迁移 |

### 新建文件（前端 8 个）

| # | 文件路径 | 说明 |
|---|----------|------|
| 9 | `src/frontend/src/stores/scheduledTaskStore.ts` | Zustand store |
| 10 | `src/frontend/src/components/scheduled-tasks/ScheduledTasksPanel.tsx` | 主面板 |
| 11 | `src/frontend/src/components/scheduled-tasks/TaskCreateModal.tsx` | 创建对话框 |
| 12 | `src/frontend/src/components/scheduled-tasks/TaskCard.tsx` | 任务卡片 |
| 13 | `src/frontend/src/components/scheduled-tasks/TaskDetailDrawer.tsx` | 详情抽屉 |
| 14 | `src/frontend/src/components/scheduled-tasks/RunResultModal.tsx` | 结果查看 |
| 15 | `src/frontend/src/components/scheduled-tasks/CronExpressionInput.tsx` | Cron 输入 |
| 16 | `src/frontend/src/components/common/NotificationBell.tsx` | 通知铃铛 |

### 修改文件（8 个）

| # | 文件路径 | 变更 |
|---|----------|------|
| 17 | `src/backend/core/db/models.py` | 新增 ScheduledTask, ScheduledTaskRun, Notification 模型 |
| 18 | `src/backend/api/app.py` | startup/shutdown 中启动/关闭调度器 |
| 19 | `src/backend/api/routes/v1/__init__.py` | import 新路由 |
| 20 | `src/backend/api/routes/__init__.py` | re-export 新路由 |
| 21 | `requirements.txt` | 添加 `apscheduler>=3.10.0` |
| 22 | `src/frontend/src/types.ts` | 新增 TS 类型 |
| 23 | `src/frontend/src/api.ts` | 新增 API 函数 |
| 24 | `src/frontend/src/components/sidebar/Sidebar.tsx` | 添加导航入口 |

---

## 7. 实施顺序

| 阶段 | 内容 | 预期产出 |
|------|------|----------|
| **Phase 1** | DB 模型 + Alembic 迁移 | 3 张新表可用 |
| **Phase 2** | 后端服务层 (ScheduledTaskService + NotificationService) | 业务逻辑 CRUD |
| **Phase 3** | 调度器 + 执行器 (APScheduler + executor) | 后台 Agent 可执行 |
| **Phase 4** | API 端点 + 路由注册 | 接口可 curl 调用 |
| **Phase 5** | 前端（类型 → API → Store → 组件 → 侧边栏 → 通知铃铛） | 完整 UI 可交互 |

---

## 8. 验证方案

### 单元测试
```bash
PYTHONPATH=src/backend pytest src/backend/tests/test_scheduled_task_service.py -v
```

### API 集成测试
```bash
# 创建一次性任务（1分钟后执行）
curl -X POST http://localhost:3000/api/v1/scheduled-tasks \
  -H "Content-Type: application/json" \
  -H "Cookie: session=xxx" \
  -d '{
    "title": "测试任务",
    "prompt": "请总结今天的新闻",
    "schedule_type": "once",
    "run_at": "2026-04-09T10:30:00+08:00"
  }'

# 查看任务列表
curl http://localhost:3000/api/v1/scheduled-tasks -H "Cookie: session=xxx"

# 手动触发
curl -X POST http://localhost:3000/api/v1/scheduled-tasks/{task_id}/run-now \
  -H "Cookie: session=xxx"

# 查看执行记录
curl http://localhost:3000/api/v1/scheduled-tasks/{task_id}/runs -H "Cookie: session=xxx"

# SSE 通知流
curl -N http://localhost:3000/api/v1/notifications/stream -H "Cookie: session=xxx"
```

### E2E 验证
1. 创建一次性任务（1 分钟后）
2. 等待触发 → 检查 `scheduled_task_runs` 表有 `status=success` 记录
3. 检查 `artifacts` 表有新记录（带 `source: "scheduled_task"` metadata）
4. 检查 `notifications` 表有通知
5. 前端 SSE 收到推送，显示 Toast
6. 我的空间显示新的 Artifact

### 边界情况
- **超时**: timeout_seconds 设为 10s，发送耗时任务 → 验证 `status=timeout`
- **Agent 异常**: 构造会触发错误的 prompt → 验证 `status=failed` + error_message
- **服务重启**: 创建 cron 任务 → 重启 backend → 验证 APScheduler 自动恢复 Job
- **暂停/恢复**: pause → 验证 APScheduler 无 Job → resume → 验证 Job 恢复
- **并发保护**: 快速连续 run-now → 验证 max_instances=1 生效

---

## 9. 注意事项与后续扩展

### 当前版本限制
- Agent 执行不支持流式输出预览（后台执行，仅收集最终结果）
- 通知仅推送到 Web 端（无手机推送）
- 无任务执行进度百分比（Agent 迭代轮次可作为粗略进度）

### 后续扩展方向
- **执行日志实时流**: 通过 Redis 存储中间结果，SSE 推送执行进度
- **任务模板**: 预设常用任务模板（日报生成、数据分析、邮件摘要等）
- **审批流**: 敏感任务执行前需用户确认
- **成本控制**: 按用户限制 token 消耗上限
- **Webhook 通知**: 支持企业微信/钉钉/邮件通知
- **多 Agent 编排**: 单个定时任务内编排多个 Agent 协作
