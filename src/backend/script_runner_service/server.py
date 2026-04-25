"""
Skill 脚本执行 sidecar 服务。

接收来自 backend 的 HTTP 请求，在受限子进程中执行预定义脚本。
此服务运行在独立容器中，无数据库/Redis/API Key 访问权限。
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
import resource
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# ── 文件产物捕获 ──
MAX_FILE_SIZE = 10 * 1024 * 1024      # 10MB per file
MAX_TOTAL_FILE_SIZE = 20 * 1024 * 1024  # 20MB total
MAX_FILE_COUNT = 20
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".csv", ".xlsx", ".xls", ".json", ".txt", ".pdf",
    ".html", ".htm", ".docx", ".pptx", ".md",
}

# 清洁环境变量 — 不泄露任何敏感信息
SAFE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "XDG_CACHE_HOME": "/tmp/.cache",
    "FONTCONFIG_PATH": "/etc/fonts",
    "FONTCONFIG_FILE": "/etc/fonts/fonts.conf",
    "LANG": "en_US.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "MPLBACKEND": "Agg",  # matplotlib 非交互后端
    "OPENBLAS_NUM_THREADS": "1",  # 防止 OpenBLAS 分配大量线程内存
    "OMP_NUM_THREADS": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",  # 禁止 dotnet 遥测
    "DOTNET_NOLOGO": "1",  # 抑制 dotnet 启动横幅
    "DOTNET_EnableDiagnostics": "0",  # 禁止 dotnet 创建诊断管道/core dump 文件
}
for _key in ("NODE_PATH", "PLAYWRIGHT_BROWSERS_PATH"):
    _val = os.getenv(_key)
    if _val:
        SAFE_ENV[_key] = _val

# Pre-create fontconfig cache dir once (avoids per-request mkdir)
Path("/tmp/.cache/fontconfig").mkdir(parents=True, exist_ok=True)


class ExecuteRequest(BaseModel):
    script_content: str
    script_name: str
    language: str = "python"
    params: Dict[str, Any] = {}
    timeout: int = DEFAULT_TIMEOUT
    resource_files: Optional[Dict[str, str]] = None
    input_files: Optional[Dict[str, str]] = None
    input_files_b64: Optional[Dict[str, str]] = None


class FileOutput(BaseModel):
    name: str
    size: int
    content_b64: str
    mime_type: str


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    execution_time_ms: int
    files: List[FileOutput] = []


def _validate_filename(name: str) -> None:
    """Reject filenames with path traversal components."""
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        raise HTTPException(400, f"不安全的文件名: {name}")


def _validate_user_id(user_id: str) -> None:
    """Reject user_id values that could cause path traversal."""
    if not user_id or "/" in user_id or "\\" in user_id or ".." in user_id:
        raise HTTPException(400, f"不安全的 user_id: {user_id!r}")


class StageFile(BaseModel):
    name: str
    content_b64: str


class StageRequest(BaseModel):
    user_id: str
    files: List[StageFile]


class StageResponse(BaseModel):
    staged: List[Dict[str, str]]  # [{"name": ..., "path": ...}]


@app.post("/stage", response_model=StageResponse)
async def stage_files(req: StageRequest):
    """将文件暂存到 /workspace/myspace/{user_id}/ 目录，供后续代码执行直接按路径读取。"""
    _validate_user_id(req.user_id)
    base_dir = Path(f"/workspace/myspace/{req.user_id}")
    base_dir.mkdir(parents=True, exist_ok=True)

    staged = []
    for f in req.files:
        _validate_filename(f.name)
        try:
            content = base64.b64decode(f.content_b64)
        except Exception:
            raise HTTPException(400, f"文件 {f.name} 的 base64 内容无效")
        dest = base_dir / f.name
        dest.write_bytes(content)
        staged.append({"name": f.name, "path": str(dest)})

    return StageResponse(staged=staged)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _seed_text_files(
    work_dir: Path,
    file_dict: Optional[Dict[str, str]],
    seeded_files: set,
) -> None:
    """Write text files into work_dir and register them in seeded_files."""
    if not file_dict:
        return
    for fname, fcontent in file_dict.items():
        fpath = work_dir / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(fcontent, encoding="utf-8")
        seeded_files.add(str(fpath.relative_to(work_dir)))


def _seed_b64_files(
    work_dir: Path,
    file_dict: Optional[Dict[str, str]],
    seeded_files: set,
) -> None:
    """Write base64-decoded binary files into work_dir and register them in seeded_files."""
    if not file_dict:
        return
    for fname, b64content in file_dict.items():
        fpath = work_dir / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(base64.b64decode(b64content))
        seeded_files.add(str(fpath.relative_to(work_dir)))


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    # ── 基础校验 ──
    if req.language not in INTERPRETERS:
        raise HTTPException(400, f"不支持的语言: {req.language}")
    if len(req.script_content) > MAX_SCRIPT_SIZE:
        raise HTTPException(400, f"脚本过大: {len(req.script_content)} > {MAX_SCRIPT_SIZE}")
    timeout = min(req.timeout, MAX_TIMEOUT)

    # ── 文件名安全校验（防止路径穿越） ──
    _validate_filename(req.script_name)
    for file_dict in filter(None, [req.resource_files, req.input_files, req.input_files_b64]):
        for fname in file_dict:
            _validate_filename(fname)

    # ── 准备临时工作目录 ──
    work_dir = Path(tempfile.mkdtemp(prefix="skill_", dir="/workspace"))
    seeded_files: set[str] = set()
    # Snapshot existing files in /workspace/ root before execution
    _pre_existing_root_files: set = set()
    try:
        for _f in Path("/workspace").iterdir():
            if _f.is_file():
                _pre_existing_root_files.add(_f.name)
    except Exception:
        pass
    try:
        # 写入脚本文件
        script_path = work_dir / req.script_name
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(req.script_content, encoding="utf-8")

        # 写入资源文件和输入文件（input_files 在 resource_files 之后，同名时覆盖）
        _seed_text_files(work_dir, req.resource_files, seeded_files)
        _seed_text_files(work_dir, req.input_files, seeded_files)
        _seed_b64_files(work_dir, req.input_files_b64, seeded_files)

        # ── 执行 ──
        interpreter = INTERPRETERS[req.language]
        t0 = time.monotonic()

        # Support CLI args: params._args list is appended to command line
        cli_args: list[str] = []
        stdin_params = dict(req.params)
        if "_args" in stdin_params:
            raw_args = stdin_params.pop("_args")
            if isinstance(raw_args, list):
                cli_args = [str(a) for a in raw_args]

        result = await _execute_subprocess(
            cmd=[*interpreter, str(script_path), *cli_args],
            stdin_data=json.dumps(stdin_params, ensure_ascii=False),
            timeout=timeout,
            cwd=str(work_dir),
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result["execution_time_ms"] = elapsed_ms

        # ── 扫描生成的文件产物 ──
        # LLM 生成的代码可能写到 work_dir（相对路径）或 /workspace/（绝对路径）
        # 需要同时扫描两个位置
        generated_files: List[dict] = []
        total_size = 0
        seen_names: set = set()
        # 记录执行前 /workspace/ 根目录已有的文件，避免误采集
        workspace_root = Path("/workspace")

        def _collect_file(fpath: Path) -> bool:
            """Try to collect a file. Returns True if collected."""
            nonlocal total_size
            if not fpath.is_file():
                return False
            if fpath == script_path:
                return False
            if fpath.is_relative_to(work_dir):
                rel_path = str(fpath.relative_to(work_dir))
            else:
                rel_path = ""
            if rel_path and rel_path in seeded_files:
                return False
            if fpath.suffix.lower() not in ALLOWED_EXTENSIONS:
                return False
            if fpath.name in seen_names:
                return False
            fsize = fpath.stat().st_size
            if fsize == 0 or fsize > MAX_FILE_SIZE:
                return False
            if total_size + fsize > MAX_TOTAL_FILE_SIZE:
                return False
            if len(generated_files) >= MAX_FILE_COUNT:
                return False
            mime, _ = mimetypes.guess_type(str(fpath))
            with open(fpath, "rb") as fh:
                content_b64 = base64.b64encode(fh.read()).decode("ascii")
            generated_files.append({
                "name": fpath.name,
                "size": fsize,
                "content_b64": content_b64,
                "mime_type": mime or "application/octet-stream",
            })
            seen_names.add(fpath.name)
            total_size += fsize
            return True

        try:
            # 1) Scan work_dir (relative path outputs)
            for fpath in sorted(work_dir.rglob("*")):
                _collect_file(fpath)

            # 2) Scan /workspace/ root (absolute path outputs like /workspace/output.csv)
            #    Only collect NEW files (not pre-existing, not inside work_dir)
            for fpath in sorted(workspace_root.iterdir()):
                if fpath.is_dir():
                    continue
                if fpath.name in _pre_existing_root_files:
                    continue
                _collect_file(fpath)
        except Exception as e:
            logger.warning("file scan error: %s", e)

        result["files"] = generated_files

        return ExecuteResponse(**result)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        # Clean up files written to /workspace/ root by the script
        try:
            for _f in Path("/workspace").iterdir():
                if _f.is_file() and _f.name not in _pre_existing_root_files:
                    _f.unlink(missing_ok=True)
        except Exception:
            pass


async def _execute_subprocess(
    cmd: list, stdin_data: str, timeout: int, cwd: str
) -> Dict[str, Any]:
    """在受限子进程中执行命令。"""

    def _set_limits():
        # 不限制 RLIMIT_AS（虚拟地址空间）：mmap 映射 .so 共享库需要大量虚拟地址空间，
        # 256MB 会导致 lxml/numpy 等 C 扩展报 "failed to map segment from shared object"。
        # 不限制 RLIMIT_FSIZE：.NET runtime 启动时内部文件操作会触发 SIGXFSZ。
        # 实际磁盘使用由 Docker tmpfs size 和 mem_limit 在容器层面控制。
        nproc_limit = 128 if cmd and cmd[0] in {"node", "bash"} else 64
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, nproc_limit))

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
