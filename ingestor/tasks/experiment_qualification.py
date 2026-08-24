"""tasks.experiment_qualification — §8.3 step-test qualification scheduler
(#584/#588, epic #581).

Runs every 60s from task_loop. Feature-off (VERDIFY_POLICY_VECTOR_MODE=off or
no VERDIFY_ACTIVE_EXPERIMENT_ID) it returns immediately WITHOUT touching the
database — the hard acceptance bar is that default env is byte-identical to
current prod. It also idles unless the active experiment is
kind='qualification' and armed/running.

The worker drives the audit §8.3 protocol
(docs/research/planner-efficacy-current-firmware-2026-08-14.md) against the
migration 207/208/212 schema:

  1. mid-assignment, it watches a running analyzed step for failure evidence
     (safety/override event, delivery failure) and marks the claimed slot
     'failed' immediately — a failed cell result is NEVER replaced;
  2. at an assignment boundary it first resolves the just-finished analyzed
     step (completed/failed from exposure identity + post-step snapshot
     continuity), then either
       a. atomically claims the next FIFO cell slot when every §8.3
          eligibility predicate passes — fresh inputs, no manual/safety
          override, gap-free 60-minute source-content pretrace evaluated by
          CONTENT/template hash (never the assignment-bound activation hash),
          regime match on current conditions — via
          fn_claim_qualification_slot (migration 212: FIFO + frozen_strata
          validated server-side, immutable analyzed assignment committed
          before actuation), then enqueues the transition through the sole
          arbiter path (fn_submit_policy_proposal); or
       b. creates the next locked 15-minute same-content identity_hold
          assignment (deterministic cadence anchored at the previous
          boundary); or
       c. creates a positioning move (fixed 3h: 2h settle + 60-min pretrace)
          to the next needed source vector — only when the current content
          has no open cell work left; or
       d. after the 45-local-day window or full resolution, returns to
          baseline via a baseline_recovery move and idles.

  Every move lands in control_transition_ledger (fn_create_assignment /
  fn_resolve_qualification_slot write their own rows; declined boundary
  claims are recorded via fn_record_qualification_event('skipped')). The
  worker stops starting analyzed transitions when a cell reaches four
  resolved slots — structurally guaranteed by the fixed 24x4 slot inventory
  plus the server-side FIFO gate.

Scheduling decisions are pure functions over plain rows so the protocol state
machine is testable without a database (tests/test_experiment_qualification_
scheduler.py).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from verdify_schemas.experiment_config import (
    POLICY_VECTOR_MODE_LIVE,
    POLICY_VECTOR_MODE_OFF,
    active_experiment_id,
    policy_device_id,
    policy_vector_mode,
)
from verdify_schemas.experiment_regimes import (
    DEFAULT_STATION_PRESSURE_KPA,
    REGIMES,
    Regime,
    maybe_classify_regime,
    regime_for_cell,
)

from ._common import asyncpg, json, log

# ---- Frozen protocol parameters -------------------------------------------
# These values are the worker half of the qualification specification; the
# spec template (research/planner-efficacy/qualification/
# qualification-spec-v1.template.yaml) encodes the same numbers and a CI test
# asserts they agree. Changing any of them is a new spec version (§8.3: any
# revision change voids the qualification).
QUALIFICATION_SPEC_VERSION = "qualification-spec-v1"
POSITIONING_HOURS = 3  # fixed: 2h settle + the 60-min pretrace (§8.3)
RECOVERY_HOURS = 3  # baseline_recovery uses the positioning shape
ANALYZED_HOURS = 6  # six post-step hours (§8.3)
IDENTITY_HOLD_MINUTES = 15  # locked hold cadence (§8.3)
PRETRACE_MINUTES = 60  # gap-free source-content pretrace (§8.3)
PRETRACE_MAX_GAP_S = 180  # max gap between device snapshot echoes
INPUT_FRESHNESS_S = 300  # 'fresh inputs' predicate: newest climate row age
OVERRIDE_LOOKBACK_MINUTES = 60  # no manual/safety override within the pretrace
POST_STEP_MAX_GAP_S = 900  # post-step snapshot continuity for analyzability
DELIVERY_CONFIRM_GRACE_S = 900  # analyzed step must confirm identity within this
BOUNDARY_BACKDATE_GRACE_S = 120  # identity_hold may start at the exact boundary
WINDOW_LOCAL_DAYS = 45  # qualification window (§8.3)
SITE_PRESSURE_FALLBACK_HPA = DEFAULT_STATION_PRESSURE_KPA * 10.0

PRODUCER = "qualification_scheduler"
SCHEDULER_REF = "experiment_qualification/v1"
_ACTOR = "experiment_qualification"

_scheduler_logged: set[str] = set()

_TERMINAL_SLOT_STATES = ("completed", "failed", "skipped")


def _log_once(key: str, message: str, *args) -> None:
    if key not in _scheduler_logged:
        _scheduler_logged.add(key)
        log.warning(message, *args)


# ============================================================================
# Pure protocol state machine (no I/O)
# ============================================================================


def fifo_next_slots(slots: list[dict]) -> list[dict]:
    """The claimable head of each cell queue, FIFO within the cell.

    A cell's next slot is its lowest-ordinal 'open' slot, and only when no
    lower-ordinal slot is still open/claimed (mirrors the server-side gate in
    fn_claim_qualification_slot). Cells where all four slots are resolved
    contribute nothing — the stop-at-4 rule is structural.
    """
    by_cell: dict[int, list[dict]] = {}
    for slot in slots:
        by_cell.setdefault(int(slot["cell_index"]), []).append(slot)
    heads: list[dict] = []
    for _cell, cell_slots in sorted(by_cell.items()):
        cell_slots.sort(key=lambda s: int(s["slot_ordinal"]))
        blocked = False
        for slot in cell_slots:
            if slot["status"] == "claimed":
                blocked = True
                break
            if slot["status"] == "open":
                if not blocked:
                    heads.append(slot)
                break
            # completed/failed/skipped: resolved — never replaced, keep going.
    return heads


def eligible_claim_candidate(
    slots: list[dict],
    current_kind: str | None,
    regime: Regime | None,
) -> dict | None:
    """Deterministic claim choice: lowest cell_index FIFO head whose edge
    source is the current content and whose cell regime matches now."""
    if current_kind is None or regime is None:
        return None
    for slot in fifo_next_slots(slots):
        if slot["from_kind"] != current_kind:
            continue
        if regime_for_cell(int(slot["cell_index"])) is regime:
            return slot
    return None


def open_work_for_source(slots: list[dict], kind: str | None) -> bool:
    """Whether any FIFO head still needs `kind` as its source vector."""
    if kind is None:
        return False
    return any(slot["from_kind"] == kind for slot in fifo_next_slots(slots))


def positioning_target(slots: list[dict], current_kind: str | None) -> str | None:
    """Source vector of the lowest-cell-index FIFO head needing repositioning.

    Deterministic rule R4 (spec): reposition only when the current content has
    no open cell work at all; the target source is taken from the lowest
    cell_index still-open head.
    """
    heads = fifo_next_slots(slots)
    if not heads:
        return None
    if open_work_for_source(slots, current_kind):
        return None
    return heads[0]["from_kind"]


def all_slots_resolved(slots: list[dict]) -> bool:
    return bool(slots) and all(s["status"] in _TERMINAL_SLOT_STATES for s in slots)


def pretrace_evaluate(
    snapshots: list[dict],
    source_content_sha256: str | None,
    window_start: datetime,
    boundary: datetime,
    max_gap_s: float = PRETRACE_MAX_GAP_S,
) -> dict:
    """§8.3 gap-free 60-minute source-content pretrace.

    Continuity is judged by the device-echoed CONTENT hash (identity holds
    change the activation hash by design — content/template continuity is
    what must hold), over policy_device_snapshots rows covering
    [window_start, boundary]. Any gap over `max_gap_s`, a wrong hash, or an
    empty trace fails (and, per §8.3, restarts the pretrace clock).
    """
    result = {
        "ok": False,
        "reason": None,
        "snapshots": len(snapshots),
        "max_gap_s": None,
        "window_start": window_start.isoformat(),
        "boundary": boundary.isoformat(),
    }
    if not source_content_sha256:
        result["reason"] = "unknown_source_content"
        return result
    if not snapshots:
        result["reason"] = "no_snapshots_in_window"
        return result
    rows = sorted(snapshots, key=lambda r: r["reported_at"])
    for row in rows:
        if row["content_sha256"] != source_content_sha256:
            result["reason"] = "content_hash_mismatch"
            return result
    max_gap = (rows[0]["reported_at"] - window_start).total_seconds()
    for prev_row, next_row in zip(rows, rows[1:], strict=False):
        gap = (next_row["reported_at"] - prev_row["reported_at"]).total_seconds()
        max_gap = max(max_gap, gap)
    max_gap = max(max_gap, (boundary - rows[-1]["reported_at"]).total_seconds())
    result["max_gap_s"] = round(max_gap, 3)
    if max_gap > max_gap_s:
        result["reason"] = "snapshot_gap"
        return result
    result["ok"] = True
    return result


def window_cutoff(started_at: datetime, timezone: str, days: int = WINDOW_LOCAL_DAYS) -> datetime:
    """End of the qualification window: `days` local days after the local
    midnight preceding the experiment start (§8.3: a 45-local-day window)."""
    tz = ZoneInfo(timezone)
    local_start = started_at.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return (local_start + timedelta(days=days)).astimezone(ZoneInfo("UTC"))


def regime_code(regime: Regime) -> int:
    return REGIMES.index(regime)


# ============================================================================
# DB glue
# ============================================================================


async def _fetch_conditions(conn, now: datetime) -> tuple[dict | None, Regime | None, bool]:
    """Latest outdoor conditions + classified regime + freshness predicate."""
    row = await conn.fetchrow(
        """
        SELECT ts, outdoor_temp_f, outdoor_rh_pct,
               COALESCE(solar_irradiance_w_m2, 0) AS solar_w_m2,
               COALESCE(pressure_hpa, $1) / 10.0 AS pressure_kpa
          FROM climate
         WHERE outdoor_temp_f IS NOT NULL
           AND outdoor_rh_pct IS NOT NULL
         ORDER BY ts DESC
         LIMIT 1
        """,
        SITE_PRESSURE_FALLBACK_HPA,
    )
    if row is None:
        return None, None, False
    fresh = (now - row["ts"]).total_seconds() <= INPUT_FRESHNESS_S
    regime = maybe_classify_regime(row["solar_w_m2"], row["outdoor_temp_f"], row["outdoor_rh_pct"], row["pressure_kpa"])
    conditions = {
        "ts": row["ts"].isoformat(),
        "outdoor_temp_f": float(row["outdoor_temp_f"]),
        "outdoor_rh_pct": float(row["outdoor_rh_pct"]),
        "solar_w_m2": float(row["solar_w_m2"]),
        "pressure_kpa": float(row["pressure_kpa"]),
    }
    return conditions, regime, fresh


async def _fetch_slots(conn, experiment_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT s.slot_id::text AS slot_id, s.cell_index, s.slot_ordinal, s.status,
               s.edge_id::text AS edge_id,
               e.from_template_id::text AS from_template_id,
               e.to_template_id::text AS to_template_id,
               ft.kind AS from_kind, tt.kind AS to_kind,
               ft.content_sha256 AS from_content_sha256,
               tt.content_sha256 AS to_content_sha256
          FROM qualification_transition_slots s
          LEFT JOIN policy_template_edges e ON e.edge_id = s.edge_id
          LEFT JOIN policy_templates ft ON ft.template_id = e.from_template_id
          LEFT JOIN policy_templates tt ON tt.template_id = e.to_template_id
         WHERE s.experiment_id = $1::uuid
         ORDER BY s.cell_index, s.slot_ordinal
        """,
        experiment_id,
    )
    return [dict(row) for row in rows]


