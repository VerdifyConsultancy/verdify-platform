#!/usr/bin/env bash
# Repeatable proof for the firmware audit traceability closure.
#
# This intentionally checks both static worktree surfaces and live/generated
# state. It does not deploy or restart services.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-/srv/greenhouse/.venv/bin/python}"
DB_CMD=(docker exec -i verdify-timescaledb psql -U verdify -d verdify -t -A -F '|' -v ON_ERROR_STOP=1)
AI_TUNABLES_PAGE="${AI_TUNABLES_PAGE:-/mnt/iris/verdify-vault/website/reference/ai-tunables.md}"
LESSONS_PAGE="${LESSONS_PAGE:-/mnt/iris/verdify-vault/website/reference/lessons.md}"
LIVE_SOURCE_ROOT="${LIVE_SOURCE_ROOT:-/srv/verdify}"
ALLOW_LIVE_SOURCE_DRIFT="${FIRMWARE_AUDIT_ALLOW_LIVE_SOURCE_DRIFT:-0}"

LIVE_SOURCE_PARITY_FILES=(
  api/main.py
  db/schema.sql
  firmware/greenhouse.yaml
  firmware/greenhouse/controls.yaml
  firmware/greenhouse/globals.yaml
  firmware/greenhouse/sensors.yaml
  firmware/greenhouse/tunables.yaml
  firmware/lib/greenhouse_logic.h
  firmware/lib/greenhouse_types.h
  ingestor/entity_map.py
  ingestor/ingestor.py
  ingestor/iris_planner.py
  ingestor/tasks.py
  scripts/audit-tunable-traceability.py
  scripts/gather-plan-context.sh
  scripts/generate-ai-tunables-page.py
  scripts/planner-core-params.md
  scripts/smoke-feedback-loop.py
  verdify_schemas/telemetry.py
  verdify_schemas/tunable_registry.py
)

cd "$REPO_ROOT"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

section() {
  echo ""
  echo "== $* =="
}

section "registry and route audit"
"$PYTHON_BIN" scripts/audit-tunable-traceability.py

section "active plan coverage"
bash scripts/validate-plan-coverage.sh

section "live DB traceability"
db_checks="$("${DB_CMD[@]}" <<'SQL'
WITH checks AS (
  SELECT 'diagnostics_cols' AS check_name,
         (
           SELECT count(*) = 4
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'diagnostics'
             AND column_name IN (
               'effective_heat_target_f',
               'effective_cool_stage2_delta_f',
               'effective_vpd_hysteresis_kpa',
               'effective_dehum_aggressive_kpa'
             )
         ) AS ok,
         (
           SELECT string_agg(column_name, ',' ORDER BY column_name)
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'diagnostics'
             AND column_name IN (
               'effective_heat_target_f',
               'effective_cool_stage2_delta_f',
               'effective_vpd_hysteresis_kpa',
               'effective_dehum_aggressive_kpa'
             )
         ) AS detail
  UNION ALL
  SELECT 'tactical_view_retired_rows',
         count(*) = 0,
         count(*)::text
  FROM v_plan_tactical_outcome_daily
  WHERE parameter IN ('bias_heat','bias_cool','d_heat_stage_2','sw_fsm_controller_enabled')
  UNION ALL
  SELECT 'future_nonpushable_rows',
         count(*) = 0,
         count(*)::text
  FROM setpoint_plan
  WHERE is_active = true
    AND ts >= now()
    AND parameter IN (
      'bias_heat','bias_cool','d_heat_stage_2','sw_fsm_controller_enabled',
      'min_heat_on_s','min_heat_off_s','min_vent_on_s','min_vent_off_s'
    )
  UNION ALL
  SELECT 'active_plan_out_of_bounds_rows',
         count(*) = 0,
         count(*)::text
  FROM v_active_plan
  WHERE (parameter = 'fog_escalation_kpa' AND value > 0.5)
     OR (parameter = 'mister_water_budget_gal' AND value > 300)
     OR (parameter = 'mister_vpd_weight' AND value > 3.0)
     OR (parameter = 'mister_engage_delay_s' AND value > 300)
     OR (parameter = 'mister_all_delay_s' AND value > 600)
  UNION ALL
  SELECT 'latest_snapshot_out_of_bounds_rows',
         count(*) = 0,
         count(*)::text
  FROM (
    SELECT DISTINCT ON (parameter) parameter, value
    FROM setpoint_snapshot
    WHERE parameter IN (
      'fog_escalation_kpa','mister_water_budget_gal','mister_vpd_weight',
      'mister_engage_delay_s','mister_all_delay_s'
    )
    ORDER BY parameter, ts DESC
  ) s
  WHERE (parameter = 'fog_escalation_kpa' AND value > 0.5)
     OR (parameter = 'mister_water_budget_gal' AND value > 300)
     OR (parameter = 'mister_vpd_weight' AND value > 3.0)
     OR (parameter = 'mister_engage_delay_s' AND value > 300)
     OR (parameter = 'mister_all_delay_s' AND value > 600)
)
SELECT check_name, ok, detail FROM checks ORDER BY check_name;
SQL
)"
echo "$db_checks"
if echo "$db_checks" | awk -F'|' '$2 != "t" { found=1 } END { exit found ? 0 : 1 }'; then
  fail "one or more live DB traceability checks failed"
