"""VD-42 (#1157): irrigation_sync with the engine on the connection, on a fake native-API client and no database.

esp32_push.push_policy_transaction is the real one; EngineClient answers like the engine firmware (its services and
readbacks), FakeStore is the bridge test's in-memory irrigation_command table and FakeProgram an in-memory irrigation_push.
tests/test_dashboard_db_irrigation_ledger.py runs the stores' SQL, the golden replay and the alerts on a private PostgreSQL.

Covered: the wake flag and the 60 s full pass, push and confirm, the re-push that updates its row, rejects and their
back-off, transport failures, stop order and the earlier run it fails, the run arguments per line kind, the capability
flip, the 2 min expiry, heap deferral, and the text-sensor routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from collections import deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingestor") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingestor"))
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
from entity_map import STATE_MAP  # noqa: E402
from tasks import alerts as alert_tasks  # noqa: E402
from tasks import irrigation as task  # noqa: E402
from test_irrigation_bridge import (  # noqa: E402
    BUTTON,
    ENABLED,
    GENERATION,
    KEYS,
    FakeClient,
    FakeStore,
    calls,
    irrig_enabled,
    now,
    relay,
)

from verdify_schemas.alerts import AlertEnvelope  # noqa: E402
from verdify_schemas.irrigation import (  # noqa: E402
    PROGRAM_RECORD_LEN,
    IrrigationCommandRow,
    LineSpec,
    ScheduleSpec,
    SiteValues,
    Start,
    compile_program,
    occurrence_start,
    pack_program,
    parse_irr_state,
    unpack_program,
)
from verdify_schemas.tunable_registry import _esphome_wire_slug  # noqa: E402

ENGINE_SERVICES = ("irr_program_begin", "irr_program_chunk", "irr_program_commit", "irr_command", "manual_override")
EARLIER = 1_790_000_000


class EngineClient(FakeClient):
    """The engine firmware's native API: services recorded in order, with the program staging and the readbacks."""

    def __init__(self, events: list) -> None:
        super().__init__(events)
        self.staged: dict | None = None
        self.reject: str | None = None  # the next commits are rejected with this code
        self.silent = False  # a commit that is neither accepted nor rejected (a failed NVS save)
        self.fail_on: str | None = None

    def execute_service(self, service, payload):
        self.events.append(("service", service, dict(payload)))
        if service == self.fail_on:
            raise ConnectionError("socket gone")
        if service == "irr_program_begin":
            self.staged = {"sha256": payload["sha256_hex"], "data": bytearray(payload["len"])}
        elif service == "irr_program_chunk":
            data = bytes.fromhex(payload["data_hex"])
            self.staged["data"][payload["offset"] : payload["offset"] + len(data)] = data
        elif service == "irr_program_commit":
            self._commit()
        elif service == "irr_command" and payload["cmd"] == "stop":
            for equipment, on in shared.live_wet_relays().items():
                if on and equipment not in self.stuck:
                    relay(equipment, False)

    def _commit(self) -> None:
        record = bytes(self.staged["data"])
        assert hashlib.sha256(record).hexdigest() == self.staged["sha256"]
        unpack_program(record)  # the firmware decoder's golden vectors hold for this record
        if self.silent:
            return
        if self.reject is not None:
            readback("irr_state", f"v=hhh j={self.reject}")
            return
        readback("irr_state", "v=hhh")
        readback("irr_program_id", f"h={self.staged['sha256'][:16]} src=nvs")


class FakeProgram:
    """ProgramStore in memory: irrigation_push rows as dicts, with its record_push rule."""

    def __init__(self, snapshot: task.ProgramSnapshot) -> None:
        self.current = snapshot
        self.rows: list[dict] = []
        self.reads = 0
        self.on_read = None

    async def snapshot(self):
        self.reads += 1
        if self.on_read is not None:
            self.on_read()
        return self.current

    async def newest_push(self):
        return self.rows[-1] if self.rows else None

    async def record_push(self, snapshot, pushed_at):
        compiled = snapshot.compiled
        if self.rows and self.rows[-1]["sha256"] == compiled.sha256:
            self.rows[-1].update(pushed_at=pushed_at, reject_code=None, confirmed_at=None)
        else:
            self.rows.append(
                {
                    "revision": len(self.rows) + 1,
                    "sha256": compiled.sha256,
                    "bytes": compiled.record,
                    "site_desired_at": snapshot.site_desired_at,
                    "pushed_at": pushed_at,
                    "confirmed_at": None,
                    "reject_code": None,
                }
            )
        return self.rows[-1]["revision"]

    async def confirm(self, revision, confirmed_at):
        row = self.rows[revision - 1]
        row["confirmed_at"] = row["confirmed_at"] or confirmed_at

    async def record_reject(self, revision, code):
        self.rows[revision - 1]["reject_code"] = code


class FakeLedger:
    """LedgerStore's calls; its SQL runs on PostgreSQL in tests/test_dashboard_db_irrigation_ledger.py."""

    def __init__(self) -> None:
        self.started: list = []
        self.closed: dict[int, task.RunClose] = {}
        self.outcomes: list = []
        self.edges: tuple[bool, list] = (False, [])
        self.stored: list = []

    async def open_rows(self, _now):
        return self.stored

    async def record_start(self, run, _observed_at):
        self.started.append(run)
        return len(self.started)

    async def relay_edges(self, _relay, _start, _end):
        return self.edges

    async def close(self, row_id, closing):
        self.closed[row_id] = closing

    async def record_outcomes(self, events, running, _now):
        self.outcomes.append((list(events), list(running)))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t


def readback(sensor: str, text: str, *, at: datetime | None = None, generation: int | None = None) -> None:
    shared.note_irrigation_readback(
        sensor, text, shared.transport_generation if generation is None else generation, at or now()
    )


def lines(*enabled: str) -> tuple[LineSpec, ...]:
    return tuple(
        LineSpec(line_id=f"L{index:02d}", enabled=f"L{index:02d}" in enabled, daily_cap_min=60)
        for index in range(1, 12)
    )


