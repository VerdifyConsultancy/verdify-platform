"""Real-Postgres proof for the ingestor's exhausted required-cycle fallback."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

INGESTOR = Path(__file__).resolve().parents[1] / "ingestor"
sys.path.insert(0, str(INGESTOR))

from planner_local_fallback import LOCAL_FALLBACK_FAILURE_CLASS, record_required_local_fallback


@pytest.mark.asyncio
async def test_required_failure_fallback_retains_delivery_evidence_and_fences_late_plan(monkeypatch):
    dsn = os.environ.get("PLANNER_FENCE_TEST_DSN")
    if not dsn:
        pytest.skip("PLANNER_FENCE_TEST_DSN is required for real PostgreSQL fallback proof")
    asyncpg = pytest.importorskip("asyncpg")
    schema = f"planner_fallback_{uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(
            f'''
            CREATE TABLE "{schema}".plan_delivery_log (
                id bigint PRIMARY KEY, status text NOT NULL, trigger_id uuid NOT NULL,
                terminal_action text, terminal_at timestamptz, failure_class text,
                delivered_at timestamptz DEFAULT now(), resulting_plan_id text
            );
            CREATE TABLE "{schema}".planner_trigger_ledger (
                id bigint PRIMARY KEY, plan_delivery_log_id bigint, status text NOT NULL,
                expected_action text NOT NULL, due_at timestamptz NOT NULL,
                terminal_action text, terminal_at timestamptz, failure_class text,
                had_required_failure boolean NOT NULL DEFAULT false,
                resolved_at timestamptz, updated_at timestamptz, notes text,
                delivered_at timestamptz, resulting_plan_id text, trigger_id uuid
            );
            '''
        )
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=2, server_settings={"search_path": schema})
        try:
            now = datetime.now(UTC)
            for row_id, status, due_at in (
                (1, "delivery_failed", now + timedelta(hours=1)),
                (2, "pending", now - timedelta(minutes=1)),
                (3, "pending", now + timedelta(hours=1)),
                (4, "plan_written", now - timedelta(minutes=1)),
            ):
                await pool.execute(
                    "INSERT INTO plan_delivery_log (id, status, trigger_id) VALUES ($1, $2, $3)",
                    row_id,
                    status,
                    uuid4(),
                )
                await pool.execute(
                    """INSERT INTO planner_trigger_ledger
                       (id, plan_delivery_log_id, status, expected_action, due_at)
                       VALUES ($1, $1, $2, 'set_plan', $3)""",
                    row_id,
                    "plan_written" if row_id == 4 else "delivered" if status == "pending" else status,
                    due_at,
                )

            assert await record_required_local_fallback(pool, 1) is True
            assert await record_required_local_fallback(pool, 2) is True
            assert await record_required_local_fallback(pool, 3) is False
            assert await record_required_local_fallback(pool, 4) is False
            assert await record_required_local_fallback(pool, 1) is False
            await pool.execute(
                """INSERT INTO planner_trigger_ledger
                   (id, status, expected_action, due_at)
                   VALUES (6, 'missed', 'set_plan', $1),
                          (7, 'expected', 'set_plan', $2)""",
                now - timedelta(minutes=1),
                now + timedelta(hours=1),
            )
            assert await record_required_local_fallback(pool, 6) is True
            assert await record_required_local_fallback(pool, 7) is False

            rows = await pool.fetch(
                """SELECT l.id, l.status AS ledger_status, l.terminal_action,
                          l.terminal_at, l.failure_class, l.had_required_failure,
                          l.notes, d.status AS delivery_status
                     FROM planner_trigger_ledger l JOIN plan_delivery_log d
                       ON d.id = l.plan_delivery_log_id ORDER BY l.id"""
            )
            assert [(r["ledger_status"], r["delivery_status"]) for r in rows] == [
                ("neutral_fallback", "delivery_failed"),
                ("neutral_fallback", "timed_out"),
                ("delivered", "pending"),
                ("plan_written", "plan_written"),
            ]
            for row in rows[:2]:
                assert row["terminal_action"] == "neutral_fallback"
                assert row["terminal_at"] is not None
                assert row["failure_class"] == LOCAL_FALLBACK_FAILURE_CLASS
                assert row["had_required_failure"] is True
                assert "no plan or device write" in row["notes"]
            monkeypatch.setenv("DB_USER", "local-test")
            monkeypatch.setenv("DB_PASSWORD", "local-test")
            from tasks.heartbeat import _sync_planner_trigger_ledger

            async with pool.acquire() as conn:
                await _sync_planner_trigger_ledger(conn)
            assert await pool.fetchval("SELECT status FROM planner_trigger_ledger WHERE id = 1") == "neutral_fallback"
            assert await pool.fetchval("SELECT status FROM planner_trigger_ledger WHERE id = 2") == "neutral_fallback"
            assert await pool.fetchval("SELECT status FROM planner_trigger_ledger WHERE id = 6") == "neutral_fallback"
            # MCP's writable-attempt predicate is now false for both fallback
            # cycles, including the formerly pending late Hermes run.
            assert (
                await pool.fetchval("SELECT count(*) FROM plan_delivery_log WHERE id IN (1, 2) AND status = 'pending'")
                == 0
            )

            # If a valid MCP plan commits first, the fallback must wait for
            # the same delivery lock and then leave the successful cycle alone.
            await pool.execute(
                "INSERT INTO plan_delivery_log (id, status, trigger_id) VALUES (5, 'pending', $1)",
                uuid4(),
            )
            await pool.execute(
                """INSERT INTO planner_trigger_ledger
                   (id, plan_delivery_log_id, status, expected_action, due_at)
                   VALUES (5, 5, 'delivered', 'set_plan', $1)""",
                now - timedelta(minutes=1),
            )
            winner = await asyncpg.connect(dsn, server_settings={"search_path": schema})
            try:
                async with winner.transaction():
                    await winner.fetchrow("SELECT id FROM plan_delivery_log WHERE id = 5 FOR UPDATE")
                    blocked_fallback = asyncio.create_task(record_required_local_fallback(pool, 5))
                    await asyncio.sleep(0.1)
                    assert blocked_fallback.done() is False
                    await winner.execute("UPDATE plan_delivery_log SET status = 'plan_written' WHERE id = 5")
                    await winner.execute("UPDATE planner_trigger_ledger SET status = 'plan_written' WHERE id = 5")
                assert await blocked_fallback is False
                assert await pool.fetchval("SELECT status FROM planner_trigger_ledger WHERE id = 5") == "plan_written"
            finally:
                await winner.close()
        finally:
            await pool.close()
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()
