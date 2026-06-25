#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo="${1:-$PWD}"
[[ $# -eq 0 ]] || shift
"$ROOT/bin/verdify" doctor --repo "$repo"
export VERDIFY_SKILLS_ROOT="$ROOT"
cd "$repo"
exec codex "$@"
