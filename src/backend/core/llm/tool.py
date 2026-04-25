"""LLM tool registration helpers.

This module keeps AgentScope tool implementations separate from
``agent_factory.py`` so the factory focuses on orchestration.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Optional

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse, Toolkit

from agent_skills.loader import get_skill_loader

logger = logging.getLogger(__name__)


def _resolve_skill_path(file_path: str) -> str | None:
    """Try to resolve a non-existent skill file path to the materialized cache."""
    parts = file_path.replace("\\", "/").split("/")
    candidates: list[tuple[str, str]] = []
    for i, seg in enumerate(parts):
        if seg == "skills" and i + 2 <= len(parts) - 1:
            skill_id = parts[i + 1]
            rel_path = "/".join(parts[i + 2:])
            if skill_id and rel_path:
                candidates.append((skill_id, rel_path))

    cache_root = os.path.join(
        os.path.expanduser("~"), ".cache", "jingxin-agent", "skills",
    )
    for skill_id, rel_path in reversed(candidates):
        cache_path = os.path.join(cache_root, skill_id, rel_path)
        if os.path.exists(cache_path):
            return cache_path

        try:
            loader = get_skill_loader()
            skill_dir = loader.get_skill_dir(skill_id)
            if skill_dir:
                candidate = os.path.join(skill_dir, rel_path)
                if os.path.exists(candidate):
                    return candidate
        except Exception:
            pass

    return None


def _extract_skill_id_from_skill_file(file_path: str) -> str | None:
    """Best-effort skill id extraction from a resolved SKILL.md path."""
    path = Path(file_path)
    if path.name != "SKILL.md":
        return None
    skill_id = path.parent.name.strip()
    return skill_id or None


def _build_skill_script_runtime_hint(
    loader: Any,
    skill_id: str,
    skill_dir: str,
) -> str | None:
    """Build a skill-specific run_skill_script hint after SKILL.md is loaded."""
    spec = loader.load_skill_full(skill_id)
    if not spec or not spec.executable_scripts:
        return None

    script_names = [s.get("name", "").strip() for s in spec.executable_scripts if s.get("name")]
    script_names = [name for name in script_names if name]
    if not script_names:
        return None

    first_script = script_names[0]
    lines = [
        "",
        "----- Runtime Hint -----",
        f"当前已加载技能：{spec.name}",
        "该技能现在可以使用 run_skill_script 工具执行脚本。",
        f"技能目录：{skill_dir}",
        f"当前技能可执行脚本：{', '.join(script_names)}",
        "调用要求：",
        f"1. skill_id 固定为 \"{skill_id}\"",
        "2. script_name 优先使用上面的完整相对路径；若短文件名在当前技能内唯一，也可直接写短名",
        "3. params 默认传 JSON 对象字符串；CLI 脚本也可传原始命令行字符串",
        (
            "调用示例："
            f"run_skill_script(skill_id=\"{skill_id}\", script_name=\"{first_script}\", params=\"{{}}\")"
        ),
    ]
    return "\n".join(lines)


def register_sandboxed_view_text_file(
    toolkit: Toolkit,
    allowed_dirs: list[str],
    loader: Any,
    loaded_skill_ids: set[str] | None = None,
) -> None:
    """Register a sandboxed view_text_file tool."""
    import os as _os

    resolved_dirs = [_os.path.realpath(d) for d in allowed_dirs]

    async def view_text_file(
        file_path: str,
        ranges: list[int] | None = None,
    ) -> ToolResponse:
        """View file content within allowed skill directories."""
        real = _os.path.realpath(_os.path.expanduser(file_path))

        if not _os.path.exists(real):
            resolved = _resolve_skill_path(file_path)
            if resolved:
                file_path = resolved
                real = _os.path.realpath(resolved)

        if not any(real.startswith(d + _os.sep) or real == d for d in resolved_dirs):
            return ToolResponse(content=[TextBlock(
                type="text",
                text=f"Error: Access denied. Only files inside skill directories can be read.\nRequested: {file_path}",
            )])

        from agentscope.tool._text_file._view_text_file import (
            view_text_file as _upstream,
        )

        resp = await _upstream(file_path, ranges)

        if _os.path.basename(real) == "SKILL.md":
            skill_dir = _os.path.dirname(real)
            skill_id = _extract_skill_id_from_skill_file(real)
            if loaded_skill_ids is not None and skill_id:
                loaded_skill_ids.add(skill_id)
            for i, block in enumerate(resp.content):
                if hasattr(block, "text") and "{baseDir}" in block.text:
                    resp.content[i] = TextBlock(
                        type="text",
                        text=block.text.replace("{baseDir}", skill_dir),
                    )
            if skill_id:
                runtime_hint = _build_skill_script_runtime_hint(loader, skill_id, skill_dir)
                if runtime_hint:
                    resp.content.append(TextBlock(type="text", text=runtime_hint))

                # ── Observability: record skill auto-load via view_text_file ──
                try:
                    from core.infra import log_writer as _lw
                    spec = loader.load_skill_full(skill_id)
                    _lw.schedule_skill_call_write({
                        "skill_id": skill_id,
                        "skill_name": getattr(spec, "name", skill_id) if spec else skill_id,
                        "skill_version": getattr(spec, "version", None) if spec else None,
                        "skill_source": getattr(spec, "source", None) if spec else None,
                        "invocation_type": "view",
                        "script_name": None,
                        "status": "success",
                    })
                except Exception:
                    logger.debug("skill view log failed", exc_info=True)

        return resp

    toolkit.register_tool_function(view_text_file, namesake_strategy="override")


def register_use_skill_redirect(toolkit: Toolkit) -> None:
    """Register a use_skill stub that redirects the model to view_text_file."""

    async def use_skill(skill_id: str) -> ToolResponse:
        """Deprecated. Do NOT call this function."""
        _ = skill_id
        return ToolResponse(content=[TextBlock(
            type="text",
            text=(
                "use_skill is not available. "
                "To load a skill, call view_text_file with the SKILL.md path "
                "shown in the Agent Skills section of your system prompt. "
                "Example: view_text_file(file_path=\"<skill_dir>/SKILL.md\")"
            ),
        )])

    toolkit.register_tool_function(use_skill, namesake_strategy="skip")


def _store_generated_files(
    files_data: list[dict[str, Any]],
    *,
    user_id: Optional[str],
    source: str,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Persist sidecar-generated files and return normalized artifact refs."""
    if not files_data:
        return []

    try:
        from artifacts.store import save_artifact_bytes
    except Exception as exc:
        logger.warning("artifact store unavailable for %s: %s", source, exc)
        return []

    refs: list[dict[str, Any]] = []
    for fd in files_data:
        content_b64 = fd.get("content_b64", "")
        if not content_b64:
            continue

        name = str(fd.get("name", "output")).strip() or "output"
        mime_type = str(fd.get("mime_type", "application/octet-stream")).strip() or "application/octet-stream"
        try:
            content = base64.b64decode(content_b64)
        except Exception:
            logger.warning("skip invalid base64 artifact payload: %s", name)
            continue

        metadata: dict[str, Any] = {"source": source}
        if user_id:
            metadata["user_id"] = user_id
        if extra_metadata:
            metadata.update(extra_metadata)

        try:
            item = save_artifact_bytes(
                content=content,
                name=name,
                mime_type=mime_type,
                extension=Path(name).suffix.lstrip("."),
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning("failed to persist generated artifact %s: %s", name, exc)
            continue

        refs.append({
            "file_id": item["file_id"],
            "name": item.get("name", name),
            "url": f"/files/{item['file_id']}",
            "mime_type": item.get("mime_type", mime_type),
            "size": item.get("size", len(content)),
            "storage_key": item.get("storage_key"),
        })

    return refs


def _summarize_generated_files(files_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip binary payloads before exposing file metadata to the model/frontend."""
    summaries: list[dict[str, Any]] = []
    for fd in files_data or []:
        name = str(fd.get("name", "")).strip()
        if not name:
            continue
        summaries.append({
            "name": name,
            "mime_type": str(fd.get("mime_type", "application/octet-stream")),
            "size": int(fd.get("size", 0) or 0),
        })
    return summaries


def _resolve_registered_skill_script(
    loader: Any,
    skill_id: str,
    script_name: str,
) -> dict[str, Any] | None:
    spec = loader.load_skill_full(skill_id)
    if not spec or not spec.executable_scripts:
        return None

    normalized_name = script_name.replace("\\", "/").strip()
    for script in spec.executable_scripts:
        if script.get("name") == normalized_name:
            return script

    basename = Path(normalized_name).name
    if not basename:
        return None

    matches = [
        script for script in spec.executable_scripts
        if Path(script.get("name", "")).name == basename
    ]
    if len(matches) == 1:
        return matches[0]
    return None


MAX_ARTIFACT_FILE_SIZE = 10 * 1024 * 1024  # 10MB per artifact file


def _resolve_artifact_files(
    artifact_refs: dict[str, str],
    user_id: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve artifact:<id> references to base64-encoded binary content.

    Tries DB lookup first, falls back to local artifact store index.
    Returns (files_b64_dict, error_message).
    """
    if not artifact_refs:
        return None, None

    try:
        from core.storage.factory import get_storage
        from api.routes.v1.artifacts import resolve_artifact_storage_key
    except Exception as exc:
        return None, f"artifact 解析依赖不可用: {exc}"

    result: dict[str, str] = {}
    storage = get_storage()

    for filename, artifact_id in artifact_refs.items():
        storage_key = None
        owner_ok = True

        # Try 1: DB lookup
        try:
            from core.db.engine import SessionLocal
            from core.db.repository import ArtifactRepository

            db = SessionLocal()
            try:
                repo = ArtifactRepository(db)
                art = repo.get_by_id(artifact_id)
                if art:
                    if user_id and art.user_id != user_id:
                        owner_ok = False
                    else:
                        storage_key = resolve_artifact_storage_key(
                            art.artifact_id, art.storage_key
                        )
            finally:
                db.close()
        except Exception:
            pass

        if not owner_ok:
            return None, f"无权访问 artifact '{artifact_id}'"

        # Try 2: local artifact store fallback
        if not storage_key:
            try:
                from artifacts.store import get_artifact
                item = get_artifact(artifact_id)
                if item:
                    item_user = (item.get("metadata") or {}).get("user_id")
                    if user_id and item_user and item_user != user_id:
                        return None, f"无权访问 artifact '{artifact_id}'"
                    storage_key = item.get("storage_key")
            except Exception:
                pass

        if not storage_key:
            return None, f"artifact '{artifact_id}' 不存在或已删除"

        try:
            file_bytes = storage.download_bytes(storage_key)
        except Exception as exc:
            return None, f"artifact 文件 '{filename}' 读取失败: {exc}"

        if len(file_bytes) > MAX_ARTIFACT_FILE_SIZE:
            return None, (
                f"artifact 文件 '{filename}' 过大: "
                f"{len(file_bytes)} bytes > {MAX_ARTIFACT_FILE_SIZE} bytes"
            )

        result[filename] = base64.b64encode(file_bytes).decode("ascii")

    return result or None, None


def _parse_run_skill_script_params(
    loader: Any,
    skill_id: str,
    script_name: str,
    params: str,
) -> tuple[dict[str, Any] | None, str | None]:
    raw = (params or "").strip()
    if not raw:
        return {}, None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        script_decl = _resolve_registered_skill_script(loader, skill_id, script_name)
        if script_decl and script_decl.get("input_mode") == "cli_args":
            try:
                cli_args = shlex.split(raw)
            except ValueError as split_exc:
                return None, f"参数 CLI 解析失败: {split_exc}"

            canonical_name = script_decl.get("name", script_name)
            canonical_basename = Path(canonical_name).name
            trimmed_args = list(cli_args)

            if trimmed_args and trimmed_args[0] in {"bash", "sh"}:
                trimmed_args = trimmed_args[1:]
            if trimmed_args and Path(trimmed_args[0]).name in {
                canonical_basename,
                Path(script_name).name,
            }:
                trimmed_args = trimmed_args[1:]

            return {"_args": trimmed_args}, None

        return None, f"参数 JSON 解析失败: {exc}"

    if parsed is None:
        return {}, None
    if not isinstance(parsed, dict):
        return None, "参数必须是 JSON 对象，或 CLI 脚本可用的原始命令行字符串"
    return parsed, None


def register_run_skill_script(
    toolkit: Toolkit,
    enabled_skill_ids: list[str],
    loader: Any,
    user_id: Optional[str] = None,
    loaded_skill_ids: set[str] | None = None,
) -> None:
    """Register run_skill_script when enabled skills expose executable scripts."""
    if os.getenv("SKILL_SCRIPT_ENABLED", "false").lower() != "true":
        return

    available_scripts: dict[str, list] = {}
    for sid in enabled_skill_ids:
        spec = loader.load_skill_full(sid)
        if spec and spec.executable_scripts:
            available_scripts[sid] = spec.executable_scripts

    if not available_scripts:
        return

    async def run_skill_script(
        skill_id: str,
        script_name: str,
        params: str = "{}",
        input_files: str = "{}",
    ) -> ToolResponse:
        from agent_skills.script_runner import run_skill_script as _run, SkillScriptError

        if loaded_skill_ids is not None and skill_id not in loaded_skill_ids:
            skill_dir = loader.get_skill_dir(skill_id)
            skill_md_path = f"{skill_dir}/SKILL.md" if skill_dir else "<skill_dir>/SKILL.md"
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps({
                    "error": (
                        "调用 run_skill_script 前，必须先用 view_text_file 读取该技能的 SKILL.md。"
                        f"请先调用：view_text_file(file_path=\"{skill_md_path}\")"
                    ),
                }, ensure_ascii=False),
            )])

        parsed_params, parse_error = _parse_run_skill_script_params(
            loader=loader,
            skill_id=skill_id,
            script_name=script_name,
            params=params,
        )
        if parse_error:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps({"error": parse_error}, ensure_ascii=False),
            )])

        # Parse input_files JSON
        parsed_input_files = None
        raw_files = (input_files or "").strip()
        if raw_files and raw_files != "{}":
            try:
                parsed_input_files = json.loads(raw_files)
                if not isinstance(parsed_input_files, dict):
                    return ToolResponse(content=[TextBlock(
                        type="text",
                        text=json.dumps({"error": "input_files 必须是 JSON 对象 {文件名: 内容}"},
                                        ensure_ascii=False),
                    )])
                # Auto-stringify non-string values (e.g. LLM passes list/dict directly)
                for k, v in parsed_input_files.items():
                    if not isinstance(v, str):
                        parsed_input_files[k] = json.dumps(v, ensure_ascii=False)
            except json.JSONDecodeError as exc:
                return ToolResponse(content=[TextBlock(
                    type="text",
                    text=json.dumps({"error": f"input_files JSON 解析失败: {exc}"},
                                    ensure_ascii=False),
                )])

        # Resolve "artifact:<id>" references → binary files
        parsed_input_files_b64 = None
        if parsed_input_files:
            artifact_refs = {
                k: v[9:]  # strip "artifact:" prefix
                for k, v in parsed_input_files.items()
                if isinstance(v, str) and v.startswith("artifact:")
            }
            if artifact_refs:
                resolved, resolve_err = _resolve_artifact_files(artifact_refs, user_id)
                if resolve_err:
                    return ToolResponse(content=[TextBlock(
                        type="text",
                        text=json.dumps({"error": resolve_err}, ensure_ascii=False),
                    )])
                parsed_input_files_b64 = resolved
                # Remove resolved artifact refs from text input_files
                for k in artifact_refs:
                    del parsed_input_files[k]
                if not parsed_input_files:
                    parsed_input_files = None

        try:
            result = await _run(
                skill_id=skill_id,
                script_name=script_name,
                params=parsed_params,
                input_files=parsed_input_files,
                input_files_b64=parsed_input_files_b64,
            )
            stored_refs = _store_generated_files(
                result.get("files", []),
                user_id=user_id,
                source="skill_script",
                extra_metadata={"skill_id": skill_id, "script_name": script_name},
            )
            safe_result = dict(result)
            if "files" in safe_result:
                safe_result["files"] = _summarize_generated_files(result.get("files", []))
            if stored_refs:
                safe_result["artifacts"] = stored_refs
                if len(stored_refs) == 1:
                    safe_result.update(stored_refs[0])
                    safe_result["ok"] = True
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps(safe_result, ensure_ascii=False),
            )])
        except SkillScriptError as e:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps({"error": str(e)}, ensure_ascii=False),
            )])

    run_skill_script.__doc__ = (
        "执行 Skill 中预定义的脚本（在隔离容器中运行）。\n\n"
        "硬性前提：当前轮次必须先通过 view_text_file 读取对应 Skill 的 SKILL.md，"
        "否则此工具会直接报错。\n\n"
        "此工具不会在全局文档中暴露具体脚本白名单。"
        "读取某个 Skill 的 SKILL.md 后，系统会在该次 view_text_file 的结果末尾追加"
        "当前技能专属的 run_skill_script 调用提示；具体脚本名、调用方式和参数要求以该提示"
        "以及 SKILL.md 内容为准。\n\n"
        "如果某个 Skill 的 SKILL.md 指示你直接调用 MCP 工具而不是脚本，"
        "则不要使用此工具。\n\n"
        "Args:\n"
        "    skill_id (`str`):\n"
        "        Skill ID。必须与当前已加载的 Skill 一致。\n"
        "    script_name (`str`):\n"
        "        脚本文件名。请优先使用读取 SKILL.md 后提示出的完整相对路径。\n"
        "    params (`str`):\n"
        "        参数字符串。默认传 JSON 对象字符串；若脚本是 CLI 模式，也可直接传原始命令行字符串，"
        "例如 \"demo\" 或 \"run --title '测试' --type report\"。\n"
        "    input_files (`str`):\n"
        "        可选。JSON 对象字符串，键是文件名，值是文件内容或 artifact 引用。"
        "文件会被写入脚本工作目录，脚本可通过相对路径直接读取。\n"
        "        文本内容：'{\"content.json\": \"[{\\\"type\\\":\\\"h1\\\",\\\"text\\\":\\\"标题\\\"}]\"}'。\n"
        "        二进制文件（用户上传的 PDF/DOCX/XLSX 等）：使用 'artifact:<file_id>' 引用，"
        "其中 file_id 取自对话上下文中用户上传文件的 file_id 值（如 ua_xxxx）。"
        "例如 '{\"report.docx\": \"artifact:ua_abc123\"}'。系统会自动从存储中读取文件并传递给脚本。\n"
        "        文本文件上限 512KB/个、1MB 总计；artifact 文件上限 10MB/个。\n"
    )

    toolkit.register_tool_function(run_skill_script, namesake_strategy="override")
    logger.info(
        "[factory] Registered run_skill_script tool with %d script(s) from %d skill(s)",
        sum(len(v) for v in available_scripts.values()),
        len(available_scripts),
    )


