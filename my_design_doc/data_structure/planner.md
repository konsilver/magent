
//你能看到context，它是你LLM的prompt之一


output:
{
  //写入context中，注意REPLAN后要同步更新到context
  "steps": [
    {
      "step_id": 1,
      //步骤任务简述
      "brief_description":
      //只描述步骤任务，不包含约束、格式、实现方式
      "description": "..."
    }
  ]
}

- Planner 只负责“任务分解”，不负责执行细节
- 不生成：
  - 局部约束
  - 输出格式
