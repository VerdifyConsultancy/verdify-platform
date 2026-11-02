"""VD-64 (#1177): the ingestor's L01 bridge, with a fake ESP32 client and no database.

esp32_push talks to FakeClient, which answers like the pre-engine controller (each switch state echoes back as
a native callback), and irrigation_sync to FakeStore, an in-memory irrigation_command table with CommandStore's
status guards. Covered: the press, the toggle order and its restore, re-sends, the 2 min expiry, heap deferral,
nothing sent while the writer preflight fails, and fog untouched. tests/test_dashboard_db_irrigation_bridge.py
runs CommandStore's SQL as the ingestor login on a private PostgreSQL.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGESTOR = ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))
for key, value in {
    "DB_USER": "verdify-test",
    "DB_PASSWORD": "not-a-secret",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "verdify-test",
}.items():
    os.environ.setdefault(key, value)

import esp32_push  # noqa: E402
import shared  # noqa: E402
import tasks  # noqa: E402
from entity_map import EQUIPMENT_SWITCH_MAP, SETPOINT_MAP  # noqa: E402
from tasks import irrigation as bridge  # noqa: E402

from verdify_schemas.irrigation import IrrigationCommandRow  # noqa: E402
from verdify_schemas.tunable_registry import _esphome_wire_slug  # noqa: E402

REAL_SLEEP = asyncio.sleep
GENERATION = 7
ENABLED = esp32_push.IRRIGATION_ENABLED_SWITCH
BUTTON = esp32_push.WALL_CLEAN_BUTTON
RELAY_OF = esp32_push.BRIDGE_RELAY_OBJECT_IDS  # equipment -> object id
EQUIPMENT_OF = {object_id: equipment for equipment, object_id in RELAY_OF.items()}
KEYS = {object_id: 100 + index for index, object_id in enumerate([*RELAY_OF.values(), ENABLED, BUTTON])}
# The legacy job machine's cancel_all closes these (turn_off_all_irrigation); the climate misters stay.
CANCEL_ALL = {
    "drip_wall",
    "drip_wall_fert",
    "mister_south",
    "mister_south_fert",
    "mister_west",
    "mister_west_fert",
    "drip_center",
    "drip_center_fert",
    "fert_master_valve",
}


def now() -> datetime:
    return datetime.now(UTC)


def relay(equipment: str, on: bool, *, at: datetime | None = None, generation: int = GENERATION) -> None:
    shared.note_wet_relay(equipment, on, generation, at or now())


def irrig_enabled(on: bool | None) -> None:
    """The switch's native state, which reaches cfg_readback at once (see the on_state_change test below)."""
    param = esp32_push.IRRIGATION_ENABLED_PARAM
    if on is None:
        shared.cfg_readback.pop(param, None)
        shared.cfg_readback_generation.pop(param, None)
        return
    shared.cfg_readback[param] = 1.0 if on else 0.0
    shared.cfg_readback_generation[param] = shared.transport_generation


