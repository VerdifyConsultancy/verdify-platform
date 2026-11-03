"""VD-42 (#1157) on a private PostgreSQL: the compile's read, irrigation_push, the ledger's golden replay and the alerts.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback PostgreSQL
and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips. ``irrigation_db_support`` builds M4,
the dashboard login (282) and M3 (283); this file adds migration 280's statements (``v_desired_state_current``) and
stand-ins for what production grants the ingestor login outside these migrations: SELECT on ``system_state``,
``climate`` and ``equipment_state``.

Everything runs as ``verdify_ingestor_runtime_login``, so a statement outside its grants fails here. The stores and
``_sync_ledger`` run from ``ingestor/tasks/irrigation.py``'s own source and the alert producers from
``ingestor/tasks/alerts.py``'s, without importing or starting the ingestor. A replay feeds ``shared`` as the text-sensor
handler does; relay edges and climate rows are written as the owner, stamped relative to now.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
import logging
import sys
import types
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import irrigation_db_support as support
import pytest
from conftest import isolated_mutation_test_dsn

from verdify_schemas import irrigation as irr
from verdify_schemas.alerts import AlertEnvelope

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

INGESTOR_DIR = str(support.REPO_ROOT / "ingestor")
if INGESTOR_DIR not in sys.path:
    sys.path.insert(0, INGESTOR_DIR)

import shared  # noqa: E402

INGESTOR = "verdify_ingestor_runtime_login"
ACTIVITY_MIGRATION = support.REPO_ROOT / "db" / "migrations" / "280-desired-state-activity-log.sql"
ALERTS = support.REPO_ROOT / "ingestor" / "tasks" / "alerts.py"
VECTORS = support.REPO_ROOT / "verdify_schemas" / "tests" / "fixtures" / "irrigation_program_vectors.json"
EPOCH_DAY = dt.date(1970, 1, 1)

STAND_IN_GRANTS = """
GRANT SELECT ON public.system_state, public.climate, public.equipment_state TO verdify_ingestor_runtime;
"""
# The seed's three windows on L04 stay; L01 and L04 are enabled.
RESET = """
DELETE FROM public.irrigation_ledger;
DELETE FROM public.irrigation_push;
DELETE FROM public.irrigation_command;
DELETE FROM public.irrigation_slot WHERE slot_id > 3;
DELETE FROM public.desired_state;
DELETE FROM public.equipment_state;
DELETE FROM public.climate;
DELETE FROM public.system_state;
UPDATE public.irrigation_line SET enabled = line_id IN ('L01', 'L04'), vpd_member = CASE WHEN line_id = 'L04' THEN true
    WHEN line_id IN ('L07', 'L09') THEN false END, schedule_member = CASE WHEN line_id IN ('L04', 'L07', 'L09') THEN false END;
"""


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


async def admin_fetch(sql: str, *args):
    conn = await connect()
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def fetch(sql: str, *args):
    return run(admin_fetch(sql, *args))


async def admin_execute(sql: str, *args):
    conn = await connect()
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


def execute(sql: str, *args) -> None:
    run(admin_execute(sql, *args))


@pytest.fixture(scope="module")
def database():
    async def setup():
        conn = await connect()
        try:
            await support.prepare_irrigation_tables(conn)
            sections = support.schema_sections()
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute(
                    "\n".join(
                        sections[(name, "TABLE", "public")] for name in ("setpoint_snapshot", "crop_band_anchors")
                    )
                )
                await conn.execute(
                    "SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(ACTIVITY_MIGRATION)
                )
            finally:
                await conn.execute("RESET search_path")
                await conn.execute("RESET ROLE")
            await conn.execute(STAND_IN_GRANTS)
        finally:
            await conn.close()

    run(setup())


class OnePool:
    """The stores' pool: every acquire lends the same ingestor-login connection."""

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


@pytest.fixture(scope="module")
def task():
    namespace = support.load_irrigation_task()
    namespace["shared"] = shared
    return namespace


@pytest.fixture(scope="module")
def alerts(task):
    """The irrigation alert producers from ingestor/tasks/alerts.py's own source."""

    def wanted(name: str) -> bool:
        return name.startswith(("_IRR_", "_irrigation_", "_Irrigation")) or name in {
            "_MissedOccurrence",
            "_applied_fence_hits",
        }

    body = []
    for node in ast.parse(ALERTS.read_text()).body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef | ast.ClassDef):
            names = {node.name}
        else:
            continue
        if any(wanted(name) for name in names):
            body.append(node)
    module = types.ModuleType("irrigation_alerts_under_test")
    sys.modules[module.__name__] = module
    module.__dict__.update(
        {
            "UTC": UTC,
            "date": dt.date,
            "datetime": datetime,
            "_td": timedelta,
            "log": logging.getLogger("irrigation-alerts-under-test"),
            "dataclass": dataclass,
            "Mapping": Mapping,
            "Sequence": Sequence,
            "DEVICE_IDX": irr.DEVICE_IDX,
            "REASON_WORDS": irr.REASON_WORDS,
            "IrrState": irr.IrrState,
            "ProgramId": irr.ProgramId,
            "ProgramRecordError": irr.ProgramRecordError,
            "agenda": irr.agenda,
            "irr_state_reading": irr.irr_state_reading,
            "local_tz": irr.local_tz,
            "occurrence_start": irr.occurrence_start,
            "parse_irr_program_id": irr.parse_irr_program_id,
            "unpack_program": irr.unpack_program,
            "LINE_RELAYS": task["LINE_RELAYS"],
            "_schedule_specs": task["_schedule_specs"],
            "read_program": task["read_program"],
        }
    )
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ALERTS), "exec"), module.__dict__)  # noqa: S102
    return module.__dict__


@pytest.fixture(autouse=True)
def seeded(database, monkeypatch):
    run(admin_execute(RESET))
    monkeypatch.setattr(shared, "transport_generation", 7)
    monkeypatch.setattr(shared, "irrigation_readback", {})
    monkeypatch.setattr(shared, "irr_state_changes", deque())
    yield