async def _fetch_templates(conn, experiment_id: str) -> dict[str, dict]:
    rows = await conn.fetch(
        "SELECT template_id::text AS template_id, kind, content_sha256 "
        "FROM policy_templates WHERE experiment_id = $1::uuid",
        experiment_id,
    )
    return {row["kind"]: dict(row) for row in rows}


async def _current_content_kind(conn, device_id: str, templates: dict[str, dict]) -> tuple[str | None, str | None]:
    """Map the latest device-echoed content hash onto a locked template kind."""
    row = await conn.fetchrow(
        "SELECT content_sha256, reported_at FROM policy_device_snapshots "
        "WHERE device_id = $1 ORDER BY reported_at DESC LIMIT 1",
        device_id,
    )
    if row is None or not row["content_sha256"]:
        return None, None
    for kind, template in templates.items():
        if template["content_sha256"] == row["content_sha256"]:
            return kind, row["content_sha256"]
    return None, row["content_sha256"]


async def _override_count(conn, experiment_id: str, window_start: datetime) -> int:
    return (
        await conn.fetchval(
            """
            SELECT count(*) FROM experiment_events
             WHERE experiment_id = $1::uuid
               AND recorded_at >= $2
               AND (event_kind IN ('override', 'emergency_action') OR severity = 'critical')
            """,
            experiment_id,
            window_start,
        )
        or 0
    )


