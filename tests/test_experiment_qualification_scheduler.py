"""experiment_qualification worker (#584/#588) — §8.3 protocol state machine.

No live DB: the scripted fake pool from test_experiment_workers answers the
worker's queries by SQL substring. Proven here:

- feature-off/shadow inertness (default env touches nothing; shadow mode is
  env-only inert because the step protocol requires live delivery);
- non-qualification experiments are ignored;
- FIFO slot selection, eligibility predicates, and the deterministic
  boundary decisions (claim / identity_hold / positioning / recovery);
- failed-cell handling is never-replace (the worker never inserts slot rows
  and resolution is one-way via fn_resolve_qualification_slot);
- stop-at-4 is structural: fully resolved cells yield no candidates;
- migration 212 text guards (producer check, frozen_strata claim signature,
  one-way resolution, REVOKEs).
"""

from __future__ import annotations

import asyncio
import json as _json
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tasks.experiment_qualification import (  # noqa: E402
    ANALYZED_HOURS,
    BOUNDARY_BACKDATE_GRACE_S,
    IDENTITY_HOLD_MINUTES,
    POSITIONING_HOURS,
    all_slots_resolved,
    eligible_claim_candidate,
    experiment_qualification_scheduler,
    fifo_next_slots,
    open_work_for_source,
    positioning_target,
    pretrace_evaluate,
    window_cutoff,
)
from test_experiment_workers import FakeConn, FakePool, ForbiddenPool  # noqa: E402

from verdify_schemas.experiment_regimes import Regime  # noqa: E402

EXPERIMENT_ID = str(uuid.uuid4())
GREENHOUSE = "vallery"
DEVICE_ID = f"esp32-{GREENHOUSE}"
NOW = datetime(2026, 8, 20, 18, 0, 0, tzinfo=UTC)

BASELINE_ID = str(uuid.uuid4())
MODERATE_ID = str(uuid.uuid4())
AGGRESSIVE_ID = str(uuid.uuid4())
BASELINE_SHA = "b" * 64
MODERATE_SHA = "d" * 64
AGGRESSIVE_SHA = "a" * 64

