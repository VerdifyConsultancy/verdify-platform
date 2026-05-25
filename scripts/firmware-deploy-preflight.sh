#!/usr/bin/env bash
# Firmware OTA preflight guard.
#
# Enforces the phase-0 deploy rules that can be checked locally before compile
# or upload. Forecast heat is reported as context, not a deploy blocker.

set -euo pipefail

DB=(docker exec verdify-timescaledb psql -U verdify -d verdify -t -A -F '|' -c)

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
        warn "$message — operator override active (${FIRMWARE_DEPLOY_OVERRIDE_REASON:-no reason supplied})"
    else
        if override_enabled; then
            fail "$message; override requested but FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1 and FIRMWARE_DEPLOY_OVERRIDE_REASON are required"
        fi
        fail "$message"
    fi
}

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
    mkdir -p "$(dirname "$override_log")"
    printf '%s\tgate=%s\treason=%s\tdetail=%s\tworktree=%s\tsha=%s\n' \
        "$(date -Is)" \
        "$gate" \
        "$override_reason" \
        "$detail" \
        "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" \
        "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
        >> "$override_log"
    warn "$gate override accepted: $detail"
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

action_age="$("${DB[@]}" "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(ts))::int, 2147483647) FROM climate_action_log" | tr -d '[:space:]')"
if [[ ! "${action_age:-}" =~ ^[0-9]+$ || "$action_age" -gt 300 ]]; then
    guard_or_fail "Climate action log stale or missing (${action_age:-no data}s); no firmware OTA without fresh controller-decision proof"
fi
pass "Climate action log fresh (${action_age}s)"

max_temp="$("${DB[@]}" "SELECT COALESCE(max(temp_f), -999) FROM weather_forecast WHERE ts > now() AND ts <= now() + interval '24 hours'" | tr -d '[:space:]')"
if awk -v t="$max_temp" 'BEGIN { exit !(t > 85.0) }'; then
    warn "Forecast max next 24h is ${max_temp}F (>85F); proceed with normal post-OTA health validation"
else
    pass "Forecast max next 24h is ${max_temp}F"
fi

last_good="firmware/artifacts/last-good.ota.bin"
if [[ -f "$last_good" ]]; then
    age_s=$(( $(date +%s) - $(stat -c %Y "$last_good") ))
    if (( age_s < 172800 )); then
        require_override_reason "48-hour bake"
        record_override "48-hour bake" "last-good artifact age is ${age_s}s"
    else
        pass "48-hour bake check passed for $last_good"
    fi
else
    guard_or_fail "No last-good rollback artifact at $last_good; auto-rollback would be unavailable"
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
