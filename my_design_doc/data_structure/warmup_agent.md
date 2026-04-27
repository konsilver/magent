//你拥有查看context的权利
//不被QA检查

input:
{
  //和planner一样，是最近的3轮与用户的交互历史(上下文)+最新用户query
  "user_input": "...",

  //planner的输出
  "planner_output": {...},  

  //可以复用planner查找memory的结果，不用再查一遍
  "retrieved_memory": {
    "similar_tasks": [...],
  },
}

output:
{
  //结合context中的用户特征，在planner定义的goal上更进一步对准用户目标
  "refined_user_goal": "...",

  //全局约束：后续所有 step 必须遵守，这里面全是hard约束，soft约束放在后面的success_criteria中
  "global_constraints": [
    {
      "constraint": "...",
      "type": "semantic | logic | format",
      "priority": "hard"
    }
  ],

  // QA 用于最终判断的标准（判断全局任务完成情况）,写入context
  "success_criteria": [
    {
      "criterion": "...",
      "check_method": "..." 
        /**分为 1. rule_match 例如是为必须为json，是否必须包含某些字段，是否满足长度格式
                2. schema_validation  是否满足output schema
                3. constraint_check   软约束，交给LLM judge
        **/
    }
  ],

  "next_step_instruction": {

    // 给第一个subagent的局部约束，允许有软约束和硬约束
    "local_constraint": {
      "constraint": "...",

      "type": "semantic | logic | format",

      // QA 如何验证，和上面的success_criteria一样分类
      "check_method": "...",

      // hard = 必须满足，soft = 尽量满足
      "priority": "hard | soft",
    },

    // 下一步输出格式（必须可验证），要和local_constraint中的schema类约束一致
    "expected_output_schema": {
      "fields": [...],
      "types": {...},
      "required": [...],
      "validation_rules": [...]
    }
  }

  // 显式假设（避免隐式错误），写入context
  "assumptions": [...]
}

- Warmup 结合memory中的个性化部分渲染任务