def now() -> datetime:
    return datetime.now(UTC)


def local_day(moment: datetime) -> dt.date:
    return moment.astimezone(irr.local_tz()).date()


def day_number(day: dt.date) -> int:
    return (day - EPOCH_DAY).days


def schedule(
    line_id: str, moment: datetime, *, days: int = 127, degraded_only: bool = False, enabled: bool = True
) -> int:
    """A clock schedule whose start is ``moment``'s local minute."""
    local = moment.astimezone(irr.local_tz())
    kind = irr.LINE_KIND[line_id]
    on_s, count, gap_s = (300, 1, 0) if kind in ("drip", "fert_drip") else (30, 2, 60)
    [row] = fetch(
        "INSERT INTO public.irrigation_slot (line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, "
        "degraded_only, source, updated_by) VALUES ($1, $2, $3, 'clock', $4, $5, $6, $7, $8, 'system', 'test') "
        "RETURNING slot_id",
        line_id,
        enabled,
        days,
        local.hour * 60 + local.minute,
        on_s,
        count,
        gap_s,
        degraded_only,
    )
    return row["slot_id"]


def sent_command(line_id: str, sent_at: datetime, actor: str = "jason") -> uuid.UUID:
    [row] = fetch(
        "INSERT INTO public.irrigation_command (kind, line_id, args, status, source, actor, created_at, sent_at) "
        "VALUES ('run', $1, '{}', 'sent', 'agent:steward', $2, $3, $3) RETURNING command_id",
        line_id,
        actor,
        sent_at,
    )
    return row["command_id"]


def edge(equipment: str, at: datetime, on: bool) -> None:
    execute("INSERT INTO public.equipment_state (ts, equipment, state) VALUES ($1, $2, $3)", at, equipment, on)


def system_state(entity: str, value: str, at: datetime | None = None) -> None:
    execute("INSERT INTO public.system_state (ts, entity, value) VALUES ($1, $2, $3)", at or now(), entity, value)


def climate_rows(start: datetime, end: datetime, *, gaps: Sequence[tuple[datetime, datetime]] = ()) -> None:
    execute(
        "INSERT INTO public.climate (ts) SELECT g FROM generate_series($1::timestamptz, $2::timestamptz, interval '60 seconds') "
        "AS g WHERE NOT EXISTS (SELECT 1 FROM unnest($3::timestamptz[], $4::timestamptz[]) AS gap(a, b) WHERE g > gap.a AND g < gap.b)",
        start,
        end,
        [gap[0] for gap in gaps],
        [gap[1] for gap in gaps],
    )


def rows_of(**where) -> list:
    clauses = [f"{key} = ${index}" for index, key in enumerate(where, start=1)]
    sql = "SELECT * FROM public.irrigation_ledger"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return fetch(sql + " ORDER BY started_at NULLS LAST, id", *where.values())


class Replay:
    """The controller's text sensors and one ingestor's ledger step."""

    def __init__(self, task) -> None:
        self.task = task
        self.memory = task["EngineMemory"]()
        self.generation = shared.transport_generation

    def state(self, at: datetime, text: str) -> None:
        shared.note_irrigation_readback("irr_state", text, self.generation, at)

    def events(self, text: str) -> None:
        shared.note_irrigation_readback("irr_events", text, self.generation, now())

    def reconnect(self, at: datetime) -> None:
        """A new connection: the readbacks are gone until its state burst, and the connect writes its irr_engine row."""
        self.generation += 1
        shared.transport_generation = self.generation
        shared.irrigation_readback.clear()
        system_state("irr_engine", "true", at)

    def restart(self) -> None:
        """A new ingestor process: everything in memory is gone."""
        self.memory = self.task["EngineMemory"]()

    def tick(self) -> None:
        async def body():
            conn = await connect(INGESTOR)
            try:
                await self.task["_sync_ledger"](self.task["LedgerStore"](OnePool(conn)), self.memory)
            finally:
                await conn.close()

        run(body())


@pytest.fixture
def replay(task):
    return Replay(task)


def replay_base(hours_ago: float) -> datetime:
    """A whole second that long ago, moved earlier when the next hours hold a local midnight: a replay's local days
    are then one day, whenever CI runs."""
    moment = whole(now() - timedelta(hours=hours_ago))
    hour = moment.astimezone(irr.local_tz()).hour
    return moment - timedelta(hours=hour - 19) if hour >= 20 else moment


def token(key: str, line: int, cause: str, slot: int, start: datetime, end: datetime) -> str:
    return f"{key}={line},{cause},{slot},{int(start.timestamp())},{int(end.timestamp())}"


def whole(moment: datetime) -> datetime:
    return datetime.fromtimestamp(int(moment.timestamp()), UTC)


# ── The golden replay ─────────────────────────────────────────────────────────


