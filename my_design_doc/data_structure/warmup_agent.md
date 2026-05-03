//你拥有查看context的权利
input:
{
  
}

output:
{
  "user_goal": 结合context中用户特征与计划总结用户目标
  
  //全局约束写入context：QA在每次检查step时会检查这里的约束，在最后计划完成后QA进行全局检查时也会检查这里的约束
  //每条constraint有自己独立的priority，软硬约束比例hard >= 60%，soft <= 40%
  "global_constraints": [
    {
      "constraint_type": "field_presence | value_range | format | dependency",//字段类型
      "target": "...",  //字段
      "rule": "...",  //字段的规则
      "priority": "hard | soft" //每条约束独立设置
    }
          /**例如：
            {
              "constraint_type": "field_presence",
              "target": "attractions",
              "rule": "must_exist",
              "priority": "hard"
            }
          **/
  ],


  "next_step_instruction": {
    // 给第一个subagent的局部约束，允许有软约束和硬约束
    "local_constraint": {
      "constraint": [  
        {
          "constraint_type": "field_presence | value_range | format | dependency",//字段类型
          "target": "...",  //字段
          "rule": "...",  //字段的规则
          "priority": "hard | soft" //每条约束独立设置，软硬约束比例hard >= 60%，soft <= 40%
        }
          /**例如：
            {
              "constraint_type": "field_presence",
              "target": "attractions",
              "rule": "must_exist",
              "priority": "hard"
            }
          **/
      ]
    },
    "expected_output_schema": {
        "fields": ["",""],  //输出结构包含的字段
        "required": ["",""] //fields中哪些字段必须包含
    }
  }

}

- Warmup 构造全局约束和第一个subagent的子任务约束