class FakeClient:
    """The native API of the pre-engine controller: switch states echo back as callbacks."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.by_key = {key: object_id for object_id, key in KEYS.items()}
        self.stuck: set[str] = set()  # relays whose OFF never takes (no echo)
        self.on_toggle_off = None  # a hook run when sw_irrig_enabled is switched off

    @property
    def calls(self) -> list[tuple]:
        return [event for event in self.events if event[0] in {"press", "switch"}]

    def button_command(self, key):
        self.events.append(("press", self.by_key[key]))

    def switch_command(self, key, state):
        object_id = self.by_key[key]
        self.events.append(("switch", object_id, state))
        if object_id == ENABLED:
            irrig_enabled(state)
            if not state:
                # cancel_all on the job machine's next tick closes the irrigation valves.
                for equipment, on in shared.live_wet_relays().items():
                    if on and equipment in CANCEL_ALL and equipment not in self.stuck:
                        relay(equipment, False)
                if self.on_toggle_off is not None:
                    self.on_toggle_off()
            return
        equipment = EQUIPMENT_OF[object_id]
        if equipment not in self.stuck:
            relay(equipment, state)


class FakeStore:
    """irrigation_command in memory, with CommandStore's methods and status guards."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.rows: dict[uuid.UUID, IrrigationCommandRow] = {}
        self.heap = False

    def add(
        self,
        kind: str,
        line_id: str | None = None,
        *,
        age_s: float = 1.0,
        args: dict | None = None,
        status: str = "pending",
        sent_age_s: float | None = None,
    ) -> IrrigationCommandRow:
        row = IrrigationCommandRow(
            command_id=uuid.uuid4(),
            kind=kind,
            line_id=line_id,
            args=args if args is not None else ({"minutes": 10, "bridge": True} if kind == "run" else {"bridge": True}),
            status=status,
            source="agent:steward",
            actor="steward",
            created_at=now() - timedelta(seconds=age_s),
            sent_at=None if sent_age_s is None else now() - timedelta(seconds=sent_age_s),
            fail_reason="fixture" if status == "failed" else None,
        )
        self.rows[row.command_id] = row
        return row

    def row(self, command: IrrigationCommandRow) -> IrrigationCommandRow:
        return self.rows[command.command_id]

    def _set(self, command_id, **changes) -> None:
        self.rows[command_id] = bridge._transition(self.rows[command_id], **changes)

    async def expire(self):
        expired = [
            row for row in self.rows.values() if row.status == "pending" and row.created_at < now() - bridge.SEND_WINDOW
        ]
        for row in expired:
            self._set(row.command_id, status="failed", fail_reason=bridge.TOO_OLD)
        return [self.rows[row.command_id] for row in expired]

    async def pending(self, kind):
        rows = [
            row
            for row in self.rows.values()
            if row.status == "pending" and row.kind == kind and row.created_at >= now() - bridge.SEND_WINDOW
        ]
        return sorted(rows, key=lambda row: row.created_at)

    async def mark_stop_sent(self, stop, sent_at, relays_on):
        current = self.rows[stop.command_id]
        if current.status != "pending" or current.created_at < now() - bridge.SEND_WINDOW:
            return False
        self._set(stop.command_id, status="sent", sent_at=sent_at, args={**current.args, "relays_on": relays_on})
        for row in list(self.rows.values()):
            if row.kind == "run" and row.status == "pending" and (stop.line_id is None or row.line_id == stop.line_id):
                self._set(row.command_id, status="failed", fail_reason=bridge.STOPPED)
        self.events.append(("stop_sent", stop.command_id))
        return True

    async def mark_run_sent(self, run, sent_at):
        current = self.rows[run.command_id]
        if current.status != "pending" or current.created_at < now() - bridge.SEND_WINDOW:
            return False
        self._set(run.command_id, status="sent", sent_at=sent_at)
        self.events.append(("run_sent", run.command_id))
        return True

    async def mark_failed(self, row, reason):
        if self.rows[row.command_id].status == row.status:
            self._set(row.command_id, status="failed", fail_reason=reason)
            self.events.append(("failed", row.command_id, reason))

    async def merge_args(self, row, patch):
        self._set(row.command_id, args={**self.rows[row.command_id].args, **patch})
        self.events.append(("args", row.command_id, dict(patch)))

    async def sent_stops(self, window=bridge.RESTORE_WINDOW):
        rows = [
            row
            for row in self.rows.values()
            if row.kind == "stop" and row.status == "sent" and row.sent_at > now() - window
        ]
        return sorted(rows, key=lambda row: row.sent_at)

    async def sent_runs(self, line_id="L01"):
        return [
            row
            for row in self.rows.values()
            if row.kind == "run"
            and row.status == "sent"
            and row.line_id == line_id
            and row.sent_at > now() - bridge.RUN_LOOKBACK
        ]

    async def heap_deferred(self):
        return self.heap


@pytest.fixture
def events():
    return []


