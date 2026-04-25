"""PromptProvider: pluggable prompt loading with safe fallbacks.

Design goals:
- No import-time dependency on external services / keys.
- Filesystem → Inline → Hardcoded minimal fallback.
- Strict/loose formatting controlled by env PROMPT_STRICT_VARS.

Env:
- PROMPT_PROVIDER: filesystem|inline (default: filesystem)
- PROMPT_DIR: directory for filesystem prompts (default: ./prompts/prompt_text/v1)
- PROMPT_INLINE_TEMPLATE: inline template string (optional)
- PROMPT_STRICT_VARS: 1|0 (default: 1)

Filesystem convention (minimal):
- {prompt_id}.{role}.txt (preferred)
- {prompt_id}.{role}.md
- {prompt_id}.txt (fallback)
- {prompt_id}.md

For system prompt, use prompt_id="system", role="system".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol


# ── File content cache (mtime-based) ───────────────────────────────────────
# key = file path string, value = (mtime, content)
_FILE_CONTENT_CACHE: Dict[str, tuple[float, str]] = {}


def _read_file_cached(p: Path) -> str:
    """Read file contents with an mtime-based cache to avoid redundant disk I/O."""
    key = str(p)
    try:
        current_mtime = p.stat().st_mtime
    except OSError:
        # File disappeared – evict cache entry and return empty.
        _FILE_CONTENT_CACHE.pop(key, None)
        return ""

    cached = _FILE_CONTENT_CACHE.get(key)
    if cached is not None:
        cached_mtime, cached_content = cached
        if cached_mtime == current_mtime:
            return cached_content

    content = p.read_text(encoding="utf-8")
    _FILE_CONTENT_CACHE[key] = (current_mtime, content)
    return content


class PromptProvider(Protocol):
    def get_prompt(self, prompt_id: str, role: str, vars: Dict[str, Any] | None = None) -> str: ...


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


class _LooseFormatDict(dict):
    def __missing__(self, key: str) -> str:
        # Keep the placeholder visible (so missing vars are not silently swallowed)
        return "{" + key + "}"


def render_template(template: str, vars: Dict[str, Any] | None, strict: bool) -> str:
    vars = vars or {}
    if strict:
        return template.format(**vars)
    return template.format_map(_LooseFormatDict(vars))


def hardcoded_minimal_system_prompt() -> str:
    return (
        "你是由宁波市经济和信息化局开发的经信智能体。\n"
        "请用专业、可核验的方式回答问题；需要数据/依据时优先使用可用工具检索。\n"
        "若缺少关键上下文，请先询问澄清。"
    )


@dataclass(frozen=True)
class FilesystemPromptProvider:
    prompt_dir: Path
    strict_vars: bool = True

    def _candidate_paths(self, prompt_id: str, role: str) -> list[Path]:
        exts = [".txt", ".md"]
        candidates: list[Path] = []
        for ext in exts:
            candidates.append(self.prompt_dir / f"{prompt_id}.{role}{ext}")
        for ext in exts:
            candidates.append(self.prompt_dir / f"{prompt_id}{ext}")
        return candidates

    def get_prompt(self, prompt_id: str, role: str, vars: Dict[str, Any] | None = None) -> str:
        template = ""
        for p in self._candidate_paths(prompt_id, role):
            try:
                if p.exists():
                    template = _read_file_cached(p)
                    break
            except Exception:
                continue

        if not template.strip():
            return hardcoded_minimal_system_prompt() if prompt_id == "system" else ""

        return render_template(template, vars=vars, strict=self.strict_vars).strip()


@dataclass(frozen=True)
class InlinePromptProvider:
    template: str
    strict_vars: bool = True

    def get_prompt(self, prompt_id: str, role: str, vars: Dict[str, Any] | None = None) -> str:
        if not self.template.strip():
            return hardcoded_minimal_system_prompt() if prompt_id == "system" else ""
        return render_template(self.template, vars=vars, strict=self.strict_vars).strip()


def get_prompt_provider(
    *,
    default_prompt_dir: str = "./prompts/prompt_text/v1",
    inline_template: str | None = None,
) -> PromptProvider:
    provider = (os.getenv("PROMPT_PROVIDER") or "filesystem").strip().lower()
    strict = _env_bool("PROMPT_STRICT_VARS", True)

    if provider == "inline":
        tmpl = inline_template if inline_template is not None else (os.getenv("PROMPT_INLINE_TEMPLATE") or "")
        return InlinePromptProvider(template=tmpl, strict_vars=strict)

    prompt_dir = Path(os.getenv("PROMPT_DIR") or default_prompt_dir)
    return FilesystemPromptProvider(prompt_dir=prompt_dir, strict_vars=strict)
