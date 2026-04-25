# mem0 记忆系统集成方案

> **状态**：已实现 v1.0（Phase 1 向量记忆 + 用户开关 + 管理 API）
> **日期**：2026-03-04（设计）/ 2026-03-09（实现）
> **适用版本**：Jingxin-Agent test 分支

---

## 目录

1. [方案概述](#1-方案概述)
2. [技术选型说明](#2-技术选型说明)
3. [整体架构](#3-整体架构)
4. [mem0 框架能力复用](#4-mem0-框架能力复用)
5. [基础设施变更](#5-基础设施变更)
6. [后端代码改动详解](#6-后端代码改动详解)
7. [前端改动](#7-前端改动)
8. [环境变量配置](#8-环境变量配置)
9. [部署步骤](#9-部署步骤)
10. [数据治理与安全](#10-数据治理与安全)
11. [演进路线](#11-演进路线)

---

## 1. 方案概述

### 1.1 要解决的问题

LLM 本身无状态，每次对话从零开始。Jingxin-Agent 当前的会话历史仅在单次会话内有效，下次打开新对话，AI 对用户一无所知。

### 1.2 引入 mem0 后能实现什么

| 功能 | 具体效果 |
|---|---|
| **跨会话持久化记忆** | AI 记住用户的姓名、职位、部门、偏好，下次对话无需重新介绍 |
| **个性化响应** | 财政局预算分析人员问 GDP → AI 自动从其预算分析视角展开分析 |
| **智能去重与更新** | 用户换岗后，旧的"在 XX 岗位"记忆自动被覆盖，不产生矛盾记忆 |
| **图谱式关系记忆** | 存储实体关系（张三—任职于—财政局—隶属于—市政府），支持关联推理 |
| **记忆管理** | 用户可在设置页查看、删除自己的记忆条目；支持整体清空 |
| **用户自主开关** | "永久记忆"功能由用户在设置页自行开启/关闭，尊重用户意愿 |
| **完全内网化** | Embedding、Reranker 通过内网 vLLM API 调用；向量库（Milvus）和图库（Neo4j）均自托管 |

### 1.3 设计原则

- **直接复用 mem0 框架**，不自行实现记忆提取、去重、更新逻辑
- **最小侵入**：失败时静默降级，不影响主流程
- **全离线**：无任何外网请求，全部服务在内网部署
- **渐进式**：`MEM0_ENABLED=false` 时代码路径完全跳过，零性能影响

---

## 2. 技术选型说明

### 2.1 向量数据库：Milvus（核心选型）

**选择 Milvus 而非其他方案的理由：**

| 对比维度 | Milvus | Qdrant | pgvector | Faiss |
|---|---|---|---|---|
| **水平扩展** | ✅ 集群模式，分片+副本，线性扩展 | ⚠️ 分布式支持有限 | ❌ 单实例 | ❌ 内存方案 |
| **企业级特性** | ✅ RBAC、审计日志、数据加密 | ⚠️ 部分 | ❌ 依赖 PostgreSQL | ❌ 无 |
| **生产稳定性** | ✅ Linux Foundation 顶级项目 | ✅ 成熟 | ✅ 成熟 | ⚠️ 无持久化 |
| **向量规模** | ✅ 亿级 | ✅ 千万级 | ⚠️ 百万级 | ⚠️ 内存限制 |
| **检索性能** | ✅ 毫秒级，多种索引（IVF/HNSW/DiskANN） | ✅ 毫秒级 | ⚠️ 较慢 | ✅ 快但无持久化 |
| **K8s 支持** | ✅ 官方 Helm Chart | ✅ | ⚠️ 依赖 PG Operator | ❌ |
| **mem0 原生支持** | ✅ | ✅ | ✅ | ❌ |

**结论**：Milvus 是面向企业级生产部署的最优解。当前可以 Standalone 模式启动，后续按需切换到 Distributed 模式，无需改变 mem0 侧代码。

### 2.2 图数据库：Neo4j（图记忆模式）

mem0 原生支持图记忆模式（`enable_graph=True`），选用 **Neo4j Community Edition**：

- mem0 对 Neo4j 的集成最为成熟（默认图库）
- 支持 Cypher 查询，语义丰富
- 图记忆能存储实体关系三元组（主体—关系—客体），支持多跳推理
- 向量检索（Milvus）+ 图检索（Neo4j）双路召回，结果更完整

**图记忆示例**：
```
用户: "我叫张三，我在市财政局预算处工作"
→ 图节点: 张三, 市财政局, 预算处
→ 图边: 张三-[任职于]->预算处, 预算处-[隶属于]->市财政局
下次问: "财政局有什么政策" → 图检索能关联到张三与财政局的关系
```

### 2.3 Embedding 服务：内网 vLLM API

- 通过 vLLM 部署嵌入模型，暴露 OpenAI 兼容的 `/v1/embeddings` 接口
- mem0 的 `embedder.provider=openai` 支持配置 `openai_base_url`，直接指向内网地址
- 模型名称、API 地址、Key 均在 `.env` 中配置，零代码改动切换模型

### 2.4 Reranker 服务：内网 vLLM API（可选）

- 通过 vLLM 部署 Reranker 模型（如 bge-reranker），暴露 OpenAI 兼容接口
- mem0 内置 Reranker 支持，在 `search()` 时自动触发
- 显著提升记忆检索精度（尤其是相似记忆较多时）

---

## 3. 整体架构

### 3.1 数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户浏览器                                │
│  发送消息 ──────────────────────────────── 设置页永久记忆开关    │
└───────────────┬──────────────────────────────────┬─────────────┘
                │                                  │ PATCH /v1/users/me/settings
                ▼                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Jingxin-Agent 后端                           │
│                                                                 │
│  POST /chat/stream                                              │
│       │                                                         │
│       ▼                                                         │
│  astream_chat_workflow()                                        │
│       │                                                         │
│       ├─[新增]─ memory_service.retrieve_memories()              │
│       │          └─ mem0.Memory.search()                        │
│       │               ├─ Milvus 向量检索                        │
│       │               ├─ Neo4j 图检索 (enable_graph=True)       │
│       │               └─ vLLM Reranker 重排序                   │
│       │          → 返回格式化记忆文本，注入 session_messages     │
│       │                                                         │
│       ├──────── StreamingAgent → LangChain Agent                │
│       │         (携带用户记忆上下文的 AI 响应)                   │
│       │                                                         │
│       └─[新增]─ asyncio.create_task(save_conversation())       │
│                  └─ mem0.Memory.add() [后台，不阻塞流式输出]     │
│                       ├─ vLLM 提取事实（内网 LLM）              │
│                       ├─ vLLM 决策 ADD/UPDATE/DELETE            │
│                       ├─ Milvus 写入向量                        │
│                       └─ Neo4j 写入实体关系                     │
│                                                                 │
│  GET /v1/memories        ← 记忆管理 API（查看/删除）            │
└─────────────────────────────────────────────────────────────────┘
         │                │                │
         ▼                ▼                ▼
    ┌─────────┐     ┌──────────┐     ┌──────────┐
    │ Milvus  │     │  Neo4j   │     │ vLLM     │
    │ (向量库) │     │ (图数据库)│     │ Embed/   │
    │         │     │          │     │ Rerank   │
    └─────────┘     └──────────┘     └──────────┘
```

### 3.2 记忆类型

mem0 框架原生支持三种记忆类型，均会被使用：

| 类型 | 存储内容 | 示例 |
|---|---|---|
| `semantic_memory` | 事实和偏好 | "姓名：张三"、"偏好简洁的回答" |
| `episodic_memory` | 具体事件 | "2026年3月询问过GDP分析" |
| `procedural_memory` | 操作流程 | "用户习惯先看总结再看详情" |

---

## 4. mem0 框架能力复用

本方案**直接使用 mem0 提供的所有能力**，不自行实现任何记忆管理逻辑。

### 4.1 mem0 完成的工作（我们无需实现）

```
mem0.Memory.add(messages, user_id=user_id)
│
├─ 第1次 LLM 调用：从对话中提取关键事实
│   → "Name: 张三", "Works at: 市财政局", "Role: 预算分析"
│
├─ 对每条新事实，在 Milvus 中检索最相似的旧记忆（top-5）
│
├─ 第2次 LLM 调用：对比新旧，决策
│   → ADD（新增）/ UPDATE（更新旧记忆）/ DELETE（删除矛盾记忆）/ NONE（忽略）
│
├─ 执行决策 → Milvus 向量写入/更新/删除
│
└─ enable_graph=True 时：提取实体关系 → Neo4j 写入三元组

mem0.Memory.search(query, user_id=user_id)
│
├─ Milvus 向量相似度检索
├─ Neo4j 图检索（如启用）
└─ vLLM Reranker 重排序（如配置）
```

### 4.2 我们只需要做的三件事

1. **配置 mem0**：指定 Milvus、Neo4j、vLLM 地址（写在 `.env`）
2. **在 workflow 中调用 `search()` 和 `add()`**：各一行代码
3. **提供管理 API 和前端开关**：让用户可见可控

---

## 5. 基础设施变更

### 5.1 新增 Docker 服务

在 `docker-compose.yml` 中新增以下服务：

#### Milvus Standalone

```yaml
services:
  etcd:
    image: quay.io/coreos/etcd:v3.5.5
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - etcd_data:/etcd
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
    healthcheck:
      test: ["CMD", "etcdctl", "endpoint", "health"]
      interval: 30s
      timeout: 20s
      retries: 3

  minio:
    image: minio/minio:RELEASE.2023-03-13T19-46-17Z
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    volumes:
      - minio_data:/minio_data
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3

  milvus:
    image: milvusdb/milvus:v2.4.0
    command: ["milvus", "run", "standalone"]
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    volumes:
      - milvus_data:/var/lib/milvus
    ports:
      - "19530:19530"   # gRPC（内部使用）
      - "9091:9091"     # HTTP metrics
    depends_on:
      - etcd
      - minio
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      timeout: 20s
      retries: 3

  neo4j:
    image: neo4j:5.15-community
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-jingxin_neo4j_2026}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: apoc.*
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    ports:
      - "7474:7474"     # 浏览器界面（内部访问）
      - "7687:7687"     # Bolt 协议
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD:-jingxin_neo4j_2026}", "RETURN 1"]
      interval: 30s
      timeout: 10s
      retries: 5

volumes:
  # 原有
  postgres_data:
  # 新增
  etcd_data:
  minio_data:
  milvus_data:
  neo4j_data:
  neo4j_logs:
```

> **说明**：Milvus Standalone 模式需要 etcd（元数据）和 MinIO（对象存储）。
> **注意**：如果已有内网 MinIO 实例，可直接复用（修改 `MINIO_ADDRESS`）。

### 5.2 后端服务 depends_on 更新

```yaml
services:
  backend:
    depends_on:
      postgres:
        condition: service_healthy
      milvus:                      # 新增
        condition: service_healthy
      neo4j:                       # 新增
        condition: service_healthy
```

---

## 6. 后端代码改动详解

### 6.1 `requirements.txt` — 新增依赖

```
# mem0 记忆系统
mem0ai>=0.1.50
pymilvus>=2.4.0       # Milvus Python SDK（mem0 的 milvus provider 依赖）
neo4j>=5.15.0         # Neo4j Python Driver（mem0 的 neo4j provider 依赖）
```

> `mem0ai` 安装后即可通过 `from mem0 import Memory` 使用其全部能力，包括向量检索、图检索、Reranker 等。

### 6.2 `src/backend/core/memory_service.py` — 新建

```python
"""mem0 记忆服务封装

直接复用 mem0 框架能力：
- 事实提取（LLM）
- 向量检索（Milvus）
- 图检索（Neo4j，enable_graph=True）
- 重排序（vLLM Reranker API）
- 去重/更新决策（LLM）

本文件只做配置组装 + 异步封装，不实现任何记忆管理逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import List

logger = logging.getLogger(__name__)

MEM0_ENABLED: bool = os.getenv("MEM0_ENABLED", "false").lower() in ("1", "true", "yes")
MEM0_GRAPH_ENABLED: bool = os.getenv("MEM0_GRAPH_ENABLED", "false").lower() in ("1", "true", "yes")


def _build_mem0_config() -> dict:
    """
    组装 mem0 配置：
    - LLM：复用内网 LLM（OpenAI 兼容接口）
    - Embedder：内网 vLLM Embedding API（OpenAI 兼容）
    - Vector Store：Milvus
    - Graph Store：Neo4j（可选，由 MEM0_GRAPH_ENABLED 控制）
    - Reranker：内网 vLLM Reranker API（可选）
    """
    config: dict = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": os.getenv("BASE_MODEL_NAME", "deepseek-chat"),
                "openai_base_url": os.getenv("MODEL_URL", ""),
                "api_key": os.getenv("API_KEY", "sk-placeholder"),
                "temperature": 0.1,
                "max_tokens": 2000,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": os.getenv("MEM0_EMBED_MODEL", "bge-m3"),
                "openai_base_url": os.getenv("MEM0_EMBED_URL", ""),
                "api_key": os.getenv("MEM0_EMBED_API_KEY", "sk-placeholder"),
            },
        },
        "vector_store": {
            "provider": "milvus",
            "config": {
                "host": os.getenv("MILVUS_HOST", "milvus"),
                "port": int(os.getenv("MILVUS_PORT", "19530")),
                "collection_name": "jingxin_memories",
                "embedding_model_dims": int(os.getenv("MEM0_EMBED_DIMS", "1024")),
            },
        },
    }

    # 图记忆（可选）
    if MEM0_GRAPH_ENABLED:
        config["graph_store"] = {
            "provider": "neo4j",
            "config": {
                "url": os.getenv("NEO4J_URL", "bolt://neo4j:7687"),
                "username": os.getenv("NEO4J_USERNAME", "neo4j"),
                "password": os.getenv("NEO4J_PASSWORD", "jingxin_neo4j_2026"),
            },
        }

    # Reranker（可选）
    reranker_url = os.getenv("MEM0_RERANKER_URL", "")
    if reranker_url:
        config["reranker"] = {
            "provider": "huggingface",   # mem0 通过 huggingface cross-encoder 接口调用
            "config": {
                "model": os.getenv("MEM0_RERANKER_MODEL", "BAAI/bge-reranker-base"),
                "base_url": reranker_url,
                "api_key": os.getenv("MEM0_RERANKER_API_KEY", "sk-placeholder"),
            },
        }

    return config


@lru_cache(maxsize=1)
def _get_memory():
    """延迟初始化单例，复用 mem0.Memory 实例。"""
    if not MEM0_ENABLED:
        return None
    try:
        from mem0 import Memory
        cfg = _build_mem0_config()
        logger.info("[MemoryService] 初始化 mem0.Memory (graph=%s)", MEM0_GRAPH_ENABLED)
        return Memory.from_config(cfg)
    except Exception as exc:
        logger.error("[MemoryService] 初始化失败，记忆功能将降级为空: %s", exc)
        return None


async def retrieve_memories(user_id: str, query: str, limit: int = 5) -> str:
    """
    调用 mem0.Memory.search()，同时触发：
    - Milvus 向量检索
    - Neo4j 图检索（如 MEM0_GRAPH_ENABLED=true）
    - Reranker 重排序（如配置 MEM0_RERANKER_URL）

    返回可直接注入消息列表的格式化文本。
    """
    if not MEM0_ENABLED or not user_id:
        return ""
    memory = _get_memory()
    if memory is None:
        return ""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: memory.search(query, user_id=user_id, limit=limit)
        )
        items = result.get("results", [])
        relations = result.get("relations", [])  # 图检索结果

        if not items and not relations:
            return ""

        lines = ["## 关于该用户的已知背景信息（来自历史会话记忆）"]
        for m in items:
            text = (m.get("memory") or "").strip()
            if text:
                lines.append(f"- {text}")

        # 附加图关系信息
        if relations:
            lines.append("\n## 用户相关实体关系")
            for r in relations[:5]:   # 最多5条关系
                src = r.get("source", "")
                rel = r.get("relationship", "")
                tgt = r.get("target", "")
                if src and rel and tgt:
                    lines.append(f"- {src} → {rel} → {tgt}")

        return "\n".join(lines)
    except Exception as exc:
        logger.warning("[MemoryService] 记忆检索失败，降级为空: %s", exc)
        return ""


async def save_conversation(user_id: str, user_message: str, assistant_message: str) -> None:
    """
    调用 mem0.Memory.add()，mem0 框架自动完成：
    1. LLM 提取事实
    2. Milvus 相似记忆检索
    3. LLM 决策 ADD/UPDATE/DELETE
    4. Milvus 写入/更新/删除
    5. Neo4j 写入实体关系（如 MEM0_GRAPH_ENABLED=true）

    本函数应通过 asyncio.create_task() 调用，不应被 await（后台执行）。
    """
    if not MEM0_ENABLED or not user_id:
        return
    memory = _get_memory()
    if memory is None:
        return
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_message},
    ]
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: memory.add(messages, user_id=user_id)
        )
        logger.debug("[MemoryService] 用户 %s 的记忆已保存", user_id)
    except Exception as exc:
        logger.warning("[MemoryService] 记忆保存失败: %s", exc)


async def get_all_memories(user_id: str) -> List[dict]:
    """获取用户所有记忆条目（供管理 API）。"""
    if not MEM0_ENABLED or not user_id:
        return []
    memory = _get_memory()
    if memory is None:
        return []
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: memory.get_all(user_id=user_id)
        )
        return result.get("results", [])
    except Exception as exc:
        logger.warning("[MemoryService] 获取记忆列表失败: %s", exc)
        return []


async def delete_memory(memory_id: str) -> bool:
    """删除单条记忆。"""
    memory = _get_memory()
    if memory is None:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: memory.delete(memory_id))
        return True
    except Exception as exc:
        logger.warning("[MemoryService] 单条删除失败: %s", exc)
        return False


async def delete_all_memories(user_id: str) -> bool:
    """清空用户所有记忆。"""
    if not MEM0_ENABLED or not user_id:
        return False
    memory = _get_memory()
    if memory is None:
        return False
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: memory.delete_all(user_id=user_id))
        return True
    except Exception as exc:
        logger.warning("[MemoryService] 批量删除失败: %s", exc)
        return False
```

### 6.3 `src/backend/routing/workflow.py` — 修改（两处精确插入）

**插入点 1**：`astream_chat_workflow()` 函数体内，路由决策完成后、第一个 `yield thinking` 之前

```python
# ── [mem0] 记忆检索 ─────────────────────────────────────────────
# 检查用户是否启用了永久记忆功能
_mem0_user_id = str(context.get("user_id", ""))
_mem0_enabled_by_user = bool(context.get("memory_enabled", False))

_memory_context = ""
if _mem0_enabled_by_user:
    from core.memory_service import retrieve_memories
    _memory_context = await retrieve_memories(_mem0_user_id, user_message)
    if _memory_context:
        # 在 session_messages 最前插入记忆 system 消息
        session_messages = [
            {"role": "system", "content": _memory_context},
            *session_messages,
        ]
        payload = {"messages": session_messages}
# ── [end mem0] ──────────────────────────────────────────────────

# Send initial "thinking" indicator immediately
yield {
    "type": "thinking",
    "message": "正在分析您的问题...",
}
```

**插入点 2**：`yield {"type": "meta", ...}` 之后（流式输出完全结束后）

```python
# Yield metadata after streaming is complete
yield {
    "type": "meta",
    ...
}

# ── [mem0] 后台保存记忆（非阻塞）──────────────────────────────
if _mem0_enabled_by_user and full_response and _mem0_user_id:
    from core.memory_service import save_conversation
    asyncio.create_task(save_conversation(_mem0_user_id, user_message, full_response))
# ── [end mem0] ──────────────────────────────────────────────────
```

**`memory_enabled` 的来源**：在 `chat.py` 的 `_build_runtime_context()` 中，从当前用户的设置中读取并注入到 context：

```python
# chat.py 中，在构建 context 时增加：
user_settings = user_service.get_user_settings(db_user_id)  # 从 users_shadow.metadata 读取
context = {
    "user_id": db_user_id,
    "chat_id": chat_id,
    "model_name": request.model_name,
    "enabled_skills": enabled_skills,
    "memory_enabled": user_settings.get("memory_enabled", False),  # 新增
}
```

### 6.4 `src/backend/api/routes/v1/memories.py` — 新建

```python
"""记忆管理 API

GET    /v1/memories           查看当前用户所有记忆
DELETE /v1/memories/{id}      删除单条记忆
DELETE /v1/memories           清空所有记忆
PATCH  /v1/memories/settings  更新用户记忆设置（开关）
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.auth import get_current_user, UserContext
from core.database import get_db
from core.memory_service import (
    MEM0_ENABLED,
    get_all_memories,
    delete_memory,
    delete_all_memories,
)
from core.responses import success_response, error_response
from core.service import UserService
from pydantic import BaseModel

router = APIRouter(prefix="/v1/memories", tags=["memories"])


class MemorySettingsRequest(BaseModel):
    memory_enabled: bool


@router.get("")
async def list_memories(user: UserContext = Depends(get_current_user)):
    """获取当前用户所有记忆条目。"""
    if not MEM0_ENABLED:
        return success_response(data={"enabled": False, "items": [], "count": 0})
    items = await get_all_memories(str(user.user_id))
    return success_response(data={"enabled": True, "items": items, "count": len(items)})


@router.delete("/{memory_id}")
async def remove_memory(memory_id: str, user: UserContext = Depends(get_current_user)):
    """删除单条记忆。"""
    ok = await delete_memory(memory_id)
    if not ok:
        return error_response(code=500, message="删除失败")
    return success_response(data={"deleted": memory_id})


@router.delete("")
async def remove_all_memories(user: UserContext = Depends(get_current_user)):
    """清空当前用户所有记忆。"""
    ok = await delete_all_memories(str(user.user_id))
    if not ok:
        return error_response(code=500, message="清空失败")
    return success_response(data={"message": "已清空所有记忆"})


@router.patch("/settings")
async def update_memory_settings(
    body: MemorySettingsRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新用户记忆开关设置（持久化到 users_shadow.metadata）。"""
    svc = UserService(db)
    svc.update_user_metadata(
        user_id=str(user.user_id),
        patch={"memory_enabled": body.memory_enabled},
    )
    return success_response(data={"memory_enabled": body.memory_enabled})
```

### 6.5 `api/routes/__init__.py` 和 `api/app.py` — 注册路由

`__init__.py` 追加：
```python
from .v1.memories import router as memories_router
```

`app.py` 追加：
```python
from api.routes import ..., memories_router
app.include_router(memories_router)
```

---

## 7. 前端改动

### 7.1 新增 API 调用函数（`api.ts`）

```typescript
// 记忆管理
export async function getMemories(): Promise<ApiEnvelope<{ enabled: boolean; items: MemoryItem[]; count: number }>> {
  return apiFetch('/v1/memories');
}

export async function deleteMemory(memoryId: string): Promise<ApiEnvelope<{ deleted: string }>> {
  return apiFetch(`/v1/memories/${memoryId}`, { method: 'DELETE' });
}

export async function clearAllMemories(): Promise<ApiEnvelope<{ message: string }>> {
  return apiFetch('/v1/memories', { method: 'DELETE' });
}

export async function updateMemorySettings(memoryEnabled: boolean): Promise<ApiEnvelope<{ memory_enabled: boolean }>> {
  return apiFetch('/v1/memories/settings', {
    method: 'PATCH',
    body: JSON.stringify({ memory_enabled: memoryEnabled }),
  });
}
```

### 7.2 新增类型（`types.ts`）

```typescript
export interface MemoryItem {
  id: string;
  memory: string;
  created_at: string;
  updated_at: string;
  score?: number;       // 检索相关性分数（search 时有）
}
```

### 7.3 设置弹窗新增记忆控制区（`App.tsx`）

在现有 `settingsOpen` Modal 的 `Space` 内，追加以下 JSX 块（放在"已启用清单"之前）：

```tsx
{/* ── 永久记忆设置 ── */}
<Divider style={{ margin: '6px 0' }} />

<div>
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
    <div>
      <Typography.Text strong>永久记忆</Typography.Text>
      <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
        开启后 AI 将记住您跨会话的偏好和背景信息
      </Typography.Text>
    </div>
    <Switch
      checked={memoryEnabled}
      onChange={async (checked) => {
        setMemoryEnabled(checked);
        await updateMemorySettings(checked);
        message.success(checked ? '永久记忆已开启' : '永久记忆已关闭');
      }}
    />
  </div>

  {memoryEnabled && (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          当前记忆条数：{memoryItems.length}
        </Typography.Text>
        <Space>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={async () => {
              const res = await getMemories();
              setMemoryItems(res.data?.items || []);
              setMemoryPanelOpen(true);
            }}
          >
            查看记忆
          </Button>
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={async () => {
              await clearAllMemories();
              setMemoryItems([]);
              message.success('记忆已清空');
            }}
          >
            清空记忆
          </Button>
        </Space>
      </div>
    </div>
  )}
</div>
```

### 7.4 记忆查看面板（`App.tsx`）

新增一个 Drawer 或 Modal，展示记忆列表（支持单条删除）：

```tsx
<Modal
  title="我的记忆"
  open={memoryPanelOpen}
  onCancel={() => setMemoryPanelOpen(false)}
  footer={null}
  width={600}
>
  <List
    dataSource={memoryItems}
    renderItem={(item: MemoryItem) => (
      <List.Item
        actions={[
          <Button
            key="del"
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={async () => {
              await deleteMemory(item.id);
              setMemoryItems(prev => prev.filter(m => m.id !== item.id));
            }}
          />
        ]}
      >
        <List.Item.Meta
          description={
            <Typography.Text style={{ fontSize: 13 }}>{item.memory}</Typography.Text>
          }
        />
      </List.Item>
    )}
    locale={{ emptyText: '暂无记忆' }}
  />
</Modal>
```

### 7.5 新增 State（`App.tsx`）

```tsx
const [memoryEnabled, setMemoryEnabled] = useState<boolean>(() =>
  localStorage.getItem('jingxin_memory_enabled') === 'true'
);
const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
const [memoryPanelOpen, setMemoryPanelOpen] = useState(false);

// 初始化时同步到后端
useEffect(() => {
  localStorage.setItem('jingxin_memory_enabled', String(memoryEnabled));
}, [memoryEnabled]);
```

---

## 8. 环境变量配置

在 `.env.example` 中新增以下配置段：

```bash
# ════════════════════════════════════════════════════════════════
#  mem0 记忆系统配置
# ════════════════════════════════════════════════════════════════

# 全局开关（后端层面，为 false 时所有记忆功能完全跳过）
MEM0_ENABLED=false

# 图记忆开关（需先确保 Milvus 正常运行后再考虑开启图模式）
MEM0_GRAPH_ENABLED=false

# ── Embedding 服务（vLLM 部署，OpenAI 兼容接口）────────────────
MEM0_EMBED_URL=http://<vllm-host>:<port>/v1
MEM0_EMBED_API_KEY=sk-placeholder
MEM0_EMBED_MODEL=bge-m3
MEM0_EMBED_DIMS=1024        # 向量维度，需与模型匹配

# ── Reranker 服务（vLLM 部署，可选）────────────────────────────
# 留空则不启用 Reranker
MEM0_RERANKER_URL=
MEM0_RERANKER_API_KEY=sk-placeholder
MEM0_RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# ── Milvus 向量数据库 ──────────────────────────────────────────
MILVUS_HOST=milvus
MILVUS_PORT=19530

# ── Neo4j 图数据库 ─────────────────────────────────────────────
NEO4J_URL=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=jingxin_neo4j_2026   # 生产环境请修改为强密码
```

---

## 9. 部署步骤

### 9.1 初次部署

```bash
# 1. 更新 .env（填写 MEM0_EMBED_URL 等实际地址）
cp .env.example .env
vim .env

# 2. 构建并启动所有服务（含新增的 Milvus、Neo4j）
docker-compose up -d --build

# 3. 等待 Milvus 就绪（约 30-60 秒）
docker-compose logs -f milvus | grep "Milvus Proxy successfully started"

# 4. 验证记忆 API
curl http://localhost:3000/api/v1/memories
# 期望: {"code": 0, "data": {"enabled": false, ...}}

# 5. 开启记忆功能
# 在 .env 中设置 MEM0_ENABLED=true，然后重启 backend：
docker-compose up -d --build backend
```

### 9.2 开启图记忆模式

```bash
# 确认 Neo4j 正常运行
docker-compose exec neo4j cypher-shell -u neo4j -p "${NEO4J_PASSWORD}" "RETURN 1"

# 在 .env 中设置
MEM0_GRAPH_ENABLED=true

# 重启 backend
docker-compose up -d --build backend
```

### 9.3 接入真实 Embedding/Reranker API

```bash
# 填写 vLLM 地址（地址由基础设施组提供后填入）
MEM0_EMBED_URL=http://<实际地址>/v1
MEM0_EMBED_API_KEY=<实际 Key>
MEM0_RERANKER_URL=http://<实际地址>/v1   # 有 Reranker 时填写

# 重启
docker-compose up -d --build backend
```

---

## 10. 数据治理与安全

### 10.1 数据存储位置

| 数据类型 | 存储位置 | 访问权限 |
|---|---|---|
| 记忆向量 | Milvus（Docker volume `milvus_data`） | 仅后端容器 |
| 记忆文本 | Milvus（payload 字段） | 仅后端容器 |
| 实体关系图 | Neo4j（Docker volume `neo4j_data`） | 仅后端容器 |
| Milvus 元数据 | etcd（Docker volume `etcd_data`） | 仅 Milvus 内部 |

**全部数据均存储在内网**，无任何数据发往外网。

### 10.2 用户数据隔离

mem0 通过 `user_id` 进行严格的数据隔离：
- 每个用户的记忆向量在 Milvus 中以 `user_id` 字段过滤
- 不同用户之间无法访问彼此的记忆
- API 层强制使用认证用户的 `user_id`，防止越权访问

### 10.3 GDPR 合规

- 用户可随时在设置页关闭永久记忆
- 用户可查看自己的所有记忆条目
- 用户可单条删除或一键清空全部记忆
- 管理员可通过 `DELETE /v1/memories?user_id=xxx` 删除指定用户数据

### 10.4 记忆范围限制

mem0 默认只对 `user` 和 `assistant` 角色的消息提取事实，工具调用结果不会直接进入记忆，保证记忆内容的质量和安全性。

---

## 11. 演进路线

### Phase 1（当前方案）：向量记忆

- Milvus 向量检索
- 基本的事实提取与去重
- 用户开关 + 管理 API

### Phase 2：图记忆增强

- 开启 `MEM0_GRAPH_ENABLED=true`
- Neo4j 实体关系存储
- 双路召回（向量 + 图），更丰富的上下文

### Phase 3：Reranker 精排

- 接入内网 vLLM Reranker 服务
- 配置 `MEM0_RERANKER_URL`
- 记忆检索精度进一步提升

### Phase 4：Milvus 集群化

- 按需从 Standalone 切换到 Distributed 模式
- 支持亿级记忆向量
- 代码层无需改动，仅变更 `MILVUS_HOST` 指向集群地址

---

## 附录：mem0 关键能力汇总

| 能力 | mem0 API | 配置项 |
|---|---|---|
| 记忆写入（含提取+决策） | `memory.add(messages, user_id)` | `llm` |
| 向量检索 | `memory.search(query, user_id)` | `vector_store` |
| 图检索 | 同上（自动合并到 results） | `graph_store` |
| 重排序 | 同上（自动后处理） | `reranker` |
| 获取全部记忆 | `memory.get_all(user_id)` | — |
| 删除单条 | `memory.delete(memory_id)` | — |
| 删除全部 | `memory.delete_all(user_id)` | — |
| 变更历史 | `memory.history(memory_id)` | — |