def snapshot(*minutes_of_day: int) -> task.ProgramSnapshot:
    """The program with one L01 schedule per start, so a new start is a changed schedule."""
    specs = tuple(
        ScheduleSpec(
            slot_id=index + 10,
            line_id="L01",
            days=0x7F,
            start=Start(kind="clock", minute=minute),
            on_s=300,
        )
        for index, minute in enumerate(minutes_of_day)
    )
    all_lines = lines("L01", "L04")
    site = SiteValues(saf4_gal=300, rh_ceiling_pct=90, climate_yield_min=15)
    return task.ProgramSnapshot(
        lines=all_lines,
        slots=specs,
        zones={},
        compiled=compile_program(all_lines, specs, site),
        site_desired_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(task, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


@pytest.fixture
def client(monkeypatch, events, clock):
    """A live, fenced, writable engine connection on transport generation 7, with no pacing."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "writer_lease", None)
    monkeypatch.setattr(shared, "transport_generation", GENERATION)
    monkeypatch.setattr(shared, "wet_relays", {})
    monkeypatch.setattr(shared, "cfg_readback", {})
    monkeypatch.setattr(shared, "cfg_readback_generation", {})
    monkeypatch.setattr(shared, "irrigation_readback", {})
    monkeypatch.setattr(shared, "irr_state_changes", deque())
    monkeypatch.setattr(shared, "irrigation_sync_requested", asyncio.Event())
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_BLOCKED", False)
    monkeypatch.setattr(esp32_push, "_MIN_COMMAND_INTERVAL_S", 0.0)
    monkeypatch.setattr(task, "_memory", task.EngineMemory())
    monkeypatch.setattr(task, "_last_block_reason", None)
    fake = EngineClient(events)
    monkeypatch.setitem(shared.esp32, "client", fake)
    monkeypatch.setitem(shared.esp32, "keys", dict(KEYS))
    monkeypatch.setitem(shared.esp32, "services", {name: name for name in ENGINE_SERVICES})
    return fake


@pytest.fixture
def store(events):
    return FakeStore(events)


@pytest.fixture
def ledger():
    return FakeLedger()


@pytest.fixture
def program():
    return FakeProgram(snapshot(360))


def sync(store, program, ledger) -> None:
    asyncio.run(task.irrigation_sync(None, store=store, program=program, ledger=ledger))


def services(events) -> list[tuple]:
    return [event for event in events if event[0] == "service"]


def on_rom() -> None:
    readback("irr_program_id", "h=0000000000000000 src=rom")
    readback("irr_state", "v=hhh")


def run_closed(ended_at: datetime, open_s: int, pulses: int) -> task.RunClose:
    return task.RunClose(ended_at=ended_at, open_s=open_s, pulses=pulses)


def command_row(kind, line_id=None, /, **args) -> IrrigationCommandRow:
    return IrrigationCommandRow(kind=kind, line_id=line_id, args=args, source="agent:steward", actor="steward")


def in_sync(program) -> None:
    """The controller runs the compiled program, so a pass pushes nothing and the ledger is all that moves."""
    readback("irr_program_id", f"h={program.current.compiled.hash16} src=nvs")


# ── Cadence: the wake flag and the 60 s full pass ───────────────────────────────


def test_a_pass_runs_the_program_steps_only_on_the_flag_or_after_60_s(client, store, program, ledger, clock):
    readback("irr_program_id", f"h={program.current.compiled.hash16} src=nvs")
    sync(store, program, ledger)
    assert program.reads == 1  # the first pass on a connection is a full one
    sync(store, program, ledger)
    assert program.reads == 1  # 5 s later, with no wake: commands only
    clock.t += 59
    sync(store, program, ledger)
    assert program.reads == 1
    clock.t += 1
    sync(store, program, ledger)
    assert program.reads == 2
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert program.reads == 3 and not shared.irrigation_sync_requested.is_set()


def test_the_flag_is_cleared_before_the_read_so_a_write_during_it_wakes_the_next_pass(client, store, program, ledger):
    readback("irr_program_id", f"h={program.current.compiled.hash16} src=nvs")
    seen = []
    program.on_read = lambda: seen.append(shared.irrigation_sync_requested.is_set())
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert seen == [False]

    program.on_read = shared.irrigation_sync_requested.set
    sync(store, program, ledger)  # no wake and within 60 s: no read, so the flag from before stands
    assert program.reads == 1
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert program.reads == 2 and shared.irrigation_sync_requested.is_set()  # set during the read: the next pass runs


def test_a_new_connection_is_compared_at_once(client, store, program, ledger, monkeypatch):
    readback("irr_program_id", f"h={program.current.compiled.hash16} src=nvs")
    sync(store, program, ledger)
    assert program.reads == 1
    monkeypatch.setattr(shared, "transport_generation", GENERATION + 1)
    shared.irrigation_readback.clear()
    sync(store, program, ledger)
    assert program.reads == 1  # nothing to compare with until the new connection reads irr_program_id
    readback("irr_program_id", "h=0000000000000000 src=rom")
    sync(store, program, ledger)
    assert program.reads == 2 and len(program.rows) == 1  # back on src=rom: pushed again on this pass


# ── Push and confirm ───────────────────────────────────────────────────────────


def test_a_changed_schedule_sends_begin_four_chunks_and_commit_in_one_transaction(
    client, store, program, ledger, events, monkeypatch
):
    on_rom()
    transactions = []
    real = esp32_push.push_policy_transaction

    async def spy(calls, **kwargs):
        transactions.append([call.service for call in calls])
        return await real(calls, **kwargs)

    monkeypatch.setattr(esp32_push, "push_policy_transaction", spy)
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)

    compiled = program.current.compiled
    assert transactions == [
        [
            "irr_program_begin",
            "irr_program_chunk",
            "irr_program_chunk",
            "irr_program_chunk",
            "irr_program_chunk",
            "irr_program_commit",
        ]
    ]
    sent = services(events)
    assert sent[0] == ("service", "irr_program_begin", {"len": 584, "sha256_hex": compiled.sha256})
    chunks = [event[2] for event in sent[1:5]]
    assert [chunk["offset"] for chunk in chunks] == [0, 192, 384, 576]
    assert [len(chunk["data_hex"]) // 2 for chunk in chunks] == [192, 192, 192, 8]
    assert b"".join(bytes.fromhex(chunk["data_hex"]) for chunk in chunks) == compiled.record
    assert sent[5] == ("service", "irr_program_commit", {})
    assert (
        len(compiled.record) == PROGRAM_RECORD_LEN and pack_program(unpack_program(compiled.record)) == compiled.record
    )

    [row] = program.rows
    assert (row["revision"], row["sha256"], row["bytes"]) == (1, compiled.sha256, compiled.record)
    assert row["site_desired_at"] == program.current.site_desired_at
    assert row["pushed_at"] is not None and row["confirmed_at"] is None  # recorded before the device call


def test_the_push_is_recorded_just_before_the_device_call(client, store, program, ledger, events):
    on_rom()
    real = program.record_push

    async def recording(*args):
        events.append(("recorded",))
        return await real(*args)

    program.record_push = recording
    sync(store, program, ledger)
    assert [event[0] for event in events[:2]] == ["recorded", "service"]


def test_confirmed_at_is_set_when_irr_program_id_reports_the_hash(client, store, program, ledger, clock, events):
    on_rom()
    sync(store, program, ledger)
    [row] = program.rows
    assert shared.irrigation_readback["irr_program_id"] == f"h={row['sha256'][:16]} src=nvs"
    # The next 5 s tick confirms from memory; no compile and no second push.
    reads = program.reads
    sync(store, program, ledger)
    assert row["confirmed_at"] is not None and program.reads == reads
    clock.t += 61
    sync(store, program, ledger)
    assert len(services(events)) == 6 and len(program.rows) == 1


def test_a_full_pass_confirms_after_a_restart_lost_the_push_from_memory(client, store, program, ledger, monkeypatch):
    compiled = program.current.compiled
    program.rows.append(
        {"revision": 9, "sha256": compiled.sha256, "bytes": compiled.record, "confirmed_at": None, "reject_code": None}
    )
    program.rows = [None] * 8 + program.rows  # revision 9 at index 8
    readback("irr_program_id", f"h={compiled.hash16} src=nvs")
    sync(store, program, ledger)
    assert program.rows[8]["confirmed_at"] is not None


def test_only_a_changed_hash_adds_a_revision_and_a_repush_updates_that_row(
    client, store, program, ledger, clock, events
):
    on_rom()
    client.silent = True  # the commit goes unanswered, as when the controller's NVS save fails
    sync(store, program, ledger)
    first_pushed = program.rows[0]["pushed_at"]
    program.rows[0]["reject_code"] = "bad_slot"

    clock.t += task.REJECT_SETTLE_S
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert len(services(events)) == 6  # a wake while it waits for this push's readback sends no second one

    clock.t += task.FULL_PASS_INTERVAL_S
    sync(store, program, ledger)
    assert len(services(events)) == 12 and len(program.rows) == 1
    assert program.rows[0]["pushed_at"] >= first_pushed and program.rows[0]["reject_code"] is None

    program.current = snapshot(360, 720)
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert len(program.rows) == 2 and program.rows[1]["revision"] == 2  # a newly edited program goes at once


def test_a_controller_back_on_src_rom_gets_the_program_again_within_a_minute(client, store, program, ledger, clock):
    on_rom()
    sync(store, program, ledger)
    sync(store, program, ledger)
    assert program.rows[0]["confirmed_at"] is not None
    on_rom()
    clock.t += 60
    sync(store, program, ledger)
    assert len(program.rows) == 1
    # The re-push is unconfirmed until the controller reports the hash again, here at once.
    assert program.rows[0]["confirmed_at"] is None
    sync(store, program, ledger)
    assert program.rows[0]["confirmed_at"] is not None


def test_nothing_is_pushed_or_recorded_while_the_writer_is_fenced(client, store, program, ledger, events, monkeypatch):
    on_rom()
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "0")
    sync(store, program, ledger)
    assert program.rows == [] and services(events) == []


def test_a_transport_failure_retries_on_the_next_pass_and_nothing_halts(client, store, program, ledger, events):
    on_rom()
    client.fail_on = "irr_program_chunk"
    sync(store, program, ledger)
    assert [event[1] for event in services(events)] == [
        "irr_program_begin",
        "irr_program_chunk",
    ]  # the first failure ends it
    assert shared.irrigation_sync_requested.is_set()
    client.fail_on = None
    sync(store, program, ledger)
    assert [event[1] for event in services(events)][-1] == "irr_program_commit"
    assert len(program.rows) == 1 and not shared.irrigation_sync_requested.is_set()


def test_a_reject_is_stored_and_retried_1_5_and_15_min_later_then_every_15(
    client, store, program, ledger, clock, events
):
    on_rom()
    client.reject = "bad_hash"
    sync(store, program, ledger)
    pushes = lambda: sum(event[1] == "irr_program_commit" for event in services(events))  # noqa: E731
    assert pushes() == 1 and program.rows[0]["reject_code"] is None  # the commit has only just gone

    clock.t += task.REJECT_SETTLE_S
    sync(store, program, ledger)
    assert program.rows[0]["reject_code"] == "bad_hash" and pushes() == 1

    for delay_s in (60, 300, 900, 900):
        before = pushes()
        clock.t += delay_s - 1
        shared.irrigation_sync_requested.set()  # a wake inside the back-off changes nothing
        sync(store, program, ledger)
        assert pushes() == before
        clock.t += 1
        sync(store, program, ledger)
        assert pushes() == before + 1
        assert program.rows[0]["reject_code"] is None  # cleared by the re-push
        clock.t += task.REJECT_SETTLE_S
        sync(store, program, ledger)
        assert program.rows[0]["reject_code"] == "bad_hash"
    assert len(program.rows) == 1

    client.reject = None
    clock.t += 900
    sync(store, program, ledger)
    sync(store, program, ledger)
    assert program.rows[0]["confirmed_at"] is not None and task._memory.backoff is None


def test_a_newly_edited_program_does_not_wait_out_the_old_ones_back_off(client, store, program, ledger, clock, events):
    on_rom()
    client.reject = "bad_slot"
    sync(store, program, ledger)
    clock.t += task.REJECT_SETTLE_S
    sync(store, program, ledger)
    assert task._memory.backoff is not None
    client.reject = None
    program.current = snapshot(360, 720)
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert len(program.rows) == 2 and task._memory.backoff is None


def test_an_old_reject_token_is_not_this_pushs_while_the_commit_settles(client, store, program, ledger, clock):
    readback("irr_program_id", "h=0000000000000000 src=rom")
    readback("irr_state", "v=hhh j=bad_line")  # left by a push before this one
    client.silent = True
    sync(store, program, ledger)
    assert program.rows[0]["reject_code"] is None
    readback("irr_state", "v=hhh")  # the accepted commit cleared it
    clock.t += task.REJECT_SETTLE_S
    sync(store, program, ledger)
    assert program.rows[0]["reject_code"] is None


def test_the_program_waits_out_heap_deferral_and_a_pending_stop(client, store, program, ledger, events):
    on_rom()
    store.heap = True
    sync(store, program, ledger)
    assert program.rows == [] and services(events) == []

    store.heap = False
    program.on_read = lambda: store.add("stop", "L01", args={})  # a Stop arrives during the compile
    shared.irrigation_sync_requested.set()
    sync(store, program, ledger)
    assert program.rows == [] and services(events) == []  # no program with a Stop pending
    assert shared.irrigation_sync_requested.is_set()  # the next pass, after the Stop

    program.on_read = None
    sync(store, program, ledger)
    assert [event[1] for event in services(events)][0] == "irr_command"
    assert [event[1] for event in services(events)][-1] == "irr_program_commit"


def test_schedules_are_never_pushed_until_irr_program_id_appears(client, store, program, ledger, events):
    shared.irrigation_sync_requested.set()
    readback("irr_state", "d=0,schedule,11,1790000000,1790000300 v=hhh")
    sync(store, program, ledger)
    assert program.reads == 0 and program.rows == [] and services(events) == [] and ledger.started == []
    assert shared.irrigation_sync_requested.is_set()  # the first reading is compared at once


# ── Step 1: commands ───────────────────────────────────────────────────────────


def test_a_stop_goes_first_and_the_run_asked_for_just_before_it_never_starts(client, store, program, ledger, events):
    in_sync(program)
    relay("drip_wall", True)
    relay("mister_center", False)
    run = store.add("run", "L01", args={"minutes": 5}, age_s=3)
    clear = store.add("clear_fault", "L04", args={}, age_s=2)
    stop = store.add("stop", "L01", args={}, age_s=1)
    later = store.add("run", "L01", args={"minutes": 5}, age_s=0)
    sync(store, program, ledger)

    sent = services(events)
    assert sent[0] == (
        "service",
        "irr_command",
        {"cmd": "stop", "line": 0, "on_s": 0, "count": 0, "gap_s": 0, "prewet_s": 0, "flush_s": 0},
    )
    assert (store.row(run).status, store.row(run).fail_reason) == ("failed", "stopped")
    assert store.row(stop).status == "sent" and store.row(stop).args["relays_on"] == ["drip_wall"]
    assert store.row(stop).sent_at is not None
    assert [event[2]["cmd"] for event in sent[1:]] == ["clear_fault", "run"]  # oldest first, after the Stop
    assert store.row(clear).status == "sent" and store.row(later).status == "sent"  # asked for after the Stop
    assert shared.live_wet_relays()["drip_wall"] is False  # the edge reads OFF on the same pass


def test_a_stop_goes_even_with_no_open_relay_and_with_255_for_all_lines(client, store, program, ledger, events):
    for equipment in shared.WET_RELAYS:
        relay(equipment, False)
    stop = store.add("stop", None, args={})
    sync(store, program, ledger)
    assert services(events)[0][2]["line"] == 255 and services(events)[0][2]["cmd"] == "stop"
    assert store.row(stop).args["relays_on"] == []
    sync(store, program, ledger)
    assert len(services(events)) == 1  # nothing to wait for


def test_a_stop_is_re_sent_each_pass_until_its_relays_read_off_for_at_most_2_min(
    client, store, program, ledger, events
):
    relay("drip_wall", True)
    client.stuck.add("drip_wall")
    stop = store.add("stop", "L01", args={})
    sync(store, program, ledger)
    sync(store, program, ledger)
    assert len(services(events)) == 2
    store._set(stop.command_id, sent_at=now() - timedelta(seconds=121))  # past the window
    sync(store, program, ledger)
    assert len(services(events)) == 2

    store._set(stop.command_id, sent_at=now() - timedelta(seconds=10))
    client.stuck.clear()
    relay("drip_wall", False)  # read OFF since it was sent: done, so VPD misting may pulse again
    relay("drip_wall", True)
    sync(store, program, ledger)
    assert len(services(events)) == 2


def test_a_stop_that_failed_on_the_wire_goes_again_even_with_no_relay_to_wait_for(
    client, store, program, ledger, events
):
    relay("drip_wall", False)
    client.fail_on = "irr_command"
    store.add("stop", "L01", args={})
    sync(store, program, ledger)
    assert len(services(events)) == 1
    client.fail_on = None
    sync(store, program, ledger)
    assert len(services(events)) == 2
    sync(store, program, ledger)
    assert len(services(events)) == 2


def test_a_stop_is_never_heap_deferred_and_the_other_commands_wait(client, store, program, ledger, events):
    in_sync(program)
    store.heap = True
    relay("drip_wall", True)
    run = store.add("run", "L04", args={"on_s": 30, "count": 4, "gap_s": 60})
    clear = store.add("clear_fault", "L04", args={})
    override = store.add("override", None, args={"kind": "fans", "minutes": 10})
    stop = store.add("stop", "L01", args={})
    sync(store, program, ledger)
    assert [event[2]["cmd"] for event in services(events)] == ["stop"]
    assert store.row(stop).status == "sent"
    assert [store.row(row).status for row in (run, clear, override)] == ["pending"] * 3

    store.heap = False
    sync(store, program, ledger)
    assert [store.row(row).status for row in (run, clear, override)] == ["sent"] * 3


@pytest.mark.parametrize(
    ("line_id", "args", "expected"),
    [
        ("L01", {"minutes": 5}, {"line": 0, "on_s": 300, "count": 1, "gap_s": 0, "prewet_s": 0, "flush_s": 0}),
        ("L05", {"minutes": 120}, {"line": 4, "on_s": 7200, "count": 1, "gap_s": 0, "prewet_s": 0, "flush_s": 0}),
        (
            "L02",
            {"minutes": 10, "prewet_s": 60, "flush_s": 90},
            {"line": 1, "on_s": 600, "count": 1, "gap_s": 0, "prewet_s": 60, "flush_s": 90},
        ),
        (
            "L04",
            {"on_s": 45, "count": 3, "gap_s": 90},
            {"line": 3, "on_s": 45, "count": 3, "gap_s": 90, "prewet_s": 0, "flush_s": 0},
        ),
        (
            "L08",
            {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240},
            {"line": 7, "on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240},
        ),
        # A row stored before the connection flipped still carries the bridge's mark.
        (
            "L01",
            {"minutes": 12, "bridge": True},
            {"line": 0, "on_s": 720, "count": 1, "gap_s": 0, "prewet_s": 0, "flush_s": 0},
        ),
    ],
)
def test_a_run_reaches_irr_command_with_its_arguments(client, store, program, ledger, events, line_id, args, expected):
    in_sync(program)
    run = store.add("run", line_id, args=args)
    sync(store, program, ledger)
    assert services(events) == [("service", "irr_command", {"cmd": "run", **expected})]
    assert store.row(run).status == "sent"
    sync(store, program, ledger)
    assert len(services(events)) == 1  # at most once


@pytest.mark.parametrize(
    ("kind", "line_id", "args", "service", "payload"),
    [
        (
            "clear_fault",
            "L07",
            {},
            "irr_command",
            {"cmd": "clear_fault", "line": 6, "on_s": 0, "count": 0, "gap_s": 0, "prewet_s": 0, "flush_s": 0},
        ),
        (
            "clear_fault",
            None,
            {},
            "irr_command",
            {"cmd": "clear_fault", "line": 255, "on_s": 0, "count": 0, "gap_s": 0, "prewet_s": 0, "flush_s": 0},
        ),
        ("override", None, {"kind": "fans", "minutes": 30}, "manual_override", {"kind": "fans", "minutes": 30}),
        ("override", None, {"kind": "restart"}, "manual_override", {"kind": "restart", "minutes": 0}),
    ],
)
def test_clear_fault_and_override_reach_their_services(
    client, store, program, ledger, events, kind, line_id, args, service, payload
):
    in_sync(program)
    row = store.add(kind, line_id, args=args)
    sync(store, program, ledger)
    assert services(events) == [("service", service, payload)]
    assert store.row(row).status == "sent"


@pytest.mark.parametrize(
    ("kind", "line_id", "args"),
    [
        ("run", "L01", {"on_s": 30}),  # a drip runs minutes
        ("run", "L04", {"minutes": 5}),  # a mister pulses
        ("run", "L02", {"minutes": 5}),  # a fertilizer run needs its prewet and flush
        ("run", "L03", {"minutes": 5}),  # the master valve takes no runs
        ("run", None, {"minutes": 5}),
        ("run", "L01", {"minutes": True}),
        ("override", None, {"minutes": 5}),
    ],
)
def test_a_command_whose_args_cannot_be_sent_ends_failed_and_is_never_sent(
    client, store, program, ledger, events, kind, line_id, args
):
    in_sync(program)
    row = store.add(kind, line_id, args=args)
    sync(store, program, ledger)
    assert services(events) == []
    assert (store.row(row).status, store.row(row).fail_reason) == ("failed", task.BAD_ARGS)


def test_a_command_not_sent_within_2_min_fails_too_old_and_is_never_sent(client, store, program, ledger, events):
    run = store.add("run", "L01", age_s=121, args={"minutes": 5})
    clear = store.add("clear_fault", "L04", age_s=121, args={})
    stop = store.add("stop", "L01", age_s=121, args={})
    sync(store, program, ledger)
    for row in (run, clear, stop):
        assert (store.row(row).status, store.row(row).fail_reason) == ("failed", task.TOO_OLD)
    assert services(events) == []


def test_a_call_that_never_reached_the_transport_ends_failed_and_an_unknown_outcome_stays_sent(
    client, store, program, ledger, events, monkeypatch
):
    in_sync(program)
    run = store.add("run", "L01", args={"minutes": 5})
    client.fail_on = "irr_command"
    sync(store, program, ledger)
    assert store.row(run).status == "sent"  # the socket errored: it may have gone

    other = store.add("run", "L04", args={"on_s": 30, "count": 1, "gap_s": 30})
    monkeypatch.setitem(
        shared.esp32, "services", {"irr_command": "irr_command", "irr_program_begin": "irr_program_begin"}
    )
    real = esp32_push.push_policy_transaction

    async def lose_the_service(calls, **kwargs):
        shared.esp32["services"] = {"irr_program_begin": "x", "irr_command": None}
        return await real(calls, **kwargs)

    monkeypatch.setattr(esp32_push, "push_policy_transaction", lose_the_service)
    sync(store, program, ledger)
    assert (store.row(other).status, store.row(other).fail_reason) == ("failed", "policy_service_unavailable")


def test_a_run_waits_for_the_engines_first_readback_and_a_stop_does_not(client, store, program, ledger, events):
    """In the seconds before its first tick the engine logs and ignores a service call, so a run sent then would be
    marked sent and lost. A Stop goes anyway, and is re-sent until its relays read OFF."""
    relay("drip_wall", True)
    run = store.add("run", "L01", args={"minutes": 5})
    stop = store.add("stop", "L04", args={})
    sync(store, program, ledger)
    assert store.row(stop).status == "sent" and store.row(run).status == "pending"
    assert [event[2]["cmd"] for event in services(events)] == ["stop"]
    in_sync(program)
    sync(store, program, ledger)
    assert store.row(run).status == "sent"


def test_while_the_preflight_fails_no_engine_command_is_marked_sent(
    client, store, program, ledger, events, monkeypatch
):
    monkeypatch.setitem(shared.esp32, "client", None)
    run = store.add("run", "L01", args={"minutes": 5})
    stop = store.add("stop", "L01", args={})
    sync(store, program, ledger)
    assert store.row(run).status == "pending" and store.row(stop).status == "pending" and services(events) == []


# ── Step 0: the capability flip ────────────────────────────────────────────────


def test_a_connection_flipped_to_pre_engine_gets_no_program_or_irr_command_and_the_bridge_takes_over(
    client, store, program, ledger, events, monkeypatch, caplog
):
    on_rom()
    sync(store, program, ledger)
    assert len(program.rows) == 1
    events.clear()

    # The controller came back on pre-engine firmware: a new connection, without the engine's services.
    monkeypatch.setattr(shared, "transport_generation", GENERATION + 1)
    shared.irrigation_readback.clear()
    monkeypatch.setitem(shared.esp32, "services", {"set_band_anchor": object()})
    irrig_enabled(True)
    relay("drip_wall", False, generation=GENERATION + 1)
    shared.irrigation_sync_requested.set()
    run = store.add("run", "L01", args={"minutes": 12, "bridge": True})
    with caplog.at_level(logging.INFO, logger="irrigation"):
        sync(store, program, ledger)
        relay("drip_wall", True, generation=GENERATION + 1)
        stop = store.add("stop", None, args={"bridge": True})
        sync(store, program, ledger)
    notes = [record for record in caplog.records if "irrigation engine not on controller" in record.getMessage()]
    assert len(notes) == 1

    assert services(events) == []  # no irr_command and no program
    assert program.reads == 1 and len(program.rows) == 1 and ledger.started == []
    assert store.row(run).status == "sent" and ("press", BUTTON) in calls(events)
    assert store.row(stop).status == "sent"
    assert ("switch", ENABLED, False) in calls(events) and ("switch", "drip___wall", False) in calls(events)

    # A new pre-engine connection logs once more.
    monkeypatch.setattr(shared, "transport_generation", GENERATION + 2)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="irrigation"):
        sync(store, program, ledger)
    assert sum("irrigation engine not on controller" in record.getMessage() for record in caplog.records) == 1


def test_other_commands_on_a_pre_engine_connection_end_failed_at_2_min_and_are_never_sent(
    client, store, program, ledger, events, monkeypatch
):
    monkeypatch.setitem(shared.esp32, "services", {"set_band_anchor": object()})
    clear = store.add("clear_fault", "L04", args={})
    override = store.add("override", None, args={"kind": "fans", "minutes": 5})
    sync(store, program, ledger)
    assert store.row(clear).status == "pending" and store.row(override).status == "pending" and services(events) == []
    store._set(clear.command_id, created_at=now() - timedelta(seconds=121))
    store._set(override.command_id, created_at=now() - timedelta(seconds=121))
    sync(store, program, ledger)
    assert store.row(clear).fail_reason == task.TOO_OLD and store.row(override).fail_reason == task.TOO_OLD
    assert services(events) == []


def test_the_engine_row_is_false_on_a_pre_engine_connect_so_the_api_reports_src_null():
    written = []

    class Conn:
        async def execute(self, _sql, *args):
            written.append(args[1:])

    class Lend:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    pool = SimpleNamespace(acquire=lambda: Lend())
    assert asyncio.run(task.record_engine_capability(pool, {"set_band_anchor": 1})) is False
    assert written == [("irr_engine", "false")]


# ── Readbacks ──────────────────────────────────────────────────────────────────


def test_the_three_text_sensors_route_to_system_state():
    assert {name: STATE_MAP[name] for name in shared.IRRIGATION_TEXT_SENSORS} == {
        "irr_program_id": "irr_program_id",
        "irr_state": "irr_state",
        "irr_events": "irr_events",
    }


def test_the_text_sensor_handler_keeps_each_value_and_an_empty_one_is_a_reading(monkeypatch):
    import ingestor

    monkeypatch.setattr(shared, "transport_generation", GENERATION)
    monkeypatch.setattr(shared, "irrigation_readback", {})
    monkeypatch.setattr(shared, "irr_state_changes", deque())
    monkeypatch.setattr(ingestor.state, "key_to_object_id", {1: "irr_state", 2: "irr_program_id", 3: "irr_events"})
    monkeypatch.setattr(ingestor.state, "key_to_type", {1: "text", 2: "text", 3: "text"})
    monkeypatch.setattr(ingestor.state, "system", {})
    monkeypatch.setattr(ingestor.state, "pending_states", [])
    monkeypatch.delenv("VERDIFY_OBSERVATION_SPOOL_ENABLED", raising=False)

    def text(key, value, **fields):
        ingestor.on_state_change(SimpleNamespace(key=key, state=value, **fields), native_generation=GENERATION)

    text(1, "", missing_state=True)  # not published yet: no reading
    assert shared.irrigation_readback == {} and ingestor.state.pending_states == []

    text(2, "h=0000000000000000 src=rom")
    text(1, "d=0,schedule,11,1790000000,1790000300 v=hhh", missing_state=False)
    text(1, "", missing_state=False)  # the last token left
    text(3, "")
    assert shared.irrigation_readback == {
        "irr_program_id": "h=0000000000000000 src=rom",
        "irr_state": "",
        "irr_events": "",
    }
    assert [change.text for change in shared.irr_state_changes] == ["d=0,schedule,11,1790000000,1790000300 v=hhh", ""]
    assert {change.generation for change in shared.irr_state_changes} == {GENERATION}
    assert ingestor.state.pending_states == [
        ("irr_program_id", "h=0000000000000000 src=rom"),
        ("irr_state", "d=0,schedule,11,1790000000,1790000300 v=hhh"),
        ("irr_state", ""),
        ("irr_events", ""),
    ]


def test_a_new_connection_clears_the_readbacks_and_keeps_the_unread_changes(monkeypatch):
    monkeypatch.setattr(shared, "irrigation_readback", {"irr_program_id": "h=0000000000000000 src=rom"})
    monkeypatch.setattr(shared, "irr_state_changes", deque())
    readback("irr_state", "v=hhh")
    generation = shared.note_transport_connected()
    assert shared.irrigation_readback == {} and shared.transport_generation == generation
    assert [change.text for change in shared.irr_state_changes] == ["v=hhh"]


# ── The ledger's tracking (its SQL and the golden replay run on PostgreSQL) ────


def test_a_token_gets_one_row_vpd_pulses_get_none_and_a_row_closes_once_the_token_has_left(
    client, store, program, ledger
):
    in_sync(program)
    start = EARLIER
    at = now() - timedelta(minutes=10)
    readback("irr_state", f"d=0,schedule,11,{start},{start + 300} v=hhh", at=at)
    readback("irr_state", f"d=0,schedule,11,{start},{start + 300} m=3,vpd,0,{start},{start + 60} v=hhh", at=at)
    readback("irr_state", f"d=0,schedule,11,{start},{start + 360} v=hhh", at=at)  # a pause moves the planned end
    sync(store, program, ledger)
    assert [run.line_id for run in ledger.started] == ["L01"]  # no row for the VPD pulse, one for the run
    assert ledger.closed == {}

    ledger.edges = (False, [(at, True), (at + timedelta(seconds=300), False)])
    readback("irr_state", "v=hhh", at=at + timedelta(seconds=301))
    sync(store, program, ledger)
    assert ledger.closed[1] == run_closed(at + timedelta(seconds=300), 300, 1)
    sync(store, program, ledger)
    assert len(ledger.closed) == 1 and task._memory.open_runs == {}


def test_a_row_waits_out_the_close_delay_and_a_token_back_in_time_stays_open(client, store, program, ledger):
    in_sync(program)
    start = EARLIER
    readback("irr_state", f"m=3,schedule,12,{start},{start + 600} v=hhh")
    sync(store, program, ledger)
    readback("irr_state", f"m=3,vpd,0,{start + 90},{start + 150} v=hhh")  # a VPD pulse takes the m= token for a moment
    sync(store, program, ledger)
    assert ledger.closed == {}  # gone for less than the close delay
    readback("irr_state", f"m=3,schedule,12,{start},{start + 600} v=hhh")
    sync(store, program, ledger)
    assert len(ledger.started) == 1 and ledger.closed == {}
    assert task._memory.open_runs[("L04", "schedule", 12, start)].left_at is None


def test_a_token_after_its_row_closed_is_recorded_again_for_the_store_to_reopen(client, store, program, ledger):
    in_sync(program)
    start = EARLIER
    at = now() - timedelta(minutes=5)
    readback("irr_state", f"m=3,schedule,12,{start},{start + 600} v=hhh", at=at)
    readback("irr_state", "w=3,occ v=hhh", at=at + timedelta(seconds=30))
    sync(store, program, ledger)
    assert 1 in ledger.closed  # occupancy holds the window past the close delay
    readback("irr_state", f"m=3,schedule,12,{start},{start + 600} v=hhh", at=at + timedelta(seconds=240))
    sync(store, program, ledger)
    assert len(ledger.started) == 2 and ("L04", "schedule", 12, start) in task._memory.open_runs


def test_a_row_left_open_by_a_restart_is_tracked_and_closed(client, store, program, ledger):
    in_sync(program)
    started_at = now() - timedelta(minutes=30)
    ledger.stored = [{"id": 41, "line_id": "L01", "slot_id": 11, "cause": "schedule", "started_at": started_at}]
    ledger.edges = (False, [(started_at, True), (started_at + timedelta(seconds=120), False)])
    at = now() - timedelta(minutes=1)
    readback("irr_state", "v=hhh", at=at)
    sync(store, program, ledger)
    assert ledger.closed[41] == run_closed(started_at + timedelta(seconds=120), 120, 1)


def test_the_ledger_never_stops_the_commands_or_the_program(client, store, program, ledger, events):
    on_rom()

    async def broken(*_args):
        raise ConnectionError("database down")

    ledger.open_rows = broken
    stop = store.add("stop", "L01", args={})
    sync(store, program, ledger)
    assert store.row(stop).status == "sent" and len(program.rows) == 1


def test_a_row_the_model_refuses_is_dropped_and_never_stalls_the_ledger(client, store, program, ledger):
    in_sync(program)
    start = EARLIER
    readback("irr_state", f"d=0,schedule,11,{start},{start + 300} v=hhh")
    readback("irr_state", f"d=1,schedule,12,{start},{start + 300} v=hhh")
    readback("irr_events", "5,3,0,20395,sk,flt")
    real = ledger.record_start

    async def refuses_the_first(run, observed_at):
        if run.line_id == "L01":
            IrrigationCommandRow(kind="bogus", source="agent:steward", actor="steward")
        return await real(run, observed_at)

    async def refuses_the_ring(_events, _running, _now):
        IrrigationCommandRow(kind="bogus", source="agent:steward", actor="steward")

    ledger.record_start = refuses_the_first
    ledger.record_outcomes = refuses_the_ring
    sync(store, program, ledger)
    assert [run.line_id for run in ledger.started] == ["L02"]
    assert len(shared.irr_state_changes) == 0
    assert task._memory.events_text == "5,3,0,20395,sk,flt"  # not retried


def test_a_database_error_keeps_the_reading_for_the_next_pass(client, store, program, ledger):
    in_sync(program)
    start = EARLIER
    readback("irr_state", f"d=0,schedule,11,{start},{start + 300} v=hhh")
    real = ledger.record_start

    async def down(_run, _observed_at):
        raise ConnectionError("database down")

    ledger.record_start = down
    sync(store, program, ledger)
    assert len(shared.irr_state_changes) == 1 and ledger.started == []
    ledger.record_start = real
    sync(store, program, ledger)
    assert len(shared.irr_state_changes) == 0 and [run.line_id for run in ledger.started] == ["L01"]


def test_a_reading_outside_the_grammar_is_no_reading(client, store, program, ledger):
    in_sync(program)
    readback("irr_state", "d=bogus")
    readback("irr_events", "not,an,event")
    sync(store, program, ledger)
    assert ledger.started == [] and ledger.outcomes == []


def test_outcomes_are_read_once_per_text_with_the_running_rows_set_aside(client, store, program, ledger):
    in_sync(program)
    start = EARLIER
    readback("irr_state", f"d=0,run_now,0,{start},{start + 300} v=hhh")
    readback("irr_events", "5,3,0,20395,sk,flt")
    sync(store, program, ledger)
    sync(store, program, ledger)
    [(events_seen, running)] = ledger.outcomes
    assert [event.seq for event in events_seen] == [5] and running == [1]


# ── Closing a row: pure ────────────────────────────────────────────────────────

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def close(edges, **changes):
    fields = {
        "started_at": T0,
        "left_at": at(300),
        "planned_end": at(300),
        "seed_on": False,
        "watched": True,
        "now": at(320),
    }
    return task.run_close([(at(ts), on) for ts, on in edges], **{**fields, **changes})


def test_ended_at_is_the_off_edge_and_open_s_and_pulses_come_from_the_edges():
    assert close([(1, True), (31, False), (61, True), (91, False)]) == run_closed(at(91), 60, 2)


def test_an_off_edge_may_come_just_after_the_token_left():
    assert close([(1, True), (301, False)]) == run_closed(at(301), 300, 1)


def test_an_on_edge_after_the_token_left_is_another_runs():
    assert close([(1, True), (299, False), (305, True), (310, False)]) == run_closed(at(299), 298, 1)


def test_a_relay_on_before_the_run_counts_from_its_start():
    assert close([(100, False)], seed_on=True) == run_closed(at(100), 100, 1)


def test_a_missing_off_edge_waits_for_the_flush_then_takes_the_planned_end():
    assert close([(1, True)]) is None
    assert close([(1, True)], now=at(300 + 120)) == run_closed(at(300), 299, 1)


def test_a_token_that_left_early_never_ends_past_when_it_left():
    assert close([(1, True)], left_at=at(100), now=at(500)) == run_closed(at(100), 99, 1)


def test_a_run_that_never_opened_has_no_open_time():
    assert close([]) == run_closed(at(300), 0, 0)
    assert close([], planned_end=None) == run_closed(at(300), 0, 0)


def test_across_a_reconnect_the_rows_edge_is_not_an_edge_and_the_planned_end_stands():
    closed = close([(1, True), (1800, False)], left_at=at(1800), planned_end=at(300), watched=False, now=at(1820))
    assert closed == run_closed(at(300), 299, 1)


def test_after_a_restart_with_no_planned_end_the_off_edge_stands():
    assert close([(1, True), (40, False)], planned_end=None, watched=False) == run_closed(at(40), 39, 1)


# ── The occurrence's local day: pure ───────────────────────────────────────────


def spec(minute: int, *, kind: str = "clock") -> ScheduleSpec:
    return ScheduleSpec(slot_id=1, line_id="L01", days=0x7F, start=Start(kind=kind, minute=minute), on_s=300)


def test_an_occurrence_belongs_to_the_day_it_opened_on():
    started = datetime(2026, 11, 2, 13, 0, 3, tzinfo=UTC)  # 06:00 in Denver
    assert task.occurrence_day(spec(360), started) == date(2026, 11, 2)


def test_a_late_start_past_midnight_belongs_to_the_day_before():
    started = datetime(2026, 11, 3, 7, 5, tzinfo=UTC)  # 00:05 in Denver on the 3rd
    assert task.occurrence_day(spec(23 * 60 + 50), started) == date(2026, 11, 2)


def test_a_solar_start_past_midnight_belongs_to_the_day_before():
    # Sunset + 240 min in June falls early the next morning.
    start = spec(240, kind="sunset")
    planned = occurrence_start(start.start, date(2026, 6, 21))
    assert planned.date() == date(2026, 6, 22)
    assert task.occurrence_day(start, planned + timedelta(seconds=3)) == date(2026, 6, 21)


def test_a_schedule_that_is_gone_takes_the_day_it_opened_on():
    assert task.occurrence_day(None, datetime(2026, 11, 3, 7, 5, tzinfo=UTC)) == date(2026, 11, 3)


# ── Alerts: pure (the queries run on PostgreSQL) ───────────────────────────────


def relay_row(equipment, last_on_ts=None, on_before=None):
    return {"equipment": equipment, "last_on_ts": last_on_ts, "on_before": on_before}


ENABLED_L01_L04 = (1 << 0) | (1 << 3)


def test_a_relay_on_while_the_program_disables_its_line_breaches():
    hits = alert_tasks._applied_fence_hits([relay_row("drip_center_fert", T0)], ENABLED_L01_L04)
    assert hits == [("relay_on_line_disabled", "drip_center_fert", T0, "L06")]


def test_enabling_l06_quiets_the_fence_for_its_relay():
    assert alert_tasks._applied_fence_hits([relay_row("drip_center_fert", T0)], ENABLED_L01_L04 | 1 << 5) == []


def test_no_on_row_in_the_window_is_no_breach_and_fog_is_not_fenced():
    rows = [relay_row("drip_center_fert", None, True), relay_row("fog", T0)]
    assert alert_tasks._applied_fence_hits(rows, 0) == []


def test_l03_has_only_its_own_rule():
    # L03's enable bit is never read: it opens with fertilizer runs.
    rows = [relay_row("fert_master_valve", T0), relay_row("drip_wall_fert", T0)]
    assert alert_tasks._applied_fence_hits(rows, 1 << 1) == []
    hits = alert_tasks._applied_fence_hits([relay_row("fert_master_valve", T0)], 0x7FF)
    assert hits == [("fert_master_without_fert_run", "fert_master_valve", T0, "L03")]


def test_a_feed_already_on_when_the_window_opened_covers_l03():
    rows = [relay_row("fert_master_valve", T0), relay_row("drip_center_fert", None, True)]
    assert alert_tasks._applied_fence_hits(rows, 1 << 5) == []


def minutes(*values: int) -> list[datetime]:
    return [T0 + timedelta(minutes=value) for value in values]


def test_a_window_is_watched_with_climate_rows_and_no_reconnect():
    climate = minutes(*range(0, 121))
    assert alert_tasks._irrigation_watched(T0, T0 + timedelta(hours=2), climate=climate, reconnects=[])


def test_a_climate_gap_over_300_s_or_a_reconnect_means_it_was_not_watched():
    end = T0 + timedelta(hours=2)
    gap = minutes(*range(0, 60), *range(66, 121))
    assert not alert_tasks._irrigation_watched(T0, end, climate=gap, reconnects=[])
    assert not alert_tasks._irrigation_watched(T0, end, climate=minutes(*range(0, 121)), reconnects=minutes(30))
    assert not alert_tasks._irrigation_watched(T0, end, climate=minutes(*range(0, 100)), reconnects=[])  # stopped early
    assert not alert_tasks._irrigation_watched(T0, end, climate=minutes(*range(30, 121)), reconnects=[])  # started late
    # A row just outside the window, and a gap of exactly 300 s, are fine.
    assert alert_tasks._irrigation_watched(T0, end, climate=minutes(*range(0, 121)), reconnects=minutes(-1, 121))
    assert alert_tasks._irrigation_watched(T0, end, climate=minutes(*range(0, 61), *range(65, 121)), reconnects=[])


def _fault_view(text: str) -> alert_tasks._IrrigationView:
    return alert_tasks._IrrigationView(
        engine=True, state=parse_irr_state(text), zones={"L07": "south", "L04": "center"}
    )


def test_a_latch_raises_the_fault_and_a_back_off_alone_raises_nothing():
    [alert] = alert_tasks._irrigation_line_faulted_alerts(_fault_view("f=6,l,1790000000 f=3,b,1790000000 v=hhh"))
    assert alert["sensor_id"] == "irrigation.L07" and alert["zone"] == "south" and alert["severity"] == "warning"
    assert alert["category"] == "water"
    assert alert["details"]["line_id"] == "L07" and alert["details"]["fault_until"].startswith("2026-")
    AlertEnvelope.model_validate(alert)
    assert alert_tasks._irrigation_line_faulted_alerts(_fault_view("f=3,b,1790000000 v=hhh")) == []
    assert alert_tasks._irrigation_line_faulted_alerts(_fault_view("v=hhh")) == []


def test_a_latch_with_no_clock_yet_has_no_end_to_name():
    [alert] = alert_tasks._irrigation_line_faulted_alerts(_fault_view("f=6,l,0 v=hhh"))
    assert alert["details"]["fault_until"] is None
    AlertEnvelope.model_validate(alert)


# ── The service calls ──────────────────────────────────────────────────────────


def firmware_services() -> dict[str, set[str]]:
    engine = yaml.safe_load((ROOT / "firmware/greenhouse/irrigation_engine.yaml").read_text())
    return {service["service"]: set(service.get("variables") or {}) for service in engine["api"]["services"]}


def test_the_program_calls_carry_exactly_the_firmware_services_arguments(client, events):
    """aioesphomeapi reads each of a service's declared arguments from the payload by name, so a drifted name raises."""
    declared = firmware_services()
    record = snapshot(360).compiled
    asyncio.run(esp32_push.push_irrigation_program(record.record, record.sha256))
    for _, service, payload in services(events):
        assert set(payload) == declared[service], service
    assert {service for _, service, _ in services(events)} == {
        "irr_program_begin",
        "irr_program_chunk",
        "irr_program_commit",
    }


@pytest.mark.parametrize(
    "row",
    [
        command_row("stop", "L01"),
        command_row("clear_fault", None),
        command_row("run", "L01", minutes=5),
        command_row("run", "L08", on_s=30, count=4, gap_s=60, prewet_s=120, flush_s=240),
        command_row("override", None, kind="fans", minutes=5),
    ],
    ids=lambda row: f"{row.kind}-{row.line_id}",
)
def test_the_command_calls_carry_exactly_the_firmware_services_arguments(row):
    call = esp32_push.irrigation_command_call(row)
    assert set(call.payload) == firmware_services()[call.service]


def test_the_text_sensor_routes_are_the_firmware_entities_object_ids():
    sensors = yaml.safe_load((ROOT / "firmware/greenhouse/irrigation_engine.yaml").read_text())["text_sensor"]
    assert {_esphome_wire_slug(sensor["name"]) for sensor in sensors} == set(shared.IRRIGATION_TEXT_SENSORS)
    assert {sensor["id"] for sensor in sensors} == set(shared.IRRIGATION_TEXT_SENSORS)


def test_the_stop_call_names_each_line_by_its_device_idx():
    assert esp32_push.irrigation_command_call(command_row("stop", "L10")).payload["line"] == 9
    assert esp32_push.irrigation_command_call(command_row("stop", None)).payload["line"] == 255
    assert esp32_push.irrigation_command_call(command_row("stop", "L11")).payload["line"] == 10


def test_a_policy_failure_says_whether_the_call_reached_the_transport():
    failed = esp32_push._policy_result(
        [esp32_push.PolicyServiceCall("irr_command", {})], [], 0, "writer_lease_not_held"
    )
    assert esp32_push.policy_call_unsent(failed)
    unknown = esp32_push._policy_result(
        [esp32_push.PolicyServiceCall("irr_command", {})], [], 0, "command_timeout_outcome_unknown"
    )
    assert not esp32_push.policy_call_unsent(unknown)
    assert not esp32_push.policy_call_unsent(esp32_push.PolicyTransactionResult(()))