fi

section "generated AI tunables page"
[ -f "$AI_TUNABLES_PAGE" ] || fail "missing AI tunables page: $AI_TUNABLES_PAGE"
routine_section="$(awk '/^## Routine Plan Contract/{flag=1} /^## Findings That Matter/{flag=0} flag' "$AI_TUNABLES_PAGE")"
[ -n "$routine_section" ] || fail "Routine Plan Contract section missing"
if echo "$routine_section" | rg -n 'bias_heat|bias_cool|d_heat_stage_2|sw_fsm_controller_enabled' >/dev/null; then
  fail "retired params found in Routine Plan Contract"
fi
rg -n '\| `fog_escalation_kpa` \| `planner_policy` \| planner \| default `0\.4`; 0\.1 to 0\.5' "$AI_TUNABLES_PAGE"
rg -n '\| `mister_water_budget_gal` \| `planner_policy` \| planner \| default `300`; 100 to 300' "$AI_TUNABLES_PAGE"
for retired in bias_cool bias_heat d_heat_stage_2 sw_fsm_controller_enabled; do
  rg -n "\| \`${retired}\` \| \`retired\` .*MCP rejects planner writes; reserved/no-op" "$AI_TUNABLES_PAGE"
done

section "active lessons page"
[ -f "$LESSONS_PAGE" ] || fail "missing lessons page: $LESSONS_PAGE"
if rg -n 'bias_heat|bias_cool|d_heat_stage_2|fog_escalation_kpa 0\.(9|95)|fog_escalation_kpa 1' "$LESSONS_PAGE"; then
  fail "active lessons page still contains retired or out-of-range guidance"
fi
echo "active lessons page clean"

section "static documentation and prompts"
if rg -n 'increase bias|decrease bias|Use bias|bias_cool may|bias_cool \+|bias_heat \+|temp_high \+ bias_cool|band midpoint|raw temp-band midpoint|mister_water_budget_gal \| 200-500|fog_escalation_kpa \| 0\.2-0\.8|\[30, 900\]|\[60, 900\]' \
  docs/planner docs/tunable-cascade.md docs/VPD-PRIMARY-ARCHITECTURE.md scripts/planner-core-params.md ingestor/iris_planner.py; then
  fail "static docs or prompts contain stale firmware audit guidance"
fi
echo "static docs/prompts clean"

section "live source parity"
if [ ! -d "$LIVE_SOURCE_ROOT" ]; then
  fail "live source root missing: $LIVE_SOURCE_ROOT"
fi
source_drift=0
for rel in "${LIVE_SOURCE_PARITY_FILES[@]}"; do
  if [ ! -f "$LIVE_SOURCE_ROOT/$rel" ]; then
    echo "MISSING_IN_LIVE: $rel"
    source_drift=1
    continue
  fi
  if ! diff -q "$REPO_ROOT/$rel" "$LIVE_SOURCE_ROOT/$rel" >/dev/null; then
    echo "DIFFERS_FROM_LIVE: $rel"
    source_drift=1
  fi
done
if [ "$source_drift" -ne 0 ]; then
  if [ "$ALLOW_LIVE_SOURCE_DRIFT" = "1" ]; then
    echo "WARN: live source drift allowed by FIRMWARE_AUDIT_ALLOW_LIVE_SOURCE_DRIFT=1"
  else
    echo "Next steps: merge/deploy this worktree to ${LIVE_SOURCE_ROOT}, restart verdify-ingestor and verdify-mcp, regenerate the AI Tunables page from live source, then rerun this proof." >&2
    fail "live /srv source differs from audited worktree; merge/deploy/restart before claiming full closure"
  fi
else
  echo "live source matches audited worktree for runtime/generator files"
fi

section "summary"
echo "firmware audit traceability proof passed"
