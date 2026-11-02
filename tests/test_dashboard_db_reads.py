"""VD-54 on a private PostgreSQL: the Now, plan and equipment reads' acceptance fixtures.

jvallery/agents#5994's campaign step starts a fresh server for each
tests/test_dashboard_db_*.py file and sets VERDIFY_ISOLATED_TEST_DSN; without
it this file skips. It builds a private schema from db/schema.sql's own DDL
for every table and view the reads touch, migration 280's desired_state and
v_desired_state_current, and migration 259's fn_mcp_armed_assignment_context()
over stand-ins for the two experiment tables db/schema.sql predates. The reads
run as a role holding only SELECT on those relations and EXECUTE on that
function (a subset of what M2, #1179, grants the dashboard login), so a read of
anything else fails here. The reads measure every age from the server's now(),
so each fixture is placed relative to it.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from verdify_schemas.tunable_registry import BAND_OWNED_REG, REGISTRY
from verdify_services import reads

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "vd54_reads"
ROLE = "vd54_dashboard_reads"
SCHEMA_DDL = (
    ("CREATE TABLE public.climate (", ");\n"),
    ("CREATE TABLE public.diagnostics (", ");\n"),
    ("CREATE TABLE public.system_state (", ");\n"),
    ("CREATE TABLE public.equipment_state (", ");\n"),
    ("CREATE TABLE public.setpoint_snapshot (", ");\n"),
    ("CREATE TABLE public.plan_journal (", ");\n"),
    ("CREATE TABLE public.setpoint_plan (", ");\n"),
    ("CREATE TABLE public.planner_trigger_ledger (", ");\n"),
    ("CREATE TABLE public.setpoint_changes (", ");\n"),
    ("CREATE TABLE public.setpoint_clamps (", ");\n"),
    ("CREATE TABLE public.water_meter_events (", ");\n"),
    ("CREATE TABLE public.alert_log (", ");\n"),
    ("CREATE SEQUENCE public.alert_log_id_seq", ";\n"),
    ("ALTER SEQUENCE public.alert_log_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.alert_log ALTER COLUMN id SET DEFAULT", ";\n"),
    ("CREATE VIEW public.v_open_alerts AS", ";\n"),
    ("CREATE VIEW public.v_active_plan AS", ";\n"),
)
MIGRATION_DDL = (
    ("db/migrations/280-desired-state-activity-log.sql", "CREATE TABLE public.desired_state (", ");\n"),
    ("db/migrations/280-desired-state-activity-log.sql", "CREATE VIEW public.v_desired_state_current AS", ";\n"),
    (
        "db/migrations/259-mcp-ordinary-runtime-boundary.sql",
        "CREATE FUNCTION public.fn_mcp_armed_assignment_context()",
        "$body$;\n",
    ),
)
# Only the columns fn_mcp_armed_assignment_context() reads (migration 207 made the real ones).
EXPERIMENT_STAND_INS = f"""
CREATE TABLE {SCHEMA}.control_experiments (
    experiment_id uuid PRIMARY KEY, greenhouse_id text NOT NULL DEFAULT 'vallery',
    status text NOT NULL, armed_at timestamptz);
CREATE TABLE {SCHEMA}.control_assignments (
    assignment_id uuid PRIMARY KEY, experiment_id uuid NOT NULL, status text NOT NULL, valid_range tstzrange NOT NULL);
