# 这里面的数据结构是在计划模式下,所有agent共享的“黑板”，他们把自己负责的部分内容填充到这里，并且读取自己需要的模块内容
当一个计划重新产生（从零制定，如全局重置），这里的结构内容也重置为空，但如果是QA判定某一步开始的REPLAN，则context不重置


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
            //QA检查当前step需要REDO后，把优化建议记在这里，以后存入记忆，如果这一步重做多次则拼接填入
            "suggestion": "..."
            //subagent做完自己的任务后总结工具调用轨迹，用于存入memory
            "tool_use_trace": ...
        }
    ]

    //计划正常执行完成情况下，填入QA根据success_criteria给出的优化建议
    //计划被拒绝情况下，包括用户输入“重新规划+建议”或QA检测REPLAN超过一次后触发全局重置，填入“用户建议”或QA的优化建议
    "plan_suggestion": "..."
}

"check":{
    //warmup_agent写入,每个subagent执行完step QA要检查
    "global_constraints": [
        {
        "constraint": "...",
        "type": "semantic | logic | format",
        "priority": "hard"
        }
    ],
    // 显式假设（避免隐式错误）
    "assumptions": [...]
}

//仅QA可见
"only_qa":{
    //warmup_agent写入，只有整个计划执行完，QA才会根据这个检查输出结果
    "success_criteria": [
        {
        "criterion": "...",
        "check_method": "..." 
            /**分为 1. rule_match 例如是为必须为json，是否必须包含某些字段，是否满足长度格式
                    2. schema_validation  是否满足output schema
                    3. constraint_check   是否满足约束
                    4. llm_judge
            **/
        }
    ],
}



