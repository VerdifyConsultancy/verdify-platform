#!/usr/bin/env bash
# Firmware OTA preflight guard.
#
# Enforces the phase-0 deploy rules that can be checked locally before compile
# or upload. Forecast heat is reported as context, not a deploy blocker. There
# is no release timing gate: no bake, minimum artifact age or weekly limit, so a
# new build can ship as often as needed (Jason, 2026-11-05).

set -euo pipefail

DB_STATEMENT_TIMEOUT_MS="${VERDIFY_DB_STATEMENT_TIMEOUT_MS:-5000}"
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves the exact prior argv on the VM). The PGOPTIONS statement-timeout is
# injected as a docker-exec extra flag in docker-exec mode.
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
DB=()
while IFS= read -r arg; do DB+=("$arg"); done \
    < <(verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=${DB_STATEMENT_TIMEOUT_MS}")
DB+=(-t -A -F '|' -c)

fail() { echo "✗ $1" >&2; exit 1; }
pass() { echo "✓ $1"; }
warn() { echo "⚠ $1"; }
override_enabled() { [[ "${ALLOW_FIRMWARE_DEPLOY_GUARD_OVERRIDE:-0}" == "1" ]]; }
override_authorized() {
    override_enabled \
        && [[ -n "${FIRMWARE_DEPLOY_OVERRIDE_REASON:-}" ]]
}
guard_or_fail() {
    local message="$1"
    if override_authorized; then
        # 2026-07-11 audit: this override previously left NO durable record
        # (only the since-removed bake/weekly gates called record_override).
        # Every accepted override is now audited to the override log AND alert_log.
        record_deploy_override "severe-alert-or-telemetry" "$message" "${FIRMWARE_DEPLOY_OVERRIDE_REASON:-no reason supplied}"
        warn "$message — explicit override active (${FIRMWARE_DEPLOY_OVERRIDE_REASON:-no reason supplied})"
    else
        if override_enabled; then
            fail "$message; override requested but FIRMWARE_DEPLOY_OVERRIDE_REASON is required"
        fi
        fail "$message"
    fi
}

override_reason="${FIRMWARE_OTA_FREEZE_OVERRIDE_REASON:-}"
override_log="${FIRMWARE_OTA_FREEZE_OVERRIDE_LOG:-/var/local/verdify/state/firmware-ota-freeze-overrides.log}"

require_override_reason() {
    local gate="$1"
    if [[ ${#override_reason} -lt 12 ]]; then
        fail "$gate blocked; set FIRMWARE_OTA_FREEZE_OVERRIDE_REASON to a specific reason of at least 12 characters to override"
    fi
}

record_override() {
    local gate="$1"
    local detail="$2"
    # Audit log defaults to /var/local (the VM path); fall back to a writable
    # location (e.g. macOS laptop operator) so the override is still recorded.
    if ! mkdir -p "$(dirname "$override_log")" 2>/dev/null; then
        override_log="${HOME}/.verdify/state/firmware-ota-freeze-overrides.log"
        mkdir -p "$(dirname "$override_log")"
    fi
    printf '%s\tgate=%s\treason=%s\tdetail=%s\tworktree=%s\tsha=%s\n' \
        "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
        "$gate" \
        "$override_reason" \
        "$detail" \
        "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" \
        "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
        >> "$override_log"
    db_audit_override "$gate" "$detail" "$override_reason"
    warn "$gate override accepted: $detail"
}

# Durable, host-independent override audit: an info row in alert_log. The
# host-local log file above is invisible from other kubectl hosts and from
# the repo; the DB row is queryable by the sweeps and any future session.
# Best-effort — an audit-write failure must not block the deploy itself
# (the override was already authorized), but it is loudly warned.
db_audit_override() {
    local gate="$1" detail="$2" reason="$3"
    local esc_msg esc_details
    esc_msg="$(printf 'FIRMWARE DEPLOY OVERRIDE (%s): %s' "$gate" "$detail" | sed "s/'/''/g")"
    esc_details="$(printf '{"gate": "%s", "reason": "%s", "sha": "%s"}' \
        "$gate" "$(printf '%s' "$reason" | sed 's/["\\]/\\&/g')" \
        "$(git rev-parse HEAD 2>/dev/null || echo unknown)" | sed "s/'/''/g")"
    if ! "${DB[@]}" "INSERT INTO alert_log (alert_type, severity, category, message, details, source, disposition, resolved_at, resolved_by, resolution)
        VALUES ('deploy_override', 'info', 'system', '${esc_msg}', '${esc_details}'::jsonb, 'firmware_preflight', 'resolved', now(), 'operator', 'audit record')" >/dev/null 2>&1; then
        warn "override DB audit write FAILED — only the local log at ${override_log} records this override"
    fi
}

# guard_or_fail overrides route through the same durable audit as the
# provisional-rollback override, without requiring FIRMWARE_OTA_FREEZE_OVERRIDE_REASON.
record_deploy_override() {
    local gate="$1" detail="$2" reason="$3"
    local saved_reason="$override_reason"
    override_reason="$reason"
    record_override "$gate" "$detail"
    override_reason="$saved_reason"
}

open_bad="$("${DB[@]}" "SELECT count(*) FROM alert_log WHERE disposition IN ('open','acknowledged') AND resolved_at IS NULL AND severity IN ('critical','high')" | tr -d '[:space:]')"
if [[ "${open_bad:-0}" -gt 0 ]]; then
    guard_or_fail "$open_bad unresolved critical/legacy-high alert(s); no firmware OTA while severe alerts are unresolved"
fi
pass "No unresolved critical/legacy-high alerts"

climate_age="$("${DB[@]}" "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(ts))::int, 2147483647) FROM climate WHERE temp_avg IS NOT NULL" | tr -d '[:space:]')"
if [[ ! "${climate_age:-}" =~ ^[0-9]+$ || "$climate_age" -gt 300 ]]; then
    guard_or_fail "Climate telemetry stale or missing (${climate_age:-no data}s); no firmware OTA without fresh controller inputs"
fi
pass "Climate telemetry fresh (${climate_age}s)"

action_log_table="$("${DB[@]}" "SELECT CASE WHEN to_regclass('public.climate_action_log') IS NULL THEN 0 ELSE 1 END" | tr -d '[:space:]')"
if [[ "$action_log_table" != "1" ]]; then
    guard_or_fail "Climate action log table missing; no firmware OTA without controller-decision proof"
fi

action_age="$("${DB[@]}" "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(ts))::int, 2147483647) FROM climate_action_log" | tr -d '[:space:]')"
if [[ ! "${action_age:-}" =~ ^[0-9]+$ || "$action_age" -gt 300 ]]; then
    guard_or_fail "Climate action log stale or missing (${action_age:-no data}s); no firmware OTA without fresh controller-decision proof"
