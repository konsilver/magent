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
    # Minimal env so importing project modules won't explode.
    os.environ.setdefault("TAVILY_API_KEY", "DUMMY")
    os.environ.setdefault("DIFY_API_KEY", "DUMMY")
    os.environ.setdefault("DIFY_URL", "http://localhost")
    os.environ.setdefault("DATABASE_URL", "http://localhost")

    try:
        importlib.import_module("mcp_servers.query_database_mcp.server")
        _ok("import server")
    except Exception as e:
        _fail(f"import server failed: {e!r}")

    try:
        mod = importlib.import_module("mcp_servers.query_database_mcp.impl")
        fn = getattr(mod, "query_database", None)
        if fn is None:
            _fail("impl.query_database not found")
        _ok("import impl.query_database")
    except Exception as e:
        _fail(f"import impl.query_database failed: {e!r}")

    try:
        sig = inspect.signature(fn)
        if "question" not in sig.parameters:
            _fail("query_database signature missing 'question'")
        _ok("signature check")
    except Exception as e:
        _fail(f"signature check failed: {e!r}")

    print("SELFTEST_PASS")


if __name__ == "__main__":
    main()
