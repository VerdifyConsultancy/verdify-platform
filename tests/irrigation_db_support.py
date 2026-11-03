"""Database helpers for the irrigation minutes and tables tests (VD-39, #1154; VD-38, #1153).

``tests/test_dashboard_db_irrigation_minutes.py`` (M4) and
``tests/test_dashboard_db_irrigation_tables.py`` (M3) use them on the private
PostgreSQL that jvallery/agents#5994's campaign step starts; the irrigation
reads (#1155) reuse the schema subset, the role setup, M3's setup and the
production-sized generators for their own timing cases.

Nothing here reaches production: every connection comes from
``isolated_mutation_test_dsn()`` (a loopback ``verdify_test_*`` database).
"""

from __future__ import annotations

import ast
import bisect
import datetime as dt
import random
import re
import sys
import types
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"
MINUTES_MIGRATION = REPO_ROOT / "db" / "migrations" / "281-irrigation-minutes-by-zone.sql"
DAYS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "irrigation_minutes_days.sql"
DENVER = ZoneInfo("America/Denver")
UTC = dt.UTC

# Relay slug -> (line, zone, chemistry, cause): irrigation §3 and the M4 map.
LINE_MAP = {
    "drip_wall": ("L01", "wall", "clean", "unknown"),
    "drip_wall_fert": ("L02", "wall", "fert", "feed"),
    "fert_master_valve": ("L03", "supply", "fert", "feed"),
    "mister_center": ("L04", "center", "clean", "vpd"),
    "drip_center": ("L05", "center", "clean", "unknown"),
    "drip_center_fert": ("L06", "center", "fert", "unknown"),
    "mister_south": ("L07", "south", "clean", "vpd"),
    "mister_south_fert": ("L08", "south", "fert", "unknown"),
    "mister_west": ("L09", "west", "clean", "vpd"),
    "mister_west_fert": ("L10", "west", "fert", "unknown"),
    "fog": ("L11", "fog", "clean", "vpd"),
}
RELAYS = tuple(LINE_MAP)

# The production objects M4 reads, taken verbatim from db/schema.sql (pg_dump
# section headers), in dependency order. Foreign keys and triggers are left out.
BASE_OBJECTS = (
    ("greenhouses", "TABLE"),
    ("greenhouses greenhouses_pkey", "CONSTRAINT"),
    ("equipment_state", "TABLE"),
    ("equipment_state_ts_idx", "INDEX"),
    ("idx_equipment_state_equip", "INDEX"),
    ("idx_equipment_state_greenhouse_equipment_ts", "INDEX"),
    ("idx_equipment_state_ts", "INDEX"),
    ("climate", "TABLE"),
    ("climate_ts_idx", "INDEX"),
    ("idx_climate_ts", "INDEX"),
    ("data_gaps", "TABLE"),
    ("data_gaps_id_seq", "SEQUENCE"),
    ("data_gaps_id_seq", "SEQUENCE OWNED BY"),
    ("data_gaps id", "DEFAULT"),
    ("data_gaps data_gaps_pkey", "CONSTRAINT"),
    ("water_meter_events", "TABLE"),
    ("water_meter_events_id_seq", "SEQUENCE"),
    ("water_meter_events_id_seq", "SEQUENCE OWNED BY"),
    ("water_meter_events id", "DEFAULT"),
    ("water_meter_events water_meter_events_pkey", "CONSTRAINT"),
    ("water_meter_events water_meter_events_ts_source_meter_id_event_type_key", "CONSTRAINT"),
    ("idx_water_meter_events_day", "INDEX"),
    ("idx_water_meter_events_greenhouse_ts", "INDEX"),
    ("idx_water_meter_events_ts", "INDEX"),
    ("v_water_relay_runs", "VIEW"),
    ("v_water_event_attribution", "VIEW"),
    ("v_water_run_accounting", "VIEW"),
)
DUTIES = ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_mcp_runtime")
_HEADER = re.compile(
    r"^--\n-- Name: (?P<name>.+?); Type: (?P<type>.+?); Schema: (?P<schema>.+?); Owner: .*\n--\n", re.M
)


def schema_sections(text: str | None = None) -> dict[tuple[str, str, str], str]:
    """Split a pg_dump schema into its sections, keyed by (name, type, schema)."""
    text = SCHEMA_SQL.read_text() if text is None else text
    heads = list(_HEADER.finditer(text))
    sections = {}
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        key = (head["name"], head["type"], head["schema"])
        assert key not in sections, key
        sections[key] = text[head.end() : end].replace("\\unrestrict", "-- unrestrict")
    return sections


