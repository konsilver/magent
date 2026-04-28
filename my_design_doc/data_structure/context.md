# 这里面的数据结构是在计划模式下,所有agent共享的“黑板”，他们把自己负责的部分内容填充到这里，并且读取自己需要的模块内容
在整个计划执行的生命周期内，context应该一直保留在进程内存中并实时更新（它算作全局变量）

每次发给对应agent的LLM的prompt中，都应该包含context的全部内容

当一个计划重新产生（从零制定，如全局重置或计划被用户拒绝），这里的结构内容也重置为空，但如果是QA判定某一步开始的REPLAN，则context不重置

//有关用户的个性特征会被提取到这里
"user":{
    "Urgent"：   //user_profile_agent从最新query中提取，代表用户的第一时间需求
    "mem":      //user_profile_agent从查找和当前任务可能有关的用户特征
}

"plan":{
    "user_goal":    //先由planner生成，再经过warmup_agent加工

    "steps": [
        {
            "step_id": 1,
            //步骤简述
            "brief_description":
            //任务描述
            "description": "...",
            //对应subagent的输出结果写在这里,注意要经过QA check成功后再写
            "output": "...",
        }
    ]
}

//每个subagent做子任务时都要遵循的约束（有软硬之分）
"check":{
    //warmup_agent写入,每个subagent执行完step QA要检查
    "global_constraints": [
        {
        "constraint": "...",
        "type": "semantic | logic | format",
              // QA 如何验证
        "check_method": "...",
                /**分为 1. rule_match 例如是为必须为json，是否必须包含某些字段，是否满足长度格式
                    2. schema_validation  是否满足output schema
                    3. constraint_check   软约束，交给LLM judge
                **/
        "priority": "hard | soft"
        }
    ],
    // 显式假设（避免隐式错误）
    "assumptions": [...]
}





