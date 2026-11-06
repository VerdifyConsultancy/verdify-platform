"""The irrigation reads (VD-40, #1155) on a private PostgreSQL, read as the dashboard and the MCP logins.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback
PostgreSQL with TimescaleDB and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``. Without one the module skips, and a
skip fails campaign qualification. ``irrigation_db_support.prepare_irrigation_tables`` loads what M4 and M3 read and
their bodies; this file adds what the reads also touch from ``db/schema.sql`` and M1's body. The MCP login gets only
the grants its migrations give it (each checked against 259's text), and the dashboard login the grants VD-74 (M2b,
#1233) lists, so a read of anything else fails here. Every age is measured from the server's now(), so each fixture
is placed relative to it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import re
import time

import irrigation_db_support as support
import jsonschema
import pytest
import yaml
from conftest import isolated_mutation_test_dsn

from verdify_schemas import irrigation as irr
from verdify_services import alerts, reads, settings
from verdify_services.actor import Actor
from verdify_services.irrigation import reads as water
from verdify_services.irrigation import runs as run_rules
from verdify_services.labels import EQUIPMENT_LINE_IDS
from verdify_services.problems import ServiceError

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

DASHBOARD, MCP = "verdify_dashboard_runtime_login", "verdify_mcp_runtime_login"
LOGINS = (DASHBOARD, MCP)
PERSON = Actor.person("jason", "viewer")
OPENAPI = yaml.safe_load((support.REPO_ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
M1 = support.REPO_ROOT / "db/migrations/280-desired-state-activity-log.sql"
S = dt.timedelta(seconds=1)
MIN = dt.timedelta(minutes=1)
H = dt.timedelta(hours=1)
D = dt.timedelta(days=1)

# What the reads touch beyond irrigation_db_support's subset, from db/schema.sql in dependency order.
READS_OBJECTS = (
    ("idx_system_state_entity", "INDEX"),
    ("alert_log", "TABLE"),
    ("alert_log_id_seq", "SEQUENCE"),
    ("alert_log_id_seq", "SEQUENCE OWNED BY"),
    ("alert_log id", "DEFAULT"),
    ("alert_log alert_log_pkey", "CONSTRAINT"),
    ("idx_alert_log_type", "INDEX"),
    ("slack_alert_runbooks", "TABLE"),
    ("setpoint_snapshot", "TABLE"),
    ("setpoint_snapshot_ts_idx", "INDEX"),
    ("idx_snapshot_param", "INDEX"),
    ("setpoint_clamps", "TABLE"),
    ("plan_journal", "TABLE"),
    ("plan_journal plan_journal_pkey", "CONSTRAINT"),
    ("v_active_plan", "VIEW"),
    ("crop_band_anchors", "TABLE"),
)
# Read by the services as both logins; 259 grants each to the MCP duty (checked), VD-74 to the dashboard duty.
SHARED_READS = (
    "alert_log",
    "climate",
    "equipment_state",
    "setpoint_changes",
    "setpoint_clamps",
    "setpoint_snapshot",
    "slack_alert_runbooks",
    "system_state",
    "v_active_plan",
)
# VD-74's other reads: M1's and M4's relations, which 280 and 281 grant the MCP duty themselves.
DASHBOARD_ONLY_READS = ("activity_log", "v_desired_state_current", "v_irrigation_day_coverage")


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


def mcp_grants_259() -> set[str]:
    text = support.MCP_BOUNDARY_MIGRATION.read_text()
    block = re.search(r"GRANT SELECT ON\s+(public\.alert_log.*?)\s+TO verdify_mcp_runtime;", text, re.S)
    assert block, "259's read grant moved"
    return {name.strip().removeprefix("public.") for name in block.group(1).split(",")}


@pytest.fixture(scope="module")
def database():
    async def setup():
        conn = await connect()
        try:
            await support.prepare_irrigation_tables(conn)
            sections = support.schema_sections()
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute("\n".join(sections[(name, kind, "public")] for name, kind in READS_OBJECTS))
                await conn.execute("SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(M1))
            finally:
                await conn.execute("RESET search_path")
                await conn.execute("RESET ROLE")
            assert set(SHARED_READS) <= mcp_grants_259()
            relations = ", ".join(f"public.{name}" for name in SHARED_READS)
            dashboard_only = ", ".join(f"public.{name}" for name in DASHBOARD_ONLY_READS)
            await conn.execute(
                f"""
                GRANT SELECT ON {relations} TO verdify_mcp_runtime, verdify_dashboard_runtime;
                GRANT SELECT ON {dashboard_only} TO verdify_dashboard_runtime;
                GRANT EXECUTE ON FUNCTION public.fn_irrigation_minutes_daily(date, date),
                    public.fn_mcp_armed_assignment_context() TO verdify_dashboard_runtime;
                """
            )
            await conn.execute("ANALYZE")
            return {
                "lines": [dict(row) for row in await conn.fetch("SELECT * FROM public.irrigation_line")],
                "slots": [dict(row) for row in await conn.fetch("SELECT * FROM public.irrigation_slot")],
            }
        finally:
            await conn.close()

    return run(setup())


# ── Fixture rows ────────────────────────────────────────────────────────────


class Fixture:
    """One owner connection on emptied tables, with the program's seeds back in place."""

    def __init__(self, conn, seeds):
        self.conn = conn
        self.seeds = seeds
        self.at: dt.datetime | None = None
        self.midnight: dt.datetime | None = None

    async def reset(self):
        await self.conn.execute(
            "TRUNCATE public.equipment_state, public.climate, public.system_state, public.water_meter_events, "
            "public.data_gaps, public.alert_log, public.setpoint_snapshot, public.activity_log, public.desired_state, "
            "public.irrigation_ledger, public.irrigation_command, public.irrigation_push"
        )
        await self.conn.execute("DELETE FROM public.irrigation_slot WHERE slot_id > 3")
        for row in self.seeds["lines"]:
            await self.conn.execute(
                "UPDATE public.irrigation_line SET enabled = $2, vpd_member = $3, schedule_member = $4, "
                "daily_cap_min = $5, vpd_reserve_min = $6, paused_until = NULL, manual = false, checked_by = NULL, "
                "checked_at = NULL, check_note = NULL, updated_at = now() - interval '30 days' WHERE line_id = $1",
                row["line_id"],
                row["enabled"],
                row["vpd_member"],
                row["schedule_member"],
                row["daily_cap_min"],
                row["vpd_reserve_min"],
            )
        await self.conn.execute(
            "UPDATE public.irrigation_slot SET enabled = true, updated_at = now() - interval '30 days', "
            "created_at = now() - interval '30 days'"
        )
        self.at = await self.conn.fetchval("SELECT now()")
        self.midnight = reads.local_midnight(self.at)

    def today_at(self, fraction: float) -> dt.datetime:
        """A moment of today so far (0 is local midnight, 1 is now), so a run stays today at any hour. In UTC: adding to
        a time in the local zone would count wall-clock hours, and land past now on the 25-hour day."""
        return self.midnight.astimezone(dt.UTC) + (self.at - self.midnight) * fraction

    async def reporting(self, *, climate_age=0 * S, heartbeat_age=30 * S, **climate):
        """A fresh heartbeat and climate row. The row is stamped now, so today has one even just after local midnight."""
        await self.state("ingestor_heartbeat", "alive", self.at - heartbeat_age)
        await self.climate(self.at - climate_age, **climate)

    async def climate(self, ts, **columns):
        names = ["ts", "greenhouse_id", *columns]
        marks = ", ".join(f"${index}" for index in range(1, len(names) + 1))
        await self.conn.execute(
            f"INSERT INTO public.climate ({', '.join(names)}) VALUES ({marks})", ts, "vallery", *columns.values()
        )

    async def state(self, entity, value, ts):
        await self.conn.execute(
            "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) VALUES ($1, $2, $3, 'vallery')",
            ts,
            entity,
            value,
        )

    async def edge(self, relay, ts, state):
        await self.conn.execute(
            "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) VALUES ($1, $2, $3, 'vallery')",
            ts,
            relay,
            state,
        )

    async def run(self, relay, start, end=None):
        await self.edge(relay, start, True)
        if end is not None:
            await self.edge(relay, end, False)

    async def delta(self, ts, prior_ts, gal, klass="meter_attributed"):
        await self.conn.execute(
            "INSERT INTO public.water_meter_events (id, ts, greenhouse_id, event_type, quality_flag, prior_ts, "
            "delta_gal, attribution_class) VALUES ((SELECT coalesce(max(id), 0) + 1 FROM public.water_meter_events), "
            "$1, 'vallery', 'delta', 'ok', $2, $3, $4)",
            ts,
            prior_ts,
            float(gal),
            klass,
        )

    async def ledger(self, line_id, cause, started_at, ended_at=None, **columns):
        day = columns.pop("local_day", support.local_day(started_at) if started_at else support.local_day(self.at))
        names = ["line_id", "cause", "started_at", "ended_at", "local_day", *columns]
        marks = ", ".join(f"${index}" for index in range(1, len(names) + 1))
        return await self.conn.fetchval(
            f"INSERT INTO public.irrigation_ledger ({', '.join(names)}) VALUES ({marks}) RETURNING id",
            line_id,
            cause,
            started_at,
            ended_at,
            day,
            *columns.values(),
        )

    async def command(self, sent_at, *, line_id="L01", actor="jason", status="sent"):
        return await self.conn.fetchval(
            "INSERT INTO public.irrigation_command (kind, line_id, status, source, actor, created_at, sent_at) "
            "VALUES ('run', $1, $2, 'dashboard', $3, $4, $4) RETURNING command_id",
            line_id,
            status,
            actor,
            sent_at,
        )

    async def alert(self, alert_type, ts, *, sensor_id="meter.main", resolved=False, disposition="open"):
        return await self.conn.fetchval(
            "INSERT INTO public.alert_log (ts, alert_type, severity, category, sensor_id, message, source, "
            "disposition, resolved_at) VALUES ($1, $2, 'critical', 'water', $3, 'fixture', 'system', $4, $5) "
            "RETURNING id",
            ts,
            alert_type,
            sensor_id,
            disposition,
            ts if resolved else None,
        )

    async def pause(self, target, until, *, source="dashboard", actor="jason", reason=None, ts=None):
        await self.conn.execute(
            "INSERT INTO public.activity_log (ts, source, actor, action, target, detail, reason) "
            "VALUES ($1, $2, $3, 'irrigation.line.update', $4, $5::jsonb, $6)",
            ts or self.at - MIN,
            source,
            actor,
            target,
            json.dumps({"paused_until": until.isoformat() if until else None}),
            reason,
        )
        lines = [target] if target != "all" else [row["line_id"] for row in self.seeds["lines"]]
        await self.conn.execute(
            "UPDATE public.irrigation_line SET paused_until = $2 WHERE line_id = ANY($1::text[])", lines, until
        )

    async def set_line(self, line_id, **columns):
        sets = ", ".join(f"{name} = ${index}" for index, name in enumerate(columns, start=2))
        await self.conn.execute(
            f"UPDATE public.irrigation_line SET {sets} WHERE line_id = $1", line_id, *columns.values()
        )

    async def slot(self, line_id, *, created=None, updated=None, kind="clock", minute=None, **columns):
        """A schedule due every day, at a minute that has already passed today unless ``minute`` says otherwise."""
        local = self.at.astimezone(support.DENVER)
        start_min = max(0, local.hour * 60 + local.minute - 1) if minute is None else minute
        fields = {"on_s": 600, "count": 1, "gap_s": 0, "label": "fixture"} | columns
        names = [
            "line_id",
            "days",
            "start_kind",
            "start_min",
            "source",
            "updated_by",
            "created_at",
            "updated_at",
            *fields,
        ]
        marks = ", ".join(f"${index}" for index in range(1, len(names) + 1))
        return await self.conn.fetchval(
            f"INSERT INTO public.irrigation_slot ({', '.join(names)}) VALUES ({marks}) RETURNING slot_id",
            line_id,
            127,
            kind,
            start_min,
            "dashboard",
            "jason",
            created or self.at - 30 * D,
            updated or self.at - 30 * D,
            *fields.values(),
        )

    async def compiled(self) -> irr.CompiledProgram:
        lines = [irr.LineSpec.from_row(row) for row in await self.conn.fetch("SELECT * FROM public.irrigation_line")]
        slots = [
            irr.ScheduleSpec.from_row(row) for row in await self.conn.fetch("SELECT * FROM public.irrigation_slot")
        ]
        site = irr.site_values(await self.conn.fetch("SELECT * FROM public.v_desired_state_current"))
        return irr.compile_program(lines, slots, site)

    async def push(
        self, record, *, revision=None, site_desired_at=None, pushed_at=None, confirmed_at=None, reject=None
    ):
        sql = (
            "INSERT INTO public.irrigation_push ({columns}sha256, bytes, site_desired_at, pushed_at, confirmed_at, "
            "reject_code) {override}VALUES ({values}$1, $2, $3, $4, $5, $6) RETURNING revision"
        )
        if revision is None:
            sql = sql.format(columns="", override="", values="")
            args = ()
        else:
            sql = sql.format(columns="revision, ", override="OVERRIDING SYSTEM VALUE ", values="$7, ")
            args = (revision,)
        return await self.conn.fetchval(
            sql, record.sha256, record.record, site_desired_at, pushed_at, confirmed_at, reject, *args
        )

    async def engine_on(self, *, irr_state="v=hoo", compiled=None, revision=None, confirmed=True, src="nvs"):
        """The engine firmware on the controller, running the program the tables compile to now."""
        await self.state("irr_engine", "true", self.at - 2 * H)
        await self.state("irr_state", irr_state, self.at - 5 * MIN)
        compiled = compiled or await self.compiled()
        await self.state("irr_program_id", f"h={compiled.hash16} src={src}", self.at - 10 * MIN)
        if confirmed:
            await self.push(
                compiled,
                revision=revision,
                site_desired_at=self.at - H,
                pushed_at=self.at - 11 * MIN,
                confirmed_at=self.at - 10 * MIN,
            )
        return compiled


