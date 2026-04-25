### 记忆的读

 在整个链路中允许读记忆的有：

- User-profile agent：专门负责存取用户个性特征（LLM judge），但与任务无关，**记忆会存用户特征**
- planner agent：只在接收用户任务时查找历史相似任务的plan，根据其制定计划，**记忆会存计划方案**
- Warmup agent: 在planner查找的记忆信息基础上，user-profile
- subagent：查找相似子任务的执行方案，**记忆会存执行方案**

### 记忆的写

- QA只写记忆（不读），写记忆是异步线程。

- 对于plan，不会存储完整的step细节，而是只存入结构化、可复用的任务信息单元

  ```
  {
    "type": "task_memory",
    "user_goal": "...",
    "plan": [...],            //调用一个LLM对step进行总结，转化为为适合存到  memory的结构
    "success": true/false,    //QA在subagent把
    "failure_reason": "...",
    "key_constraints": [...],
    "final_solution_summary": "...",
    "quality_score": 0.0-1.0,
    "forced": true/false      //是否触发了forced模式
  }
  ```

- 对于子任务的执行方案，如下存储：

  ```
  {
    "type": "step_memory",
    "step_description": "...",
    "input_context": "...",
    "local_constraint": {...},
    "output_schema": {...},
    "result_quality": "high/low",
    "error_pattern": "...",
    "improvement_hint": "..."
  }
  ```

- User Memory，存储用户个性特征

  ```
  {
    "type": "user_profile",
    "preference": [...],
    "skill_level": "...",
    "common_tasks": [...],
    "style": "...",
    "constraints": [...]
  }
  ```

### 记忆的结构（未完成）

我们希望结合KV与图结构构建记忆：

Vector Memory (KV)： task embedding+step embedding

Graph (Neo4j)： task-step关系 、constraint链、failure pattern