def test_golden_replay_two_outcomes_in_one_tick_and_a_30_minute_disconnect(replay, task, alerts):
    base = replay_base(6)
    day = local_day(base)
    number = day_number(day)
    drip = schedule("L01", base)
    skipped = schedule("L07", base + timedelta(minutes=1))
    during = schedule("L01", base + timedelta(hours=1))
    # Three more due inside the 30-minute disconnect that opens at base + 1 h.
    unseen = schedule("L01", base + timedelta(minutes=65))
    missed = schedule("L01", base + timedelta(minutes=70))
    schedule("L01", base + timedelta(minutes=75))  # no outcome anywhere

    # A drip with its edges, then two outcomes in one tick. A VPD pulse in between gets no row.
    start = base
    replay.state(start, token("d", 0, "schedule", drip, start, start + timedelta(seconds=300)) + " v=hhh")
    edge("drip_wall", start + timedelta(seconds=1), True)
    replay.tick()
    assert [row["slot_id"] for row in rows_of()] == [drip] and rows_of()[0]["ended_at"] is None
    edge("drip_wall", start + timedelta(seconds=300), False)
    replay.state(start + timedelta(seconds=300), "v=hhh")
    replay.state(
        start + timedelta(seconds=400), token("m", 3, "vpd", 0, start, start + timedelta(seconds=60)) + " v=hhh"
    )
    replay.state(start + timedelta(seconds=460), "v=hhh")
    replay.events(f"1,0,{drip},{number},ok 2,6,{skipped},{number},sk,nsm")
    replay.tick()

    [first, second] = rows_of()
    assert (first["slot_id"], first["local_day"], first["cause"]) == (drip, day, "schedule")
    assert first["started_at"] == start and first["ended_at"] == start + timedelta(seconds=300)
    assert (first["open_s"], first["pulses"], first["outcome"], first["reason"], first["event_seq"]) == (
        299,
        1,
        "ok",
        None,
        1,
    )
    assert (second["slot_id"], second["line_id"], second["cause"], second["open_s"]) == (skipped, "L07", "schedule", 0)
    assert (second["started_at"], second["outcome"], second["reason"], second["event_seq"]) == (
        None,
        "skipped",
        "nsm",
        2,
    )
    assert rows_of(cause="vpd") == []

    # The same text again inserts nothing, here on a new text with the same two entries as well.
    replay.tick()
    replay.events(f"1,0,{drip},{number},ok 2,6,{skipped},{number},sk,nsm")
    replay.memory.events_text = None
    replay.tick()
    assert len(rows_of()) == 2

    # A run starts, the connection drops for 30 minutes, and the run ends unseen. The reconnect's relay row is not the
    # edge, so the row ends at the token's planned end. The ring fills what happened in the gap.
    start = base + timedelta(hours=1)
    replay.state(start, token("d", 0, "schedule", during, start, start + timedelta(seconds=300)) + " v=hhh")
    edge("drip_wall", start + timedelta(seconds=1), True)
    replay.tick()
    back = start + timedelta(minutes=30)
    replay.reconnect(back)
    edge("drip_wall", back, False)
    replay.state(back, "v=hhh")
    replay.events(
        f"1,0,{drip},{number},ok 2,6,{skipped},{number},sk,nsm 3,0,{during},{number},ok 4,0,{unseen},{number},ok "
        f"5,0,{missed},{number},mi,man"
    )
    replay.tick()
    [closed] = rows_of(slot_id=during)
    assert closed["ended_at"] == start + timedelta(seconds=300) and closed["open_s"] == 299
    assert closed["outcome"] == "ok" and closed["event_seq"] == 3
    [ran_unseen] = rows_of(slot_id=unseen)
    assert (ran_unseen["outcome"], ran_unseen["open_s"], ran_unseen["started_at"]) == ("ok", 0, None)
    [missed_row] = rows_of(slot_id=missed)
    assert (missed_row["outcome"], missed_row["reason"]) == ("missed", "man")
    assert len(rows_of()) == 5

    # An applied, confirmed program and climate rows with the disconnect's gap: the reported miss and the occurrence
    # with no outcome both fall in a window the ingestor did not fully watch, so nothing is raised.
    climate_rows(base - timedelta(hours=1), now(), gaps=[(start, back)])
    system_state("irr_engine", "true", base - timedelta(days=1))
    applied = push_applied(task, confirmed_at=base - timedelta(hours=2))
    system_state("irr_program_id", f"h={applied['sha256'][:16]} src=nvs", base - timedelta(hours=1))
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []


def test_a_replayed_ring_after_a_restart_inserts_nothing(replay):
    base = replay_base(2)
    number = day_number(local_day(base))
    slot = schedule("L01", base)
    replay.events(f"7,0,{slot},{number},sk,dis 8,0,0,{number},sk,flt")
    replay.tick()
    assert len(rows_of()) == 2
    replay.restart()
    replay.events(f"7,0,{slot},{number},sk,dis 8,0,0,{number},sk,flt")
    replay.tick()
    assert len(rows_of()) == 2


def test_the_sequence_wraps_from_65535_to_0(replay):
    base = replay_base(2)
    number = day_number(local_day(base))
    slots = [schedule("L01", base + timedelta(minutes=index)) for index in range(5)]
    replay.events(
        f"65534,0,{slots[0]},{number},sk,dis 65535,0,{slots[1]},{number},sk,dis 0,0,{slots[2]},{number},sk,dis"
    )
    replay.tick()
    assert sorted(row["event_seq"] for row in rows_of()) == [0, 65534, 65535]
    replay.events(
        f"65535,0,{slots[1]},{number},sk,dis 0,0,{slots[2]},{number},sk,dis 1,0,{slots[3]},{number},sk,dis "
        f"2,0,{slots[4]},{number},ok"
    )
    replay.tick()
    assert sorted(row["event_seq"] for row in rows_of()) == [0, 1, 2, 65534, 65535]


def test_a_power_cut_restarts_the_sequence_and_new_outcomes_still_land(replay):
    base = replay_base(2)
    number = day_number(local_day(base))
    before, after = schedule("L01", base), schedule("L01", base + timedelta(minutes=1))
    replay.events(f"5000,0,{before},{number},ok")
    replay.tick()
    replay.reconnect(base)
    replay.events(f"0,0,{after},{number},sk,rst")
    replay.tick()
    assert {row["slot_id"]: row["event_seq"] for row in rows_of()} == {before: 5000, after: 0}


def test_an_aged_out_outcome_stays_null_and_is_never_invented(replay):
    base = replay_base(2)
    number = day_number(local_day(base))
    gone, kept = schedule("L01", base), schedule("L01", base + timedelta(minutes=30))
    replay.state(base, token("d", 0, "schedule", gone, base, base + timedelta(seconds=60)) + " v=hhh")
    replay.state(base + timedelta(seconds=60), "v=hhh")
    start = base + timedelta(minutes=30)
    replay.state(start, token("d", 0, "schedule", kept, start, start + timedelta(seconds=60)) + " v=hhh")
    replay.state(start + timedelta(seconds=60), "v=hhh")
    replay.events(f"9,0,{kept},{number},ok")  # the first run's entry aged out of the ring
    replay.tick()
    outcomes = {row["slot_id"]: row["outcome"] for row in rows_of()}
    assert outcomes == {gone: None, kept: "ok"}


