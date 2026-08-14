"""experiment_assignments worker (#584 Lane C) — pure-logic tests.

No live DB: a scripted fake pool answers the worker's queries by SQL
substring. The hard acceptance bar is feature-off inertness — with the
default env the worker must return without acquiring a single connection.
"""

from __future__ import annotations

import asyncio
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

import esp32_push  # noqa: E402
import shared  # noqa: E402
from tasks.experiment_assignments import (  # noqa: E402
    EXPERIMENT_OWNED_PARAMS,
    experiment_assignment_scheduler,
)

EXPERIMENT_ID = str(uuid.uuid4())
ASSIGNMENT_ID = str(uuid.uuid4())


class FakeConn:
    """Answer queries by first-matching SQL substring; record every call."""

    def __init__(self, responders):
        self.responders = list(responders)
        self.calls: list[tuple[str, str, tuple]] = []

    def _respond(self, kind, sql, args, default):
        self.calls.append((kind, " ".join(sql.split()), args))
        for fragment, result in self.responders:
            if fragment in sql:
                return result(args) if callable(result) else result
        return default

    async def fetchrow(self, sql, *args):
        return self._respond("fetchrow", sql, args, None)

    async def fetch(self, sql, *args):
        return self._respond("fetch", sql, args, [])

    async def fetchval(self, sql, *args):
        return self._respond("fetchval", sql, args, None)

    async def execute(self, sql, *args):
        return self._respond("execute", sql, args, "OK")

    def sql_calls(self, fragment):
        return [(kind, sql, args) for kind, sql, args in self.calls if fragment in sql]


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return _Acquire(self._conn)


class ForbiddenPool:
    """Feature-off proof: any acquire is a test failure."""

    def acquire(self):
        raise AssertionError("feature-off worker must not touch the database")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    esp32_push.set_experiment_policy_hold(False)
    shared.experiment_assignment.clear()
    yield
    esp32_push.set_experiment_policy_hold(False)
    shared.experiment_assignment.clear()


def _enable(monkeypatch, mode="live", experiment_id=EXPERIMENT_ID):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", mode)
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", experiment_id)


def _exp_row(status="running"):
    return {"experiment_id": EXPERIMENT_ID, "status": status, "greenhouse_id": "vallery", "kind": "randomized"}


def _current_row():
    return {
        "assignment_id": ASSIGNMENT_ID,
        "arm_label": "X",
        "operation_kind": "randomized_day",
        "boundary": datetime.now(UTC) + timedelta(hours=6),
    }


# ── Feature-off inertness (the hard acceptance bar) ─────────────────────────


def test_feature_off_default_env_touches_nothing():
    _run(experiment_assignment_scheduler(ForbiddenPool()))


