//你拥有查看context的权利

input:
{
  //你可以在context中看到整个plan的执行进度，这是你的step id
  "step_id": 

  "previous_step_output": {
    // 上一个agent定义我这一步的局部约束
    "local_constraint": {...},

    // 上一个agent定义我这一步的输出结构
    "expected_schema": {...}
  },

  "retrieved_memory": {
    //在记忆中查找和我现在负责的子任务相似的任务的解决方案
    "relevant_patterns": [...]
  },

  //qa裁决这一步需要重新做时非空
  "failure_reason": [
    //遍历每一条，把错误汇总到这里
    {
      "type": "execution_error | goal_misalignment ",

      "description": "...",

      // 哪个约束被违反
      "violated": "...",

      "evidence": "...",

      "confidence": 0.0
    }
  ]
}


output:
{
  // 当前 step 的执行结果
  "result": "...",

  "next_step_instruction": {
    // 给下一步agent的局部约束，允许有软约束和硬约束
    "local_constraint": {
      "constraint": "...",

      // 用于 QA 分类判断
      "type": "format | logic | semantic",

      // QA 如何验证
      "check_method": "...",
              /**分为 1. rule_match 例如是为必须为json，是否必须包含某些字段，是否满足长度格式
                2. schema_validation  是否满足output schema
                3. constraint_check   软约束，交给LLM judge
              **/

      // hard = 必须满足，soft = 尽量满足
      "priority": "hard | soft",
    },

    // 下一步输出格式（必须可验证）要和local_constraint中的schema类约束一致
    "expected_output_schema": {
      "fields": [...],
      "types": {...},
      "required": [...],
      "validation_rules": [...]
    }
  }
}

- 必须遵守：
  - global_constraints
  - previous local_constraint

- 局部约束只能影响下一步（不能跨步传播）

- 不允许：
  - 修改 global_constraints
  - 覆盖已有约束

- 所有 constraint 必须：
  - 可解释（rationale）
  - 可验证（check_method）