def scenario(database, body):
    """Run ``body(fixture)`` on freshly reset tables, committed, so the logins read them in their own snapshots."""

    async def go():
        conn = await connect()
        try:
            fixture = Fixture(conn, database)
            await fixture.reset()
            await body(fixture)
        finally:
            await conn.close()

    run(go())


async def as_login(login, read):
    conn = await connect(login)
    try:
        return await read(conn)
    finally:
        await conn.close()


async def as_both(read):
    """``read(conn)`` as each login; the same answers, by the same functions, return for both."""
    return [await as_login(login, read) for login in LOGINS]


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [f"{list(error.path)}: {error.message}" for error in validator.iter_errors(instance)]


def as_json(model) -> dict:
    return model.model_dump(mode="json", by_alias=True)


def line_of(status, line_id):
    return next(line for line in status.lines if line.line_id == line_id)


def status_read(conn):
    return water.status(conn, PERSON)


# ── The four operations against the committed OpenAPI ───────────────────────


def test_the_four_reads_validate_against_the_committed_schemas_as_both_logins(database):
    async def body(f):
        await f.reporting(water_total_gal=100.0, flow_gpm=0.0, mister_water_today=12.0)
        started, ended = f.today_at(0.3), f.today_at(0.4)
        await f.run("drip_wall", started, ended)
        await f.run("mister_center", f.today_at(0.5), f.today_at(0.6))
        slot = await f.slot("L01")
        await f.ledger("L01", "schedule", started - S, ended, slot_id=slot, open_s=60, pulses=1, outcome="ok")
        await f.delta(ended, started - S, 3)
        await f.engine_on(irr_state="d=0,schedule,4,1800000000,1800000600 w=4,man v=opo")
        await f.conn.execute(
            "INSERT INTO public.setpoint_snapshot (ts, parameter, value) VALUES ($1, 'mister_water_budget_gal', 300)",
            f.at - MIN,
        )
        today = support.local_day(f.at)

        async def read(conn):
            return (
                await water.status(conn, PERSON),
                await water.agenda(conn, PERSON, days=7, past_days=7),
                await water.minutes(conn, PERSON, today - dt.timedelta(days=2), today, "line"),
                await water.runs(conn, PERSON, f.at - 2 * D, f.at),
            )

        for status, agenda, minutes, runs in await as_both(read):
            assert openapi_errors("IrrigationStatus", as_json(status)) == []
            assert openapi_errors("ScheduleAgenda", as_json(agenda)) == []
            assert openapi_errors("MinutesReport", as_json(minutes)) == []
            assert openapi_errors("RunPage", as_json(runs)) == []
            assert [line.line_id for line in status.lines] == list(irr.LINE_IDS)
            assert status.limits.soft_budget_gal.max == 300.0
            assert status.limits.saf4_gal.used == 12.0
            assert agenda.counts.schedules == 4 and agenda.counts.max_schedules == 24
            assert set(agenda.bounds) == {"drip", "fert_drip", "mister", "fert_mister"}
            assert len(agenda.solar) == 14

    scenario(database, body)