MIGRATION_212 = (
    Path(__file__).resolve().parents[1] / "db" / "migrations" / "212-qualification-scheduler.sql"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("VERDIFY_POLICY_DEVICE_ID", raising=False)
    yield


def _enable(monkeypatch, mode="live", experiment_id=EXPERIMENT_ID):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", mode)
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", experiment_id)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _exp_row(status="running", kind="qualification", started_days_ago=2):
    return {
        "experiment_id": EXPERIMENT_ID,
        "status": status,
        "kind": kind,
        "greenhouse_id": GREENHOUSE,
        "timezone": "America/Denver",
        "started_at": NOW - timedelta(days=started_days_ago),
    }


def _template_rows():
    return [
        {"template_id": BASELINE_ID, "kind": "baseline", "content_sha256": BASELINE_SHA},
        {"template_id": MODERATE_ID, "kind": "moderate", "content_sha256": MODERATE_SHA},
        {"template_id": AGGRESSIVE_ID, "kind": "aggressive", "content_sha256": AGGRESSIVE_SHA},
    ]


_EDGE_TEMPLATES = {
    "baseline": (BASELINE_ID, BASELINE_SHA),
    "moderate": (MODERATE_ID, MODERATE_SHA),
    "aggressive": (AGGRESSIVE_ID, AGGRESSIVE_SHA),
}


def _slot(cell, ordinal, status, from_kind="baseline", to_kind="moderate", slot_id=None):
    from_id, from_sha = _EDGE_TEMPLATES[from_kind]
    to_id, to_sha = _EDGE_TEMPLATES[to_kind]
    return {
        "slot_id": slot_id or str(uuid.uuid4()),
        "cell_index": cell,
        "slot_ordinal": ordinal,
        "status": status,
        "edge_id": str(uuid.uuid4()),
        "from_template_id": from_id,
        "to_template_id": to_id,
        "from_kind": from_kind,
        "to_kind": to_kind,
        "from_content_sha256": from_sha,
        "to_content_sha256": to_sha,
    }


def _snapshots(sha, start, end, step_s=60):
    rows = []
    t = start
    while t <= end:
        rows.append({"reported_at": t, "content_sha256": sha})
        t += timedelta(seconds=step_s)
    return rows


def _conditions_row(solar=500.0, temp=90.0, rh=30.0, age_s=60):
    # solar 500 / 90F / 30% RH @ 84 kPa => hot_bright_dry (regime code 1).
    return {
        "ts": NOW - timedelta(seconds=age_s),
        "outdoor_temp_f": temp,
        "outdoor_rh_pct": rh,
        "solar_w_m2": solar,
        "pressure_kpa": 84.0,
    }


def _assignment(op, start, end, *, arm="baseline", slot_id=None, status="closed"):
    return {
        "assignment_id": str(uuid.uuid4()),
        "arm_label": arm,
        "operation_kind": op,
        "slot_id": slot_id,
        "frozen_strata": _json.dumps(
            {"source_template_id": BASELINE_ID, "target_template_id": BASELINE_ID, "regime": 1}
        ),
        "status": status,
        "valid_from": start,
        "valid_to": end,
    }


def _base_responders(
    *,
    exp=None,
    current=None,
    prev=None,
    slots=(),
    latest_snap_sha=BASELINE_SHA,
    pretrace_rows=None,
    conditions=None,
    override_count=0,
    exposure=None,
    slot_status="claimed",
):
    """Scripted responders, most-specific fragments first."""
    if pretrace_rows is None:
        pretrace_rows = _snapshots(latest_snap_sha, NOW - timedelta(minutes=70), NOW)
    responders = [
        ("SELECT now() AS now_utc", NOW),
        ("fn_runtime_v1_claim_qualification_slot(", str(uuid.uuid4())),
        ("fn_runtime_v1_create_assignment(", str(uuid.uuid4())),
        ("fn_runtime_v1_submit_policy_proposal(", str(uuid.uuid4())),
        ("fn_runtime_v1_record_qualification_event(", 1),
        ("fn_runtime_v1_resolve_qualification_slot(", None),
        ("SELECT status FROM qualification_transition_slots", slot_status),
        ("FROM qualification_transition_slots s", list(slots)),
        ("FROM control_experiments WHERE experiment_id", exp or _exp_row()),
        ("AND now() <@ valid_range", current),
        ("AND upper(valid_range) <= now()", prev),
        ("FROM policy_templates WHERE experiment_id", _template_rows()),
        (
            "ORDER BY reported_at DESC LIMIT 1",
            {"content_sha256": latest_snap_sha, "reported_at": NOW - timedelta(seconds=30)}
            if latest_snap_sha
            else None,
        ),
        ("AND reported_at >= $2", pretrace_rows),
        ("FROM experiment_events", override_count),
        ("FROM policy_exposures", exposure),
        ("FROM climate", conditions if conditions is not None else _conditions_row()),
    ]
    return responders


# ---------------------------------------------------------------------------
# Inertness
# ---------------------------------------------------------------------------


def test_feature_off_default_env_touches_nothing():
    _run(experiment_qualification_scheduler(ForbiddenPool()))


def test_shadow_mode_is_env_only_inert(monkeypatch):
    _enable(monkeypatch, mode="shadow")
    _run(experiment_qualification_scheduler(ForbiddenPool()))


def test_off_mode_with_experiment_id_touches_nothing(monkeypatch):
    _enable(monkeypatch, mode="off")
    _run(experiment_qualification_scheduler(ForbiddenPool()))


def test_non_qualification_experiment_is_ignored(monkeypatch):
    _enable(monkeypatch)
    conn = FakeConn(_base_responders(exp=_exp_row(kind="randomized")))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    kinds = [sql for _, sql, _ in conn.calls]
    assert any("FROM control_experiments" in sql for sql in kinds)
    assert any("protocol_version = 1" in sql for sql in kinds)
    assert not any("control_assignments" in sql for sql in kinds)


def test_locked_experiment_is_idle(monkeypatch):
    _enable(monkeypatch)
    conn = FakeConn(_base_responders(exp=_exp_row(status="locked")))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    assert not any("control_assignments" in sql for _, sql, _ in conn.calls)


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------


def test_fifo_next_blocks_on_lower_ordinals():
    slots = [
        _slot(1, 1, "completed"),
        _slot(1, 2, "open"),
        _slot(1, 3, "open"),
        _slot(1, 4, "open"),
    ]
    heads = fifo_next_slots(slots)
    assert [s["slot_ordinal"] for s in heads] == [2]
    # A claimed lower ordinal blocks the whole cell.
    slots[1]["status"] = "claimed"
    assert fifo_next_slots(slots) == []


def test_fifo_failed_slot_is_never_replaced_and_queue_continues():
    slots = [
        _slot(0, 1, "failed"),
        _slot(0, 2, "open"),
        _slot(0, 3, "open"),
        _slot(0, 4, "open"),
    ]
    heads = fifo_next_slots(slots)
    # The failure permanently occupies ordinal 1; the cell continues at 2.
    assert [s["slot_ordinal"] for s in heads] == [2]


def test_stop_at_four_resolved_cell_yields_no_candidates():
    slots = [_slot(1, k, "completed" if k < 4 else "failed") for k in (1, 2, 3, 4)]
    assert fifo_next_slots(slots) == []
    assert eligible_claim_candidate(slots, "baseline", Regime.HOT_BRIGHT_DRY) is None
    assert all_slots_resolved(slots)


def test_eligible_claim_requires_source_and_regime_match():
    # cell 1 = edge (baseline->moderate) x hot_bright_dry.
    slots = [
        _slot(1, 1, "open", "baseline", "moderate"),
        _slot(5, 1, "open", "moderate", "baseline"),
    ]
    hit = eligible_claim_candidate(slots, "baseline", Regime.HOT_BRIGHT_DRY)
    assert hit is not None and hit["cell_index"] == 1
    assert eligible_claim_candidate(slots, "baseline", Regime.NIGHT) is None
    assert eligible_claim_candidate(slots, "aggressive", Regime.HOT_BRIGHT_DRY) is None
    assert eligible_claim_candidate(slots, None, Regime.HOT_BRIGHT_DRY) is None
    # moderate-sourced cell 5 (regime code 1) matches for moderate content.
    hit = eligible_claim_candidate(slots, "moderate", Regime.HOT_BRIGHT_DRY)
    assert hit is not None and hit["cell_index"] == 5


def test_claim_choice_is_lowest_cell_index():
    slots = [
        _slot(9, 1, "open", "baseline", "aggressive"),
        _slot(1, 1, "open", "baseline", "moderate"),
    ]
    hit = eligible_claim_candidate(slots, "baseline", Regime.HOT_BRIGHT_DRY)
    assert hit["cell_index"] == 1


def test_positioning_only_when_current_source_exhausted():
    slots = [
        _slot(1, 1, "completed", "baseline", "moderate"),
        _slot(1, 2, "completed", "baseline", "moderate"),
        _slot(1, 3, "failed", "baseline", "moderate"),
        _slot(1, 4, "completed", "baseline", "moderate"),
        _slot(5, 1, "open", "moderate", "baseline"),
    ]
    assert not open_work_for_source(slots, "baseline")
    assert open_work_for_source(slots, "moderate")
    assert positioning_target(slots, "baseline") == "moderate"
    assert positioning_target(slots, "moderate") is None  # still has open work
    assert positioning_target([], "baseline") is None


def test_pretrace_evaluate_gap_and_hash_rules():
    start = NOW - timedelta(minutes=60)
    clean = _snapshots(BASELINE_SHA, start, NOW, step_s=60)
    result = pretrace_evaluate(clean, BASELINE_SHA, start, NOW)
    assert result["ok"] and result["max_gap_s"] <= 60.0

    gap_rows = [
        r for r in clean if not (start + timedelta(minutes=20) < r["reported_at"] < start + timedelta(minutes=30))
    ]
    result = pretrace_evaluate(gap_rows, BASELINE_SHA, start, NOW)
    assert not result["ok"] and result["reason"] == "snapshot_gap"

    wrong = list(clean)
    wrong[10] = {**wrong[10], "content_sha256": MODERATE_SHA}
    result = pretrace_evaluate(wrong, BASELINE_SHA, start, NOW)
    assert not result["ok"] and result["reason"] == "content_hash_mismatch"

    assert pretrace_evaluate([], BASELINE_SHA, start, NOW)["reason"] == "no_snapshots_in_window"
    assert pretrace_evaluate(clean, None, start, NOW)["reason"] == "unknown_source_content"
    # Trailing gap (trace stops early) also fails.
    early = [r for r in clean if r["reported_at"] <= NOW - timedelta(minutes=10)]
    assert pretrace_evaluate(early, BASELINE_SHA, start, NOW)["reason"] == "snapshot_gap"


def test_window_cutoff_is_45_local_days():
    started = datetime(2026, 8, 15, 15, 30, tzinfo=UTC)  # 09:30 Denver (MDT)
    cutoff = window_cutoff(started, "America/Denver")
    # Local midnight Aug 15 + 45 days = local midnight Sep 29 (MDT, UTC-6).
    assert cutoff == datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Boundary decisions through the fake DB
# ---------------------------------------------------------------------------


def test_initial_positioning_created_when_no_assignments(monkeypatch):
    _enable(monkeypatch)
    slots = [_slot(1, 1, "open", "baseline", "moderate")]
    conn = FakeConn(_base_responders(slots=slots, latest_snap_sha=None, prev=None))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1
    args = creates[0][2]
    assert args[3] == "positioning"
    # Fixed 3h positioning interval.
    assert args[5] - args[4] == timedelta(hours=POSITIONING_HOURS)
    strata = _json.loads(args[10])
    assert strata["target_template_id"] == BASELINE_ID  # cell 1 source is baseline
    proposals = conn.sql_calls("fn_runtime_v1_submit_policy_proposal")
    assert len(proposals) == 1
    assert proposals[0][2][2] == BASELINE_ID  # proposal delivers the source vector
    # The worker NEVER creates slot rows (no fabricated 97th transition).
    assert not conn.sql_calls("INSERT INTO qualification_transition_slots")


def test_eligible_boundary_claims_slot_and_proposes_target(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "open", "baseline", "moderate")
    prev = _assignment("identity_hold", NOW - timedelta(minutes=16), NOW - timedelta(minutes=1))
    conn = FakeConn(
        _base_responders(
            slots=[slot],
            prev=prev,
            pretrace_rows=_snapshots(BASELINE_SHA, NOW - timedelta(minutes=75), NOW),
        )
    )
    _run(experiment_qualification_scheduler(FakePool(conn)))
    claims = conn.sql_calls("fn_runtime_v1_claim_qualification_slot")
    assert len(claims) == 1
    args = claims[0][2]
    assert args[0] == slot["slot_id"]
    snapshot = _json.loads(args[1])
    assert snapshot["predicates"] == {
        "inputs_fresh": True,
        "no_override": True,
        "pretrace_gap_free": True,
        "regime_match": True,
    }
    assert snapshot["regime"] == "hot_bright_dry"
    # Anchored at the previous boundary (within grace), six post-step hours.
    assert args[2] == prev["valid_to"]
    assert args[3] - args[2] == timedelta(hours=ANALYZED_HOURS)
    assert args[4] == "moderate"
    strata = _json.loads(args[5])
    assert strata == {
        "source_template_id": BASELINE_ID,
        "target_template_id": MODERATE_ID,
        "regime": 1,
    }
    proposals = conn.sql_calls("fn_runtime_v1_submit_policy_proposal")
    assert len(proposals) == 1
    assert proposals[0][2][2] == MODERATE_ID
    # A claim is not a positioning/hold move.
    assert not conn.sql_calls("fn_runtime_v1_create_assignment")


def test_stale_inputs_skip_claim_and_chain_hold(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "open", "baseline", "moderate")
    prev = _assignment("identity_hold", NOW - timedelta(minutes=16), NOW - timedelta(minutes=1))
    conn = FakeConn(
        _base_responders(
            slots=[slot],
            prev=prev,
            conditions=_conditions_row(age_s=3600),  # stale: fresh-inputs fails
        )
    )
    _run(experiment_qualification_scheduler(FakePool(conn)))
    assert not conn.sql_calls("fn_runtime_v1_claim_qualification_slot")
    skips = conn.sql_calls("fn_runtime_v1_record_qualification_event")
    assert len(skips) == 1
    detail = _json.loads(skips[0][2][1])
    assert detail["reason"] == "eligibility_failed"
    assert detail["predicates"]["inputs_fresh"] is False
    # The deterministic hold cadence continues on the same content.
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1
    args = creates[0][2]
    assert args[3] == "identity_hold"
    assert args[4] == prev["valid_to"]
    assert args[5] - args[4] == timedelta(minutes=IDENTITY_HOLD_MINUTES)
    strata = _json.loads(args[10])
    assert strata["source_template_id"] == strata["target_template_id"] == BASELINE_ID


def test_regime_mismatch_chains_hold_without_skip_record(monkeypatch):
    _enable(monkeypatch)
    # Night conditions, but the only open cell is hot_bright_dry.
    slot = _slot(1, 1, "open", "baseline", "moderate")
    prev = _assignment("identity_hold", NOW - timedelta(minutes=16), NOW - timedelta(minutes=1))
    conn = FakeConn(_base_responders(slots=[slot], prev=prev, conditions=_conditions_row(solar=5.0)))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    assert not conn.sql_calls("fn_runtime_v1_claim_qualification_slot")
    # No candidate was considered (regime mismatch), so no skipped-move row.
    assert not conn.sql_calls("fn_runtime_v1_record_qualification_event")
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1 and creates[0][2][3] == "identity_hold"


def test_boundary_gap_reanchors_and_records_skip(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "open", "baseline", "moderate")
    prev = _assignment(
        "identity_hold",
        NOW - timedelta(minutes=40),
        NOW - timedelta(seconds=BOUNDARY_BACKDATE_GRACE_S + 300),
    )
    conn = FakeConn(_base_responders(slots=[slot], prev=prev, conditions=_conditions_row(solar=5.0)))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    skips = conn.sql_calls("fn_runtime_v1_record_qualification_event")
    assert len(skips) == 1
    assert _json.loads(skips[0][2][1])["reason"] == "boundary_gap"
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1
    assert creates[0][2][4] == NOW  # re-anchored at now, not the stale boundary


def test_source_exhausted_triggers_positioning_rotation(monkeypatch):
    _enable(monkeypatch)
    slots = [_slot(1, k, "completed", "baseline", "moderate") for k in (1, 2, 3, 4)] + [
        _slot(5, 1, "open", "moderate", "baseline")
    ]
    prev = _assignment("identity_hold", NOW - timedelta(minutes=16), NOW - timedelta(minutes=1))
    conn = FakeConn(_base_responders(slots=slots, prev=prev))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1
    args = creates[0][2]
    assert args[3] == "positioning"
    assert args[2] == "moderate"
    strata = _json.loads(args[10])
    assert strata["source_template_id"] == BASELINE_ID
    assert strata["target_template_id"] == MODERATE_ID
    proposals = conn.sql_calls("fn_runtime_v1_submit_policy_proposal")
    assert proposals[0][2][2] == MODERATE_ID


def test_all_resolved_recovers_to_baseline_then_idles(monkeypatch):
    _enable(monkeypatch)
    slots = [_slot(1, k, "completed", "baseline", "moderate") for k in (1, 2, 3, 4)]
    prev = _assignment(
        "identity_hold",
        NOW - timedelta(minutes=16),
        NOW - timedelta(minutes=1),
        arm="moderate",
    )
    conn = FakeConn(_base_responders(slots=slots, prev=prev, latest_snap_sha=MODERATE_SHA))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    creates = conn.sql_calls("fn_runtime_v1_create_assignment")
    assert len(creates) == 1
    args = creates[0][2]
    assert args[3] == "baseline_recovery"
    strata = _json.loads(args[10])
    assert strata["source_template_id"] == MODERATE_ID
    assert strata["target_template_id"] == BASELINE_ID

    # Already on baseline: fully idle (no new assignments, no proposals).
    conn2 = FakeConn(_base_responders(slots=slots, prev=prev, latest_snap_sha=BASELINE_SHA))
    _run(experiment_qualification_scheduler(FakePool(conn2)))
    assert not conn2.sql_calls("fn_runtime_v1_create_assignment")
    assert not conn2.sql_calls("fn_runtime_v1_submit_policy_proposal")


def test_window_cutoff_stops_new_claims(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "open", "baseline", "moderate")
    prev = _assignment("identity_hold", NOW - timedelta(minutes=16), NOW - timedelta(minutes=1))
    conn = FakeConn(_base_responders(exp=_exp_row(started_days_ago=50), slots=[slot], prev=prev))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    assert not conn.sql_calls("fn_runtime_v1_claim_qualification_slot")
    # On baseline already -> idle, no recovery needed.
    assert not conn.sql_calls("fn_runtime_v1_create_assignment")


# ---------------------------------------------------------------------------
# Analyzed-step resolution (failed-never-replaced)
# ---------------------------------------------------------------------------


def _analyzed_prev(slot_id):
    return _assignment(
        "analyzed",
        NOW - timedelta(hours=ANALYZED_HOURS, minutes=1),
        NOW - timedelta(minutes=1),
        arm="moderate",
        slot_id=slot_id,
    )


def test_finished_analyzed_step_resolves_completed(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "claimed", "baseline", "moderate")
    prev = _analyzed_prev(slot["slot_id"])
    exposure = {
        "exposure_id": str(uuid.uuid4()),
        "started_at": prev["valid_from"] + timedelta(seconds=45),
        "ended_at": None,
        "close_reason": None,
        "identity_confirmed": True,
    }
    post_rows = _snapshots(MODERATE_SHA, prev["valid_from"], NOW, step_s=300)
    conn = FakeConn(
        _base_responders(
            slots=[slot],
            prev=prev,
            exposure=exposure,
            latest_snap_sha=MODERATE_SHA,
            pretrace_rows=post_rows,
        )
    )
    _run(experiment_qualification_scheduler(FakePool(conn)))
    resolves = conn.sql_calls("fn_runtime_v1_resolve_qualification_slot")
    assert len(resolves) == 1
    args = resolves[0][2]
    assert args[0] == slot["slot_id"]
    assert args[1] == "completed"


def test_missing_post_step_data_fails_cell(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "claimed", "baseline", "moderate")
    prev = _analyzed_prev(slot["slot_id"])
    exposure = {
        "exposure_id": str(uuid.uuid4()),
        "started_at": prev["valid_from"] + timedelta(seconds=45),
        "ended_at": None,
        "close_reason": None,
        "identity_confirmed": True,
    }
    # Echoes stop halfway through the six post-step hours.
    post_rows = _snapshots(MODERATE_SHA, prev["valid_from"], prev["valid_from"] + timedelta(hours=3), step_s=300)
    conn = FakeConn(
        _base_responders(
            slots=[slot],
            prev=prev,
            exposure=exposure,
            latest_snap_sha=MODERATE_SHA,
            pretrace_rows=post_rows,
        )
    )
    _run(experiment_qualification_scheduler(FakePool(conn)))
    resolves = conn.sql_calls("fn_runtime_v1_resolve_qualification_slot")
    assert len(resolves) == 1
    assert resolves[0][2][1] == "failed"
    detail = _json.loads(resolves[0][2][2])
    assert detail["failure"] == "missing_post_step_data"
    # Never replaced: the worker creates no slot rows, ever.
    assert not conn.sql_calls("INSERT INTO qualification_transition_slots")


def test_delivery_failure_fails_cell(monkeypatch):
    _enable(monkeypatch)
    slot = _slot(1, 1, "claimed", "baseline", "moderate")
    prev = _analyzed_prev(slot["slot_id"])
    conn = FakeConn(_base_responders(slots=[slot], prev=prev, exposure=None, latest_snap_sha=MODERATE_SHA))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    resolves = conn.sql_calls("fn_runtime_v1_resolve_qualification_slot")
    assert len(resolves) == 1
    assert resolves[0][2][1] == "failed"
    assert _json.loads(resolves[0][2][2])["failure"] == "delivery_failure"


def test_mid_assignment_safety_event_fails_cell_immediately(monkeypatch):
    _enable(monkeypatch)
    slot_id = str(uuid.uuid4())
    current = _assignment(
        "analyzed",
        NOW - timedelta(hours=2),
        NOW + timedelta(hours=4),
        arm="moderate",
        slot_id=slot_id,
        status="active",
    )
    conn = FakeConn(_base_responders(current=current, override_count=1))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    resolves = conn.sql_calls("fn_runtime_v1_resolve_qualification_slot")
    assert len(resolves) == 1
    assert resolves[0][2][0] == slot_id
    assert resolves[0][2][1] == "failed"
    detail = _json.loads(resolves[0][2][2])
    assert detail["failure"] == "safety_or_override_event"
    # Mid-assignment: no boundary action is taken.
    assert not conn.sql_calls("fn_runtime_v1_create_assignment")


def test_already_resolved_slot_is_not_re_resolved(monkeypatch):
    _enable(monkeypatch)
    slot_id = str(uuid.uuid4())
    current = _assignment(
        "analyzed",
        NOW - timedelta(hours=2),
        NOW + timedelta(hours=4),
        arm="moderate",
        slot_id=slot_id,
        status="active",
    )
    conn = FakeConn(_base_responders(current=current, override_count=1, slot_status="failed"))
    _run(experiment_qualification_scheduler(FakePool(conn)))
    assert not conn.sql_calls("fn_runtime_v1_resolve_qualification_slot")


# ---------------------------------------------------------------------------
# Migration 212 text guards
# ---------------------------------------------------------------------------


def test_migration_212_extends_producer_check():
    assert "policy_proposals_producer_check_v2" in MIGRATION_212
    assert "'qualification_scheduler'" in MIGRATION_212


def test_migration_212_claim_requires_frozen_strata():
    assert (
        "DROP FUNCTION IF EXISTS public.fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, text)"
        in MIGRATION_212
    )
    match = re.search(
        r"CREATE OR REPLACE FUNCTION public\.fn_claim_qualification_slot\((.*?)\) RETURNS",
        MIGRATION_212,
        re.S,
    )
    assert match and "p_frozen_strata jsonb" in match.group(1)
    assert "p_frozen_strata  => p_frozen_strata" in MIGRATION_212
    # Regime cross-check against the locked cell layout.
    assert "cell_index % 4" in MIGRATION_212


def test_migration_212_resolution_is_one_way_and_revoked():
    assert "fn_resolve_qualification_slot" in MIGRATION_212
    assert "only claimed slots resolve" in MIGRATION_212
    for fn in (
        "fn_claim_qualification_slot(uuid, jsonb, tstzrange, text, jsonb, text)",
        "fn_resolve_qualification_slot(uuid, text, jsonb, text)",
        "fn_record_qualification_event(uuid, text, jsonb, uuid, uuid, text)",
    ):
        assert f"REVOKE ALL ON FUNCTION public.{fn} FROM PUBLIC;" in MIGRATION_212
