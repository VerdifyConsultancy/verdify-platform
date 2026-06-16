#!/usr/bin/env bash
# data-path-postdeploy-verify.sh — production-impact check for the 2026-06-16
# data-path 5-fix deploy (firmware 2026.6.16.1613.c5455ac + migrations 177-179 +
# ingestor/grafana roll). Run from the LAPTOP (needs kubectl → prod DB; a cloud
# agent can't reach it). Re-run periodically over the 48h firmware bake.
#
#   bash scripts/data-path-postdeploy-verify.sh
#
# Optional laptop cron (every 4h):
#   0 */4 * * * cd ~/repos/verdify-platform && bash scripts/data-path-postdeploy-verify.sh >> /tmp/datapath-verify.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
DB() { scripts/verdify-db.sh prod -t -A -P pager=off -c "$1" 2>/dev/null; }
FW="2026.6.16.1613.c5455ac"
echo "═══ data-path 5-fix post-deploy verify @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ═══"

# ── Issue 5: firmware deployed + healthy + F7 rails firing on excursions ──
live_fw="$(DB "SELECT firmware_version FROM diagnostics ORDER BY ts DESC LIMIT 1")"
# The live device doesn't expose mode_reason in a clean column (only twin_decisions
# does), so observe F7 by its TRIGGER: VPD excursions past the 2.50 kPa vpd_max_safe
# rail (which now forces SEALED_MIST) and below the 0.30 vpd_min_safe rail. With the
# rails wired, sustained excursions should NOT persist (the device mists/dehums them down).
f7hi="$(DB "SELECT count(*) FROM climate WHERE ts > now()-interval '6 hours' AND vpd_avg > 2.50")"
f7lo="$(DB "SELECT count(*) FROM climate WHERE ts > now()-interval '6 hours' AND vpd_avg < 0.30 AND vpd_avg IS NOT NULL")"
echo "issue5  firmware=$live_fw $([ "$live_fw" = "$FW" ] && echo GREEN || echo '⚠ NOT the deployed build') | F7 excursions 6h: vpd>2.50=$f7hi vpd<0.30=$f7lo (rails now act on these)"

# ── Issue 2: device-vs-DB band divergence visible + under alert threshold ──
div="$(DB "SELECT round(EXTRACT(epoch FROM device_age))||'s dev-age, maxΔvpd='||round(max_vpd_abs_diff::numeric,3)||' maxΔT='||round(max_temp_abs_diff::numeric,2) FROM v_band_device_divergence")"
div_alerts="$(DB "SELECT count(*) FROM alert_log WHERE alert_type='band_device_db_divergence' AND disposition='open'")"
echo "issue2  divergence: $div | open band_device_db_divergence alerts=$div_alerts (GREEN if maxΔvpd<0.40 & 0 alerts)"

# ── Issue 1: orchid night band durable + fail-closed (no DB-error fallback) ──
orchid="$(DB "SELECT value FROM crop_band_anchors WHERE crop_type='orchid' AND series='vpd_target' AND anchor='mid'")"
rh="$(DB "SELECT round(avg(rh_avg)::numeric,0) FROM climate WHERE ts > now()-interval '30 min'")"
failclosed="$(DB "SELECT count(*) FROM alert_log WHERE alert_type='band_anchor_db_read_failed' AND created_at > now()-interval '6 hours'")"
echo "issue1  orchid mid=$orchid $([ "$orchid" = "0.7" ] && echo GREEN || echo '⚠ DRIFTED from 0.70') | RH(30m)=${rh}% | band_anchor_db_read_failed(6h)=$failclosed (GREEN=0)"

# ── Issue 4: lighting single-source + differentiated, no reboot poison-loop ──
light="$(DB "SELECT string_agg(light_key||'='||target_light_minutes||'min/'||round(legacy_dli_target)||'mol', ' ') FROM fn_lighting_minutes_policy(now())")"
echo "issue4  lighting: $light (GREEN = main=780 grow=900, differentiated)"

# ── Issue 3: dispatcher band_source default = anchors (no-op in prod) ──
echo "issue3  band_source default=anchors (code reads true; prod overlay pins anchors) — GREEN by deploy"

# ── 48h firmware bake → promote to rollback target ──
bake_h="$(DB "SELECT round(EXTRACT(epoch FROM now()-min(ts))/3600,1) FROM diagnostics WHERE firmware_version='$FW'")"
crit="$(DB "SELECT count(*) FROM alert_log WHERE disposition='open' AND severity IN ('critical','high')")"
echo "bake    $FW baked ${bake_h}h | open critical/high alerts=$crit"
if awk -v b="$bake_h" 'BEGIN{exit !(b+0 >= 48)}' && [ "${crit:-1}" = "0" ] && [ "$live_fw" = "$FW" ]; then
    echo "bake    ≥48h clean → promoting $FW to rollback target"
    make firmware-promote-last-good FW_VERSION="$FW"
else
    echo "bake    not yet promotable (need ≥48h baked, 0 critical alerts, build still live)"
fi
echo "═══ done ═══"
