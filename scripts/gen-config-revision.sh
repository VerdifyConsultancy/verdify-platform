#!/usr/bin/env bash
# gen-config-revision.sh — GitOps-owned config-revision rollout trigger (#587,
# audit §8.10 in docs/research/planner-efficacy-current-firmware-2026-08-14.md).
#
# WHY: the fixed-name verdify-config ConfigMap is consumed via envFrom by
# api / mcp / ingestor / migration-job / planner / setpoint-server /
# lab-publisher / ha-gap-backfill, so editing the ConfigMap does NOT restart
# any pod — a flag flip (e.g. VERDIFY_POLICY_VECTOR_MODE at a §8.10 rollout
# step) would silently not take effect until some unrelated rollout. This
# script maintains a deterministic content hash of every verdify-config source
# as the `verdify.io/config-revision` pod-template annotation on the five
# long-running env consumers, so a config edit changes the pod template and
# the next ArgoCD sync rolls exactly those pods.
#
# HASH INPUT (canonical, no-arg invocation): the base ConfigMap PLUS every
# overlay file that patches the verdify-config ConfigMap (device-write /
# publish-all / hermes-url / gather-script-env / future experiment patches),
# discovered by content, sorted, hashed via sha256sum of the file list. The
# overlay patches are deliberately included: the §8.10 rollout steps flip
# flags in the PROD OVERLAY patch, and those edits must bump the revision
# too. The blast radius is intentionally one shared revision across overlays
# (a config edit for either overlay rolls both at their next gated sync) —
# restarts are ArgoCD-manual-sync-gated, and the ingestor keeps its gated
# single-writer Recreate semantics.
#
# USAGE:
#   scripts/gen-config-revision.sh                 # rewrite annotations in place (idempotent)
#   scripts/gen-config-revision.sh --check         # CI gate: fail if annotations are stale
#   scripts/gen-config-revision.sh --print         # print the canonical revision
#   scripts/gen-config-revision.sh --print prod    # print a revision scoped to base + one
#                                                  # overlay's config patches (rollout
#                                                  # verification aid; scoped values are
#                                                  # NEVER written — the committed value is
#                                                  # always the canonical all-overlay hash)
#
# CI: tests/test_21_config_revision.py and the scripts/ci-local.sh step run
# `--check`, so a verdify-config edit without the annotation bump fails CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_CM="deploy/k8s/base/configmap.yaml"
OVERLAY_DIR="deploy/k8s/overlays"

# Pod templates carrying the annotation (the long-running envFrom consumers).
# migration-job / lab-publisher / ha-gap-backfill are Jobs/CronJobs — they read
# the ConfigMap fresh at each run, so they need no rollout trigger.
ANNOTATED_FILES=(
  "deploy/k8s/base/api-deployment.yaml"
  "deploy/k8s/base/mcp-deployment.yaml"
  "deploy/k8s/base/ingestor-deployment.yaml"
  "deploy/k8s/components/planner/planner-deployment.yaml"
  "deploy/k8s/components/setpoint-server/setpoint-server.yaml"
)

ANNOTATION="verdify.io/config-revision"

mode="write"
overlays=()
for arg in "$@"; do
  case "$arg" in
    --check) mode="check" ;;
    --print) mode="print" ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) overlays+=("$arg") ;;
  esac
done

# Discover verdify-config patch ConfigMaps by content: standalone ConfigMap
# docs named verdify-config directly under an overlay dir. Deployment patches
# that merely *reference* the ConfigMap (configMapRef) are excluded by the
# `kind: ConfigMap` requirement.
discover_patches() {
  local dir="$1"
  grep -rl --include='*.yaml' '^  name: verdify-config$' "$dir" 2>/dev/null \
    | while read -r f; do
        grep -q '^kind: ConfigMap$' "$f" && echo "$f"
      done
}

inputs=("$BASE_CM")
if [ "${#overlays[@]}" -eq 0 ]; then
  scope_dirs=("$OVERLAY_DIR")
else
  scope_dirs=()
  for o in "${overlays[@]}"; do
    d="$OVERLAY_DIR/$o"
    [ -d "$d" ] || { echo "ERROR: no such overlay: $o ($d)" >&2; exit 2; }
    scope_dirs+=("$d")
  done
fi
for d in "${scope_dirs[@]}"; do
  while IFS= read -r f; do inputs+=("$f"); done < <(discover_patches "$d")
done

# Deterministic order + repo-relative paths inside the hashed text.
mapfile -t inputs < <(printf '%s\n' "${inputs[@]}" | LC_ALL=C sort -u)

revision="$(sha256sum "${inputs[@]}" | sha256sum | cut -c1-12)"

if [ "$mode" = "print" ]; then
  echo "$revision"
  exit 0
fi

if [ "${#overlays[@]}" -gt 0 ]; then
  echo "ERROR: overlay-scoped invocation is --print only; the committed" >&2
  echo "annotation is always the canonical all-overlay hash (run with no args)." >&2
  exit 2
fi

status=0
for f in "${ANNOTATED_FILES[@]}"; do
  if ! grep -q "$ANNOTATION" "$f"; then
    echo "ERROR: $f is missing the $ANNOTATION pod-template annotation" >&2
    status=1
    continue
  fi
  current="$(grep -o "$ANNOTATION: \"[0-9a-f]*\"" "$f" | head -1 | cut -d'"' -f2)"
  if [ "$current" = "$revision" ]; then
    continue
  fi
  if [ "$mode" = "check" ]; then
    echo "STALE: $f has $ANNOTATION=\"$current\", expected \"$revision\"" >&2
    status=1
  else
    sed -i -E "s|($ANNOTATION: )\"[0-9a-f]*\"|\\1\"$revision\"|" "$f"
    echo "updated $f -> $revision"
  fi
done

if [ "$mode" = "check" ] && [ "$status" -ne 0 ]; then
  echo "" >&2
  echo "verdify-config sources changed without a config-revision bump." >&2
  echo "Fix: scripts/gen-config-revision.sh  (then commit the annotation updates)" >&2
fi
[ "$mode" = "check" ] && [ "$status" -eq 0 ] && echo "config-revision OK ($revision)"
exit "$status"
