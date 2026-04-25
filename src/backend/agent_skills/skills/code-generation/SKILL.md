---
name: architect-design
display_name: 模块架构设计
description: 当用户需要设计后端系统的模块划分时使用，如"帮我设计一个短链接系统的模块""拆分任务调度器的核心组件""设计聊天后端的服务层结构"。输出模块列表（名称+职责），符合 ArchitectAgent 的输出 Schema。
version: 2.0.0
tags: architecture,module,design,backend,system-design
---

# 模块架构设计

根据用户给出的系统需求，识别并定义后端系统的核心模块，输出符合系统设计方案 Schema 的结构化模块列表。

## 重要说明

- 本技能**不执行代码**，不调用 `execute_code`。
- 输出必须是**严格 JSON**，对应 `ArchitectAgent` 的局部输出格式。
- 禁止调用 `run_skill_script`，本技能无任何可执行脚本。

## Instructions

1. **理解系统目标**：明确系统要解决的核心问题（如高并发读写、任务调度、实时通信等）
2. **识别职责边界**：按单一职责原则拆分模块，确保模块间低耦合、高内聚
3. **确定模块数量**：通常 4-8 个核心模块，复杂系统可适当增加，避免过度拆分
4. **为每个模块命名**：名称使用英文 PascalCase（如 `APIGateway`、`CacheLayer`），职责用一句话描述
5. **输出 JSON**：严格按照以下 Schema 输出，不添加额外文字

## Output Schema

```json
{
  "modules": [
    {
      "name": "模块英文名（PascalCase）",
      "responsibility": "该模块的核心职责，1-2 句话"
    }
  ]
}
```

## 设计原则

- **单一职责**：每个模块只做一件事，职责描述中不出现"以及""同时"等并列关系
- **分层清晰**：接入层（Gateway）→ 业务层（Service）→ 存储层（Storage/Cache）
- **非功能性模块**：高并发系统需有缓存层；需要可观测性的系统需有监控/日志模块
- **不要遗漏**：认证、限流、消息队列等横切关注点也应作为独立模块

## Inputs

- 系统需求描述（必须）
- 非功能性约束（可选，如高并发、低延迟、高可用）
- 已有技术栈约束（可选）

## Outputs

- 模块列表 JSON（`modules` 数组，每项含 `name` 和 `responsibility`）
- 简要设计说明（模块分层思路，2-3 句）
