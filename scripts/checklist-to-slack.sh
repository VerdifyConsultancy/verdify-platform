#!/usr/bin/env bash
# checklist-to-slack.sh — Generate daily checklist and post to Slack #greenhouse
# Cron: 0 13 * * * in the host timezone (America/Denver)
set -uo pipefail

PYTHON="/srv/greenhouse/.venv/bin/python3"
SCRIPTS="/srv/verdify/scripts"
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
MESSAGE=$("$PYTHON" -c "
import sys
text = sys.stdin.read()
# Convert to Slack mrkdwn
text = text.replace('[ ]', ':white_large_square:').replace('[x]', ':white_check_mark:').replace('[-]', ':fast_forward:')
link = '\n:bar_chart: <https://graphs.verdify.ai/d/greenhouse-grower-daily/|Full checklist + dashboard>'
print(':clipboard: *' + text.split(chr(10))[0] + '*\n' + chr(10).join(text.split(chr(10))[1:]) + link)
" <<< "$SUMMARY")

if printf '%s' "$MESSAGE" | "$PYTHON" "$SCRIPTS/slack-post.py" >> "$LOG" 2>&1; then
    log "OK: Posted checklist to Slack"
else
    log "WARN: Slack post failed"
fi
