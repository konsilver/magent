"""Selftest for ai_chain_information_mcp.

Run:
  python -m mcp_servers.ai_chain_information_mcp._selftest

This test is import-level only and must not require external API keys.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    # Guard env for other modules.
    os.environ.setdefault("TAVILY_API_KEY", "DUMMY")
    os.environ.setdefault("DIFY_API_KEY", "DUMMY")
    os.environ.setdefault("DIFY_URL", "http://localhost")
    os.environ.setdefault("DATABASE_URL", "http://localhost")

    try:
        mod = importlib.import_module("mcp_servers.ai_chain_information_mcp.server")
        _ok("import server")
    except ModuleNotFoundError as e:
        # Allow running selftest in minimal envs where MCP deps are not installed.
        # In real gate checks, run inside the project conda env where `mcp` is available.
        _ok(f"SKIP: import server (missing dependency): {e}")
        print("SELFTEST_SKIP")
        return
    except Exception as e:
        _fail(f"import server failed: {e!r}")

    for fn_name in ("get_chain_information", "get_industry_news", "get_latest_ai_news"):
        fn = getattr(mod, fn_name, None)
        if fn is None:
            _fail(f"missing tool function: {fn_name}")
        target = fn
        try:
            sig = inspect.signature(target)
            _ok(f"signature: {fn_name}{sig}")
        except Exception as e:
            _fail(f"signature check failed for {fn_name}: {e!r}")

    print("SELFTEST_PASS")


if __name__ == "__main__":
    main()