def test_the_reads_run_in_one_read_only_repeatable_read_snapshot(database):
    async def body(f):
        await f.reporting()

        async def read(conn):
            async with water._snapshot(conn):
                modes = (
                    await conn.fetchval("SHOW transaction_isolation"),
                    await conn.fetchval("SHOW transaction_read_only"),
                )
            await water.status(conn, PERSON)
            assert not conn.is_in_transaction()
            return modes

        assert await as_both(read) == [("repeatable read", "on")] * 2

    scenario(database, body)


# ── Status: engine, program, lines ──────────────────────────────────────────


def test_before_the_engine(database):
    """With irr_engine false: relay edges, src null, site writes held, occurrences blocked, no next run."""

    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        await f.run("drip_wall", f.today_at(0.5))
        await f.run("mister_center", f.today_at(0.2), f.today_at(0.3))
        await f.run("fert_master_valve", f.today_at(0.7))
        slot = await f.slot("L01", minute=1439)
        write = f.at - 3 * H
        await f.conn.execute(
            "INSERT INTO public.desired_state (param, value, manual, source, actor, created_at) "
            "VALUES ('mister_daily_volume_max_gal', 400, false, 'dashboard', 'jason', $1)",
            write,
        )

        async def read(conn):
            return (
                await water.status(conn, PERSON),
                await water.agenda(conn, PERSON, days=2),
                await settings.status(conn, "mister_daily_volume_max_gal"),
            )

        for status, agenda, site in await as_both(read):
            assert status.engine is False
            program = status.program
            assert (program.src, program.reason, program.state) == (None, "engine not on controller", "requested")
            assert program.applied_hash is None and program.applied_revision is None and program.next_retry_at is None
            l01 = line_of(status, "L01")
            assert (l01.state.code, l01.state.cause) == ("running", irr.IrrigationCause.UNKNOWN)
            assert l01.state.observed_at == f.today_at(0.5)
            assert line_of(status, "L04").state.code == "idle"
            assert (line_of(status, "L03").state.code, line_of(status, "L03").state.cause) == (
                "running",
                irr.IrrigationCause.FEED,
            )
            assert all(line.next_run is None for line in status.lines)
            assert {line.sync for line in status.lines} == {"requested"}
            assert [(run.manifold, run.line_id, run.started_at) for run in status.active_runs] == [
                ("drip", "L01", f.today_at(0.5))
            ]
            assert status.fallback_active is False
            assert status.freshness.observed_at is None and status.freshness.stale is True
            assert status.limits.saf4_gal.max == 360.0  # the registry default

            assert (site.state, site.state_reason.code, site.state_reason.since) == ("requested", "no_engine", write)
            assert site.state_reason.detail == "applies with the irrigation firmware"

            planned = [item for item in agenda.occurrences if item.slot_id == slot]
            assert planned and {item.status for item in planned} == {"blocked"}
            assert {item.reason for item in planned} == {"arrives with the irrigation firmware"}
            assert {schedule.sync for schedule in agenda.schedules} == {"requested"}

    scenario(database, body)


def test_no_irr_engine_row_reads_the_same_as_false(database):
    async def body(f):
        await f.reporting()
        for status in await as_both(status_read):
            assert (status.engine, status.program.src) == (False, None)
            assert status.program.reason == "engine not on controller"
            assert {line.state.code for line in status.lines} == {"unknown"}  # no relay row at all

    scenario(database, body)


def test_program_rev_42_confirmed_after_a_site_write_reads_confirmed_with_360(database):
    async def body(f):
        await f.reporting()
        write = f.at - 2 * H
        await f.conn.execute(
            "INSERT INTO public.desired_state (param, value, manual, source, actor, created_at) "
            "VALUES ('mister_daily_volume_max_gal', 360, true, 'dashboard', 'jason', $1)",
            write,
        )
        compiled = await f.compiled()
        await f.state("irr_engine", "true", f.at - 3 * H)
        await f.state("irr_program_id", f"h={compiled.hash16} src=nvs", f.at - H)
        confirmed_at = f.at - H
        await f.push(compiled, revision=42, site_desired_at=write, pushed_at=f.at - H - MIN, confirmed_at=confirmed_at)

        async def read(conn):
            return await water.status(conn, PERSON), await settings.status(conn, "mister_daily_volume_max_gal")

        for status, site in await as_both(read):
            assert (site.state, site.state_reason, site.confirmed_at) == ("confirmed", None, confirmed_at)
            assert (site.readback.value, site.readback.observed_at) == (360.0, confirmed_at)
            program = status.program
            assert (program.state, program.src, program.reason) == ("confirmed", "nvs", None)
            assert (program.revision, program.applied_revision, program.applied_hash) == (42, 42, compiled.hash16)
            assert program.desired_sha256 == compiled.sha256
            assert status.limits.saf4_gal.max == 360.0
            assert {line.sync for line in status.lines} == {"confirmed"}

    scenario(database, body)


def test_program_states_and_item_sync_as_the_desired_hash_moves(database):
    async def body(f):
        await f.reporting()
        applied = await f.engine_on(revision=7)
        await f.set_line("L01", daily_cap_min=90, updated_at=f.at - 2 * MIN)  # changes the record
        status = (await as_both(status_read))[1]
        assert status.program.state == "requested"  # no push of the desired hash yet
        assert status.program.applied_revision == 7
        assert line_of(status, "L01").sync == "requested"
        assert line_of(status, "L02").sync == "confirmed"  # its write precedes the applied push

        desired = await f.compiled()
        assert desired.sha256 != applied.sha256
        push = await f.push(desired, revision=8, pushed_at=f.at - 2 * MIN)
        status = (await as_both(status_read))[0]
        assert status.program.state == "requested"
        assert (status.program.revision, status.program.pushed_at) == (push, f.at - 2 * MIN)

        await f.conn.execute(
            "UPDATE public.irrigation_push SET pushed_at = $1 WHERE revision = $2", f.at - 6 * MIN, push
        )
        assert (await as_both(status_read))[0].program.state == "not_confirmed"

        await f.conn.execute(
            "UPDATE public.irrigation_push SET pushed_at = now(), reject_code = 'bad_slot' WHERE revision = $1", push
        )
        status = (await as_both(status_read))[0]
        assert (status.program.state, status.program.reject_code) == ("not_confirmed", "bad_slot")

        await f.set_line("L01", updated_at=f.at - 6 * MIN)
        assert line_of((await as_both(status_read))[0], "L01").sync == "not_confirmed"

    scenario(database, body)


def test_a_controller_on_the_built_in_program(database):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "true", f.at - H)
        await f.state("irr_program_id", "h=0000000000000000 src=rom", f.at - MIN)
        await f.state("irr_state", "", f.at - MIN)
        status = (await as_both(status_read))[0]
        assert (status.program.src, status.program.applied_hash) == ("rom", "0000000000000000")
        assert status.program.applied_revision is None
        assert status.limits.saf4_gal.max == 360.0
        assert line_of(status, "L01").state.code == "idle"
        assert line_of(status, "L02").state.code == "disabled"
        assert line_of(status, "L03").state.code == "unknown"  # no relay row

    scenario(database, body)


def test_engine_tokens_active_runs_fallback_and_faults(database):
    async def body(f):
        await f.reporting()
        await f.set_line("L07", enabled=True, schedule_member=True)
        epoch = int(f.at.timestamp())
        token = (
            f"d=0,schedule,4,{epoch - 60},{epoch + 540} m=3,fallback,1,{epoch - 30},{epoch + 15} "
            f"w=6,occ f=8,l,{epoch + 3600} v=oph"
        )
        await f.engine_on(irr_state=token)
        await f.slot("L01")
        await f.run("fog", f.today_at(0.9))
        status = (await as_both(status_read))[1]
        l01, l04, l07, l09 = (line_of(status, line_id) for line_id in ("L01", "L04", "L07", "L09"))
        assert (l01.state.code, l01.state.cause, l01.state.slot_id) == ("running", irr.IrrigationCause.SCHEDULE, 4)
        assert l01.state.ends_at.timestamp() == epoch + 540
        assert (l04.state.code, l04.state.cause, l04.vpd_source) == ("running", irr.IrrigationCause.FALLBACK, "own")
        assert (l07.state.code, l07.state.reason, l07.vpd_source) == ("waiting", "someone inside (Frigate)", "peer")
        assert (l09.state.code, l09.fault, l09.vpd_source) == ("disabled", "mister_fault", "house")
        assert l09.fault_until.timestamp() == epoch + 3600
        assert line_of(status, "L11").state.code == "running"  # never named by the engine; its relay reads
        assert line_of(status, "L11").state.cause == irr.IrrigationCause.VPD
        assert status.fallback_active is True
        assert [(run.manifold, run.line_id, run.pulses_total) for run in status.active_runs] == [
            ("drip", "L01", None),
            ("mister", "L04", 4),
        ]
        assert status.freshness.stale is False and status.freshness.age_s >= 300

    scenario(database, body)