async def _pretrace_snapshots(conn, device_id: str, window_start: datetime) -> list[dict]:
    rows = await conn.fetch(
        "SELECT reported_at, content_sha256 FROM policy_device_snapshots "
        "WHERE device_id = $1 AND reported_at >= $2 ORDER BY reported_at",
        device_id,
        window_start,
    )
    return [dict(row) for row in rows]


async def _create_move(
    conn,
    exp: dict,
    *,
    operation_kind: str,
    arm_label: str,
    start: datetime,
    end: datetime,
    reason: str,
    source_template_id: str,
    target_template_id: str,
    regime_code_value: int,
) -> str:
    """One non-analysis move: immutable assignment (ledgered by
    fn_create_assignment) + the arbiter-path proposal for its content."""
    strata = {
        "source_template_id": source_template_id,
        "target_template_id": target_template_id,
        "regime": regime_code_value,
    }
    assignment_id = await conn.fetchval(
        """
        SELECT public.fn_runtime_v1_create_assignment(
            $1::uuid, $2, $3, $4,
            tstzrange($5::timestamptz, $6::timestamptz, '[)'),
            NULL, NULL, NULL, $7, $8, $9, $10, $11::jsonb, $12)
        """,
        exp["experiment_id"],
        exp["greenhouse_id"],
        arm_label,
        operation_kind,
        start,
        end,
        "experiment_qualification",
        QUALIFICATION_SPEC_VERSION,
        SCHEDULER_REF,
        reason,
        json.dumps(strata, sort_keys=True),
        _ACTOR,
    )
    await _submit_proposal(
        conn,
        exp,
        assignment_id=str(assignment_id),
        template_id=target_template_id,
        trigger_ref=f"qualification:{operation_kind}:{start.isoformat()}",
        start=start,
        end=end,
    )
    log.info(
        "experiment_qualification: created %s assignment %s [%s, %s) -> %s",
        operation_kind,
        assignment_id,
        start.isoformat(),
        end.isoformat(),
        arm_label,
    )
    return str(assignment_id)


