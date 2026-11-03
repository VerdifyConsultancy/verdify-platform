"""Migration M3 (VD-38, #1153): irrigation tables, seeds, grants and the redefined minutes, on a private database.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a
fresh loopback PostgreSQL with TimescaleDB and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``.
Without one the module skips, and a skip fails campaign qualification. The migration chain
cannot replay there (254 and later check production digest literals), so
``irrigation_db_support.prepare_irrigation_tables`` creates the roles, loads the production
objects M4 and M3 read from ``db/schema.sql`` and runs the statements between the
``$predecessor$`` and ``$successor$`` blocks of 281 (M4), 282 (the dashboard login) and 283 (M3),
as #1146's test does. The seals and the api, ingestor and MCP attestations need the production
catalog: the rolled-back native qualification proves them
(``tests/test_283_irrigation_tables_delivery.py``). Every scenario that adds rows rolls back.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import math
import re
import time
import uuid

import irrigation_db_support as support
import pytest
from conftest import isolated_mutation_test_dsn

from verdify_schemas import irrigation as irr

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

API, INGESTOR, MCP = "verdify_api_runtime_login", "verdify_ingestor_runtime_login", "verdify_mcp_runtime_login"
DASHBOARD = "verdify_dashboard_runtime_login"
OK, DENIED, CHECK, UNIQUE = "ok", "42501", "23514", "23505"
TABLES = ("irrigation_line", "irrigation_slot", "irrigation_command", "irrigation_push", "irrigation_ledger")
VECTORS = support.REPO_ROOT / "verdify_schemas/tests/fixtures/irrigation_program_vectors.json"
DAY = dt.date

# irrigation §3 with the D-IRP-06 caps: name, zone, kind, relay, equipment, water_system,
# device_idx, clean_partner, reserved, enabled, vpd_member, schedule_member, daily_cap_min,
# vpd_reserve_min.
SEEDS = {
    "L01": ("Drip • Wall", "wall", "drip", "wall_drips", "drip_wall", "wall_drip_clean", 0, None, False, True,
            None, None, 60, None),
    "L02": ("Drip • Wall (Fert.)", "wall", "fert_drip", "wall_drips_fertilized", "drip_wall_fert", "wall_drip_fert",
            1, "L01", False, False, None, None, 0, None),
    "L03": ("Valve • Fert. Master", "supply", "master", "fertilizer_master_valve", "fert_master_valve", None, 2,
            None, False, False, None, None, 0, None),
    "L04": ("Mister • Center", "center", "mister", "center_mister", "mister_center", "center_mister_clean", 3, None,
            False, True, True, False, 120, 60),
    "L05": ("Drip • Center", "center", "drip", "center_drips", "drip_center", "center_drip_clean", 4, None, True,
            False, None, None, 30, None),
    "L06": ("Drip • Center (Fert.)", "center", "fert_drip", "center_drips_fertilized", "drip_center_fert",
            "center_drip_fert", 5, "L05", True, False, None, None, 0, None),
    "L07": ("Mister • South Wall", "south", "mister", "south_wall_mister", "mister_south", "south_mister_clean", 6,
            None, False, False, False, False, 45, 15),
    "L08": ("Mister • South Wall (Fert.)", "south", "fert_mister", "south_wall_mister_fertilized",
            "mister_south_fert", "south_mister_fert", 7, "L07", False, False, None, None, 0, None),
    "L09": ("Mister • West Wall", "west", "mister", "west_wall_mister", "mister_west", "west_mister_clean", 8,
            None, False, False, False, False, 45, 15),
    "L10": ("Mister • West Wall (Fert.)", "west", "fert_mister", "west_wall_mister_fertilized", "mister_west_fert",
            "west_mister_fert", 9, "L09", False, False, None, None, 0, None),
    "L11": ("Fog (AquaFog XE 2000)", "house", "fog", "fog_rly", "fog", "center_fog", 10, None, False, False, None,
            None, 0, None),
}  # fmt: skip
SEED_COLUMNS = (
    "name, zone, kind, relay, equipment, water_system, device_idx, clean_partner, reserved, enabled, "
    "vpd_member, schedule_member, daily_cap_min, vpd_reserve_min"
)
LINE_COLUMNS = [
    ("line_id", "text"), ("greenhouse_id", "text"), ("name", "text"), ("zone", "text"), ("kind", "text"),
    ("relay", "text"), ("equipment", "text"), ("water_system", "text"), ("device_idx", "smallint"),
    ("clean_partner", "text"), ("reserved", "boolean"), ("enabled", "boolean"), ("vpd_member", "boolean"),
    ("schedule_member", "boolean"), ("daily_cap_min", "integer"), ("vpd_reserve_min", "integer"),
    ("paused_until", "timestamp with time zone"), ("manual", "boolean"), ("checked_by", "text"),
    ("checked_at", "timestamp with time zone"), ("check_note", "text"), ("source", "text"), ("updated_by", "text"),
    ("updated_at", "timestamp with time zone"), ("reason", "text"),
]  # fmt: skip
LINE_STATE_EXTRA = [
    ("relay_on", "boolean"), ("relay_at", "timestamp with time zone"), ("relay_age_s", "integer"),
    ("irr_state", "text"), ("irr_state_at", "timestamp with time zone"), ("irr_state_age_s", "integer"),
    ("irr_events", "text"), ("irr_events_at", "timestamp with time zone"), ("irr_events_age_s", "integer"),
]  # fmt: skip
MINUTES_COLUMNS = [
    ("greenhouse_id", "text"), ("local_day", "date"), ("zone", "text"), ("line_id", "text"), ("cause", "text"),
    ("chemistry", "text"), ("slot_id", "integer"), ("minutes", "double precision"), ("pulses", "integer"),
    ("gal", "integer"), ("partial", "boolean"),
]  # fmt: skip

NEW_SLOT = (
    "INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, count, gap_s, degraded_only, "
    "label, source, updated_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'agent:test', 'vd38') RETURNING slot_id"
)
NEW_COMMAND = (
    "INSERT INTO public.irrigation_command (kind, line_id, args, source, actor) "
    "VALUES ('run', 'L01', '{}', 'dashboard', 'jason') RETURNING command_id"
)
NEW_LEDGER = (
    "INSERT INTO public.irrigation_ledger (line_id, slot_id, local_day, cause, started_at, outcome, event_seq) "
    "VALUES ('L04', 2, current_date, 'fallback', now(), NULL, 7) RETURNING id"
)
SEED_RECORD = bytes.fromhex(
    "".join(
        hex_chunk
        for vector in json.loads(VECTORS.read_text())["vectors"]
        if vector["name"] == "seed"
        for _, hex_chunk in vector["chunks"]
    )
)
NEW_PUSH = "INSERT INTO public.irrigation_push (sha256, bytes) VALUES ($1, $2) RETURNING revision"


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


def local(text: str) -> dt.datetime:
    """An America/Denver wall-clock time as an aware instant."""
    return dt.datetime.fromisoformat(text).replace(tzinfo=support.DENVER)


@pytest.fixture(scope="module")
def database():
    async def setup():
        conn = await connect()
        try:
            timescale = await support.prepare_irrigation_tables(conn)
            await conn.execute(support.DAYS_FIXTURE.read_text())
            # Today is the newest climate day; the fixture carries no climate rows.
            await conn.execute("INSERT INTO public.climate (ts, greenhouse_id) VALUES (now(), 'vallery')")
            # One pending command the ingestor test marks sent (a run on L07, never a bridge run).
            await conn.execute(
                "INSERT INTO public.irrigation_command (command_id, kind, line_id, source, actor) "
                "VALUES ('00000000-0000-4000-8000-000000000383', 'run', 'L07', 'dashboard', 'jason')"
            )
            await conn.execute("ANALYZE")
            return timescale
        finally:
            await conn.close()

    return run(setup())


async def attempts(user: str, statements) -> list[str]:
    """Each (sql, *args) as the login, in its own savepoint of one rolled-back transaction: 'ok' or SQLSTATE."""
    conn = await connect(user)
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


def read_statements(relations):
    return [(f"SELECT * FROM public.{relation} LIMIT 1",) for relation in relations]


# ── Catalog ─────────────────────────────────────────────────────────────────


def test_tables_views_and_the_minutes_contract(database):
    async def check():
        conn = await connect()
        try:

            async def columns(name):
                return [
                    (r["column_name"], r["data_type"])
                    for r in await conn.fetch(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = $1 ORDER BY ordinal_position",
                        name,
                    )
                ]

            out = {name: await columns(name) for name in (*TABLES, "v_irrigation_line_state")}
            out["minutes"] = await columns("v_irrigation_minutes_daily")
            out["function"] = await conn.fetchrow(
                "SELECT pg_get_function_result(p.oid) AS result, p.prosecdef, p.provolatile::text AS volatile, "
                "p.proisstrict, p.proconfig, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p "
                "WHERE p.oid = 'public.fn_irrigation_minutes_daily(date, date)'::regprocedure"
            )
            out["sequences"] = {
                r["sequencename"]: (r["data_type"], r["start_value"], r["min_value"], r["max_value"], r["cycle"])
                for r in await conn.fetch(
                    "SELECT sequencename, data_type::text, start_value, min_value, max_value, cycle FROM pg_sequences "
                    "WHERE schemaname = 'public' AND sequencename LIKE 'irrigation%'"
                )
            }
            out["identity"] = {
                (r["table_name"], r["column_name"]): r["identity_generation"]
                for r in await conn.fetch(
                    "SELECT table_name, column_name, identity_generation FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND is_identity = 'YES' AND table_name LIKE 'irrigation%'"
                )
            }
            out["indexes"] = {
                r["indexname"]: r["indexdef"]
                for r in await conn.fetch(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
                    "AND tablename IN ('irrigation_command', 'irrigation_ledger') AND indexname NOT LIKE '%pkey'"
                )
            }
            out["owners"] = {
                r["relname"]: r["owner"]
                for r in await conn.fetch(
                    "SELECT relname, pg_get_userbyid(relowner) AS owner FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relname = ANY($1::text[])",
                    [*TABLES, "v_irrigation_line_state", "v_irrigation_minutes_daily"],
                )
            }
            out["triggers"] = await conn.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = ANY($1::regclass[])",
                [f"public.{t}" for t in TABLES],
            )
            return out
        finally:
            await conn.close()

    out = run(check())
    assert out["irrigation_line"] == LINE_COLUMNS
    assert out["v_irrigation_line_state"] == LINE_COLUMNS + LINE_STATE_EXTRA
    assert [c for c, _ in out["irrigation_slot"]] == [
        "slot_id", "greenhouse_id", "line_id", "enabled", "days", "start_kind", "start_min", "on_s", "count",
        "gap_s", "prewet_s", "flush_s", "degraded_only", "label", "manual", "source", "updated_by", "updated_at",
        "reason", "created_at",
    ]  # fmt: skip
    assert dict(out["irrigation_slot"])["days"] == dict(out["irrigation_slot"])["start_min"] == "smallint"
    assert [c for c, _ in out["irrigation_command"]] == [
        "command_id", "greenhouse_id", "kind", "line_id", "args", "status", "fail_reason", "source", "actor",
        "reason", "created_at", "sent_at",
    ]  # fmt: skip
    assert dict(out["irrigation_command"])["command_id"] == "uuid"
    assert out["irrigation_push"] == [
        ("revision", "bigint"), ("greenhouse_id", "text"), ("sha256", "text"), ("bytes", "bytea"),
        ("site_desired_at", "timestamp with time zone"), ("pushed_at", "timestamp with time zone"),
        ("confirmed_at", "timestamp with time zone"), ("reject_code", "text"),
    ]  # fmt: skip
    assert [c for c, _ in out["irrigation_ledger"]] == [
        "id", "greenhouse_id", "line_id", "slot_id", "local_day", "cause", "started_at", "ended_at", "open_s",
        "pulses", "outcome", "reason", "event_seq", "command_id", "actor", "recorded_at",
    ]  # fmt: skip
    assert dict(out["irrigation_ledger"])["slot_id"] == "integer"
    # The minutes keep M4's exact columns, and the reader its signature, definer rights and owner.
    assert out["minutes"] == MINUTES_COLUMNS
    function = out["function"]
    assert function["result"] == "TABLE(" + ", ".join(f"{n} {t}" for n, t in MINUTES_COLUMNS) + ")"
    assert function["prosecdef"] and function["volatile"] == "s" and function["proisstrict"]
    assert function["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]
    assert out["sequences"] == {
        "irrigation_slot_slot_id_seq": ("integer", 4, 1, 65535, False),
        "irrigation_push_revision_seq": ("bigint", 1, 1, 9223372036854775807, False),
        "irrigation_ledger_id_seq": ("bigint", 1, 1, 9223372036854775807, False),
    }
    assert out["identity"] == {("irrigation_slot", "slot_id"): "ALWAYS", ("irrigation_push", "revision"): "ALWAYS"}
    assert out["indexes"] == {
        "irrigation_command_pending": "CREATE INDEX irrigation_command_pending ON public.irrigation_command "
        "USING btree (created_at) WHERE (status = 'pending'::text)",
        "irrigation_command_sent_runs": "CREATE INDEX irrigation_command_sent_runs ON public.irrigation_command "
        "USING btree (greenhouse_id, line_id, sent_at) WHERE ((kind = 'run'::text) AND (status = 'sent'::text))",
        "irrigation_ledger_occurrence": "CREATE UNIQUE INDEX irrigation_ledger_occurrence ON public.irrigation_ledger "
        "USING btree (greenhouse_id, slot_id, local_day, cause) WHERE (slot_id IS NOT NULL)",
        "irrigation_ledger_line_started": "CREATE INDEX irrigation_ledger_line_started ON public.irrigation_ledger "
        "USING btree (greenhouse_id, line_id, started_at)",
    }
    assert set(out["owners"].values()) == {"verdify"}
    assert out["triggers"] == 0  # no trigger and no NOTIFY: the services wake the ingestor themselves


# ── Seeds ───────────────────────────────────────────────────────────────────


def test_seeds_are_the_design_and_equal_the_seed_vector(database):
    async def fetch():
        conn = await connect()
        try:
            lines = await conn.fetch("SELECT * FROM public.irrigation_line ORDER BY line_id")
            slots = await conn.fetch("SELECT * FROM public.irrigation_slot ORDER BY slot_id")
            return lines, slots
        finally:
            await conn.close()

    lines, slots = run(fetch())
    names = [c.strip() for c in SEED_COLUMNS.split(",")]
    assert {r["line_id"]: tuple(r[c] for c in names) for r in lines} == SEEDS
    for row in lines:
        # Source system, actor migration, manual false and no check on any line (D-IRP-07).
        assert (row["greenhouse_id"], row["source"], row["updated_by"], row["manual"]) == (
            "vallery",
            "system",
            "migration",
            False,
        )
        assert row["checked_by"] is None and row["checked_at"] is None and row["check_note"] is None
        assert row["paused_until"] is None and row["device_idx"] == int(row["line_id"][1:]) - 1
    assert [r["line_id"] for r in lines if r["enabled"]] == ["L01", "L04"]
    assert [r["line_id"] for r in lines if r["reserved"]] == ["L05", "L06"]
    assert [
        (r["slot_id"], r["line_id"], r["days"], r["start_kind"], r["start_min"], r["on_s"], r["count"], r["gap_s"])
        for r in slots
    ] == [(n, "L04", 127, "solar_noon", start, 45, 4, 60) for n, start in ((1, -120), (2, 0), (3, 120))]
    assert all(r["degraded_only"] and r["enabled"] and r["label"] == "vanda-degraded" for r in slots)
    assert all((r["prewet_s"], r["flush_s"], r["manual"], r["source"], r["updated_by"]) == (0, 0, False, "system",
               "migration") for r in slots)  # fmt: skip
    # The rows equal the program of #1151's seed vector, byte for byte, through #1152's compiler.
    seed = next(v for v in json.loads(VECTORS.read_text())["vectors"] if v["name"] == "seed")
    compiled = irr.compile_program(
        [irr.LineSpec.from_row(r) for r in lines], [irr.ScheduleSpec.from_row(r) for r in slots], irr.site_values()
    )
    assert compiled.sha256 == seed["sha256"]
    assert compiled.record == SEED_RECORD
    assert irr.unpack_program(compiled.record).model_dump(mode="json") == seed["program"]
    assert compiled.slot_ids == (1, 2, 3) and compiled.not_on_controller == ()


def test_slot_ids_start_at_four_and_are_never_reused(database):
    async def scenario():
        conn = await connect(MCP)
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                first = await conn.fetchval(NEW_SLOT, "L01", 21, "clock", 450, 600, 1, 0, False, "mon-wed-fri")
                await conn.execute("DELETE FROM public.irrigation_slot WHERE slot_id = $1", first)
                second = await conn.fetchval(NEW_SLOT, "L01", 21, "clock", 450, 600, 1, 0, False, "again")
                explicit = []
                for sql in (
                    "INSERT INTO public.irrigation_slot (slot_id, line_id, days, start_kind, start_min, on_s, "
                    "source, updated_by) VALUES (5, 'L01', 1, 'clock', 0, 60, 'agent:test', 'vd38')",
                    "UPDATE public.irrigation_slot SET slot_id = 9 WHERE slot_id = 1",
                ):
                    try:
                        async with conn.transaction():
                            await conn.execute(sql)
                        explicit.append(OK)
                    except asyncpg.PostgresError as exc:
                        explicit.append(exc.sqlstate)
                return first, second, explicit
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    first, second, explicit = run(scenario())
    assert first >= 4 and second > first  # a deleted schedule's id is never handed out again
    assert explicit == ["428C9", "428C9"]  # GENERATED ALWAYS: no caller picks or changes an id


# ── Grants ──────────────────────────────────────────────────────────────────


def writer_statements(login: str):
    """What the dashboard and MCP logins try, in order (see writer_expected)."""
    check_cols = "checked_by = $1, checked_at = now(), check_note = 'flushed the line'"
    return [
        *read_statements((*TABLES, "v_irrigation_line_state", "v_water_event_attribution")),
        (NEW_SLOT, "L07", 127, "sunrise", 15, 60, 8, 60, False, "dawn drench"),
        ("UPDATE public.irrigation_slot SET enabled = false, updated_by = 'vd38' WHERE slot_id = 1",),
        ("DELETE FROM public.irrigation_slot WHERE slot_id = 3",),
        (NEW_COMMAND,),
        (
            "UPDATE public.irrigation_line SET enabled = true, vpd_member = true, schedule_member = true, "
            "daily_cap_min = 40, vpd_reserve_min = 10, paused_until = now() + interval '1 day', manual = true, "
            "source = 'dashboard', updated_by = 'jason', updated_at = now(), reason = 'bring-up' WHERE line_id = 'L07'",
        ),
        (f"UPDATE public.irrigation_line SET {check_cols} WHERE line_id = 'L01'", login),
        ("UPDATE public.irrigation_line SET kind = 'drip' WHERE line_id = 'L07'",),
        ("UPDATE public.irrigation_line SET equipment = 'fog' WHERE line_id = 'L07'",),
        (
            "INSERT INTO public.irrigation_line (line_id, name, zone, kind, relay, equipment, device_idx, source, "
            "updated_by) VALUES ('L12', 'x', 'wall', 'drip', 'x', 'x', 11, 'system', 'x')",
        ),
        ("DELETE FROM public.irrigation_line WHERE line_id = 'L11'",),
        ("UPDATE public.irrigation_command SET status = 'sent', sent_at = now()",),
        (NEW_LEDGER,),
        (NEW_PUSH, hashlib.sha256(SEED_RECORD).hexdigest(), SEED_RECORD),
        ("TRUNCATE public.irrigation_slot",),
        ("INSERT INTO public.setpoint_changes (ts, parameter, value, source) VALUES (now(), 'x', 1, 'manual')",),
        ("SELECT * FROM public.control_experiments LIMIT 1",),
        ("SELECT * FROM public.fn_mcp_armed_assignment_context()",),
    ]


def writer_expected(may_check: bool):
    return [
        *[OK] * 7,  # the five tables, the line state and v_water_event_attribution
        OK,  # a schedule
        OK,  # edit a schedule
        OK,  # delete a schedule
        OK,  # a run request
        OK,  # the editable line columns and their attribution
        OK if may_check else DENIED,  # only a person records a check (D-IRP-07)
        DENIED,  # topology
        DENIED,
        DENIED,  # no new lines
        DENIED,  # no deleted lines
        DENIED,  # only the ingestor marks commands
        DENIED,  # only the ingestor records outcomes
        DENIED,  # and pushes
        DENIED,  # no TRUNCATE
        DENIED,  # nothing on setpoint_changes
        DENIED,
        DENIED,  # the armed-assignment context is the ingestor's (and 259's MCP grant, below)
    ]


def test_dashboard_login_reads_writes_and_records_checks(database):
    assert run(attempts(DASHBOARD, writer_statements(DASHBOARD))) == writer_expected(may_check=True)


def test_mcp_login_reads_and_writes_but_never_records_a_check(database):
    expected = writer_expected(may_check=False)
    expected[-1] = OK  # 259 already granted the MCP duty its armed-assignment context
    assert run(attempts(MCP, writer_statements(MCP))) == expected


def test_ingestor_records_outcomes_and_asks_whether_an_experiment_is_armed(database):
    command = "00000000-0000-4000-8000-000000000383"
    statements = [
        *read_statements((*TABLES, "v_irrigation_line_state")),
        ("SELECT * FROM public.v_water_event_attribution LIMIT 1",),
        (
            "UPDATE public.irrigation_command SET status = 'sent', sent_at = now(), fail_reason = NULL, "
            "args = '{\"minutes\": 5}' WHERE command_id = $1",
            uuid.UUID(command),
        ),
        ("UPDATE public.irrigation_command SET kind = 'stop' WHERE command_id = $1", uuid.UUID(command)),
        (NEW_LEDGER,),
        (
            "UPDATE public.irrigation_ledger SET ended_at = now(), open_s = 180, pulses = 4, outcome = 'ok', "
            "reason = NULL, event_seq = 8 WHERE event_seq = 7",
        ),
        ("UPDATE public.irrigation_ledger SET cause = 'vpd'",),
        ("DELETE FROM public.irrigation_ledger",),
        (NEW_PUSH, hashlib.sha256(SEED_RECORD).hexdigest(), SEED_RECORD),
        (
            "UPDATE public.irrigation_push SET pushed_at = now(), confirmed_at = now(), reject_code = NULL "
            "WHERE sha256 = $1",
            hashlib.sha256(SEED_RECORD).hexdigest(),
        ),
        ("UPDATE public.irrigation_push SET bytes = bytes",),
        (NEW_SLOT, "L01", 1, "clock", 0, 60, 1, 0, False, "x"),
        ("UPDATE public.irrigation_slot SET enabled = false",),
        ("DELETE FROM public.irrigation_slot",),
        ("UPDATE public.irrigation_line SET enabled = true WHERE line_id = 'L07'",),
        (NEW_COMMAND,),
        ("SELECT * FROM public.fn_mcp_armed_assignment_context()",),
        ("SELECT * FROM public.control_experiments LIMIT 1",),
        ("INSERT INTO public.setpoint_changes (ts, parameter, value, source) VALUES (now(), 'x', 1, 'manual')",),
    ]
    assert run(attempts(INGESTOR, statements)) == [
        *[OK] * 6,
        DENIED,  # per-run gallons are the readers' (dashboard and MCP)
        OK,  # mark a command sent, with its args
        DENIED,
        OK,  # record an occurrence
        OK,  # and its outcome
        DENIED,
        DENIED,
        OK,  # record a push
        OK,  # and its confirmation
        DENIED,
        DENIED,  # the ingestor cannot write irrigation_slot
        DENIED,
        DENIED,
        DENIED,  # nor a line
        DENIED,  # nor request commands
        OK,  # Iris's irrigation context: is an experiment armed (SECURITY DEFINER)
        DENIED,  # 283 grants no SELECT on control_experiments
        DENIED,  # this grant set reaches nothing on setpoint_changes
    ]


def test_api_login_reaches_nothing_new(database):
    statements = [
        *read_statements((*TABLES, "v_irrigation_line_state", "v_water_event_attribution")),
        ("SELECT * FROM public.fn_mcp_armed_assignment_context()",),
    ]
    assert run(attempts(API, statements)) == [DENIED] * len(statements)


def test_grants_are_exactly_the_listed_ones(database):
    async def fetch():
        conn = await connect()
        try:
            relations = await conn.fetch(
                "SELECT c.relname, a.grantee::regrole::text AS grantee, a.privilege_type FROM pg_class c "
                "CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE c.relnamespace = 'public'::regnamespace "
                "AND (c.relname LIKE 'irrigation%' OR c.relname IN ('v_irrigation_line_state', "
                "'v_irrigation_minutes_daily', 'v_water_event_attribution')) AND a.grantee <> c.relowner"
            )
            columns = await conn.fetch(
                "SELECT c.relname, t.attname, a.grantee::regrole::text AS grantee, a.privilege_type FROM pg_attribute t "
                "JOIN pg_class c ON c.oid = t.attrelid CROSS JOIN LATERAL aclexplode(t.attacl) a "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relname LIKE 'irrigation%' AND t.attnum > 0"
            )
            functions = await conn.fetch(
                "SELECT p.proname, a.grantee::regrole::text AS grantee FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.oid IN "
                "('public.fn_irrigation_minutes_daily(date, date)'::regprocedure, "
                "'public.fn_mcp_armed_assignment_context()'::regprocedure) AND a.grantee <> p.proowner"
            )
            return relations, columns, functions
        finally:
            await conn.close()

    relations, columns, functions = run(fetch())
    dash, mcp, ingestor = "verdify_dashboard_runtime", "verdify_mcp_runtime", "verdify_ingestor_runtime"
    readable = (*TABLES, "v_irrigation_line_state")
    expected = {(r, d, "SELECT") for r in (*readable, "v_water_event_attribution") for d in (dash, mcp)}
    expected |= {(r, ingestor, "SELECT") for r in readable}
    expected |= {("irrigation_slot", d, p) for d in (dash, mcp) for p in ("INSERT", "UPDATE", "DELETE")}
    expected |= {("irrigation_command", d, "INSERT") for d in (dash, mcp)}
    expected |= {("irrigation_push", ingestor, "INSERT"), ("irrigation_ledger", ingestor, "INSERT")}
    expected |= {("irrigation_slot_slot_id_seq", d, "USAGE") for d in (dash, mcp)}
    expected |= {(s, ingestor, "USAGE") for s in ("irrigation_push_revision_seq", "irrigation_ledger_id_seq")}
    # 281's readers of the minutes view, unchanged.
    expected |= {("v_irrigation_minutes_daily", d, "SELECT") for d in (mcp, ingestor)}
    assert {(r["relname"], r["grantee"], r["privilege_type"]) for r in relations} == expected
    editable = (
        "enabled", "vpd_member", "schedule_member", "daily_cap_min", "vpd_reserve_min", "paused_until", "manual",
        "source", "updated_by", "updated_at", "reason",
    )  # fmt: skip
    expected_columns = {("irrigation_line", c, d, "UPDATE") for c in editable for d in (dash, mcp)}
    expected_columns |= {("irrigation_line", c, dash, "UPDATE") for c in ("checked_by", "checked_at", "check_note")}
    expected_columns |= {
        ("irrigation_command", c, ingestor, "UPDATE") for c in ("status", "sent_at", "fail_reason", "args")
    }
    expected_columns |= {
        ("irrigation_push", c, ingestor, "UPDATE") for c in ("pushed_at", "confirmed_at", "reject_code")
    }
    expected_columns |= {
        ("irrigation_ledger", c, ingestor, "UPDATE")
        for c in ("ended_at", "open_s", "pulses", "outcome", "reason", "event_seq")
    }
    assert {(r["relname"], r["attname"], r["grantee"], r["privilege_type"]) for r in columns} == expected_columns
    assert sorted((r["proname"], r["grantee"]) for r in functions) == [
        ("fn_irrigation_minutes_daily", ingestor),
        ("fn_irrigation_minutes_daily", mcp),
        ("fn_mcp_armed_assignment_context", ingestor),
        ("fn_mcp_armed_assignment_context", mcp),
    ]


def test_dashboard_login_still_attests_with_the_irrigation_grants(database):
    async def check():
        conn = await connect(DASHBOARD)
        try:
            return await conn.fetchval("SELECT verdify_dashboard_runtime.fn_attest_login()")
        finally:
            await conn.close()

    assert run(check()) is True


# ── Record rules ────────────────────────────────────────────────────────────

SLOT = (
    "INSERT INTO public.irrigation_slot (line_id, days, start_kind, start_min, on_s, count, gap_s, prewet_s, "
    "flush_s, degraded_only, label, source, updated_by) VALUES ({}, 'agent:test', 'vd38')"
)
RULES = {
    "slot_on_l03": SLOT.format("'L03', 127, 'clock', 420, 600, 1, 0, 0, 0, false, NULL"),
    "slot_on_l11": SLOT.format("'L11', 127, 'clock', 420, 60, 1, 0, 0, 0, false, NULL"),
    "degraded_only_on_l01": SLOT.format("'L01', 127, 'solar_noon', 0, 600, 1, 0, 0, 0, true, NULL"),
    "degraded_only_on_l08": SLOT.format("'L08', 127, 'solar_noon', 0, 45, 1, 30, 0, 0, true, NULL"),
    "clock_1440": SLOT.format("'L01', 127, 'clock', 1440, 600, 1, 0, 0, 0, false, NULL"),
    "clock_negative": SLOT.format("'L01', 127, 'clock', -1, 600, 1, 0, 0, 0, false, NULL"),
    "solar_241": SLOT.format("'L04', 127, 'sunrise', 241, 45, 1, 30, 0, 0, false, NULL"),
    "solar_minus_241": SLOT.format("'L04', 127, 'sunset', -241, 45, 1, 30, 0, 0, false, NULL"),
    "start_kind": SLOT.format("'L01', 127, 'midnight', 0, 600, 1, 0, 0, 0, false, NULL"),
    "days_0": SLOT.format("'L01', 0, 'clock', 420, 600, 1, 0, 0, 0, false, NULL"),
    "days_128": SLOT.format("'L01', 128, 'clock', 420, 600, 1, 0, 0, 0, false, NULL"),
    "on_s_9": SLOT.format("'L01', 127, 'clock', 420, 9, 1, 0, 0, 0, false, NULL"),
    "on_s_7201": SLOT.format("'L01', 127, 'clock', 420, 7201, 1, 0, 0, 0, false, NULL"),
    "count_61": SLOT.format("'L04', 127, 'clock', 420, 45, 61, 60, 0, 0, false, NULL"),
    "gap_s_601": SLOT.format("'L04', 127, 'clock', 420, 45, 2, 601, 0, 0, false, NULL"),
    "prewet_s_1801": SLOT.format("'L02', 127, 'clock', 420, 300, 1, 0, 1801, 0, false, NULL"),
    "flush_s_1801": SLOT.format("'L02', 127, 'clock', 420, 300, 1, 0, 0, 1801, false, NULL"),
    "label_61": SLOT.format("'L01', 127, 'clock', 420, 600, 1, 0, 0, 0, false, repeat('x', 61)"),
    "reason_501": "UPDATE public.irrigation_slot SET reason = repeat('x', 501) WHERE slot_id = 1",
    "cap_481": "UPDATE public.irrigation_line SET daily_cap_min = 481 WHERE line_id = 'L01'",
    "reserve_over_cap": "UPDATE public.irrigation_line SET vpd_reserve_min = 121 WHERE line_id = 'L04'",
    "reserve_negative": "UPDATE public.irrigation_line SET vpd_reserve_min = -1 WHERE line_id = 'L04'",
    "vpd_member_on_l01": "UPDATE public.irrigation_line SET vpd_member = true WHERE line_id = 'L01'",
    "schedule_member_on_l08": "UPDATE public.irrigation_line SET schedule_member = false WHERE line_id = 'L08'",
    "vpd_member_null_on_l07": "UPDATE public.irrigation_line SET vpd_member = NULL WHERE line_id = 'L07'",
    "check_without_when": "UPDATE public.irrigation_line SET checked_by = 'jason' WHERE line_id = 'L01'",
    "note_without_check": "UPDATE public.irrigation_line SET check_note = 'x' WHERE line_id = 'L01'",
    "note_501": (
        "UPDATE public.irrigation_line SET checked_by = 'jason', checked_at = now(), check_note = repeat('x', 501) "
        "WHERE line_id = 'L01'"
    ),
    "line_reason_501": "UPDATE public.irrigation_line SET reason = repeat('x', 501) WHERE line_id = 'L01'",
    "command_kind": (
        "INSERT INTO public.irrigation_command (kind, source, actor) VALUES ('flush', 'dashboard', 'jason')"
    ),
    "command_status": (
        "INSERT INTO public.irrigation_command (kind, status, source, actor) VALUES ('stop', 'done', 'dashboard', 'x')"
    ),
    "command_reason_501": (
        "INSERT INTO public.irrigation_command (kind, source, actor, reason) "
        "VALUES ('stop', 'dashboard', 'jason', repeat('x', 501))"
    ),
    "ledger_cause": (
        "INSERT INTO public.irrigation_ledger (line_id, local_day, cause) VALUES ('L01', current_date, 'manual')"
    ),
    "ledger_outcome": (
        "INSERT INTO public.irrigation_ledger (line_id, local_day, cause, outcome) "
        "VALUES ('L01', current_date, 'run_now', 'done')"
    ),
    "ledger_event_seq": (
        "INSERT INTO public.irrigation_ledger (line_id, local_day, cause, event_seq) "
        "VALUES ('L01', current_date, 'run_now', 65536)"
    ),
    "ledger_open_s": (
        "INSERT INTO public.irrigation_ledger (line_id, local_day, cause, open_s) "
        "VALUES ('L01', current_date, 'run_now', -1)"
    ),
    "push_length": (
        "INSERT INTO public.irrigation_push (sha256, bytes) "
        "VALUES (encode(sha256('\\x00'::bytea), 'hex'), '\\x00'::bytea)"
    ),
    "push_hash": (
        "INSERT INTO public.irrigation_push (sha256, bytes) VALUES (repeat('0', 64), decode(repeat('00', 584), 'hex'))"
    ),
}


@pytest.mark.parametrize("name", sorted(RULES))
def test_each_record_rule_is_checked(database, name):
    # As the owner, so each refusal is the rule's, not a missing privilege.
    async def attempt():
        conn = await connect()
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE verdify")
                try:
                    async with conn.transaction():
                        await conn.execute(RULES[name])
                    return OK
                except asyncpg.PostgresError as exc:
                    return exc.sqlstate
        finally:
            await conn.close()

    assert run(attempt()) == CHECK, name


def test_the_mcp_login_hits_the_acceptance_checks(database):
    """A slot on L03 and a degraded_only slot on L01 fail their CHECKs (acceptance)."""
    statements = [
        (NEW_SLOT, "L03", 127, "clock", 420, 600, 1, 0, False, None),
        (NEW_SLOT, "L01", 127, "solar_noon", 0, 600, 1, 0, True, None),
        (NEW_SLOT, "L09", 127, "solar_noon", 60, 45, 4, 60, True, "west fallback"),
    ]
    assert run(attempts(MCP, statements)) == [CHECK, CHECK, OK]


def test_cause_and_outcome_checks_equal_the_schema_enums(database):
    async def fetch():
        conn = await connect()
        try:
            return {
                r["column"]: r["definition"]
                for r in await conn.fetch(
                    "SELECT a.attname AS column, pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                    "WHERE c.conrelid = 'public.irrigation_ledger'::regclass AND c.contype = 'c' "
                    "AND a.attname IN ('cause', 'outcome')"
                )
            }
        finally:
            await conn.close()

    definitions = run(fetch())
    assert set(re.findall(r"'(\w+)'::text", definitions["cause"])) == {c.value for c in irr.IrrigationCause}
    assert set(re.findall(r"'(\w+)'::text", definitions["outcome"])) == {o.value for o in irr.RunOutcome}


def test_ledger_occurrences_are_unique_and_run_now_rows_are_not(database):
    occurrence = (
        "INSERT INTO public.irrigation_ledger (line_id, slot_id, local_day, cause) VALUES ('L04', 2, '2026-11-02', $1)"
    )
    run_now = "INSERT INTO public.irrigation_ledger (line_id, local_day, cause) VALUES ('L01', '2026-11-02', 'run_now')"

    async def scenario():
        conn = await connect()
        try:
            outcomes = []
            tr = conn.transaction()
            await tr.start()
            try:
                for sql, *args in ((occurrence, "fallback"), (occurrence, "fallback"), (occurrence, "schedule"),
                                   (run_now,), (run_now,)):  # fmt: skip
                    try:
                        async with conn.transaction():
                            await conn.execute(sql, *args)
                        outcomes.append(OK)
                    except asyncpg.PostgresError as exc:
                        outcomes.append(exc.sqlstate)
            finally:
                await tr.rollback()
            return outcomes
        finally:
            await conn.close()

    assert run(scenario()) == [OK, UNIQUE, OK, OK, OK]


# ── The line-state view ─────────────────────────────────────────────────────


def test_line_state_reads_the_newest_relay_edge_and_the_raw_irr_text(database):
    async def scenario():
        conn = await connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                before = {r["line_id"]: r for r in await conn.fetch("SELECT * FROM public.v_irrigation_line_state")}
                await conn.execute(
                    "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) VALUES "
                    "(now() - interval '300 seconds', 'drip_wall', false, 'vallery'), "
                    "(now() - interval '90 seconds', 'drip_wall', true, 'vallery'), "
                    "(now() - interval '45 seconds', 'mister_west', true, 'vallery'), "
                    "(now() - interval '45 seconds', 'mister_west', false, 'vallery'), "
                    "(now() - interval '20 seconds', 'fog', false, NULL)"
                )
                await conn.execute(
                    "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) VALUES "
                    "(now() - interval '600 seconds', 'irr_state', 'v=hhh', 'vallery'), "
                    "(now() - interval '30 seconds', 'irr_state', 'd=0,run_now,0,1793664000,1793664300 v=hhh', 'vallery'), "
                    "(now() - interval '12 seconds', 'irr_events', '41,3,2,20394,ok', 'vallery'), "
                    "(now() - interval '5 seconds', 'irr_program_id', 'h=0000000000000000 src=rom', 'vallery')"
                )
                after = {r["line_id"]: r for r in await conn.fetch("SELECT * FROM public.v_irrigation_line_state")}
                now = await conn.fetchval("SELECT now()")
                return before, after, now
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    before, after, now = run(scenario())
    assert sorted(after) == list(SEEDS) and len(after) == 11
    assert all(r["irr_state"] is None and r["irr_events"] is None for r in before.values())
    wall = after["L01"]
    assert (wall["relay_on"], wall["relay_at"], wall["relay_age_s"]) == (True, now - dt.timedelta(seconds=90), 90)
    assert (after["L09"]["relay_on"], after["L09"]["relay_age_s"]) == (True, 45)  # any row on at the newest instant
    assert (after["L11"]["relay_on"], after["L11"]["relay_age_s"]) == (False, 20)  # a NULL greenhouse is vallery's
    for row in after.values():
        assert row["irr_state"] == "d=0,run_now,0,1793664000,1793664300 v=hhh" and row["irr_state_age_s"] == 30
        assert row["irr_events"] == "41,3,2,20394,ok" and row["irr_events_age_s"] == 12
        assert row["irr_events_at"] == now - dt.timedelta(seconds=12)
        assert {k: row[k] for k, _ in LINE_COLUMNS} == {k: before[row["line_id"]][k] for k, _ in LINE_COLUMNS}


# ── Minutes: cause and slot from the ledger ─────────────────────────────────


async def snapshot(conn, sql="SELECT * FROM public.v_irrigation_minutes_daily", *args):
    return {tuple(r) for r in await conn.fetch(sql, *args)}


def by_line(rows, day):
    out: dict = {}
    for r in rows:
        if r[1] == day:
            out.setdefault(r[3], []).append(r)
    return {line: sorted(found, key=lambda r: (r[4], r[6] or 0)) for line, found in out.items()}


def test_runs_with_no_ledger_row_or_command_keep_m4s_rows(database):
    async def compare():
        conn = await connect()
        try:
            async with conn.transaction():
                inputs = await support.read_inputs(conn)
                return await snapshot(conn), support.expected_minutes(*inputs)
        finally:
            await conn.close()

    rows, expected = run(compare())
    assert rows == expected  # 281's topology cause and a NULL slot for every run
    assert {r[4] for r in rows} == {"unknown", "feed", "vpd"} and {r[6] for r in rows} == {None}


EDGES = "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) VALUES ($1, $2, $3, 'vallery')"
LEDGER = (
    "INSERT INTO public.irrigation_ledger (line_id, slot_id, local_day, cause, started_at, ended_at, outcome) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7)"
)
COMMAND = (
    "INSERT INTO public.irrigation_command (kind, line_id, status, source, actor, created_at, sent_at) "
    "VALUES ($1, $2, $3, 'dashboard', 'jason', $4, $4)"
)


async def scenario_rows(edges=(), ledger=(), commands=(), queries=(), statements=()):
    """Add rows and run each write (sql, *args) in ``statements``, then read the view and each
    (sql, *args) in ``queries``, all in the same rolled-back transaction."""
    conn = await connect()
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            for ts, relay, state in edges:
                await conn.execute(EDGES, ts, relay, state)
            for row in ledger:
                await conn.execute(LEDGER, *row)
            for kind, line, status, sent in commands:
                await conn.execute(COMMAND, kind, line, status, sent)
            for sql, *args in statements:
                await conn.execute(sql, *args)
            return [await snapshot(conn), *[await snapshot(conn, sql, *args) for sql, *args in queries]]
        finally:
            await tr.rollback()
    finally:
        await conn.close()


def test_l01_run_10_s_after_a_bridge_command_reads_run_now_and_60_s_after_reads_unknown(database):
    # 2026-09-01 is a complete day with every relay off (M4's fixture).
    day = DAY(2026, 9, 1)
    first, second = local("2026-09-01 07:00:00"), local("2026-09-01 12:00:00")
    edges = [
        (first + dt.timedelta(seconds=10), "drip_wall", True),
        (first + dt.timedelta(seconds=10, minutes=5), "drip_wall", False),
        (second + dt.timedelta(seconds=60), "drip_wall", True),
        (second + dt.timedelta(seconds=60, minutes=3), "drip_wall", False),
        # Only a sent run command on L01 counts: a pending one, a stop and a run on another line do not.
        (local("2026-09-01 15:00:05"), "drip_wall", True),
        (local("2026-09-01 15:02:05"), "drip_wall", False),
    ]
    commands = [
        ("run", "L01", "sent", first),
        ("run", "L01", "sent", second),
        ("run", "L01", "pending", local("2026-09-01 15:00:00")),
        ("stop", "L01", "sent", local("2026-09-01 15:00:00")),
        ("run", "L05", "sent", local("2026-09-01 15:00:00")),
    ]
    view, reader = run(
        scenario_rows(
            edges,
            commands=commands,
            queries=[("SELECT * FROM public.fn_irrigation_minutes_daily($1, $1)", day)],
        )
    )
    wall = by_line(view, day)["L01"]
    assert [r[4:] for r in wall] == [
        ("run_now", "clean", None, 5.0, 1, None, False),
        ("unknown", "clean", None, 5.0, 2, None, False),  # 60 s after, and the 15:00 run with no sent run command
    ]
    assert {r for r in reader} == {r for r in view if r[1] == day}


def test_ledger_rows_give_cause_and_slot_and_others_keep_the_topology_cause(database):
    day = DAY(2026, 9, 1)
    t = local
    edges = [
        # L04: a fallback window holds two pulses; the third pulse is outside it (VPD).
        (t("2026-09-01 12:01:00"), "mister_center", True), (t("2026-09-01 12:01:45"), "mister_center", False),
        (t("2026-09-01 12:03:00"), "mister_center", True), (t("2026-09-01 12:03:45"), "mister_center", False),
        (t("2026-09-01 12:30:00"), "mister_center", True), (t("2026-09-01 12:31:00"), "mister_center", False),
        # A wall feed: prewet on L01, the feed on L02 with L03 open, the flush on L01.
        (t("2026-09-01 08:00:00"), "drip_wall", True), (t("2026-09-01 08:02:00"), "drip_wall", False),
        (t("2026-09-01 08:02:00"), "fert_master_valve", True), (t("2026-09-01 08:02:00"), "drip_wall_fert", True),
        (t("2026-09-01 08:07:00"), "drip_wall_fert", False), (t("2026-09-01 08:07:00"), "fert_master_valve", False),
        (t("2026-09-01 08:07:00"), "drip_wall", True), (t("2026-09-01 08:11:00"), "drip_wall", False),
        # L07: a still-open schedule window (ended_at NULL) holds this pulse.
        (t("2026-09-01 18:00:30"), "mister_south", True), (t("2026-09-01 18:01:00"), "mister_south", False),
        # L09: a row whose end never arrived, started more than 24 h before, claims nothing.
        (t("2026-09-01 20:00:00"), "mister_west", True), (t("2026-09-01 20:01:00"), "mister_west", False),
    ]  # fmt: skip
    ledger = [
        ("L04", 2, day, "fallback", t("2026-09-01 12:00:30"), t("2026-09-01 12:10:00"), "short"),
        ("L01", 7, day, "prewet", t("2026-09-01 07:59:59"), t("2026-09-01 08:02:00"), "ok"),
        ("L02", 7, day, "feed", t("2026-09-01 08:01:59"), t("2026-09-01 08:07:00"), "ok"),
        ("L01", 7, day, "flush", t("2026-09-01 08:06:59"), t("2026-09-01 08:11:00"), "ok"),
        ("L07", 4, day, "schedule", t("2026-09-01 18:00:00"), None, None),
        ("L09", 5, DAY(2026, 8, 31), "schedule", t("2026-08-31 19:00:00"), None, None),
        # Skipped occurrences never opened a valve and match nothing.
        ("L04", 1, day, "fallback", None, None, "skipped"),
    ]
    view, reader = run(
        scenario_rows(edges, ledger, queries=[("SELECT * FROM public.fn_irrigation_minutes_daily($1, $1)", day)])
    )
    lines = by_line(view, day)
    assert [r[4:] for r in lines["L04"]] == [
        ("fallback", "clean", 2, 1.5, 2, None, False),
        ("vpd", "clean", None, 1.0, 1, None, False),
    ]
    assert [r[4:] for r in lines["L01"]] == [
        ("flush", "clean", 7, 4.0, 1, None, False),
        ("prewet", "clean", 7, 2.0, 1, None, False),
    ]
    assert [r[4:] for r in lines["L02"]] == [("feed", "fert", 7, 5.0, 1, None, False)]
    # L03 has no ledger row of its own: its runs keep 281's cause, and zone supply.
    assert [r[2:] for r in lines["L03"]] == [("supply", "L03", "feed", "fert", None, 5.0, 1, None, False)]
    assert [r[4:7] for r in lines["L07"]] == [("schedule", "clean", 4)]
    assert [r[4:7] for r in lines["L09"]] == [("vpd", "clean", None)]
    assert reader == {r for r in view if r[1] == day}


def test_the_newest_containing_row_wins_the_bridge_window_is_inclusive_and_greenhouses_stay_apart(database):
    """When several ledger rows on a line contain a run's start, the newest started one gives its
    cause and slot, an earlier row left open included; a run starting exactly 30 s after a sent
    bridge command is run_now and 31 s after is not; another greenhouse's rows match nothing."""
    day = DAY(2026, 9, 1)
    t = local
    edges = [
        # L04: an earlier window left open (its outcome never arrived) and a newer closed one.
        (t("2026-09-01 10:05:00"), "mister_center", True), (t("2026-09-01 10:05:45"), "mister_center", False),
        (t("2026-09-01 11:00:00"), "mister_center", True), (t("2026-09-01 11:00:45"), "mister_center", False),
        # L05: touching occurrences; a run starting on the boundary takes the newer one.
        (t("2026-09-01 12:30:00"), "drip_center", True), (t("2026-09-01 12:40:00"), "drip_center", False),
        # L01: 30 s and 31 s after a sent bridge command.
        (t("2026-09-01 06:00:30"), "drip_wall", True), (t("2026-09-01 06:02:30"), "drip_wall", False),
        (t("2026-09-01 06:30:31"), "drip_wall", True), (t("2026-09-01 06:32:31"), "drip_wall", False),
        # Runs that only another greenhouse's occurrence and bridge command would hold.
        (t("2026-09-01 14:10:00"), "mister_south", True), (t("2026-09-01 14:10:45"), "mister_south", False),
        (t("2026-09-01 16:00:10"), "drip_wall", True), (t("2026-09-01 16:01:10"), "drip_wall", False),
    ]  # fmt: skip
    ledger = [
        ("L04", 21, day, "schedule", t("2026-09-01 09:00:00"), None, None),
        ("L04", 22, day, "schedule", t("2026-09-01 10:00:00"), t("2026-09-01 10:10:00"), "ok"),
        ("L05", 31, day, "schedule", t("2026-09-01 12:00:00"), t("2026-09-01 12:30:00"), "ok"),
        ("L05", 32, day, "schedule", t("2026-09-01 12:30:00"), t("2026-09-01 13:00:00"), "ok"),
    ]
    commands = [("run", "L01", "sent", t("2026-09-01 06:00:00")), ("run", "L01", "sent", t("2026-09-01 06:30:00"))]
    elsewhere = [
        (
            "INSERT INTO public.irrigation_ledger (greenhouse_id, line_id, slot_id, local_day, cause, started_at, "
            "ended_at) VALUES ('elsewhere', 'L07', 41, $1, 'schedule', $2, $3)",
            day,
            t("2026-09-01 14:00:00"),
            t("2026-09-01 15:00:00"),
        ),
        (
            "INSERT INTO public.irrigation_command (greenhouse_id, kind, line_id, status, source, actor, sent_at) "
            "VALUES ('elsewhere', 'run', 'L01', 'sent', 'dashboard', 'jason', $1)",
            t("2026-09-01 16:00:00"),
        ),
    ]
    view, reader = run(
        scenario_rows(
            edges,
            ledger,
            commands,
            queries=[("SELECT * FROM public.fn_irrigation_minutes_daily($1, $1)", day)],
            statements=elsewhere,
        )
    )
    lines = by_line(view, day)
    # The 10:05 pulse is in both L04 windows and takes the newer; the 11:00 pulse only the open one.
    assert [r[4:] for r in lines["L04"]] == [
        ("schedule", "clean", 21, 0.8, 1, None, False),
        ("schedule", "clean", 22, 0.8, 1, None, False),
    ]
    assert [r[4:] for r in lines["L05"]] == [("schedule", "clean", 32, 10.0, 1, None, False)]
    assert [r[4:] for r in lines["L01"]] == [
        ("run_now", "clean", None, 2.0, 1, None, False),
        ("unknown", "clean", None, 3.0, 2, None, False),  # 31 s after, and the 16:00:10 run
    ]
    assert [r[4:] for r in lines["L07"]] == [("vpd", "clean", None, 0.8, 1, None, False)]
    assert reader == {r for r in view if r[1] == day}


