"""Database helpers for the irrigation minutes tests (VD-39, #1154).

``tests/test_dashboard_db_irrigation_minutes.py`` uses them on the private
PostgreSQL that jvallery/agents#5994's campaign step starts; M3 (#1153) and the
irrigation reads (#1155) reuse the schema subset, the role setup and the
production-sized generator for their own timing cases.

Nothing here reaches production: every connection comes from
``isolated_mutation_test_dsn()`` (a loopback ``verdify_test_*`` database).
"""

from __future__ import annotations

import bisect
import datetime as dt
import random
import re
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
    """Each local day's coverage from the first wet-relay row to today."""
    today, newest = local_day(now), (local_day(newest_climate) if newest_climate else None)
    days, day = {}, local_day(first_row)
    while day <= today:
        start, end = day_bounds(day)
        if (newest is not None and day > newest) or any(a <= start and b >= end for a, b in gaps):
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
