---
name: design-review-and-refine
display_name: 设计方案审查与改进
description: 当用户提供了系统设计方案需要评审或改进时使用，如"帮我看看这个架构有什么问题""这个接口设计合理吗""我的模块划分是否有依赖环""如何改进这个设计的可扩展性"。输出问题清单、改进建议及精炼后的设计方案。
version: 2.0.0
tags: design,review,architecture,refine,consistency
---

# 设计方案审查与改进

接收用户提供的系统设计方案（JSON 或描述），进行结构分析与一致性检查，输出问题清单和改进后的设计方案。

## 重要说明

- 本技能**不执行代码**，专注于设计层面的分析。
- 审查结论必须基于设计方案中的实际内容，禁止凭空补全不存在的模块或接口。
- 禁止调用 `run_skill_script`，本技能无任何可执行脚本。

## Instructions

1. **解析设计方案**：识别用户提供的设计内容，提取 `modules`、`interfaces`、`data_flow`、`constraints_covered` 字段（若存在）
2. **结构完整性检查**：
   - 必要字段是否齐全（`system_goal`、`modules`、`interfaces`、`data_flow`、`constraints_covered`）
   - 每个模块是否有明确的 `name` 和 `responsibility`
   - 每个接口是否有 `from`、`to`、`type`
3. **一致性检查**：
   - `interfaces` 中的 `from`/`to` 是否均引用了 `modules` 中已定义的模块名
   - 是否存在孤立模块（无任何接口连接）
   - `data_flow` 中的步骤是否与接口定义对应
4. **约束覆盖检查**：
   - 检查 `constraints_covered` 是否涵盖 `high concurrency`、`scalability`、`fault tolerance`、`modularity`
   - 找出设计中对应约束的具体实现点（如缓存层对应 high concurrency）
5. **改进建议**：针对每个问题给出具体的改进方向，输出精炼后的完整设计 JSON

## 问题严重级别

| 级别 | 含义 | 示例 |
|---|---|---|
| **严重** | 影响设计可行性 | 接口引用了未定义的模块；缺少核心字段 |
| **警告** | 影响非功能性质量 | 未覆盖 fault tolerance；无缓存层却声称支持高并发 |
| **建议** | 可读性或扩展性 | 模块职责描述不清晰；接口类型选择可以更优 |

## Inputs

- 系统设计方案（JSON 或描述，必须）
- 关注的改进维度（可选，如"重点看可扩展性"）

## Outputs

- 问题清单（严重 / 警告 / 建议，每项说明位置和原因）
- 改进建议（针对每个问题的具体方向）
- 精炼后的完整设计 JSON（修复所有严重和警告问题后）