# ── Run-nows ──────────────────────────────────────────────────────────────────


def test_two_same_cause_run_nows_on_one_line_in_a_day_store_two_rows(replay):
    base = replay_base(2)
    number = day_number(local_day(base))
    second = base + timedelta(minutes=10)
    first_command = sent_command("L01", base - timedelta(seconds=1), "jason")
    second_command = sent_command("L01", second - timedelta(seconds=1), "steward")
    replay.state(base, token("d", 0, "run_now", 0, base, base + timedelta(seconds=60)) + " v=hhh")
    replay.state(base + timedelta(seconds=60), "v=hhh")
    replay.state(second, token("d", 0, "run_now", 0, second, second + timedelta(seconds=60)) + " v=hhh")
    replay.state(second + timedelta(seconds=30), "v=hhh")
    replay.events(f"10,0,0,{number},ok 11,0,0,{number},st,cmd")  # both outcomes in one text
    replay.tick()

    [one, two] = rows_of()
    assert one["slot_id"] is None and two["slot_id"] is None
    assert (one["cause"], one["started_at"], one["outcome"], one["event_seq"]) == ("run_now", base, "ok", 10)
    assert (one["command_id"], one["actor"]) == (first_command, "jason")
    assert (two["started_at"], two["outcome"], two["reason"], two["event_seq"]) == (second, "stopped", "cmd", 11)
    assert (two["command_id"], two["actor"]) == (second_command, "steward")


def test_a_refused_run_now_gets_its_own_row_and_leaves_a_running_one_alone(replay):
    base = replay_base(5 / 60)
    number = day_number(local_day(base))
    running = sent_command("L01", base)
    replay.state(base, token("d", 0, "run_now", 0, base, base + timedelta(hours=1)) + " v=hhh")
    replay.tick()
    refused = sent_command("L04", base + timedelta(seconds=30))
    replay.events(f"12,3,0,{number},sk,flt")
    replay.tick()
    [open_row] = rows_of(line_id="L01")
    assert (open_row["outcome"], open_row["command_id"]) == (None, running)
    [row] = rows_of(line_id="L04")
    assert (row["cause"], row["slot_id"], row["open_s"], row["started_at"]) == ("run_now", None, 0, None)
    assert (row["outcome"], row["reason"], row["command_id"]) == ("skipped", "flt", refused)


def test_an_outcome_finds_a_run_now_by_line_but_not_one_still_running(replay):
    base = replay_base(30 / 60)
    number = day_number(local_day(base))
    replay.state(base, token("d", 0, "run_now", 0, base, base + timedelta(seconds=60)) + " v=hhh")
    replay.state(base + timedelta(seconds=60), "v=hhh")
    later = base + timedelta(minutes=5)
    replay.state(later, token("d", 0, "run_now", 0, later, later + timedelta(hours=1)) + " v=hhh")
    replay.events(f"13,0,0,{number},ok")
    replay.tick()
    [done, running] = rows_of()
    assert done["outcome"] == "ok" and running["outcome"] is None


# ── Fertilizer runs ───────────────────────────────────────────────────────────


def test_a_fertilizer_occurrences_stages_are_separate_rows_with_one_outcome(replay):
    base = replay_base(1)
    number = day_number(local_day(base))
    slot = schedule("L02", base)
    feed_at, flush_at = base + timedelta(seconds=60), base + timedelta(seconds=360)
    replay.state(base, token("d", 0, "prewet", slot, base, feed_at) + " v=hhh")
    replay.state(feed_at, token("d", 1, "feed", slot, feed_at, flush_at) + " v=hhh")
    edge("drip_wall", base + timedelta(seconds=1), True)
    edge("drip_wall", feed_at, False)
    edge("drip_wall_fert", feed_at, True)
    edge("drip_wall_fert", flush_at, False)
    replay.state(flush_at, "w=0,man v=hhh")
    replay.events(f"20,1,{slot},{number},ok")  # the outcome goes out when the feed ends, before the flush token
    replay.tick()
    replay.state(
        flush_at + timedelta(seconds=5),
        token("d", 0, "flush", slot, flush_at + timedelta(seconds=5), flush_at + timedelta(seconds=65)) + " v=hhh",
    )
    edge("drip_wall", flush_at + timedelta(seconds=6), True)
    edge("drip_wall", flush_at + timedelta(seconds=65), False)
    replay.state(flush_at + timedelta(seconds=65), "v=hhh")
    replay.tick()

    rows = {row["cause"]: row for row in rows_of(slot_id=slot)}
    assert set(rows) == {"prewet", "feed", "flush"}
    assert (rows["prewet"]["line_id"], rows["feed"]["line_id"], rows["flush"]["line_id"]) == ("L01", "L02", "L01")
    assert {row["local_day"] for row in rows.values()} == {local_day(base)}
    assert {row["outcome"] for row in rows.values()} == {"ok"} and {row["event_seq"] for row in rows.values()} == {20}
    assert rows["feed"]["open_s"] == 300 and rows["prewet"]["open_s"] == 59 and rows["flush"]["open_s"] == 59


def test_a_fertilizer_run_nows_stages_share_its_command_and_its_outcome(replay):
    base = replay_base(1)
    number = day_number(local_day(base))
    command = sent_command("L02", base - timedelta(seconds=1))
    feed_at, flush_at = base + timedelta(seconds=60), base + timedelta(seconds=360)
    replay.state(base, token("d", 0, "prewet", 0, base, feed_at) + " v=hhh")
    replay.state(feed_at, token("d", 1, "feed", 0, feed_at, flush_at) + " v=hhh")
    replay.state(flush_at, "v=hhh")
    replay.events(f"21,1,0,{number},sh,rst")
    replay.tick()
    start = flush_at + timedelta(seconds=5)
    replay.state(start, token("d", 0, "flush", 0, start, start + timedelta(seconds=60)) + " v=hhh")
    replay.state(start + timedelta(seconds=60), "v=hhh")
    replay.tick()

    rows = {row["cause"]: row for row in rows_of(command_id=command)}
    assert set(rows) == {"prewet", "feed", "flush"} and {row["slot_id"] for row in rows.values()} == {None}
    assert {(row["outcome"], row["reason"], row["event_seq"]) for row in rows.values()} == {("short", "rst", 21)}
    assert (rows["prewet"]["line_id"], rows["feed"]["line_id"]) == ("L01", "L02")


