#!/usr/bin/env python3
"""Import model providers and role assignments from model_config.json into the database.

Usage (from repo root):
    PYTHONPATH=src/backend python scripts/import_model_configs.py

Or:
    cd src/backend && PYTHONPATH=. python ../../scripts/import_model_configs.py
"""

from __future__ import annotations

import json
import os
import sys

# Load .env
for candidate in ("../../.env", "../.env", ".env"):
    if os.path.exists(candidate):
        from dotenv import load_dotenv
        load_dotenv(candidate, override=False)
        break

# Ensure src/backend is on PYTHONPATH
_backend = os.path.join(os.path.dirname(__file__), "..", "src", "backend")
if os.path.isdir(_backend):
    sys.path.insert(0, os.path.abspath(_backend))

from core.db.engine import SessionLocal  # noqa: E402
from core.db.model_repository import import_all  # noqa: E402

_IMPORT_FILE = os.path.join(
    os.path.dirname(__file__), "..", "my_design_doc", "model_import.json"
)


def main() -> None:
    with open(_IMPORT_FILE, encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        result = import_all(db, data, overwrite=True)
        print(
            f"Done: {result['imported_providers']} provider(s), "
            f"{result['imported_roles']} role(s) imported."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
