#!/usr/bin/env bash
# Iris health check — Verdify platform operational status
# Usage: ./health-check.sh
# Checks: containers, services, data freshness, ESP32, alerts, planning, cron
set -uo pipefail

DB="docker exec verdify-timescaledb psql -U verdify -d verdify -t -c"
PASS=0; FAIL=0; WARN=0

check() {
  local label="$1" result="$2"
  if [ "$result" = "true" ] || [ "$result" = "pass" ]; then
    echo "  ✓ $label"; ((PASS++))
  else
    echo "  ✗ $label — $result"; ((FAIL++))
  fi
}

warn() { echo "  ⚠ $1"; ((WARN++)); }

echo "=== Verdify Health Check ($(date '+%Y-%m-%d %H:%M')) ==="
echo ""

# ── Docker ──
echo "Docker:"
for c in verdify-timescaledb verdify-grafana verdify-traefik; do
  s=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
  check "$c" "$([ "$s" = "running" ] && echo true || echo "$s")"
done

# ── Services ──
echo "Services:"
for svc in verdify-ingestor verdify-setpoint-server; do
  a=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
  check "$svc" "$([ "$a" = "active" ] && echo true || echo "$a")"
done

# ── Database objects ──
echo "Database:"
tc=$($DB "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE '_hyper_%';" | tr -d ' ')
check "Tables: $tc (expect 27)" "$([ "$tc" -ge 27 ] && echo true || echo "only $tc")"

vc=$($DB "SELECT count(*) FROM pg_views WHERE schemaname='public';" | tr -d ' ')
mc=$($DB "SELECT count(*) FROM pg_matviews WHERE schemaname='public';" | tr -d ' ')
tv=$((vc + mc))
check "Views: $tv (expect ≥25)" "$([ "$tv" -ge 25 ] && echo true || echo "only $tv")"

# ── Data freshness ──
echo "Freshness:"
ca=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate WHERE temp_avg IS NOT NULL;" | tr -d ' ')
check "Climate: ${ca}s ago (<600s)" "$([ "$ca" -lt 600 ] && echo true || echo "stale: ${ca}s")"

ea=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM equipment_state;" | tr -d ' ')
check "Equipment: ${ea}s ago (<86400s)" "$([ "$ea" -lt 86400 ] && echo true || echo "stale: ${ea}s")"

da=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM diagnostics;" | tr -d ' ')
check "Diagnostics: ${da}s ago (<300s)" "$([ "$da" -lt 300 ] && echo true || echo "stale: ${da}s")"

aa=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate_action_log;" | tr -d ' ')
check "Climate action log: ${aa:-no data}s ago (<300s)" "$([ -n "$aa" ] && [ "$aa" -lt 300 ] && echo true || echo "stale: ${aa:-no data}s")"

fc=$($DB "SELECT count(*) FROM weather_forecast WHERE ts > now();" | tr -d ' ')
check "Future forecasts: $fc rows" "$([ "$fc" -gt 0 ] && echo true || echo "none")"

# ── ESP32 ──
echo "ESP32:"
hp=$($DB "SELECT ROUND(heap_bytes::numeric,0) FROM diagnostics ORDER BY ts DESC LIMIT 1;" | tr -d ' ')
check "Heap: ${hp}KB (>30KB)" "$([ "${hp%.*}" -gt 30 ] && echo true || echo "LOW: ${hp}KB")"

wi=$($DB "SELECT wifi_rssi FROM diagnostics ORDER BY ts DESC LIMIT 1;" | tr -d ' ')
check "WiFi: ${wi}dBm (>-75)" "$(echo "$wi" | awk '{print ($1>-75)?"true":"weak: "$1"dBm"}')"

# ── Alerts ──
echo "Alerts:"
oa=$($DB "SELECT count(*) FROM alert_log WHERE disposition='open';" | tr -d ' ')
cr=$($DB "SELECT count(*) FROM alert_log WHERE disposition='open' AND severity='critical';" | tr -d ' ')
if [ "$cr" -gt 0 ]; then check "Open: $oa ($cr CRITICAL)" "CRITICAL"
elif [ "$oa" -gt 20 ]; then warn "Open alerts: $oa (high)"
else check "Open alerts: $oa" true; fi

# ── Planning ──
echo "Planning:"
pf=$($DB "SELECT count(*) FROM setpoint_plan WHERE ts > now() AND parameter!='plan_metadata';" | tr -d ' ')
check "Future waypoints: $pf" "$([ "$pf" -gt 0 ] && echo true || echo "no plan")"

hs=$($DB "SELECT fn_system_health();" | tr -d ' ')
check "Health score: $hs/100 (≥50)" "$([ "$hs" -ge 50 ] && echo true || echo "LOW: $hs")"

# ── Pipeline tasks (absorbed into ingestor task_loop) ──
echo "Pipeline tasks:"
ta=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate WHERE outdoor_temp_f IS NOT NULL AND ts > now() - interval '1 hour';" | tr -d ' ')
check "tempest-sync: ${ta}s ago (<600s)" "$([ -n "$ta" ] && [ "$ta" -lt 600 ] && echo true || echo "stale: ${ta:-no data}s")"

ea=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM energy WHERE ts > now() - interval '1 hour';" | tr -d ' ')
check "shelly-sync: ${ea}s ago (<600s)" "$([ -n "$ea" ] && [ "$ea" -lt 600 ] && echo true || echo "stale: ${ea:-no data}s")"

ha=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(ts))::int FROM climate WHERE hydro_ph IS NOT NULL AND ts > now() - interval '1 day';" | tr -d ' ')
check "ha-sensor-sync: ${ha:-no data}s ago (<1800s)" "$([ -n "$ha" ] && [ "$ha" -lt 1800 ] && echo true || echo "stale: ${ha:-no data}s")"

# Alert monitor: check if ingestor is running (it contains the alert task)
ia=$(systemctl is-active verdify-ingestor 2>/dev/null)
check "alert-monitor: ingestor $ia" "$([ "$ia" = "active" ] && echo true || echo "$ia")"

# Dispatcher: only writes when plan changes exist. Check ingestor is alive (dispatcher runs inside it).
check "setpoint-dispatcher: ingestor $ia" "$([ "$ia" = "active" ] && echo true || echo "$ia")"

fa=$($DB "SELECT EXTRACT(EPOCH FROM now()-max(fetched_at))::int FROM weather_forecast;" | tr -d ' ')
check "forecast-sync: ${fa}s ago (<7200s)" "$([ -n "$fa" ] && [ "$fa" -lt 7200 ] && echo true || echo "stale: ${fa:-no data}s")"

echo ""
echo "=== $PASS passed, $FAIL failed, $WARN warnings ==="
[ "$FAIL" -eq 0 ] && echo "HEALTHY" || echo "ISSUES DETECTED"
exit "$FAIL"
