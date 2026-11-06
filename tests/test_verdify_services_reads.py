"""VD-54: the Now, plan and equipment reads' rules, models and routes, with no database.

collapse() and the runs today, the required-trigger map, the band verdicts,
reporting(), freshness, the leak's flow_stopped_at(), applied_from, the interim
desired state, writer.differing, the equipment states, the labels, the models
against the committed OpenAPI, and the auth-free, SQL-free route module. The
SQL itself runs in tests/test_dashboard_db_reads.py on the campaign database.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
import pytest
import yaml

from verdify_schemas import alerts as schema_alerts
from verdify_schemas.irrigation import LINE_IDS
from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.tunable_registry import REGISTRY
from verdify_services import activity, alerts, reads, settings
from verdify_services.actor import ActorRef
from verdify_services.labels import EQUIPMENT_LABELS, EQUIPMENT_LINE_IDS, LINE_NAMES, name_line_codes

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml"
DENVER = ZoneInfo("America/Denver")
API = ROOT / "api"  # operator_routes imports as a top-level package, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))


def den(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """A November 2026 America/Denver wall time, in UTC."""
    return datetime(2026, 11, day, hour, minute, second, tzinfo=DENVER).astimezone(UTC)


AT = den(2, 14)  # 14:00 MST
MIDNIGHT = den(2, 0)


# ── collapse() and the runs today ───────────────────────────────────────────


def test_collapse_merges_repeats_into_runs_oldest_first():
    rows = [
        (den(2, 9), True),
        (den(1, 22), False),
        (den(2, 9, 5), True),  # per-poll repeat
        (den(2, 10), False),
        (den(1, 23), False),  # reconnect snapshot repeat
    ]
    spans = reads.collapse(rows)
    assert [(span.state, span.start, span.end) for span in spans] == [
        (False, den(1, 22), den(2, 9)),
        (True, den(2, 9), den(2, 10)),
        (False, den(2, 10), None),
    ]
    assert reads.collapse([]) == []


def test_since_is_the_newest_run_and_null_when_it_is_the_windows_first():
    spans = reads.collapse([(den(1, 3), False), (den(1, 4), False)])
    assert reads.run_since(spans) is None  # one run: it may have begun before the window
    spans = reads.collapse([(den(1, 3), True), (den(1, 4), False), (den(2, 1), False)])
    assert reads.run_since(spans) == den(1, 4)


def test_runtime_and_cycles_today_use_denver_midnight_and_ignore_repeats():
    rows = [
        (den(1, 22), True),  # on since before midnight: clipped to 00:00
        (den(2, 1), False),
        (den(2, 6), True),
        (den(2, 6, 5), True),  # poll repeats add no cycle
        (den(2, 6, 10), True),
        (den(2, 7), False),
        (den(2, 13), True),  # still on at 14:00
    ]
    since, runtime, cycles = reads.summarize(reads.collapse(rows), AT)
    assert since == den(2, 13)
    assert runtime == (1 + 1 + 1) * 3600
    assert cycles == 2  # 06:00 and 13:00; the run already on at midnight has no edge today


def test_midnight_is_local_across_the_dst_change():
    # DST ended 2026-11-01 02:00: that day's midnight is 06:00 UTC, the next is 07:00 UTC.
    assert reads.local_midnight(datetime(2026, 11, 1, 18, tzinfo=UTC)) == datetime(2026, 11, 1, 6, tzinfo=UTC)
    assert reads.local_midnight(datetime(2026, 11, 2, 18, tzinfo=UTC)) == datetime(2026, 11, 2, 7, tzinfo=UTC)


def test_a_first_run_on_since_before_the_window_counts_runtime_but_no_cycle():
    spans = reads.collapse([(den(1, 5), True)])
    since, runtime, cycles = reads.summarize(spans, AT)
    assert (since, runtime, cycles) == (None, 14 * 3600, 0)


# ── reporting(), freshness and the band verdicts ───────────────────────────


@pytest.mark.parametrize(
    ("heartbeat_s", "climate_s", "ingestor_fresh", "controller_reporting"),
    [
        (180, 300, True, True),
        (181, 10, False, False),
        (30, 301, True, False),
        (None, 10, False, False),
        (30, None, True, False),
    ],
)
def test_reporting_is_the_one_test(heartbeat_s, climate_s, ingestor_fresh, controller_reporting):
    heartbeat = AT - timedelta(seconds=heartbeat_s) if heartbeat_s is not None else None
    climate = AT - timedelta(seconds=climate_s) if climate_s is not None else None
    rep = reads.reporting_from(heartbeat, climate, AT)
    assert (rep.ingestor_fresh, rep.controller_reporting) == (ingestor_fresh, controller_reporting)
    assert (rep.heartbeat_at, rep.climate_at, rep.at) == (heartbeat, climate, AT)


def test_freshness_is_stale_past_its_limit_or_when_never_seen():
    sensor = {"cadence_s": 60, "stale_after_s": 120}
    assert reads.freshness(AT - timedelta(seconds=120), AT, **sensor).stale is False
    fresh = reads.freshness(AT - timedelta(seconds=121), AT, **sensor)
    assert (fresh.stale, fresh.age_s, fresh.cadence_s) == (True, 121, 60)
    heartbeat = {"cadence_s": 60, "stale_after_s": 180}
    assert reads.freshness(AT - timedelta(minutes=4), AT, **heartbeat).stale is True
    assert reads.freshness(AT - timedelta(seconds=30), AT, **heartbeat).stale is False
    never = reads.freshness(None, AT, **heartbeat)
    assert (never.observed_at, never.age_s, never.stale) == (None, None, True)


def reading(value, age_s=20):
    return reads.Reading(value=value, observed_at=AT - timedelta(seconds=age_s), reason=None)


@pytest.mark.parametrize(
    ("value", "verdict"),
    [(74.92, "in"), (84.92, "in"), (80.0, "in"), (84.93, "above"), (74.91, "below")],
)
def test_band_verdict_is_inclusive(value, verdict):
    position = reads.band_position(reading(value), 74.92, 84.92, AT, reporting=True)
    assert (position.low, position.high, position.verdict) == (74.92, 84.92, verdict)


@pytest.mark.parametrize(
    ("read", "low", "high", "reporting"),
    [
        (reading(80.0), None, 84.92, True),  # no band
        (reading(80.0), 74.92, None, True),
        (reading(80.0, age_s=121), 74.92, 84.92, True),  # stale data gets no verdict
        (reading(None), 74.92, 84.92, True),
        (reads.Reading(value=80.0, observed_at=None), 74.92, 84.92, True),
        (reading(80.0), 74.92, 84.92, False),  # nothing reads as fine while not reporting
    ],
)
def test_band_verdict_is_unavailable_without_a_band_or_fresh_data(read, low, high, reporting):
    assert reads.band_position(read, low, high, AT, reporting=reporting).verdict == "unavailable"


# ── Iris's required trigger ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "due_in_s", "expected"),
    [
        ("plan_written", -60, "ok"),
        ("expected", 60, "ok"),
        ("delivered", 60, "ok"),
        ("expected", -1, "late"),
        ("delivered", 0, "late"),
        ("missed", -60, "missed"),
        ("timed_out", -60, "failed"),
        ("delivery_failed", -60, "failed"),
        ("wrong_action", -60, "failed"),
        ("neutral_fallback", -60, "failed"),
        ("acked", -60, "failed"),
        ("action_completed", -60, "failed"),
        (None, None, "missed"),
    ],
)
def test_required_trigger_map(status, due_in_s, expected):
    due = AT + timedelta(seconds=due_in_s) if due_in_s is not None else None
    assert reads.required_trigger_state(status, due, AT) == expected


@pytest.mark.parametrize(
    ("label", "mister_state", "expected"),
    [
        # The firmware's SEALED_MIST label carries the stage; mister_state (the pulse machine) never reads FOG.
        ("SEALED_MIST_FOG", "WATCH", ("SEALED_MIST", "FOG")),
        ("SEALED_MIST_S2", None, ("SEALED_MIST", "S2")),
        ("SEALED_MIST", "S1", ("SEALED_MIST", "S1")),  # a bare label takes mister_state
        ("SEALED_MIST", "0.0", ("SEALED_MIST", None)),
        ("IDLE", "S1", ("IDLE", None)),
        ("SAFETY_COOL", None, ("SAFETY_COOL", None)),
        (None, "S1", ("UNKNOWN", None)),
    ],
)
def test_mode_splits_the_sealed_mist_label_into_code_and_stage(label, mister_state, expected):
    assert reads.mode_code_and_stage(label, mister_state) == expected


@pytest.mark.parametrize(
    ("reason", "fog_on", "held"),
    [
        ("occupancy", False, "occupancy"),
        ("vent_interlock", False, "vent_interlock"),
        ("occupancy", True, None),  # fog runs: nothing is held
        ("served", False, None),  # served means the fog relay is on
        ("none", False, None),
        (None, False, None),
    ],
)
def test_a_humidify_burst_is_held_only_by_a_named_fog_block(reason, fog_on, held):
    assert reads.humidify_held_reason(reason, fog_on) == held


def test_required_trigger_reads_the_newest_fired_row_not_the_days_rows_ahead():
    sql = reads._REQUIRED_TRIGGER_SQL
    assert "expected_at <= now()" in sql and "interval '36 hours'" in sql


# ── the leak: flow_stopped_at() is the shared VD-72 rule ────────────────────


def test_the_leak_block_uses_the_one_flow_stopped_rule():
    assert reads.flow_stopped_at is schema_alerts.flow_stopped_at
    readings = [(AT - timedelta(minutes=minute), gpm) for minute, gpm in ((4, 1.1), (3, 0.05), (2, None), (1, 0.0))]
    assert schema_alerts.flow_stopped_at(readings, below_gpm=0.1, needed=2) == AT - timedelta(minutes=1)
    assert schema_alerts.flow_stopped_at([*readings, (AT, 0.4)], below_gpm=0.1, needed=2) is None
    assert (reads.LEAK_STOPPED_BELOW_GPM, reads.LEAK_STOPPED_READINGS) == (0.1, 2)


def test_the_leak_block_reads_nothing_as_clear_while_the_controller_is_not_reporting():
    quiet = track([(AT - timedelta(hours=2), True), (AT - timedelta(hours=1), False)])
    no_alert = {"leak_alert_id": None, "leak_open_since": None}  # no open row: the read needs no query
    leak = asyncio.run(reads.leak_block(None, quiet, no_alert, 0.0, None, reporting(climate_s=360)))
    assert (leak.active, leak.flow_gpm, leak.gal_last_10min, leak.since) == (None, None, None, None)
    leak = asyncio.run(reads.leak_block(None, quiet, no_alert, 0.0, None, reporting()))
    assert (leak.active, leak.flow_gpm, leak.gal_last_10min, leak.since) == (False, 0.0, 0, None)


# ── applied_from and waypoints' effective ───────────────────────────────────


def desired_row(param="mister_engage_kpa", value=1.55, manual=False, created_at=None, **extra):
    return {
        "id": 7,
        "param": param,
        "value": value,
        "manual": manual,
        "source": "dashboard",
        "actor": "jason",
        "reason": None,
        "created_at": created_at or den(2, 8),
        **extra,
    }


def plan_row(value=1.70, created_at=None, ts=None):
    return {"value": value, "created_at": created_at or den(2, 7, 2), "ts": ts or den(2, 7, 2)}


def test_lane_applied_from_is_the_settings_rule():
    """API-07's lanes take applied_from from settings.status (VD-55), the dispatcher's §5.2 order."""
    spec = REGISTRY["mister_engage_kpa"]
    assert settings.applied_from(spec, desired_row(manual=True), plan_row(created_at=den(2, 9))) == "manual"
    # A plan written 07:02 and a non-manual desired row at 08:00: the desired row applies.
    assert settings.applied_from(spec, desired_row(), plan_row()) == "desired"
    assert settings.applied_from(spec, desired_row(), plan_row(created_at=den(2, 9))) == "plan"
    assert settings.applied_from(spec, desired_row(value=None), plan_row()) == "plan"
    assert settings.applied_from(spec, None, plan_row()) == "plan"
    assert REGISTRY["temp_low"].push_owner == "band"
    assert settings.applied_from(REGISTRY["temp_low"], None, None) == "band"
    assert REGISTRY["gl_main_lux_threshold"].push_owner == "schedule"
    assert settings.applied_from(REGISTRY["gl_main_lux_threshold"], None, None) == "band"
    assert settings.applied_from(spec, None, None) == "default"


def test_waypoint_effective_is_resolve_effective_picking_the_plan_row():
    from verdify_schemas.setting_values import resolve_effective

    waypoint = {"value": 1.70, "created_at": den(2, 7, 2)}  # the 13:00 waypoint of the 07:02 plan
    assert resolve_effective(desired_row(), waypoint).applied_from == "desired"  # effective false
    assert resolve_effective(desired_row(manual=True, created_at=den(1, 8)), waypoint).applied_from == "desired"
    newer = {"value": 1.70, "created_at": den(2, 9)}
    assert resolve_effective(desired_row(), newer).applied_from == "plan"  # effective true
    param = "mister_engage_kpa"
    assert reads.waypoint_effective(param, desired_row(), waypoint) is False
    assert reads.waypoint_effective(param, desired_row(manual=True, created_at=den(1, 8)), newer) is False
    assert reads.waypoint_effective(param, desired_row(), newer) is True
    assert reads.waypoint_effective(param, desired_row(value=None), waypoint) is True  # released
    assert reads.waypoint_effective(param, None, waypoint) is True
    assert reads.waypoint_effective(param, None, newer, superseded=True) is False
    # The dispatcher skips a desired row of a setting that is not writable now, and so does effective.
    assert reads.waypoint_effective("temp_low", desired_row(param="temp_low", manual=True), waypoint) is True


def facts(newest=None, last=None, clamped=False, newest_at=None):
    return reads.ReadbackFacts(newest_value=newest, newest_at=newest_at, last_match_at=last, clamped=clamped)


# ── writer.differing ──────────────────────────────────────────────────────


def planner_params(count: int) -> list[str]:
    names = [
        name
        for name, spec in sorted(REGISTRY.items())
        if spec.control_class == "planner_policy"
        and spec.esp_object_id
        and spec.push_owner != "dispatcher_default"
        and name not in reads._LIGHTING_LUX_PARAMS
        and name not in reads.BAND_OWNED_REG
    ]
    assert len(names) >= count
    return names[:count]


def plan_record(param, value, took_force):
    return {"parameter": param, "value": value, "ts": took_force, "created_at": took_force}


def mismatch(param, value):
    step = REGISTRY[param].step or 1.0
    return value + 10 * max(step, 1.0)


def test_differing_counts_values_unconfirmed_for_more_than_ten_minutes():
    at = den(2, 12)
    took = at - timedelta(hours=2)
    params = planner_params(26)
    active = {param: plan_record(param, 1.0, took) for param in params}
    values = reads.values_in_force({}, active, experiment_armed=False)
    assert [item.param for item in values] == sorted(params)
    readbacks = {param: facts(mismatch(param, 1.0)) for param in params[:19]}
    readbacks |= {param: facts(mismatch(param, 1.0), clamped=True) for param in params[19:]}
    assert reads.count_differing(values, readbacks, at) == (19, took)
    clamped_only = {param: readbacks[param] for param in params[19:]}
    assert reads.count_differing([v for v in values if v.param in clamped_only], clamped_only, at) == (0, None)


def test_differing_waits_ten_minutes_and_counts_from_the_last_match():
    at = den(2, 12)
    param = planner_params(1)[0]
    recent = reads.InForce(param=param, value=1.0, since=at - timedelta(minutes=9))
    assert reads.count_differing([recent], {param: facts(None)}, at) == (0, None)
    old = reads.InForce(param=param, value=1.0, since=at - timedelta(hours=3))
    assert reads.count_differing([old], {param: facts(1.0)}, at) == (0, None)  # newest matches
    assert reads.count_differing([old], {}, at) == (1, old.since)  # no readback at all since then
    drifted = facts(mismatch(param, 1.0), last=at - timedelta(minutes=30))
    assert reads.count_differing([old], {param: drifted}, at) == (1, at - timedelta(minutes=30))
    just_drifted = facts(mismatch(param, 1.0), last=at - timedelta(minutes=2))
    assert reads.count_differing([old], {param: just_drifted}, at) == (0, None)


def test_values_in_force_resolve_desired_rows_and_leave_out_what_is_not_sent():
    at = den(2, 12)
    took = at - timedelta(hours=2)
    param = planner_params(1)[0]
    desired = {param: desired_row(param=param, value=2.0, created_at=took + timedelta(minutes=5))}
    [value] = reads.values_in_force(desired, {param: plan_record(param, 1.0, took)}, experiment_armed=False)
    assert (value.value, value.since) == (2.0, took + timedelta(minutes=5))
    # A released row hands the setting back to the plan.
    released = {param: desired_row(param=param, value=None, created_at=took + timedelta(minutes=5))}
    [value] = reads.values_in_force(released, {param: plan_record(param, 1.0, took)}, experiment_armed=False)
    assert (value.value, value.since) == (1.0, took)
    # A waypoint written after its time took force when it was written.
    late = {"parameter": param, "value": 1.0, "ts": took, "created_at": took + timedelta(minutes=7)}
    assert reads.values_in_force({}, {param: late}, experiment_armed=False)[0].since == took + timedelta(minutes=7)
    # Plan rows the dispatcher never sends: band, legacy lighting, lux, dispatcher-derived, retired, unknown.
    skipped = ["temp_low", "gl_lux_threshold", "gl_main_lux_threshold", "activity_start_hour", "not_a_tunable"]
    rows = {name: plan_record(name, 1.0, took) for name in skipped}
    assert reads.values_in_force({}, rows, experiment_armed=False) == []
    # While an experiment is armed the 48 policy fields are left out.
    wire = sorted(spec.name for spec in wire_fields())[:3]
    rows = {name: plan_record(name, 1.0, took) for name in wire}
    assert reads.values_in_force({}, rows, experiment_armed=True) == []
    assert len(reads.values_in_force({}, rows, experiment_armed=False)) == 3


# ── equipment states ────────────────────────────────────────────────────────


def track(rows):
    spans = tuple(reads.collapse(rows))
    return reads.Track(spans, max((ts for ts, _ in rows), default=None))


def reporting(*, heartbeat_s=30, climate_s=20, at=AT):
    return reads.reporting_from(at - timedelta(seconds=heartbeat_s), at - timedelta(seconds=climate_s), at)


def items_by_name(tracks, rep):
    return {item.equipment: item for item in reads._equipment_items(tracks, rep)}


def test_fixed_list_order_labels_groups_and_line_ids():
    items = reads._equipment_items({}, reporting())
    assert [item.equipment for item in items] == [
        "fan1",
        "fan2",
        "vent",
        "heat1",
        "heat2",
        "drip_wall",
        "drip_wall_fert",
        "fert_master_valve",
        "mister_center",
        "drip_center",
        "drip_center_fert",
        "mister_south",
        "mister_south_fert",
        "mister_west",
        "mister_west_fert",
        "fog",
        "grow_light_main",
        "grow_light_grow",
    ]
    assert [item.line_id for item in items if item.group == "water"] == list(LINE_IDS)
    assert {item.group for item in items[:5]} == {"climate"} and {item.group for item in items[-2:]} == {"light"}
    assert all(item.label == EQUIPMENT_LABELS[item.equipment] for item in items)
    # No row: unknown, never off.
    assert {(item.state, item.since, item.observed_at) for item in items} == {("unknown", None, None)}


def test_fan1_reads_off_since_its_last_real_change_while_the_controller_reports():
    def october(day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=DENVER)

    # The 10-27 04:26 OFF edge, then reconnect snapshots repeating OFF.
    rows = [(october(26, 4), True), (october(27, 4, 26), False), (october(28, 23), False), (october(29, 11), False)]
    item = items_by_name({"fan1": track(rows)}, reporting())["fan1"]
    assert (item.state, item.since, item.runtime_today_s, item.cycles_today) == ("off", october(27, 4, 26), 0, 0)
    assert item.observed_at == october(29, 11)


def test_a_state_older_than_the_window_is_known_with_no_since():
    class Conn:
        def __init__(self):
            self.calls = []

        async def fetch(self, sql, keys, window):
            self.calls.append((sql, list(keys), window))
            if sql is reads._EQUIPMENT_TRACKS_SQL:  # fan1 changed today; drip_wall not in 31 days
                return [
                    {"key": "fan1", "ts": den(2, 9), "state": True, "newest_at": den(2, 10)},
                    {"key": "fan1", "ts": den(2, 10), "state": False, "newest_at": den(2, 10)},
                ]
            return [{"key": "drip_wall", "ts": AT - timedelta(days=40), "state": False}]

    conn = Conn()
    tracks = asyncio.run(reads._tracks(conn, reads._EQUIPMENT_TRACKS_SQL, ("fan1", "drip_wall", "fog")))
    assert [call[1:] for call in conn.calls] == [
        (["fan1", "drip_wall", "fog"], reads.STATE_WINDOW),
        (["drip_wall", "fog"], reads.NEWEST_ROW_WINDOW),  # only the keys with no row in the window
    ]
    assert conn.calls[1][0] is reads._EQUIPMENT_NEWEST_SQL
    items = items_by_name(tracks, reporting())
    drip = items["drip_wall"]
    assert (drip.state, drip.since, drip.runtime_today_s, drip.cycles_today) == ("off", None, 0, 0)
    assert drip.observed_at == AT - timedelta(days=40)
    assert (items["fog"].state, items["fan1"].state, items["fan1"].since) == ("unknown", "off", den(2, 10))


def test_every_state_is_unknown_with_its_age_while_the_controller_is_not_reporting():
    rows = {"fan1": track([(den(2, 9), True)]), "heat1": track([(den(2, 13, 50), False)])}
    for rep in (reporting(climate_s=360), reporting(heartbeat_s=240)):
        items = items_by_name(rows, rep)
        assert (items["fan1"].state, items["fan1"].observed_at) == ("unknown", den(2, 9))
        assert (items["heat1"].state, items["heat1"].observed_at) == ("unknown", den(2, 13, 50))
        assert {(item.since, item.runtime_today_s, item.cycles_today) for item in items.values()} == {
            (None, None, None)
        }


def test_grow_lights_are_the_lutron_readback_and_unknown_after_two_missed_polls():
    polls = [(AT - timedelta(minutes=minute), minute <= 60) for minute in range(120, 0, -5)]  # on at 13:00
    stale = [(AT - timedelta(minutes=minute), True) for minute in (30, 20, 11)]
    tracks = {"grow_light_main": track(polls), "grow_light_grow": track(stale)}
    items = items_by_name(tracks, reporting())
    main, grow = items["grow_light_main"], items["grow_light_grow"]
    assert (main.state, main.since, main.cycles_today) == ("on", den(2, 13), 1)  # the polls collapse
    assert (grow.state, grow.since, grow.observed_at) == ("unknown", None, AT - timedelta(minutes=11))
    # The Lutron state stands whatever the controller does.
    assert items_by_name(tracks, reporting(climate_s=900))["grow_light_main"].state == "on"


def test_grow_light_intent_is_the_controllers_switch():
    tracks = {"grow_light_main_intent": track([(den(2, 6), True)])}
    items = items_by_name(tracks, reporting())
    assert (items["grow_light_main"].intent, items["grow_light_grow"].intent) == ("on", None)
    assert items_by_name(tracks, reporting(climate_s=900))["grow_light_main"].intent == "unknown"
    assert items["fan1"].intent is None


def test_labels_name_every_actuator_and_flag_once():
    names = [name for name, _ in reads.EQUIPMENT_LIST]
    intents = {"grow_light_main_intent", "grow_light_grow_intent"}  # API-05's controller-intent lanes (#1356)
    assert set(EQUIPMENT_LABELS) == {*names, "occupancy", "leak_detected", "water_flowing", *intents}
    assert list(EQUIPMENT_LINE_IDS.values()) == list(LINE_IDS)
    for name, line_id in EQUIPMENT_LINE_IDS.items():
        # The operator's name, never the relay code (#1341), and the line reads the same everywhere.
        assert line_id not in EQUIPMENT_LABELS[name]
        assert LINE_NAMES[line_id] == EQUIPMENT_LABELS[name]
    assert EQUIPMENT_LABELS["drip_wall"] == "Wall drip" and LINE_NAMES["L02"] == "Wall drip (fertilized)"
    assert EQUIPMENT_LABELS["heat1"] == "Heat 1 electric" and EQUIPMENT_LABELS["heat2"] == "Heat 2 gas"
    assert len(set(EQUIPMENT_LABELS.values())) == len(EQUIPMENT_LABELS)


# ── the models follow the committed OpenAPI ─────────────────────────────────


def _resolve(node, root):
    while isinstance(node, dict) and "$ref" in node:
        target = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _non_null(node, root):
    node = _resolve(node, root)
    if isinstance(node, dict) and "anyOf" in node:
        branches = [_resolve(branch, root) for branch in node["anyOf"] if branch.get("type") != "null"]
        if len(branches) == 1:
            return branches[0]
    return node


def _enum(node):
    values = node.get("enum") if isinstance(node, dict) else None
    return {value for value in values if value is not None} if values else None


def _compare(model, model_root, spec, spec_root, path, problems):
    model, spec = _non_null(model, model_root), _non_null(spec, spec_root)
    if model.get("type") == "array" and spec.get("type") == "array":
        _compare(model["items"], model_root, spec["items"], spec_root, f"{path}[]", problems)
        return
    model_enum, spec_enum = _enum(model), _enum(spec)
    if model_enum is not None and spec_enum is not None and not model_enum <= spec_enum:
        problems.append(f"{path}: enum {sorted(model_enum - spec_enum)} not committed")
    if "properties" not in spec:
        return
    if "properties" not in model:
        problems.append(f"{path}: committed object, model is not")
        return
    extra = set(model["properties"]) - set(spec["properties"])
    missing = set(spec.get("required", ())) - set(model["properties"])
    problems.extend(f"{path}.{name}: not in the committed schema" for name in sorted(extra))
    problems.extend(f"{path}.{name}: committed as required" for name in sorted(missing))
    for name in sorted(set(model["properties"]) & set(spec["properties"])):
        _compare(model["properties"][name], model_root, spec["properties"][name], spec_root, f"{path}.{name}", problems)


@pytest.mark.parametrize("name", ["Now", "Plan", "Equipment"])
def test_models_follow_the_committed_openapi(name):
    spec_root = yaml.safe_load(OPENAPI.read_text())
    model_root = getattr(reads, name).model_json_schema()
    problems: list[str] = []
    _compare(model_root, model_root, spec_root["components"]["schemas"][name], spec_root, name, problems)
    assert problems == []


def test_the_routes_are_the_committed_operations():
    spec = yaml.safe_load(OPENAPI.read_text())["paths"]
    from operator_routes import reads as routes

    served = {(route.path, method, route.operation_id) for route in routes.router.routes for method in route.methods}
    assert served == {
        ("/now", "GET", "API-02"),
        ("/plan", "GET", "API-07"),
        ("/equipment", "GET", "API-13"),
        # VD-55 (tests/test_operator_routes_reads.py covers these four).
        ("/settings", "GET", "API-09"),
        ("/settings/{param}/history", "GET", "API-12"),
        ("/alerts", "GET", "API-14"),
        ("/activity", "GET", "API-17"),
        ("/water/daily", "GET", "API-21"),  # VD-57
        ("/planner/history", "GET", "API-25"),  # #1361
        ("/planner/experiments", "GET", "API-26"),
        ("/planner/experiments/{id}", "GET", "API-27"),
        ("/soil-targets", "GET", "API-SOIL-01"),  # #1345
    }
    for path, method, operation_id in served:
        assert spec[f"/v1{path}"][method.lower()]["operationId"] == operation_id


# ── the route module: no auth, no SQL ───────────────────────────────────────


def test_the_route_module_carries_no_auth_and_no_sql():
    source = (API / "operator_routes/reads.py").read_text()
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", source)
    assert not re.search(r"(?i)bearer|session|role|security|authorization", source.split('"""', 2)[2])
    from operator_routes import reads as routes

    for route in routes.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert dependencies == {routes.connection}, route.path