def base_schema_sql() -> str:
    sections = schema_sections()
    return "\n".join(sections[(name, kind, "public")] for name, kind in BASE_OBJECTS)


def migration_body(path: Path = MINUTES_MIGRATION) -> str:
    """The statements between a delivered file's predecessor and successor blocks."""
    text = path.read_text()
    start = text.index("END $predecessor$;") + len("END $predecessor$;")
    end = text.index("DO $successor$")
    return text[start:end]


ROLES_SQL = """
DO $roles$
DECLARE duty text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        CREATE ROLE verdify NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    -- 217's and 259's shape: a NOLOGIN NOINHERIT duty and an INHERIT login
    -- that is its only member.
    FOREACH duty IN ARRAY ARRAY['verdify_api_runtime', 'verdify_ingestor_runtime', 'verdify_mcp_runtime'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = duty) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOREPLICATION NOBYPASSRLS', duty);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = duty || '_login') THEN
            EXECUTE format('CREATE ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOREPLICATION NOBYPASSRLS', duty || '_login');
            EXECUTE format('GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET TRUE', duty, duty || '_login');
        END IF;
    END LOOP;
END
$roles$;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public AUTHORIZATION verdify;
GRANT USAGE ON SCHEMA public TO verdify_api_runtime, verdify_ingestor_runtime, verdify_mcp_runtime;
"""


async def prepare_database(conn, *, hypertable: bool = True, migration: Path = MINUTES_MIGRATION) -> bool:
    """Owner, duties, the schema subset and the migration body, as the owner.

    Returns whether equipment_state became a TimescaleDB hypertable (seven-day
    chunks, like production) - only where the extension is installable.
    """
    await conn.execute(ROLES_SQL)
    timescale = False
    if hypertable:
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            timescale = True
        except Exception:  # noqa: BLE001 - a plain PostgreSQL keeps plain tables
            timescale = False
    await conn.execute("SET ROLE verdify")
    try:
        await conn.execute(base_schema_sql())
        if timescale:
            await conn.execute(
                "SELECT public.create_hypertable('public.equipment_state', public.by_range('ts', interval '7 days'))"
            )
        await conn.execute("INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery greenhouse')")
        await conn.execute(migration_body(migration))
    finally:
        await conn.execute("RESET ROLE")
    return timescale


# ── M3 (VD-38, #1153): the irrigation tables on top of M4 ──────────────────────

DASHBOARD_MIGRATION = REPO_ROOT / "db" / "migrations" / "282-dashboard-runtime-login.sql"
TABLES_MIGRATION = REPO_ROOT / "db" / "migrations" / "283-irrigation-tables.sql"
MCP_BOUNDARY_MIGRATION = REPO_ROOT / "db" / "migrations" / "259-mcp-ordinary-runtime-boundary.sql"
# What M3's line-state view reads and what the logins must stay off, from db/schema.sql.
M3_OBJECTS = (
    ("system_state", "TABLE"),
    ("system_state_ts_idx", "INDEX"),
    ("setpoint_changes", "TABLE"),
    ("setpoint_plan", "TABLE"),
)
# The dump predates these; production owns each with no grant to the logins tested here.
# 259's armed-assignment function reads the two experiment tables (production has more
# columns), and 282's attestor checks the dashboard login holds nothing on all five.
M3_STAND_INS = """
CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY, greenhouse_id text NOT NULL, status text NOT NULL, armed_at timestamptz);
CREATE TABLE public.control_assignments (
    assignment_id uuid PRIMARY KEY, experiment_id uuid NOT NULL, status text NOT NULL,
    valid_range tstzrange NOT NULL);
CREATE TABLE public.effective_policy_vectors (id bigint PRIMARY KEY);
CREATE TABLE public.policy_delivery_outbox (id bigint PRIMARY KEY);
CREATE TABLE public.policy_exposures (id bigint PRIMARY KEY);
"""


def armed_context_sql() -> str:
    """259's fn_mcp_armed_assignment_context(), its REVOKE and its MCP grant, verbatim."""
    text = MCP_BOUNDARY_MIGRATION.read_text()
    start = text.index("CREATE FUNCTION public.fn_mcp_armed_assignment_context()")
    end = text.index("TO verdify_mcp_runtime;", start) + len("TO verdify_mcp_runtime;")
    return text[start:end]