fi
pass "Climate action log fresh (${action_age}s)"

action_proof_missing="$("${DB[@]}" \
    "WITH latest AS (
       SELECT *
         FROM climate_action_log
        ORDER BY ts DESC
        LIMIT 1
     )
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
       FROM latest" | tr -d '[:space:]')"
if [[ -n "$action_proof_missing" ]]; then
    guard_or_fail "Climate action log proof incomplete (${action_proof_missing}); no firmware OTA without graphable target deltas and relay truth"
fi
pass "Climate action log proof complete"

# #390: release deny checks for topology, cycling and outdoor inputs.
# Irrigation topology: the device must run the confirmed desired program (lines,
# slots, zone membership), from NVS, with the engine up. A pending, rejected or
# unmatched push means the flash would boot into an unknown wet topology.
topology_missing="$("${DB[@]}" \
    "/* preflight:topology */
     WITH p AS (SELECT sha256, confirmed_at, NULLIF(reject_code, '') AS reject_code
                  FROM irrigation_push ORDER BY revision DESC LIMIT 1),
          d AS (SELECT value FROM system_state WHERE entity = 'irr_program_id'
                   AND ts > now() - interval '30 days' ORDER BY ts DESC LIMIT 1),
          g AS (SELECT value FROM system_state WHERE entity = 'irr_engine'
                   AND ts > now() - interval '30 days' ORDER BY ts DESC LIMIT 1)
     SELECT concat_ws(',',
            CASE WHEN p.sha256 IS NULL THEN 'no_program_push' END,
            CASE WHEN p.sha256 IS NOT NULL AND p.confirmed_at IS NULL THEN 'push_unconfirmed' END,
            CASE WHEN p.reject_code IS NOT NULL THEN 'push_rejected:' || p.reject_code END,
            CASE WHEN g.value IS DISTINCT FROM 'true' THEN 'irr_engine_not_true' END,
            CASE WHEN d.value IS NULL OR p.sha256 IS NULL
                      OR position(('h=' || left(p.sha256, 16)) IN d.value) <> 1
                      OR position('src=nvs' IN d.value) = 0 THEN 'device_program_mismatch' END)
       FROM (SELECT 1) AS one
       LEFT JOIN p ON true LEFT JOIN d ON true LEFT JOIN g ON true" | tr -d '[:space:]')"
if [[ -n "$topology_missing" ]]; then
    guard_or_fail "Irrigation topology not confirmed (${topology_missing}); no firmware OTA while the device's wet topology differs from the desired program"
fi
pass "Irrigation topology confirmed (device runs the newest confirmed program from NVS)"