"""
READ_TABLES = (
    "climate",
    "diagnostics",
    "system_state",
    "equipment_state",
    "setpoint_snapshot",
    "plan_journal",
    "setpoint_plan",
    "planner_trigger_ledger",
    "setpoint_changes",
    "setpoint_clamps",
    "water_meter_events",
)
READ_VIEWS = ("v_open_alerts", "v_active_plan", "v_desired_state_current")
ALL_TABLES = (*READ_TABLES, "alert_log", "desired_state", "control_experiments", "control_assignments")


def extract(text: str, start: str, end: str) -> str:
    assert text.count(start) == 1, start
    begin = text.index(start)
    return text[begin : text.index(end, begin) + len(end)].replace("public.", f"{SCHEMA}.")


def ddl() -> list[str]:
    schema = (ROOT / "db/schema.sql").read_text()
    statements = [extract(schema, start, end) for start, end in SCHEMA_DDL]
    statements.append(EXPERIMENT_STAND_INS)
    statements.extend(extract((ROOT / path).read_text(), start, end) for path, start, end in MIGRATION_DDL)
    return statements


async def install(conn) -> None:
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
    for statement in ddl():
        await conn.execute(statement)
    await conn.execute(
        f"""
        DO $$ BEGIN CREATE ROLE {ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        REVOKE ALL ON FUNCTION {SCHEMA}.fn_mcp_armed_assignment_context() FROM PUBLIC;
        GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE};
        GRANT SELECT ON {", ".join(f"{SCHEMA}.{name}" for name in (*READ_TABLES, *READ_VIEWS))} TO {ROLE};
        GRANT EXECUTE ON FUNCTION {SCHEMA}.fn_mcp_armed_assignment_context() TO {ROLE};
        """
    )


async def remove(conn) -> None:
    await conn.execute(f"RESET ROLE; DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.execute(
        f"DO $$ BEGIN DROP OWNED BY {ROLE}; DROP ROLE {ROLE}; EXCEPTION WHEN undefined_object THEN NULL; END $$"
    )


@pytest.fixture(scope="module")
def dsn():
    from conftest import isolated_mutation_test_dsn

    value = isolated_mutation_test_dsn()

    async def setup():
        conn = await asyncpg.connect(value)
        try:
            await install(conn)
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(value)
        try:
            await remove(conn)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield value
    asyncio.run(teardown())


def scenario(dsn, body):
    """Run ``body(conn, t0)`` on emptied tables; ``t0`` is the server's now() before the fixture."""

    async def run():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f"SET search_path = {SCHEMA}")
            await conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY")
            await body(conn, await conn.fetchval("SELECT now()"))
        finally:
            await conn.close()

    asyncio.run(run())


async def as_reader(conn, read, *args, **kwargs):
    """A read as the role with only the read grants."""
    await conn.execute(f"SET ROLE {ROLE}")
    try:
        return await read(conn, *args, **kwargs)
    finally:
        await conn.execute("RESET ROLE")


# ── fixture rows ────────────────────────────────────────────────────────────

S = timedelta(seconds=1)
MIN = timedelta(minutes=1)
H = timedelta(hours=1)
D = timedelta(days=1)


async def heartbeat(conn, ts):
    await conn.execute("INSERT INTO system_state (ts, entity, value) VALUES ($1, 'ingestor_heartbeat', 'alive')", ts)


async def climate(conn, ts, **columns):
    names = ["ts", *columns]
    marks = ", ".join(f"${index}" for index in range(1, len(names) + 1))
    await conn.execute(f"INSERT INTO climate ({', '.join(names)}) VALUES ({marks})", ts, *columns.values())


async def states(conn, equipment, rows):
    await conn.executemany(
        "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)",
        [(ts, equipment, state) for ts, state in rows],
    )


async def system(conn, entity, rows):
    await conn.executemany(
        "INSERT INTO system_state (ts, entity, value) VALUES ($1, $2, $3)", [(ts, entity, value) for ts, value in rows]
    )


async def snapshot(conn, ts, parameter, value, zone=None, band_role=None):
    await conn.execute(
        "INSERT INTO setpoint_snapshot (ts, parameter, value, zone, band_role, target_value) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        ts,
        parameter,
        value,
        zone,
        band_role,
        value if band_role else None,
    )


async def alert(conn, ts, alert_type, severity, *, zone=None, disposition="open", resolved=False, category="system"):
    return await conn.fetchval(
        "INSERT INTO alert_log (ts, alert_type, severity, category, sensor_id, zone, message, source, disposition, "
        "acknowledged_at, resolved_at) VALUES ($1, $2, $3, $4, $5, $6, $7, 'system', $8, $9, $10) RETURNING id",
        ts,
        alert_type,
        severity,
        category,
        f"fixture.{alert_type}",
        zone,
        f"fixture {alert_type}",
        disposition,
        ts if disposition == "acknowledged" else None,
        ts if resolved else None,
    )


async def plan_in_force(conn, plan_id, created, expires, *, status="effective", trigger_id=None, **extra):
    await conn.execute(
        "INSERT INTO plan_journal (plan_id, created_at, valid_from, expires_at, lifecycle_status, trigger_id, "
        "hypothesis, hypothesis_structured, climate_intents) VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8)",
        plan_id,
        created,
        expires,
        status,
        trigger_id,
        extra.get("hypothesis"),
        json.dumps(extra["structured"]) if "structured" in extra else None,
        json.dumps(extra["intents"]) if "intents" in extra else None,
    )


