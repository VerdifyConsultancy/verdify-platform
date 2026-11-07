#!/usr/bin/env bash
# sensor-health-sweep.sh — Layer 3 of the Firmware Change Protocol.
# Post-deploy validation: every sensor still reaching the ingestor, every probe
# on the Modbus bus answered, no new sensor_offline alerts fired, no crash or
# unexplained reset (no recent Task WDT resets), no unexplained water, no
# override-event storm.
# Auto-invoked by `make firmware-deploy`. Also runnable standalone via
# `make sensor-health` or directly: `./scripts/sensor-health-sweep.sh [--since "5 min ago"]`
#
# Exits nonzero on any CRITICAL failure so it properly gates firmware-deploy.
# WARNINGs do not fail the sweep but are surfaced for operator review. A query
# that fails is a FAIL with psql's own message, never an empty healthy answer.

set -uo pipefail

SINCE="${SINCE:-5 minutes}"
EXPECTED_FW_VERSION="${EXPECTED_FW_VERSION:-}"
NO_WATER=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE="$2"; shift 2 ;;
        --expected-fw) EXPECTED_FW_VERSION="$2"; shift 2 ;;
        --no-water) NO_WATER=1; shift ;;
        -h|--help)
            echo "Usage: $0 [--since 'PG INTERVAL'] [--expected-fw VERSION] [--no-water]"
            echo "  --since takes a PostgreSQL interval string: '5 minutes', '1 hour',"
            echo "  '2 days', etc.  The leading ago is handled — do NOT append 'ago'."
            echo "  SINCE env var is equivalent."
            echo "  --expected-fw, or EXPECTED_FW_VERSION, fails the sweep if the latest"
            echo "  diagnostics row still reports a different firmware version."
            echo "  --no-water fails the sweep if a drip, fertilizer, south or west relay"
            echo "  opened in the window, for a release with a no-water rule."
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
DB=()
while IFS= read -r arg; do DB+=("$arg"); done < <(verdify_psql_cmd)
DB+=(-t -A -c)
PASS=0; FAIL=0; WARN=0
QUERY_ERROR="$(mktemp)"
trap 'rm -f "$QUERY_ERROR"' EXIT

pass() { echo "  ✓ $1"; ((PASS++)); }
fail() { echo "  ✗ $1"; ((FAIL++)); }
warn() { echo "  ⚠ $1"; ((WARN++)); }

section() { echo ""; echo "── $1 ──"; }

# query VAR LABEL SQL — the rows into VAR. A failed query counts as a FAIL with
# psql's first error line and returns 1, so its callers skip the verdict.
query() {
    local rows
    if rows=$("${DB[@]}" "$3" 2>"$QUERY_ERROR" </dev/null); then
        printf -v "$1" '%s' "$rows"
        return 0
    fi
    printf -v "$1" '%s' ""
    fail "$2 — query failed: $(head -n 1 "$QUERY_ERROR" | cut -c1-200)"
    return 1
}

# provenance_sql COLUMN... — one "column|value|seconds since it arrived" row per
# column, from the newest climate source event. A climate row is no proof of life:
# write_climate re-uses a sensor's last value for 600 s after it stops, and
# sample_provenance records when each value really arrived.
provenance_sql() {
    local columns
    columns="$(printf "'%s'," "$@")"
    echo "SELECT k.col, e.climate_payload ->> k.col, round(extract(epoch FROM now() - (e.sample_provenance -> k.col ->> 'received_at')::timestamptz)) FROM unnest(ARRAY[${columns%,}]) AS k(col) LEFT JOIN (SELECT climate_payload, sample_provenance FROM climate_source_events WHERE source_ts > now() - interval '15 minutes' ORDER BY source_ts DESC LIMIT 1) e ON true"
}

# received_recently AGE — the sweep's freshness rule, 5 min since the value arrived.
received_recently() { [[ "$1" =~ ^-?[0-9]+$ && "$1" -le 300 ]]; }

echo "═══════════════════════════════════════════════════════════"
echo "  Verdify Sensor Health Sweep"
echo "  Window: since '$SINCE'"
echo "  Time:   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════════════════"

# ── 1. CLIMATE SENSORS ─────────────────────────────────────────────
# Every climate column must have arrived in the last 5 min, by provenance.
# The known absent south triplet may warn only when it also had no values in
# a populated pre-OTA 24-hour baseline; a newly lost south reading still fails.
section "Climate sensors (received in the last 5 min, by provenance)"

