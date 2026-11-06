"""API-22, the grow lights by day for Graphs → Lighting (#1357): DB-free, with a connection that answers by SQL marker."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from tests.test_verdify_services_series import FakeConnection
from verdify_services import lighting as lt
from verdify_services import series as s
from verdify_services.problems import ServiceError

MIN = timedelta(minutes=1)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


POLICY = {"lux_on": 40000.0, "hysteresis": 8000.0, "start_hour": 7.0, "cutoff_hour": 20.0, "target_min": 780.0}


def test_local_hours_follow_the_denver_wall_clock_across_the_fall_back():
    assert lt.local_hour(date(2026, 11, 5), 7) == at("2026-11-05T14:00:00+00:00")  # MST
    assert lt.local_hour(date(2026, 10, 30), 7) == at("2026-10-30T13:00:00+00:00")  # MDT
    assert lt.local_hour(date(2026, 11, 5), 24) == at("2026-11-06T07:00:00+00:00")


def test_a_day_counts_on_time_starts_and_minutes_with_enough_light_or_the_light_on():
    day = date(2026, 11, 5)
    midnight = s.denver_midnight(day)
    seven = lt.local_hour(day, 7)
    # Off at midnight, on 06:00-08:00, off, on 18:00-21:00.
    rows = [
        (midnight + timedelta(hours=6), True),
        (midnight + timedelta(hours=8), False),
        (midnight + timedelta(hours=18), True),
        (midnight + timedelta(hours=21), False),
    ]
    spans = lt.on_spans(False, rows, midnight, midnight + timedelta(days=1))
    # Bright from 12:00 to 14:00 (120 minutes), one dim minute at 12:30.
    lux = {seven + timedelta(hours=5) + n * MIN: 50000.0 for n in range(120)}
    lux[seven + timedelta(hours=5, minutes=30)] = 39999.0
    facts = lt.day_facts(spans, lux, POLICY, day, now=midnight + timedelta(days=2))
    assert (facts.on_s, facts.starts, facts.partial) == (5 * 3600, 2, False)
    assert (facts.start_hour, facts.cutoff_hour, facts.lux_on, facts.lux_off, facts.target_min) == (
        7,
        20,
        40000.0,
        48000.0,
        780,
    )
    # The window is 07:00-20:00: 60 lit minutes (07-08), 119 bright, 120 lit (18-20).
    assert (facts.window_min, facts.qualified_min) == (13 * 60, 60 + 119 + 120)


def test_today_counts_up_to_now_and_a_day_without_a_policy_readback_has_no_minutes():
    day = date(2026, 11, 5)
    midnight = s.denver_midnight(day)
    now = lt.local_hour(day, 9)
    spans = lt.on_spans(True, [], midnight, now)
    facts = lt.day_facts(spans, {}, POLICY, day, now=now)
    assert (facts.partial, facts.on_s, facts.starts, facts.window_min, facts.qualified_min) == (
        True,
        9 * 3600,
        0,
        120,
        120,
    )
    bare = lt.day_facts(spans, {}, {**POLICY, "lux_on": None, "hysteresis": None}, day, now=now)
    assert (bare.window_min, bare.qualified_min, bare.lux_off) == (None, None, None)


def test_lighting_days_reads_the_policy_state_and_lux_once_and_answers_each_circuit():
    now = at("2026-11-05T21:00:00+00:00")  # 14:00 MST
    from_, to = now - timedelta(days=1), now + timedelta(days=1)
    days = [date(2026, 11, 4), date(2026, 11, 5)]
    policy_rows = [
        {"day": day, "parameter": lt.parameter(light, field), "value": value}
        for day in days
        for light in ("main", "grow")
        for field, value in POLICY.items()
    ]
    seven = lt.local_hour(days[1], 7)
    conn = FakeConnection(
        [
            ("setpoint_snapshot", policy_rows),
            ("fn_equip_at", [{"equipment": "grow_light_main", "state": False}]),
            ("SELECT equipment, ts, state", [{"equipment": "grow_light_main", "ts": seven, "state": True}]),
            ("outdoor_lux", [{"minute": seven + n * MIN, "lux": 41000.0} for n in range(30)]),
        ]
    )
    result = asyncio.run(lt.lighting_days(conn, from_, to, now=now))
    assert conn.readonly is True
    main, grow = result.circuits
    assert (main.light, main.equipment, main.label, grow.equipment) == (
        "main",
        "grow_light_main",
        "Main",
        "grow_light_grow",
    )
    assert [day.date for day in main.days] == days and [day.partial for day in main.days] == [False, True]
    # Main has been on since 07:00 today; grow never had a row, so it reads off.
    assert (main.days[0].on_s, main.days[1].on_s, main.days[1].starts) == (0, 7 * 3600, 1)
    assert (main.days[1].window_min, main.days[1].qualified_min) == (7 * 60, 7 * 60)
    assert (grow.days[1].on_s, grow.days[1].qualified_min) == (0, 30)
    policy_sql, policy_args = conn.fetches[0]
    assert policy_args[0] == days and policy_args[1] == [s.denver_midnight(days[1]), now]
    assert lt.POLICY_LOOKBACK == policy_args[2] and len(policy_args[3]) == 10
    assert conn.fetches[-1][1] == (s.denver_midnight(days[0]), now, 40000.0)


def test_a_window_after_now_reads_nothing_and_a_long_window_is_refused():
    now = at("2026-11-05T21:00:00+00:00")
    conn = FakeConnection()
    future = asyncio.run(lt.lighting_days(conn, now + MIN, now + timedelta(hours=2), now=now))
    assert [circuit.days for circuit in future.circuits] == [[], []] and conn.fetches == []
    with pytest.raises(ServiceError) as caught:
        asyncio.run(lt.lighting_days(conn, now - timedelta(days=32), now, now=now))
    assert caught.value.problem.code == "window_too_large"


def test_the_response_model_follows_the_committed_schema():
    from tests.test_verdify_services_series import OPENAPI

    committed = OPENAPI["components"]["schemas"]["LightingDays"]
    schema = lt.LightingDays.model_json_schema(by_alias=True, mode="serialization")
    assert set(schema["properties"]) == set(committed["properties"]) and set(committed["required"]) <= set(
        schema["properties"]
    )
    day = committed["properties"]["circuits"]["items"]["properties"]["days"]["items"]
    assert set(lt.LightingDay.model_fields) == set(day["properties"]) == set(day["required"])
