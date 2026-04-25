# Skill 脚本执行方案

> 版本: v2.1 | 日期: 2026-03-30
> 目标: 为 Jingxin-Agent 增加有限的代码执行能力 — **仅执行 Skill 中预定义的脚本**，禁止任意代码执行
> 架构: 独立 sidecar 容器执行，与 backend 进程隔离

---

## 一、需求定义

### 1.1 核心约束

**只执行 Skill 作者预先编写的脚本，不执行任何 LLM 生成或用户输入的代码。**

- 代码是**可信的**（Skill 作者/管理员编写并审核）
- 只有**参数**来自 LLM/用户（不可信输入到可信代码）
- 不需要通用代码沙盒，只需要**白名单脚本执行器**

### 1.2 为什么不在 backend 容器内执行

虽然代码可信，但在 `jingxin-backend` 容器内直接 `subprocess` 有以下问题：

| 问题 | 说明 |
|---|---|
| 资源争抢 | 脚本内存/CPU 爆掉会拖垮 backend 主进程 |
| 环境污染 | 脚本和 backend 共享文件系统，可读取 DB 密码、API Key |
| 网络暴露 | 脚本可访问 backend 同网络的 postgres、redis、内网服务 |
| 依赖冲突 | 脚本需要 pandas/matplotlib 等库，会使 backend 镜像膨胀 |
| 故障隔离 | 脚本 segfault 或无限 fork 可能导致 backend 容器重启 |

### 1.3 方案选择：独立 Sidecar 容器

```
┌─ jingxin-backend ──────────┐      HTTP       ┌─ jingxin-script-runner ──────┐
│                             │  ──────────→    │                              │
│  FastAPI + Agent            │  POST /execute  │  轻量 HTTP 服务              │
│  不含 pandas/matplotlib     │  ←──────────    │  预装数据分析库              │
│  可访问 DB/Redis/LLM        │  JSON result    │  不可访问 DB/Redis/LLM       │
│                             │                 │  独立资源限制                │
└─────────────────────────────┘                 └──────────────────────────────┘
     jingxin-network                              script-runner-network (隔离)
```

| 方案 | 隔离程度 | 复杂度 | 性能 |
|---|---|---|---|
| A. backend 内 subprocess | 低 | 最简单 | <10ms |
| **B. 独立 sidecar 容器（选定）** | **中高** | **中等** | **~20ms** |
| C. 按需创建临时容器 | 最高 | 高 | 200ms-2s |

---

## 二、架构设计

### 2.1 整体架构

```
用户发送消息
  → workflow.py → Agent (ReActAgent)
    → Agent 判断需要执行 Skill 脚本
    → 调用 run_skill_script 工具
      → agent_skills/script_runner.py (backend 侧)
        ① 白名单校验: skill 存在 + 脚本在 executable_scripts 中
        ② 参数校验: params 匹配 JSON Schema
        ③ HTTP POST → jingxin-script-runner:8900/execute
      → script_runner 容器 (sidecar 侧)
        ④ 接收请求，写脚本到 /tmp
        ⑤ subprocess 执行 (ulimit + timeout + 清洁 env)
        ⑥ 收集 stdout/stderr/exit_code
        ← HTTP 200 JSON response
      ← Agent 处理结果，继续推理
    ← SSE stream 返回前端
  ← 前端展示结果
```

### 2.2 容器职责划分

| | jingxin-backend | jingxin-script-runner |
|---|---|---|
| **职责** | 白名单校验 + Schema 校验 + HTTP 调用 | 接收脚本 + subprocess 执行 |
| **网络** | jingxin-network（可访问 DB/Redis） | script-runner-network（仅与 backend 通信） |
| **数据库** | 可访问 | **不可访问** |
| **API Key** | 有 | **没有** |
| **Python 库** | FastAPI, agentscope, ... | pandas, numpy, matplotlib, openpyxl |
| **资源限制** | 无特殊限制 | mem_limit: 512m, cpus: 1.0 |
| **崩溃影响** | 影响所有用户 | 仅影响当前脚本执行，自动重启 |

### 2.3 网络隔离

```yaml
networks:
  jingxin-network:      # 现有：backend ↔ postgres ↔ redis ↔ frontend
    driver: bridge
  script-runner-network: # 新增：backend ↔ script-runner（仅此二者）
    driver: bridge
    internal: true       # 无外网访问
```

`jingxin-script-runner` 只连接 `script-runner-network`，**无法访问**：
- postgres（数据库）
- redis（会话）
- milvus / neo4j（向量/图数据库）
- 外部网络（`internal: true`）