CLIMATE_COLS=(
    temp_avg vpd_avg rh_avg dew_point
    temp_north rh_north
    temp_south rh_south vpd_south
    temp_east  rh_east  vpd_east
    temp_west  rh_west  vpd_west
    outdoor_temp_f outdoor_rh_pct
    co2_ppm lux
)

if query CLIMATE_ROWS "Climate provenance" "$(provenance_sql "${CLIMATE_COLS[@]}")"; then
    while IFS='|' read -r col value age; do
        [[ -n "$col" ]] || continue
        if [[ -n "$value" ]] && received_recently "$age"; then
            pass "$col = $value"
            continue
        fi
        stale="no value received"
        [[ -n "$value" && -n "$age" ]] && stale="last received ${age}s ago"
        case "$col" in
            temp_south|rh_south|vpd_south)
                prior=""
                if query prior "$col baseline" "SELECT count(*), count($col) FROM climate WHERE ts >= now() - interval '24 hours' AND ts < now() - interval '10 minutes'"; then
                    IFS='|' read -r prior_rows prior_nonnull <<< "$prior"
                    if [[ "${prior_rows:-0}" =~ ^[0-9]+$ && "${prior_nonnull:-}" == "0" && "${prior_rows:-0}" -ge 100 ]]; then
                        warn "$col — absent in last 5 min and throughout populated pre-OTA 24-hour baseline"
                    else
                        fail "$col — $stale (previously present or baseline unavailable)"
                    fi
                fi
                ;;
            *) fail "$col — $stale" ;;
        esac
    done <<< "$CLIMATE_ROWS"
fi

# ── 2. MODBUS BUS HEALTH ───────────────────────────────────────────
# Any Modbus timeout in the last 2 min that pattern-matches a *specific*
# probe address indicates that slave is silent. The bus itself may still
# be healthy if only one address is affected.
section "Modbus bus (timeout distribution, last 2 min)"

# ESPHome embeds its tag and source line in message; the separate tag column
# can be NULL. Match the timeout itself, independent of vendor line numbers.
if query MODBUS_ROWS "Modbus timeout query unavailable — bus health unknown" "SELECT substring(message from 'Stop waiting for response from ([0-9]+) ') AS addr, count(*) FROM esp32_logs WHERE ts > now() - interval '2 min' AND message ~ '[[]modbus:[0-9]+]: Stop waiting for response from [0-9]+ [0-9]+ms after last send' GROUP BY 1 ORDER BY 1"; then
    if [[ -z "$MODBUS_ROWS" ]]; then
        pass "No Modbus timeouts on any address"
    else
        while IFS='|' read -r addr count; do
            addr=$(echo "$addr" | tr -d ' ')
            count=$(echo "$count" | tr -d ' ')
            # ESP32 probe addresses 1-10 per hardware.yaml:
            case "$addr" in
                10)
                    # #1184: the PAR meter is telemetry only and may not be fitted
                    # yet; a silent meter warns and never rolls back an OTA.
                    warn "par_probe (addr $addr) — $count timeouts in 2 min (PAR meter absent or silent; DLI stays unavailable)"
                    continue
                    ;;
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
fi

# ── 3a. ACTIVE PROBE COUNT (FW-10, Sprint 17) ──────────────────────
# A zone probe gone stale (>5 min Modbus timeout) is excluded from the
# avg_temp/rh/vpd aggregates. If count < 4 the planner's view of the
# greenhouse is partial. Warn, don't fail — intermittent stalls happen
# but persistent < 4 is worth investigating.
section "Active probe count (zone aggregation health)"

if query PROBE_ROW "Active probe count" "SELECT active_probe_count || '|' || COALESCE(uptime_s::text, '?') FROM diagnostics WHERE active_probe_count IS NOT NULL AND ts > now() - interval '5 min' ORDER BY ts DESC LIMIT 1"; then
    PROBE_ROW="${PROBE_ROW// /}"
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
fi

# ── 3. ESP32 DIAGNOSTICS ───────────────────────────────────────────
# After a firmware deploy we expect uptime to be small (ESP32 just
# rebooted) and reset_reason NOT to be a crash. Outside the immediate
# post-boot window, reset_reason is sticky across the whole uptime; a crash from
# many hours ago should warn but not permanently fail every later sweep.
# Absence of diagnostics row in 5 min is itself a failure. Each column is read
# as its own latest non-null value: a partial row (NULL reset_reason or version)
# must neither read as a missing row nor hide the newest complete values.
section "ESP32 diagnostics"