async def _submit_proposal(
    conn,
    exp: dict,
    *,
    assignment_id: str,
    template_id: str,
    trigger_ref: str,
    start: datetime,
    end: datetime,
) -> None:
    await conn.fetchval(
        """
        SELECT public.fn_runtime_v1_submit_policy_proposal(
            $1, $2, $3::uuid, NULL::jsonb, NULL, NULL::jsonb, 'proposed', $4,
            $5::uuid, $6::uuid,
            tstzrange($7::timestamptz, $8::timestamptz, '[)'), NULL, NULL)
        """,
        PRODUCER,
        trigger_ref,
        template_id,
        _ACTOR,
        exp["experiment_id"],
        assignment_id,
        start,
        end,
    )


async def _record_skip(conn, exp: dict, *, slot_id: str | None, detail: dict) -> None:
    await conn.fetchval(
        "SELECT public.fn_runtime_v1_record_qualification_event("
        "$1::uuid, 'skipped', $2::jsonb, $3::uuid, NULL::uuid, $4)",
        exp["experiment_id"],
        json.dumps(detail, sort_keys=True, default=str),
        slot_id,
        _ACTOR,
    )


async def _resolve_slot(conn, slot_id: str, outcome: str, detail: dict) -> None:
    await conn.fetchval(
        "SELECT public.fn_runtime_v1_resolve_qualification_slot($1::uuid, $2, $3::jsonb, $4)",
        slot_id,
        outcome,
        json.dumps(detail, sort_keys=True, default=str),
        _ACTOR,
    )
    log.info("experiment_qualification: slot %s resolved %s", slot_id, outcome)


