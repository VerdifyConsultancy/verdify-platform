"""tasks.experiment_assignments — controlled-experiment assignment scheduler
(#584 Lane C).

Runs every 60s from task_loop. Feature-off (VERDIFY_POLICY_VECTOR_MODE=off or
no VERDIFY_ACTIVE_EXPERIMENT_ID) it returns immediately WITHOUT touching the
database — the hard acceptance bar is that default env is byte-identical to
current prod.

When live, each cycle:
  1. validates the locked assignment schedule exists for the active
     experiment (a missing schedule is a protocol deviation, not a crash);
  2. closes assignments whose UTC upper boundary has passed — open exposures
     close first via fn_close_exposure(reason='boundary'), then the
     assignment row transitions to 'closed' (the migration-207 immutability
     trigger permits exactly status/updated_at);
  3. confirms an active assignment covers now() (a gap is a deviation event)
     and arms/releases the esp32_push experiment policy hold accordingly —
     the hold is armed ONLY in live mode, so shadow mode never changes what
     the device receives;
  4. pre-stages the next boundary's activation intent as an append-only
     experiment_events note so the arbiter/delivery pipeline (and the ops
     board) see the upcoming transition ahead of the boundary.

Deviations are emitted as experiment_events rows, deduplicated over a
6-hour window so a persistent condition cannot flood the append-only table.
"""

import esp32_push

from verdify_schemas.experiment_config import (
    POLICY_VECTOR_MODE_LIVE,
    POLICY_VECTOR_MODE_OFF,
    active_experiment_id,
    policy_vector_mode,
)
from verdify_schemas.policy_vector import wire_fields

from ._common import asyncpg, json, log

# The 49 experiment-owned parameters (the canonical wire surface): while an
# assignment is armed in live mode, legacy per-parameter pushes for these are
# rejected at the esp32_push chokepoint.
EXPERIMENT_OWNED_PARAMS: frozenset[str] = frozenset(defn.name for defn in wire_fields())

_EVENT_DEDUP_WINDOW = "6 hours"
_PRESTAGE_LOOKAHEAD = "30 minutes"
_scheduler_logged: set[str] = set()


def _log_once(key: str, message: str, *args) -> None:
    if key not in _scheduler_logged:
        _scheduler_logged.add(key)
        log.warning(message, *args)


async def _emit_event_once(
    conn: asyncpg.Connection,
    experiment_id: str,
    *,
    event_kind: str,
    lane_c_kind: str,
    assignment_id: str | None = None,
    severity: str = "warning",
    detail: dict | None = None,
) -> bool:
    """Append one experiment_events row unless an identical one is recent."""
    existing = await conn.fetchval(
        f"""
        SELECT 1 FROM experiment_events
         WHERE experiment_id = $1::uuid
           AND event_kind = $2
           AND detail->>'lane_c_kind' = $3
           AND assignment_id IS NOT DISTINCT FROM $4::uuid
           AND recorded_at > now() - interval '{_EVENT_DEDUP_WINDOW}'
         LIMIT 1
        """,
        experiment_id,
        event_kind,
        lane_c_kind,
        assignment_id,
    )
    if existing:
        return False
    payload = {"lane_c_kind": lane_c_kind, **(detail or {})}
    await conn.execute(
        """
        INSERT INTO experiment_events
            (experiment_id, assignment_id, event_kind, severity, actor, detail)
        VALUES ($1::uuid, $2::uuid, $3, $4, 'experiment_assignments', $5::jsonb)
        """,
        experiment_id,
        assignment_id,
        event_kind,
        severity,
        json.dumps(payload, sort_keys=True, default=str),
    )
    return True


async def _close_overdue_assignments(conn: asyncpg.Connection, experiment_id: str) -> int:
    """Close every active assignment whose UTC upper boundary has passed."""
    overdue = await conn.fetch(
        """
        SELECT assignment_id::text AS assignment_id, upper(valid_range) AS boundary
          FROM control_assignments
         WHERE experiment_id = $1::uuid
           AND status = 'active'
           AND upper(valid_range) <= now()
         ORDER BY upper(valid_range)
        """,
        experiment_id,
    )
    closed = 0
    for row in overdue:
        assignment_id = row["assignment_id"]
        open_exposures = await conn.fetch(
            "SELECT exposure_id FROM policy_exposures WHERE assignment_id = $1::uuid AND ended_at IS NULL",
            assignment_id,
        )
        for exposure in open_exposures:
            await conn.fetchval(
                "SELECT fn_close_exposure($1::uuid, 'boundary', NULL, $2)",
                exposure["exposure_id"],
                row["boundary"],
            )
        await conn.execute(
            "UPDATE control_assignments SET status = 'closed', updated_at = now() WHERE assignment_id = $1::uuid",
            assignment_id,
        )
        await _emit_event_once(
            conn,
            experiment_id,
            event_kind="state_transition",
            lane_c_kind="assignment_closed_at_boundary",
            assignment_id=assignment_id,
            severity="info",
            detail={"boundary": row["boundary"], "exposures_closed": len(open_exposures)},
        )
        closed += 1
    return closed


