"""
Skill 脚本执行客户端。

运行在 backend 容器中，负责：
1. 白名单校验（skill 存在 + 脚本已声明）
2. 参数 JSON Schema 校验
3. 通过 HTTP 调用 script-runner sidecar 执行

不负责实际脚本执行 — 执行在独立的 jingxin-script-runner 容器中进行。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import jsonschema

from core.infra import log_writer

from .loader import get_skill_loader

logger = logging.getLogger(__name__)


class SkillScriptError(Exception):
    """脚本执行相关的错误。"""
    pass


def _config() -> Dict[str, Any]:
    """Read script-runner config from env (per-call, not cached at import)."""
    return {
        "enabled": os.getenv("SKILL_SCRIPT_ENABLED", "false").lower() == "true",
        "url": os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900"),
        "timeout": int(os.getenv("SKILL_SCRIPT_TIMEOUT", "30")),
        "max_timeout": int(os.getenv("SKILL_SCRIPT_MAX_TIMEOUT", "120")),
    }


def _resolve_timeout(
    cfg: Dict[str, Any],
    script_decl: Dict[str, Any],
    requested_timeout: Optional[int],
) -> int:
    """Resolve effective timeout with priority: request > script declaration > env default."""
    default_timeout = int(cfg["timeout"])
    declared_timeout = int(script_decl.get("timeout") or default_timeout)
    effective_timeout = requested_timeout or declared_timeout or default_timeout
    return min(effective_timeout, int(cfg["max_timeout"]))


MAX_INPUT_FILE_SIZE = 512 * 1024      # 512KB per file
MAX_INPUT_FILES_TOTAL = 1024 * 1024   # 1MB total


async def run_skill_script(
    skill_id: str,
    script_name: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
    input_files: Optional[Dict[str, str]] = None,
    input_files_b64: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """执行 Skill 中预定义的脚本。

    安全保证：
    1. 仅执行 executable_scripts 白名单中的脚本
    2. 参数经过 JSON Schema 校验
    3. 脚本在独立容器中执行（无 DB/Redis/API Key 访问）
    4. 参数通过 HTTP body JSON 传递（非命令行拼接）
    """
    cfg = _config()
    if not cfg["enabled"]:
        raise SkillScriptError("脚本执行功能未启用 (SKILL_SCRIPT_ENABLED=false)")

    params = params or {}

    # ── Step 1: 白名单校验 ──
    loader = get_skill_loader()
    spec = loader.load_skill_full(skill_id)
    if spec is None:
        raise SkillScriptError(f"Skill '{skill_id}' 不存在或未启用")

    script_decl = _find_script_declaration(spec, script_name)
    if script_decl is None:
        allowed = [s.get("name") for s in spec.executable_scripts]
        raise SkillScriptError(
            f"脚本 '{script_name}' 不在 Skill '{skill_id}' 的白名单中。"
            f"可用脚本: {allowed}"
        )
    canonical_script_name = script_decl.get("name", script_name)
    timeout = _resolve_timeout(cfg, script_decl, timeout)

    # ── Step 2: 参数校验 ──
    schema = script_decl.get("params_schema")
    input_mode = script_decl.get("input_mode", "stdin_json")
    if schema and not (input_mode == "cli_args" and "_args" in params):
        try:
            jsonschema.validate(instance=params, schema=schema)
        except jsonschema.ValidationError as e:
            raise SkillScriptError(f"参数校验失败: {e.message}")

    # ── Step 3: 获取脚本内容 + 资源文件 (single extra_files fetch) ──
    extra_files = loader.get_extra_files(skill_id)
    script_content = _get_script_content(loader, skill_id, canonical_script_name, extra_files)
    resource_files = _get_resource_files(extra_files, canonical_script_name)

    # ── Step 3.5: 输入文件大小校验 ──
    if input_files:
        total = 0
        for fname, fcontent in input_files.items():
            fsize = len(fcontent.encode("utf-8"))
            if fsize > MAX_INPUT_FILE_SIZE:
                raise SkillScriptError(
                    f"输入文件 '{fname}' 过大: {fsize} bytes > {MAX_INPUT_FILE_SIZE} bytes"
                )
            total += fsize
        if total > MAX_INPUT_FILES_TOTAL:
            raise SkillScriptError(
                f"输入文件总大小过大: {total} bytes > {MAX_INPUT_FILES_TOTAL} bytes"
            )

    # ── Step 4: 调用 sidecar 执行 ──
    language = script_decl.get("language", "python")
    runner_url = cfg["url"]

    # For cli_args mode: convert params to _args list for the sidecar
    send_params = dict(params)
    if input_mode == "cli_args":
        send_params = _params_to_cli_args(params, script_decl)

    # 30s margin covers sidecar overhead (cold start, base64 encode, transfer);
    # retry once on ReadTimeout to ride over intermittent stalls.
    http_timeout = timeout + 30
    request_body = {
        "script_content": script_content,
        "script_name": canonical_script_name,
        "language": language,
        "params": send_params,
        "timeout": timeout,
        "resource_files": resource_files,
        "input_files": input_files,
        "input_files_b64": input_files_b64,
    }

    _skill_start_monotonic = time.monotonic()
    _skill_started_at = datetime.now(timezone.utc)
    _skill_log_id: Optional[str] = None

    async def _emit(status: str, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        nonlocal _skill_log_id
        try:
            _skill_log_id = await log_writer.write_skill_call({
                "skill_id": skill_id,
                "skill_name": getattr(spec, "name", skill_id),
                "skill_version": getattr(spec, "version", None),
                "skill_source": getattr(spec, "source", None),
                "invocation_type": "run_script",
                "script_name": canonical_script_name,
                "script_language": language,
                "script_args": send_params,
                "script_stdin": json.dumps(params, ensure_ascii=False) if params else None,
                "script_stdout": (payload or {}).get("stdout"),
                "script_stderr": (payload or {}).get("stderr"),
                "exit_code": (payload or {}).get("exit_code"),
                "status": status,
                "error_message": error,
                "duration_ms": int((time.monotonic() - _skill_start_monotonic) * 1000),
                "started_at": _skill_started_at,
            })
        except Exception:  # noqa: BLE001
            logger.debug("skill_call log write failed", exc_info=True)

    last_exc: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        for attempt in range(2):
            try:
                resp = await client.post(f"{runner_url}/execute", json=request_body)
                resp.raise_for_status()
                result = resp.json()
                await _emit(
                    "success" if not result.get("stderr") or result.get("exit_code", 0) == 0 else "failed",
                    result,
                    None,
                )
                return result
            except httpx.ReadTimeout as e:
                last_exc = e
                logger.warning(
                    "[script_runner] ReadTimeout skill=%s script=%s attempt=%d/2 (http_timeout=%ds)",
                    skill_id, canonical_script_name, attempt + 1, http_timeout,
                )
                continue
            except httpx.TimeoutException as e:
                await _emit("timeout", None, f"{type(e).__name__}: {e}")
                raise SkillScriptError(f"脚本执行超时（{timeout}秒, {type(e).__name__}）") from e
            except httpx.HTTPStatusError as e:
                await _emit("failed", None, e.response.text[:500] if e.response is not None else str(e))
                raise SkillScriptError(f"脚本执行失败: {e.response.text}") from e
            except httpx.ConnectError as e:
                await _emit("failed", None, str(e))
                raise SkillScriptError(
                    "无法连接脚本执行服务 (jingxin-script-runner)，请检查容器是否运行"
                ) from e

    await _emit("timeout", None, f"{type(last_exc).__name__ if last_exc else 'ReadTimeout'}")
    raise SkillScriptError(
        f"脚本执行读取超时（{http_timeout}秒，已重试）: {type(last_exc).__name__ if last_exc else 'ReadTimeout'}"
    )


def _params_to_cli_args(params: Dict[str, Any], script_decl: Dict) -> Dict[str, Any]:
    """Convert params dict to _args list for CLI-mode scripts.

    Conversion rules (in order):
    1. ``_args`` key → direct passthrough (already CLI args)
    2. ``command`` / ``subcommand`` → leading positional arg
    3. Keys starting with ``--`` or ``-`` → flag args
       - bool True → flag only (``--check-token``)
       - other values → flag + value (``--set-token xxx``)
    4. Keys in ``params_schema.required`` → positional args (in schema order)
    5. Remaining keys → ``--key value`` flags

    Examples::

        {"query": "茅台股价"}              → ["茅台股价"]
        {"--set-token": "sk_abc"}          → ["--set-token", "sk_abc"]
        {"--check-token": true}            → ["--check-token"]
        {"_args": ["--set-token", "abc"]}  → ["--set-token", "abc"]

    Non-``_args`` keys are preserved in the returned dict so the sidecar can
    forward them to the script as stdin JSON (e.g. minimax-docx's ``content``
    body payload). Dict/list values are JSON-serialized via ``_stringify_arg``
    so downstream parsers receive valid JSON rather than Python repr.
    """
    if "_args" in params:
        raw = params["_args"]
        if isinstance(raw, list):
            forwarded = {k: v for k, v in params.items() if k != "_args"}
            forwarded["_args"] = [_stringify_arg(a) for a in raw]
            return forwarded

    schema = script_decl.get("params_schema") or {}
    required_keys = schema.get("required", [])

    args: list[str] = []
    used_keys: set = set()

    # Common positional subcommand field for wrapper CLIs like make.sh
    for command_key in ("command", "subcommand"):
        val = params.get(command_key)
        if val is not None:
            used_keys.add(command_key)
            args.append(_stringify_arg(val))
            break

    # Flag-style keys (--xxx / -x) from LLM
    for key, val in params.items():
        if key.startswith("-"):
            used_keys.add(key)
            args.append(key)
            if val is not True and val is not None:
                args.append(_stringify_arg(val))

    # Positional args from required fields (in schema order)
    for key in required_keys:
        if key in used_keys:
            continue
        val = params.get(key)
        if val is not None:
            used_keys.add(key)
            args.append(_stringify_arg(val))

    # Remaining optional params as --key value flags
    for key, val in params.items():
        if key in used_keys or val is None:
            continue
        args.append(f"--{key}")
        if val is not True:
            args.append(_stringify_arg(val))

    return {"_args": args}


def _stringify_arg(value: Any) -> str:
    """Convert a param value to its CLI-safe string representation.

    • dict/list → JSON (ensure_ascii=False so CJK stays readable on the command line)
    • bool       → "true" / "false"  (Python's "True" is rarely what a CLI wants)
    • other      → str(value)
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _find_script_declaration(spec, script_name: str) -> Optional[Dict]:
    normalized_name = script_name.replace("\\", "/").strip()
    for s in spec.executable_scripts:
        if s.get("name") == normalized_name:
            return s

    basename = Path(normalized_name).name
    if not basename:
        return None

    matches = [
        s for s in spec.executable_scripts
        if Path(s.get("name", "")).name == basename
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _get_script_content(
    loader, skill_id: str, script_name: str,
    extra_files: Dict[str, str],
) -> str:
    """从 Skill 文件系统或 extra_files 中获取脚本内容。"""
    # 优先尝试文件系统（已物化的 skill 目录）
    skill_dir = loader.get_skill_dir(skill_id)
    if skill_dir:
        script_path = Path(skill_dir) / script_name
        if script_path.is_file():
            return script_path.read_text(encoding="utf-8")

    # 回退到 extra_files（已由调用方预取）
    if script_name in extra_files:
        return extra_files[script_name]

    raise SkillScriptError(f"脚本文件 '{script_name}' 不存在")


def _get_resource_files(
    extra_files: Dict[str, str], script_name: str,
) -> Optional[Dict[str, str]]:
    """获取 Skill 附带的非脚本资源文件。"""
    if not extra_files:
        return None
    resources = {
        k: v for k, v in extra_files.items()
        if k != script_name and k != "_scripts.json"
    }
    return resources or None