def test_an_entry_with_no_row_takes_the_slots_cause(replay):
    base = replay_base(1)
    number = day_number(local_day(base))
    plain = schedule("L01", base)
    fertilizer = schedule("L02", base + timedelta(minutes=1))
    fallback = schedule("L04", base + timedelta(minutes=2), degraded_only=True)
    replay.events(
        f"30,0,{plain},{number},sk,dis 31,1,{fertilizer},{number},mi 32,3,{fallback},{number},sk,vok 33,0,60000,{number},ca,cap"
    )
    replay.tick()
    causes = {row["slot_id"]: (row["cause"], row["open_s"]) for row in rows_of()}
    assert causes == {
        plain: ("schedule", 0),
        fertilizer: ("feed", 0),
        fallback: ("fallback", 0),
        60000: ("schedule", 0),  # its schedule is gone: slot_id has no foreign key, so history outlives it
    }


def test_a_settled_row_keeps_its_first_outcome(replay):
    base = replay_base(1)
    number = day_number(local_day(base))
    slot = schedule("L01", base)
    replay.events(f"40,0,{slot},{number},ok")
    replay.tick()
    replay.events(f"41,0,{slot},{number},sh")
    replay.tick()
    [row] = rows_of(slot_id=slot)
    assert (row["outcome"], row["event_seq"]) == ("ok", 40)


# ── Tokens that come back, restarts, and an occurrence past midnight ─────────


def test_a_window_blocked_past_the_close_delay_reopens_its_row_and_closes_on_all_its_edges(replay):
    base = replay_base(1)
    slot = schedule("L04", base)
    text = token("m", 3, "schedule", slot, base, base + timedelta(seconds=150)) + " v=hhh"
    replay.state(base, text)
    edge("mister_center", base + timedelta(seconds=1), True)
    edge("mister_center", base + timedelta(seconds=31), False)
    replay.state(base + timedelta(seconds=40), "w=3,occ v=hhh")
    replay.tick()
    [row] = rows_of()
    assert row["ended_at"] == base + timedelta(seconds=31) and row["pulses"] == 1

    back = base + timedelta(minutes=20)
    replay.state(back, text)
    replay.tick()
    assert rows_of()[0]["ended_at"] is None and len(rows_of()) == 1

    edge("mister_center", back + timedelta(seconds=1), True)
    edge("mister_center", back + timedelta(seconds=31), False)
    replay.state(back + timedelta(seconds=32), "v=hhh")
    replay.tick()
    [row] = rows_of()
    assert row["ended_at"] == back + timedelta(seconds=31) and (row["open_s"], row["pulses"]) == (60, 2)


def test_a_restart_keeps_a_running_rows_token_and_closes_a_left_one(replay):
    base = replay_base(30 / 60)
    number = day_number(local_day(base))
    left, running = schedule("L01", base), schedule("L04", base)
    replay.state(
        base,
        token("d", 0, "schedule", left, base, base + timedelta(minutes=5))
        + " "
        + token("m", 3, "schedule", running, base, base + timedelta(hours=1))
        + " v=hhh",
    )
    edge("drip_wall", base + timedelta(seconds=1), True)
    replay.tick()
    assert len(rows_of()) == 2

    replay.restart()
    edge("drip_wall", base + timedelta(minutes=5), False)
    replay.state(
        now() - timedelta(minutes=1), token("m", 3, "schedule", running, base, base + timedelta(hours=1)) + " v=hhh"
    )
    replay.tick()
    assert len(rows_of()) == 2
    assert rows_of(slot_id=running)[0]["ended_at"] is None
    assert rows_of(slot_id=left)[0]["ended_at"] == base + timedelta(minutes=5)
    assert rows_of(slot_id=left)[0]["local_day"] == dt.date.fromordinal(EPOCH_DAY.toordinal() + number)


def test_a_run_past_midnight_lands_on_its_occurrences_day(replay, task):
    # A late start: due 23:50 local on one day, opened 00:05 the next.
    tz = irr.local_tz()
    today = local_day(now())
    opened = datetime(today.year, today.month, today.day, 0, 5, tzinfo=tz).astimezone(UTC)
    due = opened - timedelta(minutes=15)
    slot = schedule("L01", due)
    replay.state(opened, token("d", 0, "schedule", slot, opened, opened + timedelta(seconds=60)) + " v=hhh")
    replay.events(f"50,0,{slot},{day_number(today - timedelta(days=1))},ok")
    replay.tick()
    [row] = rows_of(slot_id=slot)
    assert row["local_day"] == today - timedelta(days=1) and row["outcome"] == "ok"


# ── The compile's read and irrigation_push ────────────────────────────────────


def read(task):
    async def body():
        conn = await connect(INGESTOR)
        try:
            return await task["read_program"](conn)
        finally:
            await conn.close()

    return run(body())


def test_the_compile_reads_the_seed_program_and_its_hash_is_the_golden_vectors(task):
    import json

    snapshot = read(task)
    seed = next(v for v in json.loads(VECTORS.read_text())["vectors"] if v["name"] == "seed")
    assert snapshot.compiled.sha256 == seed["sha256"]
    assert snapshot.compiled.record.hex() == seed["hex"]
    assert snapshot.site_desired_at is None and snapshot.zones["L01"] == "wall" and len(snapshot.lines) == 11


