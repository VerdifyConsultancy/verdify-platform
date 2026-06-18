#!/usr/bin/env bash
# Repeatable proof for the firmware audit traceability closure.
#
# This intentionally checks both static worktree surfaces and live/generated
# state. It does not deploy or restart services.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv). Heredoc SQL needs stdin -> VERDIFY_DOCKER_STDIN=1.
. "$REPO_ROOT/scripts/lib/psql-verdify.sh"
mapfile -t DB_CMD < <(VERDIFY_DOCKER_STDIN=1 verdify_psql_cmd)
DB_CMD+=(-t -A -F '|' -v ON_ERROR_STOP=1)
AI_TUNABLES_PAGE="${AI_TUNABLES_PAGE:-/srv/verdify/verdify-site/content/reference/ai-tunables.md}"
LESSONS_PAGE="${LESSONS_PAGE:-/srv/verdify/verdify-site/content/reference/lessons.md}"
LIVE_SOURCE_ROOT="${LIVE_SOURCE_ROOT:-/srv/verdify}"
ALLOW_LIVE_SOURCE_DRIFT="${FIRMWARE_AUDIT_ALLOW_LIVE_SOURCE_DRIFT:-0}"

LIVE_SOURCE_PARITY_FILES=(
  Makefile
  api/main.py
  db/schema.sql
  docs/planner/greenhouse-playbook.md
  firmware/greenhouse.yaml
  firmware/greenhouse/controls.yaml
  firmware/greenhouse/globals.yaml
  firmware/greenhouse/sensors.yaml
  firmware/greenhouse/tunables.yaml
  firmware/lib/greenhouse_logic.h
  firmware/lib/greenhouse_types.h
  firmware/test/invariants.h
  firmware/test/replay_invariants.cpp
  firmware/test/replay_overrides.cpp
  ingestor/entity_map.py
  ingestor/ingestor.py
  ingestor/iris_planner.py
  ingestor/tasks.py
  mcp/server.py
  scripts/audit-tunable-traceability.py
  scripts/export-hourly-performance-dataset.py
  scripts/gather-plan-context.sh
  scripts/generate-ai-tunables-page.py
  scripts/generate-daily-plan.py
  scripts/planner-core-params.md
  scripts/publish-site-content.sh
  scripts/site-doctor.py
  scripts/smoke-feedback-loop.py
  scripts/update-evidence-snapshots.py
  scripts/validate-plan-coverage.sh
  verdify_schemas/crops.py
  verdify_schemas/mcp_responses.py
  verdify_schemas/operations.py
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
planner_pushable_sql="$("$PYTHON_BIN" - <<'PY'
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG

def quote_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

print(", ".join(quote_sql(param) for param in sorted(PLANNER_PUSHABLE_REG)))
PY
)"
active_nonpushable_rows="$("${DB_CMD[@]}" <<SQL
SELECT parameter, plan_id, value
FROM v_active_plan
WHERE parameter NOT IN (${planner_pushable_sql})
ORDER BY parameter;
SQL
)"
if [ -n "$active_nonpushable_rows" ]; then
  echo "$active_nonpushable_rows"
  fail "v_active_plan contains non-planner-pushable row(s); retire stale setpoint_plan rows or update the registry contract"
fi
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
  SELECT 'active_crop_work_missing_position',
         count(*) = 0,
         count(*)::text
  FROM (
    SELECT o.id
    FROM observations o
    JOIN crops c ON c.id = o.crop_id
    WHERE c.is_active = true
      AND o.position_id IS NULL
    UNION ALL
    SELECT e.id
    FROM crop_events e
    JOIN crops c ON c.id = e.crop_id
    WHERE c.is_active = true
      AND e.position_id IS NULL
    UNION ALL
    SELECT h.id
    FROM harvests h
    JOIN crops c ON c.id = h.crop_id
    WHERE c.is_active = true
      AND h.position_id IS NULL
    UNION ALL
    SELECT t.id
    FROM treatments t
    JOIN crops c ON c.id = t.crop_id
    WHERE c.is_active = true
      AND t.position_id IS NULL
  ) missing
  UNION ALL
  SELECT 'crop_work_tenant_mismatch',
         count(*) = 0,
         count(*)::text
  FROM (
    SELECT o.id
    FROM observations o
    JOIN crops c ON c.id = o.crop_id
    WHERE o.greenhouse_id IS DISTINCT FROM c.greenhouse_id
    UNION ALL
    SELECT e.id
    FROM crop_events e
    JOIN crops c ON c.id = e.crop_id
    WHERE e.greenhouse_id IS DISTINCT FROM c.greenhouse_id
    UNION ALL
    SELECT h.id
    FROM harvests h
    JOIN crops c ON c.id = h.crop_id
    WHERE h.greenhouse_id IS DISTINCT FROM c.greenhouse_id
    UNION ALL
    SELECT t.id
    FROM treatments t
    JOIN crops c ON c.id = t.crop_id
    WHERE t.greenhouse_id IS DISTINCT FROM c.greenhouse_id
  ) mismatch
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
  UNION ALL
  SELECT 'active_lessons_without_embedding',
         count(*) = 0,
         count(*)::text
  FROM planner_lessons l
  WHERE l.is_active = true
    AND l.superseded_by IS NULL
    AND NOT EXISTS (
      SELECT 1
      FROM verdify_embeddings e
      WHERE e.source_type = 'lesson'
        AND e.source_id = l.id::text
    )
  UNION ALL
  SELECT 'site_docs_without_embedding',
         count(*) = 0,
         count(*)::text
  FROM site_content s
  WHERE NOT EXISTS (
    SELECT 1
    FROM verdify_embeddings e
    WHERE e.source_type = 'site_doc'
      AND e.source_id = s.page_path
  )
  UNION ALL
  SELECT 'playbook_chunks_without_embedding',
         count(*) = 0,
         count(*)::text
  FROM playbook_content p
  WHERE NOT EXISTS (
    SELECT 1
    FROM verdify_embeddings e
    WHERE e.source_type = 'playbook'
      AND e.source_id = p.source_path
      AND e.chunk_idx = p.chunk_idx
  )
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
routine_section="$(awk '/^## ClimateIntent Materialization Contract/{flag=1} /^## Findings That Matter/{flag=0} flag' "$AI_TUNABLES_PAGE")"
[ -n "$routine_section" ] || fail "ClimateIntent Materialization Contract section missing"
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