# ============================================================================
# Analyzed-step supervision
# ============================================================================


async def _analyzed_failure_evidence(conn, exp: dict, assignment: dict, now: datetime) -> dict | None:
    """Failure evidence for a started analyzed step (§8.3: safety event,
    delivery/readback failure). Returns a detail dict when failed."""
    start = assignment["valid_from"]
    overrides = await _override_count(conn, exp["experiment_id"], start)
    if overrides:
        return {"failure": "safety_or_override_event", "events": int(overrides)}
    exposure = await conn.fetchrow(
        """
        SELECT exposure_id::text AS exposure_id, started_at, ended_at,
               close_reason, identity_confirmed
          FROM policy_exposures
         WHERE assignment_id = $1::uuid
         ORDER BY started_at
         LIMIT 1
        """,
        assignment["assignment_id"],
    )
    if exposure is None:
        if (now - start).total_seconds() > DELIVERY_CONFIRM_GRACE_S:
            return {"failure": "delivery_failure", "detail": "no exposure opened"}
        return None
    if not exposure["identity_confirmed"]:
        return {"failure": "delivery_failure", "detail": "exposure identity unconfirmed"}
    if exposure["close_reason"] in ("fallback", "device_lost", "protocol_deviation", "manual"):
        return {"failure": "delivery_failure", "detail": f"exposure closed: {exposure['close_reason']}"}
    return None


async def _monitor_analyzed(conn, exp: dict, current: dict, now: datetime) -> None:
    if not current["slot_id"]:
        return
    status = await conn.fetchval(
        "SELECT status FROM qualification_transition_slots WHERE slot_id = $1::uuid",
        current["slot_id"],
    )
    if status != "claimed":
        return
    evidence = await _analyzed_failure_evidence(conn, exp, current, now)
    if evidence is not None:
        evidence["phase"] = "mid_assignment"
        await _resolve_slot(conn, current["slot_id"], "failed", evidence)


