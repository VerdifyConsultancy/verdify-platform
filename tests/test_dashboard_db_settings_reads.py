"""VD-55 on a private PostgreSQL: the settings, history, alerts and activity reads' acceptance fixtures.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. It builds, in that fresh database's public schema (the
activity service names public.activity_log), db/schema.sql's own DDL for every table, view and function these
reads touch, migration 280's desired_state, v_desired_state_current and activity_log, and migration 259's
fn_mcp_armed_assignment_context() over stand-ins for the two experiment tables db/schema.sql predates.

API-09, API-12, API-14 and API-17 (settings_rows, setting_history, alerts.list and activity_page) run as a role
holding only the grants the MCP login has in production on the relations they read (migrations 259, 261 and
280, checked here), because the MCP settings, activity and alerts tools call the same functions (#1171). API-02
and API-07 run as a role that also reads the Now-only relations (a subset of M2b's dashboard grants). Fixture
rows are written as the database owner, and through verdify_services where a write has a service. Every age
is measured from the server's now(), so each fixture is placed relative to it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml

from verdify_schemas.setting_help import band_in_force_sources, setting_label
from verdify_schemas.tunable_registry import REGISTRY
from verdify_services import alerts, reads, settings
from verdify_services.actor import Actor

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
MCP_ROLE = "vd55_mcp_reads"
DASHBOARD_ROLE = "vd55_dashboard_reads"
MIGRATION_280 = "db/migrations/280-desired-state-activity-log.sql"
MIGRATION_259 = "db/migrations/259-mcp-ordinary-runtime-boundary.sql"
MIGRATION_261 = "db/migrations/261-outcome-kpi-ordinary-acl.sql"
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
    ("CREATE TABLE public.slack_alert_runbooks (", ");\n"),
    ("CREATE SEQUENCE public.slack_alert_runbooks_id_seq", ";\n"),
    ("ALTER SEQUENCE public.slack_alert_runbooks_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.slack_alert_runbooks ALTER COLUMN id SET DEFAULT", ";\n"),
    ("CREATE TABLE public.crop_band_anchors (", ");\n"),
    ("CREATE SEQUENCE public.crop_band_anchors_id_seq", ";\n"),
    ("ALTER SEQUENCE public.crop_band_anchors_id_seq OWNED BY", ";\n"),
    ("ALTER TABLE ONLY public.crop_band_anchors ALTER COLUMN id SET DEFAULT", ";\n"),
    ("CREATE FUNCTION public.fn_current_season()", "$$;\n"),
    ("CREATE VIEW public.v_active_plan AS", ";\n"),
    ("CREATE VIEW public.v_open_alerts AS", ";\n"),
)
MIGRATION_DDL = (
    (MIGRATION_280, "CREATE TABLE public.desired_state (", ");\n"),
    (MIGRATION_280, "CREATE INDEX desired_state_latest", ";\n"),
    (MIGRATION_280, "CREATE VIEW public.v_desired_state_current AS", ";\n"),
    (MIGRATION_280, "CREATE TABLE public.activity_log (", ");\n"),
    (MIGRATION_280, "CREATE INDEX activity_log_ts", ";\n"),
    (MIGRATION_259, "CREATE FUNCTION public.fn_mcp_armed_assignment_context()", "$body$;\n"),
)
# Only the columns fn_mcp_armed_assignment_context() reads (migration 207 made the real ones).
EXPERIMENT_STAND_INS = """
CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, greenhouse_id text NOT NULL DEFAULT 'vallery',
    status text NOT NULL, armed_at timestamptz);
CREATE TABLE public.control_assignments (
    assignment_id uuid PRIMARY KEY, experiment_id uuid NOT NULL, status text NOT NULL, valid_range tstzrange NOT NULL);
