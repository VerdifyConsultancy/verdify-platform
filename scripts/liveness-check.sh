#!/usr/bin/env bash
# liveness-check.sh — Quick liveness probe for production monitoring
# Runs */5 via cron. Logs results. Alerts on failure.
set -uo pipefail

LOG="/srv/verdify/state/liveness.log"
DB_STATEMENT_TIMEOUT_MS="${VERDIFY_DB_STATEMENT_TIMEOUT_MS:-5000}"
FAIL=0
NOW=$(date '+%Y-%m-%d %H:%M:%S')

check() {
    local name="$1" result="$2"
    if [ "$result" = "ok" ]; then
        echo "$NOW OK $name" >> "$LOG"
    else
        echo "$NOW FAIL $name: $result" >> "$LOG"
        ((FAIL++))
    fi
}

# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves the exact prior VM argv:
#   docker exec -e PGOPTIONS=... verdify-timescaledb psql -U verdify -d verdify).
# The PGOPTIONS statement-timeout is passed as a docker-exec extra flag in
# docker-exec mode; PGOPTIONS is also exported so direct/in-cluster psql honors
# the same statement timeout (where the -e extra is ignored).
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
export PGOPTIONS="-c statement_timeout=${DB_STATEMENT_TIMEOUT_MS}"
mapfile -t DB < <(verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=${DB_STATEMENT_TIMEOUT_MS}")
db() {
    "${DB[@]}" -t -A -c "$1"
}

# 1. DB accepting connections
DB_OK=$(db "SELECT 1;" 2>/dev/null)
check "db" "$([ "$DB_OK" = "1" ] && echo ok || echo 'connection failed')"

# 2. Ingestor last insert <5min
CLIMATE_AGE=$(db "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate WHERE temp_avg IS NOT NULL;" 2>/dev/null)
check "ingestor" "$([ -n "$CLIMATE_AGE" ] && [ "$CLIMATE_AGE" -lt 300 ] && echo ok || echo "stale ${CLIMATE_AGE:-null}s")"

# 3. Climate action-log decision snapshots fresh
ACTION_AGE=$(db "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate_action_log;" 2>/dev/null)
check "climate-action-log" "$([ -n "$ACTION_AGE" ] && [ "$ACTION_AGE" -lt 300 ] && echo ok || echo "stale ${ACTION_AGE:-null}s")"

# 4. Climate action-log proof row complete enough for controller/band diagnosis
ACTION_PROOF_RAW=$(db "WITH latest AS (
       SELECT *
         FROM climate_action_log
        ORDER BY ts DESC
        LIMIT 1
     )
     SELECT COALESCE(
       (
         SELECT concat_ws(',',
           CASE WHEN climate_action IS NULL OR climate_action = '' THEN 'climate_action' END,
           CASE WHEN priority_axis IS NULL OR priority_axis = '' THEN 'priority_axis' END,
           CASE WHEN climate_intent_version IS NULL OR climate_intent_version = '' THEN 'climate_intent_version' END,
           CASE WHEN temp_low_f IS NULL THEN 'temp_low_f' END,
           CASE WHEN temp_target_f IS NULL THEN 'temp_target_f' END,
           CASE WHEN temp_high_f IS NULL THEN 'temp_high_f' END,
           CASE WHEN vpd_low_kpa IS NULL THEN 'vpd_low_kpa' END,
           CASE WHEN vpd_target_kpa IS NULL THEN 'vpd_target_kpa' END,
           CASE WHEN vpd_high_kpa IS NULL THEN 'vpd_high_kpa' END,
           CASE WHEN temp_target_delta_f IS NULL THEN 'temp_target_delta_f' END,
           CASE WHEN vpd_target_delta_kpa IS NULL THEN 'vpd_target_delta_kpa' END,
           CASE WHEN temp_band_error_f IS NULL THEN 'temp_band_error_f' END,
           CASE WHEN vpd_band_error_kpa IS NULL THEN 'vpd_band_error_kpa' END,
           CASE WHEN relay_truth IS NULL OR jsonb_typeof(relay_truth) <> 'object' OR relay_truth = '{}'::jsonb THEN 'relay_truth' END,
           CASE WHEN sensor_status IS NULL OR jsonb_typeof(sensor_status) <> 'object' OR sensor_status = '{}'::jsonb THEN 'sensor_status' END,
           CASE WHEN sensor_status->>'latest_climate_ts' IS NULL OR sensor_status->>'latest_climate_ts' = '' THEN 'sensor_status.latest_climate_ts' END,
           CASE
             WHEN CASE
               WHEN sensor_status->>'latest_climate_age_s' ~ '^[0-9]+$'
               THEN (sensor_status->>'latest_climate_age_s')::int < 300
               ELSE false
             END IS NOT true THEN 'sensor_status.latest_climate_age_s'
           END,
           CASE WHEN sensor_status->>'temp_avg_present' IS DISTINCT FROM 'true' THEN 'sensor_status.temp_avg_present' END,
           CASE WHEN sensor_status->>'vpd_avg_present' IS DISTINCT FROM 'true' THEN 'sensor_status.vpd_avg_present' END,
           CASE WHEN sensor_status->>'band_context_complete' IS DISTINCT FROM 'true' THEN 'sensor_status.band_context_complete' END
         )
         FROM latest
       ),
       'missing'
     );" 2>/dev/null)
ACTION_PROOF_RC=$?
ACTION_PROOF_MISSING=$(printf '%s' "$ACTION_PROOF_RAW" | tr -d '[:space:]')
if [ "$ACTION_PROOF_RC" -ne 0 ]; then ACTION_PROOF_MISSING="query_failed"; fi
check "climate-action-proof" "$([ -z "$ACTION_PROOF_MISSING" ] && echo ok || echo "incomplete ${ACTION_PROOF_MISSING:-missing}")"

# 5. Setpoint-server responding
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8200/setpoints 2>/dev/null)
check "setpoint-server" "$([ "$HTTP" = "200" ] && echo ok || echo "http $HTTP")"

# 6. Grafana responding
GF=$(docker exec verdify-grafana curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>/dev/null)
check "grafana" "$([ "$GF" = "200" ] && echo ok || echo "http $GF")"

# 7. ESP32 reachable
PING=$(ping -c 1 -W 2 192.168.10.111 > /dev/null 2>&1 && echo ok || echo unreachable)
check "esp32" "$PING"

# 8. Systemd services active
for svc in verdify-ingestor verdify-setpoint-server; do
    ST=$(systemctl is-active "$svc" 2>/dev/null)
    check "$svc" "$([ "$ST" = "active" ] && echo ok || echo "$ST")"
done

# Alert on failure
if [ "$FAIL" -gt 0 ]; then
    echo "$NOW ALERT: $FAIL liveness checks failed" >> "$LOG"
    # Could post to Slack here if desired
fi

# Rotate log (keep last 2000 lines)
tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit "$FAIL"
