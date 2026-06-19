#!/usr/bin/env python3
"""Planner feedback loop end-to-end smoke test.

Exercises the unified plan schema + feedback loop + manifestation surface
against the live production stack without polluting real plan state.

Strategy:
  - Schema rejections: direct Pydantic validation (same code path MCP uses).
  - DB migration: column existence check.
  - Live confirmation loop: synthetic setpoint_changes row whose value
    already matches the ESP32's current cfg readback. The next
    setpoint_snapshot cycle (~60 s) UPDATEs confirmed_at. We clean up.
  - FB-1 alert path: synthetic row with ts=6 min ago + a deliberately
    mismatched value. setpoint_confirmation_monitor (invoked directly)
    writes a setpoint_unconfirmed alert. We clean that up too.
  - Website surface: three curl checks with Host: lab.verdify.ai.

Run: python3 scripts/smoke-feedback-loop.py
Exit code: 0 on all pass, 1 on any fail.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))
from pydantic import ValidationError  # noqa: E402

from verdify_schemas import (  # noqa: E402
    Plan,
    PlanHypothesisStructured,
)

# Pick a readbackable planner-policy param unlikely to churn under real dispatch.
TEST_PARAM = "mister_vpd_weight"

# Sentinel source tag so we can clean up without touching production rows.
SMOKE_SOURCE = "smoke_feedback_loop"

# ── Helpers ─────────────────────────────────────────────────────────

_state = {"pass": 0, "fail": 0}


def step(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        _state["pass"] += 1
        print(f"  PASS  {name}")
    else:
        _state["fail"] += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
    return ok


def section(title: str) -> None:
    print(f"\n── {title} ──")


def _dsn() -> str:
    env: dict[str, str] = {}
    env_path = Path("/srv/verdify/ingestor/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k] = v
    return (
        f"postgresql://{env.get('DB_USER', 'verdify')}:"
        f"{env.get('DB_PASSWORD', os.environ.get('POSTGRES_PASSWORD', 'verdify'))}"
        f"@{env.get('DB_HOST', 'localhost')}:{env.get('DB_PORT', '5432')}/{env.get('DB_NAME', 'verdify')}"
    )


def curl(url: str, host: str, timeout: int = 10) -> int:
    r = subprocess.run(
        ["curl", "-sk", url, "-H", f"Host: {host}", "-o", "/dev/null", "-w", "%{http_code}"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return int(r.stdout.strip() or "0")


# ── Sections ────────────────────────────────────────────────────────


def test_schema_rejections() -> None:
    section("Schema rejections (Pydantic boundary)")

    # Unknown parameter
    ok = False
    try:
        Plan.model_validate(
            {
                "plan_id": "iris-20990101-0001",
                "hypothesis": "x",
                "transitions": [{"ts": "2099-01-01T00:00:00+00:00", "params": {"total_nonsense": 1.0}}],
            }
        )
    except ValidationError as e:
        ok = "Unknown tunable" in str(e)
    step("Unknown parameter rejected", ok)

    # Inverted VPD band
    ok = False
    try:
        Plan.model_validate(
            {
                "plan_id": "iris-20990101-0001",
                "hypothesis": "x",
                "transitions": [{"ts": "2099-01-01T00:00:00+00:00", "params": {"vpd_low": 2.5, "vpd_high": 0.5}}],
            }
        )
    except ValidationError as e:
        ok = "vpd_low" in str(e) and "vpd_high" in str(e)
    step("Inverted vpd band rejected", ok)

    # Non-monotonic transitions
    ok = False
    try:
        Plan.model_validate(
            {
                "plan_id": "iris-20990101-0001",
                "hypothesis": "x",
                "transitions": [
                    {"ts": "2099-01-01T02:00:00+00:00", "params": {"temp_low": 55.0}},
                    {"ts": "2099-01-01T01:00:00+00:00", "params": {"temp_low": 56.0}},
                ],
            }
        )
    except ValidationError as e:
        ok = "strictly ts-ascending" in str(e)
    step("Non-monotonic transitions rejected", ok)

    # Bad plan_id pattern
    ok = False
    try:
        Plan.model_validate(
            {
                "plan_id": "manual-thing",
                "hypothesis": "x",
                "transitions": [{"ts": "2099-01-01T00:00:00+00:00", "params": {"temp_low": 55.0}}],
            }
        )
    except ValidationError as e:
        ok = "pattern" in str(e).lower()
    step("Bad plan_id format rejected", ok)


def test_structured_hypothesis_extraction() -> None:
    section("Structured hypothesis parsing")

    valid_json = {
        "conditions": {
            "outdoor_temp_peak_f": 82.0,
            "outdoor_rh_min_pct": 12.0,
            "solar_peak_w_m2": 900.0,
            "cloud_cover_avg_pct": 15.0,
        },
        "stress_windows": [],
        "rationale": [
            {
                "parameter": "mister_engage_kpa",
                "old_value": 1.5,
                "new_value": 1.3,
                "forecast_anchor": "peak stress window",
                "expected_effect": "earlier engage",
            }
        ],
    }

    ok = False
    try:
        structured = PlanHypothesisStructured.model_validate(valid_json)
        ok = structured.conditions.outdoor_temp_peak_f == 82.0
    except ValidationError as e:
        step("Valid structured hypothesis parses", False, str(e))
        return
    step("Valid structured hypothesis parses", ok)

    # Extraction via regex — same logic the MCP set_plan tool uses
    import re

    prose = "Plan explains stuff.\n\n```json\n" + json.dumps(valid_json) + "\n```\n\nMore prose."
    match = re.search(r"```json\s*(\{.*?\})\s*```", prose, re.DOTALL)
    step("Fenced JSON block extracted from prose", match is not None)
    if match:
        extracted = PlanHypothesisStructured.model_validate_json(match.group(1))
        step(
            "Extracted JSON round-trips through schema",
            extracted.rationale[0].parameter == "mister_engage_kpa",
        )


async def test_db_migration(pool: asyncpg.Pool) -> None:
    section("DB migration 084 applied")

    async with pool.acquire() as conn:
        col = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='setpoint_changes' AND column_name='confirmed_at'"
        )
        step("setpoint_changes.confirmed_at exists", col == "confirmed_at")

        col = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='plan_journal' AND column_name='hypothesis_structured'"
        )
        step("plan_journal.hypothesis_structured exists", col == "hypothesis_structured")

        idx = await conn.fetchval(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='setpoint_changes' AND indexname='idx_setpoint_changes_unconfirmed'"
        )
        step("partial index on unconfirmed rows exists", idx == "idx_setpoint_changes_unconfirmed")


async def test_live_confirmation(pool: asyncpg.Pool) -> None:
    section("Live confirmation loop (FW-4)")

    # 1. Read current cfg readback for the test param so our synthetic row
    #    will match the dispatcher's 1% dead-band and the ingestor will
    #    confirm it on the next setpoint_snapshot cycle.
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT value FROM setpoint_snapshot WHERE parameter=$1 ORDER BY ts DESC LIMIT 1",
            TEST_PARAM,
        )

    if current is None:
        step(f"{TEST_PARAM} has a recent cfg readback", False, "no setpoint_snapshot row")
        return
    step(f"{TEST_PARAM} has a recent cfg readback ({current})", True)

    # 2. Insert a synthetic setpoint_changes row, tagged so cleanup is easy.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO setpoint_changes (ts, parameter, value, source) VALUES (now(), $1, $2, $3)",
            TEST_PARAM,
            float(current),
            SMOKE_SOURCE,
        )

    # 3. Wait up to 90 s for confirmed_at to populate (setpoint_snapshot runs
    #    every 60 s; allow one full cycle plus margin).
    confirmed = None
    deadline = time.time() + 90
    while time.time() < deadline:
        async with pool.acquire() as conn:
            confirmed = await conn.fetchval(
                "SELECT confirmed_at FROM setpoint_changes WHERE source=$1 AND parameter=$2 ORDER BY ts DESC LIMIT 1",
                SMOKE_SOURCE,
                TEST_PARAM,
            )
        if confirmed is not None:
            break
        await asyncio.sleep(5)

    step(f"setpoint_changes.confirmed_at populated within 90 s (got {confirmed})", confirmed is not None)

    # 4. Clean up test row regardless of outcome
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM setpoint_changes WHERE source=$1 AND parameter=$2",
            SMOKE_SOURCE,
            TEST_PARAM,
        )


async def test_fb1_alert(pool: asyncpg.Pool) -> None:
    section("FB-1 alert path (setpoint_unconfirmed)")

    async with pool.acquire() as conn:
        current = await conn.fetchval(
            "SELECT value FROM setpoint_snapshot WHERE parameter=$1 ORDER BY ts DESC LIMIT 1",
            TEST_PARAM,
        )

    if current is None:
        step("skipping alert path: no cfg readback for test param", False)
        return

    # Deliberately mismatched value — way outside the 1% dead-band so the
    # confirmation UPDATE never fires. ts=6 min ago so the monitor sees it.
    mismatched = float(current) + 999.0
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO setpoint_changes (ts, parameter, value, source) "
            "VALUES (now() - interval '6 minutes', $1, $2, $3)",
            TEST_PARAM,
            mismatched,
            f"{SMOKE_SOURCE}_mismatch",
        )

    # Invoke the monitor directly (same function the task loop runs every 300 s)
    try:
        from tasks import setpoint_confirmation_monitor

        await setpoint_confirmation_monitor(pool)
        step("setpoint_confirmation_monitor ran without exception", True)
    except Exception as e:
        step("setpoint_confirmation_monitor ran without exception", False, str(e))

    # Check alert_log for a matching row — find by the distinctive mismatched
    # value in details JSONB so we don't conflict with any live alerts.
    async with pool.acquire() as conn:
        alert_id = await conn.fetchval(
            "SELECT id FROM alert_log "
            "WHERE alert_type='setpoint_unconfirmed' "
            "  AND sensor_id=$1 "
            "  AND ((details->>'requested_value')::float) = $2 "
            "ORDER BY ts DESC LIMIT 1",
            f"setpoint.{TEST_PARAM}",
            mismatched,
        )

    step(
        "setpoint_unconfirmed alert_log row created for synthetic mismatch",
        alert_id is not None,
        f"alert_id={alert_id}" if alert_id is None else "",
    )

    # Cleanup: remove our synthetic row + the alert it spawned
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM setpoint_changes WHERE source=$1",
            f"{SMOKE_SOURCE}_mismatch",
        )
        if alert_id is not None:
            await conn.execute("DELETE FROM alert_log WHERE id=$1", alert_id)


def test_website_surface() -> None:
    section("Website surface (auto-published)")

    today = datetime.now().strftime("%Y-%m-%d")
    for path, label in [
        ("/plans/", "plans index"),
        (f"/plans/{today}", f"today's plan ({today})"),
        ("/forecast", "forecast page"),
    ]:
        status = curl(f"https://127.0.0.1{path}", "lab.verdify.ai")
        step(f"GET {path} — {label} returns 200", status == 200, f"got {status}")


# ── Main ────────────────────────────────────────────────────────────


async def main() -> int:
    print("Planner feedback loop end-to-end smoke test")
    print("================================")
    start = time.time()

    # Sync-only sections first
    test_schema_rejections()
    test_structured_hypothesis_extraction()
    test_website_surface()

    # DB-dependent sections
    pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=3)
    try:
        await test_db_migration(pool)
        await test_live_confirmation(pool)
        await test_fb1_alert(pool)
    finally:
        await pool.close()

    duration = time.time() - start
    print()
    print(f"Passed: {_state['pass']}  Failed: {_state['fail']}  ({duration:.1f}s)")
    return 1 if _state["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
