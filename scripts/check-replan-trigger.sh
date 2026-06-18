#!/bin/bash
# check-replan-trigger.sh — If a deviation trigger exists and is fresh, route to Iris.
#
# The planning_heartbeat task in the ingestor also checks this trigger file every
# 60s and routes to Iris (via send_to_iris → Hermes). This cron script is a
# fallback in case the ingestor is down. Both paths are idempotent — whichever
# picks up the trigger first deletes it.
set -euo pipefail

TRIGGER="/srv/verdify/state/replan-needed.json"
LOG="/srv/verdify/state/replan.log"
COOLDOWN="/srv/verdify/state/replan-cooldown"
PYTHON="${PYTHON:-python3}"
HERMES_TRIGGER="${HERMES_TRIGGER:-/srv/verdify/scripts/hermes-trigger.py}"

[ -f "$TRIGGER" ] || exit 0

AGE=$(( $(date +%s) - $(stat -c %Y "$TRIGGER") ))
[ "$AGE" -gt 600 ] && exit 0

if [ -f "$COOLDOWN" ]; then
    COOLDOWN_AGE=$(( $(date +%s) - $(stat -c %Y "$COOLDOWN") ))
    [ "$COOLDOWN_AGE" -lt 1800 ] && exit 0
fi

echo "[$(date)] REPLAN triggered (age=${AGE}s) — routing to Iris via Hermes" >> "$LOG"

DEVIATIONS=$(python3 -c "import json; d=json.load(open('$TRIGGER')); print(json.dumps(d.get('deviations',[]),indent=2))" 2>/dev/null || echo "[]")
REASON=$(python3 -c "import json; d=json.load(open('$TRIGGER')); print(d.get('reason','Unknown'))" 2>/dev/null || echo "Unknown deviation")

if "$PYTHON" "$HERMES_TRIGGER" --event FORECAST_DEVIATION --label "$DEVIATIONS" --instance local >> "$LOG" 2>&1; then
    echo "[$(date)] Iris notified via audited Hermes trigger helper. Deviation: $REASON" >> "$LOG"
    touch "$COOLDOWN"
    rm -f "$TRIGGER"
    echo "[$(date)] Replan complete (routed to Iris)" >> "$LOG"
else
    echo "[$(date)] Iris notification FAILED via audited Hermes trigger helper. Deviation: $REASON" >> "$LOG"
fi