`jingxin-backend` 同时连接两个网络，作为唯一的调用方。

---

## 三、Sidecar 容器：script-runner

### 3.1 Dockerfile

```dockerfile
# Dockerfile.script-runner
FROM python:3.11-slim

# 非 root 用户
RUN useradd -m -u 1001 -s /bin/bash runner

# 系统依赖（字体，供 matplotlib 使用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

# Python 数据分析库
COPY requirements-script-runner.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-script-runner.txt \
    && rm /tmp/requirements-script-runner.txt

# 复制 runner 服务代码
COPY src/backend/script_runner_service/ /app/
WORKDIR /app

# 工作空间目录（脚本执行的临时目录）
RUN mkdir -p /workspace /tmp/scripts && chown -R runner:runner /workspace /tmp/scripts

USER runner
EXPOSE 8900

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8900"]
```

### 3.2 requirements-script-runner.txt

```
# HTTP 服务
fastapi>=0.104.0
uvicorn>=0.24.0

# 数据分析（Skill 脚本常用）
pandas>=2.1.0
numpy>=1.26.0
matplotlib>=3.8.0
seaborn>=0.13.0
openpyxl>=3.1.0
xlsxwriter>=3.1.0
scipy>=1.11.0

# 工具
jsonschema>=4.20.0
```

### 3.3 Runner 服务代码

```python
# src/backend/script_runner_service/server.py

"""
Skill 脚本执行 sidecar 服务。

接收来自 backend 的 HTTP 请求，在受限子进程中执行预定义脚本。
此服务运行在独立容器中，无数据库/Redis/API Key 访问权限。
"""

import asyncio
import json
import logging
import os
import resource
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("script-runner")

app = FastAPI(title="Jingxin Script Runner", docs_url=None, redoc_url=None)

# ── 配置 ──
MAX_TIMEOUT = int(os.getenv("SCRIPT_MAX_TIMEOUT", "120"))
DEFAULT_TIMEOUT = int(os.getenv("SCRIPT_DEFAULT_TIMEOUT", "30"))
MAX_MEMORY_MB = int(os.getenv("SCRIPT_MAX_MEMORY_MB", "256"))
MAX_OUTPUT_BYTES = 1024 * 1024  # 1MB
MAX_SCRIPT_SIZE = 512 * 1024    # 512KB

INTERPRETERS = {
    "python": ["python3", "-u"],
    "bash": ["bash"],
    "javascript": ["node"],
}

# 清洁环境变量 — 不泄露任何敏感信息
SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "en_US.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "MPLBACKEND": "Agg",  # matplotlib 非交互后端
}


class ExecuteRequest(BaseModel):
    script_content: str                        # 脚本代码（由 backend 从 Skill extra_files 读取后传入）
    script_name: str                           # 文件名（用于确定后缀和日志）
    language: str = "python"                   # python | bash | javascript
    params: Dict[str, Any] = {}                # 传递给脚本的参数（通过 stdin JSON）
    timeout: int = DEFAULT_TIMEOUT             # 超时（秒）
    resource_files: Optional[Dict[str, str]] = None  # 附加资源文件 {filename: content}


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: int


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    # ── 基础校验 ──
    if req.language not in INTERPRETERS:
        raise HTTPException(400, f"不支持的语言: {req.language}")
    if len(req.script_content) > MAX_SCRIPT_SIZE:
        raise HTTPException(400, f"脚本过大: {len(req.script_content)} > {MAX_SCRIPT_SIZE}")
    timeout = min(req.timeout, MAX_TIMEOUT)

    # ── 准备临时工作目录 ──
    work_dir = Path(tempfile.mkdtemp(prefix="skill_", dir="/workspace"))
    try:
        # 写入脚本文件
        script_path = work_dir / req.script_name
        script_path.write_text(req.script_content, encoding="utf-8")

        # 写入附加资源文件
        if req.resource_files:
            for fname, fcontent in req.resource_files.items():
                (work_dir / fname).write_text(fcontent, encoding="utf-8")

        # ── 执行 ──
        interpreter = INTERPRETERS[req.language]
        import time
        t0 = time.monotonic()

        result = await _execute_subprocess(
            cmd=[*interpreter, str(script_path)],
            stdin_data=json.dumps(req.params, ensure_ascii=False),
            timeout=timeout,
            cwd=str(work_dir),
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result["execution_time_ms"] = elapsed_ms

        return ExecuteResponse(**result)

    finally:
        # 清理临时目录
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)


async def _execute_subprocess(
    cmd: list, stdin_data: str, timeout: int, cwd: str
) -> Dict[str, Any]:
    """在受限子进程中执行命令。"""

    def _set_limits():
        mem_bytes = MAX_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=SAFE_ENV,
            preexec_fn=_set_limits,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=stdin_data.encode("utf-8")),
            timeout=timeout,
        )
        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES],
            "stderr": stderr_bytes.decode("utf-8", errors="replace")[:10240],
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"stdout": "", "stderr": f"执行超时（{timeout}秒）", "exit_code": -1}
    except Exception as e:
        logger.exception("subprocess execution failed")
        return {"stdout": "", "stderr": str(e), "exit_code": -1}
```