def test_the_reader_attributes_runs_already_on_at_its_first_day_as_the_view_does(database):
    t = local
    edges = [
        # A bridge run from 23:58:10 to 00:05 with a same-state snapshot at 23:59: the reader for
        # 09-03 must start it at 23:58:10, 10 s after the command, not at the snapshot.
        (t("2026-09-02 23:58:10"), "drip_wall", True), (t("2026-09-02 23:59:00"), "drip_wall", True),
        (t("2026-09-03 00:05:00"), "drip_wall", False),
        # A conflicting instant at 23:00 restarts the L05 run, which the 22:59:30 occurrence holds.
        (t("2026-09-02 22:00:00"), "drip_center", True), (t("2026-09-02 23:00:00"), "drip_center", True),
        (t("2026-09-02 23:00:00"), "drip_center", False), (t("2026-09-03 00:30:00"), "drip_center", False),
    ]  # fmt: skip
    ledger = [("L05", 9, DAY(2026, 9, 2), "schedule", t("2026-09-02 22:59:30"), t("2026-09-03 00:30:00"), "ok")]
    commands = [("run", "L01", "sent", t("2026-09-02 23:58:00"))]
    queries = [
        ("SELECT * FROM public.fn_irrigation_minutes_daily($1, $1)", DAY(2026, 9, 3)),
        ("SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", DAY(2026, 9, 2), DAY(2026, 9, 3)),
    ]
    view, reader_day, reader_both = run(scenario_rows(edges, ledger, commands, queries))
    third = by_line(view, DAY(2026, 9, 3))
    assert [r[4:] for r in third["L01"]] == [("run_now", "clean", None, 5.0, 0, None, False)]
    assert [r[4:] for r in third["L05"]] == [("schedule", "clean", 9, 30.0, 0, None, False)]
    second = by_line(view, DAY(2026, 9, 2))
    assert [r[4:] for r in second["L05"]] == [
        ("schedule", "clean", 9, 60.0, 1, None, False),
        ("unknown", "clean", None, 60.0, 1, None, False),
    ]
    assert reader_day == {r for r in view if r[1] == DAY(2026, 9, 3)}
    assert reader_both == {r for r in view if r[1] in (DAY(2026, 9, 2), DAY(2026, 9, 3))}