async def prepare_irrigation_tables(conn, *, hypertable: bool = True) -> bool:
    """M4's database (``prepare_database``), what M3 reads, then 282's and 283's bodies.

    PUBLIC gets USAGE on public, as production's schema gives it (282's login relies on it).
    282 creates roles, so its body runs as the connecting superuser; 283's runs as the owner
    under the runner's search_path. Returns whether equipment_state is a hypertable.
    """
    timescale = await prepare_database(conn, hypertable=hypertable)
    sections = schema_sections()
    await conn.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    await conn.execute("SET ROLE verdify")
    try:
        await conn.execute("\n".join(sections[(name, kind, "public")] for name, kind in M3_OBJECTS))
        await conn.execute(M3_STAND_INS)
        await conn.execute(armed_context_sql())
    finally:
        await conn.execute("RESET ROLE")
    await conn.execute(migration_body(DASHBOARD_MIGRATION))
    await conn.execute("SET ROLE verdify")
    try:
        await conn.execute("SET search_path = pg_catalog, public, pg_temp;\n" + migration_body(TABLES_MIGRATION))
    finally:
        await conn.execute("RESET search_path")
        await conn.execute("RESET ROLE")
    return timescale


def load_irrigation_task() -> dict:
    """ingestor/tasks/irrigation.py's own source as a namespace, without the ingestor's device imports (``esp32_push``,
    ``shared``, ``.dispatcher``). Executed as a real module, so its dataclasses resolve their annotations."""
    task = REPO_ROOT / "ingestor" / "tasks" / "irrigation.py"
    body = [
        node
        for node in ast.parse(task.read_text()).body
        if not (isinstance(node, ast.Import) and {alias.name for alias in node.names} & {"esp32_push", "shared"})
        and not (isinstance(node, ast.ImportFrom) and node.level)
    ]
    module = types.ModuleType("irrigation_task_under_test")
    sys.modules[module.__name__] = module
    exec(compile(ast.Module(body=body, type_ignores=[]), str(task), "exec"), module.__dict__)  # noqa: S102
    return module.__dict__


# ── Independent edge walk: the expected rows, computed in plain Python ─────────


def _seconds(delta: dt.timedelta) -> Decimal:
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)


