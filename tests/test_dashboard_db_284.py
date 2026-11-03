"""Migration 284 (VD-74, M2b) on a private database: the dashboard login's reads and writes on existing objects.

jvallery/agents#5994's campaign step runs each tests/test_dashboard_db_*.py file against a fresh loopback
PostgreSQL with TimescaleDB and hands it over in VERDIFY_ISOLATED_TEST_DSN. Without one the module skips, and
a skip fails campaign qualification. The migration chain cannot replay there (254 and later check production
digest literals), so irrigation_db_support.prepare_irrigation_tables creates the roles and runs the bodies of
281, 282 and 283. This file adds the other objects 284 names from db/schema.sql and 280's body, sets the ACLs
217 left on the functions it grants, records what each login reaches, and only then runs 284's body. Every list
of granted objects comes from parsing that body; test_284_dashboard_grants_delivery.py pins the body, so a removed
grant fails there, and here too when a service read needs it. The seals and the api, ingestor and MCP attestations
need the production catalog: the rolled-back native qualification proves them (the same delivery test). Every write
rolls back.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import irrigation_db_support as support
import pytest
from conftest import isolated_mutation_test_dsn

from verdify_services import reads

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

ROOT = support.REPO_ROOT
MIGRATION = ROOT / "db/migrations/284-dashboard-runtime-grants.sql"
DESIRED_STATE_MIGRATION = ROOT / "db/migrations/280-desired-state-activity-log.sql"
DUTY = "verdify_dashboard_runtime"
LOGIN = "verdify_dashboard_runtime_login"
OTHER_LOGINS = ("verdify_api_runtime_login", "verdify_ingestor_runtime_login", "verdify_mcp_runtime_login")
OK, DENIED = "ok", "42501"

# What 284 names beyond what 281, 282 and 283 need, from db/schema.sql in dependency order: its
# tables, what its views read, the helpers beneath the band functions and the updated_at triggers.
OBJECTS = (
    ("alert_log", "TABLE"),
    ("alert_log_id_seq", "SEQUENCE"),
    ("alert_log_id_seq", "SEQUENCE OWNED BY"),
    ("alert_log id", "DEFAULT"),
    ("alert_log alert_log_pkey", "CONSTRAINT"),
    ("crop_band_anchors", "TABLE"),
    ("crop_band_anchors_id_seq", "SEQUENCE"),
    ("crop_band_anchors_id_seq", "SEQUENCE OWNED BY"),
    ("crop_band_anchors id", "DEFAULT"),
    ("crop_band_anchors crop_band_anchors_pkey", "CONSTRAINT"),
    ("daily_summary", "TABLE"),
    ("diagnostics", "TABLE"),
    ("energy", "TABLE"),
    ("equipment", "TABLE"),
    ("plan_journal", "TABLE"),
    ("plan_journal plan_journal_pkey", "CONSTRAINT"),
    ("planner_trigger_ledger", "TABLE"),
    ("planner_trigger_ledger_id_seq", "SEQUENCE"),
    ("planner_trigger_ledger_id_seq", "SEQUENCE OWNED BY"),
    ("planner_trigger_ledger id", "DEFAULT"),
    ("planner_trigger_ledger planner_trigger_ledger_pkey", "CONSTRAINT"),
    ("sensor_registry", "TABLE"),
    ("setpoint_clamps", "TABLE"),
    ("setpoint_snapshot", "TABLE"),
    ("slack_alert_runbooks", "TABLE"),
    ("slack_alert_runbooks_id_seq", "SEQUENCE"),
    ("slack_alert_runbooks_id_seq", "SEQUENCE OWNED BY"),
    ("slack_alert_runbooks id", "DEFAULT"),
    ("switches", "TABLE"),
    ("water_meter_materializer_state", "TABLE"),
    ("weather_forecast", "TABLE"),
    ("zones", "TABLE"),
    ("v_active_plan", "VIEW"),
    ("v_data_pipeline_health", "VIEW"),
    ("v_equipment_relay_map", "VIEW"),
    ("v_open_alerts", "VIEW"),
    ("v_planner_trigger_health", "VIEW"),
    ("v_sensor_staleness", "VIEW"),
    ("v_water_meter_daily", "VIEW"),
    ("v_water_attribution_daily", "VIEW"),
    ("update_updated_at_column()", "FUNCTION"),
    ("alert_log trg_alert_log_updated_at", "TRIGGER"),
    ("crop_band_anchors trg_crop_band_anchors_updated_at", "TRIGGER"),
    ("fn_current_season()", "FUNCTION"),
    ("fn_hermite_phase(double precision, double precision, double precision, double precision, double precision, "
     "double precision)", "FUNCTION"),
    ("fn_solar_sunrise_hour(timestamp with time zone)", "FUNCTION"),
    ("fn_solar_sunset_hour(timestamp with time zone)", "FUNCTION"),
    ("fn_solar_phase(timestamp with time zone)", "FUNCTION"),
    ("fn_crop_band_value(text, text, timestamp with time zone, text, text, text)", "FUNCTION"),
    ("fn_band_setpoints(timestamp with time zone)", "FUNCTION"),
    ("fn_equip_at(text, timestamp with time zone)", "FUNCTION"),
    ("fn_zone_vpd_targets(timestamp with time zone)", "FUNCTION"),
)  # fmt: skip
# 217 removed PUBLIC from the direct helpers and kept it on the transitive ones, such as fn_solar_phase.
HELPER_ACLS = """
REVOKE ALL ON FUNCTION public.fn_band_setpoints(timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_crop_band_value(text,text,timestamptz,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_current_season() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_equip_at(text,timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_zone_vpd_targets(timestamptz) FROM PUBLIC;
"""
# Production owns v_greenhouse_now with no dashboard grant; its helpers need a much larger subset.
STAND_IN = "CREATE VIEW public.v_greenhouse_now AS SELECT 1 AS stand_in;"
# Enough rows that each read below takes its populated path. A row per series and anchor for the house
# gives the band functions complete curves.
ROWS = """
INSERT INTO public.climate (ts, temp_avg, rh_avg, vpd_avg, flow_gpm, outdoor_temp_f, outdoor_rh_pct, water_total_gal)
VALUES (now() - interval '1 minute', 75, 60, 1.2, 0.05, 60, 30, 100);
INSERT INTO public.diagnostics (ts, firmware_version, uptime_s, heap_bytes, wifi_rssi)
VALUES (now() - interval '1 minute', 'test', 100, 40, -60);
INSERT INTO public.system_state (ts, entity, value) VALUES
    (now() - interval '1 minute', 'ingestor_heartbeat', 'alive'),
    (now() - interval '1 hour', 'greenhouse_state', 'IDLE'),
    (now() - interval '1 minute', 'mode_reason', 'test');
INSERT INTO public.equipment_state (ts, equipment, state) VALUES
    (now() - interval '2 hours', 'fan1', true),
    (now() - interval '1 hour', 'fan1', false),
    (now() - interval '2 hours', 'leak_detected', true);
INSERT INTO public.setpoint_snapshot (ts, parameter, value, zone, band_role) VALUES
    (now() - interval '1 minute', 'band_house_temp_low', 70, 'house', 'temp_low'),
    (now() - interval '1 minute', 'band_house_temp_high', 80, 'house', 'temp_high'),
    (now() - interval '1 minute', 'mister_engage_kpa', 1.4, NULL, NULL),
    (now() - interval '1 minute', 'fog_escalation_kpa', 0.6, NULL, NULL);
INSERT INTO public.plan_journal (plan_id, created_at, valid_from, expires_at, lifecycle_status, hypothesis)
VALUES ('vd74-plan', now() - interval '1 hour', now() - interval '1 hour', now() + interval '6 hours', 'effective',
        'test');
INSERT INTO public.setpoint_plan (ts, parameter, value, plan_id, source, created_at, is_active, expires_at) VALUES
    (now() - interval '30 minutes', 'fog_escalation_kpa', 0.6, 'vd74-plan', 'iris', now() - interval '1 hour', true,
     now() + interval '6 hours'),
    (now() + interval '1 hour', 'fog_escalation_kpa', 0.7, 'vd74-plan', 'iris', now() - interval '1 hour', true,
     now() + interval '6 hours');
INSERT INTO public.planner_trigger_ledger (event_type, expected_at, due_at, status) VALUES
    ('SUNRISE', now() - interval '3 hours', now() + interval '1 hour', 'expected');
INSERT INTO public.alert_log (ts, alert_type, severity, category, sensor_id, message, source, disposition) VALUES
    (now() - interval '2 hours', 'leak_detected', 'critical', 'system', 'water', 'test', 'system', 'open'),
    (now() - interval '2 hours', 'setpoint_delivery_stuck', 'warning', 'system', NULL, 'test', 'system', 'open');
INSERT INTO public.desired_state (param, value, manual, source, actor, created_at)
VALUES ('mister_engage_kpa', 1.5, true, 'dashboard', 'jason', now() - interval '1 hour');
INSERT INTO public.setpoint_clamps (ts, parameter, requested, applied, reason)
VALUES (now() - interval '10 minutes', 'mister_engage_kpa', 1.5, 1.4, 'test');
INSERT INTO public.setpoint_changes (ts, parameter, value, source)
VALUES (now() - interval '10 minutes', 'mister_engage_kpa', 1.4, 'dispatcher');
INSERT INTO public.water_meter_events (ts, event_type, delta_gal, quality_flag)
VALUES (now() - interval '5 minutes', 'delta', 1, 'ok');
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value, width_below, width_above)
SELECT 'house', series, anchor, 70, 1, 1
  FROM unnest(ARRAY['temp_low', 'temp_target', 'temp_high', 'vpd_low', 'vpd_target', 'vpd_high']) series
 CROSS JOIN unnest(ARRAY['sr', 'sm', 'ss', 'mid']) anchor;
"""
# Each function the file grants, called as the services call it.
CALLS = {
    "public.fn_band_setpoints(timestamptz)": "SELECT * FROM public.fn_band_setpoints(now())",
    "public.fn_crop_band_value(text,text,timestamptz,text,text,text)": (
        "SELECT public.fn_crop_band_value('house', 'temp_low', now())"
    ),
    "public.fn_current_season()": "SELECT public.fn_current_season()",
    "public.fn_equip_at(text,timestamptz)": "SELECT public.fn_equip_at('fan1', now())",
    "public.fn_irrigation_minutes_daily(date,date)": (
        "SELECT * FROM public.fn_irrigation_minutes_daily(current_date, current_date)"
    ),
    "public.fn_mcp_armed_assignment_context()": "SELECT * FROM public.fn_mcp_armed_assignment_context()",
    "public.fn_zone_vpd_targets(timestamptz)": "SELECT * FROM public.fn_zone_vpd_targets(now())",
}


@dataclass
class Grants:
    selects: list[str] = field(default_factory=list)
    inserts: list[str] = field(default_factory=list)
    sequences: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    columns: dict[str, list[str]] = field(default_factory=dict)


def parsed_grants() -> Grants:
    """284's GRANT statements: the one list every test here reads."""
    code = re.sub(r"--[^\n]*", "", support.migration_body(MIGRATION))
    found = Grants()
    statements = re.findall(rf"GRANT\s+(.+?)\s+ON\s+(SEQUENCE\s+|FUNCTION\s+)?(.+?)\s+TO\s+{DUTY}\s*;", code, re.S)
    assert len(statements) == len(re.findall(r"\bGRANT\b", code))
    for privileges, kind, objects in statements:
        names = re.findall(r"public\.\w+(?:\([^)]*\))?", objects)
        assert names, objects
        if kind.strip() == "FUNCTION":
            assert privileges == "EXECUTE"
            found.functions += names
        elif kind.strip() == "SEQUENCE":
            assert privileges == "USAGE"
            found.sequences += names
        elif privileges == "SELECT":
            found.selects += names
        elif privileges == "INSERT":
            found.inserts += names
        else:
            columns = re.fullmatch(r"UPDATE\s*\(([^)]*)\)", privileges)
            assert columns, privileges
            found.columns[names[0]] = [column.strip() for column in columns[1].split(",")]
    return found


GRANTS = parsed_grants()


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


async def reach(conn, login: str) -> dict[str, set]:
    """What a login holds beyond PUBLIC's: by relation privilege, column, sequence and function."""
    relations = await conn.fetch(
        """SELECT format('%I.%I', n.nspname, c.relname) AS name, p.privilege
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p(privilege)
            WHERE n.nspname IN ('public', 'verdify_dashboard_runtime') AND c.relkind IN ('r','p','v','m','f')
              AND has_table_privilege($1, c.oid, p.privilege) AND NOT has_table_privilege('public', c.oid, p.privilege)""",
        login,
    )
    columns = await conn.fetch(
        """SELECT format('%I.%I', n.nspname, c.relname) AS name, a.attname::text AS column_name, p.privilege
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) p(privilege)
            WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum > 0 AND NOT a.attisdropped
              AND has_column_privilege($1, c.oid, a.attnum, p.privilege)
              AND NOT has_table_privilege($1, c.oid, p.privilege)""",
        login,
    )
    sequences = await conn.fetch(
        """SELECT format('%I.%I', n.nspname, c.relname) AS name, p.privilege
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN unnest(ARRAY['USAGE','SELECT','UPDATE']) p(privilege)
            WHERE n.nspname = 'public'
              AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege($1, c.oid, p.privilege) ELSE FALSE END""",
        login,
    )
    functions = await conn.fetch(
        """SELECT p.oid::regprocedure::text AS name
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('public', 'verdify_dashboard_runtime')
              AND has_function_privilege($1, p.oid, 'EXECUTE') AND NOT has_function_privilege('public', p.oid, 'EXECUTE')""",
        login,
    )
    return {
        "relations": {(row["name"], row["privilege"]) for row in relations},
        "columns": {(row["name"], row["column_name"], row["privilege"]) for row in columns},
        "sequences": {(row["name"], row["privilege"]) for row in sequences},
        "functions": {row["name"] for row in functions},
    }


async def install() -> dict[str, dict[str, set]]:
    conn = await connect()
    try:
        fresh = await conn.fetchval(
            "SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1) AND to_regnamespace($1) IS NULL", DUTY
        )
        if not fresh:
            pytest.fail("test_dashboard_db_284 needs a fresh server: the dashboard duty already exists")
        await support.prepare_irrigation_tables(conn)
        sections = support.schema_sections()
        await conn.execute("SET ROLE verdify")
        try:
            await conn.execute("SET check_function_bodies = false")
            await conn.execute("\n".join(sections[(name, kind, "public")] for name, kind in OBJECTS))
            await conn.execute("RESET check_function_bodies")
            await conn.execute(STAND_IN)
            await conn.execute(HELPER_ACLS)
            await conn.execute(
                "SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(DESIRED_STATE_MIGRATION)
            )
            await conn.execute("RESET search_path")
        finally:
            await conn.execute("RESET ROLE")
        await conn.execute(ROWS)
        before = {login: await reach(conn, login) for login in (LOGIN, *OTHER_LOGINS)}
        await conn.execute("SET ROLE verdify")
        try:
            await conn.execute("SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(MIGRATION))
            await conn.execute("RESET search_path")
        finally:
            await conn.execute("RESET ROLE")
        return before
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def before():
    return run(install())


async def attempts(statements) -> list[str]:
    """Each (sql, *args) as the login, in its own savepoint of one rolled-back transaction: 'ok' or SQLSTATE."""
    conn = await connect(LOGIN)
    outcomes = []
    try:
        outer = conn.transaction()
        await outer.start()
        try:
            for sql, *args in statements:
                try:
                    async with conn.transaction():
                        await conn.execute(sql, *args)
                    outcomes.append(OK)
                except asyncpg.PostgresError as exc:
                    outcomes.append(exc.sqlstate)
        finally:
            await outer.rollback()
    finally:
        await conn.close()
    return outcomes


def test_the_parsed_list_has_each_kind_of_grant():
    assert "public.climate" in GRANTS.selects and "public.v_active_plan" in GRANTS.selects
    assert GRANTS.inserts == ["public.desired_state", "public.activity_log"]
    assert GRANTS.sequences == ["public.desired_state_id_seq", "public.activity_log_id_seq"]
    assert set(GRANTS.functions) == set(CALLS)
    # The issue's column lists, so a wider grant cannot pass the update test below by widening what it parses.
    assert GRANTS.columns == {
        "public.alert_log": [
            "disposition",
            "acknowledged_at",
            "acknowledged_by",
            "notes",
            "resolved_at",
            "resolved_by",
            "resolution",
        ],
        "public.crop_band_anchors": ["value", "width_below", "width_above"],
    }


def test_the_login_reads_each_listed_relation_and_nothing_before_284(before):
    assert run(attempts([(f"SELECT * FROM {name} LIMIT 1",) for name in GRANTS.selects])) == [OK] * len(GRANTS.selects)
    held = {name for name, _ in before[LOGIN]["relations"]}
    assert not held & set(GRANTS.selects)  # 283 keeps its own grants; 284 repeats none of them


def test_the_login_executes_each_listed_function(before):
    # test_284_adds_exactly_its_list_to_the_login_and_nothing_to_any_other shows it held none of them before.
    assert run(attempts([(CALLS[name],) for name in GRANTS.functions])) == [OK] * len(GRANTS.functions)


def test_the_band_functions_return_values_through_the_helpers_beneath_them(before):
    async def check():
        conn = await connect(LOGIN)
        try:
            band = await conn.fetchrow("SELECT * FROM public.fn_band_setpoints(now())")
            zones = await conn.fetchrow("SELECT * FROM public.fn_zone_vpd_targets(now())")
            season = await conn.fetchval("SELECT public.fn_current_season()")
            equip = await conn.fetchval("SELECT public.fn_equip_at('fan1', now())")
            return band, zones, season, equip
        finally:
            await conn.close()

    band, zones, season, equip = run(check())
    assert band["temp_low"] == pytest.approx(70) and band["vpd_high"] == pytest.approx(70)
    assert zones["vpd_target_center"] == pytest.approx(70)  # orchid has no anchors here: the house curve
    assert season in ("spring", "summer", "fall", "winter")
    assert equip is False  # the newest fan1 row


def test_the_login_inserts_desired_state_and_activity_log_with_their_sequences(before):
    async def check():
        conn = await connect(LOGIN)
        transaction = conn.transaction()
        await transaction.start()
        try:
            desired = await conn.fetchrow(
                "INSERT INTO desired_state (greenhouse_id, param, value, manual, source, actor, reason) "
                "VALUES ('vallery', 'vd74', 1.5, true, 'dashboard', 'jason', 'test') RETURNING id, manual"
            )
            activity = await conn.fetchval(
                "INSERT INTO public.activity_log (source, actor, action, target) "
                "VALUES ('dashboard', 'jason', 'setting.write', 'vd74') RETURNING id"
            )
            current = await conn.fetchval("SELECT value FROM v_desired_state_current WHERE param = 'vd74'")
            return desired, activity, current
        finally:
            await transaction.rollback()
            await conn.close()

    desired, activity, current = run(check())
    assert desired["id"] > 0 and desired["manual"] is True and activity > 0 and current == 1.5


@pytest.mark.parametrize("name", sorted(GRANTS.columns))
def test_the_login_updates_only_the_listed_columns(before, name):
    async def fetch():
        conn = await connect()
        try:
            rows = await conn.fetch(
                "SELECT attname::text FROM pg_attribute WHERE attrelid = $1::regclass AND attnum > 0 "
                "AND NOT attisdropped ORDER BY attnum",
                name,
            )
            row_id = await conn.fetchval(f"SELECT min(id) FROM {name}")
            return [row[0] for row in rows], row_id
        finally:
            await conn.close()

    columns, row_id = run(fetch())
    granted = GRANTS.columns[name]
    assert set(granted) < set(columns)
    outcomes = run(attempts([(f"UPDATE {name} SET {column} = {column} WHERE id = $1", row_id) for column in columns]))
    assert dict(zip(columns, outcomes, strict=True)) == {c: OK if c in granted else DENIED for c in columns}


def test_ack_resolve_and_a_band_write_run_as_the_services_run_them(before):
    """The columns of verdify_services' statements, and the updated_at triggers with no column grant."""

    async def check():
        conn = await connect(LOGIN)
        transaction = conn.transaction()
        await transaction.start()
        try:
            acked = await conn.fetch(
                "UPDATE alert_log SET disposition = 'acknowledged', acknowledged_at = now(), "
                "acknowledged_by = 'jason', notes = coalesce('a note', notes) "
                "WHERE alert_type = 'leak_detected' AND sensor_id IS NOT DISTINCT FROM 'water' "
                "AND resolved_at IS NULL AND disposition = 'open' RETURNING id, updated_at > created_at AS moved"
            )
            resolved = await conn.fetch(
                "UPDATE alert_log SET disposition = 'resolved', resolved_at = now(), resolved_by = 'jason', "
                "resolution = 'a note' WHERE alert_type = 'leak_detected' AND sensor_id IS NOT DISTINCT FROM 'water' "
                "AND resolved_at IS NULL AND disposition <> 'suppressed' RETURNING id"
            )
            anchors = await conn.fetch(
                "UPDATE crop_band_anchors SET value = 71 WHERE series = 'temp_low' AND crop_type = 'house' "
                "RETURNING id, updated_at > created_at AS moved"
            )
            widths = await conn.execute(
                "UPDATE crop_band_anchors SET width_below = 2, width_above = 2 WHERE series = 'temp_low'"
            )
            return acked, resolved, anchors, widths
        finally:
            await transaction.rollback()
            await conn.close()

    acked, resolved, anchors, widths = run(check())
    assert len(acked) == 1 and acked[0]["moved"] and [r["id"] for r in resolved] == [acked[0]["id"]]
    assert len(anchors) == 4 and all(row["moved"] for row in anchors) and widths == "UPDATE 4"


def test_the_login_is_refused_everything_else(before):
    statements = {
        "INSERT INTO public.setpoint_changes (ts, parameter, value, source) VALUES (now(), 'x', 1, 'manual')": DENIED,
        "UPDATE public.setpoint_changes SET value = 1": DENIED,
        "DELETE FROM public.setpoint_changes": DENIED,
        "INSERT INTO public.setpoint_plan (ts, parameter, value, plan_id) VALUES (now(), 'x', 1, 'x')": DENIED,
        "UPDATE public.setpoint_plan SET value = 1": DENIED,
        "DELETE FROM public.setpoint_plan": DENIED,
        "SELECT * FROM public.control_experiments LIMIT 1": DENIED,
        "SELECT * FROM public.control_assignments LIMIT 1": DENIED,
        "SELECT * FROM public.effective_policy_vectors LIMIT 1": DENIED,
        "SELECT * FROM public.policy_delivery_outbox LIMIT 1": DENIED,
        "SELECT * FROM public.policy_exposures LIMIT 1": DENIED,
        "SELECT * FROM public.v_greenhouse_now LIMIT 1": DENIED,
        "UPDATE public.desired_state SET value = 1": DENIED,
        "DELETE FROM public.desired_state": DENIED,
        "UPDATE public.activity_log SET reason = 'x'": DENIED,
        "DELETE FROM public.activity_log": DENIED,
        "INSERT INTO public.alert_log (alert_type, message) VALUES ('x', 'x')": DENIED,
        "DELETE FROM public.alert_log": DENIED,
        "INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value) VALUES ('x', 'temp_low', 'sr', 1)": DENIED,
        "DELETE FROM public.crop_band_anchors": DENIED,
        "INSERT INTO public.climate (ts) VALUES (now())": DENIED,
        "INSERT INTO public.equipment_state (ts, equipment, state) VALUES (now(), 'fan1', true)": DENIED,
        "INSERT INTO public.system_state (ts, entity, value) VALUES (now(), 'x', 'x')": DENIED,
        "TRUNCATE public.desired_state": DENIED,
        "SELECT nextval('public.alert_log_id_seq')": DENIED,
        "CREATE TABLE public.vd74_probe (id int)": DENIED,
    }  # fmt: skip
    assert dict(zip(statements, run(attempts([(sql,) for sql in statements])), strict=True)) == statements


def test_284_adds_exactly_its_list_to_the_login_and_nothing_to_any_other(before):
    async def after():
        conn = await connect()
        try:
            names = await conn.fetch("SELECT unnest($1::text[])::regprocedure::text AS name", GRANTS.functions)
            return {login: await reach(conn, login) for login in (LOGIN, *OTHER_LOGINS)}, {row["name"] for row in names}
        finally:
            await conn.close()

    now, functions = run(after())
    expected = {
        "relations": {(name, "SELECT") for name in GRANTS.selects} | {(name, "INSERT") for name in GRANTS.inserts},
        "columns": {(name, column, "UPDATE") for name, columns in GRANTS.columns.items() for column in columns},
        "sequences": {(name, "USAGE") for name in GRANTS.sequences},
        "functions": functions,
    }
    for kind, added in expected.items():
        assert now[LOGIN][kind] - before[LOGIN][kind] == added, kind
        assert before[LOGIN][kind] <= now[LOGIN][kind], kind
    for login in OTHER_LOGINS:
        assert now[login] == before[login], login


def test_the_login_still_attests(before):
    async def check():
        conn = await connect(LOGIN)
        try:
            return await conn.fetchval(f"SELECT {DUTY}.fn_attest_login()")
        finally:
            await conn.close()

    assert run(check()) is True


def test_the_operator_api_reads_run_as_the_login(before):
    """/v1/now, /v1/plan and /v1/equipment: what answered 503 until these grants."""

    async def check():
        conn = await connect(LOGIN)
        try:
            return await reads.now(conn), await reads.plan(conn), await reads.equipment(conn)
        finally:
            await conn.close()

    now, plan, equipment = run(check())
    assert now.indoor.temp_f.value == 75 and now.writer.last_at is not None and now.freshness.ingestor.age_s is not None
    assert now.alerts.critical >= 1 and now.leak.active is True and now.leak.alert_id is not None
    assert plan.plan is not None and plan.plan.plan_id == "vd74-plan" and plan.lanes and plan.health
    assert any(item.equipment == "fan1" and item.state == "off" for item in equipment.items)