def test_the_compile_takes_site_values_and_the_newest_of_their_times(task):
    older = now() - timedelta(hours=2)
    newer = now() - timedelta(hours=1)
    for param, value, created in (
        ("mister_daily_volume_max_gal", 400, older),
        ("sched_mist_rh_max_pct", 80, newer),
        ("climate_yield_min", None, newer - timedelta(minutes=5)),  # released: the registry default
        ("temp_low", 50, now()),  # not a site value
    ):
        execute(
            "INSERT INTO public.desired_state (param, value, source, actor, created_at) VALUES ($1, $2, 'test', 'test', $3)",
            param,
            value,
            created,
        )
    snapshot = read(task)
    record = irr.unpack_program(snapshot.compiled.record)
    assert (record.saf4_gal, record.rh_ceiling_pct) == (400, 80)
    assert record.climate_yield_min == irr.site_values().climate_yield_min
    assert snapshot.site_desired_at == newer


def test_a_disabled_schedule_is_left_off_and_an_invalid_one_never_blocks_the_program(task):
    schedule("L01", now(), enabled=False)
    baseline = read(task).compiled.sha256
    assert len(read(task).slots) == 3
    assert (
        task["_schedule_specs"](
            [
                {
                    "slot_id": 99,
                    "line_id": "L01",
                    "enabled": True,
                    "days": 0,
                    "start_kind": "clock",
                    "start_min": 1,
                    "on_s": 60,
                    "count": 1,
                    "gap_s": 0,
                    "prewet_s": 0,
                    "flush_s": 0,
                    "degraded_only": False,
                }
            ]
        )
        == ()
    )
    assert baseline == read(task).compiled.sha256


def test_a_new_hash_adds_a_revision_and_a_repush_updates_that_row(task):
    column = "SELECT {} FROM public.irrigation_push ORDER BY revision DESC LIMIT 1"

    async def first_pushes():
        conn = await connect(INGESTOR)
        store = task["ProgramStore"](OnePool(conn))
        try:
            snapshot = await store.snapshot()
            pushed = now() - timedelta(minutes=5)
            first = await store.record_push(snapshot, pushed)
            newest = await store.newest_push()
            assert (newest["revision"], newest["sha256"]) == (first, snapshot.compiled.sha256)

            # Rejected, then pushed again: the same row, with a later pushed_at and no reject_code.
            await store.record_reject(first, "bad_slot")
            assert await conn.fetchval(column.format("reject_code")) == "bad_slot"
            assert await store.record_push(snapshot, now()) == first
            assert await conn.fetchval(column.format("reject_code")) is None
            assert await conn.fetchval(column.format("pushed_at")) > pushed

            # Only the first report sets confirmed_at.
            await store.confirm(first, now())
            confirmed = await conn.fetchval(column.format("confirmed_at"))
            await store.confirm(first, now() + timedelta(hours=1))
            assert await conn.fetchval(column.format("confirmed_at")) == confirmed

            # The controller lost it: pushed again, unconfirmed until it reports the hash.
            assert await store.record_push(snapshot, now()) == first
            assert await conn.fetchval(column.format("confirmed_at")) is None
            return first, snapshot
        finally:
            await conn.close()

    async def edited_push():
        conn = await connect(INGESTOR)
        store = task["ProgramStore"](OnePool(conn))
        try:
            edited = await store.snapshot()
            return await store.record_push(edited, now()), edited
        finally:
            await conn.close()

    first, snapshot = run(first_pushes())
    schedule("L01", now())
    second, edited = run(edited_push())
    assert second > first and edited.compiled.sha256 != snapshot.compiled.sha256
    rows = fetch("SELECT revision, sha256, bytes, site_desired_at FROM public.irrigation_push ORDER BY revision")
    assert [tuple(row) for row in rows] == [
        (first, snapshot.compiled.sha256, snapshot.compiled.record, None),
        (second, edited.compiled.sha256, edited.compiled.record, None),
    ]


def test_a_stop_on_the_engine_path_fails_only_the_runs_requested_before_it(task):
    created = now() - timedelta(seconds=30)

    def command(kind: str, at: datetime) -> uuid.UUID:
        [row] = fetch(
            "INSERT INTO public.irrigation_command (kind, line_id, source, actor, created_at) "
            "VALUES ($1, 'L01', 'agent:steward', 'jason', $2) RETURNING command_id",
            kind,
            at,
        )
        return row["command_id"]

    before = command("run", created - timedelta(seconds=1))
    stop = command("stop", created)
    after = command("run", created + timedelta(seconds=1))

    async def body():
        conn = await connect(INGESTOR)
        store = task["CommandStore"](OnePool(conn))
        try:
            [pending] = await store.pending("stop")
            assert await store.mark_stop_sent(pending, now(), ["drip_wall"], earlier_runs_only=True)
        finally:
            await conn.close()

    run(body())
    rows = {row["command_id"]: row for row in fetch("SELECT * FROM public.irrigation_command")}
    assert (rows[before]["status"], rows[before]["fail_reason"]) == ("failed", "stopped")
    assert rows[stop]["status"] == "sent" and rows[after]["status"] == "pending"


# ── Alerts ────────────────────────────────────────────────────────────────────


def push_applied(task, *, confirmed_at: datetime | None) -> Mapping:
    """The compiled program as the applied push: the newest irrigation_push row, confirmed at ``confirmed_at``."""
    snapshot = read(task)
    [row] = fetch(
        "INSERT INTO public.irrigation_push (sha256, bytes, pushed_at, confirmed_at) VALUES ($1, $2, $3, $4) RETURNING *",
        snapshot.compiled.sha256,
        snapshot.compiled.record,
        now() - timedelta(days=2),
        confirmed_at,
    )
    return row


def engine_on(applied: Mapping | None = None, *, at: datetime | None = None) -> None:
    system_state("irr_engine", "true", at or now() - timedelta(days=1))
    if applied is not None:
        system_state("irr_program_id", f"h={applied['sha256'][:16]} src=nvs", at or now() - timedelta(days=1))


def engine_alerts(alerts, moment: datetime | None = None) -> list[dict]:
    async def body():
        conn = await connect(INGESTOR)
        try:
            view = await alerts["_irrigation_view"](conn)
            return await alerts["_irrigation_engine_alerts"](conn, view, moment or now())
        finally:
            await conn.close()

    produced = run(body())
    for alert in produced:
        AlertEnvelope.model_validate(alert)
    return produced


