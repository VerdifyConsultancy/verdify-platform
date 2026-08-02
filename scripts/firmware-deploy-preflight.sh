#!/usr/bin/env bash
# Firmware OTA preflight guard.
#
# Enforces the phase-0 deploy rules that can be checked locally before compile
# or upload. Forecast heat is reported as context, not a deploy blocker.

set -euo pipefail

DB_STATEMENT_TIMEOUT_MS="${VERDIFY_DB_STATEMENT_TIMEOUT_MS:-5000}"
# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves the exact prior argv on the VM). The PGOPTIONS statement-timeout is
# injected as a docker-exec extra flag in docker-exec mode.
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
mapfile -t DB < <(verdify_psql_cmd -e "PGOPTIONS=-c statement_timeout=${DB_STATEMENT_TIMEOUT_MS}")
DB+=(-t -A -F '|' -c)

fail() { echo "✗ $1" >&2; exit 1; }
pass() { echo "✓ $1"; }
warn() { echo "⚠ $1"; }
override_enabled() { [[ "${ALLOW_FIRMWARE_DEPLOY_GUARD_OVERRIDE:-0}" == "1" ]]; }
override_authorized() {
    override_enabled \
        && [[ "${FIRMWARE_DEPLOY_OPERATOR_SIGNOFF:-0}" == "1" ]] \
        && [[ -n "${FIRMWARE_DEPLOY_OVERRIDE_REASON:-}" ]]
}
guard_or_fail() {
    local message="$1"
    if override_authorized; then
        # 2026-07-11 audit: this override previously left NO durable record
        # (only the bake/weekly gates called record_override). Every accepted
        # override is now audited to the override log AND alert_log.
        record_deploy_override "severe-alert-or-telemetry" "$message" "${FIRMWARE_DEPLOY_OVERRIDE_REASON:-no reason supplied}"
        warn "$message — operator override active (${FIRMWARE_DEPLOY_OVERRIDE_REASON:-no reason supplied})"
    else
        if override_enabled; then
            fail "$message; override requested but FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1 and FIRMWARE_DEPLOY_OVERRIDE_REASON are required"
        fi
        fail "$message"
    fi
}

# Portable file mtime (epoch seconds): BSD/macOS `stat -f %m`, GNU/Linux `stat -c %Y`.
mtime_of() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null; }

override_reason="${FIRMWARE_OTA_FREEZE_OVERRIDE_REASON:-}"
override_log="${FIRMWARE_OTA_FREEZE_OVERRIDE_LOG:-/var/local/verdify/state/firmware-ota-freeze-overrides.log}"

require_override_reason() {
    local gate="$1"
    if [[ ${#override_reason} -lt 12 ]]; then
        fail "$gate blocked; set FIRMWARE_OTA_FREEZE_OVERRIDE_REASON with an operator-approved reason to override"
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
        "$(date -Is)" \
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
# freeze-gate overrides, without requiring FIRMWARE_OTA_FREEZE_OVERRIDE_REASON.
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

max_temp="$("${DB[@]}" "SELECT COALESCE(max(temp_f), -999) FROM weather_forecast WHERE ts > now() AND ts <= now() + interval '24 hours'" | tr -d '[:space:]')"
if awk -v t="$max_temp" 'BEGIN { exit !(t > 85.0) }'; then
    warn "Forecast max next 24h is ${max_temp}F (>85F); proceed with normal post-OTA health validation"
else
    pass "Forecast max next 24h is ${max_temp}F"
fi

last_good="firmware/artifacts/last-good.ota.bin"
last_good_version_file="firmware/artifacts/last-good.version"
if [[ -f "$last_good" ]]; then
    age_s=$(( $(date +%s) - $(mtime_of "$last_good") ))
    if (( age_s < 172800 )); then
        require_override_reason "48-hour bake"
        record_override "48-hour bake" "last-good artifact age is ${age_s}s"
    else
        pass "48-hour bake check passed for $last_good"
    fi
else
    guard_or_fail "No last-good rollback artifact at $last_good; auto-rollback would be unavailable"
fi

last_good_version=""
if [[ -s "$last_good_version_file" ]]; then
    IFS= read -r last_good_version < "$last_good_version_file" || true
fi
if [[ -n "$last_good_version" ]]; then
    pass "Last-good rollback version metadata is present"
else
    guard_or_fail "No nonempty last-good rollback version at $last_good_version_file; exact rollback verification would be unavailable"
fi

week_versions="$("${DB[@]}" \
    "WITH first_seen AS (
       SELECT firmware_version, min(ts) AS first_ts
         FROM diagnostics
        WHERE firmware_version IS NOT NULL AND firmware_version <> ''
        GROUP BY firmware_version
     )
     SELECT count(*)
       FROM first_seen
     WHERE first_ts >= date_trunc('week', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver'" \
    | tr -d '[:space:]')"
if [[ "${week_versions:-0}" -gt 0 ]]; then
    require_override_reason "Weekly OTA limit"
    record_override "Weekly OTA limit" "$week_versions firmware version(s) first appeared this calendar week"
else
    pass "Weekly OTA limit clear"
fi