def test_routes_call_the_services_and_validate_hours(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from operator_routes import reads as routes

    calls = []

    async def fake_plan(conn, hours=24):
        calls.append((conn, hours))
        return reads.Plan(plan=None, no_plan_since=AT, lanes=[], manual_count=0, health=[])

    async def fake_equipment(conn):
        reason = reads.reason_state(None, reporting())
        return reads.Equipment(
            items=reads._equipment_items({}, reporting()),
            mode_reason=reason,
            fog_block_reason=reason,
            moisture_block_reason=reason,
        )

    async def fake_water(conn, days=30):
        calls.append((conn, days))
        return reads.WaterUse(from_=den(1, 12).date(), to=den(2, 12).date(), days=[])

    async def fake_connection():
        yield "conn"

    monkeypatch.setattr(reads, "plan", fake_plan)
    monkeypatch.setattr(reads, "equipment", fake_equipment)
    monkeypatch.setattr(reads, "water_daily", fake_water)
    app = FastAPI()
    app.include_router(routes.router, prefix="/v1")
    app.dependency_overrides[routes.connection] = fake_connection
    client = TestClient(app)
    assert client.get("/v1/plan").json()["no_plan_since"] == AT.isoformat().replace("+00:00", "Z")
    assert client.get("/v1/plan?hours=72").status_code == 200
    assert calls == [("conn", 24), ("conn", 72)]
    assert client.get("/v1/plan?hours=48").status_code == 422
    body = client.get("/v1/equipment").json()
    assert body["items"][0] == {
        "equipment": "fan1",
        "label": "Fan 1",
        "group": "climate",
        "line_id": None,
        "state": "unknown",
        "since": None,
        "runtime_today_s": None,
        "cycles_today": None,
        "intent": None,
        "observed_at": None,
        "manual_until": None,
        "auto_reason": None,
    }
    empty = {"code": None, "since": None, "observed_at": None}
    assert (body["mode_reason"], body["fog_block_reason"], body["moisture_block_reason"]) == (empty, empty, empty)
    calls.clear()
    assert client.get("/v1/water/daily").json() == {"from": "2026-11-01", "to": "2026-11-02", "days": []}
    assert client.get("/v1/water/daily?days=90").status_code == 200
    assert calls == [("conn", 30), ("conn", 90)]
    assert client.get("/v1/water/daily?days=45").status_code == 422


# ── API-13's reasons ────────────────────────────────────────────────────────


def test_a_reason_is_its_newest_value_with_the_start_of_its_run():
    rows = [(den(2, 9), "seal_enter"), (den(2, 10), "temp_high"), (den(2, 11), "temp_high")]
    state = reads.reason_state(track(rows), reporting())
    assert (state.code, state.since, state.observed_at) == ("temp_high", den(2, 10), den(2, 11))


def test_a_reason_that_has_not_changed_in_the_window_has_no_since():
    state = reads.reason_state(track([(den(1, 9), "none"), (den(2, 9), "none")]), reporting())
    assert (state.code, state.since, state.observed_at) == ("none", None, den(2, 9))


def test_a_reason_is_null_while_the_controller_is_not_reporting_and_keeps_its_newest_time():
    rows = [(den(2, 9), "seal_enter"), (den(2, 10), "temp_high")]
    for rep in (reporting(climate_s=301), reporting(heartbeat_s=181)):
        state = reads.reason_state(track(rows), rep)
        assert (state.code, state.since, state.observed_at) == (None, None, den(2, 10))


def test_a_reason_with_no_row_is_null_throughout():
    for empty in (None, track([])):
        state = reads.reason_state(empty, reporting())
        assert (state.code, state.since, state.observed_at) == (None, None, None)


def test_equipment_reads_the_three_reasons_in_its_snapshot():
    clock = {"heartbeat_at": AT - timedelta(seconds=30), "climate_at": AT - timedelta(seconds=20), "at": AT}
    reason_edges = [
        {"key": "mode_reason", "ts": den(2, 9), "state": "seal_enter", "newest_at": den(2, 13)},
        {"key": "mode_reason", "ts": den(2, 10), "state": "temp_high", "newest_at": den(2, 13)},
    ]
    conn = ReadsConn(
        {
            reads._REPORTING_SQL: [clock],
            reads._EQUIPMENT_TRACKS_SQL: [],
            reads._EQUIPMENT_NEWEST_SQL: [],
            reads._SYSTEM_TRACKS_SQL: reason_edges,
            reads._SYSTEM_NEWEST_SQL: [{"key": "fog_block_reason", "ts": den(1, 9), "state": "none"}],
            reads._LIGHT_TIMERS_SQL: [
                {"key": "grow_light_main", "ends_at": AT + timedelta(minutes=9)},
                {"key": "grow_light_grow", "ends_at": AT - timedelta(minutes=1)},
            ],
            reads._LIGHT_REASONS_SQL: [
                {"key": "grow_light_main", "reason": "plant_hysteresis_hold"},
                {"key": "grow_light_grow", "reason": None},
            ],
        }
    )
    result = asyncio.run(reads.equipment(conn))
    # Each light's controller reason (#1335), while the controller reports.
    reasons = {item.equipment: item.auto_reason for item in result.items if item.group == "light"}
    assert reasons == {"grow_light_main": "plant_hysteresis_hold", "grow_light_grow": None}
    assert next(args for sql, args in conn.sent if sql == reads._LIGHT_REASONS_SQL) == (
        ["grow_light_main", "grow_light_grow"],
        ["gl_main_reason", "gl_grow_reason"],
    )
    assert all(item.auto_reason is None for item in result.items if item.group != "light")
    from verdify_services import lights

    assert list(reads.LIGHT_REASON_ENTITIES.values()) == [lights.DECISION_ENTITIES[light] for light in ("main", "grow")]
    # A timed light switch reads its end while it is ahead; one already up reads none (#1377's timer sends automatic).
    lights = {item.equipment: item.manual_until for item in result.items if item.group == "light"}
    assert lights == {"grow_light_main": AT + timedelta(minutes=9), "grow_light_grow": None}
    timers = next(args for sql, args in conn.sent if sql == reads._LIGHT_TIMERS_SQL)
    assert timers == (reads.GREENHOUSE_ID, ["grow_light_main", "grow_light_grow"], reads.LIGHT_TIMER_WINDOW)
    assert (result.mode_reason.code, result.mode_reason.since) == ("temp_high", den(2, 10))
    assert result.mode_reason.observed_at == den(2, 13)
    assert (result.fog_block_reason.code, result.fog_block_reason.since) == ("none", None)
    assert result.fog_block_reason.observed_at == den(1, 9)
    assert result.moisture_block_reason.model_dump() == {"code": None, "since": None, "observed_at": None}
    sent = next(args for sql, args in conn.sent if sql == reads._SYSTEM_TRACKS_SQL)
    assert sent == (list(reads.REASON_ENTITIES), reads.STATE_WINDOW)  # bounded by the state window
    # Served as the committed OpenAPI says.
    spec = yaml.safe_load(OPENAPI.read_text())["components"]
    errors = jsonschema.Draft202012Validator(
        {"$ref": "#/components/schemas/Equipment", "components": spec}
    ).iter_errors(result.model_dump(mode="json"))
    assert [error.message for error in errors] == []


def test_the_reasons_are_controller_entities_the_ingestor_writes():
    from_map = (ROOT / "ingestor/entity_map.py").read_text()
    for entity in reads.REASON_ENTITIES:
        assert f'"{entity}": "{entity}"' in from_map, entity


# ── API-21 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("quality", "coverage"),
    [
        ("ok", "complete"),
        ("unavailable", "no_data"),
        (None, "no_data"),
        ("partial_day", "partial"),
        ("discontinuous", "partial"),
        ("incomplete", "partial"),
        ("ledger_incomplete", "partial"),
    ],
)
def test_water_coverage_follows_the_meter_views_quality(quality, coverage):
    assert reads.water_coverage(quality) == coverage