def test_mode_off_with_experiment_id_still_inert(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    _run(experiment_assignment_scheduler(ForbiddenPool()))


def test_mode_without_experiment_id_inert(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
    _run(experiment_assignment_scheduler(ForbiddenPool()))


def test_unrecognized_mode_fails_safe_to_off(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "lIvE-typo")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    _run(experiment_assignment_scheduler(ForbiddenPool()))


def test_feature_off_releases_a_stale_hold():
    esp32_push.set_experiment_policy_hold(True, EXPERIMENT_OWNED_PARAMS)
    _run(experiment_assignment_scheduler(ForbiddenPool()))
    assert esp32_push.experiment_policy_hold() == (False, frozenset())


def test_feature_off_clears_a_stale_assignment_receipt():
    # Lane D (#585): a mode flip back to off must not leave iris_planner a
    # stale receipt that would keep experiment gather mode armed.
    shared.experiment_assignment["assignment_id"] = ASSIGNMENT_ID
    _run(experiment_assignment_scheduler(ForbiddenPool()))
    assert shared.experiment_assignment == {}


# ── Schedule validation + deviations ────────────────────────────────────────


def test_missing_schedule_emits_critical_deviation(monkeypatch):
    _enable(monkeypatch)
    conn = FakeConn(
        [
            ("FROM control_experiments", _exp_row()),
            ("count(*)", 0),
        ]
    )
    _run(experiment_assignment_scheduler(FakePool(conn)))
    events = conn.sql_calls("INSERT INTO experiment_events")
    assert len(events) == 1
    assert "schedule_missing" in events[0][2][4]
    assert events[0][2][3] == "critical"


def test_assignment_gap_emits_deviation_and_releases_hold(monkeypatch):
    _enable(monkeypatch)
    esp32_push.set_experiment_policy_hold(True, EXPERIMENT_OWNED_PARAMS)
    conn = FakeConn(
        [
            ("FROM control_experiments", _exp_row("running")),
            ("count(*)", 30),
            ("upper(valid_range) <= now()", []),  # nothing overdue
            ("now() <@ valid_range", None),  # no current assignment
        ]
    )
    _run(experiment_assignment_scheduler(FakePool(conn)))
    events = conn.sql_calls("INSERT INTO experiment_events")
    assert any("assignment_gap" in event[2][4] for event in events)
    assert esp32_push.experiment_policy_hold() == (False, frozenset())


def test_assignment_gap_clears_the_receipt_cache(monkeypatch):
    # Lane D (#585): no covering assignment => no receipt => iris_planner
    # fails closed instead of gathering the general packet.
    _enable(monkeypatch)
    shared.experiment_assignment["assignment_id"] = ASSIGNMENT_ID
    conn = FakeConn(
        [
            ("FROM control_experiments", _exp_row("running")),
            ("count(*)", 30),
            ("upper(valid_range) <= now()", []),
            ("now() <@ valid_range", None),
        ]
    )
    _run(experiment_assignment_scheduler(FakePool(conn)))
    assert shared.experiment_assignment == {}


def test_current_assignment_caches_the_opaque_receipt_in_shadow_and_live(monkeypatch):
    # Lane D (#585): the receipt cache feeds context gathering only, so it is
    # populated in BOTH shadow and live modes (unlike the device-write hold).
    for mode in ("shadow", "live"):
        _enable(monkeypatch, mode=mode)
        shared.experiment_assignment.clear()
        conn = FakeConn(
            [
                ("FROM control_experiments", _exp_row("running")),
                ("count(*)", 30),
                ("upper(valid_range) <= now()", []),
                ("now() <@ valid_range", _current_row()),
                ("lower(valid_range) > now()", None),
            ]
        )
        _run(experiment_assignment_scheduler(FakePool(conn)))
        assert shared.experiment_assignment["assignment_id"] == ASSIGNMENT_ID, mode
        assert shared.experiment_assignment["experiment_id"] == EXPERIMENT_ID, mode
        assert "boundary" in shared.experiment_assignment, mode
        esp32_push.set_experiment_policy_hold(False)


# ── UTC boundary open/close with a fixture schedule ─────────────────────────


def _boundary_conn(mode_rows):
    boundary = datetime.now(UTC) - timedelta(minutes=1)
    overdue = {"assignment_id": str(uuid.uuid4()), "boundary": boundary}
    exposure = {"exposure_id": str(uuid.uuid4())}
    responders = [
        ("FROM control_experiments", _exp_row("running")),
        ("count(*)", 30),
        ("upper(valid_range) <= now()", [overdue]),
        ("FROM policy_exposures", [exposure]),
        ("fn_close_exposure", None),
        ("UPDATE control_assignments SET status = 'closed'", "UPDATE 1"),
        ("now() <@ valid_range", _current_row()),
        *mode_rows,
    ]
    return FakeConn(responders), overdue, exposure


def test_boundary_close_then_current_open_arms_hold_in_live(monkeypatch):
    _enable(monkeypatch, mode="live")
    conn, overdue, exposure = _boundary_conn([("lower(valid_range) > now()", None)])
    _run(experiment_assignment_scheduler(FakePool(conn)))
    # Exposure closed with reason boundary BEFORE the assignment closes.
    close_calls = conn.sql_calls("fn_close_exposure")
    assert len(close_calls) == 1 and "'boundary'" in close_calls[0][1]
    assert close_calls[0][2][0] == exposure["exposure_id"]
    assert close_calls[0][2][1] == overdue["boundary"]
    assert len(conn.sql_calls("UPDATE control_assignments SET status = 'closed'")) == 1
    close_index = conn.calls.index(close_calls[0])
    update_index = conn.calls.index(conn.sql_calls("UPDATE control_assignments SET status = 'closed'")[0])
    assert close_index < update_index
    # Live mode + covered now() => the 49-param legacy-push hold is armed.
    active, params = esp32_push.experiment_policy_hold()
    assert active and params == EXPERIMENT_OWNED_PARAMS and len(params) == 48  # wire schema v2


def test_shadow_mode_never_arms_the_hold(monkeypatch):
    _enable(monkeypatch, mode="shadow")
    conn, _overdue, _exposure = _boundary_conn([("lower(valid_range) > now()", None)])
    _run(experiment_assignment_scheduler(FakePool(conn)))
    assert esp32_push.experiment_policy_hold() == (False, frozenset())


def test_prestage_emits_activation_intent_once(monkeypatch):
    _enable(monkeypatch)
    upcoming = {"assignment_id": str(uuid.uuid4()), "boundary": datetime.now(UTC) + timedelta(minutes=10)}
    conn = FakeConn(
        [
            ("FROM control_experiments", _exp_row("running")),
            ("count(*)", 30),
            ("upper(valid_range) <= now()", []),
            ("now() <@ valid_range", _current_row()),
            ("lower(valid_range) > now()", upcoming),
        ]
    )
    _run(experiment_assignment_scheduler(FakePool(conn)))
    events = conn.sql_calls("INSERT INTO experiment_events")
    assert len(events) == 1
    assert "boundary_activation_intent" in events[0][2][4]
    assert events[0][2][1] == upcoming["assignment_id"]


def test_dedup_suppresses_repeat_events(monkeypatch):
    _enable(monkeypatch)
    upcoming = {"assignment_id": str(uuid.uuid4()), "boundary": datetime.now(UTC) + timedelta(minutes=10)}
    conn = FakeConn(
        [
            ("FROM control_experiments", _exp_row("running")),
            ("count(*)", 30),
            ("upper(valid_range) <= now()", []),
            ("now() <@ valid_range", _current_row()),
            ("lower(valid_range) > now()", upcoming),
            ("recorded_at > now()", 1),  # dedup probe: recent identical event
        ]
    )
    _run(experiment_assignment_scheduler(FakePool(conn)))
    assert conn.sql_calls("INSERT INTO experiment_events") == []
