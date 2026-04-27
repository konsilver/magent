//你拥有查看context的权利


input:
{
   //注意是把最近的3轮与用户的交互历史(上下文)+最新用户query放在这里
   //以让planner尽可能获得全局视角
  "recent_input": "...",    

  //从memory中查找，记得用完别扔掉，warmup_agent也要用这个
  "retrieved_memory": {
    // 相似任务（用于参考 plan 的step制定）
    "similar_tasks": [...],

    // 相关的历史失败记录（用于规避）
    "failure_patterns": [...]
  },

  //QA触发REPLAN时才不为空
  "replan_context": {
    //是否是彻底replan（从头开始而不是从当前step开始）,如果不是则从当前出问题步骤开始重新规划
    "complete": false,

    // replan触发发生在哪一步
    "failed_step": 2,

    // QA 提供的失败原因（Planner 重做计划时使用参考）
    "failure_reason": {...}
  }
}

output:
{
  //初步总结的任务目标，写入context中
  "user_goal": "...",

  //写入context中，注意REPLAN后也写入context
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
