#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""CLI smoke path for deterministic Iris Slack operations."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from slack_config import load_slack_settings  # noqa: E402
from slack_ops.service import handle_slack_command  # noqa: E402
from verdify_schemas.slack_ops import SlackCommandRequest  # noqa: E402


async def _amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="+", help="Command text to parse and execute")
    parser.add_argument("--user-id", default="local-cli")
    parser.add_argument("--user-name", default="local-cli")
    parser.add_argument("--role", default="coordinator")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = load_slack_settings()
    req = SlackCommandRequest(
        text=" ".join(args.text),
        slack_user_id=args.user_id,
        slack_user_name=args.user_name,
        channel_id=settings.channel_id,
        channel_name=settings.channel_name,
        raw_event={"source": "scripts/slack-ops.py"},
    )
    response = await handle_slack_command(req, settings=settings, role_override=args.role)
    if args.json:
        print(response.model_dump_json(indent=2))
    else:
        print(response.text)
    return 0 if response.ok else 1


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