def test_unknown_comes_only_from_the_reporting_test(database):
    async def body(f):
        await f.reporting(climate_age=6 * MIN)
        await f.run("drip_wall", f.at - H, f.at - 50 * MIN)
        await f.engine_on(irr_state="v=hoo")
        status = (await as_both(status_read))[0]
        assert status.engine is True
        assert {line.state.code for line in status.lines} == {"unknown"}
        assert line_of(status, "L01").state.observed_at == f.at - 5 * MIN  # the newest row, so the age shows
        assert status.active_runs == [] and status.freshness.stale is True
        assert status.limits.saf4_gal.used is None and status.occupancy.occupied is None

    scenario(database, body)


def test_an_irr_state_published_hours_ago_still_holds_while_the_controller_reports(database):
    async def body(f):
        await f.reporting()
        await f.engine_on(irr_state="v=ooo")
        await f.conn.execute("UPDATE public.system_state SET ts = $1 WHERE entity = 'irr_state'", f.at - 7 * H)
        status = (await as_both(status_read))[0]
        assert line_of(status, "L01").state.code == "idle"
        assert line_of(status, "L04").vpd_source == "own"
        assert status.freshness.stale is False and status.freshness.age_s >= 7 * 3600

    scenario(database, body)


def test_line_fields_paused_by_reserved_and_the_check(database):
    async def body(f):
        await f.reporting()
        await f.engine_on()
        until = f.at + 2 * H
        await f.pause("L01", until, reason="repotting the wall", actor="jason")
        await f.set_line("L04", checked_by="jason", checked_at=f.at - D, check_note="nozzles clear", manual=True)
        status = (await as_both(status_read))[1]
        l01 = line_of(status, "L01")
        assert (l01.state.code, l01.paused_until) == ("paused", until)
        assert (l01.paused_by.source, l01.paused_by.name, l01.pause_reason) == (
            "dashboard",
            "jason",
            "repotting the wall",
        )
        assert line_of(status, "L02").paused_by is None and line_of(status, "L02").pause_reason is None
        assert line_of(status, "L05").reserved is True and line_of(status, "L06").reserved is True
        assert line_of(status, "L07").reserved is False
        assert (
            line_of(status, "L04").check.checked_by == "jason" and line_of(status, "L04").check.note == "nozzles clear"
        )
        assert line_of(status, "L01").check is None and line_of(status, "L04").manual is True
        assert (line_of(status, "L04").vpd_member, line_of(status, "L04").schedule_member) == (True, False)
        assert line_of(status, "L01").vpd_member is None and line_of(status, "L01").daily_cap_min == 60

        # A pause of every line names its row; a later resume of one line ends it there; no reason stays null.
        await f.pause("all", until, actor="iris", source="iris")
        await f.pause("L09", None, ts=f.at - S)
        status = (await as_both(status_read))[0]
        assert (line_of(status, "L02").paused_by.name, line_of(status, "L02").pause_reason) == ("iris", None)
        assert line_of(status, "L09").paused_by is None

    scenario(database, body)


def test_the_line_reader_returns_only_the_lines_asked_for(database):
    async def body(f):
        await f.reporting()

        async def read(conn):
            return await water.lines(conn, PERSON, ["L04", "L01"])

        for found in await as_both(read):
            assert [line.line_id for line in found] == ["L01", "L04"]

    scenario(database, body)


def test_a_line_that_never_ran_and_the_newest_water_of_one_that_did(database):
    async def body(f):
        await f.reporting()
        # Off rows only: L05 has never run. L01 ran 3 days ago; L04's newest is a VPD episode of 3 pulses, in progress.
        await f.edge("drip_center", f.at - D, False)
        await f.run("drip_wall", f.at - 3 * D, f.at - 3 * D + 10 * MIN)
        await f.edge("drip_wall", f.at - 2 * D, False)
        await f.run("mister_center", f.at - 2 * H, f.at - 2 * H + 30 * S)
        await f.run("mister_center", f.at - 12 * MIN, f.at - 11 * MIN)
        await f.run("mister_center", f.at - 8 * MIN, f.at - 7 * MIN)
        await f.run("mister_center", f.at - 2 * MIN)
        status = (await as_both(status_read))[0]
        assert line_of(status, "L05").last_water is None and line_of(status, "L02").last_water is None
        l01 = line_of(status, "L01").last_water
        assert (l01.at, l01.open_s, l01.pulses, l01.cause) == (
            f.at - 3 * D,
            600,
            None,
            irr.IrrigationCause.UNKNOWN,
        )
        l04 = line_of(status, "L04").last_water
        assert (l04.at, l04.pulses, l04.cause) == (f.at - 12 * MIN, 3, irr.IrrigationCause.VPD)
        assert l04.open_s >= 2 * 60 + 120  # two closed pulses and the one in progress
        assert line_of(status, "L01").last_outcome is None

    scenario(database, body)


def test_last_outcome_is_the_newest_ended_ledger_row_and_unknown_with_no_outcome(database):
    async def body(f):
        await f.reporting()
        slot = await f.slot("L01")
        # One row per local day, by the calendar: two 24-hour steps can land on one day across the 25-hour day.
        today = support.local_day(f.at)
        older, _ = support.day_bounds(today - 2 * D)
        newer, _ = support.day_bounds(today - D)
        await f.ledger("L01", "schedule", older + H, older + 2 * H, slot_id=slot, outcome="short", reason="cap")
        await f.ledger("L01", "schedule", newer + H, newer + 2 * H, slot_id=slot, event_seq=None)
        await f.ledger("L01", "run_now", f.at - MIN, None)  # not ended: it does not count
        await f.ledger("L04", "fallback", None, None, slot_id=1, outcome="skipped", reason="vok", open_s=0)
        status = (await as_both(status_read))[0]
        l01 = line_of(status, "L01").last_outcome
        assert (l01.outcome, l01.at, l01.reason) == (irr.RunOutcome.UNKNOWN, newer + 2 * H, None)
        l04 = line_of(status, "L04").last_outcome
        assert (l04.outcome, l04.reason) == (irr.RunOutcome.SKIPPED, "VPD sensing is healthy")

    scenario(database, body)


def test_the_next_run_needs_the_engine_and_skips_the_ones_that_will_not_run(database):
    async def body(f):
        await f.reporting()
        slot = await f.slot("L01", minute=0, on_s=600, label="wall morning")  # past today unless just after midnight
        await f.set_line("L04", enabled=False)
        await f.engine_on()
        status = (await as_both(status_read))[0]
        next_run = line_of(status, "L01").next_run
        assert (next_run.slot_id, next_run.label, next_run.minutes) == (slot, "wall morning", 10.0)
        assert next_run.start_at > f.at
        assert line_of(status, "L04").next_run is None  # disabled: its fallback slots read skipped
        assert line_of(status, "L02").next_run is None

    scenario(database, body)


# ── Limits, leak and occupancy ──────────────────────────────────────────────


def test_the_leak_as_both_logins_equals_api_14(database):
    async def body(f):
        await f.reporting(flow_gpm=1.0)
        oldest = await f.alert("leak_detected", f.at - 2 * H)
        await f.alert("leak_detected", f.at - 30 * MIN)  # a repeat of the same sensor merges
        await f.alert("leak_detected", f.at - H, sensor_id="other", resolved=True)
        await f.edge("leak_detected", f.at - H, False)
        await f.edge("leak_detected", f.at - 20 * MIN, True)
        await f.delta(f.at - 9 * MIN, f.at - 10 * MIN, 2)
        await f.delta(f.at - 4 * MIN, f.at - 5 * MIN, 1, "manual_or_unattributed")
        await f.delta(f.at - 30 * MIN, f.at - 31 * MIN, 5)  # older than 10 min

        async def read(conn):
            return await water.status(conn, PERSON), await alerts.list(conn)

        for status, page in await as_both(read):
            api_14 = next(item for item in page.items if item.alert_type == "leak_detected")
            assert status.leak.alert_id == api_14.id == oldest
            assert status.leak.gal_last_10min == 3
            assert status.leak.active is True and status.leak.flow_gpm == 1.0
            assert status.leak.since == f.at - 20 * MIN
            assert status.leak.stopped_at is None

    scenario(database, body)