latest_diag() { echo "(SELECT $1::text FROM diagnostics WHERE ts > now() - interval '5 min' AND $1 IS NOT NULL ORDER BY ts DESC LIMIT 1)"; }
fw_ver=""
if query DIAG "ESP32 diagnostics" "SELECT (SELECT count(*) FROM diagnostics WHERE ts > now() - interval '5 min'), $(latest_diag reset_reason), $(latest_diag firmware_version), $(latest_diag wifi_rssi), $(latest_diag uptime_s)"; then
    IFS='|' read -r diag_rows reset_reason fw_ver rssi uptime <<< "$DIAG"
    if [[ "${diag_rows:-0}" -eq 0 ]]; then
        fail "No diagnostics row in last 5 min (ESP32 unreachable?)"
    else
        RESET_RECENT=$(awk -v u="${uptime:-?}" 'BEGIN { print (u != "?" && u + 0 >= 21600) ? 0 : 1 }')
        case "$reset_reason" in
            "")
                fail "reset_reason — no non-null value in last 5 min"
                ;;
            "Software reset")
                pass "reset_reason = $reset_reason"
                ;;
            "Task WDT"|"Interrupt WDT"|"Other WDT"|"Guru/Panic")
                if [[ "$RESET_RECENT" -eq 1 ]]; then
                    fail "reset_reason = $reset_reason with uptime ${uptime:-?}s (recent watchdog-induced reboot or panic — investigate!)"
                else
                    warn "reset_reason = $reset_reason, but uptime ${uptime}s >= 21600s (sticky reset cause, not a recent reboot)"
                fi
                ;;
            "Power-on"|"Brown-out"|"External reset")
                if [[ "$RESET_RECENT" -eq 1 ]]; then
                    warn "reset_reason = $reset_reason with uptime ${uptime:-?}s (site power or a person, not an OTA: confirm the relays read OFF)"
                else
                    pass "reset_reason = $reset_reason"
                fi
                ;;
            *)
                warn "reset_reason = $reset_reason (not a known reason)"
                ;;
        esac
        if [[ -z "$fw_ver" ]]; then
            fail "firmware_version — no non-null value in last 5 min"
        elif [[ -n "$EXPECTED_FW_VERSION" ]]; then
            if [[ "$fw_ver" == "$EXPECTED_FW_VERSION" ]]; then
                pass "firmware_version = $fw_ver (expected)"
            else
                fail "firmware_version = $fw_ver (expected $EXPECTED_FW_VERSION)"
            fi
        else
            pass "firmware_version = $fw_ver"
        fi
        if [[ -n "$rssi" ]]; then
            pass "wifi_rssi = $rssi dBm"
        else
            warn "wifi_rssi — no non-null value in last 5 min"
        fi
        if [[ -z "$uptime" ]]; then
            fail "uptime_s — no non-null value in last 5 min"
        else
            pass "uptime = $uptime s"
            # A value received before this boot still reads as received in the last 5 min.
            if awk -v u="$uptime" 'BEGIN { exit !(u + 0 < 360) }'; then
                warn "uptime under 360 s — values from before this boot can still read fresh; re-run for a decisive sweep"
            fi
        fi
    fi
fi

# ── 3b. UPTIME CONTINUITY ──────────────────────────────────────────
# Uptime must keep pace with the wall clock between rows. A row that falls behind
# is a reset. A changed firmware_version explains it: that is the flash. The same
# image resetting is not explained: only one that follows about 300 s of silence
# fits the firmware's reboot_timeout (a link event), and only one the controller
# itself reports as Power-on, Brown-out or External reset is site power or a person,
# not the image. Either warns once; a second reset in the window fails.
# Rows stamped exactly on the minute are gap backfill and carry old uptimes, so
# only real rows count, and a partial row is skipped rather than read as a reset.
section "Uptime continuity ('$SINCE' window)"