async def _resolve_finished_analyzed(conn, exp: dict, prev: dict, now: datetime) -> None:
    """Boundary resolution of a finished analyzed step (completed vs failed)."""
    status = await conn.fetchval(
        "SELECT status FROM qualification_transition_slots WHERE slot_id = $1::uuid",
        prev["slot_id"],
    )
    if status != "claimed":
        return
    evidence = await _analyzed_failure_evidence(conn, exp, prev, now)
    if evidence is not None:
        evidence["phase"] = "boundary"
        await _resolve_slot(conn, prev["slot_id"], "failed", evidence)
        return
    # Post-step data completeness: gap-free device echoes of the TARGET
    # content through the six post-step hours (missing post-step data is a
    # failed cell result). The analyzed assignment's arm_label is the target
    # template kind under test.
    device_id = policy_device_id(exp["greenhouse_id"])
    templates = await _fetch_templates(conn, exp["experiment_id"])
    target = templates.get(prev["arm_label"])
    snapshots = await _pretrace_snapshots(conn, device_id, prev["valid_from"])
    in_range = [row for row in snapshots if row["reported_at"] < prev["valid_to"]]
    trace = pretrace_evaluate(
        in_range,
        target["content_sha256"] if target else None,
        prev["valid_from"],
        prev["valid_to"],
        max_gap_s=POST_STEP_MAX_GAP_S,
    )
    if not trace["ok"]:
        await _resolve_slot(
            conn,
            prev["slot_id"],
            "failed",
            {"phase": "boundary", "failure": "missing_post_step_data", "trace": trace},
        )
        return
    await _resolve_slot(conn, prev["slot_id"], "completed", {"phase": "boundary", "trace": trace})


# ============================================================================
# Boundary decision
# ============================================================================


