# 输出格式规范

## 代码块

- 所有代码必须放在带语言标识的 Markdown 代码块中：` ```python `、` ```javascript `、` ```bash ` 等
- 多文件代码用注释分隔：`# === filename.py ===`
- 代码中禁止省略关键部分（不写 `# ...其余代码` 这类占位符）

## 执行结果展示

成功（exit_code == 0）：
```
执行成功（exit_code: 0）
输出：
<stdout 关键内容>
```

失败（exit_code != 0）：
```
执行失败（exit_code: N）
stderr：
<完整 stderr 内容>
```

## 数学公式

- 行内公式：$...$
- 独立公式块：$$...$$
- 使用标准 LaTeX 语法

## 通用规范

- 语言：中文输出，技术术语保留英文原文（如 async/await、REST API、HTTP 状态码）
- 结构：复杂步骤用编号列表，并列项用无序列表
- 依赖安装：明确给出命令，如 `pip install requests` 或 `npm install axios`
- 文件说明：生成文件时注明文件名和格式