### 3.4 Docker Compose 配置

```yaml
# docker-compose.yml — 新增 script-runner 服务

  script-runner:
    build:
      context: .
      dockerfile: Dockerfile.script-runner
    container_name: jingxin-script-runner
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      SCRIPT_MAX_TIMEOUT: ${SKILL_SCRIPT_MAX_TIMEOUT:-120}
      SCRIPT_DEFAULT_TIMEOUT: ${SKILL_SCRIPT_TIMEOUT:-30}
      SCRIPT_MAX_MEMORY_MB: ${SKILL_SCRIPT_MAX_MEMORY:-256}
      # 注意：此容器没有 DATABASE_URL、API_KEY、REDIS_URL 等敏感变量
    mem_limit: 512m                     # 容器级内存硬上限
    cpus: 1.0                           # CPU 限制
    pids_limit: 128                     # 进程数限制
    read_only: true                     # 根文件系统只读
    tmpfs:
      - /tmp:size=256m                  # /tmp 可写，256MB 上限
      - /workspace:size=256m            # 工作空间可写，256MB 上限
    security_opt:
      - no-new-privileges:true          # 禁止提权
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8900/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    networks:
      - script-runner-network           # 仅此网络，不接入 jingxin-network
    restart: unless-stopped

# backend 服务新增网络
  backend:
    # ... 现有配置不变 ...
    networks:
      - jingxin-network
      - script-runner-network           # 新增：可调用 script-runner
    environment:
      # ... 现有变量不变 ...
      SKILL_SCRIPT_ENABLED: ${SKILL_SCRIPT_ENABLED:-false}
      SKILL_SCRIPT_RUNNER_URL: http://jingxin-script-runner:8900

# 网络定义
networks:
  jingxin-network:
    driver: bridge
  script-runner-network:                # 新增
    driver: bridge
    internal: true                      # 无外网访问
```

---

## 四、Backend 侧代码

### 4.1 Backend 客户端：script_runner.py