@pytest.fixture
def client(monkeypatch, events):
    """A live, fenced, writable connection on transport generation 7, with sw_irrig_enabled on."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "writer_lease", None)
    monkeypatch.setattr(shared, "transport_generation", GENERATION)
    monkeypatch.setattr(shared, "wet_relays", {})
    monkeypatch.setattr(shared, "cfg_readback", {})
    monkeypatch.setattr(shared, "cfg_readback_generation", {})
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_BLOCKED", False)
    fake = FakeClient(events)
    monkeypatch.setitem(shared.esp32, "client", fake)
    monkeypatch.setitem(shared.esp32, "keys", dict(KEYS))
    monkeypatch.setitem(shared.esp32, "services", {"set_band_anchor": object()})
    irrig_enabled(True)

    async def fast_sleep(seconds):
        events.append(("sleep", seconds))
        await REAL_SLEEP(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    return fake


@pytest.fixture
def store(events):
    return FakeStore(events)


def sync(store: FakeStore) -> None:
    asyncio.run(bridge.irrigation_sync(None, store=store))


def calls(events) -> list[tuple]:
    return [event for event in events if event[0] in {"press", "switch"}]


def waits(events) -> list[float]:
    return [event[1] for event in events if event[0] == "sleep" and event[1] >= esp32_push.BRIDGE_JOB_TICK_WAIT_S]


# ── esp32_push: the press and the Stop sequence ─────────────────────────────────


def test_the_press_is_one_button_command_on_the_wall_clean_button(client, events):
    outcome = asyncio.run(esp32_push.press_wall_clean())
    assert outcome.ok and outcome.sent == (f"{BUTTON}=press",)
    assert calls(events) == [("press", "wall_drips_-_run_clean")]


@pytest.mark.parametrize(
    ("block", "reason"),
    [
        ("device_writes", "device_writes_disabled"),
        ("lease", "writer_lease_not_held"),
        ("transport", "transport_disconnected"),
    ],
)
def test_nothing_reaches_the_device_while_the_writer_preflight_fails(client, events, monkeypatch, block, reason):
    if block == "device_writes":
        monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "0")
    elif block == "lease":
        monkeypatch.setattr(shared, "writer_lease", type("Lost", (), {"is_held": lambda self: False})())
    else:
        monkeypatch.setitem(shared.esp32, "client", None)
    relay("drip_wall", True)
    for outcome in (
        asyncio.run(esp32_push.press_wall_clean()),
        asyncio.run(esp32_push.bridge_stop(["drip_wall"], True)),
        asyncio.run(esp32_push.bridge_switch_off(["drip_wall"])),
        asyncio.run(esp32_push.bridge_set_irrigation_enabled(True)),
    ):
        assert (outcome.failure, outcome.unsent, outcome.sent) == (reason, True, ())
    assert calls(events) == []


def test_a_stop_covering_l01_toggles_the_job_off_for_one_tick_then_restores_it(client, events):
    relay("drip_wall", True)
    recorded = []

    async def record_restore(value):
        recorded.append(value)
        events.append(("recorded", value))

    outcome = asyncio.run(esp32_push.bridge_stop(["drip_wall"], True, record_restore=record_restore))
    assert (outcome.ok, outcome.toggled, outcome.restored) == (True, True, True)
    ordered = [event for event in events if event[0] in {"recorded", "switch"} or event in [("sleep", 6.0)]]
    assert ordered == [
        ("recorded", True),  # args.restore_irrig_enabled is committed before the switch goes off
        ("switch", ENABLED, False),
        ("sleep", esp32_push.BRIDGE_JOB_TICK_WAIT_S),  # one 5 s job tick for cancel_all
        ("switch", "drip___wall", False),
        ("switch", ENABLED, True),
    ]
    assert esp32_push.BRIDGE_JOB_TICK_WAIT_S >= 6
    assert outcome.sent == (f"{ENABLED}=off", "drip___wall=off", f"{ENABLED}=on")
    assert esp32_push.irrigation_enabled_reading() is True  # left as it was


@pytest.mark.parametrize("reading", [False, None])
def test_the_switch_is_never_toggled_unless_it_reads_on(client, events, reading):
    irrig_enabled(reading)
    relay("drip_wall", True)
    recorded = []

    async def record_restore(value):
        recorded.append(value)

    outcome = asyncio.run(esp32_push.bridge_stop(["drip_wall"], True, record_restore=record_restore))
    assert calls(events) == [("switch", "drip___wall", False)]
    assert recorded == [] and not outcome.toggled and waits(events) == []


def test_a_stop_without_l01_in_scope_switches_only_its_relay(client, events):
    relay("mister_south", True)
    outcome = asyncio.run(esp32_push.bridge_stop(["mister_south"], False))
    assert outcome.ok and calls(events) == [("switch", "mister___south_wall", False)]
    assert waits(events) == []


def test_stop_all_switches_fertilizer_relays_before_the_master_and_never_fog(client, events):
    relays = ["fert_master_valve", "drip_wall_fert", "mister_center", "mister_west_fert", "drip_wall"]
    for equipment in relays:
        relay(equipment, True)
    outcome = asyncio.run(esp32_push.bridge_stop(relays, True))
    switched = [event[1] for event in calls(events)]
    assert switched == [
        ENABLED,
        "drip___wall",
        "drip___wall__fert__",
        "mister___center",
        "mister___west_wall__fert__",
        "valve___fert__master",
        ENABLED,
    ]
    assert outcome.ok and outcome.restored
    assert "fog" not in RELAY_OF and not any("fog" in object_id for object_id in switched)
    with pytest.raises(ValueError, match="fog"):
        asyncio.run(esp32_push.bridge_stop(["fog"], False))


def test_a_transport_change_mid_stop_sends_nothing_more_and_leaves_the_restore_to_the_next_pass(client, events):
    relay("drip_wall", True)
    relay("mister_center", True)
    client.on_toggle_off = lambda: setattr(shared, "transport_generation", GENERATION + 1)
    outcome = asyncio.run(esp32_push.bridge_stop(["drip_wall", "mister_center"], True))
    assert calls(events) == [("switch", ENABLED, False)]
    assert (outcome.toggled, outcome.restored, outcome.failure) == (True, False, "transport_generation_changed")


def test_a_failed_restore_record_skips_the_toggle_but_still_closes_the_relays(client, events):
    relay("drip_wall", True)

    async def record_restore(value):
        raise ConnectionError("database down")

    outcome = asyncio.run(esp32_push.bridge_stop(["drip_wall"], True, record_restore=record_restore))
    assert calls(events) == [("switch", "drip___wall", False)]
    assert (outcome.toggled, outcome.failure, outcome.unsent) == (False, "restore_record_failed", False)


def test_the_bridge_object_ids_are_the_firmware_entity_names():
    """ESPHome derives object ids from names; the switch map's ids were read from the live controller. The relays
    stay in hardware.yaml. The button and the master switch exist only on the pre-engine firmware the bridge drives
    (the engine release, VD-47, removed them from the YAML), so their names are pinned here."""
    hardware = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()
    names = dict(re.findall(r"\n    id: (\w+)\n    name: \"([^\"]+)\"", hardware))
    for relay_id in (
        "wall_drips",
        "wall_drips_fertilized",
        "fertilizer_master_valve",
        "center_mister",
        "center_drips",
        "center_drips_fertilized",
        "south_wall_mister",
        "south_wall_mister_fertilized",
        "west_wall_mister",
        "west_wall_mister_fertilized",
    ):
        assert _esphome_wire_slug(names[relay_id]) in EQUIPMENT_SWITCH_MAP, relay_id
    assert set(RELAY_OF) == set(shared.WET_RELAYS) and len(shared.WET_RELAYS) == 10
    assert set(esp32_push.BRIDGE_STOP_ORDER) == set(shared.WET_RELAYS)
    assert esp32_push.BRIDGE_STOP_ORDER[-1] == "fert_master_valve"
    assert BUTTON == _esphome_wire_slug("Wall Drips - RUN CLEAN") == "wall_drips_-_run_clean"
    assert ENABLED == _esphome_wire_slug("Irrigation Enabled") == "irrigation_enabled"
    assert EQUIPMENT_SWITCH_MAP[ENABLED] == "irrigation_enabled"
    # The switch's native state reaches cfg_readback under this name, which irrigation_enabled_reading reads.
    assert SETPOINT_MAP[ENABLED] == esp32_push.IRRIGATION_ENABLED_PARAM == "sw_irrigation_enabled"


def test_each_line_stops_the_relay_migration_283_seeds_for_it():
    """A wrong row here closes the wrong valve: L01-L10 and their equipment are irrigation_line's seed."""
    seeds = dict(
        re.findall(
            r"\('(L\d{2})', '[^']*', '[a-z]+', '[a-z_]+', '[a-z_]+',\s+'([a-z_]+)'",
            max((ROOT / "db/migrations").glob("*-irrigation-tables.sql")).read_text(),
        )
    )
    assert seeds.pop("L11") == "fog"
    assert seeds == bridge.LINE_RELAYS
    assert tuple(bridge.LINE_RELAYS.values()) == shared.WET_RELAYS
    assert bridge.stop_scope(None) == shared.WET_RELAYS
    assert bridge.stop_scope("L11") == () and bridge.stop_scope("L04") == ("mister_center",)