def water_fixture():
    first = date(2026, 10, 29)
    meter = {
        date(2026, 10, 29): {"quality": "ok", "gal": 78},
        date(2026, 10, 30): {"quality": "unavailable", "gal": 0},  # the view coalesces it to 0
        date(2026, 11, 1): {"quality": "discontinuous", "gal": 2},
        date(2026, 11, 2): {"quality": "partial_day", "gal": 8},
    }
    runs = [
        {"local_day": date(2026, 10, 29), "line_id": "L11", "cause": "vpd", "gal": 37},
        {"local_day": date(2026, 10, 29), "line_id": "L01", "cause": "schedule", "gal": 4},
        {"local_day": date(2026, 10, 29), "line_id": "L01", "cause": "run_now", "gal": 1},
        {"local_day": date(2026, 10, 29), "line_id": "L04", "cause": "vpd", "gal": 0},
        {"local_day": date(2026, 10, 29), "line_id": "L03", "cause": "feed", "gal": None},  # the supply valve
        {"local_day": date(2026, 10, 30), "line_id": "L11", "cause": "vpd", "gal": 3},
    ]
    no_valve = {date(2026, 10, 29): 41, date(2026, 10, 30): 0, date(2026, 11, 1): 1, date(2026, 11, 2): 0}
    return first, date(2026, 11, 2), meter, runs, no_valve


