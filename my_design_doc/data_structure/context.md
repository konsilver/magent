# 这里面的数据结构是在计划模式下,所有agent共享的“黑板”，他们把自己负责的部分内容填充到这里，并且读取自己需要的模块内容
在整个计划执行的生命周期内，context应该一直保留在进程内存中并实时更新（它算作全局变量）

当一个计划重新产生（从零制定，如全局重置或计划被用户拒绝），这里的结构内容也重置为空，但如果是QA判定某一步开始的REPLAN，则context不重置

//有关用户的个性特征会被提取到这里
"user":{
    "Urgent"：   //user_profile_agent从最新query中提取，代表用户的第一时间需求
    "mem":      //user_profile_agent从查找和当前任务可能有关的用户特征
}

"plan":{
    "user_goal":    warmup agent生成
    "steps": [
        {
            "step_id": 1,
            //步骤简述
            "brief_description":
            //任务描述
            "description": "...",
            //对应subagent的输出结果写在这里,注意要经过QA check成功后再写
            "output": "...",
            "if_code_exc": bool
        }
    ]
    "suggestion": 这个字段很特殊但很实用，QA不管判断redo还是replan，生成的最新建议都同步到这里，且新的覆盖旧的
    "redo_id": -1  //当前某一步需要重做或planner需要从某一步开始重新规划，把id同步到这里
}

//每个subagent做子任务时都要遵循的约束（有软硬之分）
"check":{
    //warmup_agent写入,每个subagent执行完step QA要检查
    "global_constraints": [
        {
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
        }
    ],
}





