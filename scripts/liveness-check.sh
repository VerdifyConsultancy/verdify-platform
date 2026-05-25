#!/usr/bin/env bash
# liveness-check.sh — Quick liveness probe for production monitoring
# Runs */5 via cron. Logs results. Alerts on failure.
set -uo pipefail

LOG="/srv/verdify/state/liveness.log"
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

# 1. DB accepting connections
DB_OK=$(docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -c "SELECT 1;" 2>/dev/null)
check "db" "$([ "$DB_OK" = "1" ] && echo ok || echo 'connection failed')"

# 2. Ingestor last insert <5min
CLIMATE_AGE=$(docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -c \
    "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate WHERE temp_avg IS NOT NULL;" 2>/dev/null)
check "ingestor" "$([ -n "$CLIMATE_AGE" ] && [ "$CLIMATE_AGE" -lt 300 ] && echo ok || echo "stale ${CLIMATE_AGE:-null}s")"

# 3. Climate action-log decision snapshots fresh
ACTION_AGE=$(docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -c \
    "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate_action_log;" 2>/dev/null)
check "climate-action-log" "$([ -n "$ACTION_AGE" ] && [ "$ACTION_AGE" -lt 300 ] && echo ok || echo "stale ${ACTION_AGE:-null}s")"

# 4. Climate action-log proof row complete enough for controller/band diagnosis
ACTION_PROOF_RAW=$(docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -c \
    "WITH latest AS (
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
           CASE WHEN relay_truth IS NULL OR jsonb_typeof(relay_truth) <> 'object' OR relay_truth = '{}'::jsonb THEN 'relay_truth' END
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