def test_water_days_are_oldest_first_with_the_no_valve_gallons_apart_and_gaps_null():
    days = reads.water_days(*water_fixture())
    assert [day.date for day in days] == [date(2026, 10, 29) + timedelta(days=n) for n in range(5)]
    assert days[0].model_dump(exclude={"date"}) == {
        "coverage": "complete",
        "meter_gal": 78,
        "by_line": {"L11": 37, "L01": 5, "L04": 0},
        "by_cause": {"vpd": 37, "schedule": 4, "run_now": 1},
        "by_purpose": {"climate": 37, "irrigation": 5},  # #1348: fog and VPD misting apart from watering
        "no_valve_gal": 41,
    }
    nothing = {"meter_gal": None, "by_line": None, "by_cause": None, "by_purpose": None, "no_valve_gal": None}
    assert days[1].model_dump(exclude={"date"}) == {"coverage": "no_data", **nothing}  # unavailable: never 0
    assert days[2].model_dump(exclude={"date"}) == {"coverage": "no_data", **nothing}  # no row
    assert days[3].model_dump(exclude={"date"}) == {
        "coverage": "partial",
        "meter_gal": 2,
        "by_line": {},  # a day with data and no run
        "by_cause": {},
        "by_purpose": {},
        "no_valve_gal": 1,
    }
    assert days[4].coverage == "partial" and days[4].meter_gal == 8