async def _boundary_action(conn, exp: dict, prev: dict | None, now: datetime) -> None:
    experiment_id = exp["experiment_id"]
    device_id = policy_device_id(exp["greenhouse_id"])
    templates = await _fetch_templates(conn, experiment_id)
    if len(templates) < 3:
        _log_once(
            f"templates:{experiment_id}",
            "experiment_qualification: experiment %s lacks the 3 locked templates — idle",
            experiment_id,
        )
        return
    slots = await _fetch_slots(conn, experiment_id)
    if not slots:
        _log_once(
            f"slots:{experiment_id}",
            "experiment_qualification: experiment %s has no qualification slots — idle",
            experiment_id,
        )
        return

    current_kind, current_sha = await _current_content_kind(conn, device_id, templates)
    baseline = templates["baseline"]

    # Boundary anchor: the deterministic cadence chains exactly at the
    # previous UTC boundary; a worker pause beyond the grace records the gap
    # and re-anchors at now (never a retroactive interval, §8.3).
    if prev is None:
        start = now
    else:
        boundary = prev["valid_to"]
        lag = (now - boundary).total_seconds()
        if lag <= BOUNDARY_BACKDATE_GRACE_S:
            start = boundary
        else:
            start = now
            await _record_skip(
                conn,
                exp,
                slot_id=None,
                detail={
                    "reason": "boundary_gap",
                    "boundary": boundary.isoformat(),
                    "observed_lag_s": round(lag, 1),
                },
            )

    # Window cutoff / completion: recover to baseline, then idle.
    done = all_slots_resolved(slots)
    cutoff = None
    if exp["started_at"] is not None:
        cutoff = window_cutoff(exp["started_at"], exp["timezone"])
    if done or (cutoff is not None and now >= cutoff):
        if current_kind is not None and current_kind != "baseline":
            source = templates[current_kind]
            await _create_move(
                conn,
                exp,
                operation_kind="baseline_recovery",
                arm_label="baseline",
                start=start,
                end=start + timedelta(hours=RECOVERY_HOURS),
                reason="qualification_complete" if done else "window_cutoff",
                source_template_id=source["template_id"],
                target_template_id=baseline["template_id"],
                regime_code_value=_carry_regime(prev),
            )
        else:
            _log_once(
                f"done:{experiment_id}:{done}",
                "experiment_qualification: experiment %s %s — no further moves",
                experiment_id,
                "fully resolved" if done else "past the 45-local-day window",
            )
        return

    conditions, regime, fresh = await _fetch_conditions(conn, now)

    # 1. Try an analyzed claim (running experiments only; §8.3 predicates).
    candidate = eligible_claim_candidate(slots, current_kind, regime)
    if candidate is not None and exp["status"] == "running":
        window_start = start - timedelta(minutes=PRETRACE_MINUTES)
        snapshots = await _pretrace_snapshots(conn, device_id, window_start)
        trace = pretrace_evaluate(snapshots, current_sha, window_start, start)
        overrides = await _override_count(conn, experiment_id, start - timedelta(minutes=OVERRIDE_LOOKBACK_MINUTES))
        predicates = {
            "inputs_fresh": bool(fresh),
            "no_override": overrides == 0,
            "pretrace_gap_free": bool(trace["ok"]),
            "regime_match": True,  # candidate selection already required it
        }
        if all(predicates.values()):
            snapshot = {
                "spec": QUALIFICATION_SPEC_VERSION,
                "cell_index": candidate["cell_index"],
                "slot_ordinal": candidate["slot_ordinal"],
                "regime": regime.value,
                "regime_code": regime_code(regime),
                "conditions": conditions,
                "pretrace": trace,
                "predicates": predicates,
                "boundary": start.isoformat(),
            }
            end = start + timedelta(hours=ANALYZED_HOURS)
            strata = {
                "source_template_id": candidate["from_template_id"],
                "target_template_id": candidate["to_template_id"],
                "regime": regime_code(regime),
            }
            assignment_id = await conn.fetchval(
                """
                SELECT public.fn_runtime_v1_claim_qualification_slot(
                    $1::uuid, $2::jsonb,
                    tstzrange($3::timestamptz, $4::timestamptz, '[)'),
                    $5, $6::jsonb, $7)
                """,
                candidate["slot_id"],
                json.dumps(snapshot, sort_keys=True, default=str),
                start,
                end,
                candidate["to_kind"],
                json.dumps(strata, sort_keys=True),
                _ACTOR,
            )
            await _submit_proposal(
                conn,
                exp,
                assignment_id=str(assignment_id),
                template_id=candidate["to_template_id"],
                trigger_ref=(f"qualification:analyzed:cell{candidate['cell_index']}:slot{candidate['slot_ordinal']}"),
                start=start,
                end=end,
            )
            log.info(
                "experiment_qualification: claimed cell %d slot %d (%s->%s, %s) as assignment %s",
                candidate["cell_index"],
                candidate["slot_ordinal"],
                candidate["from_kind"],
                candidate["to_kind"],
                regime.value,
                assignment_id,
            )
            return
        await _record_skip(
            conn,
            exp,
            slot_id=candidate["slot_id"],
            detail={
                "reason": "eligibility_failed",
                "cell_index": candidate["cell_index"],
                "slot_ordinal": candidate["slot_ordinal"],
                "predicates": predicates,
                "pretrace": trace,
            },
        )

    # 2. Reposition only when the current content has no open cell work left.
    target_kind = positioning_target(slots, current_kind)
    if current_kind is None or target_kind is not None:
        needed = target_kind or (fifo_next_slots(slots)[0]["from_kind"] if fifo_next_slots(slots) else None)
        if needed is None:
            return
        target = templates[needed]
        source_id = templates[current_kind]["template_id"] if current_kind else target["template_id"]
        await _create_move(
            conn,
            exp,
            operation_kind="positioning",
            arm_label=needed,
            start=start,
            end=start + timedelta(hours=POSITIONING_HOURS),
            reason="initial_positioning" if prev is None or current_kind is None else "source_rotation",
            source_template_id=source_id,
            target_template_id=target["template_id"],
            regime_code_value=regime_code(regime) if regime is not None else _carry_regime(prev),
        )
        return

    # 3. Otherwise: the next locked 15-minute same-content identity_hold.
    current = templates[current_kind]
    await _create_move(
        conn,
        exp,
        operation_kind="identity_hold",
        arm_label=current_kind,
        start=start,
        end=start + timedelta(minutes=IDENTITY_HOLD_MINUTES),
        reason="hold_cadence",
        source_template_id=current["template_id"],
        target_template_id=current["template_id"],
        regime_code_value=regime_code(regime) if regime is not None else _carry_regime(prev),
    )