def of_type(produced: list[dict], alert_type: str) -> list[dict]:
    return [alert for alert in produced if alert["alert_type"] == alert_type]


def test_an_occurrence_the_controller_reports_missed_raises_with_the_window_watched(replay, task, alerts):
    planned = whole(now() - timedelta(hours=3))
    day = local_day(planned)
    slot = schedule("L01", planned)
    applied = push_applied(task, confirmed_at=planned - timedelta(hours=2))
    engine_on(applied, at=planned - timedelta(hours=1))
    climate_rows(planned - timedelta(minutes=10), now())
    replay.events(f"60,0,{slot},{day_number(day)},mi,man")
    replay.tick()
    # recorded_at is when the outcome arrived, inside the watched window.
    [alert] = of_type(engine_alerts(alerts), "irrigation_run_missed")
    assert alert["severity"] == "warning" and alert["category"] == "water"
    assert alert["sensor_id"] == "irrigation.L01" and alert["zone"] == "wall"
    assert alert["details"]["rule"] == "reported_missed" and alert["details"]["reason"] == "man"
    assert alert["details"]["slot_id"] == slot and alert["details"]["local_day"] == day.isoformat()
    assert "waiting for the manifold" in alert["message"]


def test_an_occurrence_with_no_outcome_2_h_after_its_planned_start_raises(task, alerts):
    planned = whole(now() - timedelta(hours=3))
    slot = schedule("L01", planned)
    applied = push_applied(task, confirmed_at=planned - timedelta(hours=2))
    engine_on(applied, at=planned - timedelta(hours=1))
    climate_rows(planned - timedelta(minutes=10), now())
    [alert] = of_type(engine_alerts(alerts), "irrigation_run_missed")
    assert alert["details"]["rule"] == "no_outcome" and alert["details"]["slot_id"] == slot
    assert alert["sensor_id"] == "irrigation.L01"
    assert datetime.fromisoformat(alert["details"]["planned_start"]) == planned.replace(second=0)


def test_nothing_raises_before_the_2_h_mark_or_once_a_row_exists(replay, task, alerts):
    planned = whole(now() - timedelta(hours=1))
    schedule("L01", planned)
    applied = push_applied(task, confirmed_at=planned - timedelta(hours=2))
    engine_on(applied, at=planned - timedelta(hours=1))
    climate_rows(planned - timedelta(minutes=10), now())
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []

    # A long mist window that opened 3 h ago still has no outcome: it is running, not missed.
    execute("DELETE FROM public.irrigation_push")
    execute("DELETE FROM public.system_state")
    long_ago = whole(now() - timedelta(hours=3))
    slot = schedule("L04", long_ago)
    execute("UPDATE public.irrigation_line SET schedule_member = true WHERE line_id = 'L04'")
    applied = push_applied(task, confirmed_at=long_ago - timedelta(hours=2))
    engine_on(applied, at=long_ago - timedelta(hours=1))
    climate_rows(long_ago - timedelta(minutes=10), planned)
    replay.state(long_ago, token("m", 3, "schedule", slot, long_ago, long_ago + timedelta(hours=8)) + " v=hhh")
    replay.tick()
    assert [
        alert
        for alert in of_type(engine_alerts(alerts), "irrigation_run_missed")
        if alert["details"]["slot_id"] == slot
    ] == []


def test_a_degraded_only_slot_that_does_not_fire_raises_nothing(task, alerts):
    # The seed's three windows on L04 are degraded_only and fire on no day here.
    planned = whole(now() - timedelta(hours=3))
    schedule("L04", planned, degraded_only=True)
    applied = push_applied(task, confirmed_at=planned - timedelta(days=2))
    engine_on(applied, at=now() - timedelta(days=2))
    climate_rows(now() - timedelta(hours=25), now())
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []


def test_no_outcome_counts_only_under_the_confirmed_applied_program(task, alerts):
    planned = whole(now() - timedelta(hours=3))
    schedule("L01", planned)
    climate_rows(planned - timedelta(minutes=10), now())

    # Confirmed after the planned start: the controller did not have it yet.
    applied = push_applied(task, confirmed_at=planned + timedelta(minutes=30))
    engine_on(applied, at=planned - timedelta(hours=1))
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []

    # An edit since: the applied hash no longer equals the compiled one.
    execute("UPDATE public.irrigation_push SET confirmed_at = $1", planned - timedelta(hours=2))
    assert of_type(engine_alerts(alerts), "irrigation_run_missed")
    schedule("L01", planned + timedelta(minutes=30))
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []


def test_a_reconnect_or_a_climate_gap_in_the_window_raises_nothing(task, alerts):
    planned = whole(now() - timedelta(hours=3))
    schedule("L01", planned)
    applied = push_applied(task, confirmed_at=planned - timedelta(hours=2))
    engine_on(applied, at=planned - timedelta(hours=1))
    climate_rows(
        planned - timedelta(minutes=10),
        now(),
        gaps=[(planned + timedelta(minutes=30), planned + timedelta(minutes=36))],
    )
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []

    execute("DELETE FROM public.climate")
    climate_rows(planned - timedelta(minutes=10), now())
    assert of_type(engine_alerts(alerts), "irrigation_run_missed")
    system_state("irr_engine", "true", planned + timedelta(minutes=30))
    assert of_type(engine_alerts(alerts), "irrigation_run_missed") == []