"""
# What API-09, API-12, API-14 and API-17 read; each must be the MCP login's production grant.
MCP_READS = (
    "activity_log",
    "alert_log",
    "climate",
    "crop_band_anchors",
    "desired_state",
    "equipment_state",
    "plan_journal",
    "setpoint_changes",
    "setpoint_clamps",
    "setpoint_plan",
    "setpoint_snapshot",
    "slack_alert_runbooks",
    "system_state",
    "v_active_plan",
    "v_desired_state_current",
)
MCP_FUNCTIONS = ("public.fn_mcp_armed_assignment_context()", "public.fn_current_season()")
# API-02 and API-07 also read these (dashboard only: the reads have no MCP twin).
DASHBOARD_ONLY_READS = ("diagnostics", "planner_trigger_ledger", "water_meter_events")
TABLES = (
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
    "alert_log",
    "slack_alert_runbooks",
    "crop_band_anchors",
    "desired_state",
    "activity_log",
    "control_experiments",
    "control_assignments",
)
GRANT = re.compile(
    r"GRANT\s+([A-Z][A-Z, ]*?)\s+ON\s+(?:TABLE\s+)?(?!FUNCTION|SEQUENCE|SCHEMA)(.+?)\s+TO\s+verdify_mcp_runtime\s*;",
    re.S,
)
EXECUTE = re.compile(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+(.+?)\s+TO\s+verdify_mcp_runtime\s*;", re.S)

S = timedelta(seconds=1)
MIN = timedelta(minutes=1)
H = timedelta(hours=1)
D = timedelta(days=1)
STEWARD = Actor.agent("steward")
OTHER_AGENT = Actor.agent("pathfinder")
PERSON = Actor.person("jason", "operator")


def extract(text: str, start: str, end: str) -> str:
    assert text.count(start) == 1, start
    begin = text.index(start)
    return text[begin : text.index(end, begin) + len(end)]


def ddl() -> list[str]:
    schema = (ROOT / "db/schema.sql").read_text()
    statements = [extract(schema, start, end) for start, end in SCHEMA_DDL]
    statements.append(EXPERIMENT_STAND_INS)
    statements.extend(extract((ROOT / path).read_text(), start, end) for path, start, end in MIGRATION_DDL)
    return statements


def mcp_grants() -> tuple[dict[str, set[str]], str]:
    """Relation → privileges, and the EXECUTE grant text, that migrations 259, 261 and 280 give the MCP duty."""
    granted: dict[str, set[str]] = {}
    executes = []
    for path in (MIGRATION_259, MIGRATION_261, MIGRATION_280):
        text = (ROOT / path).read_text()
        for match in GRANT.finditer(text):
            privileges = {privilege.strip() for privilege in match.group(1).split(",")}
            for relation in match.group(2).split(","):
                granted.setdefault(relation.strip().removeprefix("public."), set()).update(privileges)
        executes.extend(match.group(1) for match in EXECUTE.finditer(text))
    return granted, " ".join(executes)


async def install(conn) -> None:
    if await conn.fetchval("SELECT to_regclass('public.desired_state') IS NOT NULL"):
        pytest.fail("test_dashboard_db_settings_reads needs a fresh database: public.desired_state already exists")
    for statement in ddl():
        await conn.execute(statement)
    relations = ", ".join(f"public.{name}" for name in MCP_READS)
    dashboard = ", ".join(f"public.{name}" for name in (*MCP_READS, *DASHBOARD_ONLY_READS))
    functions = ", ".join(MCP_FUNCTIONS)
    await conn.execute(
        f"""
        DO $$ BEGIN CREATE ROLE {MCP_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        DO $$ BEGIN CREATE ROLE {DASHBOARD_ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        REVOKE ALL ON FUNCTION {functions} FROM PUBLIC;
        GRANT USAGE ON SCHEMA public TO {MCP_ROLE}, {DASHBOARD_ROLE};
        GRANT SELECT ON {relations} TO {MCP_ROLE};
        GRANT EXECUTE ON FUNCTION {functions} TO {MCP_ROLE};
        GRANT SELECT ON {dashboard} TO {DASHBOARD_ROLE};
        GRANT EXECUTE ON FUNCTION {functions} TO {DASHBOARD_ROLE};
        """
    )


async def remove(conn) -> None:
    for role in (MCP_ROLE, DASHBOARD_ROLE):
        await conn.execute(
            f"DO $$ BEGIN DROP OWNED BY {role}; DROP ROLE {role}; EXCEPTION WHEN undefined_object THEN NULL; END $$"
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
            await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
            await body(conn, await conn.fetchval("SELECT now()"))
        finally:
            await conn.close()

    asyncio.run(run())


async def as_role(conn, role, read, *args, **kwargs):
    """A read as ``role``, which holds only its grants."""
    await conn.execute(f"SET ROLE {role}")
    try:
        return await read(conn, *args, **kwargs)
    finally:
        await conn.execute("RESET ROLE")


def openapi_errors(schema: dict, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator({**schema, "components": OPENAPI["components"]})
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


def response_schema(path: str) -> dict:
    return OPENAPI["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


# ── fixture rows ────────────────────────────────────────────────────────────


async def reporting_now(conn, t0):
    await conn.execute("INSERT INTO system_state (ts, entity, value) VALUES ($1, 'ingestor_heartbeat', 'alive')", t0)
    await conn.execute("INSERT INTO climate (ts, temp_avg, vpd_avg) VALUES ($1, 79.0, 1.0)", t0 - 20 * S)


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


async def full_plan(conn, plan_id, created, *, status, rows, active, journal_after=timedelta(0)):
    """A set_plan plan: its plan_journal row and its waypoints (set_plan deactivates the older plans' rows)."""
    await conn.execute(
        "INSERT INTO plan_journal (plan_id, created_at, valid_from, expires_at, lifecycle_status) "
        "VALUES ($1, $2, $2, $3, $4)",
        plan_id,
        created + journal_after,
        created + 3 * D,
        status,
    )
    await waypoints(conn, plan_id, created, rows, active=active)


async def waypoints(conn, plan_id, created, rows, *, source="iris", active=True):
    await conn.executemany(
        "INSERT INTO setpoint_plan (ts, parameter, value, plan_id, source, created_at, is_active, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        [(ts, parameter, value, plan_id, source, created, active, created + 3 * D) for ts, parameter, value in rows],
    )


async def alert(conn, ts, alert_type, sensor_id, *, severity="warning", disposition="open", details=None):
    return await conn.fetchval(
        "INSERT INTO alert_log (ts, alert_type, severity, category, sensor_id, message, disposition, details, "
        "acknowledged_at) VALUES ($1, $2, $3, 'sensor', $4, $5, $6, $7::jsonb, $8) RETURNING id",
        ts,
        alert_type,
        severity,
        sensor_id,
        f"{alert_type} {sensor_id}",
        disposition,
        json.dumps(details) if details is not None else None,
        ts if disposition == "acknowledged" else None,
    )


# ── the grants the MCP-shared reads run with ────────────────────────────────


def test_the_mcp_shared_reads_need_only_the_mcp_logins_production_grants():
    granted, executes = mcp_grants()
    missing = [name for name in MCP_READS if "SELECT" not in granted.get(name, set())]
    assert missing == []
    assert all(function in executes for function in MCP_FUNCTIONS)


# ── API-09: every registry row, then the Band in force ──────────────────────


def test_api09_lists_every_registry_row_with_its_label_and_unit_and_the_band_in_force(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        newest = {}
        for index, (zone, role) in enumerate((zone, role) for zone in reads.BAND_ZONES for role in reads.BAND_ROLES):
            value = round((80.0 if role.startswith("temp") else 1.0) + index / 100, 3)
            await snapshot(conn, t0 - 70 * MIN, f"band_{zone}_{role}", value - 5, zone, role)  # outside the hour
            if (zone, role) != ("east", "vpd_target"):  # one role the dispatcher did not write this hour
                await snapshot(conn, t0 - 3 * MIN, f"band_{zone}_{role}", value, zone, role)
                newest[(zone, role)] = value
        await snapshot(conn, t0 - 1 * MIN, "temp_low", 40.0)  # the legacy wide limit is a registry row

        result = await as_role(conn, MCP_ROLE, reads.settings_rows)
        assert openapi_errors(response_schema("/v1/settings"), result.model_dump(mode="json")) == []
        registry = [item for item in result.items if item.group != "band_in_force"]
        # Every REGISTRY row, in order, by control_class: a missing row fails here.
        assert [(item.param, item.control_class) for item in registry] == [
            (name, spec.control_class) for name, spec in REGISTRY.items()
        ]
        for item in registry:
            spec = REGISTRY[item.param]
            # The served label is the help label where the registry label is the raw param name.
            assert (item.label, item.unit) == (setting_label(item.param), spec.unit), item.param
            assert item.help is not None, item.param
        rows = {item.param: item for item in result.items}
        assert (rows["mister_engage_kpa"].label, rows["mister_engage_kpa"].unit) == ("VPD Mister Engage", "kPa")
        legacy = rows["temp_low"]
        assert (legacy.group, legacy.writable, legacy.readback.value) == ("read_only", False, 40.0)

        band = [item for item in result.items if item.group == "band_in_force"]
        assert [item.param for item in band] == [
            f"band_{zone}_{role}" for zone in ("house", "center", "south", "west", "east") for role in reads.BAND_ROLES
        ]
        for item in band:
            zone, role = item.param.removeprefix("band_").split("_", 1)
            assert (item.control_class, item.kind, item.writable, item.read_only_reason) == (
                "crop_band",
                "numeric",
                False,
                "derived by the dispatcher from the crop band anchors",
            )
            assert (item.read_only_kind, item.source_params) == (
                "band_in_force",
                list(band_in_force_sources(zone, role)),
            )
            assert item.unit == ("°F" if role.startswith("temp") else "kPa")
            assert (item.readback_via, item.desired, item.plan, item.applied_from, item.state) == (
                "none",
                None,
                None,
                "band",
                "no_readback",
            )
            if (zone, role) in newest:
                assert (item.readback.value, item.readback.observed_at) == (newest[(zone, role)], t0 - 3 * MIN)
        assert (rows["band_house_temp_low"].label, rows["band_west_vpd_high"].label) == (
            "House temp low",
            "West VPD high",
        )
        missing = rows["band_east_vpd_target"].readback
        assert (missing.value, missing.observed_at, missing.reason) == (None, None, reads.NO_BAND_ROW)
        assert result.server_time >= t0

    scenario(dsn, body)


def test_iris_48_75_shows_as_stored_and_confirms_from_the_readback(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        written = t0 - 2 * H
        await full_plan(
            conn,
            "iris-vd55-48",
            written,
            status="effective",
            rows=[(written, "mister_engage_delay_s", 48.75), (t0 + 3 * H, "mister_engage_delay_s", 60.0)],
            active=True,
        )
        await waypoints(
            conn,
            "preemptive-vd55",
            t0 - 10 * MIN,
            [(t0 - 10 * MIN, "sw_economiser_enabled", 1.0)],
            source="preemptive",
        )
        await snapshot(conn, t0 - 1 * MIN, "mister_engage_delay_s", 48.75)

        rows = {item.param: item for item in (await as_role(conn, MCP_ROLE, reads.settings_rows)).items}
        delay = rows["mister_engage_delay_s"]
        # The plan-only case: no desired row, so applied_from is plan (resolve_effective returns None).
        assert (delay.desired, delay.applied_from, delay.state) == (None, "plan", "confirmed")
        assert delay.plan.model_dump() == {
            "value": 48.75,  # as stored, never snapped to the 30 s step
            "plan_id": "iris-vd55-48",
            "source": "iris",
            "written_at": written,
            "next": {"at": t0 + 3 * H, "value": 60.0, "source": "iris", "effective": True},
        }
        forecast = rows["sw_economiser_enabled"].plan
        assert (forecast.source, forecast.plan_id, forecast.value, forecast.next) == (
            "preemptive",
            "preemptive-vd55",
            1.0,
            None,
        )
        assert rows["mister_engage_kpa"].plan is None

        await snapshot(conn, t0 - 20 * S, "mister_engage_delay_s", 42.0)  # the device reports something else
        rows = {item.param: item for item in (await as_role(conn, MCP_ROLE, reads.settings_rows)).items}
        assert (rows["mister_engage_delay_s"].state, rows["mister_engage_delay_s"].plan.value) == (
            "not_confirmed",
            48.75,
        )

    scenario(dsn, body)


# ── API-14 and API-02: merged repeats and the one count ─────────────────────


def test_thirty_soil_sensor_offline_repeats_return_one_row_counted_thirty(dsn):
    async def body(conn, t0):
        ids = [
            await alert(conn, t0 - (40 - minute) * MIN, "soil_sensor_offline", "soil.west_1") for minute in range(30)
        ]
        await alert(
            conn,
            t0 - 3 * D,
            "soil_sensor_offline",
            "soil.south_1",
            details={"count": 30, "last_seen_at": (t0 - 5 * MIN).isoformat()},
        )

        page = await as_role(conn, MCP_ROLE, alerts.list, "open", 50, None)
        assert openapi_errors(response_schema("/v1/alerts"), page.model_dump(mode="json")) == []
        assert len(page.items) == 2 and page.next_cursor is None
        rows = sorted(page.items, key=lambda item: item.first_seen_at)
        south, west = rows
        assert (west.id, west.count, west.first_seen_at, west.last_seen_at) == (
            ids[0],
            30,
            t0 - 40 * MIN,
            t0 - 11 * MIN,
        )
        assert (south.count, south.last_seen_at, south.status, south.details["count"]) == (30, t0 - 5 * MIN, "open", 30)

    scenario(dsn, body)


def test_api02_counts_exactly_the_open_rows_api14_returns(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        for minutes in (30, 20, 10):  # three open repeats of one relay
            await alert(conn, t0 - minutes * MIN, "relay_stuck", "equipment.fan2", severity="critical")
        await alert(conn, t0 - 15 * MIN, "sensor_offline", "climate.rh_south", disposition="acknowledged")

        page = await as_role(conn, MCP_ROLE, alerts.list, "open", 50, None)
        now = await as_role(conn, DASHBOARD_ROLE, reads.now)
        open_rows = [item for item in page.items if item.status == "open"]
        assert len(page.items) == 2 and [item.count for item in open_rows] == [3]
        assert now.alerts.open == len(open_rows) == 1
        assert (now.alerts.critical, now.alerts.warning, now.alerts.info) == (1, 0, 0)
        assert now.alerts == await as_role(conn, MCP_ROLE, alerts.open_count)

    scenario(dsn, body)


# ── API-17: the activity log, filtered, paged and with readback outcomes ────


def test_activity_by_agent_steward_is_that_agent_only_newest_first_and_paged(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        first = (await settings.write(conn, STEWARD, "mister_engage_kpa", 1.5, False, "dry", None)).value
        await snapshot(conn, await conn.fetchval("SELECT now()"), "mister_engage_kpa", 1.5)  # confirms the first
        await settings.write(conn, PERSON, "fog_rh_ceiling_pct", 90, True, None, None)
        await settings.write(conn, OTHER_AGENT, "site_pressure_hpa", 845, False, None, None)
        second = (await settings.write(conn, STEWARD, "mister_engage_kpa", 1.6, False, "drier", None)).value
        await settings.release(conn, STEWARD, "fog_rh_ceiling_pct", "back to auto", None)

        async def pages(**filters):
            out, cursor = [], None
            while True:
                page = await as_role(conn, MCP_ROLE, reads.activity_page, limit=2, cursor=cursor, **filters)
                assert openapi_errors(response_schema("/v1/activity"), page.model_dump(mode="json")) == []
                out.append(page.items)
                cursor = page.next_cursor
                if cursor is None:
                    return out

        steward = await pages(source="agent:steward")
        assert [len(items) for items in steward] == [2, 1]
        rows = [entry for items in steward for entry in items]
        assert {(entry.actor.source, entry.actor.name) for entry in rows} == {("agent:steward", "steward")}
        assert [entry.action for entry in rows] == ["setting.release", "setting.write", "setting.write"]
        assert [(entry.ts, entry.id) for entry in rows] == sorted(((e.ts, e.id) for e in rows), reverse=True)
        release, newest, oldest = rows
        assert (release.state, release.confirmed_at) == ("released", None)
        assert (newest.detail["desired_id"], newest.state, newest.confirmed_at) == (second.id, "requested", None)
        assert (oldest.detail["desired_id"], oldest.state) == (first.id, "confirmed")
        assert oldest.confirmed_at is not None and oldest.confirmed_at >= first.created_at

        agents = [entry for items in await pages(source="agent") for entry in items]
        assert {entry.actor.source for entry in agents} == {"agent:steward", "agent:pathfinder"} and len(agents) == 4
        everyone = await as_role(conn, MCP_ROLE, reads.activity_page, action="setting.", target="fog_rh_ceiling_pct")
        assert [(entry.actor.source, entry.action) for entry in everyone.items] == [
            ("agent:steward", "setting.release"),
            ("dashboard", "setting.write"),
        ]
        # The person's manual write was released after it: an older row reads released or (not) confirmed.
        assert everyone.items[1].state == "not_confirmed"

    scenario(dsn, body)


# ── API-12: desired rows, plan values and readbacks over a window ───────────


def test_api12_marks_forecast_rule_superseded_and_overridden_waypoints(dsn):
    param = "mister_engage_kpa"

    async def body(conn, t0):
        await reporting_now(conn, t0)
        plan_a, plan_b = t0 - 8 * H, t0 - 5 * H
        rows_a = [(t0 - 8 * H, param, 1.40), (t0 - 6 * H, param, 1.45), (t0 + 1 * H, param, 1.50)]
        rows_a.append((t0 + 3 * H, param, 1.49))
        await full_plan(conn, "iris-vd55-a", plan_a, status="superseded", rows=rows_a, active=False)
        rows_b = [(t0 - 5 * H, param, 1.60), (t0 + 3 * H, param, 1.65)]
        await full_plan(conn, "iris-vd55-b", plan_b, status="effective", rows=rows_b, active=True)
        await waypoints(conn, "preemptive-vd55", t0 - 1 * H, [(t0 - 1 * H, param, 1.70)], source="preemptive")
        await waypoints(conn, "operator-vd55", t0 - 7 * H, [(t0 - 7 * H, param, 9.0)], source="operator")
        [desired_id] = await conn.fetch(
            "INSERT INTO desired_state (param, value, manual, source, actor, created_at) "
            "VALUES ($1, 1.55, false, 'agent:steward', 'steward', $2) RETURNING id",
            param,
            t0 - 2 * H,
        )
        await snapshot(conn, t0 - 3 * H, param, 1.60)
        await snapshot(conn, t0 - 90 * MIN, param, 1.55)

        history = await as_role(conn, MCP_ROLE, reads.setting_history, param, t0 - 10 * H, t0 + 6 * H)
        body_json = history.model_dump(mode="json")
        assert openapi_errors({"$ref": "#/components/schemas/SettingHistory"}, body_json) == []
        assert "from" in body_json and "from_" not in body_json
        assert (history.unit, history.min, history.max) == ("kPa", 0.5, 2.5)
        assert [(point.at, point.value, point.source, point.effective) for point in history.plan] == [
            (t0 - 8 * H, 1.40, "iris", True),
            (t0 - 6 * H, 1.45, "iris", True),
            (t0 - 5 * H, 1.60, "iris", True),
            (t0 - 1 * H, 1.70, "preemptive", True),  # the forecast rule, written after the desired row
            (t0 + 1 * H, 1.50, "iris", False),  # plan A was superseded by plan B (written t0 - 5 h)
            (t0 + 3 * H, 1.65, "iris", False),  # plan B's own row, after the newer non-manual desired row
        ]
        assert [(row.id, row.value, row.manual) for row in history.desired] == [(desired_id["id"], 1.55, False)]
        assert [(point.at, point.value) for point in history.readback] == [(t0 - 3 * H, 1.60), (t0 - 90 * MIN, 1.55)]

        with pytest.raises(Exception) as unknown:
            await as_role(conn, MCP_ROLE, reads.setting_history, "band_house_temp_low", t0 - 1 * H, t0)
        assert unknown.value.problem.code == "not_found"

    scenario(dsn, body)


def test_api12_a_plan_is_never_superseded_by_its_own_journal_row(dsn):
    """60 plans up to 2026-04-28 wrote their journal row after their waypoints, at most 54 s later."""
    param = "mister_engage_kpa"

    async def body(conn, t0):
        await reporting_now(conn, t0)
        written = t0 - 4 * H
        rows = [(written, param, 1.40), (t0 - 1 * H, param, 1.45), (t0 + 1 * H, param, 1.50)]
        await full_plan(conn, "iris-vd55-late", written, status="effective", rows=rows, active=True, journal_after=S)

        history = await as_role(conn, MCP_ROLE, reads.setting_history, param, t0 - 6 * H, t0 + 6 * H)
        assert [(point.value, point.effective) for point in history.plan] == [(1.40, True), (1.45, True), (1.50, True)]

    scenario(dsn, body)


# ── API-07: Plan and Settings show the same state and reason ────────────────


def test_plan_lanes_take_their_state_and_reason_from_settings(dsn):
    async def body(conn, t0):
        await reporting_now(conn, t0)
        held = (await settings.write(conn, PERSON, "site_pressure_hpa", 845, True, "clamp test", None)).value
        await conn.execute(
            "INSERT INTO setpoint_clamps (ts, parameter, requested, applied, reason) "
            "VALUES (now(), 'site_pressure_hpa', 845, 840, 'campaign_guard')"
        )
        await snapshot(conn, await conn.fetchval("SELECT now()"), "site_pressure_hpa", 840.0)

        lanes = {lane.param: lane for lane in (await as_role(conn, DASHBOARD_ROLE, reads.plan)).lanes}
        rows = {item.param: item for item in (await as_role(conn, MCP_ROLE, reads.settings_rows)).items}
        lane, row = lanes["site_pressure_hpa"], rows["site_pressure_hpa"]
        assert (lane.applied_from, lane.desired.id, lane.desired.state) == ("manual", held.id, "held")
        assert lane.desired.state_reason.code == "clamped" and lane.desired.state_reason.applied_value == 840.0
        assert lane.desired == row.desired and lane.applied_from == row.applied_from

    scenario(dsn, body)