# ── shared: the live wet relays ─────────────────────────────────────────────────


def test_wet_relays_are_mirrored_with_their_generation_and_last_off(monkeypatch):
    monkeypatch.setattr(shared, "wet_relays", {})
    monkeypatch.setattr(shared, "transport_generation", GENERATION)
    t0 = now()
    shared.note_wet_relay("drip_wall", True, GENERATION - 1, t0)
    assert shared.live_wet_relays() == {}  # read on an older connection only
    shared.note_wet_relay("drip_wall", False, GENERATION, t0 + timedelta(seconds=1))
    shared.note_wet_relay("drip_wall", True, GENERATION, t0 + timedelta(seconds=2))
    assert shared.live_wet_relays() == {"drip_wall": True}
    assert shared.wet_relay_read_off_since("drip_wall", t0 + timedelta(seconds=1))
    assert not shared.wet_relay_read_off_since("drip_wall", t0 + timedelta(seconds=1.5))
    for ignored in (("fog", True, GENERATION), ("drip_wall", 1, GENERATION), ("drip_wall", False, 0)):
        shared.note_wet_relay(*ignored, t0)
    assert set(shared.wet_relays) == {"drip_wall"} and shared.live_wet_relays() == {"drip_wall": True}


def test_the_switch_reading_is_the_native_state_and_never_the_30_s_cfg_sensor(client, monkeypatch):
    """The real callback path. cfg_irrig_enabled (sw_irrig_enabled) publishes every 30 s, so around a toggle it reads
    the old value; only the native switch state may decide a toggle or a restore."""
    import ingestor

    monkeypatch.setattr(ingestor, "state", ingestor.State())
    monkeypatch.setattr(shared, "transport_expected_cfg_readbacks", frozenset())
    monkeypatch.setattr(shared, "cfg_drift_versions", {})
    ingestor.state.key_to_object_id = {1: ENABLED, 2: "cfg_irrig_enabled", 3: RELAY_OF["drip_wall"]}
    ingestor.state.key_to_type = {1: "switch", 2: "sensor", 3: "switch"}

    irrig_enabled(None)
    assert esp32_push.irrigation_enabled_reading() is None
    ingestor.on_state_change(SimpleNamespace(key=2, state=1.0))  # the sensor's last publish
    assert esp32_push.irrigation_enabled_reading() is None  # the sensor alone says nothing about the switch
    ingestor.on_state_change(SimpleNamespace(key=1, state=False))  # switched off; the sensor has not caught up
    assert esp32_push.irrigation_enabled_reading() is False
    assert shared.current_cfg_readbacks()["sw_irrig_enabled"] == 1.0
    ingestor.on_state_change(SimpleNamespace(key=1, state=True))
    ingestor.on_state_change(SimpleNamespace(key=2, state=0.0))  # the sensor published inside the toggle
    assert esp32_push.irrigation_enabled_reading() is True

    ingestor.on_state_change(SimpleNamespace(key=3, state=True))  # and the wet relays come the same way
    assert shared.live_wet_relays() == {"drip_wall": True}