def test_before_the_engine_no_new_alert_appears(replay, task, alerts):
    planned = whole(now() - timedelta(hours=3))
    day = local_day(planned)
    slot = schedule("L01", planned)
    applied = push_applied(task, confirmed_at=planned - timedelta(hours=2))
    climate_rows(planned - timedelta(minutes=10), now())
    replay.events(f"61,0,{slot},{day_number(day)},mi")
    replay.tick()
    system_state("irr_state", "f=3,l,1790000000 v=hhh", now() - timedelta(minutes=1))
    system_state("irr_program_id", f"h={applied['sha256'][:16]} src=nvs", planned - timedelta(hours=1))
    system_state("irr_engine", "false", planned - timedelta(hours=1))
    assert engine_alerts(alerts) == []
    # A rollback's false row is the newest: an old true row no longer counts.
    system_state("irr_engine", "true", planned - timedelta(hours=2))
    assert engine_alerts(alerts) == []
    # No irr_program_id yet: inert too.
    execute("DELETE FROM public.system_state WHERE entity = 'irr_program_id'")
    system_state("irr_engine", "true", now())
    assert engine_alerts(alerts) == []


def fence_alerts(alerts) -> list[dict]:
    return of_type(engine_alerts(alerts), "irrigation_fence_breach")


def test_enabling_l06_raises_no_breach_when_its_relay_turns_on(task, alerts):
    execute("UPDATE public.irrigation_line SET enabled = true WHERE line_id = 'L06'")
    applied = push_applied(task, confirmed_at=now() - timedelta(hours=1))
    engine_on(applied)
    edge("drip_center_fert", now() - timedelta(minutes=5), True)
    edge("fert_master_valve", now() - timedelta(minutes=5), True)
    assert fence_alerts(alerts) == []


def test_a_relay_on_while_the_program_disables_its_line_breaches(task, alerts):
    applied = push_applied(task, confirmed_at=now() - timedelta(hours=1))
    engine_on(applied)
    on_at = now() - timedelta(minutes=5)
    edge("drip_center_fert", on_at, True)
    [alert] = fence_alerts(alerts)
    assert alert["severity"] == "critical" and alert["sensor_id"] == "equipment.drip_center_fert"
    assert alert["details"]["rule"] == "relay_on_line_disabled" and alert["details"]["equipment"] == "drip_center_fert"
    assert alert["details"]["context"]["line_id"] == "L06"


def test_disabling_l01_while_it_runs_raises_no_breach(task, alerts):
    # L01 turned on before the push that disables it was confirmed, and stays on for a moment.
    edge("drip_wall", now() - timedelta(minutes=10), True)
    execute("UPDATE public.irrigation_line SET enabled = false WHERE line_id = 'L01'")
    applied = push_applied(task, confirmed_at=now() - timedelta(minutes=9))
    engine_on(applied)
    edge("drip_wall", now() - timedelta(minutes=8), False)
    assert fence_alerts(alerts) == []
    # On again after the confirmed push: that is a breach.
    edge("drip_wall", now() - timedelta(minutes=2), True)
    [alert] = fence_alerts(alerts)
    assert alert["details"]["equipment"] == "drip_wall"


def test_l03_open_with_no_fertilizer_run_breaches_and_a_feed_covers_it(task, alerts):
    applied = push_applied(task, confirmed_at=now() - timedelta(hours=1))
    engine_on(applied)
    edge("fert_master_valve", now() - timedelta(minutes=5), True)
    [alert] = fence_alerts(alerts)
    assert alert["details"]["rule"] == "fert_master_without_fert_run"
    assert alert["sensor_id"] == "equipment.fert_master_valve"

    # A feed on an enabled line, whichever edge landed first, and one already on when the window opened.
    execute("UPDATE public.irrigation_line SET enabled = true WHERE line_id = 'L02'")
    execute("DELETE FROM public.irrigation_push")
    applied = push_applied(task, confirmed_at=now() - timedelta(hours=1))
    execute("DELETE FROM public.system_state")
    engine_on(applied)
    edge("drip_wall_fert", now() - timedelta(minutes=16), True)
    assert fence_alerts(alerts) == []


def test_an_unconfirmed_push_checks_nothing_and_with_no_applied_program_the_new_rules_do_not_run(task, alerts):
    applied = push_applied(task, confirmed_at=None)
    engine_on(applied)
    edge("drip_center_fert", now() - timedelta(minutes=5), True)
    assert fence_alerts(alerts) == []

    # src=rom: the view has no applied program, so alert_monitor keeps today's three rules.
    execute("DELETE FROM public.system_state")
    system_state("irr_engine", "true", now() - timedelta(days=1))
    system_state("irr_program_id", "h=0000000000000000 src=rom", now() - timedelta(days=1))

    async def view():
        conn = await connect(INGESTOR)
        try:
            return await alerts["_irrigation_view"](conn)
        finally:
            await conn.close()

    seen = run(view())
    assert seen.engine and seen.applied is None and seen.program_id.src == "rom"
    assert fence_alerts(alerts) == []


def test_a_latch_raises_the_fault_until_it_clears_and_a_back_off_raises_nothing(task, alerts):
    applied = push_applied(task, confirmed_at=now() - timedelta(hours=1))
    engine_on(applied)
    system_state("irr_state", "f=6,l,1790000000 v=hhh", now() - timedelta(minutes=5))
    [alert] = of_type(engine_alerts(alerts), "irrigation_line_faulted")
    assert alert["severity"] == "warning" and alert["sensor_id"] == "irrigation.L07" and alert["zone"] == "south"
    assert alert["details"]["line_id"] == "L07"

    system_state("irr_state", "f=6,b,1790000000 v=hhh", now() - timedelta(minutes=4))
    assert of_type(engine_alerts(alerts), "irrigation_line_faulted") == []

    system_state("irr_state", "f=6,l,1790000000 v=hhh", now() - timedelta(minutes=3))
    assert len(of_type(engine_alerts(alerts), "irrigation_line_faulted")) == 1
    system_state("irr_state", "v=hhh", now() - timedelta(minutes=2))  # midnight, or clear_fault
    assert of_type(engine_alerts(alerts), "irrigation_line_faulted") == []


def test_the_row_models_name_only_m3s_columns(task):
    for model, table in ((irr.IrrigationPushRow, "irrigation_push"), (irr.IrrigationLedgerRow, "irrigation_ledger")):
        rows = fetch("SELECT column_name FROM information_schema.columns WHERE table_name = $1", table)
        assert set(model.model_fields) == {row["column_name"] for row in rows}
