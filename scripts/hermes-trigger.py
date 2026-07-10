#!/usr/bin/env python3
"""Send a single audited planning trigger through Hermes.

Fallback path for cron/operator scripts when the ingestor heartbeat is down.
It pre-creates plan_delivery_log, sends the normal iris_planner prompt with the
audit banner, then updates the same row with the Hermes /v1/runs result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from iris_planner import (  # noqa: E402
    CONTEXT_GATHER_FAILED_SENTINEL,
    gather_context,
    prepare_delivery_result,
    send_to_iris,
)

from config import DB_DSN  # noqa: E402
from verdify_schemas import PlanDeliveryLogRow  # noqa: E402


async def _upsert_delivery(result: dict) -> int | None:
    row = {
        "event_type": result["event_type"],
        "event_label": result.get("event_label"),
        "session_key": result.get("session_key"),
        "wake_mode": result.get("wake_mode"),
        "gateway_status": result.get("gateway_status"),
        "gateway_body": result.get("gateway_body"),
        "trigger_id": result.get("trigger_id"),
        "instance": result.get("instance"),
        "hermes_run_id": result.get("hermes_run_id"),
    }
    explicit_status = result.get("status")
    if explicit_status is None and result.get("delivered") is False and result.get("gateway_status") is not None:
        explicit_status = "delivery_failed"
    terminal_action = result.get("terminal_action")
    failure_class = result.get("failure_class")
    if explicit_status == "delivery_failed":
        terminal_action = terminal_action or "delivery_failed"
        failure_class = failure_class or "gateway_delivery_failed"
    if explicit_status is not None:
        row["status"] = explicit_status
    try:
        PlanDeliveryLogRow.model_validate(row)
    except ValidationError as exc:
        raise SystemExit(f"plan_delivery_log validation failed: {exc}") from exc

    conn = await asyncpg.connect(DB_DSN)
    try:
        return await conn.fetchval(
            """
            INSERT INTO plan_delivery_log AS pdl
              (event_type, event_label, session_key, wake_mode, gateway_status, gateway_body,
               status, trigger_id, instance, hermes_run_id,
               terminal_action, terminal_at, failure_class)
            VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'pending'), $8::uuid, $9, $10,
                    $11, CASE WHEN $11::text IS NULL THEN NULL ELSE now() END, $12)
            ON CONFLICT (trigger_id) DO UPDATE
              SET event_type     = EXCLUDED.event_type,
                  event_label    = EXCLUDED.event_label,
                  session_key    = COALESCE(EXCLUDED.session_key, pdl.session_key),
                  wake_mode      = COALESCE(EXCLUDED.wake_mode, pdl.wake_mode),
                  gateway_status = EXCLUDED.gateway_status,
                  gateway_body   = COALESCE(EXCLUDED.gateway_body, pdl.gateway_body),
                  instance       = COALESCE(EXCLUDED.instance, pdl.instance),
                  hermes_run_id  = COALESCE(EXCLUDED.hermes_run_id, pdl.hermes_run_id),
                  terminal_action = COALESCE(pdl.terminal_action, $11::text),
                  terminal_at = CASE
                                  WHEN pdl.terminal_at IS NOT NULL THEN pdl.terminal_at
                                  WHEN $11::text IS NOT NULL THEN now()
                                  ELSE NULL
                                END,
                  failure_class = COALESCE(pdl.failure_class, $12::text),
                  status         = CASE
                                     WHEN pdl.status IN (
                                         'acked', 'plan_written', 'action_completed',
                                         'neutral_fallback', 'wrong_action'
                                     ) THEN pdl.status
                                     ELSE EXCLUDED.status
                                   END
            RETURNING id
            """,
            row["event_type"],
            row["event_label"],
            row["session_key"],
            row["wake_mode"],
            row["gateway_status"],
            row["gateway_body"],
            explicit_status,
            row["trigger_id"],
            row["instance"],
            row["hermes_run_id"],
            terminal_action,
            failure_class,
        )
    finally:
        await conn.close()


async def _run(args: argparse.Namespace) -> int:
    context = gather_context()
    if context == CONTEXT_GATHER_FAILED_SENTINEL:
        result = prepare_delivery_result(args.event, args.label, instance=args.instance)
        result.update(
            {
                "gateway_body": "context_gather_failed",
                "status": "delivery_failed",
            }
        )
        await _upsert_delivery(result)
        print(json.dumps({"ok": False, "error": "context_gather_failed", "trigger_id": result["trigger_id"]}))
        return 2

    pre_result = prepare_delivery_result(args.event, args.label, instance=args.instance)
    await _upsert_delivery(pre_result)
    result = send_to_iris(
        args.event,
        args.label,
        context=context,
        instance=args.instance,
        trigger_id=pre_result["trigger_id"],
    )
    await _upsert_delivery(result)
    print(
        json.dumps(
            {
                "ok": bool(result.get("delivered")),
                "trigger_id": result.get("trigger_id"),
                "hermes_run_id": result.get("hermes_run_id"),
                "gateway_status": result.get("gateway_status"),
                "status": result.get("status") or ("pending" if result.get("delivered") else "delivery_failed"),
            }
        )
    )
    return 0 if result.get("delivered") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--instance", default="local", choices=("local", "opus"))
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