def test_the_reader_equals_the_view_for_any_range_with_program_rows(database):
    t = local
    edges = [
        (t("2026-10-31 23:40:00"), "drip_wall", True), (t("2026-11-01 00:20:00"), "drip_wall", False),
    ]  # fmt: skip
    ledger = [("L01", 11, DAY(2026, 10, 31), "schedule", t("2026-10-31 23:39:59"), t("2026-11-01 00:20:00"), "ok")]

    async def compare():
        conn = await connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                for ts, relay, state in edges:
                    await conn.execute(EDGES, ts, relay, state)
                for row in ledger:
                    await conn.execute(LEDGER, *row)
                today = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
                out = []
                for low, high in (
                    (DAY(2026, 5, 27), DAY(2026, 7, 11)),
                    (DAY(2026, 9, 19), DAY(2026, 9, 21)),
                    (DAY(2026, 10, 31), DAY(2026, 11, 1)),
                    (DAY(2026, 11, 1), today),
                    (DAY(2025, 1, 1), today),
                ):
                    view = await snapshot(
                        conn,
                        "SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN $1 AND $2",
                        low,
                        high,
                    )
                    reader = await snapshot(conn, "SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", low, high)
                    out.append((low, high, view, reader))
                return out
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    out = run(compare())
    for low, high, view, reader in out:
        assert view == reader, (low, high)
    november = {r for *_, view, _ in out for r in view if r[1] == DAY(2026, 11, 1) and r[3] == "L01"}
    assert {r[4:8] for r in november} == {("schedule", "clean", 11, 20.0)}


