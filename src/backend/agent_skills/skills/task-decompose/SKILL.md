---
name: system-design-decompose
display_name: 系统设计任务分解
description: 当用户提出复杂后端系统设计需求时使用，如"帮我规划设计一个高并发消息队列系统""如何拆解一个分布式任务调度器的设计过程""设计电商秒杀系统需要哪些步骤"。将设计任务分解为三步结构化流程（模块设计→接口定义→约束验证），作为系统设计 Plan Mode 的输入蓝图。
version: 2.0.0
tags: planning,decomposition,architecture,system-design,backend
allowed_tools: internet_search
---

# 系统设计任务分解

将复杂后端系统设计需求分解为三步结构化设计流程，明确每步的负责 Agent 类型、设计目标和验收标准，作为系统设计 Plan Mode 的执行蓝图。

## Instructions

1. **分析系统目标**：理解系统的核心用途、规模要求、非功能性约束（高并发、低延迟、高可用等）
2. **识别设计难点**：找出该系统中最关键的设计决策点（如存储选型、通信模式、容错策略）
3. **制定三步分解方案**，固定结构如下：

   **Step 1 — ArchitectAgent（模块设计）**
   - 目标：识别并定义系统核心模块，明确每个模块的职责边界
   - 输出：`modules` 数组（name + responsibility）
   - 验收标准：模块数 ≥ 4，无职责重叠，涵盖接入层/业务层/存储层

   **Step 2 — InterfaceAgent（接口与数据流设计）**
   - 目标：基于 Step 1 的模块定义接口交互关系和数据流转路径
   - 输出：`interfaces` 数组（from/to/type）+ `data_flow` 数组（step/description）
   - 验收标准：所有接口端点均为已定义模块；覆盖主请求链路

   **Step 3 — ConstraintAgent（约束覆盖验证）**
   - 目标：验证已设计的模块和接口是否充分覆盖非功能性约束
   - 输出：`constraints_covered` 数组
   - 验收标准：覆盖 high concurrency、scalability、fault tolerance、modularity 四项

4. **说明设计难点**：指出该系统设计中最需要关注的 1-2 个技术决策点
5. **触发 Plan Mode**：输出结构化分解方案后，系统将进入 Plan Mode 逐步执行

## Inputs

- 系统设计需求描述（必须）
- 非功能性约束（可选，如"支持每秒 10 万请求""99.99% 可用性"）
- 技术栈偏好（可选）

## Outputs

- 系统设计目标总览（核心用途 + 关键约束 + 预期产出）
- 三步分解方案（Step 1/2/3 含目标、Agent 类型、验收标准）
- 关键设计决策点说明（1-2 个最重要的技术权衡）

## Response Template

1. **系统目标总览**（1-3 句：系统是什么 + 核心约束）
2. **三步设计分解**
   - Step 1：ArchitectAgent — 模块设计（目标 + 验收标准）
   - Step 2：InterfaceAgent — 接口与数据流（目标 + 验收标准）
   - Step 3：ConstraintAgent — 约束验证（目标 + 验收标准）
3. **关键设计决策点**（最值得关注的 1-2 个技术权衡，如 CAP 选择、同步 vs 异步通信）
