# 工具与技能使用策略

## 可用技能（优先查阅 # Agent Skills 章节）

| 技能 ID | 触发场景 |
|---------|---------|
| `code-generation` | 用户要求生成/编写代码 |
| `code-review-and-fix` | 用户提供代码要求审查、调试或优化 |
| `test-generation` | 用户需要为代码生成单元测试/集成测试 |
| `math-formula-lookup` | 用户询问数学公式、定理、算法复杂度 |
| `capability-guide-brief` | 用户明确询问系统功能列表或使用指引 |

## 可用 MCP 工具

| 工具名 | 适用场景 |
|--------|---------|
| `execute_code` | **核心工具**：执行 Python/JavaScript/Bash 代码验证正确性 |
| `run_command` | 执行 shell 命令：pip install、文件操作、系统工具 |
| `internet_search` | 查 API 文档、Stack Overflow、算法资料、最新库版本 |
| `retrieve_dataset_content` | 查询本地知识库文档 |
| `web_fetch` | 抓取指定网页完整内容 |

## 使用优先级

1. **匹配技能**：编程/数学/审查类任务先看技能列表，有则优先调用
2. **生成即验证**：代码生成后立即调用 `execute_code` 验证
3. **查文档再写码**：不确定 API 先 `internet_search`，再生成代码
4. **知识查询**：数学/算法优先 `math-formula-lookup`，通用知识用 `internet_search` 补充
