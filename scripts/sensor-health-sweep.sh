#!/usr/bin/env bash
# sensor-health-sweep.sh — Layer 3 of the Firmware Change Protocol.
# Post-deploy validation: every probe on the Modbus bus answered, no new
# sensor_offline alerts fired, no recent Task WDT resets, no override-event storm.
# Auto-invoked by `make firmware-deploy`. Also runnable standalone via
# `make sensor-health` or directly: `./scripts/sensor-health-sweep.sh [--since "5 min ago"]`
#
# Exits nonzero on any CRITICAL failure so it properly gates firmware-deploy.
# WARNINGs do not fail the sweep but are surfaced for operator review.

set -uo pipefail

SINCE="${SINCE:-5 minutes}"
EXPECTED_FW_VERSION="${EXPECTED_FW_VERSION:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE="$2"; shift 2 ;;
        --expected-fw) EXPECTED_FW_VERSION="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--since 'PG INTERVAL'] [--expected-fw VERSION]"
            echo "  --since takes a PostgreSQL interval string: '5 minutes', '1 hour',"
            echo "  '2 days', etc.  The leading ago is handled — do NOT append 'ago'."
            echo "  SINCE env var is equivalent."
            echo "  --expected-fw, or EXPECTED_FW_VERSION, fails the sweep if the latest"
            echo "  diagnostics row still reports a different firmware version."
            exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done
# Strip a trailing 'ago' if someone passes it — 'ago' reverses the sign of
# a PG interval, which would flip now() - interval '5 min ago' into the future.
SINCE="${SINCE% ago}"

# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv). Build an argv array; call sites use "${DB[@]}".
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
mapfile -t DB < <(verdify_psql_cmd)
DB+=(-t -A -c)
PASS=0; FAIL=0; WARN=0

pass() { echo "  ✓ $1"; ((PASS++)); }
fail() { echo "  ✗ $1"; ((FAIL++)); }
warn() { echo "  ⚠ $1"; ((WARN++)); }

section() { echo ""; echo "── $1 ──"; }

echo "═══════════════════════════════════════════════════════════"
echo "  Verdify Sensor Health Sweep"
echo "  Window: since '$SINCE'"
echo "  Time:   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════════════════"

# ── 1. CLIMATE SENSORS ─────────────────────────────────────────────
# Every climate column must have a fresh non-null value in the last 5 min.
# Columns checked match the zone probes + derived values + outdoor + env.
section "Climate sensors (freshness + non-null)"

CLIMATE_COLS=(
    temp_avg vpd_avg rh_avg dew_point
    temp_north rh_north
    temp_south rh_south vpd_south
    temp_east  rh_east  vpd_east
    temp_west  rh_west  vpd_west
    outdoor_temp_f outdoor_rh_pct
    co2_ppm lux
)

for col in "${CLIMATE_COLS[@]}"; do
    v=$("${DB[@]}" "SELECT $col FROM climate WHERE ts > now() - interval '5 minutes' AND $col IS NOT NULL ORDER BY ts DESC LIMIT 1" 2>/dev/null | tr -d ' ')
    if [[ -n "$v" ]]; then
        pass "$col = $v"
    else
        fail "$col — no non-null value in last 5 min"
    fi
done

# ── 2. MODBUS BUS HEALTH ───────────────────────────────────────────
# Any Modbus timeout in the last 2 min that pattern-matches a *specific*
# probe address indicates that slave is silent. The bus itself may still
# be healthy if only one address is affected.
section "Modbus bus (timeout distribution, last 2 min)"

MODBUS_ROWS=$("${DB[@]}" "SELECT regexp_replace(message, '.*from ([0-9]+) .*', '\1') AS addr, count(*) FROM esp32_logs WHERE ts > now() - interval '2 min' AND message ILIKE '%modbus:064%' GROUP BY 1 ORDER BY 1" 2>/dev/null)

if [[ -z "$MODBUS_ROWS" ]]; then
    pass "No Modbus timeouts on any address"
