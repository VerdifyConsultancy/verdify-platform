#!/usr/bin/env bash
# check-service-restart-drift.sh — service-restart-drift-guard (CLAUDE.md rule 7).
#
# Observed need from the 2026-04-21 MCP staleness incident: MCP ran 40+ hours
# with a stale schema because nobody restarted it post-merge. Any change that
# touches verdify_schemas/**, ingestor/entity_map.py, or mcp/server.py must
# document which services bounce post-merge.
#
# History: this guard lived in .github/workflows/ci.yml (retired 2026-07-11,
# commit 6c7abe1) and matched /(restart|bounce|service|systemctl)/i — the bare
# word "service" appears in nearly every PR body, so it false-passed (#391).
# The contract is now structural:
#
#   PASS when the documentation text (commit messages BASE..HEAD, plus $PR_BODY
#   when the caller provides one) either
#     - names at least one known bounceable service (verdify-mcp,
#       verdify-ingestor, verdify-api) alongside restart/bounce wording, e.g.
#         Post-merge restart: verdify-mcp, verdify-ingestor
#         bounce verdify-mcp after merge
#     - or explicitly documents that no bounce is needed, WITH a reason:
#         Restart: none — <reason>
#   FAIL otherwise (incidental words like "service" or "restart" alone no
#   longer count).
#
# Usage:
#   check-service-restart-drift.sh <base> [head]     # diff mode (make ci)
#   check-service-restart-drift.sh --check-text FILE # text contract only,
#                                                    # assumes guarded paths
#                                                    # changed (test harness)
set -euo pipefail

GUARDED_PATHSPECS=('verdify_schemas/' 'ingestor/entity_map.py' 'mcp/server.py')
KNOWN_SERVICES_RE='verdify-(mcp|ingestor|api)'
RESTART_WORDS_RE='(restart|bounce)'
# "Restart: none" (or "Post-merge restart: none") must carry a reason after a
# separator, e.g. "Restart: none — schema comment only".
RESTART_NONE_RE='restart:[[:space:]]*none[^[:alnum:]]+[[:alnum:]]'

usage() { sed -n '2,29p' "${BASH_SOURCE[0]}"; }

fail_with_guidance() {
  cat >&2 <<'EOF'
FAIL: change touches verdify_schemas/**, ingestor/entity_map.py, or
mcp/server.py but the commit message / PR body does not document the
post-merge service restart (CLAUDE.md rule 7).

Add one of:
  Post-merge restart: verdify-mcp, verdify-ingestor
  Restart: none — <reason no consumer needs a bounce>

Naming a known service (verdify-mcp / verdify-ingestor / verdify-api) next to
restart/bounce wording also passes; incidental words like "service" do not.
EOF
  exit 1
}

check_text() {
  local file="$1"
  if grep -Eiq "$RESTART_NONE_RE" "$file"; then
    echo "OK: explicit 'Restart: none' with a documented reason"
    return 0
  fi
  if grep -Eiq "$KNOWN_SERVICES_RE" "$file" && grep -Eiq "$RESTART_WORDS_RE" "$file"; then
    echo "OK: restart documentation names a known bounceable service"
    return 0
  fi
  return 1
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ "${1:-}" = "--check-text" ]; then
  [ -n "${2:-}" ] || { echo "usage: $0 --check-text FILE" >&2; exit 2; }
  check_text "$2" || fail_with_guidance
  exit 0
fi

BASE="${1:-}"
HEAD_REF="${2:-HEAD}"
[ -n "$BASE" ] || { usage >&2; exit 2; }

CHANGED=$(git diff --name-only "$BASE" "$HEAD_REF" -- "${GUARDED_PATHSPECS[@]}")
if [ -z "$CHANGED" ]; then
  echo "OK: no schema/entity_map/mcp files changed; restart documentation not required"
  exit 0
fi
echo "Schema-touching files changed:"
printf '  %s\n' $CHANGED

TEXT_FILE=$(mktemp)
trap 'rm -f "$TEXT_FILE"' EXIT
git log --format=%B "$BASE".."$HEAD_REF" > "$TEXT_FILE"
if [ -n "${PR_BODY:-}" ]; then
  printf '%s\n' "$PR_BODY" >> "$TEXT_FILE"
fi

check_text "$TEXT_FILE" || fail_with_guidance
