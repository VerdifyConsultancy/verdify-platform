"""Relay-edge runs, what caused them and how they add up (VD-40, #1155; irrigation §6.2).

A run is built as migration 194's ``v_water_relay_runs`` builds it, plus L03: rows sharing a timestamp collapse to
"any on", a change of state or a same-timestamp conflict starts a segment, and an open run ends at now. It reads only
the ``equipment_state`` rows inside a window, never the view, which cannot take a ``ts`` bound below its window
functions and would scan every edge since 2024 on each poll. A window is seeded as M3's
``fn_irrigation_minutes_daily`` seeds it: any row on at the newest timestamp before it, and a run already on starts
where the view starts it, so it joins the same ledger row and counts on the same day as API-IRR-10.

Each run takes its cause from M3's rule: the newest ``irrigation_ledger`` row on its line that started at most 24 h
before it and whose span (to now while open) contains its start; else ``run_now`` for an L01 run at most 30 s after
the ``sent_at`` of a sent run command; else M4's topology cause.

``verdify_services.reads.now`` imports this module, so nothing here imports ``verdify_services.reads``.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple

import asyncpg

from verdify_schemas.irrigation import BRIDGE_RUN_WINDOW_S, LINE_IDS, LINE_KIND, relay_cause
from verdify_services.labels import EQUIPMENT_LINE_IDS

GREENHOUSE_ID = "vallery"
LINE_RELAY: Mapping[str, str] = {line_id: relay for relay, line_id in EQUIPMENT_LINE_IDS.items()}
# API-IRR-10's zones (M4's line map): supply for L03, which no zone total counts, and fog for L11.
MINUTES_ZONE: Mapping[str, str] = {
    "L01": "wall",
    "L02": "wall",
    "L03": "supply",
    "L04": "center",
    "L05": "center",
    "L06": "center",
    "L07": "south",
    "L08": "south",
    "L09": "west",
    "L10": "west",
    "L11": "fog",
}
FERT_LINES = frozenset({"L02", "L03", "L06", "L08", "L10"})
# Lines whose runs are pulses: an episode is one line's pulses of one cause with gaps of 10 min or less.
PULSING_KINDS = frozenset({"mister", "fert_mister", "fog"})
EPISODE_GAP = timedelta(minutes=10)
LEDGER_LOOKBACK = timedelta(hours=24)
BRIDGE_WINDOW = timedelta(seconds=BRIDGE_RUN_WINDOW_S)
# An accepted delta spans at most 5 min (migration 194's materializer flags longer ones source_gap).
MAX_DELTA_SPAN = timedelta(minutes=5)
_MILLI = Decimal("0.001")
_TENTH = Decimal("0.1")
_ONE = Decimal(1)
_MICROS = timedelta(microseconds=1)


def chemistry(line_id: str) -> str:
    """``fert`` or ``clean``, as M4's line map says."""
    return "fert" if line_id in FERT_LINES else "clean"


def whole_gallons(value: Decimal | float | int) -> int:
    """Whole gallons, half up: ``round(numeric)`` in the minutes view."""
    return int(Decimal(str(value)).quantize(_ONE, rounding=ROUND_HALF_UP))


# ── Pure rules: edges to runs ───────────────────────────────────────────────


@dataclass(frozen=True)
class Edge:
    """A relay's state at one timestamp: any row on, and whether the rows there disagreed."""

    ts: datetime
    state: bool
    conflicting: bool = False


@dataclass(frozen=True)
class RelayRun:
    """One ON segment of a relay: ``end`` is its closing edge, or now while ``open``."""

    line_id: str
    start: datetime
    end: datetime
    open: bool = False


def runs_from_edges(
    line_id: str,
    seed: Edge | None,
    edges: Sequence[Edge],
    *,
    at: datetime,
    closing: datetime | None = None,
) -> list[RelayRun]:
    """The ON segments of ``seed`` then ``edges`` (oldest first), by migration 194's rules.

    ``closing`` ends a segment still on after the last edge (the first closing row past the window); with none it
    is open and ends at ``at``.
    """
    changed: list[Edge] = []
    prior: bool | None = None
    for edge in [seed, *edges] if seed is not None else edges:
        if prior is None or edge.state != prior or edge.conflicting:
            changed.append(edge)
        prior = edge.state
    runs: list[RelayRun] = []
    for index, edge in enumerate(changed):
        if not edge.state:
            continue
        following = changed[index + 1].ts if index + 1 < len(changed) else closing
        end = following if following is not None else at
        if end > edge.ts:
            runs.append(RelayRun(line_id, edge.ts, end, open=following is None))
    return runs


