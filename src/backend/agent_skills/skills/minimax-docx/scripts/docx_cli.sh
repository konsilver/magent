#!/usr/bin/env bash
# docx_cli.sh — minimax-docx CLI wrapper
# Supports two calling conventions:
#
#   A) Pure CLI args (local / non-sandboxed use):
#      bash scripts/docx_cli.sh create --output out.docx --content-json content.json
#
#   B) Script-runner sandbox (stdin carries a JSON params object):
#      Params JSON sent via stdin: {"content": {...sections...}, ...other_flags...}
#      CLI args are passed normally; if --content-json is absent the wrapper
#      auto-creates /tmp/docx_content_$$.json from the stdin "content" key.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$SCRIPT_DIR/dotnet/MiniMaxAIDocx.Cli"
_TMPFILE=""

cleanup() {
  [ -n "$_TMPFILE" ] && rm -f "$_TMPFILE"
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: docx_cli.sh <command> [options]

Wrapper around the minimax-docx dotnet CLI. It will:
  1. Prefer an already-built CLI DLL if present (/opt/minimax-docx/)
  2. Fall back to `dotnet run --project ... -- <args>` when a .NET SDK is available

-- Stdin JSON params (script-runner sandbox mode) --
Pass a JSON object on stdin. The "content" key is extracted and written to a
temporary file, then --content-json is appended automatically.

Example stdin: {"content": {"sections": [{"heading":"H1","level":1,"paragraphs":["text"]}]}}

-- Examples --
  bash scripts/docx_cli.sh analyze --input document.docx
  bash scripts/docx_cli.sh create --type report --output out.docx --content-json content.json
EOF
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

if ! command -v dotnet >/dev/null 2>&1; then
  echo "Error: dotnet is required but not found." >&2
  echo "Run: bash scripts/env_check.sh" >&2
  exit 1
fi

# ── Read stdin params (script-runner passes JSON on stdin) ────────────────────
STDIN_DATA=""
if [ -t 0 ]; then
  : # stdin is a terminal — no params to read
else
  STDIN_DATA=$(cat)
fi

# ── If stdin has a "content" key and --content-json is absent, inject it ──────
INJECT_CONTENT_FILE=""
# Bash-native check: does $* contain --content-json as a word?
case " $* " in *' --content-json '*) _HAS_CONTENT_JSON=true ;; *) _HAS_CONTENT_JSON=false ;; esac

if [ -n "$STDIN_DATA" ] && ! $_HAS_CONTENT_JSON; then
  # Quick pre-filter: skip python3 subprocess if "content" key is absent
  if echo "$STDIN_DATA" | grep -q '"content"'; then
    CONTENT_JSON=$(python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    c = d.get('content')
    if c is not None:
        print(json.dumps(c, ensure_ascii=False))
except Exception as e:
    print('', end='')
    print(f'[docx_cli] warning: failed to parse stdin JSON: {e}', file=sys.stderr)
" <<< "$STDIN_DATA" 2>/dev/null || true)

    if [ -n "$CONTENT_JSON" ]; then
      _TMPFILE="/tmp/docx_content_$$.json"
      printf '%s' "$CONTENT_JSON" > "$_TMPFILE"
      INJECT_CONTENT_FILE="$_TMPFILE"
      echo "[docx_cli] injected content from stdin → $_TMPFILE" >&2
    fi
  fi
fi

declare -a DLL_CANDIDATES=(
  "/opt/minimax-docx/MiniMaxAIDocx.Cli.dll"
  "$CLI_DIR/bin/Release/net8.0/MiniMaxAIDocx.Cli.dll"
  "$CLI_DIR/bin/Debug/net8.0/MiniMaxAIDocx.Cli.dll"
  "$CLI_DIR/bin/Release/net10.0/MiniMaxAIDocx.Cli.dll"
  "$CLI_DIR/bin/Debug/net10.0/MiniMaxAIDocx.Cli.dll"
)

# Build final arg list (append --content-json if auto-injected)
declare -a FINAL_ARGS=("$@")
if [ -n "$INJECT_CONTENT_FILE" ]; then
  FINAL_ARGS+=("--content-json" "$INJECT_CONTENT_FILE")
fi

for dll in "${DLL_CANDIDATES[@]}"; do
  if [ -f "$dll" ]; then
    exec dotnet "$dll" "${FINAL_ARGS[@]}"
  fi
done

if dotnet --list-sdks 2>/dev/null | grep -Eq '^(8|9|10)\.'; then
  exec dotnet run --project "$CLI_DIR" -- "${FINAL_ARGS[@]}"
fi

echo "Error: minimax-docx CLI is not built, and no compatible .NET SDK is available." >&2
echo "Expected one of:" >&2
for dll in "${DLL_CANDIDATES[@]}"; do
  echo "  - $dll" >&2
done
exit 1