```python
# agent_skills/script_runner.py

"""
Skill 脚本执行客户端。

运行在 backend 容器中，负责：
1. 白名单校验（skill 存在 + 脚本已声明）
2. 参数 JSON Schema 校验
3. 通过 HTTP 调用 script-runner sidecar 执行

不负责实际脚本执行 — 执行在独立的 jingxin-script-runner 容器中进行。
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx
import jsonschema

from .loader import get_skill_loader

logger = logging.getLogger(__name__)

RUNNER_URL = os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900")
ENABLED = os.getenv("SKILL_SCRIPT_ENABLED", "false").lower() == "true"
DEFAULT_TIMEOUT = int(os.getenv("SKILL_SCRIPT_TIMEOUT", "30"))
MAX_TIMEOUT = int(os.getenv("SKILL_SCRIPT_MAX_TIMEOUT", "120"))


class SkillScriptError(Exception):
    """脚本执行相关的错误。"""
    pass


async def run_skill_script(
    skill_id: str,
    script_name: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    执行 Skill 中预定义的脚本。

    安全保证：
    1. 仅执行 executable_scripts 白名单中的脚本
    2. 参数经过 JSON Schema 校验
    3. 脚本在独立容器中执行（无 DB/Redis/API Key 访问）
    4. 参数通过 HTTP body JSON 传递（非命令行拼接）
    """
    if not ENABLED:
        raise SkillScriptError("脚本执行功能未启用 (SKILL_SCRIPT_ENABLED=false)")

    params = params or {}
    timeout = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)

    # ── Step 1: 白名单校验 ──
    loader = get_skill_loader()
    spec = loader.load_skill_full(skill_id)
    if spec is None:
        raise SkillScriptError(f"Skill '{skill_id}' 不存在或未启用")

    script_decl = _find_script_declaration(spec, script_name)
    if script_decl is None:
        allowed = [s.get("name") for s in getattr(spec, "executable_scripts", [])]
        raise SkillScriptError(
            f"脚本 '{script_name}' 不在 Skill '{skill_id}' 的白名单中。"
            f"可用脚本: {allowed}"
        )

    # ── Step 2: 参数校验 ──
    schema = script_decl.get("params_schema")
    if schema:
        try:
            jsonschema.validate(instance=params, schema=schema)
        except jsonschema.ValidationError as e:
            raise SkillScriptError(f"参数校验失败: {e.message}")

    # ── Step 3: 获取脚本内容 ──
    script_content = _get_script_content(loader, skill_id, script_name)

    # 获取同目录下的资源文件（非可执行文件）
    resource_files = _get_resource_files(loader, skill_id, script_name)

    # ── Step 4: 调用 sidecar 执行 ──
    language = script_decl.get("language", "python")

    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        try:
            resp = await client.post(
                f"{RUNNER_URL}/execute",
                json={
                    "script_content": script_content,
                    "script_name": script_name,
                    "language": language,
                    "params": params,
                    "timeout": timeout,
                    "resource_files": resource_files,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise SkillScriptError(f"脚本执行超时（{timeout}秒）")
        except httpx.HTTPStatusError as e:
            raise SkillScriptError(f"脚本执行失败: {e.response.text}")
        except httpx.ConnectError:
            raise SkillScriptError(
                "无法连接脚本执行服务 (jingxin-script-runner)，请检查容器是否运行"
            )


def _find_script_declaration(spec, script_name: str) -> Optional[Dict]:
    scripts = getattr(spec, "executable_scripts", [])
    for s in scripts:
        if s.get("name") == script_name:
            return s
    return None


def _get_script_content(loader, skill_id: str, script_name: str) -> str:
    """从 Skill 文件系统或 extra_files 中获取脚本内容。"""
    # 优先尝试文件系统
    skill_dir = loader.get_skill_dir(skill_id)
    from pathlib import Path
    script_path = Path(skill_dir) / script_name
    if script_path.is_file():
        return script_path.read_text(encoding="utf-8")

    # 回退到 extra_files
    extra = loader.get_extra_files(skill_id)
    if script_name in extra:
        return extra[script_name]

    raise SkillScriptError(f"脚本文件 '{script_name}' 不存在")


def _get_resource_files(
    loader, skill_id: str, script_name: str
) -> Optional[Dict[str, str]]:
    """获取 Skill 附带的非脚本资源文件。"""
    extra = loader.get_extra_files(skill_id)
    if not extra:
        return None
    # 排除脚本文件本身，只传递资源文件
    resources = {k: v for k, v in extra.items() if k != script_name}
    return resources or None
```

### 4.2 SKILL.md 格式扩展

在 frontmatter 中新增 `executable_scripts` 字段：

```yaml
---
name: data-analysis-helper
display_name: 数据分析助手
description: 对 CSV/Excel 文件进行统计分析和可视化
version: 1.0.0
tags: data-analysis,statistics
allowed_tools: retrieve_dataset_content run_skill_script

# ─── 新增：可执行脚本声明 ───
executable_scripts:
  - name: analyze_csv.py
    description: 对 CSV 文件进行描述性统计分析
    language: python
    timeout: 30
    params_schema:
      type: object
      properties:
        file_path:
          type: string
          description: 待分析的 CSV 文件路径
        columns:
          type: array
          items: { type: string }
          description: 要分析的列名（空=全部）
      required: [file_path]

  - name: generate_chart.py
    description: 根据数据生成统计图表
    language: python
    timeout: 60
    params_schema:
      type: object
      properties:
        file_path: { type: string }
        chart_type: { type: string, enum: [bar, line, pie, scatter] }
        x_column: { type: string }
        y_column: { type: string }
      required: [file_path, chart_type, x_column, y_column]
---
```

脚本文件通过 `extra_files` 存储（DB Skill）或直接放在 Skill 目录下（文件系统 Skill）：

```
agent_skills/skills/data-analysis-helper/
├── SKILL.md
├── analyze_csv.py
└── generate_chart.py
```

### 4.3 Skill 数据模型扩展

```python
# registry.py — AgentSkillSpec 新增字段

@dataclass(frozen=True)
class AgentSkillSpec:
    # ... 现有字段 ...
    executable_scripts: List[Dict[str, Any]] = field(default_factory=list)  # ← 新增
```