# ── Pure rules: causes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttributedRun:
    """A relay run with what caused it: its ledger row, or the bridge command that started it."""

    run: RelayRun
    cause: str
    ledger: Mapping[str, Any] | None = None
    bridge: Mapping[str, Any] | None = None

    @property
    def slot_id(self) -> int | None:
        return None if self.ledger is None else self.ledger["slot_id"]


class RunCause(NamedTuple):
    """``run_causes()``'s row: a wet line's run and why it ran."""

    line_id: str
    start: datetime
    end: datetime
    cause: str


def _ledger_row(rows: Sequence[Mapping[str, Any]], run: RelayRun, at: datetime) -> Mapping[str, Any] | None:
    """M3's match. ``rows`` are the line's, oldest first by (``started_at``, ``id``): the newest that started at
    most 24 h before the run and whose [``started_at``, ``ended_at`` or now] contains its start."""
    floor = run.start - LEDGER_LOOKBACK
    index = bisect.bisect_right(rows, run.start, key=lambda row: row["started_at"])
    while index > 0:
        index -= 1
        row = rows[index]
        if row["started_at"] <= floor:
            return None
        if (row["ended_at"] if row["ended_at"] is not None else at) >= run.start:
            return row
    return None


def _bridge_command(commands: Sequence[Mapping[str, Any]], run: RelayRun) -> Mapping[str, Any] | None:
    """The newest sent run command at most 30 s before an L01 run; ``commands`` are L01's, oldest first."""
    index = bisect.bisect_right(commands, run.start, key=lambda row: row["sent_at"])
    if index and run.start - commands[index - 1]["sent_at"] <= BRIDGE_WINDOW:
        return commands[index - 1]
    return None


def attribute(
    runs: Iterable[RelayRun],
    ledger_rows: Iterable[Mapping[str, Any]],
    bridge_commands: Sequence[Mapping[str, Any]],
    at: datetime,
) -> list[AttributedRun]:
    """M3's rule for each run: its ledger row, else the bridge command, else the topology cause."""
    by_line: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in sorted(ledger_rows, key=lambda row: (row["started_at"], row["id"])):
        by_line[row["line_id"]].append(row)
    out: list[AttributedRun] = []
    for run in runs:
        row = _ledger_row(by_line[run.line_id], run, at)
        if row is not None:
            out.append(AttributedRun(run, row["cause"], ledger=row))
            continue
        bridge = _bridge_command(bridge_commands, run) if run.line_id == "L01" else None
        sent_at = None if bridge is None else bridge["sent_at"]
        out.append(AttributedRun(run, relay_cause(run.line_id, run.start, sent_at).value, bridge=bridge))
    return out


# ── Pure rules: a day's minutes (M4's rules) ────────────────────────────────


class Deltas:
    """Accepted meter deltas (``v_water_event_attribution`` rows), found by overlap with a run."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self.rows = sorted(rows, key=lambda row: row["ts"])
        self._times = [row["ts"] for row in self.rows]

    def overlapping(self, start: datetime, end: datetime) -> list[Mapping[str, Any]]:
        """``ts`` after the start and ``prior_ts`` before the end, as ``v_water_run_accounting`` matches them."""
        low = bisect.bisect_right(self._times, start)
        high = bisect.bisect_right(self._times, end + MAX_DELTA_SPAN)
        return [row for row in self.rows[low:high] if row["prior_ts"] is not None and row["prior_ts"] < end]

    def any_between(self, start: datetime, end: datetime) -> bool:
        """Did the meter record a delta in [``start``, ``end``)?"""
        return bisect.bisect_left(self._times, start) < bisect.bisect_left(self._times, end)


def _attributed_gal(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    total = sum(
        (Decimal(str(row["delta_gal"])) for row in rows if row["attribution_class"] == "meter_attributed"),
        Decimal(0),
    )
    return total.quantize(_MILLI, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DayGroup:
    """One row of a day's minutes: ``v_irrigation_minutes_daily`` per line, cause and slot."""

    line_id: str
    cause: str
    slot_id: int | None
    minutes: Decimal
    pulses: int
    gal: int | None


