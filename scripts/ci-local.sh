#!/usr/bin/env bash
# ci-local.sh — the ENTIRE pre-merge validation gate, runnable anywhere
# (operator host, k3s pod, the in-cluster verdify-platform-ci Argo Workflow).
#
# 2026-07-11: GitHub Actions is removed from this repo (operator directive —
# no external CI dependency). This script IS the gate the old workflows ran:
#   ci.yml            -> lint, format, schema/logic/contract pytest suites,
#                        migration rollback safety, twin vendored-src compile
#   k8s-manifests.yml -> kustomize render of the prod overlay
#   container-publish -> image builds happen in-cluster (repo-build Kaniko
#                        workflows -> zot origin; docs/runbooks/prod-promotion.md)
#
# Gates that needed a PR event (replay-diff vs merge-base, fire-and-forget on
# the tunables diff) run in DIFF MODE when CI_BASE_REF is set:
#   CI_BASE_REF=<sha|ref> scripts/ci-local.sh
#
# Exit nonzero on the first failing gate. Keep this script dependency-light:
# python3.12+ with the repo's [dev] extras, g++, kubectl/kustomize.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY="${PYTHON:-.venv/bin/python}"
RUFF="${RUFF:-.venv/bin/ruff}"
if [ ! -x "$PY" ]; then
  PY="$(command -v "$PY" 2>/dev/null || command -v python3)"
fi
if [ ! -x "$RUFF" ]; then RUFF="$PY -m ruff"; fi

step() { printf '\n== %s ==\n' "$1"; }

step "ruff lint"
$RUFF check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/

step "ruff format"
$RUFF format --check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/

step "portable schema / logic / contract suite"
make PYTHON="$PY" test

step "optional disposable-DB schema drift guards"
if [ "${VERDIFY_TEST_DISPOSABLE_DB:-}" = "1" ] && [ -n "${POSTGRES_HOST:-}" ]; then
  $PY -m pytest -q \
    verdify_schemas/tests/test_drift_guards.py \
    verdify_schemas/tests/test_relationships.py \
    -m live_db
else
  echo "SKIP: set VERDIFY_TEST_DISPOSABLE_DB=1 with POSTGRES_HOST for the DB-backed drift guards"
fi

step "migration rollback safety classification"
$PY scripts/check_migration_rollback_safety.py

step "grafana dashboard CMs match JSON sources (#392)"
$PY scripts/gen-grafana-dashboard-cms.py --check

step "solar site constants SSOT guard (#393)"
$PY scripts/check-solar-constants.py

step "twin vendored source compiles (the initContainer's exact build)"
if command -v g++ >/dev/null; then
  g++ -std=c++17 -fsyntax-only \
    -Ideploy/k8s/components/firmware-twin/src \
    deploy/k8s/components/firmware-twin/src/replay_emit.cpp
else
  echo "SKIP: g++ not available on this host (required in the CI image)"
fi

step "prod overlay renders"
if command -v kustomize >/dev/null; then
  kustomize build deploy/k8s/overlays/prod > /dev/null
elif command -v kubectl >/dev/null; then
  kubectl kustomize deploy/k8s/overlays/prod > /dev/null
else
  echo "SKIP: no kustomize/kubectl on this host (required in the CI image)"
fi

# ── Diff-scoped gates (merge-base semantics from the retired PR workflows) ──
if [ -n "${CI_BASE_REF:-}" ]; then
  BASE=$(git merge-base "${CI_BASE_REF}" HEAD)

  step "no-new-fire-and-forget (rule 6, num_* + sw_*) vs ${BASE}"
  NEW_IDS=$(git diff "$BASE" HEAD -- firmware/greenhouse/tunables.yaml 2>/dev/null \
    | grep -oE '^\+[[:space:]]+id:[[:space:]]+(num|sw)_[a-z0-9_]+' \
    | sed -E 's/.*id:[[:space:]]+(num|sw)_//' | sort -u || true)
  MISSING=""
  for id in $NEW_IDS; do
    grep -qE "id: cfg_(sw_)?${id}\b" firmware/greenhouse/sensors.yaml || MISSING="$MISSING $id"
  done
  [ -z "$MISSING" ] || { echo "New tunables without cfg_* readback:$MISSING" >&2; exit 1; }

  step "service-restart drift guard (rule 7, schema -> runtime) vs ${BASE}"
  # #391: the retired ci.yml job grepped the bare word 'service' and
  # false-passed on ~every PR body. The structural contract lives in the guard.
  bash scripts/check-service-restart-drift.sh "$BASE" HEAD

  step "firmware replay-diff trigger check vs ${BASE}"
  CHANGED=$(git diff --name-only "$BASE" HEAD -- 'firmware/lib/*.h' 'firmware/lib/*.cpp' \
    firmware/greenhouse/controls.yaml firmware/greenhouse/tunables.yaml firmware/greenhouse/globals.yaml)
  if [ -n "$CHANGED" ]; then
    THRESHOLD_PCT="${THRESHOLD_PCT:-0}" bash scripts/firmware-replay-diff.sh "$BASE" HEAD
    if git diff --name-only "$BASE" HEAD -- firmware/lib/greenhouse_solar.h | grep -q .; then
      REPLAY_EMIT_BAND_DERIVE=1 THRESHOLD_PCT="${BAND_THRESHOLD_PCT:-0}" \
        bash scripts/firmware-replay-diff.sh "$BASE" HEAD
    fi
  else
    echo "no firmware-logic diff; replay gates not required"
  fi
fi

printf '\nALL REQUIRED PORTABLE GATES GREEN\n'