if query RESET_ROWS "Uptime continuity" "SELECT ts::timestamptz(0), round(prev_up), round(uptime_s), round(extract(epoch FROM ts - prev_ts)), firmware_version IS DISTINCT FROM prev_fw FROM (SELECT ts, uptime_s, firmware_version, lag(uptime_s) OVER w AS prev_up, lag(ts) OVER w AS prev_ts, lag(firmware_version) OVER w AS prev_fw FROM diagnostics WHERE ts >= now() - interval '$SINCE' AND uptime_s IS NOT NULL AND firmware_version IS NOT NULL AND ts <> date_trunc('minute', ts) WINDOW w AS (ORDER BY ts)) r WHERE prev_up IS NOT NULL AND (uptime_s < prev_up OR uptime_s + 120 < prev_up + extract(epoch FROM ts - prev_ts)) ORDER BY ts"; then
    same_image_resets=0; link_events=0; flash_boots=0; reset_lines=""
    while IFS='|' read -r at prev_up up gap_s version_changed; do
        [[ -n "$at" ]] || continue
        if [[ "$version_changed" == "t" ]]; then
            ((flash_boots++))
            continue
        fi
        ((same_image_resets++))
        [[ "$gap_s" -ge 240 ]] && ((link_events++))
        reset_lines+=" $at (uptime ${prev_up}s to ${up}s, ${gap_s}s since the row before);"
    done <<< "$RESET_ROWS"
    if [[ "$same_image_resets" -eq 0 ]]; then
        pass "Uptime continuous in window ($flash_boots flash boot(s))"
    elif [[ "$same_image_resets" -eq 1 && "$link_events" -eq 1 ]]; then
        warn "One reset of the same image after silence, which fits reboot_timeout (a link event):$reset_lines"
    elif [[ "$same_image_resets" -eq 1 && "${reset_reason:-}" =~ ^(Power-on|Brown-out|External\ reset)$ ]]; then
        warn "One reset of the same image, reported as ${reset_reason} (site power or a person: confirm the relays read OFF):$reset_lines"
    else
        fail "$same_image_resets reset(s) of the same image in window:$reset_lines"
    fi
fi

# ── 4. ALERTS (new since deploy window) ────────────────────────────
# Any sensor_offline alert that FIRED within the --since window is a
# regression — means a sensor went dark right around the deploy.
#
# 2026-07-11 audit: the filter was structurally blind to the 07-10 deploy's
# real band regression — band_device_db_divergence was excluded by type AND
# its acknowledged disposition was excluded by disposition='open'. Widen to
# every band/divergence/validation type and count any UNRESOLVED row
# (open or acknowledged): an acknowledged-but-unresolved alert in the deploy
# window is still a deploy-window regression.
#
# A sensor_offline re-open keeps its first ts, so a row raised again inside the
# window counts too, by details.last_seen_at (when the row was last raised, not
# the sensor's last value). Soil probes flap on their own and count only when
# freshly opened.
section "Alerts opened during the deploy window ('$SINCE')"

if query NEW_ALERTS "Deploy-window alerts" "SELECT alert_type || ' :: ' || COALESCE(sensor_id, '?') FROM alert_log WHERE disposition IN ('open', 'acknowledged') AND resolved_at IS NULL AND ((ts >= now() - interval '$SINCE' AND (alert_type IN ('sensor_offline', 'esp32_reboot', 'esp32_push_failed', 'heap_pressure_critical', 'firmware_version_mismatch', 'alert_validation_failed') OR alert_type LIKE 'band%')) OR (alert_type = 'sensor_offline' AND COALESCE(sensor_id, '') NOT LIKE 'climate.soil_%' AND (details ->> 'last_seen_at')::timestamptz >= now() - interval '$SINCE'))"; then
    if [[ -z "$NEW_ALERTS" ]]; then
        pass "No new sensor_offline / esp32_reboot / push / band / validation alerts open in window"
    else
        while IFS= read -r a; do
            # Live equals the expected version, so this says only that the pod's pin
            # lags an accepted flash: scripts/firmware-release-job.py pin clears it.
            if [[ "$a" == "firmware_version_mismatch :: diag.firmware_version" && -n "$EXPECTED_FW_VERSION" && "$fw_ver" == "$EXPECTED_FW_VERSION" ]]; then
                warn "Pin lags: $a, while live firmware_version = $EXPECTED_FW_VERSION (expected)"
            else
                fail "NEW alert: $a"
            fi
        done <<< "$NEW_ALERTS"
    fi
fi

# ── 5. WATER ───────────────────────────────────────────────────────
# The meter telemetry must still arrive, and a wet relay that opened must be
# explained. Fog and the center mister may open under climate demand; anything
# else opens only for a run or a schedule, so --no-water fails on it. The ingestor
# drops an ON edge it cannot place in its lineage, so an empty list is telemetry
# evidence, not proof that no valve moved. Meter flow alone only warns: the meter
# also reads the hose, the sink and the hydro line.
section "Water (meter telemetry, wet relay edges and flow, '$SINCE' window)"