def day_groups(
    items: Iterable[AttributedRun], day_start: datetime, day_end: datetime, deltas: Deltas
) -> list[DayGroup]:
    """A local day's rows by M4's rules, so a status read equals API-IRR-10's row for that day.

    Minutes are each run's overlap with the day, to 0.1 min. Pulses count on the start day, and so do gallons: the
    ``meter_attributed`` deltas of the runs that start that day and overlap one, whole; 0 when none of them did and
    the meter recorded a delta that day; else null. L03's water counts on the line it feeds.
    """
    open_s: dict[tuple[str, str, int | None], timedelta] = defaultdict(timedelta)
    pulses: dict[tuple[str, str, int | None], int] = defaultdict(int)
    run_gal: dict[tuple[str, str, int | None], Decimal] = {}
    for item in items:
        run = item.run
        overlap = min(run.end, day_end) - max(run.start, day_start)
        if overlap <= timedelta(0):
            continue
        key = (run.line_id, item.cause, item.slot_id)
        open_s[key] += overlap
        if day_start <= run.start < day_end:
            pulses[key] += 1
            matched = deltas.overlapping(run.start, run.end) if run.line_id != "L03" else []
            if matched:
                run_gal[key] = run_gal.get(key, Decimal(0)) + _attributed_gal(matched)
    metered_today = deltas.any_between(day_start, day_end)
    out: list[DayGroup] = []
    for key, seconds in sorted(open_s.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0)):
        line_id, cause, slot_id = key
        if line_id == "L03":
            gal = None
        elif key in run_gal:
            gal = whole_gallons(run_gal[key])
        else:
            gal = 0 if metered_today else None
        micros = Decimal(seconds // _MICROS)
        minutes = (micros / Decimal(60_000_000)).quantize(_TENTH, rounding=ROUND_HALF_UP)
        out.append(DayGroup(line_id, cause, slot_id, minutes, pulses[key], gal))
    return out


# ── Pure rules: runs as people read them ────────────────────────────────────


@dataclass(frozen=True)
class RunGroup:
    """One run on the ledger: a drip run, a fertilizer stage or a mist episode (one or more relay runs)."""

    line_id: str
    parts: tuple[AttributedRun, ...]

    @property
    def start(self) -> datetime:
        return self.parts[0].run.start

    @property
    def end(self) -> datetime:
        return self.parts[-1].run.end

    @property
    def open(self) -> bool:
        return any(part.run.open for part in self.parts)

    @property
    def open_s(self) -> int:
        return int(sum((part.run.end - part.run.start for part in self.parts), timedelta(0)).total_seconds())

    @property
    def cause(self) -> str:
        return self.parts[0].cause

    @property
    def ledger(self) -> Mapping[str, Any] | None:
        return self.parts[0].ledger

    @property
    def bridge(self) -> Mapping[str, Any] | None:
        return self.parts[0].bridge

    @property
    def pulses(self) -> int | None:
        return len(self.parts) if pulsing(self.line_id) else None


def pulsing(line_id: str) -> bool:
    """A mister, fertilizer mister or fog: its relay runs are pulses."""
    return LINE_KIND[line_id] in PULSING_KINDS


def _same_run(group: list[AttributedRun], item: AttributedRun) -> bool:
    last = group[-1]
    if last.ledger is not None or item.ledger is not None:
        return last.ledger is not None and item.ledger is not None and last.ledger["id"] == item.ledger["id"]
    return pulsing(item.run.line_id) and item.cause == last.cause and item.run.start - last.run.end <= EPISODE_GAP


def group_runs(items: Iterable[AttributedRun]) -> list[RunGroup]:
    """Relay runs as runs, by line and start: the relay runs of one ledger row are one run, and a line's unjoined
    pulses of one cause with gaps of 10 min or less are one episode. Every other relay run stands alone."""
    by_line: dict[str, list[AttributedRun]] = defaultdict(list)
    for item in items:
        by_line[item.run.line_id].append(item)
    out: list[RunGroup] = []
    for line_id, line_items in by_line.items():
        current: list[AttributedRun] = []
        for item in sorted(line_items, key=lambda part: part.run.start):
            if current and not _same_run(current, item):
                out.append(RunGroup(line_id, tuple(current)))
                current = []
            current.append(item)
        if current:
            out.append(RunGroup(line_id, tuple(current)))
    return out


def run_gallons(group: RunGroup, deltas: Deltas, meter_reported: bool) -> int | None:
    """API-IRR-11's ``gal``: the ``meter_attributed`` deltas overlapping the run, whole, each counted once; null when
    an ``ambiguous_overlap`` delta (shared with another open relay) overlaps it; with no delta, 0 while the meter
    reported and null when it did not, because unknown is never 0."""
    matched: dict[Any, Mapping[str, Any]] = {}
    for part in group.parts:
        for row in deltas.overlapping(part.run.start, part.run.end):
            matched[row["water_event_id"]] = row
    if any(row["attribution_class"] == "ambiguous_overlap" for row in matched.values()):
        return None
    if matched:
        return whole_gallons(_attributed_gal(matched.values()))
    return 0 if meter_reported else None


# ── Database (bounded; parameters only, never text from a request) ──────────

# The state each window starts from, read as v_water_relay_runs reads it: any row on at the newest timestamp before.
_SEED_SQL = """
SELECT w.equipment, s.ts, s.state, s.conflicting_state
  FROM unnest($1::text[], $2::timestamptz[]) AS w(equipment, from_ts)
 CROSS JOIN LATERAL (SELECT es.ts, bool_or(es.state) AS state, count(DISTINCT es.state) > 1 AS conflicting_state
                       FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $3
                        AND es.ts < w.from_ts
                      GROUP BY es.ts
                      ORDER BY es.ts DESC
                      LIMIT 1) AS s
"""

# Where a run already on at its window's start began, as the view starts it: the newest conflicting timestamp since
# the relay was last off, else the first timestamp after that (M3's fn_irrigation_minutes_daily).
_RUN_START_SQL = """
SELECT w.equipment, coalesce(last_conflict.ts, first_on.ts) AS run_start
  FROM unnest($1::text[], $2::timestamptz[]) AS w(equipment, from_ts)
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $3
                        AND es.ts < w.from_ts
                      GROUP BY es.ts
                     HAVING NOT bool_or(es.state)
                      ORDER BY es.ts DESC
                      LIMIT 1) AS last_off ON true
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $3
                        AND es.ts < w.from_ts AND (last_off.ts IS NULL OR es.ts > last_off.ts)
                      GROUP BY es.ts
                     HAVING count(DISTINCT es.state) > 1
                      ORDER BY es.ts DESC
                      LIMIT 1) AS last_conflict ON true
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE last_conflict.ts IS NULL
                        AND es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $3
                        AND es.ts < w.from_ts AND (last_off.ts IS NULL OR es.ts > last_off.ts)
                      ORDER BY es.ts
                      LIMIT 1) AS first_on ON true
"""

_EDGES_SQL = """
SELECT w.equipment, e.ts, e.state, e.conflicting_state
  FROM unnest($1::text[], $2::timestamptz[], $3::timestamptz[]) AS w(equipment, from_ts, to_ts)
 CROSS JOIN LATERAL (SELECT es.ts, bool_or(es.state) AS state, count(DISTINCT es.state) > 1 AS conflicting_state
                       FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $4
                        AND es.ts >= w.from_ts AND es.ts < w.to_ts
                      GROUP BY es.ts) AS e
 ORDER BY w.equipment, e.ts
"""

# The edge that closes a run still on at its window's end (M3's look-ahead).
_CLOSING_SQL = """
SELECT w.equipment, c.ts
  FROM unnest($1::text[], $2::timestamptz[]) AS w(equipment, to_ts)
 CROSS JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $3
                        AND es.ts >= w.to_ts
                      GROUP BY es.ts
                     HAVING NOT bool_or(es.state) OR count(DISTINCT es.state) > 1
                      ORDER BY es.ts
                      LIMIT 1) AS c
"""

LEDGER_COLUMNS = (
    "id, line_id, slot_id, local_day, cause, started_at, ended_at, open_s, pulses, outcome, reason, "
    "command_id, actor, recorded_at"
)
_LEDGER_SQL = f"""
SELECT {LEDGER_COLUMNS}
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND started_at > $2::timestamptz AND started_at <= $3::timestamptz
 ORDER BY started_at, id
"""

_BRIDGE_SQL = """
SELECT command_id, source, actor, sent_at
  FROM irrigation_command
 WHERE greenhouse_id = $1 AND line_id = 'L01' AND kind = 'run' AND status = 'sent'
   AND sent_at >= $2::timestamptz AND sent_at <= $3::timestamptz
 ORDER BY sent_at
"""

_DELTAS_SQL = """
SELECT water_event_id, ts, prior_ts, delta_gal, attribution_class
  FROM v_water_event_attribution
 WHERE greenhouse_id = $1 AND ts > $2::timestamptz AND ts <= $3::timestamptz
 ORDER BY ts
"""


async def relay_runs(
    conn: asyncpg.Connection, windows: Mapping[str, tuple[datetime, datetime]], at: datetime
) -> list[RelayRun]:
    """The runs of each line's relay in its window [from, to), a run already on at from or still on at to included."""
    if not windows:
        return []
    line_ids = list(windows)
    relays = [LINE_RELAY[line_id] for line_id in line_ids]
    starts = [windows[line_id][0] for line_id in line_ids]
    ends = [windows[line_id][1] for line_id in line_ids]
    seed_rows = {row["equipment"]: row for row in await conn.fetch(_SEED_SQL, relays, starts, GREENHOUSE_ID)}
    on_at_start = [(relay, start) for relay, start in zip(relays, starts, strict=True) if _on(seed_rows.get(relay))]
    run_starts: dict[str, datetime] = {}
    if on_at_start:
        rows = await conn.fetch(
            _RUN_START_SQL, [relay for relay, _ in on_at_start], [start for _, start in on_at_start], GREENHOUSE_ID
        )
        run_starts = {row["equipment"]: row["run_start"] for row in rows}
    edges: dict[str, list[Edge]] = defaultdict(list)
    for row in await conn.fetch(_EDGES_SQL, relays, starts, ends, GREENHOUSE_ID):
        edges[row["equipment"]].append(Edge(row["ts"], row["state"], row["conflicting_state"]))

    seeds: dict[str, Edge] = {}
    for relay, row in seed_rows.items():
        ts = (run_starts.get(relay) or row["ts"]) if row["state"] else row["ts"]
        seeds[relay] = Edge(ts, row["state"], row["conflicting_state"])
    still_on = []
    for relay, end in zip(relays, ends, strict=True):
        last = edges[relay][-1] if edges[relay] else seeds.get(relay)
        if end < at and last is not None and last.state:
            still_on.append((relay, end))
    closing: dict[str, datetime] = {}
    if still_on:
        rows = await conn.fetch(
            _CLOSING_SQL, [relay for relay, _ in still_on], [end for _, end in still_on], GREENHOUSE_ID
        )
        closing = {row["equipment"]: row["ts"] for row in rows}
    runs: list[RelayRun] = []
    for line_id, relay in zip(line_ids, relays, strict=True):
        runs.extend(runs_from_edges(line_id, seeds.get(relay), edges[relay], at=at, closing=closing.get(relay)))
    return runs


def _on(row: Mapping[str, Any] | None) -> bool:
    return row is not None and bool(row["state"])


async def attributed_runs(
    conn: asyncpg.Connection, windows: Mapping[str, tuple[datetime, datetime]], at: datetime
) -> list[AttributedRun]:
    """``relay_runs()`` with each run's cause, the join M3's minutes view makes."""
    runs = await relay_runs(conn, windows, at)
    if not runs:
        return []
    first = min(run.start for run in runs)
    last = max(run.start for run in runs)
    ledger = await conn.fetch(_LEDGER_SQL, GREENHOUSE_ID, first - LEDGER_LOOKBACK, last)
    bridge: Sequence[Mapping[str, Any]] = ()
    l01_starts = [run.start for run in runs if run.line_id == "L01"]
    if l01_starts:
        bridge = await conn.fetch(_BRIDGE_SQL, GREENHOUSE_ID, min(l01_starts) - BRIDGE_WINDOW, max(l01_starts))
    return attribute(runs, ledger, bridge, at)


async def deltas_between(conn: asyncpg.Connection, after: datetime, through: datetime) -> Deltas:
    """The accepted meter deltas with ``ts`` in (``after``, ``through``]."""
    return Deltas(await conn.fetch(_DELTAS_SQL, GREENHOUSE_ID, after, through))


async def run_causes(conn: asyncpg.Connection, from_: datetime, to: datetime) -> list[RunCause]:
    """(``line_id``, start, end, cause) of every wet-line run in [``from_``, ``to``): the join API-IRR-11 and the
    minutes make, for the ``cause`` of API-05's intervals (#1169) and API-02's ``running`` list. A run already on at
    ``from_`` keeps its true start, and an open run ends at now."""
    at = await conn.fetchval("SELECT now()")
    items = await attributed_runs(conn, {line_id: (from_, to) for line_id in LINE_IDS}, at)
    return [RunCause(item.run.line_id, item.run.start, item.run.end, item.cause) for item in items]
