#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Run one remote planner_graph shadow smoke from Verdify.

The smoke uses an existing production trigger as the comparison key when
available. It never calls MCP write tools and never executes the remote
proposal; it only stores a shadow evaluation row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from iris_planner import gather_context  # noqa: E402
from planner_graph_shadow import run_planner_graph_shadow  # noqa: E402

from config import DB_DSN  # noqa: E402


def _cloud_run_url(service: str, region: str, project: str) -> str:
    cmd = [
        "gcloud",
        "run",
        "services",
        "describe",
        service,
        "--region",
        region,
        "--project",
        project,
        "--format=value(status.url)",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"gcloud Cloud Run lookup failed: {proc.stderr.strip()}")
    url = proc.stdout.strip()
    if not url:
        raise SystemExit("gcloud Cloud Run lookup returned no status.url")
    return url


async def _latest_trigger() -> dict[str, str] | None:
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchrow(
            """
            SELECT trigger_id::text AS trigger_id,
                   event_type,
                   COALESCE(event_label, event_type) AS event_label,
                   COALESCE(instance, 'local') AS instance,
                   status
              FROM plan_delivery_log
             WHERE trigger_id IS NOT NULL
               AND event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT', 'MANUAL', 'FORECAST_DEVIATION', 'TRANSITION')
             ORDER BY
               CASE WHEN status = 'plan_written' THEN 0 WHEN status = 'acked' THEN 1 ELSE 2 END,
               delivered_at DESC
             LIMIT 1
            """
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def _run(args: argparse.Namespace) -> int:
    base_url = args.base_url
    if not base_url and args.cloud_run_service:
        base_url = _cloud_run_url(args.cloud_run_service, args.cloud_run_region, args.cloud_run_project)
    if not base_url:
        base_url = os.getenv("PLANNER_GRAPH_URL", "").strip()
    if not base_url:
        raise SystemExit("Provide --base-url or --cloud-run-service/--cloud-run-region/--cloud-run-project")

    if args.context_file:
        context = Path(args.context_file).read_text()
    else:
        context = gather_context()

    if args.trigger_id:
        trigger = {
            "trigger_id": args.trigger_id,
            "event_type": args.event,
            "event_label": args.label,
            "instance": args.instance,
        }
    else:
        trigger = await _latest_trigger()
        if trigger is None:
            raise SystemExit("No existing trigger_id found in plan_delivery_log; pass --trigger-id")

    summary = await run_planner_graph_shadow(
        event_type=trigger["event_type"],
        event_label=trigger["event_label"],
        context=context,
        trigger_id=trigger["trigger_id"],
        planner_instance=trigger["instance"],
        base_url=base_url,
        persist=not args.no_persist,
        local_wait_timeout_s=args.local_wait_timeout,
    )
    print(json.dumps(summary, sort_keys=True, default=str))
    return 0 if summary.get("error") is None else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", help="Planner service base URL.")
    parser.add_argument("--cloud-run-service", help="Cloud Run service name.")
    parser.add_argument("--cloud-run-region", default="us-central1")
    parser.add_argument("--cloud-run-project", default="buoyant-valve-496719-m0")
    parser.add_argument("--trigger-id", help="Existing Verdify trigger_id to compare against.")
    parser.add_argument("--event", default="MANUAL")
    parser.add_argument("--label", default="Planner graph shadow smoke")
    parser.add_argument("--instance", default="local")
    parser.add_argument("--context-file", help="Use a saved gathered context instead of collecting a fresh one.")
    parser.add_argument("--local-wait-timeout", type=float, default=0.0)
    parser.add_argument("--no-persist", action="store_true", help="Run submit/poll/compare without writing shadow row.")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