def test_water_daily_reads_each_view_by_day_in_one_snapshot():
    first, last, meter, runs, no_valve = water_fixture()
    window = []

    def meter_rows(greenhouse, low, high):
        window.append((greenhouse, low, high))
        return [{"date": day, **row} for day, row in meter.items()]

    conn = ReadsConn(
        {
            reads._WATER_METER_SQL: meter_rows,
            reads._WATER_NO_VALVE_SQL: [{"date": day, "gal": gal} for day, gal in no_valve.items()],
            reads._WATER_RUNS_SQL: runs,
        }
    )
    result = asyncio.run(reads.water_daily(conn, days=30))
    today = AT.astimezone(DENVER).date()
    assert (result.from_, result.to) == (today - timedelta(days=29), today) and len(result.days) == 30
    assert result.days[-1].date == today == last and result.days[0].date == today - timedelta(days=29)
    assert window == [("vallery", today - timedelta(days=29), today)]
    assert {args[1:] for _, args in conn.sent} == {(today - timedelta(days=29), today)}
    assert len(asyncio.run(reads.water_daily(conn, days=90)).days) == 90
    with pytest.raises(ValueError):
        asyncio.run(reads.water_daily(conn, days=45))
    for sql in (reads._WATER_METER_SQL, reads._WATER_NO_VALVE_SQL, reads._WATER_RUNS_SQL):
        assert "$2::date" in sql and "$3::date" in sql, sql  # bounded by day
    assert "meter_id = 'main_pulse'" in reads._WATER_METER_SQL
    spec = yaml.safe_load(OPENAPI.read_text())["components"]
    errors = jsonschema.Draft202012Validator({"$ref": "#/components/schemas/WaterUse", "components": spec}).iter_errors(
        result.model_dump(mode="json", by_alias=True)
    )
    assert [error.message for error in errors] == []


