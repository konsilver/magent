### 本文档主要定义memory 写入什么内容，在什么情况下触发写
KV模式下，我们主要存储经验文本，让每个agent都有自己的参考依据
而如果有些文本有强关系需要凸显在记忆中，我们会转而存储到Graph模式中。

注：之前的定义中agent没有做的行为，如果这个文档指定了则也要做
如：
1. QA额外向context的plan-step-risk字段填充了这一步的失败原因，代表这一步容易做错什么，以供写入记忆时参考
2. 我们在操作记忆时，很多时候是根据任务内容去查找相关记忆，如果有必要要先做一个缩减（任务本身->任务描述）以方便检索，类似的，如果发现存储记忆时某些内容过长不方便存储后检索，就调用LLM简化后再存储
3. QA的输出结构细化为了非全局检查和全局检查

Mem0记忆主要分为以下四个模块（一般不会联合使用或互相重叠），每个模块标注了自己以什么方式存储：

# User-profile（KV存储）
专门服务于存储用户特征这类内容，与user_id绑定
适用 agent：user_profile_agent

相关背景：user_profile_agent是第一个接手用户query的agent，它的行为是：调用LLM从query提取用户的偏好习惯、认知水平等信息，并筛选高稳定 + 高置信度部分作为context中的user-Urgent字段，然后根据用户query在memory中搜索相关的top-k作为context的user-mem字段。在这之后开启一个异步线程，把这两部分尝试合并，然后写入Memory

写时机：user_profile_agent讲context的user字段填充好后，异步写Memory
写内容：user-Urgent与mem字段的合并（如果有冲突的部分，以Urgent字段为正确，如果有重叠则覆盖，不要重复存储）

# Plan（KV + Graph）
planner制定出并执行完的计划，计划执行结果分为success（通过QA check）、fail（计划执行完但最后QA check不符合context中success_criteria）、forced（触发QA超过一次判定REPLAN），后两种统一视为失败，存的是可复用的规划结构 + 成败归因
适用 agent：planner、warmup agent

相关背景：在整个任务执行完之后，我们在context上有了整个计划的执行轨迹

写时机：plan执行完后，执行后异步写Memory（三种结果情况都写）
写内容：
a. 不管结果成败，调用LLM抽取plan的decomposition schema，输入：context 输出:任务分解策略(一句话)+抽象节点（去除实现细节）+节点依赖关系
b. KV模式存储：
        {
            "plan_id": "uuid",
            "embedding": [...],
            "payload": {
                "skeleton_description": "...",
                "status": "success | fail",
                "task_type": "...",
                "failure_type": "..."
            }
        }
c. Graph模式存储：
        PlanSkeleton（有plan_id+抽象描述） --has--> StepNode（包含节点抽象描述）
        StepNode --depends_on--> StepNode
        PlanSkeleton --fails_due_to--> FailurePattern
    成功样本：
        Task A
            ↓ uses
        Skeleton S1
            ↓ has
        [N1 → N2 → N3]
    失败样本：
        Skeleton S1
            ↓ fails_due_to（注意这里是计划骨架的失败原因而非单个节点失败原因）
        FailurePattern F1
        F1: 
            "failure_type": "missing_step"
            description = "缺少需求分析阶段"（如果有必要可以再加type字段）
d.结合context按照上述形式存储到memory两种模式中，他们之间连接的桥梁是plan_id，如果有不足的信息如task_type则让a中的LLM总结生成



# Task Execution（KV）
这一部分主要用于为subagent提供当前step的相似历史任务的处理经验
适用agent：subagent

相关背景：在整个任务执行完之后，我们在context上可以看到每个子任务是怎么做的，遍历每个step，如果这个step子任务被QA check为成功，则调用LLM先自问“这个信息，能不能在“不同任务但相似 step”中复用？”如果认为能，则：结合任务的tool_use轨迹（这个需要记录下来）且如果这个step曾经被QA判定REDO（不是REPLAN），结合QA在context填充的risk字段，让LLM提取能作为后面相似任务的经验的部分存入memory（KV模式）

写时机：整个任务执行完，异步遍历context记录下来的每个step，如果执行成功（过去失败重做后成功也算）则尝试写（但也不一定写，需要LLM judge）

写内容：step成功且LLM judge有写的必要后，写入LLM总结的insight内容

# Evaluation Heuristic
这一部分主要用于为QA提供模式上的判断经验，让其从规则驱动的判定转为结合经验与规则的判定，但目前系统有些复杂，这个模块先不实现