def test_a_toggle_cut_short_while_the_cfg_sensor_still_reads_on_is_restored(client, store, events):
    """The cut-short toggle's whole point: the switch is off although sw_irrig_enabled's readback still reads on."""
    irrig_enabled(False)
    shared.cfg_readback["sw_irrig_enabled"] = 1.0
    shared.cfg_readback_generation["sw_irrig_enabled"] = GENERATION
    cut = store.add(
        "stop", "L01", status="sent", age_s=9, sent_age_s=8, args={"relays_on": [], "restore_irrig_enabled": True}
    )
    sync(store)
    assert calls(events) == [("switch", ENABLED, True)] and store.row(cut).args["restored"] is True


def test_a_second_stop_ends_the_job_while_the_cfg_sensor_still_reads_off(client, store, events):
    """Stop, run, Stop within 30 s: the first toggle's off may still be on the sensor; the job must end again."""
    shared.cfg_readback["sw_irrig_enabled"] = 0.0
    shared.cfg_readback_generation["sw_irrig_enabled"] = GENERATION
    relay("drip_wall", False)
    store.add("stop", "L01")
    sync(store)
    assert calls(events) == [("switch", ENABLED, False), ("switch", ENABLED, True)]


def test_the_equipment_callback_mirrors_wet_relays_before_any_dedupe(monkeypatch):
    import ingestor

    monkeypatch.setattr(shared, "wet_relays", {})
    monkeypatch.setattr(shared, "transport_generation", GENERATION)
    monkeypatch.setattr(ingestor.state, "equipment", {"drip_wall": True})
    ingestor._record_equipment_callback("drip_wall", True, now(), GENERATION, False)
    assert shared.live_wet_relays() == {"drip_wall": True}


# ── irrigation_sync: step 0 ─────────────────────────────────────────────────────


def test_with_the_engine_on_the_connection_the_pass_does_nothing(client, store, events, monkeypatch):
    monkeypatch.setitem(shared.esp32, "services", {"irr_command": object(), "irr_program_begin": object()})
    old = store.add("run", "L01", age_s=300)
    store.add("stop", None)
    sync(store)
    assert store.row(old).status == "pending" and calls(events) == []


def test_a_command_not_sent_within_2_min_fails_too_old_and_is_never_sent(client, store, events):
    relay("drip_wall", False)
    run = store.add("run", "L01", age_s=121)
    stop = store.add("stop", "L01", age_s=130)
    sync(store)
    assert [(store.row(c).status, store.row(c).fail_reason) for c in (run, stop)] == [("failed", "too old")] * 2
    assert calls(events) == []


def test_while_the_preflight_fails_no_command_is_marked_sent(client, store, events, monkeypatch):
    monkeypatch.setitem(shared.esp32, "client", None)  # the transport is down
    relay("drip_wall", True)
    run = store.add("run", "L01", age_s=5)
    stop = store.add("stop", None, age_s=4)
    expired = store.add("run", "L01", age_s=200)
    sync(store)
    assert [store.row(c).status for c in (run, stop)] == ["pending", "pending"]
    assert store.row(expired).status == "failed"  # expiry still runs
    assert calls(events) == [] and not [event for event in events if event[0].endswith("_sent")]