# ── boundaries: imports, writes and bounded queries ─────────────────────────


def test_verdify_services_imports_only_schemas_asyncpg_and_pydantic():
    # pydantic_core is pydantic's own core package (verdify_services.activity uses to_jsonable_python).
    allowed = {"verdify_schemas", "verdify_services", "asyncpg", "pydantic", "pydantic_core"} | set(
        sys.stdlib_module_names
    )
    for path in sorted((ROOT / "verdify_services").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert roots <= allowed, (path.name, roots)


def test_the_ingestor_never_imports_verdify_services():
    for path in sorted((ROOT / "ingestor").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.split(".")[0] == "verdify_services" for name in names), path


def test_the_reads_never_write():
    for name, sql in vars(reads).items():
        if name.endswith("_SQL"):
            assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|CREATE|DROP)\b", sql), name


HYPERTABLES = (
    "climate",
    "diagnostics",
    "equipment_state",
    "setpoint_changes",
    "setpoint_clamps",
    "setpoint_plan",
    "setpoint_snapshot",
    "system_state",
)


def test_every_hypertable_query_is_bounded_by_ts():
    """Each FROM <hypertable> carries a ts bound up to its next FROM; the writer age is the one exception."""
    unbounded = []
    for name, sql in vars(reads).items():
        if not name.endswith("_SQL"):
            continue
        for match in re.finditer(r"\bFROM\s+([a-z_]+)", sql):
            if match.group(1) not in HYPERTABLES:
                continue
            following = sql[match.end() :]
            scope = following[: following.find(" FROM ")] if " FROM " in following else following
            if not re.search(r"\bts\s*(>|>=|<|<=)", scope):
                unbounded.append(f"{name}: {match.group(1)}")
    assert unbounded == ["_CLOCK_SQL: setpoint_changes"]
    assert "SELECT ts FROM setpoint_changes ORDER BY ts DESC LIMIT 1" in reads._CLOCK_SQL


# ── VD-55: the settings, history and activity compositions ──────────────────


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.depth += 1

    async def __aexit__(self, *exc):
        self.conn.depth -= 1
        return False


class ReadsConn:
    """Answers each statement a read sends from ``answers``, keyed by the module's SQL constant."""

    def __init__(self, answers, at=AT):
        self.answers = answers
        self.at = at
        self.depth = 0
        self.sent = []

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self, **_kwargs):
        return _Tx(self)

    def _rows(self, sql, args):
        assert self.depth > 0, "every read runs in its snapshot"
        self.sent.append((sql, args))
        answer = self.answers[sql]
        return answer(*args) if callable(answer) else answer

    async def fetch(self, sql, *args):
        return self._rows(sql, args)

    async def fetchrow(self, sql, *args):
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        if sql == "SELECT now()":
            assert self.depth > 0
            return self.at
        rows = self._rows(sql, args)
        return next(iter(rows[0].values())) if rows else None


def catalog_row(param, **update):
    spec = REGISTRY[param]
    row = settings.Setting(
        param=param,
        label=spec.label,
        control_class=spec.control_class,
        group=spec.control_class,
        kind=spec.kind,
        unit=spec.unit,
        writable=True,
        readback_via="cfg",
        readback=None,
        desired=None,
        applied_from="plan",
        state="confirmed",
    )
    return row.model_copy(update=update)


def plan_db_row(parameter, value, ts, created_at, plan_id="iris-20261102-0634", source="iris"):
    return {
        "parameter": parameter,
        "value": value,
        "ts": ts,
        "plan_id": plan_id,
        "created_at": created_at,
        "source": source,
        "is_active": True,
    }


@pytest.mark.parametrize(
    ("zone", "role", "label", "unit"),
    [
        ("house", "temp_low", "House temp low", "°F"),
        ("house", "temp_target", "House temp target", "°F"),
        ("center", "temp_high", "Center temp high", "°F"),
        ("south", "vpd_low", "South VPD low", "kPa"),
        ("east", "vpd_target", "East VPD target", "kPa"),
        ("west", "vpd_high", "West VPD high", "kPa"),
    ],
)
def test_a_band_in_force_row_is_read_only_derived_with_its_own_label_and_unit(zone, role, label, unit):
    row = reads.band_in_force_row(zone, role, 1.067, AT - timedelta(minutes=2))
    assert (row.param, row.label, row.unit) == (f"band_{zone}_{role}", label, unit)
    assert (row.group, row.control_class, row.kind) == ("band_in_force", "crop_band", "numeric")
    assert (row.writable, row.read_only_reason) == (False, "derived by the dispatcher from the crop band anchors")
    assert (row.readback_via, row.desired, row.plan, row.applied_from, row.state) == (
        "none",
        None,
        None,
        "band",
        "no_readback",
    )
    assert row.readback == settings.Reading(value=1.067, observed_at=AT - timedelta(minutes=2))
    assert (row.min, row.max, row.step, row.options) == (None, None, None, [])
    missing = reads.band_in_force_row(zone, role, None, None).readback
    assert (missing.value, missing.observed_at, missing.reason) == (None, None, reads.NO_BAND_ROW)


def test_the_band_in_force_names_are_the_dispatchers_and_never_a_registry_row():
    names = [f"band_{zone}_{role}" for zone in reads.BAND_ZONES for role in reads.BAND_ROLES]
    assert reads.BAND_ZONES == ("house", "center", "south", "west", "east") and len(reads.BAND_ROLES) == 6
    assert len(names) == 30 and not set(names) & set(REGISTRY)
    assert reads.BAND_ROLES == ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high")


