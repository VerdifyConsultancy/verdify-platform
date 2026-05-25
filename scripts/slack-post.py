#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Post a message through Verdify's shared Slack config."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from slack_config import build_slack_payload, load_slack_settings, read_slack_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=None, help="Slack channel override; defaults to slack.yaml")
    parser.add_argument("--thread-ts", default=None)
    parser.add_argument("--text", default=None)
    args = parser.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print("slack-post: empty message", file=sys.stderr)
        return 2

    settings = load_slack_settings()
    token = read_slack_token(settings.bot_token_file)
    payload = build_slack_payload(settings, text, channel=args.channel, thread_ts=args.thread_ts)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{settings.api_base_url}/chat.postMessage",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        print(f"slack-post: Slack API error: {result.get('error', 'unknown')}", file=sys.stderr)
        return 1
    print(result.get("ts", "ok"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