def test_once_the_writer_goes_away_mid_pass_nothing_more_is_marked_sent(client, store, events, monkeypatch):
    relay("mister_center", True)
    relay("drip_wall", False)
    first = store.add("stop", "L04", age_s=3)
    second = store.add("stop", "L07", age_s=2)
    run = store.add("run", "L01", age_s=1)
    switch = client.switch_command

    def then_drop(key, state):
        switch(key, state)
        monkeypatch.setitem(shared.esp32, "client", None)  # the transport drops after this call

    client.switch_command = then_drop
    sync(store)
    assert store.row(first).status == "sent"
    assert (store.row(second).status, store.row(run).status) == ("pending", "pending")
    assert calls(events) == [("switch", "mister___center", False)]


def test_a_pending_run_is_marked_sent_before_its_one_press(client, store, events):
    relay("drip_wall", False)
    run = store.add("run", "L01")
    sync(store)
    assert [event for event in events if event[0] in {"run_sent", "press"}] == [
        ("run_sent", run.command_id),
        ("press", BUTTON),
    ]
    assert store.row(run).status == "sent" and store.row(run).sent_at is not None
    sync(store)  # a sent run is never sent again
    assert calls(events) == [("press", BUTTON)]


def test_a_run_while_drip_wall_reads_on_fails_line_busy_and_is_never_pressed(client, store, events):
    relay("drip_wall", True)
    run = store.add("run", "L01")
    sync(store)
    assert (store.row(run).status, store.row(run).fail_reason) == ("failed", "line_busy")
    assert calls(events) == []


def test_a_run_while_an_earlier_run_is_still_due_fails_line_busy(client, store, events):
    relay("drip_wall", False)  # pressed 20 s ago; the relay has not opened yet
    store.add("run", "L01", status="sent", age_s=21, sent_age_s=20)
    run = store.add("run", "L01")
    sync(store)
    assert store.row(run).fail_reason == "line_busy" and calls(events) == []


def test_a_run_waits_while_drip_wall_is_unread_on_this_connection(client, store, events):
    relay("drip_wall", False, generation=GENERATION - 1)
    run = store.add("run", "L01")
    sync(store)
    assert store.row(run).status == "pending" and calls(events) == []


def test_a_press_that_never_reached_the_transport_ends_failed(client, store, events, monkeypatch):
    relay("drip_wall", False)
    monkeypatch.setitem(shared.esp32, "keys", {k: v for k, v in KEYS.items() if k != BUTTON})
    run = store.add("run", "L01")
    sync(store)
    assert (store.row(run).status, store.row(run).fail_reason) == ("failed", "entity_key_unavailable")
    assert store.row(run).sent_at is not None and calls(events) == []


def test_a_run_on_another_line_cannot_run_without_the_engine(client, store, events):
    relay("drip_wall", False)
    run = store.add("run", "L04", args={"on_s": 30, "count": 2, "gap_s": 60})
    sync(store)
    assert (store.row(run).status, store.row(run).fail_reason) == ("failed", bridge.NO_ENGINE)
    assert calls(events) == []


def test_heap_deferral_sends_the_stop_and_holds_the_run(client, store, events):
    store.heap = True
    relay("mister_center", True)
    relay("drip_wall", False)
    stop = store.add("stop", "L04", age_s=3)
    run = store.add("run", "L01", age_s=2)
    sync(store)
    assert store.row(stop).status == "sent" and store.row(stop).args["relays_on"] == ["mister_center"]
    assert store.row(run).status == "pending"
    assert calls(events) == [("switch", "mister___center", False)]
    store.heap = False
    sync(store)
    assert store.row(run).status == "sent" and calls(events)[-1] == ("press", BUTTON)


def test_a_stop_fails_every_pending_run_in_its_scope_and_ends_the_job(client, store, events):
    relay("drip_wall", True)
    before = store.add("run", "L01", age_s=30)
    stop = store.add("stop", "L01", age_s=20)
    after = store.add("run", "L01", age_s=10)
    sync(store)
    for run in (before, after):  # pending when the Stop was sent: never pressed after it
        assert (store.row(run).status, store.row(run).fail_reason) == ("failed", "stopped")
    sent = store.row(stop)
    assert sent.status == "sent" and sent.args == {
        "bridge": True,
        "relays_on": ["drip_wall"],
        "restore_irrig_enabled": True,
        "restored": True,
    }
    assert calls(events) == [
        ("switch", ENABLED, False),
        ("switch", "drip___wall", False),
        ("switch", ENABLED, True),
    ]
    assert esp32_push.irrigation_enabled_reading() is True


def test_stop_all_closes_every_open_wet_relay_of_l01_to_l10(client, store, events):
    for equipment in shared.WET_RELAYS:
        relay(equipment, equipment in {"drip_wall", "mister_center", "mister_west"})
    stop = store.add("stop", None)
    sync(store)
    assert store.row(stop).args["relays_on"] == ["drip_wall", "mister_center", "mister_west"]
    assert [event[1:] for event in calls(events)] == [
        (ENABLED, False),
        ("drip___wall", False),
        ("mister___center", False),
        ("mister___west_wall", False),
        (ENABLED, True),
    ]
    assert not any(shared.live_wet_relays().values()) and len(shared.live_wet_relays()) == 10


