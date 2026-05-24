#!/usr/bin/env bash
# validate-plan-coverage.sh — Verify tactical Tier 1 params present at every transition
set -euo pipefail

DB="docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -c"
PYTHON_BIN="${PYTHON:-/srv/greenhouse/.venv/bin/python}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The tactical Tier 1 params and dispatcher-owned exclusion set come from the
# tunable registry. This keeps plan coverage validation aligned with MCP,
# dispatcher ownership checks, and firmware setpoint routing.
mapfile -t REGISTRY_LINES < <("$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))

from verdify_schemas.tunable_registry import BAND_OWNED_REG, TIER1_REG  # noqa: E402

core = ",".join(sorted(TIER1_REG))
dispatcher_owned = sorted(BAND_OWNED_REG)
sql_list = ",".join("'" + name.replace("'", "''") + "'" for name in dispatcher_owned)
print(core)
print(sql_list)
print(len(TIER1_REG))
PY
)
CORE="${REGISTRY_LINES[0]}"
BAND_OWNED="${REGISTRY_LINES[1]}"
CORE_COUNT="${REGISTRY_LINES[2]}"
LEGACY_BIAS_PARAMS="'bias_heat_f','bias_cool_f'"

# 1. Get latest active routine plan_id with the full tactical Tier 1 surface.
# Tactical one-shot writes can supersede one parameter after a routine plan; they
# remain guarded by planner-pushable checks but must not be treated as full-plan
# coverage candidates.
LATEST=$($DB "
WITH candidate AS (
  SELECT plan_id,
         max(created_at) AS latest_created,
         count(DISTINCT parameter) FILTER (WHERE parameter = ANY(string_to_array('$CORE', ','))) AS core_params
    FROM setpoint_plan
   WHERE is_active = true
     AND plan_id IS NOT NULL
   GROUP BY plan_id
)
SELECT plan_id
  FROM candidate
 WHERE core_params = $CORE_COUNT
 ORDER BY latest_created DESC
 LIMIT 1;
" | tr -d ' ')

if [ -z "$LATEST" ]; then
    echo "WARN: No active plans found"
    exit 1
fi

# 2. For each distinct timestamp, check all tactical Tier 1 params exist
RESULT=$($DB "
WITH core(param) AS (
  SELECT unnest(string_to_array('$CORE', ',')) AS param
),
plan_ts AS (
  SELECT DISTINCT ts FROM setpoint_plan WHERE plan_id = '$LATEST' AND is_active = true
),
expected AS (
  SELECT pt.ts, c.param FROM plan_ts pt CROSS JOIN core c
),
actual AS (
  SELECT ts, parameter FROM setpoint_plan WHERE plan_id = '$LATEST' AND is_active = true
),
missing AS (
  SELECT e.ts, e.param
  FROM expected e
  LEFT JOIN actual a ON e.ts = a.ts AND e.param = a.parameter
  WHERE a.parameter IS NULL
)
SELECT
  (SELECT count(DISTINCT ts) FROM plan_ts),
  (SELECT count(DISTINCT ts) FROM plan_ts) - (SELECT count(DISTINCT ts) FROM missing),
  coalesce((SELECT string_agg(param || ' @ ' || to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH:MI AM'), '; ' ORDER BY ts, param) FROM missing), '');
")

TRANSITIONS=$(echo "$RESULT" | cut -d'|' -f1 | tr -d ' ')
COMPLETE=$(echo "$RESULT" | cut -d'|' -f2 | tr -d ' ')
MISSING_LIST=$(echo "$RESULT" | cut -d'|' -f3)

echo "routine_plan_id: $LATEST"
echo "transitions: ${TRANSITIONS:-0}"
echo "complete: ${COMPLETE:-0}"

BAND_PRESENT=$($DB "
SELECT coalesce(string_agg(DISTINCT parameter || ':' || coalesce(plan_id, '<null>'), ',' ORDER BY parameter || ':' || coalesce(plan_id, '<null>')), '')
FROM setpoint_plan
WHERE is_active = true
  AND parameter IN ($BAND_OWNED);
" | tr -d ' ')
if [ -n "$BAND_PRESENT" ]; then
    echo "BAND_OWNED_PRESENT: $BAND_PRESENT"
    echo "Dispatcher-owned band/lighting params are read-only context and must not be in active planner waypoints."
    exit 1
fi

# Check if plan uses old param names (bias_heat_f vs bias_heat)
HAS_OLD=$($DB "SELECT count(*) FROM setpoint_plan WHERE plan_id = '$LATEST' AND parameter IN ($LEGACY_BIAS_PARAMS);" | tr -d ' ')
if [ "${HAS_OLD:-0}" -gt 0 ] && [ -n "$MISSING_LIST" ]; then
    echo "Plan uses pre-Tier-1 naming (bias_heat_f). Coverage validation applies to new schema plans only."
    exit 0
elif [ -n "$MISSING_LIST" ]; then
    echo "MISSING: $MISSING_LIST"
    exit 1
else
    echo "All transitions have full coverage of $CORE_COUNT tactical Tier 1 params; no dispatcher-owned params present."
    exit 0
fi
