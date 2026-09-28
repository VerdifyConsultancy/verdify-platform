"""Fence an exhausted required Iris cycle without inventing a plan or delivery."""

from __future__ import annotations

import asyncpg

LOCAL_FALLBACK_FAILURE_CLASS = "ingestor_local_fallback_after_required_failure"


async def record_required_local_fallback(pool: asyncpg.Pool, ledger_id: int) -> bool:
    """Record a safe, explicitly failed outcome after the caller's retry budget.

    The last delivery retains its actual failure or timeout. Lock its row first,
    matching MCP's delivery-then-ledger lock order, so a concurrent set_plan
    either wins before this fallback or finds a terminal delivery afterward.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            delivery_id = await conn.fetchval(
                "SELECT plan_delivery_log_id FROM planner_trigger_ledger WHERE id = $1",
                ledger_id,
            )
            delivery = None
            if delivery_id is not None:
                delivery = await conn.fetchrow(
                    "SELECT id, status FROM plan_delivery_log WHERE id = $1 FOR UPDATE",
                    delivery_id,
                )
            ledger = await conn.fetchrow(
                """
                SELECT id, plan_delivery_log_id, status, expected_action,
                       due_at <= now() AS overdue
                  FROM planner_trigger_ledger
                 WHERE id = $1
                 FOR UPDATE
                """,
                ledger_id,
            )
            if (
                ledger is None
                or ledger["plan_delivery_log_id"] != delivery_id
                or ledger["expected_action"] != "set_plan"
                or ledger["status"] in {"plan_written", "neutral_fallback"}
                or (delivery_id is not None and delivery is None)
                or (delivery is not None and delivery["status"] in {"plan_written", "neutral_fallback"})
            ):
                return False

            # A submitted Hermes run retains its full SLA even when the retry
            # counter has reached its cap. Once overdue, close the delivery
            # before publishing the local fallback, fencing any late MCP call.
            if delivery is not None and delivery["status"] == "pending":
                if not ledger["overdue"]:
                    return False
                await conn.execute(
                    """
                    UPDATE plan_delivery_log
                       SET status = 'timed_out', terminal_action = 'timeout',
                           terminal_at = now(), failure_class = 'planner_trigger_sla_timeout'
                     WHERE id = $1 AND status = 'pending'
                    """,
                    delivery_id,
                )
            elif ledger["status"] in {"expected", "delivered"}:
                if not ledger["overdue"]:
                    return False

            await conn.execute(
                """
                UPDATE planner_trigger_ledger
                   SET status = 'neutral_fallback',
                       terminal_action = 'neutral_fallback', terminal_at = now(),
                       failure_class = $2, had_required_failure = true,
                       resolved_at = now(), updated_at = now(),
                       notes = concat_ws(E'\n', NULLIF(notes, ''),
                           'ingestor recorded neutral fallback after exhausted required-cycle retries; no plan or device write')
                 WHERE id = $1
                """,
                ledger_id,
                LOCAL_FALLBACK_FAILURE_CLASS,
            )
            return True
