"""VD-40: the irrigation reads' rules, models and routes, with no database.

Relay edges to runs, the M3 cause join, a day's minutes by M4's rules, gallons per run, program and item sync, the
pause rows, occurrences planned against actual, the minutes rows, the cursor, the refusals made before any query, the
auth-free and SQL-free route module, and the read-only, bounded SQL. The SQL itself runs in
tests/test_dashboard_db_irrigation_reads.py on the campaign database.
"""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import jsonschema
import pytest
import yaml

from verdify_schemas import irrigation as irr
from verdify_services import reads as core
from verdify_services.irrigation import program, runs
from verdify_services.irrigation import reads as water
from verdify_services.irrigation.runs import AttributedRun, Deltas, Edge, RelayRun
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
API = ROOT / "api"  # operator_routes imports as a top-level package, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

DENVER = ZoneInfo("America/Denver")
AT = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)  # 14:00 MST
MIDNIGHT = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
NEXT_MIDNIGHT = datetime(2026, 11, 3, 7, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
H = timedelta(hours=1)
S = timedelta(seconds=1)


def den(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A November 2026 America/Denver wall time, in UTC."""
    return datetime(2026, 11, day, hour, minute, second, tzinfo=DENVER).astimezone(UTC)


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [f"{list(error.path)}: {error.message}" for error in validator.iter_errors(instance)]


def ledger_row(line_id="L01", cause="schedule", started_at=None, ended_at=None, **columns) -> dict:
    row = {
        "id": columns.pop("id", 1),
        "line_id": line_id,
        "slot_id": columns.pop("slot_id", 5),
        "local_day": columns.pop("local_day", date(2026, 11, 2)),
        "cause": cause,
        "started_at": started_at,
        "ended_at": ended_at,
        "open_s": columns.pop("open_s", 60),
        "pulses": columns.pop("pulses", 1),
        "outcome": columns.pop("outcome", "ok"),
        "reason": columns.pop("reason", None),
        "command_id": columns.pop("command_id", None),
        "actor": columns.pop("actor", None),
        "recorded_at": columns.pop("recorded_at", AT),
    }
    assert not columns
    return row


def delta(ts, prior_ts, gal=1.0, klass="meter_attributed", event_id=None) -> dict:
    return {
        "water_event_id": event_id if event_id is not None else int(ts.timestamp()),
        "ts": ts,
        "prior_ts": prior_ts,
        "delta_gal": gal,
        "attribution_class": klass,
    }


def relay_run(line_id="L01", start=None, end=None, open=False) -> RelayRun:  # noqa: A002
    start = start or AT - H
    return RelayRun(line_id, start, end or start + 10 * MIN, open=open)


# ── Edges to runs: migration 194's rules ────────────────────────────────────


def test_runs_from_edges_keeps_the_on_segments_and_collapses_repeats():
    edges = [
        Edge(AT - 6 * H, False),
        Edge(AT - 5 * H, True),
        Edge(AT - 4 * H, True),  # a snapshot repeat, not a new run
        Edge(AT - 3 * H, False),
        Edge(AT - 2 * H, False),
        Edge(AT - H, True),
    ]
    found = runs.runs_from_edges("L01", None, edges, at=AT)
    assert found == [
        RelayRun("L01", AT - 5 * H, AT - 3 * H, open=False),
        RelayRun("L01", AT - H, AT, open=True),
    ]


def test_a_seed_on_at_the_window_start_keeps_its_true_start_and_the_first_on_row_starts_no_run():
    seed = Edge(AT - 9 * H, True)
    edges = [Edge(AT - 4 * H, True), Edge(AT - 3 * H, False)]
    assert runs.runs_from_edges("L04", seed, edges, at=AT) == [RelayRun("L04", AT - 9 * H, AT - 3 * H, open=False)]
    off_seed = Edge(AT - 9 * H, False)
    assert runs.runs_from_edges("L04", off_seed, edges, at=AT) == [RelayRun("L04", AT - 4 * H, AT - 3 * H, open=False)]


def test_a_same_timestamp_conflict_starts_a_new_run():
    edges = [Edge(AT - 5 * H, True), Edge(AT - 4 * H, True, True), Edge(AT - 3 * H, False)]
    assert runs.runs_from_edges("L01", None, edges, at=AT) == [
        RelayRun("L01", AT - 5 * H, AT - 4 * H),
        RelayRun("L01", AT - 4 * H, AT - 3 * H),
    ]


def test_a_run_still_on_at_the_window_end_closes_at_the_look_ahead_or_stays_open():
    edges = [Edge(AT - 5 * H, True)]
    assert runs.runs_from_edges("L01", None, edges, at=AT, closing=AT - H) == [RelayRun("L01", AT - 5 * H, AT - H)]
    assert runs.runs_from_edges("L01", None, edges, at=AT) == [RelayRun("L01", AT - 5 * H, AT, open=True)]
    assert runs.runs_from_edges("L01", None, [], at=AT) == []
    assert runs.runs_from_edges("L01", Edge(AT - H, False), [], at=AT) == []


def test_a_run_with_no_length_is_not_a_run():
    assert runs.runs_from_edges("L01", None, [Edge(AT, True)], at=AT) == []


def test_the_static_line_map_is_every_wet_relay():
    assert list(runs.LINE_RELAY) == list(irr.LINE_IDS)
    assert set(runs.MINUTES_ZONE) == set(irr.LINE_IDS)
    assert [line for line in irr.LINE_IDS if runs.chemistry(line) == "fert"] == ["L02", "L03", "L06", "L08", "L10"]


# ── Causes: M3's join ───────────────────────────────────────────────────────


def attributed(run, ledger=(), bridge=()):
    return runs.attribute([run], ledger, bridge, AT)[0]


def test_a_run_takes_the_ledger_row_whose_span_contains_its_start():
    run = relay_run(start=AT - H)
    row = ledger_row(started_at=AT - H - 5 * S, ended_at=AT - 30 * MIN, slot_id=9)
    item = attributed(run, [row])
    assert (item.cause, item.slot_id, item.ledger) == ("schedule", 9, row)


def test_the_newest_of_several_rows_wins_and_an_open_row_spans_to_now():
    run = relay_run(start=AT - H)
    older = ledger_row(cause="prewet", started_at=AT - 2 * H, ended_at=AT - 30 * MIN, id=1)
    newer = ledger_row(cause="feed", started_at=AT - H - S, ended_at=None, id=2)
    assert attributed(run, [newer, older]).cause == "feed"
    same_start = ledger_row(cause="flush", started_at=AT - H - S, ended_at=None, id=3)
    assert attributed(run, [newer, older, same_start]).cause == "flush"  # id breaks the tie


def test_rows_that_do_not_contain_the_start_do_not_match():
    run = relay_run(start=AT - H)
    ended_before = ledger_row(started_at=AT - 2 * H, ended_at=AT - H - S)
    started_after = ledger_row(started_at=AT - H + S, ended_at=AT)
    other_line = ledger_row(line_id="L02", started_at=AT - H - S, ended_at=AT)
    too_old = ledger_row(started_at=AT - H - timedelta(hours=24), ended_at=None)  # its end never arrived
    for row in (ended_before, started_after, other_line, too_old):
        assert attributed(run, [row]).ledger is None, row


def test_an_older_row_still_matches_when_the_newest_ended_before_the_run():
    run = relay_run(start=AT - H)
    spans = ledger_row(cause="schedule", started_at=AT - 3 * H, ended_at=AT, id=1)
    ended = ledger_row(cause="run_now", started_at=AT - 2 * H, ended_at=AT - 90 * MIN, id=2)
    assert attributed(run, [spans, ended]).cause == "schedule"


def test_an_l01_run_within_30_s_after_a_sent_command_is_run_now():
    run = relay_run(start=AT - H)
    command = {"command_id": uuid4(), "source": "dashboard", "actor": "jason", "sent_at": AT - H - 10 * S}
    item = attributed(run, [], [command])
    assert (item.cause, item.bridge, item.ledger) == ("run_now", command, None)
    assert attributed(run, [], [command | {"sent_at": AT - H - 30 * S}]).cause == "run_now"
    assert attributed(run, [], [command | {"sent_at": AT - H - 31 * S}]).cause == "unknown"
    assert attributed(run, [], [command | {"sent_at": AT - H + S}]).cause == "unknown"


def test_the_ledger_goes_before_the_bridge_and_the_bridge_is_l01s():
    command = {"command_id": uuid4(), "source": "dashboard", "actor": "jason", "sent_at": AT - H - 10 * S}
    run = relay_run(start=AT - H)
    row = ledger_row(started_at=AT - H - S, ended_at=AT)
    assert attributed(run, [row], [command]).cause == "schedule"
    assert attributed(relay_run("L05", start=AT - H), [], [command]).cause == "unknown"


@pytest.mark.parametrize(
    ("line_id", "cause"),
    [("L02", "feed"), ("L03", "feed"), ("L04", "vpd"), ("L07", "vpd"), ("L08", "unknown"), ("L11", "vpd")],
)
def test_an_unmatched_run_keeps_its_topology_cause(line_id, cause):
    assert attributed(relay_run(line_id)).cause == cause


# ── A day's minutes: M4's rules ─────────────────────────────────────────────


def groups_of(*items, deltas=()):
    return runs.day_groups(items, MIDNIGHT, NEXT_MIDNIGHT, Deltas(deltas))


def item_of(run, cause=None, ledger=None):
    return AttributedRun(run, cause or irr.TOPOLOGY_CAUSE[run.line_id].value, ledger=ledger)


def test_minutes_split_at_local_midnight_and_pulses_count_on_the_start_day():
    across = relay_run("L05", start=MIDNIGHT - 30 * MIN, end=MIDNIGHT + 90 * S)
    [group] = groups_of(item_of(across))
    assert (group.minutes, group.pulses, group.gal) == (Decimal("1.5"), 0, None)
    # The day before keeps the first half and the pulse.
    [before] = runs.day_groups([item_of(across)], MIDNIGHT - 24 * H, MIDNIGHT, Deltas(()))
    assert (before.minutes, before.pulses) == (Decimal("30.0"), 1)


def test_a_day_is_local_so_a_fall_back_day_has_25_hours():
    start, end = den(1, 0), den(2, 0)  # DST ended 2026-11-01 at 02:00
    assert end - start == 25 * H
    [group] = runs.day_groups([item_of(relay_run("L01", start=start, end=end))], start, end, Deltas(()))
    assert group.minutes == Decimal("1500.0")


def test_an_open_run_counts_to_now():
    [group] = groups_of(item_of(relay_run("L04", start=AT - 10 * MIN, end=AT, open=True)))
    assert (group.minutes, group.pulses) == (Decimal("10.0"), 1)


def test_minutes_round_half_up_to_a_tenth_and_groups_split_by_cause_and_slot():
    first = relay_run("L04", start=AT - 3 * H, end=AT - 3 * H + 3 * S)  # 0.05 min
    vpd = item_of(first, "vpd")
    scheduled = item_of(
        relay_run("L04", start=AT - 2 * H, end=AT - 2 * H + 60 * S),
        "schedule",
        ledger=ledger_row("L04", started_at=AT - 2 * H, slot_id=4),
    )
    found = {(group.cause, group.slot_id): group for group in groups_of(vpd, scheduled)}
    assert found[("vpd", None)].minutes == Decimal("0.1")
    assert found[("schedule", 4)].minutes == Decimal("1.0")


def test_gallons_for_the_runs_that_start_that_day():
    run = relay_run("L01", start=AT - H, end=AT - 50 * MIN)
    overlap = delta(AT - 55 * MIN, AT - 56 * MIN, 3.0)
    shared = delta(AT - 54 * MIN, AT - 55 * MIN, 2.0, "ambiguous_overlap")
    [group] = groups_of(item_of(run), deltas=[overlap, shared])
    assert group.gal == 3  # M4 sums meter_attributed; the shared delta counts for nothing here
    assert groups_of(item_of(run), deltas=[shared])[0].gal == 0  # an overlap, nothing attributed
    assert groups_of(item_of(run), deltas=[delta(AT - 10 * MIN, AT - 11 * MIN)])[0].gal == 0  # metered today
    assert groups_of(item_of(run))[0].gal is None  # the meter recorded nothing today
    assert groups_of(item_of(relay_run("L03", start=AT - H, end=AT - 50 * MIN)), deltas=[overlap])[0].gal is None


def test_whole_gallons_round_half_up_over_a_group():
    runs_of_day = [
        item_of(relay_run("L01", start=AT - 3 * H, end=AT - 3 * H + MIN)),
        item_of(relay_run("L01", start=AT - 2 * H, end=AT - 2 * H + MIN)),
    ]
    deltas = [
        delta(AT - 3 * H + 30 * S, AT - 3 * H - S, 0.25),
        delta(AT - 2 * H + 30 * S, AT - 2 * H - S, 0.25),
    ]
    assert runs.day_groups(runs_of_day, MIDNIGHT, NEXT_MIDNIGHT, Deltas(deltas))[0].gal == 1  # 0.5 rounds up


def test_deltas_overlap_as_the_run_accounting_view_says():
    run_start, run_end = AT - H, AT - 50 * MIN
    index = Deltas(
        [
            delta(run_start, run_start - MIN, event_id=1),  # ts not after the start
            delta(run_start + S, run_start - MIN, event_id=2),
            delta(run_end + MIN, run_end - S, event_id=3),  # its span reaches back into the run
            delta(run_end + MIN, run_end, event_id=4),  # prior_ts not before the end
            delta(run_end + 5 * MIN, run_end - S, event_id=5),  # the longest accepted delta
            delta(run_end + 6 * MIN, run_end - S, event_id=6),  # past any accepted delta's span
            delta(run_start + MIN, None, event_id=7),  # no interval
        ]
    )
    assert {row["water_event_id"] for row in index.overlapping(run_start, run_end)} == {2, 3, 5}


# ── Runs as people read them ────────────────────────────────────────────────


def test_a_mist_episode_is_one_lines_pulses_of_one_cause_with_gaps_of_10_min_or_less():
    start = AT - 2 * H
    pulses = [
        item_of(relay_run("L04", start=start, end=start + MIN)),
        item_of(relay_run("L04", start=start + 11 * MIN, end=start + 12 * MIN)),  # a 10 min gap: the same episode
        item_of(relay_run("L04", start=start + 22 * MIN + S, end=start + 23 * MIN)),  # 10 min 1 s: a new one
    ]
    groups = sorted(runs.group_runs(pulses), key=lambda group: group.start)
    assert [(group.start, group.end, group.pulses, group.open_s) for group in groups] == [
        (start, start + 12 * MIN, 2, 120),
        (start + 22 * MIN + S, start + 23 * MIN, 1, 59),
    ]


def test_other_causes_lines_and_drips_stand_alone():
    start = AT - 2 * H
    fog = item_of(relay_run("L11", start=start, end=start + MIN))
    center = item_of(relay_run("L04", start=start + MIN, end=start + 2 * MIN))
    other_cause = item_of(relay_run("L04", start=start + 3 * MIN, end=start + 4 * MIN), "feed")
    drip_a = item_of(relay_run("L01", start=start, end=start + MIN))
    drip_b = item_of(relay_run("L01", start=start + 2 * MIN, end=start + 3 * MIN))
    groups = runs.group_runs([fog, center, other_cause, drip_a, drip_b])
    assert len(groups) == 5
    assert {group.line_id: group.pulses for group in groups}["L01"] is None
    assert {group.line_id: group.pulses for group in groups}["L11"] == 1


def test_the_relay_runs_of_one_ledger_row_are_one_run_and_an_unjoined_pulse_is_not_in_it():
    start = AT - 2 * H
    row = ledger_row("L04", started_at=start - S, ended_at=start + 40 * MIN, id=8)
    joined = [
        item_of(relay_run("L04", start=start, end=start + MIN), "schedule", ledger=row),
        item_of(relay_run("L04", start=start + 20 * MIN, end=start + 21 * MIN), "schedule", ledger=row),
    ]
    vpd = item_of(relay_run("L04", start=start + 41 * MIN, end=start + 42 * MIN))
    groups = sorted(runs.group_runs([*joined, vpd]), key=lambda group: group.start)
    assert [(group.pulses, group.cause, group.ledger) for group in groups] == [(2, "schedule", row), (1, "vpd", None)]


def test_a_groups_open_flag_follows_its_parts():
    start = AT - 10 * MIN
    group = runs.group_runs(
        [
            item_of(relay_run("L04", start=start, end=start + MIN)),
            item_of(relay_run("L04", start=AT - MIN, end=AT, open=True)),
        ]
    )[0]
    assert group.open and group.open_s == 120


@pytest.mark.parametrize(
    ("deltas", "reported", "expected"),
    [
        ([], True, 0),  # moved no water while the meter reported
        ([], False, None),  # unknown is never 0
        ([delta(AT - 55 * MIN, AT - 56 * MIN, 2.4), delta(AT - 54 * MIN, AT - 55 * MIN, 1.2)], True, 4),
        ([delta(AT - 55 * MIN, AT - 56 * MIN, 2.0, "manual_or_unattributed")], True, 0),
        (
            [delta(AT - 55 * MIN, AT - 56 * MIN, 2.0), delta(AT - 54 * MIN, AT - 55 * MIN, 1.0, "ambiguous_overlap")],
            True,
            None,
        ),
    ],
)
def test_a_runs_gallons(deltas, reported, expected):
    [group] = runs.group_runs([item_of(relay_run("L01", start=AT - H, end=AT - 50 * MIN))])
    assert runs.run_gallons(group, Deltas(deltas), reported) == expected


def test_a_delta_across_two_pulses_counts_once():
    start = AT - H
    pulses = [
        item_of(relay_run("L04", start=start, end=start + MIN)),
        item_of(relay_run("L04", start=start + 2 * MIN, end=start + 3 * MIN)),
    ]
    [group] = runs.group_runs(pulses)
    assert runs.run_gallons(group, Deltas([delta(start + 150 * S, start + 30 * S, 2.0)]), True) == 2


# ── Program and item sync ───────────────────────────────────────────────────

DESIRED = "a" * 64
APPLIED = "b" * 16


def state_of(**kwargs):
    defaults = {"engine": True, "desired_sha256": DESIRED, "applied_hash": APPLIED, "desired_push": None, "at": AT}
    return water.program_state(**(defaults | kwargs))


def test_the_program_state_rules():
    assert state_of(engine=False) == "requested"
    assert state_of(engine=False, applied_hash=DESIRED[:16]) == "requested"
    assert state_of(applied_hash=DESIRED[:16]) == "confirmed"
    assert state_of() == "requested"  # no push of the desired hash yet
    assert state_of(applied_hash=None) == "requested"
    push = {"pushed_at": AT - 4 * MIN, "reject_code": None}
    assert state_of(desired_push=push | {"pushed_at": None}) == "requested"  # deferred
    assert state_of(desired_push=push) == "requested"
    assert state_of(desired_push=push | {"pushed_at": AT - 5 * MIN}) == "not_confirmed"
    assert state_of(desired_push=push | {"reject_code": "bad_hash"}) == "not_confirmed"


def sync_of(updated_at, **kwargs):
    defaults = {
        "engine": True,
        "desired_sha256": DESIRED,
        "applied_hash": APPLIED,
        "applied_pushed_at": AT - H,
        "at": AT,
    }
    return water.item_sync(updated_at, **(defaults | kwargs))


def test_the_item_sync_rules():
    assert sync_of(AT - 2 * H, engine=False) == "requested"
    assert sync_of(AT - 2 * H, engine=False, applied_hash=DESIRED[:16]) == "requested"  # never not_confirmed
    assert sync_of(AT - S, applied_hash=DESIRED[:16]) == "confirmed"  # an edit that does not change the record
    assert sync_of(AT - 2 * H) == "confirmed"  # the write precedes the applied push
    assert sync_of(AT - H) == "not_confirmed"  # at the push, not before it
    assert sync_of(AT - 4 * MIN) == "requested"
    assert sync_of(AT - 5 * MIN) == "not_confirmed"
    assert sync_of(AT - 2 * H, applied_pushed_at=None) == "not_confirmed"  # back on src=rom: no applied push


# ── Pause rows and the engine in effect ─────────────────────────────────────


def pause(target="L01", ts=AT - H, until=AT + H, actor="jason", reason="rain"):
    return {
        "ts": ts,
        "source": "dashboard",
        "actor": actor,
        "target": target,
        "reason": reason,
        "paused_until": None if until is None else until.isoformat(),
    }


def test_the_pause_row_is_the_newest_for_the_line_or_all_as_of_a_moment():
    rows = [
        pause("L01", ts=AT - MIN, until=None),
        pause("all", ts=AT - H, actor="iris"),
        pause("L02", ts=AT - 2 * H, actor="other"),
    ]
    assert water.pause_row(rows, "L01", AT)["paused_until"] is None
    assert water.pause_row(rows, "L03", AT)["actor"] == "iris"
    assert water.pause_row(rows, "L02", AT - 90 * MIN)["actor"] == "other"
    assert water.pause_row(rows, "L03", AT - 3 * H) is None
    assert water.pause_in_force(rows, "L01", AT) is None  # a resume ends it
    assert water.pause_in_force(rows, "L03", AT)["actor"] == "iris"
    assert water.pause_in_force(rows, "L03", AT + 2 * H) is None  # past paused_until


def test_a_pause_in_words():
    assert water.pause_words(pause(reason="rain")) == "line paused by jason: rain"
    assert water.pause_words(pause(reason=None)) == "line paused by jason"


def test_the_engine_row_in_effect():
    rows = [(AT - 3 * H, False), (AT - 2 * H, True), (AT - H, False)]
    assert water.engine_at(rows, AT - 4 * H) is False  # none in effect
    assert water.engine_at(rows, AT - 2 * H) is True
    assert water.engine_at(rows, AT - 90 * MIN) is True
    assert water.engine_at(rows, AT + H) is False


def test_reason_words():
    assert water.reason_words("occ") == "someone inside (Frigate)"
    assert water.reason_words("free text") == "free text"
    assert water.reason_words(None) is None


# ── Occurrences planned against actual ──────────────────────────────────────

MONDAY = date(2026, 11, 2)


def plan_item(status="planned", reason=None, code=None, start=None, slot_id=5, line_id="L01", day=MONDAY):
    return irr.AgendaOccurrence(
        slot_id=slot_id,
        line_id=line_id,
        local_day=day,
        planned_start=start or AT + H,
        minutes=10.0,
        status=status,
        reason=reason,
        reason_code=code,
    )


def read_occurrences(*items, created=None, ledger=None, pauses=(), engine=(), running=()):
    plan = irr.Agenda(first_day=MONDAY, last_day=MONDAY, occurrences=tuple(items), solar=())
    created = created if created is not None else {item.slot_id: AT - 30 * H * 24 for item in items}
    return water.occurrences_read(
        plan,
        created,
        ledger=ledger or {},
        pause_rows=list(pauses),
        engine_rows=list(engine),
        running_slots=running,
        at=AT,
    )


ENGINE = [(AT - 40 * H * 24, True)]


def test_a_planned_occurrence_before_the_engine_reads_blocked_past_and_future():
    future, past = plan_item(), plan_item(start=AT - H)
    for engine in ([], [(AT - 2 * H, False)]):
        found = read_occurrences(future, past, engine=engine)
        assert {item.status for item in found} == {"blocked"}
        assert {item.reason for item in found} == {water.ARRIVES_WITH_FIRMWARE}
        assert {item.outcome for item in found} == {None}


def test_a_future_occurrence_with_the_engine_stays_planned_with_its_note():
    [item] = read_occurrences(plan_item(reason=irr.DEGRADED_ONLY_NOTE), engine=ENGINE)
    assert (item.status, item.reason, item.outcome) == ("planned", irr.DEGRADED_ONLY_NOTE, None)


def test_a_past_occurrence_reads_done_from_its_ledger_row():
    past = plan_item(start=AT - H)
    rows = {(5, MONDAY): [ledger_row(id=31, outcome="short", reason="cap")]}
    [item] = read_occurrences(past, ledger=rows, engine=ENGINE)
    assert (item.status, item.outcome, item.run_id, item.reason) == ("done", "short", "ledger-31", "cap reached")


def test_a_ledger_row_with_no_outcome_and_no_row_both_read_unknown():
    past = plan_item(start=AT - H)
    [no_outcome] = read_occurrences(past, ledger={(5, MONDAY): [ledger_row(id=2, outcome=None)]}, engine=ENGINE)
    assert (no_outcome.status, no_outcome.outcome, no_outcome.run_id) == ("done", "unknown", "ledger-2")
    assert no_outcome.reason == water.NO_OUTCOME
    [no_row] = read_occurrences(past, engine=ENGINE)
    assert (no_row.status, no_row.outcome, no_row.run_id) == ("done", "unknown", None)


def test_the_ledger_row_on_the_occurrences_own_line_answers_for_a_fertilizer_run():
    past = plan_item(start=AT - H, line_id="L02")
    rows = {
        (5, MONDAY): [
            ledger_row("L01", "prewet", id=1, outcome="stopped"),
            ledger_row("L02", "feed", id=2, outcome="ok"),
        ]
    }
    [item] = read_occurrences(past, ledger=rows, engine=ENGINE)
    assert (item.outcome, item.run_id) == ("ok", "ledger-2")


def test_an_occurrence_from_before_the_schedule_existed_is_left_out():
    past = plan_item(start=AT - H)
    assert read_occurrences(past, created={5: AT - H}, engine=ENGINE) == []
    assert read_occurrences(past, created={5: AT - 30 * MIN}, engine=ENGINE) == []
    assert len(read_occurrences(past, created={5: AT - 2 * H}, engine=ENGINE)) == 1
    assert len(read_occurrences(plan_item(), created={5: AT - 30 * MIN}, engine=ENGINE)) == 1


def test_the_newest_due_occurrence_of_a_running_slot_reads_running():
    older, newer = plan_item(start=AT - 25 * H, day=date(2026, 11, 1)), plan_item(start=AT - 10 * MIN)
    future = plan_item(start=AT + 23 * H, day=date(2026, 11, 3))
    found = read_occurrences(older, newer, future, engine=ENGINE, running=[5])
    assert [item.status for item in found] == ["done", "running", "planned"]
    assert found[1].outcome is None and found[1].reason is None


def test_a_skip_keeps_its_words_and_a_pause_names_who_from_the_row_in_force():
    disabled = plan_item("skipped", "line disabled", "dis")
    [item] = read_occurrences(disabled, engine=[])
    assert (item.status, item.reason) == ("skipped", "line disabled")
    paused = plan_item("skipped", "paused", "pau", start=AT + 30 * MIN)
    [item] = read_occurrences(paused, pauses=[pause()], engine=[])
    assert (item.status, item.reason) == ("skipped", "line paused by jason: rain")
    [item] = read_occurrences(paused, pauses=[pause(reason=None)], engine=ENGINE)
    assert item.reason == "line paused by jason"
    [item] = read_occurrences(paused, engine=ENGINE)  # no row at all: still a pause, in words
    assert (item.status, item.reason) == ("skipped", "paused")


def test_a_past_start_is_not_skipped_for_a_pause_set_after_it():
    earlier = plan_item("skipped", "paused", "pau", start=AT - 2 * H)
    [item] = read_occurrences(earlier, pauses=[pause(ts=AT - H)], engine=ENGINE)
    assert (item.status, item.outcome) == ("done", "unknown")
    [item] = read_occurrences(earlier, pauses=[pause(ts=AT - H)], engine=[])
    assert item.status == "blocked"
    [item] = read_occurrences(earlier, pauses=[pause(ts=AT - 3 * H)], engine=ENGINE)
    assert (item.status, item.reason) == ("skipped", "line paused by jason: rain")


@pytest.mark.parametrize(
    ("code", "words"),
    [("dis", "line disabled"), ("nsm", "not a schedule member"), ("nvm", "not a VPD member")],
)
def test_todays_flags_say_nothing_about_a_past_start_with_the_engine(code, words):
    earlier = plan_item("skipped", words, code, start=AT - H)
    [item] = read_occurrences(earlier, engine=ENGINE)
    assert (item.status, item.outcome, item.reason) == ("done", "unknown", water.NO_OUTCOME)
    rows = {(5, MONDAY): [ledger_row(id=7, outcome="ok")]}
    [item] = read_occurrences(earlier, ledger=rows, engine=ENGINE)
    assert (item.status, item.outcome, item.run_id) == ("done", "ok", "ledger-7")
    # With no engine in effect at its start nothing could have run, so the agenda's skip stands.
    [item] = read_occurrences(earlier, engine=[])
    assert (item.status, item.outcome, item.reason) == ("skipped", None, words)
    # And a future start is the agenda's to call.
    [item] = read_occurrences(plan_item("skipped", words, code), engine=ENGINE)
    assert (item.status, item.reason) == ("skipped", words)


def test_a_ledger_skip_for_a_pause_names_who_too():
    past = plan_item(start=AT - H)
    rows = {(5, MONDAY): [ledger_row(id=4, outcome="skipped", reason="pau", open_s=0)]}
    [item] = read_occurrences(past, ledger=rows, pauses=[pause(ts=AT - 2 * H)], engine=ENGINE)
    assert (item.status, item.outcome, item.reason) == ("done", "skipped", "line paused by jason: rain")
    [item] = read_occurrences(past, ledger=rows, engine=ENGINE)
    assert item.reason == "paused"


# ── The minutes rows ────────────────────────────────────────────────────────


def minutes_row(day=MONDAY, zone="center", line_id="L04", cause="vpd", chemistry="clean", minutes=1.2, pulses=2, gal=1):
    return {
        "local_day": day,
        "zone": zone,
        "line_id": line_id,
        "cause": cause,
        "chemistry": chemistry,
        "minutes": minutes,
        "pulses": pulses,
        "gal": gal,
    }


def test_minutes_group_by_zone_line_or_cause_and_stay_split_by_cause_and_chemistry():
    rows = [
        minutes_row(minutes=1.1, pulses=2, gal=1),
        minutes_row(minutes=2.2, pulses=3, gal=None),  # another slot of the same cause
        minutes_row(line_id="L05", cause="schedule", minutes=3.0, pulses=1, gal=2),
        minutes_row(zone="supply", line_id="L03", cause="feed", chemistry="fert", minutes=6.0, pulses=1, gal=None),
        minutes_row(zone="wall", line_id="L02", cause="feed", chemistry="fert", minutes=5.0, pulses=1, gal=4),
    ]
    by_zone = water.minutes_rows(rows, "zone")
    assert [(row.group, row.cause, row.chemistry, row.minutes, row.pulses, row.gal) for row in by_zone] == [
        ("center", "schedule", "clean", 3.0, 1, 2),
        ("center", "vpd", "clean", 3.3, 5, 1),
        ("wall", "feed", "fert", 5.0, 1, 4),
    ]
    by_line = water.minutes_rows(rows, "line")
    assert {row.group for row in by_line} == {"L02", "L03", "L04", "L05"}
    by_cause = water.minutes_rows(rows, "cause")
    assert {(row.group, row.chemistry): row.minutes for row in by_cause} == {
        ("feed", "fert"): 11.0,
        ("schedule", "clean"): 3.0,
        ("vpd", "clean"): 3.3,
    }
    assert water.minutes_rows([], "zone") == []


# ── Listing runs, the cursor ────────────────────────────────────────────────


def test_only_ended_runs_that_started_in_the_window_are_listed():
    start = AT - H
    ended = runs.group_runs([item_of(relay_run("L01", start=start, end=start + MIN))])[0]
    running = runs.group_runs([item_of(relay_run("L04", start=AT - MIN, end=AT, open=True))])[0]
    before = runs.group_runs([item_of(relay_run("L05", start=start - 5 * H, end=start))])[0]
    l03 = runs.group_runs([item_of(relay_run("L03", start=start, end=start + MIN))])[0]
    open_row = ledger_row("L07", started_at=start, ended_at=None, outcome=None)
    in_window = runs.group_runs([item_of(relay_run("L07", start=start, end=start + MIN), "schedule", ledger=open_row)])[
        0
    ]
    old_row = ledger_row("L09", started_at=AT - 30 * H, ended_at=None, outcome=None, id=2)
    never_ended = runs.group_runs(
        [item_of(relay_run("L09", start=AT - 30 * H, end=AT - 29 * H), "schedule", ledger=old_row)]
    )[0]
    listed = water._list_runs(
        [ended, running, before, l03, in_window, never_ended],
        [],
        from_=AT - 40 * H,
        to=AT,
        cause=None,
        outcome=None,
        at=AT,
    )
    assert [(item.line_id, item.outcome) for item in listed] == [("L01", "ok"), ("L05", "ok"), ("L09", "unknown")]
    in_range = water._list_runs([before], [], from_=AT - H, to=AT, cause=None, outcome=None, at=AT)
    assert in_range == []


def test_lone_ledger_rows_list_in_the_window_and_filters_apply():
    row = ledger_row("L04", "fallback", None, None, id=9, outcome="skipped", reason="vok", open_s=0, pulses=0)
    listed = water._list_runs([], [(row, AT - H)], from_=AT - 2 * H, to=AT, cause=None, outcome=None, at=AT)
    assert [(item.run_id, item.open_s, item.outcome, item.cause) for item in listed] == [
        ("ledger-9", 0, "skipped", "fallback")
    ]
    assert water._gallons_of(listed[0], Deltas(()), False) == 0
    assert water._list_runs([], [(row, AT - 3 * H)], from_=AT - 2 * H, to=AT, cause=None, outcome=None, at=AT) == []
    for filters, count in (({"cause": "vpd"}, 0), ({"outcome": "ok"}, 0), ({"cause": "fallback"}, 1)):
        arguments = {"cause": None, "outcome": None} | filters
        assert len(water._list_runs([], [(row, AT - H)], from_=AT - 2 * H, to=AT, at=AT, **arguments)) == count


def test_a_no_start_row_belongs_at_its_planned_start_or_when_it_was_recorded():
    row = ledger_row("L04", "fallback", None, None, slot_id=1, local_day=MONDAY, recorded_at=AT)
    rule = {"slot_id": 1, "line_id": "L04", "label": None, "start_kind": "clock", "start_min": 480}
    assert water._no_start_time(row, {1: rule}) == den(2, 8)
    assert water._no_start_time(row, {}) == AT  # the schedule was deleted
    assert water._no_start_time(row | {"slot_id": None}, {1: rule}) == AT


def test_the_cursor_round_trips_and_a_bad_one_is_invalid_request():
    item = water.Run(
        run_id="L04-1",
        line_id="L04",
        zone="center",
        chemistry="clean",
        cause="vpd",
        started_at=AT,
        open_s=1,
        outcome="ok",
    )
    assert water.decode_run_cursor(water.encode_run_cursor(item)) == (AT, "L04-1")
    for bad in ("not-a-cursor", "e30", "W10", "WyIyMDI2IiwxXQ"):
        with pytest.raises(ServiceError) as refused:
            water.decode_run_cursor(bad)
        assert (refused.value.problem.code, refused.value.problem.field) == ("invalid_request", "cursor")


# ── Refusals before any query ───────────────────────────────────────────────


def refused(coro):
    with pytest.raises(ServiceError) as caught:
        asyncio.run(coro)
    return caught.value.problem


def test_more_than_400_days_of_minutes_is_window_too_large():
    day = date(2026, 1, 1)
    problem = refused(water.minutes(None, None, day, day + timedelta(days=400), "zone"))
    assert (problem.code, problem.status, problem.max, problem.current) == ("window_too_large", 422, 400, 401)
    assert refused(water.minutes(None, None, day, day - timedelta(days=1), "zone")).code == "invalid_request"
    assert refused(water.minutes(None, None, day, day, "plant")).code == "invalid_request"


def test_more_than_31_days_of_runs_is_window_too_large():
    problem = refused(water.runs(None, None, AT - timedelta(days=31) - S, AT))
    assert (problem.code, problem.status, problem.max) == ("window_too_large", 422, 31)
    assert refused(water.runs(None, None, AT, AT)).code == "invalid_request"
    assert refused(water.runs(None, None, AT, AT - H)).code == "invalid_request"
    assert refused(water.runs(None, None, datetime(2026, 11, 1), AT)).field == "from"  # noqa: DTZ001
    assert refused(water.runs(None, None, AT - H, AT, line="L12")).field == "line"
    assert refused(water.runs(None, None, AT - H, AT, cause="rain")).field == "cause"
    assert refused(water.runs(None, None, AT - H, AT, outcome="fine")).field == "outcome"
    for limit in (0, 201, True, "50"):
        problem = refused(water.runs(None, None, AT - H, AT, limit=limit))
        assert (problem.code, problem.field, problem.min, problem.max) == ("invalid_request", "limit", 1, 200)


@pytest.mark.parametrize(
    ("days", "past_days", "field"), [(0, 0, "days"), (8, 0, "days"), (7, 8, "past_days"), (7, -1, "past_days")]
)
def test_the_agenda_window_is_checked(days, past_days, field):
    problem = refused(water.agenda(None, None, days=days, past_days=past_days))
    assert (problem.code, problem.field) == ("invalid_request", field)


# ── Models against the committed schemas ────────────────────────────────────


def line_dict(**changes) -> dict:
    return {
        "line_id": "L01",
        "name": "Drip • Wall",
        "zone": "wall",
        "kind": "drip",
        "clean_partner": None,
        "reserved": False,
        "enabled": True,
        "check": None,
        "vpd_member": None,
        "schedule_member": None,
        "vpd_source": None,
        "daily_cap_min": 60,
        "vpd_reserve_min": None,
        "paused_until": None,
        "manual": False,
        "state": irr.LineState(code="idle", observed_at=AT),
        "today": water.LineToday(minutes=0.0, pulses=0, gal=0),
        "sync": "requested",
    } | changes


def test_a_status_validates_against_the_committed_schema():
    status = water.IrrigationStatus(
        server_time=AT,
        engine=False,
        program=water.ProgramSync(state="requested", desired_sha256=DESIRED, src=None, reason=water.NOT_ON_CONTROLLER),
        lines=[water.IrrigationLine(**line_dict())],
        active_runs=[water.ActiveRun(manifold="drip", line_id="L01", cause="run_now", started_at=AT)],
        today=water.TodayMinutes(date=MONDAY, zones={"wall": 1.5, "south": None}),
        limits=water.Limits(
            saf4_gal=water.UsedOfMax(used=None, max=360.0), soft_budget_gal=water.UsedOfMax(used=None, max=None)
        ),
        leak=core.Leak(active=None, since=None, flow_gpm=None, gal_last_10min=None, alert_id=None, stopped_at=None),
        occupancy=water.Occupancy(occupied=None, seen_at=None),
        fallback_active=False,
        freshness=water.StateFreshness(observed_at=None, age_s=None, stale=True),
    )
    assert openapi_errors("IrrigationStatus", status.model_dump(mode="json")) == []


def schedule_row() -> dict:
    return {
        "slot_id": 4,
        "line_id": "L01",
        "enabled": True,
        "days": 21,
        "start_kind": "clock",
        "start_min": 450,
        "on_s": 600,
        "count": 1,
        "gap_s": 0,
        "prewet_s": 0,
        "flush_s": 0,
        "degraded_only": False,
        "label": None,
        "manual": True,
        "source": "agent:steward",
        "updated_by": "steward",
        "updated_at": AT,
        "reason": None,
        "created_at": AT,
    }


def test_a_schedule_leaves_a_null_label_and_reason_out_as_the_committed_schema_types_them():
    row = schedule_row()
    schedule = water._schedule(row, "requested")
    dumped = schedule.model_dump(mode="json")
    assert "label" not in dumped and "reason" not in dumped
    assert dumped["days"] == ["mon", "wed", "fri"] and dumped["start"] == {"kind": "clock", "clock": "07:30"}
    assert dumped["updated_by"] == {"source": "agent:steward", "name": "steward"}
    assert openapi_errors("Schedule", dumped) == []
    labelled = water._schedule(row | {"label": "wall", "reason": "dry"}, "confirmed").model_dump(mode="json")
    assert (labelled["label"], labelled["reason"], labelled["sync"]) == ("wall", "dry", "confirmed")
    assert openapi_errors("Schedule", labelled) == []


def test_a_run_and_a_minutes_report_validate():
    run = water.Run(
        run_id="ledger-1",
        line_id="L02",
        zone="wall",
        chemistry="fert",
        cause="feed",
        slot_id=4,
        schedule_label="feed",
        command_id=str(uuid4()),
        actor={"source": "dashboard", "name": "jason"},
        started_at=AT - H,
        ended_at=AT,
        open_s=3600,
        gal=0,
        outcome="ok",
    )
    assert openapi_errors("RunPage", water.RunPage(items=[run]).model_dump(mode="json")) == []
    report = water.MinutesReport.model_validate(
        {
            "from": MONDAY,
            "to": MONDAY,
            "group_by": "zone",
            "periods": [{"start": MONDAY, "coverage": "no_data"}],
            "rows": [],
        }
    )
    dumped = report.model_dump(mode="json")
    assert dumped["from"] == "2026-11-02"
    assert openapi_errors("MinutesReport", dumped) == []


def test_a_mist_runs_planned_pulses_come_from_its_schedule():
    slots = [{"slot_id": 4, "count": 8}]

    def token(line_id, cause, slot_id):
        return irr.IrrRun(line_id=line_id, cause=cause, slot_id=slot_id, start_epoch=1, end_epoch=2)

    assert water.pulses_total(slots, token("L04", "schedule", 4)) == 8
    assert water.pulses_total(slots, token("L08", "feed", 4)) == 8
    assert water.pulses_total(slots, token("L07", "prewet", 4)) is None  # one opening on the clean partner
    assert water.pulses_total(slots, token("L07", "flush", 4)) is None
    assert water.pulses_total(slots, token("L04", "vpd", None)) is None
    assert water.pulses_total(slots, token("L01", "schedule", 4)) is None
    assert water.pulses_total(slots, token("L04", "schedule", 5)) is None  # deleted since


# ── API-02: the running list's cause ────────────────────────────────────────


def test_now_asks_run_causes_only_when_a_wet_line_is_on(monkeypatch):
    calls = []

    async def fake_run_causes(conn, from_, to):
        calls.append((from_, to))
        return [
            runs.RunCause("L04", AT - 5 * H, AT - 4 * H, "schedule"),
            runs.RunCause("L04", AT - 2 * MIN, AT, "vpd"),
            runs.RunCause("L01", AT - MIN, AT, "run_now"),
        ]

    monkeypatch.setattr(core, "run_causes", fake_run_causes)
    rep = core.reporting_from(AT - 30 * S, AT - 30 * S, AT)
    off = {name: core.Track(tuple(core.collapse([(AT - H, False)])), AT - H) for name in core.EQUIPMENT_LINE_IDS}
    assert asyncio.run(core._running_causes(None, off, rep)) == {}
    assert calls == []
    on = off | {"mister_center": core.Track(tuple(core.collapse([(AT - 2 * MIN, True)])), AT - 2 * MIN)}
    silent = core.reporting_from(AT - 30 * S, AT - 6 * MIN, AT)
    assert asyncio.run(core._running_causes(None, on, silent)) == {}
    assert calls == []
    assert asyncio.run(core._running_causes(None, on, rep)) == {"L04": "vpd", "L01": "run_now"}
    assert calls == [(AT - core.RUNNING_CAUSE_LOOKBACK, AT)]


# ── The route module: no auth, no SQL ───────────────────────────────────────


def test_the_route_module_carries_no_auth_and_no_sql():
    source = (API / "operator_routes/irrigation_reads.py").read_text()
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", source)
    assert not re.search(r"(?i)bearer|authorization|Depends\(", source.split('"""', 2)[2])
    from operator_routes import irrigation_reads as routes
    from operator_routes import reads as read_routes

    assert {route.operation_id for route in routes.router.routes} == {
        "API-IRR-01",
        "API-IRR-02",
        "API-IRR-10",
        "API-IRR-11",
    }
    for route in routes.router.routes:
        assert {dependency.call for dependency in route.dependant.dependencies} == {read_routes.connection}, route.path


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from operator_routes import irrigation_reads as routes
    from operator_routes import reads as read_routes

    calls: dict = {}

    async def fake_connection():
        yield "conn"

    async def fake_minutes(conn, actor, from_, to, group_by):
        calls["minutes"] = (conn, actor, from_, to, group_by)
        return water.MinutesReport.model_validate(
            {"from": from_, "to": to, "group_by": group_by, "periods": [], "rows": []}
        )

    async def fake_runs(conn, actor, from_, to, **filters):
        calls["runs"] = (conn, actor, from_, to, filters)
        return water.RunPage(items=[], next_cursor=None)

    async def fake_agenda(conn, actor, days=7, past_days=0):
        calls["agenda"] = (conn, actor, days, past_days)
        return water.ScheduleAgenda(
            schedules=[],
            occurrences=[],
            counts=water.AgendaCounts(schedules=0, max_schedules=24),
            bounds=irr.bounds_by_kind(),
            solar=[],
        )

    monkeypatch.setattr(water, "minutes", fake_minutes)
    monkeypatch.setattr(water, "runs", fake_runs)
    monkeypatch.setattr(water, "agenda", fake_agenda)
    app = FastAPI()

    @app.middleware("http")
    async def set_actor(request: Request, call_next):
        request.state.actor = "the-actor"
        return await call_next(request)

    app.include_router(routes.router, prefix="/v1")
    app.dependency_overrides[read_routes.connection] = fake_connection
    return TestClient(app), calls


def test_the_minutes_route_takes_from_to_and_group_by_and_serves_from(client):
    http, calls = client
    response = http.get("/v1/irrigation/minutes", params={"from": "2026-11-01", "to": "2026-11-02", "group_by": "line"})
    assert response.status_code == 200
    assert response.json()["from"] == "2026-11-01"
    assert "from_" not in response.json()
    assert calls["minutes"] == ("conn", "the-actor", date(2026, 11, 1), date(2026, 11, 2), "line")
    assert http.get("/v1/irrigation/minutes", params={"from": "2026-11-01", "to": "2026-11-02"}).status_code == 200
    assert calls["minutes"][-1] == "zone"
    assert http.get("/v1/irrigation/minutes", params={"from": "2026-11-01"}).status_code == 422
    bad_group = {"from": "2026-11-01", "to": "2026-11-02", "group_by": "plant"}
    assert http.get("/v1/irrigation/minutes", params=bad_group).status_code == 422


def test_the_runs_route_takes_every_committed_parameter(client):
    http, calls = client
    window = {"from": "2026-11-01T00:00:00-07:00", "to": "2026-11-02T00:00:00-07:00"}
    response = http.get(
        "/v1/irrigation/runs",
        params=window | {"line": "L04", "cause": "vpd", "outcome": "ok", "limit": 10, "cursor": "abc"},
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    conn, actor, from_, to, filters = calls["runs"]
    assert (conn, actor) == ("conn", "the-actor")
    assert from_ == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert to - from_ == timedelta(hours=24)
    assert filters == {"line": "L04", "cause": "vpd", "outcome": "ok", "limit": 10, "cursor": "abc"}
    http.get("/v1/irrigation/runs", params=window)
    assert calls["runs"][4] == {"line": None, "cause": None, "outcome": None, "limit": 50, "cursor": None}
    assert http.get("/v1/irrigation/runs", params=window | {"line": "L12"}).status_code == 422
    assert http.get("/v1/irrigation/runs", params=window | {"limit": 201}).status_code == 422
    assert http.get("/v1/irrigation/runs", params=window | {"cause": "rain"}).status_code == 422
    naive = {"from": "2026-11-01T00:00:00", "to": "2026-11-02T00:00:00"}
    assert http.get("/v1/irrigation/runs", params=naive).status_code == 422


def test_the_status_and_schedules_routes_serve_the_committed_shape(client, monkeypatch):
    http, calls = client

    async def fake_status(conn, actor):
        calls["status"] = (conn, actor)
        return water.IrrigationStatus(
            server_time=AT,
            engine=False,
            program=water.ProgramSync(
                state="requested", desired_sha256=DESIRED, src=None, reason=water.NOT_ON_CONTROLLER
            ),
            lines=[water.IrrigationLine(**line_dict())],
            active_runs=[],
            today=water.TodayMinutes(date=MONDAY, zones={"wall": 0.0, "south": None}),
            limits=water.Limits(
                saf4_gal=water.UsedOfMax(used=None, max=360.0), soft_budget_gal=water.UsedOfMax(used=None, max=None)
            ),
            leak=core.Leak(active=None, since=None, flow_gpm=None, gal_last_10min=None, alert_id=None, stopped_at=None),
            occupancy=water.Occupancy(occupied=None, seen_at=None),
            fallback_active=False,
            freshness=water.StateFreshness(observed_at=None, age_s=None, stale=True),
        )

    schedule = water._schedule(schedule_row(), "requested")

    async def fake_agenda(conn, actor, days=7, past_days=0):
        return water.ScheduleAgenda(
            schedules=[schedule],
            occurrences=[
                water.Occurrence(slot_id=4, line_id="L01", planned_start=AT, status="done", outcome="unknown")
            ],
            counts=water.AgendaCounts(schedules=1, max_schedules=24),
            bounds=irr.bounds_by_kind(),
            solar=[],
        )

    monkeypatch.setattr(water, "status", fake_status)
    monkeypatch.setattr(water, "agenda", fake_agenda)
    body = http.get("/v1/irrigation").json()
    assert calls["status"] == ("conn", "the-actor")
    assert openapi_errors("IrrigationStatus", body) == []
    assert body["lines"][0]["state"]["code"] == "idle"
    assert "cadence_s" not in body["freshness"]
    agenda_body = http.get("/v1/irrigation/schedules").json()
    assert openapi_errors("ScheduleAgenda", agenda_body) == []
    assert "label" not in agenda_body["schedules"][0] and "reason" not in agenda_body["schedules"][0]
    assert agenda_body["schedules"][0]["start"] == {"kind": "clock", "clock": "07:30"}


def test_the_schedules_route_checks_days_and_past_days(client):
    http, calls = client
    response = http.get("/v1/irrigation/schedules", params={"days": 3, "past_days": 7})
    assert response.status_code == 200
    assert response.json()["counts"] == {"schedules": 0, "max_schedules": 24}
    assert calls["agenda"] == ("conn", "the-actor", 3, 7)
    assert http.get("/v1/irrigation/schedules").status_code == 200
    assert calls["agenda"][2:] == (7, 0)
    assert http.get("/v1/irrigation/schedules", params={"days": 8}).status_code == 422
    assert http.get("/v1/irrigation/schedules", params={"past_days": 8}).status_code == 422


# ── Boundaries: read-only and bounded ───────────────────────────────────────

MODULES = (water, runs, program)
HYPERTABLES = ("climate", "equipment_state", "setpoint_changes", "setpoint_clamps", "setpoint_snapshot", "system_state")


def statements():
    for module in MODULES:
        for name, sql in vars(module).items():
            if name.endswith("_SQL") and isinstance(sql, str):
                yield f"{module.__name__.rsplit('.', 1)[-1]}.{name}", sql


def test_the_reads_never_write_and_send_no_notify():
    assert list(statements())
    for name, sql in statements():
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|CREATE|DROP|NOTIFY|pg_notify)\b", sql), name


def test_they_read_nothing_the_mcp_login_cannot_and_never_the_unbounded_views():
    for name, sql in statements():
        for relation in ("water_meter_events", "v_open_alerts", "v_water_relay_runs", "v_water_run_accounting"):
            assert not re.search(rf"\b{relation}\b", sql), (name, relation)
        assert "v_irrigation_minutes_daily" not in sql, name


def test_every_hypertable_query_is_bounded_by_ts():
    """Each FROM <hypertable> carries a ts bound up to its next FROM; the newest ON row walks its index to one row."""
    unbounded = []
    for name, sql in statements():
        for match in re.finditer(r"\bFROM\s+([a-z_]+)", sql):
            if match.group(1) not in HYPERTABLES:
                continue
            following = sql[match.end() :]
            scope = following[: following.find(" FROM ")] if " FROM " in following else following
            if not re.search(r"\bts\s*(>|>=|<|<=)", scope):
                unbounded.append(f"{name}: {match.group(1)}")
    assert unbounded == ["reads._NEWEST_ON_SQL: equipment_state"]


def test_engine_flag_and_program_id_readings():
    assert program.engine_flag("true") and program.engine_flag(" TRUE ")
    assert not any(program.engine_flag(value) for value in ("false", "", None, True))
    assert program.program_id_reading("h=0123456789abcdef src=nvs").src == "nvs"
    assert program.program_id_reading("nonsense") is None
    assert program.program_id_reading(None) is None
    lines = [irr.LineSpec(line_id=line_id) for line_id in irr.LINE_IDS]
    record = irr.compile_program(lines, [], irr.SiteValues(saf4_gal=400, rh_ceiling_pct=85, climate_yield_min=20))
    assert program.site_value({"bytes": record.record}, "mister_daily_volume_max_gal") == 400.0
    assert program.site_value({"bytes": record.record}, "sched_mist_rh_max_pct") == 85.0
    assert program.site_value({"bytes": record.record}, "climate_yield_min") == 20.0
    assert program.site_value({"bytes": record.record}, "not_a_site_row") is None
    assert program.site_value({"bytes": b"short"}, "climate_yield_min") is None
    assert water._saf4_max(program.ControllerProgram(engine=True, applied={"bytes": record.record})) == 400.0
    assert water._saf4_max(program.ControllerProgram(engine=True, applied={"bytes": b"short"})) == 360.0
    assert water._saf4_max(program.ControllerProgram(engine=False)) == 360.0
    assert (
        water._saf4_max(program.ControllerProgram(engine=False)) == irr.REGISTRY["mister_daily_volume_max_gal"].default
    )
