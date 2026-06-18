#!/usr/bin/env bash
# checklist-to-slack.sh — Generate daily checklist and post to Slack #greenhouse
# Cron: 0 13 * * * (7:00 AM MDT = 13:00 UTC)
set -uo pipefail

PYTHON="python3"
REPO="/srv/verdify"
SCRIPTS="/srv/verdify/scripts"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
SLACK_CONFIG="${VERDIFY_SLACK_CONFIG:-/srv/verdify/slack.yaml}"
export VERDIFY_SLACK_CONFIG="$SLACK_CONFIG"
read -r SLACK_TOKEN CHANNEL USERNAME ICON_EMOJI < <("$PYTHON" - <<'PY'
from slack_config import load_slack_settings, read_slack_token

s = load_slack_settings()
print(read_slack_token(s.bot_token_file), s.channel_id, s.username, s.icon_emoji or "")
PY
)
LOG="/srv/verdify/state/checklist-slack.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# 1. Generate today's checklist rows
$PYTHON "$SCRIPTS/generate-checklist.py" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR: generate-checklist.py failed"
    exit 1
fi

# 2. Get formatted summary
SUMMARY=$($PYTHON "$SCRIPTS/checklist-summary.py" --format text 2>>"$LOG")
if [ -z "$SUMMARY" ]; then
    log "ERROR: checklist-summary.py returned empty"
    exit 1
fi

# 3. Post to Slack
PAYLOAD=$(python3 -c "
import json, sys
text = sys.stdin.read()
# Convert to Slack mrkdwn
text = text.replace('[ ]', ':white_large_square:').replace('[x]', ':white_check_mark:').replace('[-]', ':fast_forward:')
link = '\n:bar_chart: <https://graphs.verdify.ai/d/greenhouse-grower-daily/|Full checklist + dashboard>'
payload = {'channel': '$CHANNEL', 'text': ':clipboard: *' + text.split(chr(10))[0] + '*\n' + chr(10).join(text.split(chr(10))[1:]) + link, 'username': '$USERNAME'}
if '$ICON_EMOJI':
    payload['icon_emoji'] = '$ICON_EMOJI'
print(json.dumps(payload))
" <<< "$SUMMARY")

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "https://slack.com/api/chat.postMessage" \
    -H "Authorization: Bearer $SLACK_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

if [ "$HTTP_CODE" = "200" ]; then
    log "OK: Posted checklist to #greenhouse ($HTTP_CODE)"
else
    log "WARN: Slack post returned $HTTP_CODE"
fi
