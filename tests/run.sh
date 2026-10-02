#!/bin/bash
# run.sh — LIVE operator smoke suite; not the offline CI gate (make ci).
# Usage: ./tests/run.sh          # all tests
#        ./tests/run.sh -k api   # only API tests
#        ./tests/run.sh -v       # verbose
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  echo "Missing checkout tooling: run make setup or set PYTHON." >&2
  exit 127
fi

echo "═══════════════════════════════════════════════════════"
echo "  Verdify Smoke Tests — $(date '+%Y-%m-%d %H:%M %Z')"
echo "═══════════════════════════════════════════════════════"
echo ""

"$PYTHON" -m pytest tests/ \
    --tb=short \
    -q \
    "$@"
