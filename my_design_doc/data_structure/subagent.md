//你拥有查看context的权利
//最后一个subagent是总结agent，不会被QA要求重做
input:
{
  //你可以在context中看到整个plan的执行进度，这是你的step id
  "step_id": 

  //你能在global中看到上一步agent给你做的约束
  //你能在context中看到整个计划的当前执行状况、用户特征、全局约束等信息

  "retrieved_memory": {
    //在记忆中查找和我现在负责的子任务相似的任务的解决方案经验
    "relevant_patterns": [...]
  },

  //当你被QA check结果是REDO后，你能在context中查看你这一步的suggestion
}


output:
{
  // 当前 step 的执行结果
  "result": "...",

  "next_step_instruction": {
    // 给下一步agent的局部约束，允许有软约束和硬约束，属于global
    "local_constraint": {
      "constraint": [  
        "constraint_type": "field_presence | value_range | format | dependency",//字段类型
        "target": "...",  //字段
        "rule": "..."   //字段的规则
      ],
          /**例如：
            {
              "constraint_type": "field_presence",
              "target": "attractions",
              "rule": "must_exist"
            }
          **/
      "priority": "hard | soft", //限制软硬约束比例hard >= 60%，soft <= 40%
    },
    "expected_output_schema": {
        "fields": ["",""],  //输出结构包含的字段
        "required": ["",""] //fields中哪些字段必须包含
    }
  }
  //属于global
  "tool_use_trace": ...
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