```python
# registry.py — frontmatter 解析扩展

def _parse_executable_scripts(frontmatter: dict) -> List[Dict[str, Any]]:
    """解析 executable_scripts 字段。"""
    raw = frontmatter.get("executable_scripts", [])
    if not isinstance(raw, list):
        return []
    scripts = []
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        scripts.append({
            "name": item["name"],
            "description": item.get("description", ""),
            "language": item.get("language", "python"),
            "timeout": min(item.get("timeout", 30), 120),
            "params_schema": item.get("params_schema"),
        })
    return scripts
```

### 4.4 注册到 Agent 工具链

```python
# core/llm/agent_factory.py — build_agent_with_tools() 中新增

from agent_skills.script_runner import run_skill_script, SkillScriptError

def _register_run_skill_script(toolkit, enabled_skill_ids: list[str]):
    """注册 run_skill_script 工具到 AgentScope Toolkit。"""
    loader = get_skill_loader()

    # 收集所有启用 Skill 中声明的可执行脚本
    available_scripts = {}
    for sid in enabled_skill_ids:
        spec = loader.load_skill_full(sid)
        if spec and getattr(spec, "executable_scripts", []):
            available_scripts[sid] = spec.executable_scripts

    if not available_scripts:
        return  # 没有任何可执行脚本，不注册工具

    # 构建描述文档供 LLM 理解
    script_docs = []
    for sid, scripts in available_scripts.items():
        for s in scripts:
            params_desc = ""
            if s.get("params_schema", {}).get("properties"):
                props = s["params_schema"]["properties"]
                params_desc = ", ".join(
                    f'{k}: {v.get("type", "any")} - {v.get("description", "")}'
                    for k, v in props.items()
                )
            script_docs.append(
                f"- skill_id='{sid}', script_name='{s['name']}': "
                f"{s['description']} (参数: {params_desc or '无'})"
            )

    doc_str = "\n".join(script_docs)

    @toolkit.tool
    async def run_skill_script_tool(
        skill_id: str,
        script_name: str,
        params: str = "{}",
    ) -> str:
        f"""执行 Skill 中预定义的脚本（在隔离容器中运行）。仅以下脚本可用：

{doc_str}

Args:
    skill_id: Skill ID
    script_name: 脚本文件名
    params: JSON 格式的参数字符串
"""
        try:
            parsed_params = json.loads(params)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"参数 JSON 解析失败: {e}"})

        try:
            result = await run_skill_script(
                skill_id=skill_id,
                script_name=script_name,
                params=parsed_params,
            )
            return json.dumps(result, ensure_ascii=False)
        except SkillScriptError as e:
            return json.dumps({"error": str(e)})
```

### 4.5 脚本编写规范

```python
#!/usr/bin/env python3
"""Skill 脚本模板。

输入：stdin 接收 JSON 参数
输出：stdout 输出结果（纯文本或 JSON）
错误：stderr 输出错误信息
退出码：0=成功，非0=失败
"""
import sys
import json


def main():
    params = json.loads(sys.stdin.read())

    # ── 业务逻辑 ──
    result = {"key": "value"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

**规则：**
1. 参数只从 `stdin` 读取 JSON（防注入）
2. 输出到 `stdout`，错误到 `stderr`
3. 不使用 `subprocess`、`os.system`、`eval`、`exec`
4. 不依赖网络（容器内无外网）
5. 工作目录为临时目录，可读取同目录下的资源文件

---

## 五、安全设计

### 5.1 防御层次

```
┌───────────────────────────────────────────────────┐
│  Layer 4: 容器级隔离（sidecar 架构）               │
│    ✓ 独立容器，不可访问 DB/Redis/API Key           │
│    ✓ 独立网络 (internal: true)，无外网             │
│    ✓ read_only 根文件系统                          │
│    ✓ 容器崩溃不影响 backend                        │
├───────────────────────────────────────────────────┤
│  Layer 3: 白名单 + Schema 校验（backend 侧）       │
│    ✓ 仅执行 executable_scripts 声明的脚本          │
│    ✓ 参数经 JSON Schema 校验                       │
│    ✓ 脚本来源可审计（Skill 作者/管理员）            │
├───────────────────────────────────────────────────┤
│  Layer 2: 进程隔离（sidecar 侧）                   │
│    ✓ 清洁环境变量（无 API_KEY、DATABASE_URL）       │
│    ✓ stdin JSON 传参（不拼接命令行）                │
│    ✓ 临时工作目录，执行后清理                       │
├───────────────────────────────────────────────────┤
│  Layer 1: OS + 容器资源限制                        │
│    ✓ mem_limit: 512m（容器级）                     │
│    ✓ RLIMIT_AS: 256m（进程级，防单脚本吃满）       │
│    ✓ RLIMIT_NPROC: 32（防 fork bomb）              │
│    ✓ pids_limit: 128（容器级）                     │
│    ✓ timeout: 可配置（默认 30s，上限 120s）        │
│    ✓ no-new-privileges: true                       │
└───────────────────────────────────────────────────┘
```

### 5.2 安全性对比

| 攻击向量 | 方案 A (backend 内) | 方案 B (sidecar) |
|---|---|---|
| 脚本读取 DB 密码 | 可读（共享 env） | **不可读**（容器无此 env） |
| 脚本访问 postgres | 可访问（同网络） | **不可访问**（不同网络） |
| 脚本访问外网 | 可访问 | **不可访问**（internal network） |
| 脚本崩溃影响 backend | 可能（共享内存/CPU） | **不影响**（独立容器） |
| 脚本写入 backend 文件 | 可写（共享文件系统） | **不可写**（独立文件系统 + read_only） |

### 5.3 参数注入防御

**参数永远通过 HTTP body JSON → stdin JSON 传递，绝不拼接到命令行。**

```
backend                          script-runner
   │                                  │
   │  POST /execute                   │
   │  {"params": {"file": "x.csv"}}   │
   │  ─────────────────────────────→  │
   │                                  │  stdin ← json.dumps(params)
   │                                  │  脚本内: params = json.loads(stdin)
   │                                  │  params["file"] 只是字符串值
   │  ←─────────────────────────────  │
   │  {"stdout": "...", "exit_code":0}│