# Impossible cycling: more ON edges in the last hour than the live minimum
# on+off dwell allows, for any of the six dwell-governed climate relays. The
# bound is ceil(3600 / (min_on + min_off)) + 1; a missing setpoint uses 60 s.
cycling_bad="$("${DB[@]}" \
    "/* preflight:cycling */
     WITH sp AS (SELECT DISTINCT ON (parameter) parameter, value FROM setpoint_snapshot
                  WHERE ts > now() - interval '6 hours'
                    AND parameter ~ '^min_(heat|fan|fog|vent)_(on|off)_s\$'
                  ORDER BY parameter, ts DESC),
          relay(equipment, family) AS (VALUES ('heat1','heat'),('heat2','heat'),('fan1','fan'),
                                              ('fan2','fan'),('fog','fog'),('vent','vent')),
          bound AS (SELECT r.equipment,
                           ceil(3600.0 / GREATEST(
                               COALESCE((SELECT value FROM sp WHERE parameter = 'min_' || r.family || '_on_s'), 60)
                             + COALESCE((SELECT value FROM sp WHERE parameter = 'min_' || r.family || '_off_s'), 60), 1))::int + 1 AS max_starts
                      FROM relay r),
          e AS (SELECT equipment, ts, state,
                       lag(state) OVER (PARTITION BY equipment ORDER BY ts) AS prev
                  FROM equipment_state
                 WHERE ts > now() - interval '70 minutes'
                   AND equipment IN (SELECT equipment FROM relay)),
          starts AS (SELECT equipment, count(*) AS n FROM e
                      WHERE state AND prev IS DISTINCT FROM true AND ts > now() - interval '60 minutes'
                      GROUP BY equipment)
     SELECT coalesce(string_agg(b.equipment || '=' || s.n || '>' || b.max_starts, ',' ORDER BY b.equipment), '')
       FROM bound b JOIN starts s USING (equipment)
      WHERE s.n > b.max_starts" | tr -d '[:space:]')"
if [[ -n "$cycling_bad" ]]; then
    guard_or_fail "Impossible relay cycling in the last hour (${cycling_bad} starts); no firmware OTA while a relay cycles faster than its dwell allows"
fi
pass "Relay cycling within the dwell bound"

# Stale outdoor inputs: the controller's own outdoor freshness (Tempest UDP),
# from the newest moisture-exchange row. An outdoor-blind controller cannot be
# told apart from a regression after the flash.
outdoor_state="$("${DB[@]}" \
    "/* preflight:outdoor */
     SELECT coalesce((SELECT CASE
                WHEN ts < now() - interval '10 minutes' THEN 'no_recent_row'
                WHEN outdoor_fresh IS DISTINCT FROM true THEN 'device_reports_stale'
                WHEN outdoor_age_s IS NULL OR outdoor_age_s > 600 THEN 'age_' || coalesce(outdoor_age_s::text, 'null') || 's'
                ELSE 'ok' END
              FROM v_moisture_estimator_telemetry
             WHERE ts > now() - interval '1 hour'
             ORDER BY ts DESC LIMIT 1), 'no_recent_row')" | tr -d '[:space:]')"
if [[ "$outdoor_state" != "ok" ]]; then
    guard_or_fail "Outdoor (Tempest) inputs stale on the controller (${outdoor_state:-query_failed}); no firmware OTA while the controller is outdoor-blind"
fi
pass "Outdoor (Tempest) inputs fresh on the controller"

max_temp="$("${DB[@]}" "SELECT COALESCE(max(temp_f), -999) FROM weather_forecast WHERE ts > now() AND ts <= now() + interval '24 hours'" | tr -d '[:space:]')"
if awk -v t="$max_temp" 'BEGIN { exit !(t > 85.0) }'; then
    warn "Forecast max next 24h is ${max_temp}F (>85F); proceed with normal post-OTA health validation"
else
    pass "Forecast max next 24h is ${max_temp}F"
fi

last_good="firmware/artifacts/last-good.ota.bin"
rollback_bin="${FIRMWARE_ROLLBACK_BIN:-$last_good}"
if [[ "$rollback_bin" != "$last_good" ]]; then
    [[ "$rollback_bin" == /* ]] || fail "Provisional rollback candidate path must be absolute"
    [[ -f "$rollback_bin" ]] || fail "Provisional rollback candidate missing at $rollback_bin"
    [[ "${FIRMWARE_ROLLBACK_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] ||
        fail "Provisional rollback candidate requires its exact SHA-256"
    actual_sha="$(sha256sum "$rollback_bin" | awk '{print $1}')"
    [[ "$actual_sha" == "$FIRMWARE_ROLLBACK_SHA256" ]] ||
        fail "Provisional rollback candidate SHA-256 mismatch"
    require_override_reason "provisional rollback candidate"
    record_override "provisional rollback candidate" \
        "provisional recovery binary $rollback_bin sha256=$actual_sha; not accepted last-good"
    pass "Verified provisional rollback candidate (not accepted last-good)"
elif [[ -f "$last_good" ]]; then
    pass "Last-good rollback artifact present at $last_good"
else
    fail "No last-good rollback artifact at $last_good; supply a verified provisional rollback candidate"
fi