# ── Timing ──────────────────────────────────────────────────────────────────


def p95(samples):
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_production_sized_thirty_day_read_stays_under_two_seconds_at_p95(database):
    """M4's timing case, kept for the redefinition, with a ledger and bridge commands at program density.

    Two years of edges and meter events at the April-July 2026 density; every run probes the
    ledger and every L01 run the commands. API-IRR-10 reads fn_irrigation_minutes_daily, whose
    30-day p95 is the gate; the view's time is reported.
    """

    async def scenario():
        conn = await connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                for table in ("equipment_state", "water_meter_events", "data_gaps", "irrigation_command"):
                    await conn.execute(f"DELETE FROM public.{table}")
                now = await conn.fetchval("SELECT now()")
                edges, events, gaps = support.production_sized_rows(now)
                ledger, commands = support.production_sized_ledger(edges)
                await support.load_production_sized(conn, edges, events, gaps)
                await support.load_production_sized_ledger(conn, ledger, commands)
                today = support.local_day(now)
                low = today - dt.timedelta(days=29)
                function, view = [], []
                for _ in range(20):
                    started = time.perf_counter()
                    rows = await conn.fetch("SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", low, today)
                    function.append(time.perf_counter() - started)
                for _ in range(3):
                    started = time.perf_counter()
                    view_rows = await conn.fetch(
                        "SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN $1 AND $2", low, today
                    )
                    view.append(time.perf_counter() - started)
                started = time.perf_counter()
                long_rows = await conn.fetch(
                    "SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", today - dt.timedelta(days=399), today
                )
                long_read = time.perf_counter() - started
                causes = {r["cause"] for r in long_rows}
                return (
                    len(edges),
                    len(ledger),
                    len(commands),
                    {tuple(r) for r in rows},
                    {tuple(r) for r in view_rows},
                    function,
                    view,
                    long_rows,
                    long_read,
                    causes,
                )
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    edges, ledger, commands, rows, view_rows, function, view, long_rows, long_read, causes = run(scenario())
    report = (
        f"hypertable={database} edges={edges} ledger={ledger} commands={commands} "
        f"function_p95={p95(function):.3f}s view_max={max(view):.3f}s read_400d={long_read:.3f}s"
    )
    print(report)
    assert edges > 200_000 and ledger > 2_500 and commands > 10, report
    assert rows == view_rows and len(rows) > 100, report
    assert {"vpd", "schedule", "feed", "run_now"} <= causes, report
    assert len({r[1] for r in long_rows}) == 399, report
    assert p95(function) < 2.0, report
    assert long_read < 10.0, report