if query WATER_ROWS "Water telemetry" "$(provenance_sql flow_gpm water_total_gal mister_water_today)"; then
    while IFS='|' read -r col value age; do
        [[ -n "$col" ]] || continue
        if [[ -n "$value" ]] && received_recently "$age"; then
            pass "$col = $value"
        elif [[ -n "$value" && -n "$age" ]]; then
            fail "$col — last received ${age}s ago"
        else
            fail "$col — no value received"
        fi
    done <<< "$WATER_ROWS"
fi

wet_edges=0
if query WET_ROWS "Wet relay edges" "SELECT equipment, count(*), max(ts)::timestamptz(0) FROM equipment_state WHERE ts >= now() - interval '$SINCE' AND state AND equipment IN ('fog', 'mister_center', 'mister_south', 'mister_south_fert', 'mister_west', 'mister_west_fert', 'drip_wall', 'drip_wall_fert', 'drip_center', 'drip_center_fert', 'fert_master_valve') GROUP BY 1 ORDER BY 1"; then
    while IFS='|' read -r relay edges last_on; do
        [[ -n "$relay" ]] || continue
        ((wet_edges++))
        line="$relay opened $edges time(s) in window, last $last_on"
        if [[ "$relay" == "fog" || "$relay" == "mister_center" ]]; then
            warn "$line (allowed under climate demand: check it matches VPD)"
        elif [[ "$NO_WATER" -eq 1 ]]; then
            fail "$line, under --no-water"
        else
            warn "$line (a run or a schedule must explain it)"
        fi
    done <<< "$WET_ROWS"
    [[ "$wet_edges" -eq 0 ]] && pass "No wet relay opened in window"

    if query FLOW_ROW "Meter flow" "SELECT round(max(flow_gpm)::numeric, 2) FROM climate WHERE ts >= now() - interval '$SINCE' AND flow_gpm > 0.05"; then
        if [[ -n "$FLOW_ROW" ]]; then
            if [[ "$wet_edges" -eq 0 ]]; then
                warn "Meter read up to $FLOW_ROW gpm with no wet relay edge in window (hose, sink, hydro or a stuck valve)"
            else
                pass "Meter read up to $FLOW_ROW gpm in window, with wet relay edges above"
            fi
        fi
    fi
fi

# ── 6. OVERRIDE EVENTS (OBS-1e audit trail) ────────────────────────
# A sudden spike in unexpected override_events in the deploy window suggests
# the new firmware changed control semantics. vent_mist_assist and summer_vent
# are expected during hot/dry VENTILATE windows and may be frequent.
section "Override events ('$SINCE' window)"

if query OV_TYPES "Override events" "SELECT override_type, count(*) FROM override_events WHERE ts >= now() - interval '$SINCE' GROUP BY 1 ORDER BY 2 DESC"; then
    OV_COUNT=0; OV_EXPECTED=0; OV_UNEXPECTED=0
    while IFS='|' read -r ov_type ov_count; do
        [[ -n "$ov_type" ]] || continue
        ((OV_COUNT += ov_count))
        case "$ov_type" in
            vent_mist_assist|summer_vent) ((OV_EXPECTED += ov_count)) ;;
            *) ((OV_UNEXPECTED += ov_count)) ;;
        esac
    done <<< "$OV_TYPES"

    if [[ "$OV_COUNT" -eq 0 ]]; then
        pass "0 override events in window (OK for short windows)"
    elif [[ "$OV_UNEXPECTED" -eq 0 ]]; then
        pass "$OV_COUNT override events in window; all are vent_mist_assist/summer_vent expected during hot/dry VENTILATE windows"
        echo "    $OV_TYPES" | sed 's/|/ = /'
    elif [[ "$OV_UNEXPECTED" -lt 20 ]]; then
        pass "$OV_COUNT override events in window ($OV_EXPECTED vent/summer assist, $OV_UNEXPECTED other expected)"
        echo "    $OV_TYPES" | sed 's/|/ = /'
    else
        warn "$OV_COUNT override events in window ($OV_EXPECTED vent/summer assist, $OV_UNEXPECTED other) — check if firmware changed control semantics"
        echo "    $OV_TYPES" | sed 's/|/ = /'
    fi
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
