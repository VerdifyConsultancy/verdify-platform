#!/usr/bin/env bash
# GHA path-impact: run the native classifier on the exact checkout.
#
#   ci/gha/path-impact.sh          PR/dispatch classification (check flags)
#   ci/gha/path-impact.sh --push   main-push classification (build profiles, pin-only skip)
#
# ci/gha/verdify_path_impact.py is a copy (ruff-formatted only) of jvallery/agents
# platform/kubernetes/ci/agent-fleet-ci/workflows/verdify_path_impact.py (the
# native ConfigMap's impact.py) at agents 2f2ea19bad7c436aa6bc5460d9ecc9b1cb845a57.
# Change both together until the native pipeline is removed (#1397 step 5).
#
# Prints VERDIFY_PATH_IMPACT=<receipt>. When GITHUB_OUTPUT is set, appends mode,
# every profile flag and every check flag, so build.yml can select its builds.
. "$(dirname "$0")/lib.sh"
ci_fetch_history
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
ci_classify "$out" "$@"
for flag in dashboard-check dashboard-db; do
  case "$(cat "$out/$flag")" in
    true|false) ;;
    *) echo "path impact wrote no boolean ${flag}" >&2; exit 1 ;;
  esac
done
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  for f in "$out"/*; do
    name="$(basename "$f")"
    [ "$name" = receipt.json ] && continue
    printf '%s=%s\n' "$name" "$(cat "$f")" >> "$GITHUB_OUTPUT"
  done
fi