def register_execute_code_tools(toolkit: Toolkit, user_id: Optional[str] = None) -> None:
    """Register code execution tools for Lab sessions."""
    runner_url = os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900")
    if not runner_url:
        return

    import json
    import httpx

    _EXT_MAP = {"python": "py", "javascript": "js", "bash": "sh"}

    def _store_exec_files(files_data: list) -> list:
        if not files_data:
            return []
        return _store_generated_files(
            files_data,
            user_id=user_id,
            source="code_exec",
        )

    async def _call_sidecar(
        script_content: str, script_name: str, language: str, timeout: int,
    ) -> ToolResponse:
        effective_timeout = min(timeout, 120)
        try:
            async with httpx.AsyncClient(timeout=effective_timeout + 10) as client:
                resp = await client.post(
                    f"{runner_url}/execute",
                    json={
                        "script_content": script_content,
                        "script_name": script_name,
                        "language": language,
                        "params": {},
                        "timeout": effective_timeout,
                    },
                )
                result = resp.json()
                parts = []
                if result.get("stdout"):
                    parts.append(f"stdout:\n{result['stdout']}")
                if result.get("stderr"):
                    parts.append(f"stderr:\n{result['stderr']}")
                parts.append(f"exit_code: {result.get('exit_code', -1)}")
                parts.append(f"execution_time: {result.get('execution_time_ms', 0)}ms")

                raw_files = result.get("files", [])
                if raw_files:
                    file_refs = _store_exec_files(raw_files)
                    if file_refs:
                        parts.append(f"files: {json.dumps(file_refs, ensure_ascii=False)}")

                return ToolResponse(content=[TextBlock(
                    type="text", text="\n\n".join(parts),
                )])
        except Exception as e:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps({"error": f"执行失败: {e}"}, ensure_ascii=False),
            )])

    async def execute_code(
        language: str,
        code: str,
        timeout: int = 60,
    ) -> ToolResponse:
        """在安全沙箱中执行代码。适用于数据分析、算法验证、可视化等场景。"""
        if language not in _EXT_MAP:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=json.dumps(
                    {"error": f"不支持的语言: {language}，可选: python, javascript, bash"},
                    ensure_ascii=False,
                ),
            )])
        return await _call_sidecar(code, f"exec.{_EXT_MAP[language]}", language, timeout)

    async def run_command(
        command: str,
        timeout: int = 60,
    ) -> ToolResponse:
        """在沙箱中执行 shell 命令。适用于安装依赖、文件操作、系统命令等场景。"""
        script = f"#!/bin/bash\nset -e\n{command}\n"
        return await _call_sidecar(script, "cmd.sh", "bash", timeout)

    toolkit.register_tool_function(execute_code, namesake_strategy="override")
    toolkit.register_tool_function(run_command, namesake_strategy="override")
    logger.info(
        "[factory] Registered execute_code + run_command tools for Lab session (runner=%s)",
        runner_url,
    )


