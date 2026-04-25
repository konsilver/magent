---
name: design-validation
display_name: 设计方案验证
description: 当用户需要验证系统设计是否覆盖了非功能性约束时使用，如"验证这个设计是否支持高并发""检查容错机制是否完整""这个系统能水平扩展吗""这个设计满足 CAP 定理中的哪些特性"。输出约束覆盖分析报告和 constraints_covered 字段。
version: 2.0.0
tags: validation,constraints,non-functional,concurrency,scalability,fault-tolerance
---

# 设计方案验证

对已有的系统设计方案进行非功能性约束的覆盖验证，判断设计是否满足高并发、可扩展性、容错性、模块化等关键约束，输出符合 `ConstraintAgent` Schema 的结构化结论。

## 重要说明

- 本技能**不执行代码**，专注于约束分析与覆盖判断。
- 验证结论必须**对应设计中的具体模块或接口**，不能泛泛而谈。
- 禁止调用 `run_skill_script`，本技能无任何可执行脚本。

## Instructions

1. **解析输入设计**：提取 `modules`、`interfaces`、`data_flow` 的内容作为验证依据
2. **逐项验证四大约束**：

   ### high concurrency（高并发）
   - 是否有缓存层（Redis/Memcached）减少数据库压力
   - 是否有负载均衡/API 网关分发请求
   - 核心服务是否无状态（支持水平扩展）
   - 数据库是否有读写分离或分片方案

   ### scalability（可扩展性）
   - 是否采用微服务或模块化设计（新功能可独立扩展）
   - 存储层是否支持水平扩展（分库分表、分布式存储）
   - 是否有消息队列解耦生产者和消费者

   ### fault tolerance（容错性）
   - 是否有降级策略（缓存穿透时的兜底方案）
   - 关键链路是否有重试机制
   - 数据是否有备份/副本策略（主从复制等）
   - 单点故障是否已消除

   ### modularity（模块化）
   - 每个模块职责是否单一（无上帝模块）
   - 模块间是否通过接口而非直接依赖通信
   - 是否存在循环依赖

3. **给出覆盖结论**：每个约束标注「已覆盖 / 部分覆盖 / 未覆盖」并说明依据
4. **输出 JSON**：严格按照以下 Schema 输出

## Output Schema

```json
{
  "constraints_covered": [
    "已覆盖的约束名（从四项中选取已满足的）"
  ]
}
```

约束名固定为：`high concurrency`、`scalability`、`fault tolerance`、`modularity`

## Inputs

- 系统设计方案（JSON 或描述，必须）
- 重点关注的约束（可选，如"重点验证容错性"）

## Outputs

- 四大约束逐项分析报告（覆盖状态 + 对应设计点 + 不足之处）
- `constraints_covered` JSON（仅列入已充分覆盖的约束）
- 改进建议（针对未覆盖或部分覆盖的约束）