```

### 5.4 Skill 脚本审核清单

```
□ 不包含 subprocess / os.system / os.popen
□ 不包含 eval / exec / __import__
□ 不包含 socket / requests / urllib
□ 参数从 stdin 读取，不从 sys.argv 读取
□ 输出到 stdout，错误到 stderr
□ 有合理的 params_schema 约束参数类型
□ 无硬编码路径（使用工作目录相对路径）
```

---

## 六、与现有架构的集成

### 6.1 新增/修改的文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `Dockerfile.script-runner` | **新增** | sidecar 容器镜像 |
| `requirements-script-runner.txt` | **新增** | sidecar 依赖 |
| `src/backend/script_runner_service/server.py` | **新增** | sidecar HTTP 服务（~150 行） |
| `agent_skills/script_runner.py` | **新增** | backend 侧客户端（~120 行） |
| `agent_skills/registry.py` | 修改 | 新增 `executable_scripts` 字段 + 解析 |
| `core/llm/agent_factory.py` | 修改 | 注册 `run_skill_script` 工具 |
| `docker-compose.yml` | 修改 | 新增 `script-runner` 服务 + 网络 |
| `api/routes/v1/admin_skills.py` | 修改 | 上传 Skill 时校验 `executable_scripts` |

### 6.2 不需要变更的部分

| 模块 | 原因 |
|---|---|
| `configs/mcp_config.py` | 不需要新的 MCP 服务器 |
| `configs/catalog.json` | `run_skill_script` 是内置工具 |
| 前端组件 | 脚本输出通过现有 `tool_result` SSE 事件渲染 |
| 数据库 schema | `admin_skills.extra_files` 已支持附件存储 |
| Dockerfile (backend) | backend 镜像不需要安装数据分析库 |

### 6.3 请求流

```
Browser → Nginx → FastAPI (jingxin-backend)
  → routing/workflow.py
    → AgentScope ReActAgent
      → Toolkit.run_skill_script_tool()
        → agent_skills/script_runner.py         # backend 侧：校验
          ① 白名单校验 ✓
          ② Schema 校验 ✓
          ③ HTTP POST → jingxin-script-runner:8900/execute
        → script_runner_service/server.py       # sidecar 侧：执行
          ④ 写脚本到 /workspace/skill_xxx/
          ⑤ subprocess (ulimit + timeout + 清洁 env)
          ⑥ 收集 stdout/stderr
          ← HTTP 200 JSON
        ← result dict
      ← Agent 继续推理
    → SSE stream (tool_result 事件)
  ← 前端展示
```

### 6.4 数据流

```
┌─ Skill 管理员 ────────────────────────────────────┐
│                                                    │
│  编写 SKILL.md + analyze_csv.py                    │
│  通过 Admin API 上传                               │
│                                                    │
│  → DB: admin_skills.extra_files = {脚本内容}       │
└────────────────────────────────────────────────────┘
                      │
                      ▼