def _round(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def local_day(ts: dt.datetime) -> dt.date:
    return ts.astimezone(DENVER).date()


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Local midnight to local midnight; never 24 hours added to a timestamp."""
    start = dt.datetime.combine(day, dt.time(), DENVER).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), DENVER).astimezone(UTC)
    return start, end


def walk_runs(edges, now: dt.datetime) -> list[tuple[str, str, dt.datetime, dt.datetime]]:
    """Runs from (greenhouse, relay, ts, state) edges, as migration 194 defines them.

    Rows sharing a timestamp collapse to "any on"; a change of state, the first
    row or a same-timestamp conflict starts a segment; an open run ends at now.
    """
    grouped: dict = defaultdict(lambda: defaultdict(list))
    for greenhouse, relay, ts, state in edges:
        grouped[(greenhouse, relay)][ts].append(state)
    runs = []
    for (greenhouse, relay), by_ts in grouped.items():
        changed, prior = [], None
        for ts in sorted(by_ts):
            states = by_ts[ts]
            state, conflicting = any(states), len(set(states)) > 1
            if prior is None or state != prior or conflicting:
                changed.append((ts, state))
            prior = state
        for index, (ts, state) in enumerate(changed):
            end = changed[index + 1][0] if index + 1 < len(changed) else now
            if state and end > ts:
                runs.append((greenhouse, relay, ts, end))
    return runs


def coverage_days(first_row: dt.datetime, newest_climate: dt.datetime | None, gaps, now: dt.datetime) -> dict:
    """Each local day's coverage from the first wet-relay row to today.

    With no climate row at all nothing was recorded, so every day is no_data.
    """
    today, newest = local_day(now), (local_day(newest_climate) if newest_climate else None)
    days, day = {}, local_day(first_row)
    while day <= today:
        start, end = day_bounds(day)
        if newest is None or day > newest or any(a <= start and b >= end for a, b in gaps):
            days[day] = "no_data"
        elif day == today or day == newest or any(a < end and b > start for a, b in gaps):
            days[day] = "partial"
        else:
            days[day] = "complete"
        day += dt.timedelta(days=1)
    return days


def expected_minutes(edges, events, gaps, newest_climate, now) -> set[tuple]:
    """The v_irrigation_minutes_daily rows the spec gives for these rows.

    ``events`` holds accepted deltas only: (greenhouse, ts, prior_ts, delta_gal,
    attribution_class). Gallons use the unbounded overlap rule of
    v_water_run_accounting, independent of the view's five-minute index bound.
    """
    edges = list(edges)
    runs = walk_runs(edges, now)
    coverage = coverage_days(min(ts for _, _, ts, _ in edges), newest_climate, gaps, now)
    delta_days = {(greenhouse, local_day(ts)) for greenhouse, ts, *_ in events}
    groups: dict = defaultdict(lambda: {"open_s": Decimal(0), "pulses": 0, "gal": None})
    for greenhouse, relay, start, end in runs:
        run_gal = None
        if relay != "fert_master_valve":
            overlapping = [e for e in events if e[0] == greenhouse and e[1] > start and e[2] is not None and e[2] < end]
            if overlapping:
                run_gal = _round(
                    sum((Decimal(str(e[3])) for e in overlapping if e[4] == "meter_attributed"), Decimal(0)), "0.001"
                )
        day = local_day(start)
        while day <= local_day(end):
            day_start, day_end = day_bounds(day)
            open_s = _seconds(min(end, day_end) - max(start, day_start))
            if open_s > 0:
                group = groups[(greenhouse, day, relay)]
                group["open_s"] += open_s
                if day == local_day(start):
                    group["pulses"] += 1
                    if run_gal is not None:
                        group["gal"] = (group["gal"] or Decimal(0)) + run_gal
            day += dt.timedelta(days=1)
    rows = set()
    for (greenhouse, day, relay), group in groups.items():
        state = coverage.get(day)
        if state in (None, "no_data"):
            continue
        line, zone, chemistry, cause = LINE_MAP[relay]
        if line == "L03":
            gal = None
        elif group["gal"] is not None:
            gal = int(_round(group["gal"], "1"))
        else:
            gal = 0 if (greenhouse, day) in delta_days else None
        minutes = float(_round(group["open_s"] / Decimal(60), "0.1"))
        rows.add(
            (greenhouse, day, zone, line, cause, chemistry, None, minutes, group["pulses"], gal, state != "complete")
        )
    return rows


async def read_inputs(conn):
    """The rows the view reads, for the edge walk: same transaction, same now()."""
    now = await conn.fetchval("SELECT now()")
    edges = [
        tuple(r)
        for r in await conn.fetch(
            "SELECT COALESCE(greenhouse_id, 'vallery'), equipment, ts, state FROM public.equipment_state "
            "WHERE equipment = ANY($1::text[])",
            list(RELAYS),
        )
    ]
    events = [
        tuple(r)
        for r in await conn.fetch(
            "SELECT greenhouse_id, ts, prior_ts, delta_gal, COALESCE(attribution_class, 'manual_or_unattributed') "
            "FROM public.water_meter_events WHERE event_type = 'delta' AND quality_flag = 'ok'"
        )
    ]
    gaps = [tuple(r) for r in await conn.fetch("SELECT start_ts, end_ts FROM public.data_gaps")]
    newest = await conn.fetchval("SELECT max(ts) FROM public.climate")
    return edges, events, gaps, newest, now


# ── Production-sized fixture: two years at the April-July 2026 density ─────────

# Daily pulses and pulse length (s) per misting relay, from production
# equipment_state 2026-04-15..07-15 (mister_center 131 rows/day, fog 95,
# mister_south 68, mister_west 31). Each drip and fert relay logs ~3 snapshot
# rows a day and runs every ~10 days; the master valve every other day.
MIST_DENSITY = {
    "mister_center": (65, 30, 90),
    "fog": (47, 60, 180),
    "mister_south": (32, 30, 90),
    "mister_west": (14, 30, 90),
}
QUIET_RELAYS = (
    "drip_wall",
    "drip_wall_fert",
    "drip_center",
    "drip_center_fert",
    "mister_south_fert",
    "mister_west_fert",
)
ACCEPTED_DELTAS_PER_DAY = 172  # water_meter_events delta/ok, 2026-05..07
PHANTOM_ZEROS_PER_DAY = 74


def production_sized_rows(end: dt.datetime, days: int = 730, seed: int = 1154):
    """Deterministic equipment_state, water_meter_events and data_gaps rows.

    Returns (edges, events, gaps) as tuples in the column orders of
    ``load_production_sized``: about 250k edges and 180k meter events for the
    ``days`` whole local days before ``end``'s, so no row is in the future.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not a secret
    edges, events, gaps = [], [], []
    first = local_day(end) - dt.timedelta(days=days)
    event_id = 1
    for offset in range(days):
        day_start, day_end = day_bounds(first + dt.timedelta(days=offset))
        span = (day_end - day_start).total_seconds()
        busy = []
        for relay, (pulses, low, high) in MIST_DENSITY.items():
            cursor = day_start
            for second in sorted(rng.uniform(0, span - high - 10) for _ in range(pulses)):
                start = max(day_start + dt.timedelta(seconds=second), cursor + dt.timedelta(seconds=5))
                stop = start + dt.timedelta(seconds=rng.uniform(low, high))
                if stop >= day_end:
                    break
                edges += [(start, relay, True, "vallery"), (stop, relay, False, "vallery")]
                busy.append((start, stop))
                cursor = stop
        for relay in QUIET_RELAYS:
            for _ in range(3):
                edges.append((day_start + dt.timedelta(seconds=rng.uniform(0, span)), relay, False, "vallery"))
            if rng.random() < 0.1:
                start = day_start + dt.timedelta(seconds=rng.uniform(21600, 64800))
                stop = start + dt.timedelta(minutes=rng.uniform(2, 10))
                edges += [(start, relay, True, "vallery"), (stop, relay, False, "vallery")]
                busy.append((start, stop))
        for _ in range(4):
            edges.append(
                (day_start + dt.timedelta(seconds=rng.uniform(0, span)), "fert_master_valve", False, "vallery")
            )
        if offset % 2 == 0:
            start = day_start + dt.timedelta(seconds=rng.uniform(21600, 64800))
            edges += [
                (start, "fert_master_valve", True, "vallery"),
                (start + dt.timedelta(minutes=6), "fert_master_valve", False, "vallery"),
            ]
        busy.sort()
        starts = [a for a, _ in busy]
        for _ in range(ACCEPTED_DELTAS_PER_DAY):
            ts = day_start + dt.timedelta(seconds=rng.uniform(300, span))
            prior = ts - dt.timedelta(seconds=rng.uniform(5, 240))
            index = bisect.bisect_left(starts, ts)
            hit = any(prior < b for _, b in busy[max(0, index - 8) : index])
            klass = (
                "meter_attributed"
                if hit and rng.random() < 0.6
                else ("ambiguous_overlap" if hit else "manual_or_unattributed")
            )
            events.append((event_id, ts, "vallery", "delta", "ok", prior, float(rng.choice((1, 1, 1, 2))), klass))
            event_id += 1
        for _ in range(PHANTOM_ZEROS_PER_DAY):
            ts = day_start + dt.timedelta(seconds=rng.uniform(0, span))
            events.append((event_id, ts, "vallery", "phantom_zero", "phantom_zero", None, 0.0, None))
            event_id += 1
        gap_start = day_start + dt.timedelta(seconds=rng.uniform(0, span - 900))
        gaps.append((offset + 1, gap_start, gap_start + dt.timedelta(seconds=rng.uniform(130, 600))))
    # Meter rows are unique on (ts, source, meter_id, event_type).
    unique = {}
    for row in events:
        unique.setdefault((row[1], row[3]), row)
    return edges, list(unique.values()), gaps


async def load_production_sized(conn, edges, events, gaps) -> None:
    """COPY the generated rows in and refresh planner statistics (same transaction)."""
    await conn.copy_records_to_table(
        "equipment_state", schema_name="public", columns=["ts", "equipment", "state", "greenhouse_id"], records=edges
    )
    await conn.copy_records_to_table(
        "water_meter_events",
        schema_name="public",
        columns=[
            "id",
            "ts",
            "greenhouse_id",
            "event_type",
            "quality_flag",
            "prior_ts",
            "delta_gal",
            "attribution_class",
        ],
        records=events,
    )
    await conn.copy_records_to_table(
        "data_gaps",
        schema_name="public",
        columns=["id", "start_ts", "end_ts", "duration_s"],
        records=[(i, a, b, (b - a).total_seconds()) for i, a, b in gaps],
    )
    await conn.execute("ANALYZE public.equipment_state; ANALYZE public.water_meter_events; ANALYZE public.data_gaps")


# Production-sized program rows (M3, #1153): the ledger and bridge commands the
# redefined minutes view and reader probe for every run. Slot ids per line for the
# generated occurrences; each (slot, local day, cause) occurs at most once.
LEDGER_SLOTS = {
    "drip_wall": 10,
    "drip_wall_fert": 11,
    "drip_center": 12,
    "drip_center_fert": 13,
    "mister_south_fert": 14,
    "mister_west_fert": 15,
    "mister_center": 20,
}
LEDGER_CAUSES = {"drip_wall": "schedule", "drip_center": "schedule"}  # the fertilizer lines feed


def production_sized_ledger(edges, seed: int = 1153):
    """irrigation_ledger and irrigation_command rows for ``production_sized_rows``' edges.

    Each day: an L04 schedule window from 5 s before the day's first center pulse to 10
    min after it, and the three seeded fallback slots skipped without opening a valve
    (started_at NULL). Every drip and fertilizer run gets its occurrence row, except every
    other L01 run, which gets a sent run command 10 s before it (the bridge) instead.
    Returns (ledger, commands) in ``load_production_sized_ledger``'s column orders.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not a secret
    by_relay: dict = defaultdict(list)
    for ts, relay, state, _greenhouse in edges:
        by_relay[relay].append((ts, state))
    runs: dict = defaultdict(list)
    for relay, rows in by_relay.items():
        rows.sort()
        start = None
        for ts, state in rows:
            if state and start is None:
                start = ts
            elif not state and start is not None:
                runs[relay].append((start, ts))
                start = None
    ledger, commands, seq = [], [], 0
    days = sorted({local_day(ts) for ts, *_ in edges})
    first_center = {}
    for start, end in runs["mister_center"]:
        first_center.setdefault(local_day(start), (start, end))
    for day in days:
        for slot in (1, 2, 3):
            seq = (seq + 1) % 0x10000
            ledger.append(("L04", slot, day, "fallback", None, None, 0, 0, "skipped", "vok", seq, None))
        if day in first_center:
            start, _ = first_center[day]
            seq = (seq + 1) % 0x10000
            window = (start - dt.timedelta(seconds=5), start + dt.timedelta(minutes=10))
            ledger.append(
                ("L04", LEDGER_SLOTS["mister_center"], day, "schedule", *window, 600, 4, "ok", None, seq, None)
            )
    for relay, slot in LEDGER_SLOTS.items():
        if relay == "mister_center":
            continue
        for index, (start, end) in enumerate(runs[relay]):
            if relay == "drip_wall" and index % 2:
                sent = start - dt.timedelta(seconds=10)
                commands.append(
                    (
                        uuid.UUID(int=rng.getrandbits(128)),
                        "run",
                        "L01",
                        "sent",
                        "dashboard",
                        "jason",
                        sent - dt.timedelta(seconds=2),
                        sent,
                    )
                )
                continue
            seq = (seq + 1) % 0x10000
            line = LINE_MAP[relay][0]
            cause = LEDGER_CAUSES.get(relay, "feed")
            open_s = int((end - start).total_seconds())
            ledger.append(
                (
                    line,
                    slot,
                    local_day(start),
                    cause,
                    start - dt.timedelta(seconds=1),
                    end,
                    open_s,
                    1,
                    "ok",
                    None,
                    seq,
                    None,
                )
            )
    return ledger, commands


async def load_production_sized_ledger(conn, ledger, commands) -> None:
    """COPY the generated program rows in and refresh planner statistics (same transaction)."""
    await conn.copy_records_to_table(
        "irrigation_ledger",
        schema_name="public",
        columns=[
            "line_id",
            "slot_id",
            "local_day",
            "cause",
            "started_at",
            "ended_at",
            "open_s",
            "pulses",
            "outcome",
            "reason",
            "event_seq",
            "actor",
        ],
        records=ledger,
    )
    await conn.copy_records_to_table(
        "irrigation_command",
        schema_name="public",
        columns=["command_id", "kind", "line_id", "status", "source", "actor", "created_at", "sent_at"],
        records=commands,
    )
    await conn.execute("ANALYZE public.irrigation_ledger; ANALYZE public.irrigation_command")