else
    while IFS='|' read -r addr count; do
        addr=$(echo "$addr" | tr -d ' ')
        count=$(echo "$count" | tr -d ' ')
        # ESP32 probe addresses 1-9 per hardware.yaml:
        case "$addr" in
            1) name="case_probe" ;;
            2) name="north_wall_probe" ;;
            3) name="west_wall_probe" ;;
            4) name="south_wall_probe" ;;
            5) name="east_wall_probe" ;;
            6) name="exterior_intake_probe" ;;
            7) name="south_1_soil_probe" ;;
            8) name="south_2_soil_probe" ;;
            9) name="west_soil_probe" ;;
            *) name="unknown-addr-$addr" ;;
        esac
        if [[ "$count" -gt 20 ]]; then
            fail "$name (addr $addr) — $count timeouts in 2 min (probe not responding)"
        elif [[ "$count" -gt 0 ]]; then
            warn "$name (addr $addr) — $count timeouts in 2 min (transient, watch for trend)"
        fi
    done <<< "$MODBUS_ROWS"
fi

# ── 3a. ACTIVE PROBE COUNT (FW-10, Sprint 17) ──────────────────────
# A zone probe gone stale (>5 min Modbus timeout) is excluded from the
# avg_temp/rh/vpd aggregates. If count < 4 the planner's view of the
# greenhouse is partial. Warn, don't fail — intermittent stalls happen
# but persistent < 4 is worth investigating.
section "Active probe count (zone aggregation health)"

PROBE_ROW=$("${DB[@]}" "SELECT active_probe_count || '|' || COALESCE(uptime_s::text, '?') FROM diagnostics WHERE active_probe_count IS NOT NULL AND ts > now() - interval '5 min' ORDER BY ts DESC LIMIT 1" 2>/dev/null | tr -d ' ')
if [[ -z "$PROBE_ROW" ]]; then
    warn "No active_probe_count reading in last 5 min (firmware may predate FW-10)"
else
    IFS='|' read -r PROBE_COUNT PROBE_UPTIME <<< "$PROBE_ROW"
    STARTUP_SETTLING=$(awk -v u="$PROBE_UPTIME" 'BEGIN { print (u != "?" && u < 120) ? 1 : 0 }')
    if [[ "$PROBE_COUNT" -eq 4 ]]; then
        pass "4/4 zone probes active"
    elif [[ "$STARTUP_SETTLING" -eq 1 ]]; then
        warn "Only $PROBE_COUNT/4 zone probes active at uptime ${PROBE_UPTIME}s — startup settling"
    elif [[ "$PROBE_COUNT" -ge 2 ]]; then
        warn "Only $PROBE_COUNT/4 zone probes active — aggregates are a partial view"
    else
        fail "Only $PROBE_COUNT/4 zone probes active — aggregates untrustworthy"
    fi
fi

# ── 3. ESP32 DIAGNOSTICS ───────────────────────────────────────────
# After a firmware deploy we expect uptime to be small (ESP32 just
# rebooted) and reset_reason NOT to be a Task WDT. Outside the immediate
# post-boot window, reset_reason is sticky across the whole uptime; a WDT from
# many hours ago should warn but not permanently fail every later sweep.
# Absence of diagnostics row in 5 min is itself a failure.
section "ESP32 diagnostics"

DIAG=$("${DB[@]}" "SELECT reset_reason || '|' || COALESCE(firmware_version, '?') || '|' || COALESCE(wifi_rssi::text, '?') || '|' || COALESCE(uptime_s::text, '?') FROM diagnostics WHERE ts > now() - interval '5 min' ORDER BY ts DESC LIMIT 1" 2>/dev/null)

if [[ -z "$DIAG" ]]; then
    fail "No diagnostics row in last 5 min (ESP32 unreachable?)"
else
    IFS='|' read -r reset_reason fw_ver rssi uptime <<< "$DIAG"
    if [[ "$reset_reason" == "Task WDT" ]]; then
        WDT_RECENT=$(awk -v u="$uptime" 'BEGIN { print (u != "?" && u + 0 >= 21600) ? 0 : 1 }')
        if [[ "$WDT_RECENT" -eq 1 ]]; then
            fail "reset_reason = Task WDT with uptime ${uptime}s (recent watchdog-induced reboot — investigate!)"
        else
            warn "reset_reason = Task WDT, but uptime ${uptime}s >= 21600s (sticky reset cause, not a recent reboot)"
        fi
    else
        pass "reset_reason = $reset_reason"
    fi
    if [[ -n "$EXPECTED_FW_VERSION" ]]; then
        if [[ "$fw_ver" == "$EXPECTED_FW_VERSION" ]]; then
            pass "firmware_version = $fw_ver (expected)"
        else
            fail "firmware_version = $fw_ver (expected $EXPECTED_FW_VERSION)"
        fi
    else
        pass "firmware_version = $fw_ver"
    fi
    pass "wifi_rssi = $rssi dBm"
    pass "uptime = $uptime s"