┌─ jingxin-backend ─────────────────────────────────┐
│                                                    │
│  用户: "分析这个 CSV"                              │
│  → Agent 选择 Skill                                │
│  → run_skill_script(                               │
│      skill_id, "analyze_csv.py",                   │
│      params={"file_path": "..."}                   │
│    )                                               │
│  → 白名单 ✓ → Schema ✓                            │
│  → 从 DB 读取脚本内容                              │
│  → HTTP POST → script-runner                       │
│                   │                                │
└───────────────────│────────────────────────────────┘
                    │ script-runner-network (internal)
                    ▼
┌─ jingxin-script-runner ───────────────────────────┐
│                                                    │
│  无 DB / 无 Redis / 无 API Key / 无外网            │
│                                                    │
│  ④ 脚本写入 /workspace/skill_xxx/                 │
│  ⑤ subprocess: python3 analyze_csv.py             │
│     stdin ← {"file_path": "..."}                  │
│     stdout → {"row_count": 100, "mean": 42.3}     │
│  ⑥ 清理临时目录                                   │
│  ← HTTP 200 {"stdout": "...", "exit_code": 0}     │
│                                                    │
└────────────────────────────────────────────────────┘
```

---

## 七、环境变量

```env
# .env

# 脚本执行开关
SKILL_SCRIPT_ENABLED=true                              # 总开关（默认 false）
SKILL_SCRIPT_RUNNER_URL=http://jingxin-script-runner:8900  # sidecar 地址

# 资源限制
SKILL_SCRIPT_TIMEOUT=30                                # 默认超时（秒）
SKILL_SCRIPT_MAX_TIMEOUT=120                           # 最大超时（秒）
SKILL_SCRIPT_MAX_MEMORY=256                            # 进程内存上限（MB）
```

---

## 八、实施计划

| 阶段 | 时间 | 交付物 | 说明 |
|---|---|---|---|
| **Phase 1.1** | 2 天 | sidecar 服务 + Dockerfile | `server.py` + Docker 构建验证 |
| **Phase 1.2** | 1-2 天 | `registry.py` 扩展 | 解析 `executable_scripts` frontmatter |
| **Phase 1.3** | 2 天 | backend 客户端 + 工具注册 | `script_runner.py` + agent_factory 集成 |
| **Phase 1.4** | 1 天 | docker-compose + 网络隔离 | 集成测试：backend → sidecar → 脚本执行 |
| **Phase 1.5** | 1 天 | 示例 Skill | 编写 1-2 个带脚本的 Skill + selftest |
| **Phase 2**（可选） | 1 周 | 增强 | 脚本静态分析 + 执行审计日志 + 管理后台 |

**总工期：约 1.5 周。**

---

## 九、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| Skill 作者写了有害脚本 | 低 | 中 | Admin 审核 + 容器隔离兜底 + Phase 2 静态分析 |
| LLM 构造恶意参数 | 中 | 低 | JSON Schema 校验 + stdin 传参 |
| sidecar 容器 OOM | 中 | 低 | mem_limit 512m + RLIMIT_AS 256m，崩溃自动重启 |
| sidecar 不可用 | 低 | 中 | healthcheck + restart:unless-stopped + 友好错误提示 |
| 脚本需要网络访问 | — | — | 当前不支持；如需要，可创建白名单 egress 规则 |

### 未来升级路径

1. **需要执行 LLM 生成的代码** → 将 sidecar 升级为 gVisor runtime
2. **需要更强隔离** → sidecar 改为按需创建临时容器
3. **需要 GPU** → 添加 GPU sidecar 容器

---

## 十、示例 Skill：数据分析助手

### SKILL.md

```yaml
---
name: data-analysis-helper
display_name: 数据分析助手
description: 对用户上传的 CSV 或 Excel 文件进行描述性统计分析，输出关键指标
version: 1.0.0
tags: data-analysis,statistics,jingxin-scenario
allowed_tools: run_skill_script view_text_file

executable_scripts:
  - name: analyze_csv.py
    description: 对 CSV/Excel 文件进行描述性统计（均值、中位数、标准差等）
    language: python
    timeout: 30
    params_schema:
      type: object
      properties:
        file_path:
          type: string
          description: 文件路径
        columns:
          type: array
          items: { type: string }
          description: 要分析的列名列表，空数组表示全部列
        output_format:
          type: string
          enum: [json, markdown]
          description: 输出格式
      required: [file_path]
---

# 数据分析助手

帮助用户快速了解数据文件的基本统计特征。