def test_flow_stopped_at_after_two_quiet_readings_while_the_alert_is_open(database):
    async def body(f):
        await f.reporting(flow_gpm=0.0)
        await f.alert("leak_detected", f.at - H)
        await f.edge("leak_detected", f.at - H, True)
        await f.edge("leak_detected", f.at - 10 * MIN, False)
        await f.climate(f.at - 12 * MIN, flow_gpm=1.0)
        await f.climate(f.at - 8 * MIN, flow_gpm=0.0)
        await f.climate(f.at - 7 * MIN, flow_gpm=0.0)
        for status in await as_both(status_read):
            assert status.leak.active is False and status.leak.alert_id is not None
            assert status.leak.stopped_at == f.at - 7 * MIN

    scenario(database, body)


def test_no_leak_alert_reads_null(database):
    async def body(f):
        await f.reporting(flow_gpm=0.0)
        await f.alert("leak_detected", f.at - H, resolved=True)
        await f.alert("leak_detected", f.at - H, sensor_id="s2", disposition="suppressed")
        await f.delta(f.at - MIN, f.at - 2 * MIN, 1)
        for status in await as_both(status_read):
            assert status.leak.alert_id is None and status.leak.stopped_at is None

    scenario(database, body)


def test_limits_and_occupancy(database):
    async def body(f):
        await f.reporting(mister_water_today=41.5)
        await f.conn.execute(
            "INSERT INTO public.setpoint_snapshot (ts, parameter, value) VALUES ($1, 'mister_water_budget_gal', 250)",
            f.at - MIN,
        )
        await f.edge("occupancy", f.at - H, True)
        await f.edge("occupancy", f.at - 50 * MIN, False)
        await f.edge("occupancy_inhibit", f.at - D, True)
        await f.engine_on()
        status = (await as_both(status_read))[0]
        assert (status.limits.saf4_gal.used, status.limits.saf4_gal.max) == (41.5, 360.0)
        assert (status.limits.soft_budget_gal.used, status.limits.soft_budget_gal.max) == (41.5, 250.0)
        assert status.occupancy.occupied is False
        assert status.occupancy.seen_at == f.at - H
        assert status.occupancy.hold_enabled is True

    scenario(database, body)


def test_limits_used_is_null_while_the_controller_is_not_reporting(database):
    async def body(f):
        await f.reporting(climate_age=6 * MIN, mister_water_today=41.5)
        await f.edge("occupancy", f.at - H, True)
        status = (await as_both(status_read))[0]
        assert status.limits.saf4_gal.used is None and status.limits.soft_budget_gal.used is None
        assert status.occupancy.occupied is None
        assert status.leak.gal_last_10min is None

    scenario(database, body)


# ── Today's minutes against API-IRR-10 ──────────────────────────────────────


def test_todays_minutes_equal_api_irr_10s_rows(database):
    """A run from before midnight, a split cause, the master valve, fog and a bridge run, against the reader."""

    async def body(f):
        await f.reporting(water_total_gal=10.0)
        await f.state("irr_engine", "false", f.at - H)
        today = support.local_day(f.at)
        await f.set_line("L07", enabled=True)
        # Already on at midnight: minutes today, no pulse today.
        await f.run("drip_center", f.midnight - 30 * MIN, f.today_at(0.1))
        await f.run("mister_center", f.today_at(0.15), f.today_at(0.2))
        await f.run("mister_center", f.today_at(0.25), f.today_at(0.3))
        slot = await f.slot("L04", degraded_only=False, on_s=45, count=4, gap_s=60, created=f.at - D)
        started, ended = f.today_at(0.35), f.today_at(0.4)
        await f.run("mister_center", started, ended)
        await f.ledger("L04", "schedule", f.today_at(0.32), ended, slot_id=slot, outcome="ok")
        await f.run("drip_wall_fert", f.today_at(0.42), f.today_at(0.44))
        await f.run("fert_master_valve", f.today_at(0.42), f.today_at(0.44))
        await f.run("mister_south", f.today_at(0.5), f.today_at(0.6))
        await f.run("fog", f.today_at(0.55), f.today_at(0.57))
        await f.run("drip_wall", f.today_at(0.7), f.today_at(0.75))
        await f.command(f.today_at(0.7) - 10 * S)
        await f.delta(f.today_at(0.19), f.today_at(0.14), 4)
        await f.delta(f.today_at(0.74), f.today_at(0.71), 2, "ambiguous_overlap")

        async def read(conn):
            return (
                await water.status(conn, PERSON),
                await water.minutes(conn, PERSON, today, today, "zone"),
                await water.minutes(conn, PERSON, today, today, "line"),
            )

        for status, by_zone, by_line in await as_both(read):
            assert status.today.date == today
            assert by_zone.periods[0].coverage == "partial"
            expected: dict = {}
            for row in by_zone.rows:
                expected[row.group] = expected.get(row.group, 0) + row.minutes
            for zone, value in status.today.zones.items():
                if zone == "west":
                    assert value is None  # no enabled line, and no row
                    continue
                assert value == pytest.approx(expected.get(zone, 0), abs=1e-9), zone
            assert "supply" not in status.today.zones and "supply" not in expected
            per_line: dict = {}
            for row in by_line.rows:
                total = per_line.setdefault(row.group, {"minutes": 0.0, "pulses": 0, "gal": None})
                total["minutes"] += row.minutes
                total["pulses"] += row.pulses or 0
                if row.gal is not None:
                    total["gal"] = (total["gal"] or 0) + row.gal
            for line in status.lines:
                total = per_line.get(line.line_id)
                if total is None:
                    assert (line.today.minutes, line.today.pulses) == (0.0, 0), line.line_id
                    continue
                assert line.today.minutes == pytest.approx(total["minutes"], abs=1e-9), line.line_id
                assert line.today.pulses == total["pulses"], line.line_id
                assert line.today.gal == total["gal"], line.line_id
            # Counted from midnight, not from its start: minutes round to 0.1, so just after midnight this is 0.0.
            since_midnight = (f.today_at(0.1) - f.midnight).total_seconds() / 60
            assert line_of(status, "L05").today.pulses == 0
            assert line_of(status, "L05").today.minutes == pytest.approx(since_midnight, abs=0.051)
            assert line_of(status, "L03").today.gal is None
            causes = {(row.group, row.cause) for row in by_line.rows}
            assert ("L04", "schedule") in causes and ("L04", "vpd") in causes
            # #1348: each line's climate part and the site's are API-IRR-10's climate rows, the same split.
            climate: dict = {}
            for row in by_line.rows:
                if row.purpose == "climate":
                    climate[row.group] = climate.get(row.group, 0.0) + row.minutes
            for line in status.lines:
                assert line.today.climate_minutes == pytest.approx(climate.get(line.line_id, 0.0), abs=1e-9), (
                    line.line_id
                )
            assert status.today.climate.minutes == pytest.approx(
                sum(value for group, value in climate.items() if group != "L03"), abs=1e-9
            )
            assert {(row.group, row.purpose) for row in by_line.rows if row.group == "L04"} >= {
                ("L04", "climate"),
                ("L04", "irrigation"),
            }
            assert ("L01", "run_now") in causes

    scenario(database, body)


def test_a_watering_zone_with_no_enabled_line_is_null_never_0(database):
    async def body(f):
        await f.reporting()
        status = (await as_both(status_read))[0]
        zones = status.today.zones
        assert set(zones) == {"wall", "center", "south", "west", "fog"}
        assert zones["south"] is None and zones["west"] is None  # every south and west line seeds disabled
        assert zones["wall"] == 0.0 and zones["center"] == 0.0
        assert zones["fog"] == 0.0  # climate only, so it always reports

        await f.set_line("L01", enabled=False)
        status = (await as_both(status_read))[0]
        assert status.today.zones["wall"] is None
        await f.set_line("L02", enabled=True)
        assert (await as_both(status_read))[0].today.zones["wall"] == 0.0

    scenario(database, body)