async def waypoints(conn, plan_id, created, expires, rows, *, source="iris", active=True):
    await conn.executemany(
        "INSERT INTO setpoint_plan (ts, parameter, value, plan_id, source, created_at, is_active, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        [(ts, parameter, value, plan_id, source, created, active, expires) for ts, parameter, value in rows],
    )


async def reporting_now(conn, t0):
    await heartbeat(conn, t0 - 20 * S)
    await climate(conn, t0 - 15 * S, temp_avg=79.4, rh_avg=66.0, vpd_avg=1.05, flow_gpm=0.0)


def candidate_params(count):
    names = [
        name
        for name, spec in sorted(REGISTRY.items())
        if spec.control_class == "planner_policy"
        and spec.esp_object_id
        and spec.push_owner != "dispatcher_default"
        and name not in BAND_OWNED_REG
        and name not in reads._LIGHTING_LUX_PARAMS
    ]
    return names[:count]


def mismatch(param, value):
    return value + 10 * max(REGISTRY[param].step or 1.0, 1.0)


# ── API-02: the band ────────────────────────────────────────────────────────


def test_now_reports_the_house_band_not_the_legacy_limits(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        await climate(conn, t0 - 50 * S, outdoor_temp_f=53.7, outdoor_rh_pct=51.5)
        band_at = t0 - 30 * S
        house = {"temp_low": 74.92, "temp_target": 79.92, "temp_high": 84.92, "vpd_low": 0.873, "vpd_high": 1.303}
        for role, value in house.items():
            await snapshot(conn, band_at, f"band_house_{role}", value, "house", role)
        for role, value in {"temp_low": 70.0, "temp_high": 80.0, "vpd_low": 0.8, "vpd_high": 1.2}.items():
            await snapshot(conn, t0 - 330 * S, f"band_house_{role}", value, "house", role)  # an older pass
            await snapshot(conn, band_at, f"band_center_{role}", value + 0.5, "center", role)
        for parameter, value in {"temp_low": 40.0, "temp_high": 95.0, "vpd_low": 0.35, "vpd_high": 2.8}.items():
            await snapshot(conn, t0 - 10 * S, parameter, value)  # the legacy wide limits, newer than the band

        now = await as_reader(conn, reads.now)
        assert now.band.temp_f == reads.BandPosition(low=74.92, high=84.92, verdict="in")
        assert now.band.vpd_kpa == reads.BandPosition(low=0.873, high=1.303, verdict="in")
        assert (now.band.rh_pct, now.band.observed_at) == (None, band_at)
        assert now.indoor.temp_f == reads.Reading(value=79.4, observed_at=t0 - 15 * S)
        assert now.indoor.vpd_kpa.value == 1.05
        assert now.outdoor.temp_f == reads.Reading(value=53.7, observed_at=t0 - 50 * S)
        assert now.outdoor.rh_pct.value == 51.5
        assert not now.freshness.climate.stale and not now.freshness.ingestor.stale
        assert not now.freshness.readback.stale and now.freshness.diagnostics.stale  # never seen

    scenario(dsn, body)


# ── API-13 and API-02: equipment, running, bursts, mode ─────────────────────


def test_equipment_reads_the_newest_state_and_collapses_repeats(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        day = t0 - reads.local_midnight(t0)
        step = min(60 * S, day / 10)  # every main-light edge falls after America/Denver midnight
        await states(conn, "fan1", [(t0 - 7 * D, True), (t0 - 6 * D, False), (t0 - 5 * D, False), (t0 - 1 * D, False)])
        await states(conn, "heat1", [(t0 - 2 * H, False), (t0 - 10 * MIN, True)])
        await states(conn, "mister_center", [(t0 - 3 * H, False), (t0 - 7 * MIN, True)])
        await states(conn, "fog", [(t0 - 3 * H, False)])
        await states(conn, "drip_wall", [(t0 - 40 * D, False)])  # no row in the 31-day window
        main = [(t0 - 2 * D, False), (t0 - 1 * D, False), (t0 - 6 * step, True), (t0 - 5 * step, True)]
        main += [(t0 - 4 * step, True), (t0 - 3 * step, False), (t0 - 2 * step, True), (t0 - step, True)]
        await states(conn, "grow_light_main", main)
        await states(conn, "grow_light_grow", [(t0 - 30 * MIN, True), (t0 - 20 * MIN, True), (t0 - 11 * MIN, True)])
        await states(conn, "grow_light_main_intent", [(t0 - 1 * H, True)])
        await states(conn, "fan_burst_active", [(t0 - 1 * H, False), (t0 - 3 * MIN, True)])
        await states(conn, "fog_burst_active", [(t0 - 1 * H, False), (t0 - 2 * MIN, True)])
        await system(conn, "fog_block_reason", [(t0 - 2 * MIN, "occupancy")])
        # The firmware's label carries the stage; mister_state is the pulse machine, idle while fog runs.
        await system(conn, "greenhouse_state", [(t0 - 3 * H, "IDLE"), (t0 - 40 * MIN, "SEALED_MIST_FOG")])
        await system(conn, "greenhouse_state", [(t0 - 20 * MIN, "SEALED_MIST_FOG")])  # a reconnect repeat
        await system(conn, "mister_state", [(t0 - 10 * MIN, "WATCH")])
        await system(conn, "mode_reason", [(t0 - 40 * MIN, "seal_enter")])

        items = {item.equipment: item for item in (await as_reader(conn, reads.equipment)).items}
        fan1 = items["fan1"]
        assert (fan1.state, fan1.since, fan1.observed_at) == ("off", t0 - 6 * D, t0 - 1 * D)
        assert (fan1.runtime_today_s, fan1.cycles_today) == (0, 0)
        light = items["grow_light_main"]
        assert (light.state, light.since, light.cycles_today, light.intent) == ("on", t0 - 2 * step, 2, "on")
        assert light.observed_at == t0 - step
        assert int(5 * step.total_seconds()) <= light.runtime_today_s <= int(5 * step.total_seconds()) + 30
        grow = items["grow_light_grow"]  # Home Assistant down: the Lutron row is 11 min old
        assert (grow.state, grow.since, grow.observed_at, grow.intent) == ("unknown", None, t0 - 11 * MIN, None)
        assert (items["heat2"].state, items["heat2"].observed_at) == ("unknown", None)
        drip = items["drip_wall"]  # known from its newest older row, with no since
        assert (drip.state, drip.since, drip.observed_at, drip.cycles_today) == ("off", None, t0 - 40 * D, 0)

        now = await as_reader(conn, reads.now)
        running = {item.equipment: item for item in now.running}
        assert set(running) == {"heat1", "mister_center", "grow_light_main"}
        assert (running["heat1"].since, running["heat1"].line_id, running["heat1"].cause) == (t0 - 10 * MIN, None, None)
        assert (running["mister_center"].since, running["mister_center"].line_id) == (t0 - 7 * MIN, "L04")
        assert now.overrides == [
            reads.Override(kind="fans", since=t0 - 3 * MIN, ends_at=None, held_reason=None),
            reads.Override(kind="humidify", since=t0 - 2 * MIN, ends_at=None, held_reason="occupancy"),
        ]
        assert now.mode == reads.Mode(code="SEALED_MIST", stage="FOG", reason="seal_enter", since=t0 - 40 * MIN)

    scenario(dsn, body)


def test_a_burst_ends_at_its_sent_commands_minutes(dsn):
    """ends_at reads the override command sent within 2 min before the edge, once irrigation_command exists (M3)."""

    async def body(conn, t0):
        await reporting_now(conn, t0)
        await states(conn, "fan_burst_active", [(t0 - 1 * H, False), (t0 - 3 * MIN, True)])
        await states(conn, "vent_bypass_active", [(t0 - 1 * H, False), (t0 - 5 * MIN, True)])
        await conn.execute(
            f"CREATE TABLE {SCHEMA}.irrigation_command (kind text, args jsonb, status text, sent_at timestamptz)"
        )
        try:
            commands = [
                ({"kind": "fans", "minutes": 15}, "sent", t0 - 3 * MIN - 30 * S),
                ({"kind": "fans", "minutes": 99}, "sent", t0 - 10 * MIN),  # more than 2 min before the edge
                ({"kind": "vent_bypass", "minutes": 20}, "failed", t0 - 5 * MIN - 10 * S),  # never sent
            ]
            await conn.executemany(
                "INSERT INTO irrigation_command (kind, args, status, sent_at) VALUES ('override', $1::jsonb, $2, $3)",
                [(json.dumps(args), status, sent_at) for args, status, sent_at in commands],
            )
            # Without a grant on it the read cannot see it, and no burst has an end time.
            assert {item.ends_at for item in (await as_reader(conn, reads.now)).overrides} == {None}
            await conn.execute(f"GRANT SELECT ON {SCHEMA}.irrigation_command TO {ROLE}")
            ends = {item.kind: item.ends_at for item in (await as_reader(conn, reads.now)).overrides}
            assert ends == {"fans": t0 + 12 * MIN, "vent_bypass": None}  # a push-button burst has none
        finally:
            await conn.execute(f"DROP TABLE {SCHEMA}.irrigation_command")

    scenario(dsn, body)


# ── API-02 and API-13: freshness and the reporting test ─────────────────────


def test_a_stale_heartbeat_or_climate_reads_unknown_with_ages(dsn):
    async def stale_heartbeat(conn, t0):
        await heartbeat(conn, t0 - 4 * MIN)
        await climate(conn, t0 - 10 * S, temp_avg=79.0, vpd_avg=1.0)
        await states(conn, "fan1", [(t0 - 2 * H, False)])
        now = await as_reader(conn, reads.now)
        assert now.freshness.ingestor.stale and now.freshness.ingestor.age_s >= 240
        assert now.band.temp_f.verdict == "unavailable" and now.running == [] and now.leak.active is None
        fan1 = (await as_reader(conn, reads.equipment)).items[0]
        assert (fan1.state, fan1.observed_at) == ("unknown", t0 - 2 * H)

    async def stale_climate(conn, t0):
        await heartbeat(conn, t0 - 30 * S)
        await climate(conn, t0 - 6 * MIN, temp_avg=79.0, vpd_avg=1.0, flow_gpm=0.0)
        for role, value in {"temp_low": 74.92, "temp_high": 84.92, "vpd_low": 0.873, "vpd_high": 1.303}.items():
            await snapshot(conn, t0 - 40 * S, f"band_house_{role}", value, "house", role)
        await states(conn, "fan1", [(t0 - 2 * H, False)])
        await states(conn, "leak_detected", [(t0 - 2 * H, False)])
        await states(conn, "heat1", [(t0 - 50 * MIN, True)])
        await states(conn, "grow_light_main", [(t0 - 12 * MIN, True)])
        await states(conn, "grow_light_main_intent", [(t0 - 1 * H, True)])
        now = await as_reader(conn, reads.now)
        assert not now.freshness.ingestor.stale and now.freshness.climate.stale
        assert now.freshness.climate.age_s >= 360 and now.band.vpd_kpa.verdict == "unavailable"
        # The 6-minute-old meter reading is not "no water now".
        assert (now.leak.active, now.leak.flow_gpm, now.leak.gal_last_10min) == (None, None, None)
        items = {item.equipment: item for item in (await as_reader(conn, reads.equipment)).items}
        assert {item.state for item in items.values()} == {"unknown"}
        assert items["fan1"].observed_at == t0 - 2 * H and items["heat1"].observed_at == t0 - 50 * MIN
        assert items["grow_light_main"].observed_at == t0 - 12 * MIN and items["grow_light_main"].intent == "unknown"
        assert {item.since for item in items.values()} == {None}

    scenario(dsn, stale_heartbeat)
    scenario(dsn, stale_climate)


# ── API-02: the writer ──────────────────────────────────────────────────────


def test_writer_counts_differing_settings_and_leaves_clamped_ones_out(dsn):
    params = candidate_params(26)
    assert len(params) == 26

    async def stuck(conn, t0):
        await reporting_now(conn, t0)
        took = t0 - 2 * H
        await plan_in_force(conn, "iris-vd54-writer", took, t0 + 2 * D)
        await waypoints(conn, "iris-vd54-writer", took, t0 + 2 * D, [(took, param, 1.0) for param in params[:19]])
        for param in params[:19]:
            await snapshot(conn, t0 - 90 * MIN, param, mismatch(param, 1.0))
            await snapshot(conn, t0 - 60 * S, param, mismatch(param, 1.0))
        await conn.execute(
            "INSERT INTO setpoint_changes (ts, parameter, value, source) VALUES ($1, $2, 1.0, 'plan')",
            t0 - 3 * MIN,
            params[0],
        )
        await alert(conn, t0 - 30 * MIN, "setpoint_delivery_stuck", "warning")
        writer = (await as_reader(conn, reads.now)).writer
        assert (writer.stuck, writer.differing, writer.oldest_since) == (True, 19, took)
        assert writer.last_at == t0 - 3 * MIN and 180 <= writer.age_s <= 240

    async def clamped(conn, t0):
        await reporting_now(conn, t0)
        took = t0 - 2 * H
        await plan_in_force(conn, "iris-vd54-clamped", took, t0 + 2 * D)
        await waypoints(conn, "iris-vd54-clamped", took, t0 + 2 * D, [(took, param, 1.0) for param in params[19:]])
        for param in params[19:]:
            await snapshot(conn, t0 - 60 * S, param, mismatch(param, 1.0))
            await conn.execute(
                "INSERT INTO setpoint_clamps (ts, parameter, requested, applied, reason) "
                "VALUES ($1, $2, 1.0, $3, 'fixture guardrail')",
                t0 - 100 * MIN,
                param,
                mismatch(param, 1.0),
            )
        writer = (await as_reader(conn, reads.now)).writer
        assert (writer.stuck, writer.differing, writer.oldest_since) == (False, 0, None)

    scenario(dsn, stuck)
    scenario(dsn, clamped)


# ── API-07: the plan, lanes and planner health ──────────────────────────────


def test_plan_lanes_waypoints_and_planner_health(dsn):
    trigger_id = uuid.uuid4()

    async def body(conn, t0):
        await reporting_now(conn, t0)
        written, expires = t0 - 6 * H, t0 + 40 * H  # "written 07:02"; one waypoint lies past 24 h
        intents = [{"segment": "afternoon", "mister_engage_kpa": 1.70}]
        await plan_in_force(
            conn,
            "iris-20261101-0702",
            written,
            expires,
            trigger_id=trigger_id,
            hypothesis="Hot, dry afternoon",
            structured={"stress_windows": ["13:00-17:30"]},
            intents=intents,
        )
        rows = [(written, "mister_engage_kpa", 1.60), (t0 + 4 * H, "mister_engage_kpa", 1.70)]  # the "13:00" waypoint
        rows += [(t0 + 30 * H, "mister_engage_kpa", 1.65), (written, "mister_all_kpa", 1.50)]
        rows += [(t0 + 4 * H, "mister_all_kpa", 1.80), (written, "mister_pulse_on_s", 30.0)]
        rows += [(t0 + 4 * H, "mister_pulse_on_s", 45.0)]
        await waypoints(conn, "iris-20261101-0702", written, expires, rows)
        await plan_in_force(conn, "iris-20261031-1800", t0 - 20 * H, t0 + 1 * D, status="superseded")
        await waypoints(conn, "iris-20261031-1800", t0 - 20 * H, t0 + 1 * D, [(t0 + 2 * H, "mister_all_kpa", 9.9)])
        await waypoints(
            conn, "iris-oneshot-vd54", t0 - 1 * H, t0 + 5 * H, [(t0 - 1 * H, "vpd_hysteresis", 0.2)]
        )  # an active one-shot
        await waypoints(
            conn,
            "preemptive-vd54",
            t0 - 30 * MIN,
            t0 + 6 * H,
            [(t0 + 2 * H, "sw_economiser_enabled", 0.0)],
            source="preemptive",
        )
        await conn.execute(
            "INSERT INTO desired_state (param, value, manual, source, actor, reason, created_at) VALUES "
            "('mister_engage_kpa', 1.55, false, 'dashboard', 'jason', 'drier', $1), "
            "('mister_pulse_on_s', 60, true, 'dashboard', 'jason', NULL, $2)",
            t0 - 5 * H,  # "08:00": newer than the 07:02 plan, not manual
            t0 - 3 * D,
        )
        for ts in (t0 - 4 * H, t0 - 60 * S):
            await snapshot(conn, ts, "mister_engage_kpa", 1.5499999523162842)  # binary32 1.55
        await snapshot(conn, t0 - 60 * S, "mister_pulse_on_s", 60.0)
        await snapshot(conn, t0 - 60 * S, "mister_all_kpa", 1.50)
        ledger = [
            (1, "SUNSET", t0 - 20 * H, "missed", "timeout", "sla_expired", None),
            (2, "SUNRISE", t0 - 6 * H - 2 * MIN, "plan_written", "set_plan", None, trigger_id),
            (3, "SUNSET", t0 + 5 * H, "expected", None, None, None),
            (4, "SOLAR_MAX", t0 - 2 * H, "acked", "acknowledge_trigger", None, None),
            (5, "MIDNIGHT", t0 - 40 * H, "plan_written", "set_plan", None, None),  # outside the 36 h
        ]
        await conn.executemany(
            "INSERT INTO planner_trigger_ledger (id, event_type, expected_at, due_at, status, sla_seconds, "
            "terminal_action, terminal_at, resolved_at, failure_class, trigger_id) "
            "VALUES ($1, $2, $3::timestamptz, $3::timestamptz + interval '1800 seconds', $4, 1800, $5::text, "
            "CASE WHEN $5::text IS NULL THEN NULL ELSE $3::timestamptz + interval '10 minutes' END, "
            "CASE WHEN $5::text IS NULL THEN NULL ELSE $3::timestamptz + interval '10 minutes' END, $6, $7)",
            ledger,
        )

        result = await as_reader(conn, reads.plan, hours=24)
        record = result.plan
        assert (record.plan_id, record.trigger, record.written_at, record.valid_until) == (
            "iris-20261101-0702",
            "SUNRISE",
            written,
            expires,
        )
        assert (record.status, record.hypothesis) == ("effective", "Hot, dry afternoon")
        assert record.detail == {
            "hypothesis_structured": {"stress_windows": ["13:00-17:30"]},
            "climate_intents": intents,
        }
        assert result.no_plan_since is None
        lanes = {lane.param: lane for lane in result.lanes}
        assert set(lanes) == {
            "mister_engage_kpa",
            "mister_all_kpa",
            "mister_pulse_on_s",
            "vpd_hysteresis",
            "sw_economiser_enabled",
        }
        engage = lanes["mister_engage_kpa"]
        assert engage.applied_from == "desired"
        assert engage.waypoints == [reads.PlanWaypoint(at=t0 + 4 * H, value=1.70, source="iris", effective=False)]
        assert (engage.desired.value, engage.desired.manual, engage.desired.state) == (1.55, False, "confirmed")
        assert engage.desired.confirmed_at == t0 - 4 * H
        assert engage.desired.actor == reads.ActorRef(source="dashboard", name="jason")
        assert engage.device == reads.Reading(value=1.5499999523162842, observed_at=t0 - 60 * S)
        assert engage.label == REGISTRY["mister_engage_kpa"].label and engage.unit == REGISTRY["mister_engage_kpa"].unit
        everyday = lanes["mister_all_kpa"]
        assert everyday.applied_from == "plan" and everyday.desired is None
        assert everyday.waypoints == [reads.PlanWaypoint(at=t0 + 4 * H, value=1.80, source="iris", effective=True)]
        manual = lanes["mister_pulse_on_s"]
        assert (manual.applied_from, manual.waypoints, manual.desired.manual) == ("manual", [], True)
        assert result.manual_count == 1
        assert (lanes["vpd_hysteresis"].applied_from, lanes["vpd_hysteresis"].waypoints) == ("plan", [])
        forecast = lanes["sw_economiser_enabled"]
        assert forecast.waypoints == [reads.PlanWaypoint(at=t0 + 2 * H, value=0.0, source="preemptive", effective=True)]

        assert [row.trigger for row in result.health] == ["SUNSET", "SUNRISE", "SOLAR_MAX", "SUNSET"]
        missed = result.health[0]
        assert (missed.outcome, missed.required, missed.failure_class) == ("missed", True, "sla_expired")
        assert all(row.due_at == row.expected_at + timedelta(seconds=1800) for row in result.health)
        assert result.health[2].required is False and result.health[3].outcome == "expected"

        longer = {lane.param: lane for lane in (await as_reader(conn, reads.plan, hours=72)).lanes}
        assert [waypoint.at for waypoint in longer["mister_engage_kpa"].waypoints] == [t0 + 4 * H, t0 + 30 * H]

        iris = (await as_reader(conn, reads.now)).iris
        # The newest required trigger that fired is the SUNRISE plan: the day's SUNSET ahead says nothing yet.
        assert iris == reads.Iris(
            plan_id="iris-20261101-0702", trigger="SUNRISE", valid_until=expires, required_trigger="ok"
        )

    async def no_plan(conn, t0):
        await reporting_now(conn, t0)
        await plan_in_force(conn, "iris-expired", t0 - 20 * H, t0 - 1 * H, status="expired")
        await plan_in_force(conn, "iris-superseded", t0 - 30 * H, t0 + 1 * H, status="superseded")
        await conn.execute(
            "INSERT INTO planner_trigger_ledger (id, event_type, expected_at, due_at, status, sla_seconds, "
            "terminal_action, terminal_at, resolved_at, failure_class) "
            "VALUES (1, 'SUNSET', $1::timestamptz, $1::timestamptz + interval '1800 seconds', 'missed', 1800, "
            "'timeout', $2, $2, 'sla_expired')",
            t0 - 3 * H,
            t0 - 2 * H,
        )
        result = await as_reader(conn, reads.plan)
        assert (result.plan, result.no_plan_since, result.lanes) == (None, t0 - 1 * H, [])
        iris = (await as_reader(conn, reads.now)).iris
        assert (iris.plan_id, iris.trigger, iris.required_trigger) == (None, None, "missed")

    scenario(dsn, body)
    scenario(dsn, no_plan)


# ── API-02: the leak and the one open-alert count ───────────────────────────


def test_leak_alert_stays_until_resolved_and_reads_flow_stopped(dsn):
    async def body(conn, t0):
        await heartbeat(conn, t0 - 20 * S)
        flow = [(8, 1.1), (5, 0.6), (3, 0.05), (2, 0.0)]
        for minutes, gpm in flow:
            await climate(conn, t0 - minutes * MIN, flow_gpm=gpm, temp_avg=79.0, vpd_avg=1.0)
        await climate(conn, t0 - 1 * MIN, temp_avg=79.0, vpd_avg=1.0)  # the meter column missing
        oldest = await alert(conn, t0 - 30 * MIN, "leak_detected", "critical", category="water")
        await alert(conn, t0 - 9 * MIN, "leak_detected", "critical", category="water")
        await alert(conn, t0 - 2 * H, "soil_sensor_offline", "warning", zone="west", category="sensor")
        await alert(
            conn, t0 - 1 * H, "soil_sensor_offline", "info", zone="west", category="sensor", disposition="acknowledged"
        )
        await alert(
            conn, t0 - 1 * H, "sensor_offline", "warning", zone="south", category="sensor", disposition="acknowledged"
        )
        await alert(conn, t0 - 1 * H, "fan_relay_stuck", "critical", disposition="suppressed")
        await alert(conn, t0 - 1 * H, "fan_relay_stuck", "critical", disposition="resolved", resolved=True)
        await states(conn, "leak_detected", [(t0 - 31 * MIN, False), (t0 - 30 * MIN, True), (t0 - 6 * MIN, False)])
        await states(conn, "leak_detected", [(t0 - 4 * MIN, False)])  # the other writer repeats it
        await conn.executemany(
            "INSERT INTO water_meter_events (id, ts, event_type, delta_gal, quality_flag) VALUES ($1, $2, $3, $4, $5)",
            [
                (1, t0 - 8 * MIN, "delta", 1.0, "ok"),
                (2, t0 - 6 * MIN, "delta", 1.0, "ok"),
                (3, t0 - 15 * MIN, "delta", 1.0, "ok"),
                (4, t0 - 5 * MIN, "delta", 3.0, "suspect"),
                (5, t0 - 4 * MIN, "gap", 0.0, "source_gap"),
            ],
        )

        now = await as_reader(conn, reads.now)
        assert now.leak == reads.Leak(
            active=False,
            since=t0 - 30 * MIN,
            flow_gpm=0.0,
            gal_last_10min=2,
            alert_id=oldest,
            stopped_at=t0 - 2 * MIN,
        )
        assert now.alerts == reads.AlertCounts(open=2, critical=1, warning=0, info=1)

        await climate(conn, t0 - 30 * S, flow_gpm=0.4, temp_avg=79.0, vpd_avg=1.0)  # flow starts again
        leak = (await as_reader(conn, reads.now)).leak
        assert (leak.alert_id, leak.stopped_at, leak.flow_gpm) == (oldest, None, 0.4)

    scenario(dsn, body)