def register_myspace_tools(toolkit: Toolkit, user_id: Optional[str] = None) -> None:
    """Register MySpace access tools for Lab code execution sessions."""
    if not user_id:
        return
    runner_url = os.getenv("SKILL_SCRIPT_RUNNER_URL", "http://jingxin-script-runner:8900")
    if not runner_url:
        return

    import base64 as _b64
    import json
    import httpx

    async def list_myspace_files(
        file_type: str = "all",
        keyword: str = "",
        limit: int = 20,
    ) -> ToolResponse:
        """列出"我的空间"中的文件资产。"""
        try:
            from core.db.engine import SessionLocal
            from core.db.repository import ArtifactRepository

            limit = min(int(limit), 100)
            mime_prefix: Optional[str] = None
            if file_type == "image":
                mime_prefix = "image/"
            elif file_type == "document":
                mime_prefix = "document"

            db = SessionLocal()
            try:
                repo = ArtifactRepository(db)
                items, total = repo.list_by_user_with_chat(
                    user_id=user_id,
                    mime_prefix=mime_prefix,
                    keyword=keyword or None,
                    page=1,
                    page_size=limit,
                )
            finally:
                db.close()

            results = []
            for row in items:
                art = row["artifact"]
                extra = art.extra_data or {}
                source = extra.get("source", "ai_generated")
                if source not in ("user_upload", "code_exec"):
                    if extra.get("source") == "user_upload":
                        source = "user_upload"
                    elif art.artifact_id.startswith("ua_"):
                        source = "user_upload"
                    else:
                        source = "ai_generated"
                results.append({
                    "artifact_id": art.artifact_id,
                    "name": art.filename or art.title,
                    "title": art.title,
                    "type": art.type,
                    "mime_type": art.mime_type,
                    "size_bytes": art.size_bytes,
                    "source": source,
                    "chat_title": row.get("chat_title"),
                    "created_at": art.created_at.isoformat() if art.created_at else None,
                })

            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"total": total, "items": results}, ensure_ascii=False
                ))],
            )
        except Exception as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                ))],
            )

    async def stage_myspace_file(artifact_id: str) -> ToolResponse:
        """将"我的空间"中的文件暂存到代码执行工作区。"""
        try:
            from core.db.engine import SessionLocal
            from core.db.repository import ArtifactRepository
            from core.storage.factory import get_storage
            from api.routes.v1.artifacts import resolve_artifact_storage_key

            db = SessionLocal()
            try:
                repo = ArtifactRepository(db)
                art = repo.get_by_id(artifact_id)
                if not art:
                    from core.db.models import Artifact as ArtifactModel
                    from sqlalchemy import func

                    art = db.query(ArtifactModel).filter(
                        ArtifactModel.user_id == user_id,
                        ArtifactModel.deleted_at.is_(None),
                        func.lower(ArtifactModel.filename) == func.lower(artifact_id),
                    ).order_by(ArtifactModel.created_at.desc()).first()
                if not art:
                    raise ValueError(f"文件 {artifact_id} 不存在或已删除")
                if art.user_id != user_id:
                    raise PermissionError("无权访问该文件")
                storage_key = resolve_artifact_storage_key(art.artifact_id, art.storage_key)
                if not storage_key:
                    raise ValueError(f"文件 {artifact_id} 缺少有效的存储地址")
                if storage_key != art.storage_key:
                    art.storage_key = storage_key
                    db.commit()
                filename = art.filename or art.title or "file"
                mime_type = art.mime_type or "application/octet-stream"
                size_bytes = art.size_bytes or 0
            finally:
                db.close()

            storage = get_storage()
            file_bytes = storage.download_bytes(storage_key)
            content_b64 = _b64.b64encode(file_bytes).decode()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{runner_url}/stage",
                    json={
                        "user_id": user_id,
                        "files": [{"name": filename, "content_b64": content_b64}],
                    },
                )
                resp.raise_for_status()
                staged = resp.json().get("staged", [])

            if not staged:
                raise RuntimeError("暂存失败：script-runner 未返回路径")

            result = {
                "path": staged[0]["path"],
                "name": filename,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
            }
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(result, ensure_ascii=False))],
            )
        except Exception as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                ))],
            )

    async def list_favorite_chats(
        keyword: str = "",
        limit: int = 20,
    ) -> ToolResponse:
        """列出"我的空间"中收藏的会话。"""
        try:
            from core.db.engine import SessionLocal
            from core.db.repository import ChatSessionRepository
            from core.db.models import ChatMessage

            limit = min(int(limit), 50)
            db = SessionLocal()
            try:
                repo = ChatSessionRepository(db)
                sessions, total = repo.list_by_user(
                    user_id=user_id,
                    favorite_only=True,
                    page=1,
                    page_size=limit,
                )

                results = []
                for s in sessions:
                    if keyword and keyword.lower() not in (s.title or "").lower():
                        continue
                    last_msg = db.query(ChatMessage).filter(
                        ChatMessage.chat_id == s.chat_id,
                        ChatMessage.role == "assistant",
                    ).order_by(ChatMessage.created_at.desc()).first()
                    preview = ""
                    if last_msg:
                        preview = (last_msg.content or "")[:200]

                    results.append({
                        "chat_id": s.chat_id,
                        "title": s.title or "未命名会话",
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                        "last_message_preview": preview,
                    })
            finally:
                db.close()

            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"total": total, "items": results}, ensure_ascii=False
                ))],
            )
        except Exception as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                ))],
            )

    async def get_chat_messages(
        chat_id: str,
        limit: int = 50,
    ) -> ToolResponse:
        """获取指定收藏会话的完整消息记录。"""
        try:
            from core.db.engine import SessionLocal
            from core.db.models import ChatSession, ChatMessage
            from sqlalchemy import asc

            limit = min(int(limit), 200)
            db = SessionLocal()
            try:
                session = db.query(ChatSession).filter(
                    ChatSession.chat_id == chat_id,
                    ChatSession.user_id == user_id,
                    ChatSession.deleted_at.is_(None),
                ).first()
                if not session:
                    raise ValueError(f"会话 {chat_id} 不存在或无权访问")
                if not session.favorite:
                    raise PermissionError("该会话未被收藏，无法读取（仅限收藏会话）")

                messages = db.query(ChatMessage).filter(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.role.in_(["user", "assistant"]),
                ).order_by(asc(ChatMessage.created_at)).limit(limit).all()

                results = []
                for m in messages:
                    results.append({
                        "role": m.role,
                        "content": (m.content or "")[:5000],
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    })
            finally:
                db.close()

            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"chat_id": chat_id, "messages": results}, ensure_ascii=False
                ))],
            )
        except Exception as exc:
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(
                    {"error": str(exc)}, ensure_ascii=False
                ))],
            )

    toolkit.register_tool_function(list_myspace_files, namesake_strategy="override")
    toolkit.register_tool_function(stage_myspace_file, namesake_strategy="override")
    toolkit.register_tool_function(list_favorite_chats, namesake_strategy="override")
    toolkit.register_tool_function(get_chat_messages, namesake_strategy="override")
    logger.info("[factory] Registered 4 MySpace tools for Lab session (user=%s)", user_id)


