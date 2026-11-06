#!/usr/bin/env bash
# GHA registry steps: the five checks.steps of .agent-fleet/ci.yaml (the native
# repo-validate profile from the jvallery/agents registry record), one per job.
# Commands are copied unchanged from that file; keep them in step with it until
# .agent-fleet/ci.yaml is removed (#1397 step 5), then this script is the source.
#
#   ci/gha/registry-step.sh platform-static|platform-tests|campaign-qualification
#                           |firmware-logic|firmware-secretless-compile
#
# Job images (estate-ci runners.yaml, repository jvallery/verdify-platform):
#   firmware-secretless-compile  verdify-platform-ci-esphome (ESPHome 2026.6.5)
#   the other four               verdify-platform-ci-tools (agents-ci-tools)
. "$(dirname "$0")/lib.sh"
step="${1:?usage: registry-step.sh <step>}"
case "$step" in
  platform-static)
    uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv run ruff check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/ && uv run ruff format --check ingestor/ api/ mcp/ scripts/*.py tests/ verdify_schemas/
    ;;
  platform-tests)
    ci_require_runner_tools  # two tests run `kustomize build`
    export PYTHONPATH="$PWD" && uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv pip install --python .venv/bin/python -r ingestor/requirements-image.txt && uv run pytest -q verdify_schemas/tests/ --ignore=verdify_schemas/tests/test_climate_intent.py --ignore=verdify_schemas/tests/test_drift_guards.py --ignore=verdify_schemas/tests/test_mcp_responses.py --ignore=verdify_schemas/tests/test_relationships.py --ignore=verdify_schemas/tests/test_tunables.py --ignore=verdify_schemas/tests/test_vault_writers.py --ignore=verdify_schemas/tests/test_views.py && uv run pytest -q tests/test_device_write_gate.py tests/test_migration_rollback_safety.py tests/test_14_site_doctor.py tests/test_climate_intent_replay_evaluator.py tests/test_g5_dualwrite_validation.py tests/test_generate_daily_plan.py tests/test_lab_generator_standardization.py tests/test_lab_publish_k3s_guard.py tests/test_mqtt_fanout.py tests/test_planner_memory_ingest.py tests/test_site_content_refresh.py tests/test_slack_config.py tests/test_slack_ops.py tests/test_soil_dryout_alert.py tests/test_tempest_sync.py tests/test_writer_lease_fence.py tests/test_component_grid_capture.py tests/test_component_prefix_replay.py
    ;;
  campaign-qualification)
    # The native report path (/workspace) does not exist on ARC; keep it private.
    export CI_TEST_REPORT="${CI_TEST_REPORT:-$HOME/ci-test-report.json}"
    export PYTHONPATH="$PWD" && uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv pip install --python .venv/bin/python -r ingestor/requirements-image.txt && python3 /usr/local/bin/verdify-campaign-ci
    ;;
  firmware-logic)
    bash -lc ' set -euo pipefail; test -f firmware/test/data/replay_overrides.csv || gunzip -k firmware/test/data/replay_overrides.csv.gz; cd firmware; g++ -std=c++17 -I lib -o test/test_greenhouse test/test_greenhouse_logic.cpp; ./test/test_greenhouse; g++ -std=c++17 -O2 -I lib -o test/replay_overrides test/replay_overrides.cpp; ./test/replay_overrides test/data/replay_overrides.csv | tail -30; g++ -std=c++17 -O2 -I lib -o test/replay_invariants test/replay_invariants.cpp; set +e; ./test/replay_invariants test/data/replay_overrides.csv; rc=$?; set -e; if [ "$rc" -eq 2 ]; then
  echo "replay corpus predates invariant schema; accepted legacy corpus";
  exit 0;
fi; exit "$rc" '
    ;;
  firmware-secretless-compile)
    # The registry's inline program in its own file (ruff-formatted only) (R2: no logic in YAML).
    python3 ci/gha/firmware_secretless_compile.py
    ;;
  *)
    echo "unknown registry step ${step}" >&2
    exit 2
    ;;
esac
