//你拥有查看context的权利
//不被QA检查

input:
{
  空，因为这里只需要告诉agent你该做什么（结合输出字段），并把context给它就够了
}

output:
{
  //结合context中的用户特征，在planner定义的goal上更进一步对准用户目标
  "refined_user_goal": "...",

  //全局约束写入context：QA在每次检查step时会检查这里的约束，在最后计划完成后QA进行全局检查时也会检查这里的约束
  "global_constraints": [
    {
      "constraint": "...",
      "type": "semantic | logic | format",
      "priority": "hard | soft"
    }
  ],


  "next_step_instruction": {
    // 给第一个subagent的局部约束，允许有软约束和硬约束
    "local_constraint": {
      "constraint": "...",
      "type": "semantic | logic | format",
      // QA 如何验证
      "check_method": "...",
              /**分为 1. rule_match 例如是为必须为json，是否必须包含某些字段，是否满足长度格式
                2. schema_validation  是否满足output schema
                3. constraint_check   软约束，交给LLM judge
              **/
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

- Warmup 结合memory中的个性化部分渲染任务，并构造全局约束和第一个subagent的子任务约束