_READ_ARTIFACT_DEFAULT_LIMIT = 4000
_READ_ARTIFACT_MAX_LIMIT = 20000


def register_read_artifact(toolkit: Toolkit, user_id: Optional[str] = None) -> None:
    """Register the read_artifact tool for on-demand reading of uploaded files.

    The tool returns paginated parsed text for an artifact, reading from
    Artifact.parsed_text cache when available (otherwise parses + caches).
    Used to implement cross-turn file access: hooks inject only file summaries
    for historical files, and the agent calls this tool when it needs full
    content.
    """

    async def read_artifact(
        file_id: str,
        offset: int = 0,
        limit: int = _READ_ARTIFACT_DEFAULT_LIMIT,
    ) -> ToolResponse:
        """读取已上传文件的完整解析文本（按字符分页）。

        Args:
            file_id (`str`):
                文件 ID（例如 ua_abc123），取自当前对话 [历史已上传文件] 清单或
                当轮附件的 file_id。
            offset (`int`):
                起始字符位置。从 0 开始；结合 next_offset 字段可继续分页。
            limit (`int`):
                本次返回的最大字符数，默认 4000，上限 20000。

        Returns:
            JSON: {file_id, filename, total_chars, offset, returned_chars,
                   has_more, next_offset, content} 或 {error: 原因}
        """
        import json as _json

        from core.content.artifact_reader import fetch_parsed_text, load_artifact_meta

        fid = (file_id or "").strip()
        if not fid:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=_json.dumps({"error": "file_id 不能为空"}, ensure_ascii=False),
            )])

        meta = load_artifact_meta(fid, user_id=user_id)
        if meta is None:
            return ToolResponse(content=[TextBlock(
                type="text",
                text=_json.dumps(
                    {"error": f"文件 {fid} 不存在、已删除，或无权访问"},
                    ensure_ascii=False,
                ),
            )])

        text = fetch_parsed_text(fid, user_id=user_id)
        if not text:
            error = meta.get("parse_error") or "文件暂无可读文本内容"
            return ToolResponse(content=[TextBlock(
                type="text",
                text=_json.dumps(
                    {"error": error, "filename": meta.get("filename"), "file_id": fid},
                    ensure_ascii=False,
                ),
            )])

        total = len(text)
        try:
            off = max(0, int(offset))
        except (TypeError, ValueError):
            off = 0
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = _READ_ARTIFACT_DEFAULT_LIMIT
        lim = max(1, min(lim, _READ_ARTIFACT_MAX_LIMIT))

        if off >= total:
            content_slice = ""
            next_offset = total
        else:
            content_slice = text[off: off + lim]
            next_offset = off + len(content_slice)

        result = {
            "file_id": fid,
            "filename": meta.get("filename"),
            "mime_type": meta.get("mime_type"),
            "total_chars": total,
            "offset": off,
            "returned_chars": len(content_slice),
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
            "content": content_slice,
        }
        return ToolResponse(content=[TextBlock(
            type="text",
            text=_json.dumps(result, ensure_ascii=False),
        )])

    toolkit.register_tool_function(read_artifact, namesake_strategy="override")
    logger.info("[factory] Registered read_artifact tool (user=%s)", user_id)


__all__ = [
    "register_execute_code_tools",
    "register_myspace_tools",
    "register_read_artifact",
    "register_run_skill_script",
    "register_sandboxed_view_text_file",
    "register_use_skill_redirect",
]