def test_a_stop_in_the_state_burst_after_a_reconnect_closes_the_relays_not_read_yet(client, store, events):
    """L01 was running when the connection dropped and this connection has read nothing yet: the Stop must not be
    marked sent with nothing closed. OFF is harmless on a relay that turns out to be closed."""
    irrig_enabled(None)
    relay("drip_wall", True, generation=GENERATION - 1)
    stop = store.add("stop", "L01")
    sync(store)
    sent = store.row(stop)
    assert sent.status == "sent" and sent.args["relays_on"] == ["drip_wall"]
    assert calls(events) == [("switch", "drip___wall", False)]
    assert "restore_irrig_enabled" not in sent.args  # never toggled unread: a restore could flip it on
    sync(store)  # it read OFF after the Stop: done
    assert len(calls(events)) == 1


def test_stop_all_on_a_connection_that_has_read_nothing_switches_all_ten_off_master_last(client, store, events):
    irrig_enabled(None)
    relay("mister_center", False)  # the one relay the burst has reached already reads closed
    stop = store.add("stop", None)
    sync(store)
    expected = [equipment for equipment in esp32_push.BRIDGE_STOP_ORDER if equipment != "mister_center"]
    assert store.row(stop).args["relays_on"] == [e for e in shared.WET_RELAYS if e != "mister_center"]
    assert calls(events) == [("switch", RELAY_OF[equipment], False) for equipment in expected]
    assert calls(events)[-1] == ("switch", "valve___fert__master", False)


def test_a_relay_not_read_yet_is_resent_until_it_reads_off(client, store, events):
    irrig_enabled(None)
    client.stuck.add("mister_center")  # the OFF has no echo yet
    stop = store.add("stop", "L04")
    sync(store)
    sync(store)
    assert store.row(stop).args["relays_on"] == ["mister_center"]
    assert calls(events) == [("switch", "mister___center", False)] * 2
    relay("mister_center", False)  # the burst reads it
    sync(store)
    assert len(calls(events)) == 2


def test_a_stop_on_l11_alone_is_stored_and_closes_nothing(client, store, events):
    stop = store.add("stop", "L11")
    sync(store)
    assert store.row(stop).status == "sent" and store.row(stop).args["relays_on"] == []
    assert calls(events) == []


def test_relays_that_have_not_read_off_are_resent_for_at_most_2_min(client, store, events):
    client.stuck.add("mister_center")
    relay("mister_center", True)
    stop = store.add("stop", "L04")
    sync(store)
    assert store.row(stop).args["relays_on"] == ["mister_center"]
    sync(store)  # the relay still reads ON: OFF again
    assert calls(events) == [("switch", "mister___center", False)] * 2
    client.stuck.clear()
    sync(store)  # this OFF takes
    relay("mister_center", True)  # VPD misting pulses again: that relay is done
    sync(store)
    assert calls(events) == [("switch", "mister___center", False)] * 3
    late = store.add("stop", "L09", status="sent", age_s=200, sent_age_s=121, args={"relays_on": ["mister_west"]})
    relay("mister_west", True)
    sync(store)
    assert calls(events) == [("switch", "mister___center", False)] * 3
    assert store.row(late).status == "sent"


def test_a_cut_short_toggle_gets_sw_irrig_enabled_back(client, store, events):
    irrig_enabled(False)  # a restart left the switch off between the two halves of the toggle
    cut = store.add(
        "stop", "L01", status="sent", age_s=60, sent_age_s=55, args={"relays_on": [], "restore_irrig_enabled": True}
    )
    sync(store)
    assert calls(events) == [("switch", ENABLED, True)]
    assert store.row(cut).args["restored"] is True
    sync(store)
    assert calls(events) == [("switch", ENABLED, True)]
    already = store.add(
        "stop", None, status="sent", age_s=60, sent_age_s=50, args={"relays_on": [], "restore_irrig_enabled": True}
    )
    sync(store)  # the switch already reads on: recorded without a command
    assert store.row(already).args["restored"] is True and len(calls(events)) == 1


# ── registration, the engine row and the store ─────────────────────────────────


class FakeConnection:
    def __init__(self, records=()):
        self.records = list(records)
        self.executed: list[tuple] = []

    async def fetch(self, _sql, *_args):
        return self.records

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Lend:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Lend()