async def experiment_assignment_scheduler(pool: asyncpg.Pool) -> None:
    """Drive assignment boundaries for the active controlled experiment."""
    mode = policy_vector_mode()
    experiment_id = active_experiment_id()
    if mode == POLICY_VECTOR_MODE_OFF or not experiment_id:
        # Feature-off: inert. No DB access; release a stale in-memory hold so
        # a mode flip back to off can never strand legacy pushes rejected.
        if esp32_push.experiment_policy_hold()[0]:
            esp32_push.set_experiment_policy_hold(False)
        return

    async with pool.acquire() as conn:
        exp = await conn.fetchrow(
            "SELECT experiment_id::text AS experiment_id, status, greenhouse_id, kind "
            "FROM control_experiments WHERE experiment_id = $1::uuid",
            experiment_id,
        )
        if exp is None:
            _log_once(
                f"unknown:{experiment_id}",
                "experiment_assignments: VERDIFY_ACTIVE_EXPERIMENT_ID=%s not found — worker idle",
                experiment_id,
            )
            esp32_push.set_experiment_policy_hold(False)
            return
        if exp["status"] not in ("armed", "running"):
            _log_once(
                f"status:{experiment_id}:{exp['status']}",
                "experiment_assignments: experiment %s is %s — worker idle until armed/running",
                experiment_id,
                exp["status"],
            )
            esp32_push.set_experiment_policy_hold(False)
            return

        # 1. Locked schedule must exist: an armed experiment with zero
        # assignment rows means the precommitted schedule never materialized.
        total_assignments = await conn.fetchval(
            "SELECT count(*) FROM control_assignments WHERE experiment_id = $1::uuid",
            experiment_id,
        )
        if not total_assignments:
            await _emit_event_once(
                conn,
                experiment_id,
                event_kind="protocol_deviation",
                lane_c_kind="schedule_missing",
                severity="critical",
                detail={"status": exp["status"]},
            )
            esp32_push.set_experiment_policy_hold(False)
            return

        # 2. UTC boundary close-out.
        closed = await _close_overdue_assignments(conn, experiment_id)
        if closed:
            log.info("experiment_assignments: closed %d assignment(s) at their UTC boundary", closed)

        # 3. Current assignment coverage + the legacy-push hold.
        current = await conn.fetchrow(
            """
            SELECT assignment_id::text AS assignment_id, arm_label, operation_kind,
                   upper(valid_range) AS boundary
              FROM control_assignments
             WHERE experiment_id = $1::uuid
               AND status = 'active'
               AND now() <@ valid_range
             ORDER BY lower(valid_range) DESC
             LIMIT 1
            """,
            experiment_id,
        )
        if current is None:
            if exp["status"] == "running":
                await _emit_event_once(
                    conn,
                    experiment_id,
                    event_kind="protocol_deviation",
                    lane_c_kind="assignment_gap",
                    severity="warning",
                    detail={"closed_this_cycle": closed},
                )
            esp32_push.set_experiment_policy_hold(False)
        else:
            # The hold changes device-visible behavior, so it arms ONLY in
            # live mode; shadow keeps legacy delivery byte-identical.
            esp32_push.set_experiment_policy_hold(
                mode == POLICY_VECTOR_MODE_LIVE,
                EXPERIMENT_OWNED_PARAMS,
            )

        # 4. Pre-stage the next boundary's activation intent.
        upcoming = await conn.fetchrow(
            f"""
            SELECT assignment_id::text AS assignment_id, lower(valid_range) AS boundary
              FROM control_assignments
             WHERE experiment_id = $1::uuid
               AND status = 'active'
               AND lower(valid_range) > now()
               AND lower(valid_range) <= now() + interval '{_PRESTAGE_LOOKAHEAD}'
             ORDER BY lower(valid_range)
             LIMIT 1
            """,
            experiment_id,
        )
        if upcoming is not None:
            emitted = await _emit_event_once(
                conn,
                experiment_id,
                event_kind="note",
                lane_c_kind="boundary_activation_intent",
                assignment_id=upcoming["assignment_id"],
                severity="info",
                detail={"boundary": upcoming["boundary"]},
            )
            if emitted:
                log.info(
                    "experiment_assignments: pre-staged activation intent for assignment %s at %s",
                    upcoming["assignment_id"],
                    upcoming["boundary"],
                )