## Instructions
1. 当用户上传 CSV 或 Excel 文件并请求分析时，调用 `run_skill_script` 执行 `analyze_csv.py`
2. 将文件路径作为 `file_path` 参数传入
3. 根据脚本返回的 JSON 结果，用自然语言向用户解释关键发现
4. 如用户指定了特定列，在 `columns` 参数中传入列名列表

## Inputs
- 用户上传的 CSV 或 Excel 文件
- 可选：要分析的列名

## Outputs
- 描述性统计结果（计数、均值、标准差、最小/最大值、中位数）
- 关键发现的自然语言解读
```

### analyze_csv.py

```python
#!/usr/bin/env python3
"""CSV/Excel 描述性统计分析。

输入 (stdin JSON): file_path, columns?, output_format?
输出 (stdout): JSON 或 Markdown
"""
import sys
import json

try:
    import pandas as pd
except ImportError:
    print(json.dumps({"error": "pandas 未安装"}))
    sys.exit(1)


def main():
    params = json.loads(sys.stdin.read())
    file_path = params["file_path"]
    columns = params.get("columns", [])
    output_format = params.get("output_format", "json")

    # 读取
    if file_path.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file_path)
    else:
        df = pd.read_csv(file_path)

    # 筛选列
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            print(json.dumps({"error": f"列不存在: {missing}", "available": list(df.columns)}))
            sys.exit(1)
        df = df[columns]

    # 统计
    result = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "statistics": df.describe(include="all").fillna("").to_dict(),
        "null_counts": df.isnull().sum().to_dict(),
    }

    if output_format == "markdown":
        print(df.describe(include="all").to_markdown())
    else:
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
```

---

## 附录 A：行业调研（通用代码沙盒方案）

> 以下为前期调研的通用沙盒方案，供未来扩展参考。当前 Skill 脚本执行不需要这些方案。

### A.1 行业主流沙盒方案对比

| 方案 | 隔离级别 | 启动时间 | 自托管 | 适用场景 |
|---|---|---|---|---|
| **E2B** | Firecracker microVM (KVM) | <200ms | 有（复杂） | 生产级 AI Agent |
| **Modal** | gVisor (用户态内核) | 亚秒 | 不支持 | 需 GPU 场景 |
| **Daytona** | 容器 + 加固 | <90ms | 有（K8s） | 有状态沙盒 |
| **gVisor** | 用户态内核 | 毫秒级 | 开源 | 接入 Docker 最简单 |
| **Firecracker** | KVM microVM | ~125ms | 开源 | 最强隔离，AWS Lambda 同款 |
| **Coze (Deno+Pyodide)** | Deno 权限模型 + Wasm | 即时 | 开源 | 无 Docker 轻量沙盒 |
| **nsjail / bubblewrap** | Linux namespace | 极快 | 开源 | 轻量级进程沙盒 |

### A.2 关键产品实现

**CoPaw（阿里 AgentScope）：** 内置 `bash_exec` 工具，预执行参数扫描层检测危险命令。

**Claude Cowork（Anthropic）：** MCP connector → Computer Use。VM 内执行。

**Anthropic 沙盒体系：** CLI 级 bubblewrap / API 级容器 / Auto 模式 Sonnet 分类器。

**OpenAI Code Interpreter：** gVisor 容器 on K8s。

**Coze 代码节点：** 开源版 Deno + Pyodide。SaaS 版 Docker + vArmor。

### A.3 vArmor 评估

vArmor 将 AppArmor + BPF + Seccomp 抽象为统一策略层，但**强依赖 Kubernetes**。Docker Compose 部署可用原生 `security_opt` + AppArmor/Seccomp profile 复刻约 80-90% 能力。

---

## 附录 B：参考资料

- [E2B](https://e2b.dev/) / [GitHub](https://github.com/e2b-dev/E2B)
- [gVisor](https://gvisor.dev/) / [GitHub](https://github.com/google/gvisor)
- [Firecracker](https://github.com/firecracker-microvm/firecracker)
- [Anthropic Sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing) / [sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)
- [CoPaw](https://github.com/agentscope-ai/CoPaw)
- [Coze Studio](https://github.com/coze-dev/coze-studio)
- [ByteDance SandboxFusion](https://github.com/bytedance/SandboxFusion)
- [vArmor](https://www.varmor.org/) / [GitHub](https://github.com/bytedance/vArmor)
- [code-sandbox-mcp](https://github.com/Automata-Labs-team/code-sandbox-mcp)
- [OWASP AI Agent Security Top 10 (2026)](https://owasp.org/www-project-ai-agent-security/)