def test_irr_engine_is_true_only_with_both_engine_services():
    conn = FakeConnection()
    results = [
        asyncio.run(bridge.record_engine_capability(FakePool(conn), services))
        for services in ({"set_band_anchor": 1}, {"irr_command": 1}, {"irr_command": 1, "irr_program_begin": 1}, None)
    ]
    assert results == [False, False, True, False]
    assert [args[1:] for _sql, args in conn.executed] == [
        ("irr_engine", "false"),
        ("irr_engine", "false"),
        ("irr_engine", "true"),
        ("irr_engine", "false"),
    ]
    assert {sql for sql, _ in conn.executed} == {
        "INSERT INTO v_runtime_system_state_write (ts, entity, value) VALUES ($1, $2, $3)"
    }


def _task_loop() -> ast.AsyncFunctionDef:
    tree = ast.parse((INGESTOR / "ingestor.py").read_text())
    [loop] = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "task_loop"]
    return loop


def test_the_task_loop_runs_irrigation_sync_every_5_s_with_a_30_s_timeout_and_forces_it_on_the_wake():
    loop = _task_loop()
    source = ast.unparse(loop)
    [tasks_assign] = [
        node
        for node in ast.walk(loop)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TASKS" for t in node.targets)
    ]
    entries = [ast.unparse(entry) for entry in tasks_assign.value.elts]
    assert "('irrigation_sync', 5, irrigation_sync)" in entries
    assert "'irrigation_sync': 30" in source
    assert "if shared.irrigation_sync_requested.is_set():\n                forced.add('irrigation_sync')" in source
    assert tasks.irrigation_sync is bridge.irrigation_sync
    assert tasks.record_engine_capability is bridge.record_engine_capability


def test_the_connect_writes_the_engine_row_only_after_the_generation_advance():
    """Nothing may await between sharing the new client and advancing the transport generation: a task woken in
    that gap would write to the new client against the old generation's readbacks and the old relay states."""
    source = (INGESTOR / "ingestor.py").read_text()
    client_shared = source.index('shared.esp32["client"] = client')
    cached = source.index('shared.esp32["services"] = {s.name: s for s in (services or [])}')
    generation = source.index("connection_generation = shared.note_transport_connected(")
    written = source.index('record_engine_capability(pool, shared.esp32["services"])')
    assert client_shared < cached < generation < written
    assert "await " not in source[client_shared:generation]


def stored_row(**changes) -> dict:
    row = {
        "command_id": uuid.uuid4(),
        "greenhouse_id": "vallery",
        "kind": "stop",
        "line_id": None,
        "args": "{}",
        "status": "pending",
        "fail_reason": None,
        "source": "agent:steward",
        "actor": "steward",
        "reason": None,
        "created_at": now(),
        "sent_at": None,
    }
    return {**row, **changes}


def test_a_row_the_model_refuses_is_logged_and_dropped_and_never_keeps_a_stop_from_going_out(caplog):
    good = stored_row()
    bad = stored_row(source="typed by hand", reason="not for the log")
    assert [row.command_id for row in bridge._rows([bad, good])] == [good["command_id"]]
    assert str(bad["command_id"]) in caplog.text and "source" in caplog.text
    assert "typed by hand" not in caplog.text and "not for the log" not in caplog.text


def test_a_row_the_model_refuses_still_expires():
    good, bad = stored_row(kind="run", line_id="L01"), stored_row(source="typed by hand")
    conn = FakeConnection([bad, good])
    failed = asyncio.run(bridge.CommandStore(FakePool(conn)).expire())
    assert [(row.command_id, row.status, row.fail_reason) for row in failed] == [
        (good["command_id"], "failed", "too old")
    ]
    [(sql, args)] = conn.executed
    assert sql == bridge._FAIL_SQL and args == ([bad["command_id"], good["command_id"]], "too old", "pending")


def test_the_store_updates_only_the_columns_the_ingestor_may_and_guards_each_status():
    granted = {"status", "sent_at", "fail_reason", "args"}
    for name in ("_FAIL_SQL", "_SEND_SQL", "_MERGE_ARGS_SQL"):
        sql = getattr(bridge, name)
        assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert {part.split("=")[0].strip() for part in assignments.split(",")} <= granted, name
        assert " WHERE command_id" in sql
    assert "AND status = $3" in bridge._FAIL_SQL and "AND status = 'pending'" in bridge._SEND_SQL
    assert "created_at >= now() - $4::interval" in bridge._SEND_SQL  # never sent late
    assert shared._is_write_sql(bridge._SEND_SQL) and shared._is_write_sql(bridge._ENGINE_ROW_SQL)


def test_a_status_update_that_breaks_the_row_model_is_never_written():
    row = IrrigationCommandRow(kind="stop", source="agent:steward", actor="steward", created_at=now())
    with pytest.raises(ValueError, match="sent_at"):
        bridge._transition(row, status="sent")
    with pytest.raises(ValueError):
        bridge._transition(row, status="done")
