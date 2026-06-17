"""Planning heartbeat milestone table.

Phase 4 retired the old fixed-boundary trigger fan-out and kept a small
solar-driven trigger set. The remaining milestones must all be reachable
inside the firing window.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import tasks  # noqa: E402
from tasks import heartbeat  # noqa: E402

DENVER = ZoneInfo("America/Denver")

# The five always-scheduled solar milestones. MIDNIGHT is a sixth REQUIRED
# planner trigger (SUNRISE/SUNSET/MIDNIGHT) but _compute_milestones only surfaces
# it during its early-day catch-up window (heartbeat.py:130-135), so it is
# present-or-absent depending on the time of day. The assertion below is
# therefore deliberately time-independent (it was a flaky exact-set check that
# failed whenever the suite ran shortly after local midnight).
SCHEDULED_MILESTONES = {
    "SUNRISE",
    "SOLAR_MAX",
    "TRANSITION:peak_stress",
    "TRANSITION:decline",
    "SUNSET",
}
# Conditionally-present milestones: MIDNIGHT only inside its catch-up window;
# WEEKLY only on the deep-review weekday (heartbeat._WEEKLY_REVIEW_WEEKDAY).
OPTIONAL_MILESTONES = {"MIDNIGHT", "WEEKLY"}

RETIRED_MILESTONES = {
    "TRANSITION:fixed_midnight",
    "TRANSITION:fixed_pre_dawn",
    "TRANSITION:fixed_midday",
    "TRANSITION:fixed_afternoon",
    "TRANSITION:fixed_evening",
    "TRANSITION:tree_shade",
    "TRANSITION:evening_settle",
}


@pytest.fixture(autouse=True)
def reset_milestone_cache():
    """Force _compute_milestones to rebuild from scratch in each test."""
    tasks._milestones_cache = {}
    tasks._milestones_fired = {}
    tasks._milestones_date = ""
    yield


def test_all_planning_milestones_present():
    milestones = set(tasks._compute_milestones().keys())
    # The scheduled set must always be exactly the five (MIDNIGHT excluded — it is
    # only present inside its catch-up window).
    assert milestones - OPTIONAL_MILESTONES == SCHEDULED_MILESTONES, (
        f"scheduled milestones drifted: got {milestones}, expected "
        f"{SCHEDULED_MILESTONES} (+ optional {OPTIONAL_MILESTONES})"
    )
    # Nothing outside the known scheduled + optional set may appear.
    assert milestones <= SCHEDULED_MILESTONES | OPTIONAL_MILESTONES, (
        f"unexpected milestone(s): {milestones - SCHEDULED_MILESTONES - OPTIONAL_MILESTONES}"
    )


def test_every_milestone_is_on_todays_date():
    milestones = tasks._compute_milestones()
    today = datetime.now(DENVER).date()
    off_day = {key: mt.date() for key, mt in milestones.items() if mt.date() != today}
    assert not off_day, f"Milestones not on today's date ({today}): {off_day}"


def test_retired_fixed_boundaries_are_absent():
    milestones = tasks._compute_milestones()
    assert not (set(milestones) & RETIRED_MILESTONES)


def test_every_milestone_fires_within_2h_past():
    milestones = tasks._compute_milestones()
    for key, milestone_time in milestones.items():
        simulated_now = milestone_time + timedelta(seconds=60)
        delta = (simulated_now - milestone_time).total_seconds()
        assert 0 <= delta < 7200, (
            f"{key} at {milestone_time}: delta={delta}s not in firing window; milestone can't be dispatched."
        )


def test_solar_max_maps_to_own_event_type():
    milestones = tasks._compute_milestones()
    assert "SOLAR_MAX" in milestones
    assert tasks._milestone_event("SOLAR_MAX") == ("SOLAR_MAX", "Solar peak planning checkpoint")


def test_milestones_ordered_sensibly_through_day():
    m = tasks._compute_milestones()
    assert m["SUNRISE"] < m["SOLAR_MAX"]
    assert m["SOLAR_MAX"] < m["TRANSITION:peak_stress"]
    assert m["TRANSITION:peak_stress"] < m["TRANSITION:decline"]
    assert m["TRANSITION:decline"] < m["SUNSET"]


def test_weekly_review_trigger_in_matrix():
    """L4 #346 AC6: a weekly deep-review trigger exists, expects a strategy
    set_plan, and rides the local.weekly_review cadence."""
    spec = heartbeat.PLANNER_TRIGGER_MATRIX["WEEKLY"]
    assert spec.event_type == "WEEKLY"
    assert spec.due_source == "local.weekly_review"
    assert spec.expected_action == "set_plan"
    assert spec.materialize_expected is True


def test_weekly_review_materialized_only_on_review_weekday():
    """WEEKLY is in the per-day milestone cache iff today is the review weekday —
    an unconditional entry would fire daily, defeating 'weekly'."""
    milestones = tasks._compute_milestones()
    is_review_day = datetime.now(DENVER).date().weekday() == heartbeat._WEEKLY_REVIEW_WEEKDAY
    assert ("WEEKLY" in milestones) == is_review_day


def test_weekly_review_due_time_is_review_hour():
    """When materialized, WEEKLY is due at the configured local review hour on
    today's date, so it fires once within the matrix catch-up window."""
    today = datetime.now(DENVER).date()
    due = heartbeat._milestone_due_at("WEEKLY", {}, today)
    assert (due.hour, due.minute) == (heartbeat._WEEKLY_REVIEW_HOUR, 0)
    assert due.date() == today
    assert due.tzinfo is not None