def test_a_day_with_no_climate_row_is_a_gap_in_the_status_too(database):
    async def body(f):
        await f.state("ingestor_heartbeat", "alive", f.at - 30 * S)
        await f.run("drip_wall", f.today_at(0.2), f.today_at(0.3))
        status = (await as_both(status_read))[0]
        assert set(status.today.zones.values()) == {None}
        assert line_of(status, "L01").today == water.LineToday(minutes=None, pulses=None, gal=None)

    scenario(database, body)


# ── API-IRR-10 ──────────────────────────────────────────────────────────────


def test_minutes_periods_coverage_and_groups(database):
    async def body(f):
        await f.reporting()
        today = support.local_day(f.at)
        yesterday = today - dt.timedelta(days=1)
        two_ago = today - dt.timedelta(days=2)
        ago_three = today - dt.timedelta(days=3)
        await f.climate(f.at - 4 * D)
        start_two, _ = support.day_bounds(two_ago)
        await f.run("mister_center", start_two + H, start_two + H + 6 * MIN)
        await f.run("fert_master_valve", start_two + 2 * H, start_two + 2 * H + 6 * MIN)
        await f.run("drip_wall_fert", start_two + 2 * H, start_two + 2 * H + 3 * MIN)
        first, second = support.day_bounds(yesterday)
        await f.conn.execute(
            "INSERT INTO public.data_gaps (start_ts, end_ts, duration_s) VALUES ($1, $2, $3)",
            first,
            second,
            (second - first).total_seconds(),
        )

        async def read(conn):
            return {
                group: await water.minutes(conn, PERSON, ago_three, today, group) for group in ("zone", "line", "cause")
            }

        for found in await as_both(read):
            by_zone = found["zone"]
            assert by_zone.to == today and by_zone.from_ == ago_three
            assert {period.start: period.coverage for period in by_zone.periods} == {
                ago_three: "no_data",  # before the first wet-relay row
                two_ago: "complete",
                yesterday: "no_data",  # wholly inside a gap: no rows, never 0
                today: "partial",
            }
            assert {row.period for row in by_zone.rows} == {two_ago}
            assert {(row.group, row.cause, row.chemistry) for row in by_zone.rows} == {
                ("center", "vpd", "clean"),
                ("wall", "feed", "fert"),
            }
            assert next(row for row in by_zone.rows if row.group == "center").minutes == 6.0
            assert {row.group for row in found["line"].rows} == {"L02", "L03", "L04"}
            assert {row.group for row in found["cause"].rows} == {"feed", "vpd"}
            assert as_json(by_zone)["from"] == ago_three.isoformat()

    scenario(database, body)


def test_the_window_limits_refuse(database):
    async def body(f):
        day = dt.date(2026, 1, 1)

        async def read(conn):
            with pytest.raises(ServiceError) as too_many_days:
                await water.minutes(conn, PERSON, day, day + dt.timedelta(days=400), "zone")
            page = await water.minutes(conn, PERSON, day, day + dt.timedelta(days=399), "zone")
            with pytest.raises(ServiceError) as backwards:
                await water.minutes(conn, PERSON, day, day - dt.timedelta(days=1), "zone")
            with pytest.raises(ServiceError) as too_long:
                await water.runs(conn, PERSON, f.at - 31 * D - S, f.at)
            await water.runs(conn, PERSON, f.at - 31 * D, f.at)
            return too_many_days.value.problem, page, backwards.value.problem, too_long.value.problem

        for too_many_days, page, backwards, too_long in await as_both(read):
            assert (too_many_days.code, too_many_days.status, too_many_days.max) == ("window_too_large", 422, 400)
            assert len(page.periods) == 400
            assert (backwards.code, backwards.status) == ("invalid_request", 400)
            assert (too_long.code, too_long.status, too_long.max) == ("window_too_large", 422, 31)

    scenario(database, body)


# ── API-IRR-11 ──────────────────────────────────────────────────────────────


def runs_read(f, **filters):
    async def read(conn):
        return await water.runs(conn, PERSON, f.at - 2 * D, f.at + S, **filters)

    return read


def test_gallons_a_dry_run_reads_0_a_shared_delta_null_and_no_meter_null(database):
    async def body(f):
        await f.reporting(water_total_gal=100.0)
        # A drip run with no delta while the meter reported.
        dry = f.at - 40 * MIN
        await f.run("drip_wall", dry, dry + 5 * MIN)
        await f.climate(dry + MIN, water_total_gal=100.0)
        # One whose delta overlapped another open relay.
        shared = f.at - 30 * MIN
        await f.run("drip_center", shared, shared + 5 * MIN)
        await f.climate(shared + MIN, water_total_gal=100.0)
        await f.delta(shared + 2 * MIN, shared + MIN, 3, "ambiguous_overlap")
        # One that moved water.
        wet = f.at - 20 * MIN
        await f.run("drip_wall_fert", wet, wet + 5 * MIN)
        await f.delta(wet + 2 * MIN, wet + MIN, 2)
        await f.delta(wet + 3 * MIN, wet + 2 * MIN, 1)
        await f.delta(wet + 4 * MIN, wet + 3 * MIN, 9, "manual_or_unattributed")
        # And one in a meter gap: no climate.water_total_gal from its start to 5 min past its end.
        gap = f.at - 15 * H
        await f.run("mister_south_fert", gap, gap + 5 * MIN)
        await f.climate(gap + MIN)  # a climate row with no meter reading
        for page in await as_both(runs_read(f)):
            found = {run.line_id: run for run in page.items}
            assert (found["L01"].gal, found["L05"].gal, found["L02"].gal, found["L08"].gal) == (0, None, 3, None)
            dumped = as_json(found["L01"])
            assert not {key for key in dumped if "rate" in key or "flow" in key or "gpm" in key}
            assert openapi_errors("Run", dumped) == []

    scenario(database, body)


def test_a_ledger_row_with_no_outcome_reads_unknown_and_a_run_with_none_reads_ok(database):
    async def body(f):
        await f.reporting(water_total_gal=1.0)
        await f.state("irr_engine", "true", f.at - 3 * D)
        await f.state("irr_program_id", "h=0000000000000000 src=rom", f.at - 3 * D)
        started = f.at - 2 * H
        slot = await f.slot("L01", created=f.at - 3 * D, minute=_local_minute(started))
        day = support.local_day(started)
        ledger_id = await f.ledger(
            "L01", "schedule", started - S, started + 10 * MIN, slot_id=slot, open_s=600, pulses=1, local_day=day
        )
        await f.run("drip_wall", started, started + 10 * MIN)
        await f.run("mister_center", f.at - H, f.at - H + MIN)  # a VPD episode: no ledger row

        async def read(conn):
            return await water.runs(conn, PERSON, f.at - 2 * D, f.at), await water.agenda(
                conn, PERSON, days=1, past_days=1
            )

        for page, agenda in await as_both(read):
            found = {run.line_id: run for run in page.items}
            assert (found["L01"].outcome, found["L01"].cause) == (irr.RunOutcome.UNKNOWN, irr.IrrigationCause.SCHEDULE)
            assert (found["L01"].run_id, found["L01"].slot_id) == (f"ledger-{ledger_id}", slot)
            assert found["L01"].schedule_label == "fixture"
            assert (found["L04"].outcome, found["L04"].cause) == (irr.RunOutcome.OK, irr.IrrigationCause.VPD)
            assert found["L04"].actor is None and found["L04"].pulses == 1
            due = next(
                item
                for item in agenda.occurrences
                if item.slot_id == slot and item.planned_start.astimezone(support.DENVER).date() == day
            )
            assert (due.status, due.outcome, due.run_id) == ("done", irr.RunOutcome.UNKNOWN, f"ledger-{ledger_id}")

    scenario(database, body)


def _local_minute(moment: dt.datetime) -> int:
    local = moment.astimezone(support.DENVER)
    return local.hour * 60 + local.minute


def test_ledger_rows_that_opened_no_valve_list_with_open_s_0(database):
    async def body(f):
        await f.reporting()
        # Yesterday's: this row lists at its slot's start, which today can still be ahead, and the engine writes it then.
        await f.ledger(
            "L04",
            "fallback",
            None,
            None,
            slot_id=1,
            outcome="skipped",
            reason="vok",
            open_s=0,
            pulses=0,
            local_day=support.local_day(f.at) - D,
        )
        await f.ledger("L01", "run_now", None, None, outcome="skipped", reason="man", open_s=0)
        for page in await as_both(runs_read(f)):
            found = {run.line_id: run for run in page.items}
            assert (found["L04"].open_s, found["L04"].gal, found["L04"].outcome) == (0, 0, irr.RunOutcome.SKIPPED)
            assert found["L04"].reason == "VPD sensing is healthy"
            assert (found["L01"].open_s, found["L01"].reason) == (0, "waiting for the manifold")

    scenario(database, body)


