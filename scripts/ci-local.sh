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
if [ ! -x "$PY" ]; then PY=python3; fi
if [ ! -x "$RUFF" ]; then RUFF="$PY -m ruff"; fi

step() { printf '\n== %s ==\n' "$1"; }

step "ruff lint"
$RUFF check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/

step "ruff format"
$RUFF format --check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/

step "schema suites (incl. drift guards + producer payload round-trips)"
$PY -m pytest -q verdify_schemas/tests/

step "device write gate"
$PY -m pytest -q tests/test_device_write_gate.py

step "pure-logic / contract tests"
$PY -m pytest -q \
  tests/test_10_planner_routing.py \
  tests/test_11_planner_milestones.py \
  tests/test_13_grafana_band_traceability.py \
  tests/test_14_shadow_mode.py \
  tests/test_14_site_doctor.py \
  tests/test_17_planner_health_surface.py \
  tests/test_18_twin_divergence_dashboard.py \
  tests/test_19_firmware_twin_shadow_src_sync.py \
  tests/test_20_vision_src_sync.py \
  tests/test_action_log_policy_identity.py \
  tests/test_anchor_service_sync.py \
  tests/test_api_public_output_policy.py \
  tests/test_climate_intent_replay_evaluator.py \
  tests/test_compliance_feasibility_classifier.py \
  tests/test_esp32_policy_transaction.py \
  tests/test_experiment_schema_migration.py \
  tests/test_experiment_workers.py \
  tests/test_firmware_crop_agnostic_guard.py \
  tests/test_forecast_action_engine_path.py \
  tests/test_g5_dualwrite_validation.py \
  tests/test_generate_daily_plan.py \
  tests/test_grafana_cm_check.py \
  tests/test_grafana_manifest_security.py \
  tests/test_lab_publish_k3s_guard.py \
  tests/test_mcp_audience_auth.py \
  tests/test_mqtt_fanout.py \
  tests/test_no_hosted_runner_workflows.py \
  tests/test_planner_memory_ingest.py \
  tests/test_policy_arbiter_migration.py \
  tests/test_policy_arbiter_worker.py \
  tests/test_policy_delivery_worker.py \
  tests/test_policy_writer_demotion.py \
  tests/test_psql_verdify_backend.py \
  tests/test_publish_site_content_guard.py \
  tests/test_public_output_generators.py \
  tests/test_public_output_guard.py \
  tests/test_public_output_policy.py \
  tests/test_public_output_remediation.py \
  tests/test_public_zone_renderer.py \
  tests/test_service_restart_drift_guard.py \
  tests/test_setpoint_server_db_backend.py \
  tests/test_site_content_refresh.py \
  tests/test_slack_config.py \
  tests/test_slack_ops.py \
  tests/test_soil_dryout_alert.py \
  tests/test_solar_band_anchors.py \
  tests/test_solar_constants_check.py \
  tests/test_tempest_sync.py \
  tests/test_writer_lease_fence.py

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

printf '\nALL GATES GREEN\n'
