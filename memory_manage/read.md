### 本文档主要定义memory怎么读（使用）记忆，什么时候读
KV模式下，我们主要存储经验文本，让每个agent都有自己的参考依据
而如果有些文本有强关系需要凸显在记忆中，我们会转而存储到Graph模式中。

Mem0记忆主要分为以下四个模块（一般不会联合使用或互相重叠），每个模块标注了自己以什么方式存储：

# User-profile（KV存储，top k=4）
专门服务于存储用户特征这类内容，与user_id绑定
适用 agent：user_profile_agent

相关背景：user_profile_agent是第一个接手用户query的agent，它的行为是：调用LLM从query提取用户的偏好习惯、认知水平等信息，并筛选高稳定 + 高置信度部分作为context中的user-Urgent字段，然后根据用户query在memory中搜索相关的top-k作为context的user-mem字段。在这之后开启一个异步线程，把这两部分尝试合并，然后写入Memory

读时机：user_profile_agent接收query时根据相似任务筛选top-k的用户特征
读方法：根据任务描述查，如用户在做旅游攻略计划制定时喜欢性价比高的住宿环境，则以后制定旅游攻略能找到这一条

# Plan（KV + Graph，top k=8）
planner制定出并执行完的计划，计划执行结果分为success（通过QA check）、fail（计划执行完但最后QA check不符合context中success_criteria）、forced（触发QA超过一次判定REPLAN），后两种统一视为失败，存的是可复用的规划结构 + 成败归因
适用 agent：planner、warmup agent

相关背景：在整个任务执行完之后，我们在context上有了整个计划的执行轨迹

读时机：planner制定计划时触发读
读方法：planner根据任务描述先查找KV，然后LLM筛选比较符合的方案，根据KV中的plan_id查找Graph中的依赖图，返回给LLM供计划制定参考。planner查完后保留内容给warmup再次使用



# Task Execution（KV top k=4）
这一部分主要用于为subagent提供当前step的相似历史任务的处理经验
适用agent：subagent

相关背景：在整个任务执行完之后，我们在context上可以看到每个子任务是怎么做的，遍历每个step，如果这个step子任务被QA check为成功，则调用LLM先自问“这个信息，能不能在“不同任务但相似 step”中复用？”如果认为能，则：结合任务的tool_use轨迹（这个需要记录下来）且如果这个step曾经被QA判定REDO（不是REPLAN），结合QA在context填充的risk字段，让LLM提取能作为后面相似任务的经验的部分存入memory（KV模式）

读时机：subagent在执行自己的任务前
读方法：根据子任务描述查找KV对应解决方案

# Evaluation Heuristic
这一部分主要用于为QA提供模式上的判断经验，让其从规则驱动的判定转为结合经验与规则的判定，但目前系统有些复杂，这个模块先不实现

