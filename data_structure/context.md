# 这里面的数据结构是在计划模式下,所有agent共享的“黑板”，他们把自己负责的部分内容填充到这里，并且读取自己需要的模块内容

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
            //任务描述
            "description": "...",
            //对应subagent的输出结果写在这里,注意要经过QA check成功后再写
            "output": "...",
            //QA检查当前step需要REDO后，把失败经验填充到这里，否则为空
            "risk": "..."
        }
        
    ]
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



