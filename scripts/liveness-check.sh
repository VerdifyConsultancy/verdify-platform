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

# 4. Setpoint-server responding
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8200/setpoints 2>/dev/null)
check "setpoint-server" "$([ "$HTTP" = "200" ] && echo ok || echo "http $HTTP")"

# 5. Grafana responding
GF=$(docker exec verdify-grafana curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>/dev/null)
check "grafana" "$([ "$GF" = "200" ] && echo ok || echo "http $GF")"

# 6. ESP32 reachable
PING=$(ping -c 1 -W 2 192.168.10.111 > /dev/null 2>&1 && echo ok || echo unreachable)
check "esp32" "$PING"

# 7. Systemd services active
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
