#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $(basename "$0") <presentation.pptx>"
  echo "Extract PPTX text content with markitdown."
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

INPUT="$1"
if [ ! -f "$INPUT" ]; then
  echo "Error: File not found: $INPUT"
  exit 1
fi

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "Error: python is required but not found."
  exit 1
fi

exec "$PYTHON_BIN" -m markitdown "$INPUT"
