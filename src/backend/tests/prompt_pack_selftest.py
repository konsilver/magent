"""Selftest: filesystem prompt pack is actually used.

Run:
  python -m selftests.prompt_pack_selftest

This must not require any external API keys.
"""

from __future__ import annotations

import os
from pathlib import Path

from prompts.prompt_config import PromptConfig, SystemPromptConfig
from prompts.prompt_runtime import build_system_prompt


def main() -> int:
    # Point to repo default prompt pack (as used by configs/prompts/default.json).
    root = Path(__file__).resolve().parents[1]
    prompt_dir = root / "prompts" / "prompt_text" / "default"
    assert prompt_dir.exists(), f"missing prompt_dir: {prompt_dir}"

    cfg = PromptConfig(
        system_prompt=SystemPromptConfig(
            provider="filesystem",
            prompt_dir=str(prompt_dir),
            parts=[
                "system/00_time_role",
                "system/10_abilities",
                "system/20_tools_policy",
            ],
        )
    )

    out = build_system_prompt(cfg, ctx={"now": "TEST_NOW", "selftest": True})

    # We assert on stable structure, not exact wording.
    assert "你是产业网链智能助手（iChainAgent）" in out
    assert "TEST_NOW" in out, "{now} variable should be rendered"

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
