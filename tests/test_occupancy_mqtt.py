"""#1370: Frigate person counts over MQTT feed the occupancy latch, and the controller's occupancy lights are logged.

No broker, database or controller: fake MQTT messages, a fake clock and an in-memory system_state.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR = REPO_ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))
for key, value in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "test",
}.items():
    os.environ.setdefault(key, value)

import occupancy  # noqa: E402
import occupancy_lights  # noqa: E402
import occupancy_mqtt  # noqa: E402

from verdify_schemas.activity import ActivityLogRow  # noqa: E402

TOPIC = "frigate/greenhouse_zone/person"
T0 = datetime(2026, 11, 5, 23, 0, tzinfo=UTC)


def message(payload: str, topic: str = TOPIC, retain: bool = False) -> SimpleNamespace:
    """A paho MQTTMessage as on_message sees it."""
    return SimpleNamespace(topic=topic, payload=payload.encode(), retain=retain)


def feed(tracker, msg, minute: float):
    return tracker.on_message(msg.topic, msg.payload, msg.retain, T0 + timedelta(minutes=minute))


# ── The tracker: count messages to sightings ────────────────────────────────


def test_default_topic_is_frigates_greenhouse_zone_person_count():
    assert occupancy_mqtt._topics() == (TOPIC,)


@pytest.mark.parametrize(
    ("payload", "count"), [("0", 0), ("1", 1), (" 3\n", 3), ("ON", None), ("-1", None), ("", None)]
)
def test_parse_person_count(payload, count):
    assert occupancy_mqtt.parse_person_count(payload.encode()) == count


def test_rising_edge_starts_a_visit_and_the_drop_to_zero_is_the_last_sighting():
    tracker = occupancy_mqtt.FrigatePersonTracker((TOPIC,))

    first = feed(tracker, message("1"), 0)
    assert first == occupancy_mqtt.Sighting(T0, T0, edge=True)
    second = feed(tracker, message("2"), 3)
    assert second.edge is False and second.visit_started_at == T0
    last = feed(tracker, message("0"), 7)
    assert last == occupancy_mqtt.Sighting(T0 + timedelta(minutes=7), T0, edge=False)
    # A zero with nobody present records nothing.
    assert feed(tracker, message("0"), 8) is None
    # The next sighting is a new visit.
    again = feed(tracker, message("1"), 20)
    assert again.edge is True and again.visit_started_at == T0 + timedelta(minutes=20)


@pytest.mark.parametrize(
    "msg",
    [message("1", retain=True), message("1", topic="frigate/patio_2/person"), message("unavailable")],
)
def test_retained_foreign_and_malformed_messages_are_ignored(msg):
    tracker = occupancy_mqtt.FrigatePersonTracker((TOPIC,))
    assert feed(tracker, msg, 0) is None
    assert not tracker.present


def test_heartbeat_re_records_a_still_visit_until_the_visit_cap():
    tracker = occupancy_mqtt.FrigatePersonTracker((TOPIC,))
    feed(tracker, message("1"), 0)

    assert tracker.heartbeat(T0 + timedelta(seconds=30)) is None  # too soon
    beat = tracker.heartbeat(T0 + timedelta(minutes=1))
    assert beat == occupancy_mqtt.Sighting(T0 + timedelta(minutes=1), T0, edge=False)
    assert tracker.heartbeat(T0 + timedelta(minutes=occupancy.OCCUPANCY_VISIT_MAX_MIN)) is None
    tracker.reset()  # a lost connection forgets the count
    assert tracker.heartbeat(T0 + timedelta(minutes=5)) is None


# ── The latch on a fake clock: real time in, 15 min after the last sighting out ──


class _Clock:
    now = T0


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now if tz is None else _Clock.now.astimezone(tz)


class _Store:
    def __init__(self):
        self.rows: list[tuple[datetime, str, str]] = []

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, entity, value):
        assert "v_runtime_system_state_write" in sql
        self.rows.append((_Clock.now, entity, value))

    async def fetch(self, sql, entities):
        assert "FROM system_state" in sql
        latest = {entity: value for _ts, entity, value in self.rows if entity in entities}
        return [{"entity": entity, "value": value} for entity, value in latest.items()]

    def occupied_at(self, at: datetime) -> bool:
        state = {entity: value for ts, entity, value in self.rows if ts <= at}
        return occupancy._effective_occupancy_from_state(state, at)


@pytest.fixture
def latch(monkeypatch):
    store = _Store()
    pushes: list[tuple[datetime, bool, str]] = []

    async def fake_push(occupied, source):
        pushes.append((_Clock.now, occupied, source))
        return 1

    monkeypatch.setattr(occupancy, "datetime", _FakeDatetime)
    monkeypatch.setattr(occupancy, "push_occupancy_to_esp32", fake_push)
    return SimpleNamespace(store=store, pushes=pushes)


def _at(minute: float) -> datetime:
    _Clock.now = T0 + timedelta(minutes=minute)
    return _Clock.now


def test_first_mqtt_message_pushes_occupied_at_once_and_the_latch_ends_fifteen_minutes_after_the_last(latch):
    tracker = occupancy_mqtt.FrigatePersonTracker((TOPIC,))

    async def deliver(msg, minute):
        sighting = tracker.on_message(msg.topic, msg.payload, msg.retain, _at(minute))
        if sighting is not None:
            await occupancy_mqtt.record_sighting(latch.store, sighting)

    asyncio.run(deliver(message("1"), 0))
    # The controller is told in the same step as the message, not at the next poll.
    assert latch.pushes == [(T0, True, occupancy_mqtt.SOURCE)]

    asyncio.run(deliver(message("0"), 6))
    # The Home Assistant fallback poll sees OFF a minute later: it does not cut the latch short.
    _at(7)
    asyncio.run(occupancy.sync_occupancy_state(latch.store, False, "ha_sensor_sync"))
    assert latch.store.occupied_at(T0 + timedelta(minutes=20, seconds=59))
    assert not latch.store.occupied_at(T0 + timedelta(minutes=21))

    _at(21)
    assert asyncio.run(occupancy.expire_occupancy_latch(latch.store)) is True
    assert latch.pushes[-1] == (T0 + timedelta(minutes=21), False, "occupancy_watchdog")


def test_an_off_reading_after_the_latch_ran_out_releases(latch):
    _at(0)
    asyncio.run(occupancy.sync_occupancy_state(latch.store, True, occupancy_mqtt.SOURCE, observed_at=T0))
    _at(16)
    asyncio.run(occupancy.sync_occupancy_state(latch.store, False, "ha_sensor_sync"))
    assert latch.pushes[-1] == (T0 + timedelta(minutes=16), False, "ha_sensor_sync")
    assert ("occupancy_timeout_reason", "released:ha_sensor_sync") in {(e, v) for _ts, e, v in latch.store.rows}


# ── Activity rows for the controller's occupancy lights ─────────────────────


def _decisions(log, light, steps):
    return [log.observe(light, decision, reason, T0) for decision, reason in steps]


def test_occupancy_on_then_back_to_automatic_writes_two_rows():
    log = occupancy_lights.OccupancyLightLog()
    changes = _decisions(
        log,
        "main",
        [
            ("ON", "lux_sufficient"),  # the state text arrives before the reason
            ("ON", "occupancy_lux_low"),
            ("ON", "occupancy_hysteresis_hold"),
            ("ON", "min_on_hold"),
            ("OFF", "min_on_hold"),
            ("OFF", "lux_sufficient"),
        ],
    )
    assert changes == [None, "on", None, None, "off", None]
    on, off = log.pending
    for row, action in ((on, "light.on"), (off, "light.off")):
        ActivityLogRow.model_validate(row.model_dump())
        assert (row.source, row.actor, row.action, row.target) == ("system", "controller", action, "grow_light_main")
        assert row.detail["cause"] == "occupancy"


def test_a_light_already_on_for_the_plants_is_not_claimed_and_a_plant_handover_writes_nothing():
    log = occupancy_lights.OccupancyLightLog()
    # Occupancy arrives while the plant supplement already has the light on: no switch happened.
    assert _decisions(log, "grow", [("ON", "plant_hysteresis_hold"), ("ON", "occupancy_hysteresis_hold")]) == [
        None,
        None,
    ]
    # Turned on for occupancy, then the plant schedule takes over before it would go off.
    assert _decisions(log, "grow", [("ON", "occupancy_lux_low"), ("ON", "plant_hysteresis_hold"), ("OFF", "x")]) == [
        "on",
        None,
        None,
    ]
    assert [row.action for row in log.pending] == ["light.on"]


def test_drain_inserts_the_rows_with_their_time():
    log = occupancy_lights.OccupancyLightLog()
    log.observe("main", "ON", "occupancy_lux_low", T0)
    executed = []

    class Pool:
        def acquire(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, *args):
            executed.append((sql, args))

    assert asyncio.run(occupancy_lights.drain(Pool(), log)) == 1
    ((sql, args),) = executed
    assert sql.startswith("INSERT INTO public.activity_log (greenhouse_id, ts, source, actor, action, target")
    assert args[:6] == ("vallery", T0, "system", "controller", "light.on", "grow_light_main")
    assert json.loads(args[6])["cause"] == "occupancy"
    assert not log.pending


def test_the_hook_reads_only_the_lighting_entities():
    assert occupancy_lights.light_for_entity("gl_main_reason") == "main"
    assert occupancy_lights.light_for_entity("gl_grow_state") == "grow"
    assert occupancy_lights.light_for_entity("gl_main_decision_epoch") is None
    assert occupancy_lights.light_for_entity("occupancy") is None
