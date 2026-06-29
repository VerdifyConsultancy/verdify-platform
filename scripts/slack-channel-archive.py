#!/usr/bin/env python3
"""
slack-channel-archive.py — Export #greenhouse Slack channel to searchable markdown files.

Pulls all messages (with thread replies) from the #greenhouse channel and writes
them as daily markdown files under the memory search index path. Supports incremental
sync — only fetches messages newer than the last export.

Run via cron: 0 */6 * * * /srv/verdify/scripts/slack-channel-archive.py

Output: configured by slack.yaml (default /var/local/verdify/slack/archive/YYYY-MM-DD.md)
"""

import json
import logging
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from slack_config import load_slack_settings, read_slack_token  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [slack-archive] %(message)s")
log = logging.getLogger(__name__)

SLACK_SETTINGS = load_slack_settings()
CHANNEL_ID = SLACK_SETTINGS.channel_id
TOKEN_FILE = SLACK_SETTINGS.bot_token_file
OUTPUT_DIR = SLACK_SETTINGS.archive_dir
STATE_FILE = OUTPUT_DIR / ".last-sync.json"
TZ = ZoneInfo(SLACK_SETTINGS.timezone)


def load_token():
    return read_slack_token(TOKEN_FILE)


def slack_api(method, params=None, token=None):
    """Call Slack Web API."""
    url = f"https://slack.com/api/{method}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
    if not data.get("ok"):
        log.error("Slack API error: %s", data.get("error"))
    return data


def get_user_name(user_id, users_cache, token):
    """Resolve user ID to display name."""
    if user_id in users_cache:
        return users_cache[user_id]
    try:
        data = slack_api("users.info", {"user": user_id}, token)
        profile = data.get("user", {}).get("profile", {})
        name = profile.get("display_name") or profile.get("real_name") or user_id
        users_cache[user_id] = name
        return name
    except Exception:
        users_cache[user_id] = user_id
        return user_id


def fetch_messages(token, oldest=None, limit=200):
    """Fetch channel messages, paginated."""
    messages = []
    cursor = None
    params = {"channel": CHANNEL_ID, "limit": str(limit)}
    if oldest:
        params["oldest"] = str(oldest)

    while True:
        if cursor:
            params["cursor"] = cursor
        data = slack_api("conversations.history", params, token)
        batch = data.get("messages", [])
        messages.extend(batch)
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(1)  # Rate limit

    return messages


def fetch_thread_replies(token, thread_ts):
    """Fetch replies in a thread."""
    data = slack_api("conversations.replies", {"channel": CHANNEL_ID, "ts": thread_ts, "limit": "200"}, token)
    return data.get("messages", [])[1:]  # Skip the parent message


def format_message(msg, users_cache, token, indent=""):
    """Format a single message as markdown."""
    user = msg.get("user", "bot")
    name = get_user_name(user, users_cache, token) if not msg.get("bot_id") else msg.get("username", "Iris")
    ts = datetime.fromtimestamp(float(msg["ts"]), tz=TZ)
    text = msg.get("text", "").replace("```", "\n```\n")

    # Resolve user mentions
    import re

    for match in re.finditer(r"<@(U[A-Z0-9]+)>", text):
        uid = match.group(1)
        uname = get_user_name(uid, users_cache, token)
        text = text.replace(match.group(0), f"@{uname}")

    return f"{indent}**{name}** ({ts.strftime('%H:%M')}):\n{indent}{text}\n"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    token = load_token()

    # Load sync state
    last_ts = 0
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        last_ts = state.get("last_ts", 0)

    log.info("Fetching messages since ts=%s", last_ts or "beginning")

    # Fetch messages (oldest first by reversing)
    messages = fetch_messages(token, oldest=last_ts if last_ts else None)
    messages.sort(key=lambda m: float(m["ts"]))

    if not messages:
        log.info("No new messages")
        return

    log.info("Fetched %d messages", len(messages))

    # Group by day
    days = defaultdict(list)
    users_cache = {}

    for msg in messages:
        ts = datetime.fromtimestamp(float(msg["ts"]), tz=TZ)
        day = ts.strftime("%Y-%m-%d")
        days[day].append(msg)

    # Fetch thread replies for messages with threads
    thread_count = 0
    for _day, day_msgs in days.items():
        for msg in day_msgs:
            if msg.get("reply_count", 0) > 0:
                replies = fetch_thread_replies(token, msg["ts"])
                msg["_replies"] = replies
                thread_count += len(replies)
                time.sleep(0.5)

    log.info("Fetched %d thread replies across %d days", thread_count, len(days))

    # Write daily files
    for day, day_msgs in sorted(days.items()):
        outfile = OUTPUT_DIR / f"{day}.md"

        # Append to existing file if it exists
        existing = outfile.read_text() if outfile.exists() else ""
        lines = [existing] if existing else [f"# Slack #greenhouse — {day}\n\n"]

        for msg in day_msgs:
            lines.append(format_message(msg, users_cache, token))
            for reply in msg.get("_replies", []):
                lines.append(format_message(reply, users_cache, token, indent="> "))
            lines.append("---\n\n")

        outfile.write_text("".join(lines))

    # Update sync state
    max_ts = max(float(m["ts"]) for m in messages)
    STATE_FILE.write_text(json.dumps({"last_ts": max_ts, "last_sync": datetime.now(TZ).isoformat()}))

    log.info("Wrote %d daily files. Last ts: %s", len(days), max_ts)


if __name__ == "__main__":
    main()