def _carry_regime(prev: dict | None) -> int:
    """Regime octet for non-analysis moves when now is unclassifiable: carry
    the previous assignment's code (chain continuity); default other_daylight."""
    if prev is not None:
        strata = prev.get("frozen_strata")
        if isinstance(strata, str):
            try:
                strata = json.loads(strata)
            except ValueError:
                strata = None
        if isinstance(strata, dict) and isinstance(strata.get("regime"), int):
            code = strata["regime"]
            if 0 <= code <= 3:
                return code
    return REGIMES.index(Regime.OTHER_DAYLIGHT)


# ============================================================================
# Worker entrypoint
# ============================================================================


async def experiment_qualification_scheduler(pool: asyncpg.Pool) -> None:
    """Drive the §8.3 qualification protocol for the active experiment."""
    mode = policy_vector_mode()
    experiment_id = active_experiment_id()
    if mode == POLICY_VECTOR_MODE_OFF or not experiment_id:
        # Feature-off: inert. No DB access, no leases, no timers.
        return
    if mode != POLICY_VECTOR_MODE_LIVE:
        # The step-test protocol needs real delivery + device echoes: shadow
        # mode would mint immutable assignments whose content can never
        # activate. Stay fully inert (env-only check, still no DB access).
        _log_once(
            f"mode:{mode}",
            "experiment_qualification: mode=%s — qualification protocol requires live delivery; idle",
            mode,
        )
        return

    async with pool.acquire() as conn:
        exp_row = await conn.fetchrow(
            "SELECT experiment_id::text AS experiment_id, status, kind, greenhouse_id, "
            "timezone, started_at "
            "FROM control_experiments WHERE experiment_id = $1::uuid AND protocol_version = 1",
            experiment_id,
        )
        if exp_row is None:
            _log_once(
                f"unknown:{experiment_id}",
                "experiment_qualification: VERDIFY_ACTIVE_EXPERIMENT_ID=%s not found — idle",
                experiment_id,
            )
            return
        exp = dict(exp_row)
        if exp["kind"] != "qualification":
            # Not this worker's phase (aa/randomized are driven elsewhere).
            return
        if exp["status"] not in ("armed", "running"):
            _log_once(
                f"status:{experiment_id}:{exp['status']}",
                "experiment_qualification: experiment %s is %s — idle until armed/running",
                experiment_id,
                exp["status"],
            )
            return

        now = await conn.fetchval("SELECT now() AS now_utc")

        current_row = await conn.fetchrow(
            """
            SELECT assignment_id::text AS assignment_id, arm_label, operation_kind,
                   slot_id::text AS slot_id, frozen_strata,
                   lower(valid_range) AS valid_from, upper(valid_range) AS valid_to
              FROM control_assignments
             WHERE experiment_id = $1::uuid
               AND status = 'active'
               AND now() <@ valid_range
             ORDER BY lower(valid_range) DESC
             LIMIT 1
            """,
            experiment_id,
        )
        if current_row is not None:
            current = dict(current_row)
            if current["operation_kind"] == "analyzed":
                await _monitor_analyzed(conn, exp, current, now)
            return

        prev_row = await conn.fetchrow(
            """
            SELECT assignment_id::text AS assignment_id, arm_label, operation_kind,
                   slot_id::text AS slot_id, frozen_strata, status,
                   lower(valid_range) AS valid_from, upper(valid_range) AS valid_to
              FROM control_assignments
             WHERE experiment_id = $1::uuid
               AND upper(valid_range) <= now()
             ORDER BY upper(valid_range) DESC
             LIMIT 1
            """,
            experiment_id,
        )
        prev = dict(prev_row) if prev_row is not None else None
        if prev is not None and prev["operation_kind"] == "analyzed" and prev["slot_id"]:
            await _resolve_finished_analyzed(conn, exp, prev, now)

        await _boundary_action(conn, exp, prev, now)
