项目的系统逻辑，你可以在project_plan.md, data_structure, memory_manage中看到，其大部分逻辑已经在src/backend/routing/subagents/plan_mode中实现


4.27
你现在需要做的事情：
1. 在error_log中有一些容器运行起来后的测试案例，包括对话内容与日志报错信息，请你尝试对其修复
2. plan的执行过程中有很多个agent，它们是需要模型配置信息的，为了管控这些不经过DB操控配置的agent的LLM配置，请你做一个配置文件方便我在其中进行配置

  role_key              用途                当前解析方式
`main_agent`         主对话 Agent           DB → 环境变量
`user_profile agent` 处理用户特征            环境变量
`plan_agent`          规划模式              DB → 回退 main_agent
`warmup agent`       加工plan                环境变量
`subagent`         执行plan的子任务           环境变量
`QA agent`         检查任务完成情况           环境变量
`code_exec`           代码执行              DB → 回退 main_agent
`summarizer`          历史压缩              DB → 回退 main_agent
`chart`              图表生成 MCP           DB → 回退 main_agent
`embedding`          长期记忆向量化         DB → 环境变量
`reranker`           知识库重排序           DB → 环境变量

3. planner的输出新增了step的brief_description字段，代表这个步骤任务的简述
3. 新增功能：优化与用户的交互体验。
    a.在planner制定计划后，把计划的方案呈现给前端用户，且呈现每个子步骤的简述+具体描述（可以做成用户点击简述后下拉展示除具体描述，也可以收起）
    b.用户可以对展示的plan回复“确认执行”或“重新计划+建议”两种回答（之前只支持确认执行，这两种的区分需要LLM识别），如果用户确认执行则调用整个agent链路；如果用户希望重新计划，要识别出用户给出的计划建议，先调用user_profile_agent对用户特征进行提取识别，并按照正常操作流程那样写入context+memory；接着将之前指定的被用户驳回的计划方案，并且要以用户给出的建议作为其失败原因像正常执行失败的计划那样存入memory，然后重新制定计划
    c.事实上用户在输入任务要求后，可以在query中加入自己的建议，而因为planner接受的上下文包括之前三轮历史对话+用户最新输入，因此我们认为它能看到用户的建议制定建议。包括用户输入“重新计划+建议”后的planner重新计划也是这样。