def test_the_plan_block_keeps_iris_values_as_stored():
    row = plan_db_row("mister_engage_delay_s", 48.75, AT - timedelta(hours=2), AT - timedelta(hours=2))
    later = [reads.PlanWaypoint(at=AT + timedelta(hours=3), value=60.0, source="iris", effective=True)]
    block = reads.setting_plan(row, later)
    assert block.model_dump() == {
        "value": 48.75,  # never snapped to the 30 s step
        "plan_id": "iris-20261102-0634",
        "source": "iris",
        "written_at": AT - timedelta(hours=2),
        "next": later[0].model_dump(),
    }
    assert reads.setting_plan({**row, "source": "preemptive"}, []).next is None
    assert reads.setting_plan({**row, "source": "operator"}, later) is None  # not a value a plan applies


def test_a_full_plan_row_is_superseded_once_a_newer_full_plan_was_written_by_its_time():
    full_plans = [(AT - timedelta(hours=8), "iris-20261102-0634"), (AT - timedelta(hours=5), "iris-newer")]
    row = plan_db_row("mister_engage_kpa", 1.5, AT - timedelta(hours=6), AT - timedelta(hours=8))
    assert reads.superseded_at(row, full_plans) is False  # written 8 h ago, its 6 h ago row: still in force
    assert reads.superseded_at({**row, "ts": AT - timedelta(hours=5)}, full_plans) is True  # at the newer write
    assert reads.superseded_at({**row, "ts": AT + timedelta(hours=1)}, full_plans) is True
    newest = {**row, "created_at": AT - timedelta(hours=5), "ts": AT + timedelta(hours=1)}
    assert reads.superseded_at(newest, full_plans) is False
    for other in (
        {"plan_id": "iris-oneshot-20261102-1347"},
        {"plan_id": "preemptive-20261102", "source": "preemptive"},
    ):
        assert reads.superseded_at({**row, "ts": AT + timedelta(hours=1), **other}, full_plans) is False


def test_a_full_plans_own_journal_row_never_supersedes_it():
    """Older plans wrote their journal row after their waypoints (60 plans up to 2026-04-28, at most 54 s later)."""
    written = AT - timedelta(hours=8)
    own = (written + timedelta(seconds=1), "iris-20261102-0634")
    newer = (AT - timedelta(hours=5), "iris-newer")
    row = plan_db_row("mister_engage_kpa", 1.5, AT - timedelta(hours=6), written)
    assert reads.superseded_at(row, [own]) is False
    assert reads.superseded_at(row, [own, newer]) is False  # the newer plan came after this row's time
    assert reads.superseded_at({**row, "ts": AT - timedelta(hours=4)}, [own, newer]) is True


def test_plan_history_marks_superseded_and_overridden_waypoints():
    param = "mister_engage_kpa"
    plan_a, plan_b = AT - timedelta(hours=8), AT - timedelta(hours=5)
    rows = [
        plan_db_row(param, 1.40, AT - timedelta(hours=8), plan_a, "iris-a"),
        plan_db_row(param, 1.60, AT - timedelta(hours=5), plan_b, "iris-b"),
        plan_db_row(param, 1.70, AT - timedelta(hours=1), AT - timedelta(hours=1), "preemptive-x", "preemptive"),
        plan_db_row(param, 1.50, AT + timedelta(hours=1), plan_a, "iris-a"),
        plan_db_row(param, 1.65, AT + timedelta(hours=3), plan_b, "iris-b"),
    ]
    desired = [
        settings.DesiredSetting(
            id=3,
            param=param,
            value=1.55,
            manual=False,
            state="overridden",
            actor=ActorRef(source="agent:steward", name="steward"),
            created_at=AT - timedelta(hours=2),
        )
    ]
    conn = ReadsConn(
        {
            reads._PLAN_HISTORY_SQL: rows,
            reads._FULL_PLANS_SQL: [{"created_at": plan_b, "plan_id": "iris-b"}],
        }
    )

    async def run():
        async with conn.transaction():
            return await reads.plan_history(conn, param, AT - timedelta(hours=10), AT + timedelta(hours=6), desired)

    waypoints = asyncio.run(run())
    assert [(point.at, point.value, point.source, point.effective) for point in waypoints] == [
        (AT - timedelta(hours=8), 1.40, "iris", True),
        (AT - timedelta(hours=5), 1.60, "iris", True),
        (AT - timedelta(hours=1), 1.70, "preemptive", True),  # written after the desired row: it wins
        (AT + timedelta(hours=1), 1.50, "iris", False),  # plan A was superseded by plan B
        (AT + timedelta(hours=3), 1.65, "iris", False),  # the non-manual desired row is newer than plan B
    ]
    (_, history_args), (_, plans_args) = conn.sent
    assert history_args == (param, AT - timedelta(hours=10), AT + timedelta(hours=6), ["iris", "preemptive"])
    assert plans_args == (plan_a, AT + timedelta(hours=3))  # only plans written after the oldest row, up to the newest


def test_plan_history_of_an_empty_window_sends_one_statement():
    conn = ReadsConn({reads._PLAN_HISTORY_SQL: []})

    async def run():
        async with conn.transaction():
            return await reads.plan_history(conn, "mister_engage_kpa", AT - timedelta(hours=1), AT, [])

    assert asyncio.run(run()) == [] and len(conn.sent) == 1


def test_settings_rows_adds_the_plan_block_the_next_waypoint_and_the_band_in_force(monkeypatch):
    delay = "mister_engage_delay_s"
    written = AT - timedelta(hours=2)

    async def fake_catalog(conn):
        assert conn.is_in_transaction()  # the catalog reads inside the same snapshot
        return [catalog_row(delay), catalog_row("mister_engage_kpa", applied_from="default", state="no_readback")]

    monkeypatch.setattr(settings, "catalog", fake_catalog)
    band = [
        {"zone": zone, "role": role, "value": 75.0 if role.startswith("temp") else 1.0, "ts": AT - timedelta(minutes=2)}
        for zone in reads.BAND_ZONES
        for role in reads.BAND_ROLES
        if (zone, role) != ("west", "vpd_high")
    ]
    conn = ReadsConn(
        {
            reads._SETTING_PLANS_SQL: [plan_db_row(delay, 48.75, written, written)],
            reads._PLAN_WAYPOINTS_SQL: [
                plan_db_row(delay, 48.75, AT, written),  # at now: not next
                plan_db_row(delay, 60.0, AT + timedelta(hours=3), written),
                plan_db_row(delay, 90.0, AT + timedelta(hours=9), written),
            ],
            reads._DESIRED_SQL: [],
            reads._BAND_IN_FORCE_SQL: band,
        }
    )
    result = asyncio.run(reads.settings_rows(conn))
    assert result.server_time == AT
    rows = {item.param: item for item in result.items}
    assert rows[delay].plan == settings.SettingPlan(
        value=48.75,
        plan_id="iris-20261102-0634",
        source="iris",
        written_at=written,
        next=settings.PlanWaypoint(at=AT + timedelta(hours=3), value=60.0, source="iris", effective=True),
    )
    assert rows["mister_engage_kpa"].plan is None
    assert [item.param for item in result.items[2:]] == [
        f"band_{zone}_{role}" for zone in reads.BAND_ZONES for role in reads.BAND_ROLES
    ]
    assert rows["band_west_vpd_high"].readback.reason == reads.NO_BAND_ROW
    assert rows["band_house_temp_low"].readback.value == 75.0
    sent = dict(conn.sent)
    assert sent[reads._PLAN_WAYPOINTS_SQL] == (AT, AT + reads.NEXT_WAYPOINT_HORIZON, [delay])
    zones, roles, params, window = sent[reads._BAND_IN_FORCE_SQL]
    assert (len(zones), params[0], params[-1], window) == (
        30,
        "band_house_temp_low",
        "band_east_vpd_high",
        timedelta(hours=1),
    )
    assert _compare_model(reads.SettingRows, response_schema_of("/v1/settings")) == []


