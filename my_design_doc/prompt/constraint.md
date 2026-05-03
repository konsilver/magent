# 这个文件关注如何prompt让LLM生成合法、合适的约束

背景：
    我们需要一个agent对于一个给定的任务描述输出约束，而参考AMGC等思想，我们为约束预设结构，并提前为“约束的构造”做出约束

在本项目中，凡是LLM生成的约束，输出结果分为约束和输出格式：

    "constraint": {
      "constraint": [  
        "constraint_type": "field_presence | value_range | format | dependency",//字段类型
        "target": "...",  //字段
        "rule": "...",  //字段的规则
        "priority": "hard | soft" //每条约束独立设置软硬属性，软硬约束比例hard >= 60%，soft <= 40%
      ],
          /**例如：
            {
              "constraint_type": "field_presence",
              "target": "attractions",
              "rule": "must_exist",
              "priority": "hard"
            }
          **/
    },

    "expected_output_schema": {
        "fields": ["",""],  //输出结构包含的字段
        "required": ["",""] //fields中哪些字段必须包含
    }

## System Prompt（定义规则，不让模型自由发挥）

你必须遵守以下规则：

1. expected_output_schema 定义“输出结构”
2. local_constraint 只能约束 schema 中已定义的字段
3. 禁止在 constraint.target 中使用 schema 未定义字段
4. 所有 field_presence constraint 必须来自 schema.required 或 schema.fields
5. 不允许生成模糊约束（如：合理、尽量、适当）
6. 必须先生成 schema，再生成 constraint
7. 最后必须执行一致性自检

## step-by-step prompt（告诉LLM如何构造约束）：

请按以下步骤生成：

Step 1: 生成 expected_output_schema
要求：
- fields: 列出所有输出字段
- required: 必须是 fields 的子集

Step 2: 基于 schema 生成 local_constraint
要求：
- constraint.target 必须 ∈ fields
- 每个 required 字段必须有 field_presence constraint
- 不允许引用未定义字段

Step 3: 执行一致性检查
检查：
- 是否所有 constraint.target 都在 fields 中
- 是否 required 字段都有约束
- 是否存在未定义字段引用

如果发现不一致，必须修正后再输出最终结果