fi

# ── 4. ALERTS (new since deploy window) ────────────────────────────
# Any sensor_offline alert that FIRED within the --since window is a
# regression — means a sensor went dark right around the deploy.
section "Alerts opened during the deploy window ('$SINCE')"

NEW_ALERTS=$("${DB[@]}" "SELECT alert_type || ' :: ' || COALESCE(sensor_id, '?') FROM alert_log WHERE ts >= now() - interval '$SINCE' AND disposition = 'open' AND alert_type IN ('sensor_offline', 'esp32_reboot', 'esp32_push_failed', 'band_fn_null', 'heap_pressure_critical')" 2>/dev/null)

if [[ -z "$NEW_ALERTS" ]]; then
    pass "No new sensor_offline / esp32_reboot / push / band alerts opened in window"
else
    while IFS= read -r a; do
        fail "NEW alert: $a"
    done <<< "$NEW_ALERTS"
fi

# ── 5. OVERRIDE EVENTS (OBS-1e audit trail) ────────────────────────
# A sudden spike in unexpected override_events in the deploy window suggests
# the new firmware changed control semantics. vent_mist_assist and summer_vent
# are expected during hot/dry VENTILATE windows and may be frequent.
section "Override events ('$SINCE' window)"

OV_COUNT=$("${DB[@]}" "SELECT count(*) FROM override_events WHERE ts >= now() - interval '$SINCE'" 2>/dev/null | tr -d ' ')
OV_EXPECTED=$("${DB[@]}" "SELECT count(*) FROM override_events WHERE ts >= now() - interval '$SINCE' AND override_type IN ('vent_mist_assist', 'summer_vent')" 2>/dev/null | tr -d ' ')
OV_UNEXPECTED=$("${DB[@]}" "SELECT count(*) FROM override_events WHERE ts >= now() - interval '$SINCE' AND override_type NOT IN ('vent_mist_assist', 'summer_vent')" 2>/dev/null | tr -d ' ')
OV_TYPES=$("${DB[@]}" "SELECT override_type, count(*) FROM override_events WHERE ts >= now() - interval '$SINCE' GROUP BY 1 ORDER BY 2 DESC" 2>/dev/null)
OV_COUNT=${OV_COUNT:-0}
OV_EXPECTED=${OV_EXPECTED:-0}
OV_UNEXPECTED=${OV_UNEXPECTED:-0}

if [[ "$OV_COUNT" -eq 0 ]]; then
    pass "0 override events in window (OK for short windows)"
elif [[ "$OV_UNEXPECTED" -eq 0 ]]; then
    pass "$OV_COUNT override events in window; all are vent_mist_assist/summer_vent expected during hot/dry VENTILATE windows"
    [[ -n "$OV_TYPES" ]] && echo "    $OV_TYPES" | sed 's/|/ = /'
elif [[ "$OV_UNEXPECTED" -lt 20 ]]; then
    pass "$OV_COUNT override events in window ($OV_EXPECTED vent/summer assist, $OV_UNEXPECTED other expected)"
    [[ -n "$OV_TYPES" ]] && echo "    $OV_TYPES" | sed 's/|/ = /'
else
    warn "$OV_COUNT override events in window ($OV_EXPECTED vent/summer assist, $OV_UNEXPECTED other) — check if firmware changed control semantics"
    [[ -n "$OV_TYPES" ]] && echo "    $OV_TYPES" | sed 's/|/ = /'
fi

# ── SUMMARY ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  PASS: $PASS  FAIL: $FAIL  WARN: $WARN"
echo "═══════════════════════════════════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    echo "SENSOR HEALTH SWEEP FAILED — $FAIL critical check(s)."
    echo "Firmware deploy should be rolled back or investigated."
    exit 1
fi

exit 0