def test_runs_are_ended_runs_stages_and_episodes_with_filters_and_a_keyset(database):
    async def body(f):
        await f.reporting(water_total_gal=1.0)
        # A mist episode of two VPD pulses (6 min apart), a separate one past 10 min, and one still on.
        first = f.at - 3 * H
        await f.run("mister_center", first, first + MIN)
        await f.run("mister_center", first + 7 * MIN, first + 8 * MIN)
        await f.run("mister_center", first + 30 * MIN, first + 31 * MIN)
        await f.run("mister_west", f.at - MIN)
        # A feed: prewet on L01, the feed on L02 with L03 open, a flush on L01; each stage its own run.
        feed = f.at - 2 * H
        slot = await f.slot("L02", created=f.at - D)
        await f.run("drip_wall", feed, feed + MIN)
        await f.ledger("L01", "prewet", feed - S, feed + MIN, slot_id=slot, outcome="ok")
        await f.run("drip_wall_fert", feed + MIN, feed + 3 * MIN)
        await f.run("fert_master_valve", feed + MIN, feed + 3 * MIN)
        await f.ledger("L02", "feed", feed + MIN - S, feed + 3 * MIN, slot_id=slot, outcome="ok")
        await f.run("drip_wall", feed + 3 * MIN, feed + 4 * MIN)
        await f.ledger("L01", "flush", feed + 3 * MIN - S, feed + 4 * MIN, slot_id=slot, outcome="ok")
        # A bridge run.
        bridged = f.at - H
        command = await f.command(bridged - 10 * S, actor="jason")
        await f.run("drip_wall", bridged, bridged + 5 * MIN)

        page = (await as_both(runs_read(f)))[0]
        ordered = [(run.line_id, run.cause.value, run.started_at) for run in page.items]
        assert ordered == [
            ("L01", "run_now", bridged),
            ("L01", "flush", feed + 3 * MIN),
            ("L02", "feed", feed + MIN),
            ("L01", "prewet", feed),
            ("L04", "vpd", first + 30 * MIN),
            ("L04", "vpd", first),
        ]
        assert page.next_cursor is None
        episode = page.items[-1]
        assert (episode.pulses, episode.open_s, episode.ended_at) == (2, 120, first + 8 * MIN)
        assert page.items[0].command_id == str(command)
        assert (page.items[0].actor.source, page.items[0].actor.name) == ("dashboard", "jason")
        assert (page.items[2].zone, page.items[2].chemistry) == ("wall", "fert")
        assert page.items[0].chemistry == "clean"

        assert [run.line_id for run in (await as_both(runs_read(f, line="L02")))[1].items] == ["L02"]
        assert [run.line_id for run in (await as_both(runs_read(f, line="L03")))[1].items] == []
        assert {run.cause.value for run in (await as_both(runs_read(f, cause="vpd")))[1].items} == {"vpd"}
        assert (await as_both(runs_read(f, outcome="short")))[1].items == []

        seen = []
        cursor = None
        for _ in range(6):
            part = (await as_both(runs_read(f, limit=2, cursor=cursor)))[1]
            seen += [run.run_id for run in part.items]
            cursor = part.next_cursor
            if cursor is None:
                break
        assert seen == [run.run_id for run in page.items]

    scenario(database, body)


def test_a_run_that_ends_past_the_window_is_read_to_its_closing_edge_and_counts_once(database):
    async def body(f):
        await f.reporting()
        start = f.at - 3 * H
        await f.edge("drip_wall", start - H, False)
        await f.run("drip_wall", start, start + 30 * MIN)
        cut = start + 10 * MIN

        async def read(conn):
            return (
                await water.runs(conn, PERSON, f.at - 2 * D, cut),
                await water.runs(conn, PERSON, cut, f.at),
                await water.run_causes(conn, cut, f.at),
            )

        for before, after, causes in await as_both(read):
            assert [(run.started_at, run.ended_at) for run in before.items] == [(start, start + 30 * MIN)]
            assert after.items == []  # it started in the window before
            assert [(cause.line_id, cause.start, cause.end) for cause in causes] == [("L01", start, start + 30 * MIN)]

    scenario(database, body)


def test_run_causes_is_the_same_join(database):
    async def body(f):
        await f.reporting()
        # An L01 run 10 s after a sent bridge command is run_now; 31 s after one it is not.
        bridged = f.at - 2 * H
        await f.command(bridged - 10 * S)
        await f.run("drip_wall", bridged, bridged + 5 * MIN)
        late = f.at - H
        await f.command(late - 31 * S)
        await f.run("drip_wall", late, late + 5 * MIN)
        await f.run("mister_center", f.at - 30 * MIN, f.at - 29 * MIN)
        await f.run("drip_center", f.at - 25 * MIN)
        await f.run("fert_master_valve", f.at - 20 * MIN, f.at - 19 * MIN)
        scheduled = f.at - 15 * MIN
        await f.run("mister_south", scheduled, scheduled + MIN)
        await f.ledger("L07", "schedule", scheduled - S, scheduled + MIN, slot_id=5, outcome="ok")

        async def read(conn):
            return await water.run_causes(conn, f.at - 3 * H, f.at)

        for causes in await as_both(read):
            assert sorted((cause.line_id, cause.start, cause.cause) for cause in causes) == sorted(
                [
                    ("L01", bridged, "run_now"),
                    ("L01", late, "unknown"),
                    ("L04", f.at - 30 * MIN, "vpd"),
                    ("L05", f.at - 25 * MIN, "unknown"),
                    ("L03", f.at - 20 * MIN, "feed"),
                    ("L07", scheduled, "schedule"),
                ]
            )

    scenario(database, body)


def test_the_status_causes_follow_the_minutes_for_a_run_already_on_at_midnight(database):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        start = f.midnight - H
        await f.command(start - 5 * S)
        await f.edge("drip_wall", start, True)
        await f.edge("drip_wall", f.midnight - 30 * MIN, True)  # a reconnect snapshot, not a new run
        today = support.local_day(f.at)

        async def read(conn):
            return await water.status(conn, PERSON), await water.minutes(conn, PERSON, today, today, "line")

        for status, minutes in await as_both(read):
            line = line_of(status, "L01")
            assert (line.state.cause, line.state.observed_at) == (irr.IrrigationCause.RUN_NOW, start)
            assert status.active_runs[0].started_at == start
            assert [(row.group, row.cause) for row in minutes.rows] == [("L01", "run_now")]
            assert line.today.pulses == 0  # it counts on the day it started

    scenario(database, body)


# ── API-IRR-02 ──────────────────────────────────────────────────────────────


def agenda_read(**kwargs):
    async def read(conn):
        return await water.agenda(conn, PERSON, **kwargs)

    return read


