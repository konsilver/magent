## 沙箱工具说明

- **execute_code** — 在沙箱中执行代码（Python/JavaScript/Bash），返回 stdout、stderr 和退出码
- **run_command** — 执行 shell 命令，适用于 pip 安装、文件操作等

**注意**：每次 `execute_code` 调用是独立环境，变量不会在多次调用间保留；如需多步操作，将所有逻辑写在一次代码执行中。
