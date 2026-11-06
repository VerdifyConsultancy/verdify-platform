"""The Planner section's reads (#1361): statuses, the closed 2026-08-28 draft, the calendar and the capability checks.

DB-free: the helpers are pure, and the reads run on a fake connection that answers the capability probe as it reads
before migration 302 (no view) and after it (the old record).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from verdify_schemas.planner_study import load_study
from verdify_services import planner
from verdify_services.problems import ServiceError

OLD = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
STUDY = load_study("planner-ab-winter-2026")
AT = datetime(2026, 11, 6, 0, 10, tzinfo=UTC)  # 17:10 local on 2026-11-05


def old_row(**change):
    row = {
        "experiment_id": UUID(OLD),
        "study_id": "verdify-confirmed-component-switchback-v2-2026-08",
        "name": "Verdify confirmed-component AI-vs-frozen-FSM switchback v2",
        "status": "draft",
        "admission_state": "closed",
        "closing_event_kind": "emergency_action",
        "closing_event_at": datetime(2026, 9, 30, 17, 50, 12, tzinfo=UTC),
        "closing_event_reason": "facility_safe_closure",
        "randomized_pair_count": None,
        "started_at": None,
        "ended_at": None,
        "updated_at": datetime(2026, 9, 30, 17, 50, 12, tzinfo=UTC),
        "deviation_events": 198,
        "assigned_arms": None,
        "days_done": 0,
    }
    return {**row, **change}


@pytest.mark.parametrize(
    ("raw", "admission", "kind", "shown"),
    [
        ("draft", "closed", "emergency_action", "closed"),
        ("draft", "emergency_hold", "emergency_action", "closed"),
        ("draft", "closed", "state_transition", "draft"),
        ("draft", "open", None, "draft"),
        ("locked", "closed", None, "scheduled"),
        ("armed", "open", None, "scheduled"),
        ("running", "open", None, "running"),
        ("paused", "open", None, "paused"),
        ("completed", "closed", None, "finished"),
        ("aborted", "closed", None, "closed"),
    ],
)
def test_display_status(raw, admission, kind, shown):
    assert planner.display_status(raw, admission, kind) == shown


def test_the_old_draft_reads_closed_with_the_superseding_reason():
    summary = planner.record_summary(old_row(), [], 0, [STUDY])
    assert summary.status == "closed" and summary.raw_status == "draft"
    assert summary.closed_at == datetime(2026, 9, 30, 17, 50, 12, tzinfo=UTC)
    assert summary.closed_reason.startswith("Never randomized a day.")
    assert (summary.days_done, summary.deviations, summary.arms) == (0, 198, [])
    # Without a superseding definition the closing event's code reads in words.
    bare = planner.record_summary(old_row(), [], 0, [])
    assert bare.closed_reason == "Facility safe closure"
    # An open record has no closure.
    running = planner.record_summary(old_row(status="running", closing_event_kind=None), ["ai", "baseline"], 4, [])
    assert running.closed_at is None and running.closed_reason is None and running.arms == ["ai", "baseline"]


def test_a_committed_study_not_registered_reads_defined():
    summary = planner.defined_summary(STUDY)
    assert (summary.id, summary.experiment_id, summary.status) == ("planner-ab-winter-2026", None, "defined")
    assert (summary.days_planned, summary.arms) == (60, ["ai", "baseline"])
    item = planner.definition(STUDY)
    assert item.schedule.boundary_local == "11:00" and item.metrics[0].id == "night_vpd_in_band_pct"
    assert [metric.direction for metric in item.metrics if metric.id.startswith("heat")] == ["report"] * 3


def test_calendar_cells_take_the_local_start_day_and_count_deviations():
    a1, a2 = UUID(int=1), UUID(int=2)
    assignments = [
        {
            "assignment_id": a2,
            "pair_index": 0,
            "arm_label": "baseline",
            "status": "locked",
            "starts_at": datetime(2026, 12, 2, 18, tzinfo=UTC),
            "ends_at": datetime(2026, 12, 3, 18, tzinfo=UTC),
            "created_at": AT,
        },
        {
            "assignment_id": a1,
            "pair_index": 0,
            "arm_label": "ai",
            "status": "locked",
            "starts_at": datetime(2026, 12, 1, 18, tzinfo=UTC),
            "ends_at": datetime(2026, 12, 2, 18, tzinfo=UTC),
            "created_at": AT,
        },
    ]
    events = [
        {"assignment_id": a1, "event_kind": "override", "severity": "warning"},
        {"assignment_id": a1, "event_kind": "note", "severity": "info"},
    ]
    days = planner.calendar_days(assignments, events)
    assert [(day.date, day.arm, day.deviations, day.events) for day in days] == [
        (date(2026, 12, 1), "ai", 1, ["note", "override"]),
        (date(2026, 12, 2), "baseline", 0, []),
    ]


def test_next_boundary_only_while_scheduled_or_running():
    assert planner.next_boundary(STUDY, "defined", AT) is None
    assert planner.next_boundary(None, "running", AT) is None
    assert planner.next_boundary(STUDY, "running", AT).isoformat() == "2026-11-06T11:00:00-07:00"
    morning = datetime(2026, 11, 6, 15, tzinfo=UTC)  # 08:00 local
    assert planner.next_boundary(STUDY, "scheduled", morning).isoformat() == "2026-11-06T11:00:00-07:00"


def test_results_say_not_started_and_never_invent_numbers():
    results = planner.not_started(STUDY, 0)
    assert (results.state, results.pairs_done, results.pairs_planned, results.metrics) == ("not_started", 0, 30, [])
    assert results.reason == "No study day has run."


class FakeConn:
    """Answers the capability probe and the experiment read; records what it was asked."""

    def __init__(self, *, views: bool, rows=()):
        self.views, self.rows, self.asked = views, list(rows), []

    def is_in_transaction(self):
        return True

    @asynccontextmanager
    async def transaction(self, **_):
        yield

    async def fetchrow(self, sql, *args):
        self.asked.append(sql)
        return {"runs": self.views, "experiments": self.views, "attempts": False, "at": AT}

    async def fetch(self, sql, *args):
        self.asked.append(sql)
        if "FROM v_planner_experiments" in sql:
            return self.rows
        return []

    async def fetchval(self, sql, *args):
        self.asked.append(sql)
        return None


def test_before_302_the_list_is_the_committed_study_and_no_view_is_read():
    conn = FakeConn(views=False)
    listed = asyncio.run(planner.experiments(conn)).items
    assert [(item.id, item.status) for item in listed] == [("planner-ab-winter-2026", "defined")]
    assert not any("v_planner" in sql for sql in conn.asked[1:])


def test_after_302_the_old_draft_is_listed_closed_and_found_by_id():
    conn = FakeConn(views=True, rows=[old_row()])
    listed = asyncio.run(planner.experiments(conn)).items
    assert [(item.id, item.status) for item in listed] == [("planner-ab-winter-2026", "defined"), (OLD, "closed")]
    detail = asyncio.run(planner.experiment(conn, OLD))
    assert detail.definition is None and detail.calendar == [] and detail.results.state == "not_started"
    study = asyncio.run(planner.experiment(conn, "planner-ab-winter-2026"))
    assert study.definition.study_id == "planner-ab-winter-2026" and study.next_boundary is None
    with pytest.raises(ServiceError) as refused:
        asyncio.run(planner.experiment(conn, "nope"))
    assert refused.value.problem.code == "not_found"
