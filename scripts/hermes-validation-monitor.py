#!/usr/bin/env python3
"""One-shot Hermes/planner validation monitor.

Designed to be run from cron/systemd/nohup every two hours. It prints one JSON
record and exits non-zero only for conditions that need operator attention.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from config import DB_DSN  # noqa: E402
from verdify_schemas.tunable_registry import TIER1_REG  # noqa: E402

EXPECTED_TIER1 = len(TIER1_REG)
RESERVED_NO_EFFECT = ("mist_vent_close_lead_s", "mist_vent_reopen_delay_s", "summer_vent_min_runtime_s")
EMBEDDING_SOURCES = ("lesson", "plan", "site_doc", "playbook", "observation")


def _hermes_health() -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    urls = {
        "gateway": os.environ.get("HERMES_HEALTH_URL", "http://127.0.0.1:8642/health"),
        "mcp_tools": os.environ.get("VERDIFY_MCP_READINESS_URL", "http://127.0.0.1:8000/readyz"),
    }
    for name, url in urls.items():
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read(4096).decode("utf-8")
                payload = json.loads(body) if name == "mcp_tools" else None
                ok = resp.status == 200 and (payload is None or payload.get("ready") is True)
                checks[name] = {"ok": ok, "status": resp.status, "body": payload or body[:512]}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            checks[name] = {"ok": False, "error_class": type(exc).__name__}
    return {"ok": all(check["ok"] for check in checks.values()), "checks": checks}


def _traceability_audit() -> dict[str, object]:
    proc = subprocess.run(
        [str(REPO_ROOT / "scripts" / "audit-tunable-traceability.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout.strip()}


async def _db_checks() -> dict[str, object]:
    import asyncpg

    conn = await asyncpg.connect(DB_DSN)
    try:
        open_alerts = await conn.fetchval("SELECT count(*) FROM alert_log WHERE disposition = 'open'")
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)
              FROM alert_log
             WHERE disposition = 'open'
               AND severity IN ('critical', 'high')
            """
        )
        open_warning = await conn.fetchval(
            """
            SELECT count(*)
              FROM alert_log
             WHERE disposition = 'open'
               AND severity = 'warning'
            """
        )
        future = await conn.fetchrow(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT parameter) AS params,
                   min(sp.ts) AS first_ts,
                   max(sp.ts) AS last_ts
              FROM setpoint_plan sp
              JOIN plan_journal pj ON pj.plan_id = sp.plan_id
             WHERE sp.is_active = true
               AND sp.ts > now()
               AND sp.expires_at > now()
               AND pj.lifecycle_status = 'effective'
               AND pj.valid_from <= now()
               AND pj.expires_at > now()
            """
        )
        effective_plan = await conn.fetchrow(
            """
            SELECT count(*) AS rows, min(expires_at) AS expires_at
              FROM plan_journal
             WHERE lifecycle_status = 'effective'
               AND valid_from <= now()
               AND expires_at > now()
            """
        )
        active_params = await conn.fetchval("SELECT count(DISTINCT parameter) FROM v_active_plan")
        reserved_active = await conn.fetchval(
            "SELECT count(*) FROM setpoint_plan WHERE is_active = true AND parameter = ANY($1::text[])",
            list(RESERVED_NO_EFFECT),
        )
        recent_dispatch = await conn.fetchrow(
            """
            SELECT count(*) AS rows,
                   count(*) FILTER (WHERE trigger_id IS NOT NULL) AS with_trigger
              FROM setpoint_changes
             WHERE source = 'plan'
               AND ts > now() - interval '30 minutes'
               AND COALESCE(planner_instance, '') <> 'codex-operator'
            """
        )
        unresolved_delivery = await conn.fetchval(
            """
            SELECT count(*)
              FROM plan_delivery_log
             WHERE status = 'pending'
               AND delivered_at < now() - interval '30 minutes'
            """
        )
        latest_delivery = await conn.fetchrow(
            """
            SELECT event_type, status, terminal_action, terminal_at, failure_class,
                   trigger_id::text AS trigger_id, hermes_run_id, resulting_plan_id,
                   delivered_at, acked_at, plan_written_at, result_payload
              FROM plan_delivery_log
             ORDER BY delivered_at DESC
             LIMIT 1
            """
        )
        embedding_counts = await conn.fetch(
            """
            SELECT source_type, count(*) AS rows
              FROM verdify_embeddings
             WHERE source_type = ANY($1::text[])
             GROUP BY source_type
            """,
            list(EMBEDDING_SOURCES),
        )
        return {
            "open_alerts": int(open_alerts or 0),
            "open_critical_high_alerts": int(open_critical_high or 0),
            "open_warning_alerts": int(open_warning or 0),
            "future_rows": int(future["rows"] or 0),
            "future_params": int(future["params"] or 0),
            "future_first_ts": future["first_ts"].isoformat() if future["first_ts"] else None,
            "future_last_ts": future["last_ts"].isoformat() if future["last_ts"] else None,
            "effective_plan_count": int(effective_plan["rows"] or 0),
            "effective_plan_expires_at": (
                effective_plan["expires_at"].isoformat() if effective_plan["expires_at"] else None
            ),
            "active_params": int(active_params or 0),
            "reserved_active_rows": int(reserved_active or 0),
            "recent_plan_dispatch_rows": int(recent_dispatch["rows"] or 0),
            "recent_plan_dispatch_with_trigger": int(recent_dispatch["with_trigger"] or 0),
            "unresolved_delivery_over_30m": int(unresolved_delivery or 0),
            "latest_delivery": dict(latest_delivery) if latest_delivery else None,
            "embedding_counts": {r["source_type"]: int(r["rows"]) for r in embedding_counts},
        }
    finally:
        await conn.close()


def _failures(health: dict[str, object], traceability: dict[str, object], db: dict[str, object]) -> list[str]:
    failures: list[str] = []
    if not health.get("ok"):
        failures.append("hermes_health_failed")
    if not traceability.get("ok"):
        failures.append("tunable_traceability_failed")
    # Climate warnings such as VPD stress are quality signals, not deployment
    # failures. Only deploy-blocking alerts should make this monitor non-zero.
    if db["open_critical_high_alerts"] != 0:
        failures.append("open_critical_high_alerts")
    if db["future_params"] < EXPECTED_TIER1:
        failures.append("future_plan_param_count")
    if db["effective_plan_count"] != 1:
        failures.append("effective_plan_count")
    if db["active_params"] < EXPECTED_TIER1:
        failures.append("active_plan_param_count")
    if db["reserved_active_rows"] != 0:
        failures.append("reserved_no_effect_rows_active")
    if db["unresolved_delivery_over_30m"] != 0:
        failures.append("stale_pending_delivery")
    rows = db["recent_plan_dispatch_rows"]
    with_trigger = db["recent_plan_dispatch_with_trigger"]
    if rows and rows != with_trigger:
        failures.append("dispatcher_audit_missing_trigger")
    missing_sources = [s for s in EMBEDDING_SOURCES if db["embedding_counts"].get(s, 0) == 0]
    if missing_sources:
        failures.append("embedding_source_missing:" + ",".join(missing_sources))
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one validation pass")
    parser.parse_args()

    health = _hermes_health()
    traceability = _traceability_audit()
    db = await _db_checks()
    failures = _failures(health, traceability, db)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "ok": not failures,
        "failures": failures,
        "hermes": health,
        "traceability": traceability,
        "db": db,
        "duration_ms": None,
    }
    print(json.dumps(record, default=str, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    start = time.monotonic()
    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        _ = start
