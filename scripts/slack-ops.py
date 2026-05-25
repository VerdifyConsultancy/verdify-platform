#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Deterministic Slack greenhouse operations entrypoint.

OpenClaw Iris can call this script, or the MCP `slack_ops` tool can call the
same Python service directly. By default this parses only; pass --execute to
write through the DB-backed audit and operation paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from slack_ops.service import handle_slack_command  # noqa: E402
from verdify_schemas.slack_ops import SlackCommandRequest  # noqa: E402


async def _run(args: argparse.Namespace) -> int:
    raw_event = json.loads(args.raw_event) if args.raw_event else {}
    request = SlackCommandRequest(
        command_text=args.command or sys.stdin.read(),
        slack_user_id=args.user_id,
        slack_user_name=args.user_name,
        slack_team_id=args.team_id,
        channel_id=args.channel_id,
        channel_name=args.channel_name,
        message_ts=args.message_ts,
        thread_ts=args.thread_ts,
        raw_event=raw_event,
        execute=args.execute,
    )
    result = await handle_slack_command(request)
    if args.format == "json":
        print(result.model_dump_json(indent=2))
    else:
        print(result.text)
    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", default=None, help="Slack message text; stdin is used when omitted")
    parser.add_argument("--execute", action="store_true", help="execute writes and DB reads; default parses only")
    parser.add_argument("--format", choices=("text", "json"), default="json")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--user-name", default=None)
    parser.add_argument("--team-id", default=None)
    parser.add_argument("--channel-id", default=None)
    parser.add_argument("--channel-name", default=None)
    parser.add_argument("--message-ts", default=None)
    parser.add_argument("--thread-ts", default=None)
    parser.add_argument("--raw-event", default=None, help="JSON object copied from Slack/OpenClaw metadata")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
