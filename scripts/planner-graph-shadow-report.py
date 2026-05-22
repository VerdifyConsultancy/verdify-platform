#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Summarize persisted planner_graph shadow evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from config import DB_DSN  # noqa: E402


async def _report(since: str) -> dict[str, object]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT delivered_at, gateway_status, gateway_body
             FROM plan_delivery_log_shadow
             WHERE event_type = 'PLANNER_GRAPH_SHADOW'
               AND delivered_at >= now() - ($1::text)::interval
             ORDER BY delivered_at DESC
            """,
            since,
        )
    finally:
        await conn.close()

    summary: dict[str, object] = {
        "window": since,
        "total": len(rows),
        "completed": 0,
        "failed_or_timed_out": 0,
        "accepted_by_verdify_validation": 0,
        "rejected_by_verdify_validation": 0,
        "action_types": {},
        "judgements": {},
        "top_rejection_reasons": {},
        "latest": [],
    }
    action_types: dict[str, int] = {}
    judgements: dict[str, int] = {}
    rejection_reasons: dict[str, int] = {}
    latest: list[dict[str, object]] = []
    for row in rows:
        try:
            body = json.loads(row["gateway_body"] or "{}")
        except json.JSONDecodeError:
            body = {}
        terminal = body.get("remote_terminal_status") if isinstance(body, dict) else {}
        diff = body.get("diff_summary") if isinstance(body, dict) else {}
        validation = body.get("validation_outcome") if isinstance(body, dict) else {}
        status = terminal.get("status") if isinstance(terminal, dict) else None
        if status == "completed":
            summary["completed"] = int(summary["completed"]) + 1
        else:
            summary["failed_or_timed_out"] = int(summary["failed_or_timed_out"]) + 1
        accepted = False
        if isinstance(validation, dict):
            accepted = bool(validation.get("would_accept_remote", validation.get("accepted", False)))
        if accepted:
            summary["accepted_by_verdify_validation"] = int(summary["accepted_by_verdify_validation"]) + 1
        else:
            summary["rejected_by_verdify_validation"] = int(summary["rejected_by_verdify_validation"]) + 1
        action = str(diff.get("remote_action_type") or "unknown") if isinstance(diff, dict) else "unknown"
        judgement = (
            str(diff.get("judgement") or diff.get("quality_judgement") or "unknown")
            if isinstance(diff, dict)
            else "unknown"
        )
        action_types[action] = action_types.get(action, 0) + 1
        judgements[judgement] = judgements.get(judgement, 0) + 1
        if isinstance(validation, dict):
            for reason in (validation.get("rejection_reasons") or validation.get("errors") or [])[:5]:
                reason_s = str(reason)
                rejection_reasons[reason_s] = rejection_reasons.get(reason_s, 0) + 1
        if len(latest) < 10:
            latest.append(
                {
                    "delivered_at": row["delivered_at"].isoformat(),
                    "gateway_status": row["gateway_status"],
                    "remote_status": status,
                    "remote_action": action,
                    "judgement": judgement,
                    "would_accept_remote": accepted,
                    "trigger_id": body.get("trigger_id") if isinstance(body, dict) else None,
                }
            )
    summary["action_types"] = action_types
    summary["judgements"] = judgements
    summary["top_rejection_reasons"] = dict(
        sorted(rejection_reasons.items(), key=lambda item: item[1], reverse=True)[:10]
    )
    summary["latest"] = latest
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="7 days", help="Postgres interval, e.g. '24 hours' or '7 days'.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_report(args.since)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