def test_schedules_carry_their_sync_and_the_committed_shape(database):
    async def body(f):
        await f.reporting()
        await f.engine_on()
        recent = await f.slot("L01", updated=f.at - MIN, created=f.at - MIN, label=None)
        stale = await f.slot("L09", updated=f.at - 6 * MIN, created=f.at - 6 * MIN, minute=30)  # after the push
        found = (await as_both(agenda_read(days=1)))[0]
        by_id = {schedule.slot_id: schedule for schedule in found.schedules}
        # Each new row changes the record, so the applied hash no longer matches the desired one.
        assert (by_id[recent].sync, by_id[stale].sync, by_id[1].sync) == ("requested", "not_confirmed", "confirmed")
        assert "label" not in as_json(by_id[recent])
        assert as_json(by_id[stale])["label"] == "fixture"
        assert as_json(by_id[1])["start"] == {"kind": "solar_noon", "offset_min": -120}
        assert as_json(by_id[stale])["start"] == {"kind": "clock", "clock": "00:30"}
        assert as_json(by_id[stale])["days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        assert (by_id[stale].updated_by.source, by_id[stale].updated_by.name) == ("dashboard", "jason")
        assert openapi_errors("ScheduleAgenda", as_json(found)) == []

    scenario(database, body)


def test_past_occurrences_read_done_and_a_new_schedule_shows_no_invented_history(database):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "true", f.at - 10 * D)
        await f.state("irr_program_id", "h=0000000000000000 src=rom", f.at - 10 * D)
        await f.state("irr_state", "", f.at - 10 * D)
        await f.set_line("L01", enabled=True)
        today = support.local_day(f.at)
        ran_day = today - 2 * D  # by the calendar: 24-hour steps can name one local day twice across the 25-hour day
        two_days_back = support.day_bounds(ran_day)[0] + H
        slot = await f.slot("L01", created=f.at - 3 * D, minute=_local_minute(f.at - H))
        ran = await f.ledger(
            "L01",
            "schedule",
            two_days_back,
            two_days_back + 10 * MIN,
            slot_id=slot,
            local_day=ran_day,
            outcome="short",
            reason="cap",
        )
        newer = await f.slot("L05", created=f.at - 10 * MIN, minute=_local_minute(f.at - H))
        await f.set_line("L05", enabled=True)
        found = (await as_both(agenda_read(days=1, past_days=3)))[1]
        mine = [item for item in found.occurrences if item.slot_id == slot]
        assert len(mine) >= 3
        done = {item.planned_start.astimezone(support.DENVER).date(): item for item in mine}
        assert (done[ran_day].status, done[ran_day].outcome, done[ran_day].run_id, done[ran_day].reason) == (
            "done",
            irr.RunOutcome.SHORT,
            f"ledger-{ran}",
            "cap reached",
        )
        no_row = done[today - D]
        assert (no_row.status, no_row.outcome, no_row.run_id, no_row.reason) == (
            "done",
            irr.RunOutcome.UNKNOWN,
            None,
            "no outcome received",
        )
        # Its newest start, an hour ago, came before the schedule was created, and so did each earlier day's.
        assert [item for item in found.occurrences if item.slot_id == newer and item.planned_start <= f.at] == []
        assert {item.status for item in found.occurrences if item.slot_id == newer} <= {"planned"}

    scenario(database, body)


def test_a_run_token_marks_the_occurrence_in_progress(database):
    async def body(f):
        await f.reporting()
        slot = await f.slot("L01", created=f.at - 3 * D)
        epoch = int(f.at.timestamp())
        await f.state("irr_engine", "true", f.at - 3 * D)
        await f.engine_on(irr_state=f"d=0,schedule,{slot},{epoch - 60},{epoch + 500}")
        found = (await as_both(agenda_read(days=1, past_days=1)))[0]
        mine = [item for item in found.occurrences if item.slot_id == slot]
        assert [item.status for item in mine if item.planned_start <= f.at][-1] == "running"
        assert [item.status for item in mine if item.planned_start <= f.at][:-1] == ["done"]

    scenario(database, body)


def test_an_occurrence_skipped_for_a_pause_says_who_and_why(database):
    async def body(f):
        await f.reporting()
        await f.engine_on()
        slot = await f.slot("L01", minute=1439)
        until = f.at + 3 * D
        await f.pause("L01", until, reason="rain", actor="jason", ts=f.at - H)
        found = (await as_both(agenda_read(days=2)))[1]
        paused = [item for item in found.occurrences if item.slot_id == slot]
        assert paused and {item.status for item in paused} == {"skipped"}
        assert {item.reason for item in paused} == {"line paused by jason: rain"}

        await f.conn.execute("UPDATE public.activity_log SET reason = NULL")
        found = (await as_both(agenda_read(days=2)))[0]
        assert {item.reason for item in found.occurrences if item.slot_id == slot} == {"line paused by jason"}

        # A start before the pause was set was not skipped for it: today's paused_until says nothing about it.
        await f.conn.execute("UPDATE public.activity_log SET ts = $1", f.at + 4 * D)
        found = (await as_both(agenda_read(days=1, past_days=1)))[0]
        earlier = [item for item in found.occurrences if item.slot_id == slot and item.planned_start <= f.at]
        assert earlier and all(item.reason != "line paused by jason" for item in earlier)

    scenario(database, body)


def test_a_past_occurrence_reads_blocked_only_while_no_engine_was_in_effect_at_its_start(database):
    async def body(f):
        await f.reporting()
        # The engine arrived 3 h ago, so a start an hour ago had it and the same schedule's start a day earlier did not.
        await f.state("irr_engine", "false", f.at - 3 * D)
        await f.state("irr_engine", "true", f.at - 3 * H)
        await f.state("irr_program_id", "h=0000000000000000 src=rom", f.at - 3 * H)
        minute = _local_minute(f.at - H)
        slot = await f.slot("L01", created=f.at - 5 * D, minute=minute)
        found = (await as_both(agenda_read(days=1, past_days=2)))[0]
        statuses = {
            item.planned_start.astimezone(support.DENVER).date(): item.status
            for item in found.occurrences
            if item.slot_id == slot and item.planned_start <= f.at
        }
        # Today and yesterday, except in a local day's first hour, when the start an hour ago is yesterday's.
        an_hour_ago = support.local_day(f.at - H)
        assert statuses[an_hour_ago] == "done"
        assert statuses[an_hour_ago - D] == "blocked"

    scenario(database, body)


# ── settings.status and reads.now on M3's tables ────────────────────────────


def test_the_labels_map_equals_the_seeded_lines(database):
    assert {row["equipment"]: row["line_id"] for row in database["lines"]} == dict(EQUIPMENT_LINE_IDS)
    assert {row["line_id"]: row["kind"] for row in database["lines"]} == dict(irr.LINE_KIND)
    assert run_rules.LINE_RELAY == {row["line_id"]: row["equipment"] for row in database["lines"]}


def test_site_values_no_match_since_the_first_push_compiled_after_the_write(database):
    async def body(f):
        await f.reporting()
        applied = await f.compiled()  # 90, the registry default
        write = f.at - 20 * MIN
        await f.conn.execute(
            "INSERT INTO public.desired_state (param, value, manual, source, actor, created_at) "
            "VALUES ('sched_mist_rh_max_pct', 85, true, 'dashboard', 'jason', $1)",
            write,
        )
        await f.state("irr_engine", "true", f.at - H)
        await f.state("irr_program_id", f"h={applied.hash16} src=nvs", f.at - H)
        await f.push(
            applied,
            revision=40,
            site_desired_at=f.at - 2 * H,
            pushed_at=f.at - 2 * H,
            confirmed_at=f.at - 2 * H,
        )
        await f.push(await f.compiled(), revision=41, site_desired_at=write, pushed_at=f.at - 19 * MIN)

        async def read(conn):
            return await settings.status(conn, "sched_mist_rh_max_pct")

        for site in await as_both(read):
            assert site.state == "not_confirmed"
            assert (site.state_reason.code, site.state_reason.since) == ("no_match", f.at - 19 * MIN)

    scenario(database, body)


# ── Timing, on M4's production-sized generator ──────────────────────────────


def p95(samples):
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_production_sized_status_and_31_day_runs_answer_under_two_seconds_at_p95(database):
    """Two years of wet-relay edges and meter deltas at the April-July 2026 density, with the ledger and bridge
    commands at program density. In one rolled-back transaction as the owner, since both reads run the same
    statements as either login."""

    async def go():
        conn = await connect()
        try:
            fixture = Fixture(conn, database)
            await fixture.reset()
            tr = conn.transaction()
            await tr.start()
            try:
                now = await conn.fetchval("SELECT now()")
                edges, events, gaps = support.production_sized_rows(now)
                ledger, commands = support.production_sized_ledger(edges)
                await support.load_production_sized(conn, edges, events, gaps)
                await support.load_production_sized_ledger(conn, ledger, commands)
                fixture.at = now
                await fixture.state("ingestor_heartbeat", "alive", now - 30 * S)
                await fixture.climate(now - 30 * S, water_total_gal=10.0)
                await conn.execute("ANALYZE public.system_state; ANALYZE public.climate")
                status_times, runs_times = [], []
                status_found = runs_found = None
                for _ in range(20):
                    started = time.perf_counter()
                    status_found = await water.status(conn, PERSON)
                    status_times.append(time.perf_counter() - started)
                    started = time.perf_counter()
                    runs_found = await water.runs(conn, PERSON, now - 31 * D, now, limit=200)
                    runs_times.append(time.perf_counter() - started)
                return len(edges), status_found, runs_found, status_times, runs_times
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    count, status, page, status_times, runs_times = run(go())
    assert count > 200_000
    assert len(status.lines) == 11 and page.items
    assert line_of(status, "L04").last_water is not None
    print(f"status p95 {p95(status_times):.3f}s; 31-day runs p95 {p95(runs_times):.3f}s over {count} edges")
    assert p95(status_times) < 2.0
    assert p95(runs_times) < 2.0
