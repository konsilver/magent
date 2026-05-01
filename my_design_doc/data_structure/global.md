# 这个文档明确说明哪些agent产生的信息需要作为全局变量保留在进程中，context“黑板”也是全局变量，但其是特殊的（需要作为agents的prompt），因此在额外文件中说明

1.关于step：
    
    而QA对每个步骤检查后，如果该步骤通过则产生
        "steps": [
            {
                "step_id": 1,

                // step的上一个agent定义这一步的局部约束，记录在这里，让step的subagent和QA能看见
                "local_constraint": {...},

                //step的上一个agent定义这一步的输出结构，记录在这里，让step的subagent和QA能看见
                "expected_schema": {...}

                //每个subagent做完自己的任务，且被QA check通过后（如果重做后成功也算，但不成功则不算）记录下自己的工具调用轨迹
                "tool_use_trace": ...

                "suggestion": "..."

            }
        ]


2. 关于memory：

    //planner从memory中查找，warmup_agent复用这个
    "retrieved_memory": {
        // 相似任务（用于planner参考制作计划，查找的是相似任务，可以是之前成功的也可以是失败的，先从KV找top-k，再到Graph查details）
        "similar_tasks": [...],
    },