def test_setting_history_adds_the_plan_values_over_the_same_window(monkeypatch):
    start, end = AT - timedelta(days=1), AT + timedelta(hours=12)
    calls = []

    async def fake_history(conn, param, from_, to):
        calls.append((param, from_, to))
        return settings.SettingHistory.model_validate(
            {
                "param": param,
                "unit": "kPa",
                "min": 0.5,
                "max": 2.5,
                "from": from_,
                "to": to,
                "desired": [],
                "readback": [],
            }
        )

    monkeypatch.setattr(settings, "history", fake_history)
    row = plan_db_row(
        "mister_engage_kpa", 1.7, AT - timedelta(hours=3), AT - timedelta(hours=3), "preemptive-x", "preemptive"
    )
    conn = ReadsConn({reads._PLAN_HISTORY_SQL: [row], reads._FULL_PLANS_SQL: []})
    result = asyncio.run(reads.setting_history(conn, "mister_engage_kpa", start, end))
    assert calls == [("mister_engage_kpa", start, end)]
    assert result.plan == [
        settings.PlanWaypoint(at=AT - timedelta(hours=3), value=1.7, source="preemptive", effective=True)
    ]
    assert (result.from_, result.to) == (start, end)


def test_activity_page_fills_the_readback_state_of_setting_writes_only(monkeypatch):
    def entry(entry_id, action, detail):
        return activity.ActivityEntry(
            id=entry_id,
            ts=AT - timedelta(minutes=entry_id),
            actor=ActorRef(source="agent:steward", name="steward"),
            action=action,
            target="mister_engage_kpa",
            detail=detail,
        )

    page = activity.ActivityPage(
        items=[
            entry(1, "setting.write", {"desired_id": 41}),
            entry(2, "setting.release", {"desired_id": 40}),
            entry(3, "setting.write", {"desired_id": True}),  # not an id
            entry(4, "alert.ack", {"desired_id": 41}),  # not a setting write
            entry(5, "setting.write", {"desired_id": 7}),  # names no row
        ],
        next_cursor="next",
    )
    listed, named = [], []

    async def fake_list(conn, **filters):
        listed.append(filters)
        return page

    async def fake_states(conn, ids):
        named.append(ids)
        confirmed = settings.DesiredSetting(
            id=41,
            param="mister_engage_kpa",
            value=1.55,
            manual=False,
            state="confirmed",
            confirmed_at=AT - timedelta(minutes=1),
            actor=ActorRef(source="agent:steward", name="steward"),
            created_at=AT - timedelta(minutes=3),
        )
        return {
            41: confirmed,
            40: confirmed.model_copy(update={"id": 40, "value": None, "state": "released", "confirmed_at": None}),
        }

    monkeypatch.setattr(activity, "list", fake_list)
    monkeypatch.setattr(settings, "desired_states", fake_states)
    conn = ReadsConn({})
    result = asyncio.run(reads.activity_page(conn, source="agent:steward", limit=5, cursor="c"))
    assert listed == [
        {
            "source": "agent:steward",
            "actor": None,
            "action": None,
            "target": None,
            "from_": None,
            "to": None,
            "limit": 5,
            "cursor": "c",
        }
    ]
    assert named == [[7, 40, 41]]
    assert [(item.id, item.state, item.confirmed_at) for item in result.items] == [
        (1, "confirmed", AT - timedelta(minutes=1)),
        (2, "released", None),
        (3, None, None),
        (4, None, None),
        (5, None, None),
    ]
    assert result.next_cursor == "next"


def test_plan_lanes_take_applied_from_and_the_desired_state_from_settings(monkeypatch):
    held = settings.SettingStatus(
        param="site_pressure_hpa",
        applied_from="manual",
        state="held",
        state_reason=settings.StateReason(code="clamped", applied_value=840.0, since=AT - timedelta(minutes=9)),
        readback_via="cfg",
        readback=None,
        desired=settings.DesiredSetting(
            id=12,
            param="site_pressure_hpa",
            value=845.0,
            manual=True,
            state="held",
            state_reason=settings.StateReason(code="clamped", applied_value=840.0, since=AT - timedelta(minutes=9)),
            actor=ActorRef(source="dashboard", name="jason"),
            created_at=AT - timedelta(minutes=10),
        ),
    )
    asked = []

    async def fake_statuses(conn, params):
        asked.append(list(params))
        return {"site_pressure_hpa": held}

    monkeypatch.setattr(settings, "statuses", fake_statuses)
    manual_row = {
        "id": 12,
        "param": "site_pressure_hpa",
        "value": 845.0,
        "manual": True,
        "source": "dashboard",
        "actor": "jason",
        "reason": None,
        "created_at": AT - timedelta(minutes=10),
    }
    conn = ReadsConn(
        {
            reads._PLAN_IN_FORCE_SQL: [],
            reads._NO_PLAN_SINCE_SQL: [{"max": AT - timedelta(hours=1)}],
            reads._ACTIVE_PLAN_SQL: [
                plan_db_row("not_a_tunable", 1.0, AT - timedelta(hours=1), AT - timedelta(hours=1))
            ],
            reads._DESIRED_SQL: [manual_row],
            reads._PLAN_WAYPOINTS_SQL: [],
            reads._DEVICE_READBACKS_SQL: [],
            reads._HEALTH_SQL: [],
        }
    )
    result = asyncio.run(reads.plan(conn))
    lanes = {lane.param: lane for lane in result.lanes}
    assert asked == [["not_a_tunable", "site_pressure_hpa"]]
    assert lanes["site_pressure_hpa"].desired is held.desired and lanes["site_pressure_hpa"].applied_from == "manual"
    assert (lanes["not_a_tunable"].applied_from, lanes["not_a_tunable"].desired) == ("plan", None)
    assert result.manual_count == 1


def test_the_one_open_alert_count_is_alerts_open_count():
    assert reads.AlertCounts is alerts.AlertCounts
    assert reads.Now.model_fields["alerts"].annotation is alerts.AlertCounts
    assert "v_open_alerts" not in reads._ALERT_FACTS_SQL and alerts.OPEN_PREDICATE in reads._ALERT_FACTS_SQL
    assert not hasattr(reads, "_ALERTS_SQL")


def test_one_model_per_openapi_schema():
    assert reads.DesiredSetting is settings.DesiredSetting and reads.PlanWaypoint is settings.PlanWaypoint
    assert reads.PlanLane.model_fields["desired"].annotation == settings.DesiredSetting | None
    assert reads.ActorRef is ActorRef


@pytest.mark.parametrize("first", ["settings", "reads", "alerts", "activity"])
def test_the_services_import_in_any_order(first):
    """reads imports settings and settings calls reads.reporting(): neither import order may break (VD-58's MCP)."""
    code = f"import verdify_services.{first}; import verdify_services.reads, verdify_services.settings"
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, env={**os.environ, "PYTHONPATH": str(ROOT)})


def response_schema_of(path):
    spec = yaml.safe_load(OPENAPI.read_text())
    return spec, spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


def _compare_model(model, spec_and_schema):
    spec_root, schema = spec_and_schema
    model_root = model.model_json_schema()
    problems: list[str] = []
    _compare(model_root, model_root, schema, spec_root, model.__name__, problems)
    return problems


def test_words_for_a_person_name_lines_never_relay_codes():
    """#1341: a refusal's detail reads the line's name; ``line_id`` stays in the structured fields."""
    assert name_line_codes("L01 is disabled; enable it to run it") == "Wall drip is disabled; enable it to run it"
    assert name_line_codes("vpd_member is on L04, L07 and L09 only") == (
        "vpd_member is on Center mister, South mister and West mister only"
    )
    assert name_line_codes("only L01, L02 and L04-L10 do") == (
        "only Wall drip, Wall drip (fertilized) and Center mister through West mister (fertilized) do"
    )
    assert name_line_codes("L12 and XL01 stay") == "L12 and XL01 stay"
