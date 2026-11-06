"""API-22: the grow lights by day for Graphs → Lighting (#1357).

One entry per America/Denver day the window touches, up to now, for each circuit (main and grow):

- ``on_s`` and ``starts``: the Lutron readback in ``equipment_state`` over the whole day (seeded with
  ``fn_equip_at`` at the first day's midnight; no row yet reads off), as ``mv_equipment_runtime_daily`` counts them.
- the policy the controller ran that day: its readback (``setpoint_snapshot``) of ``gl_<light>_lux_threshold``,
  ``_lux_hysteresis``, ``_sunrise_hour``, ``_sunset_hour`` (the cutoff) and ``_target_light_minutes``, in force at the
  day's end (or now). ``lux_off`` is the threshold plus the hysteresis.
- ``qualified_min``: ``v_lighting_minutes_status_now``'s rule for any day, not only today. Each minute from the start
  hour to the cutoff (or now) counts once when the minute's mean Tempest outdoor lux is at or above the ON threshold or
  the light was on during it. The decision reads the Tempest, never the dead indoor sensor, so a minute with no outdoor
  reading counts only if the light was on.

Everything is read from tables the dashboard login already reads (no view or grant of its own: the lighting views take
30 s for a few days and are not granted), in one read-only transaction with the 10 s statement timeout. DLI has no
source until the PAR meter (#1184; ADR-0008), so it is not here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from verdify_services import series

Light = Literal["main", "grow"]
LIGHTS: tuple[tuple[Light, str, str], ...] = (("main", "grow_light_main", "Main"), ("grow", "grow_light_grow", "Grow"))
# The policy each day carries, by the readback parameter's suffix (gl_<light>_<suffix>).
POLICY_SUFFIXES: Mapping[str, str] = {
    "lux_on": "lux_threshold",
    "hysteresis": "lux_hysteresis",
    "start_hour": "sunrise_hour",
    "cutoff_hour": "sunset_hour",
    "target_min": "target_light_minutes",
}
# The controller re-publishes its readback every minute or so; a value older than this at the day's end is no value.
POLICY_LOOKBACK = timedelta(days=2)
MINUTE = timedelta(minutes=1)

# The readback in force at each day's end, per parameter: idx_snapshot_param (parameter, ts DESC) answers each probe.
POLICY_SQL = (
    "SELECT d.day, p.parameter, (SELECT s.value FROM public.setpoint_snapshot s "
    "WHERE s.parameter = p.parameter AND s.ts <= d.at AND s.ts > d.at - $3::interval "
    "ORDER BY s.ts DESC LIMIT 1) AS value "
    "FROM unnest($1::date[], $2::timestamptz[]) AS d(day, at) CROSS JOIN unnest($4::text[]) AS p(parameter)"
)
# The minutes whose mean Tempest lux reaches the lowest ON threshold of the window; the rest never qualify by light.
LUX_SQL = (
    "SELECT time_bucket('1 minute', ts) AS minute, avg(outdoor_lux) AS lux FROM public.climate "
    "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz AND outdoor_lux IS NOT NULL "
    "GROUP BY 1 HAVING avg(outdoor_lux) >= $3::double precision"
)


class LightingDay(BaseModel):
    date: date
    partial: bool
    on_s: int
    starts: int
    start_hour: int | None
    cutoff_hour: int | None
    lux_on: float | None
    lux_off: float | None
    target_min: int | None
    window_min: int | None
    qualified_min: int | None


class LightingCircuit(BaseModel):
    light: Light
    equipment: str
    label: str
    days: list[LightingDay]


class LightingDays(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    circuits: list[LightingCircuit]


def parameter(light: str, field: str) -> str:
    return f"gl_{light}_{POLICY_SUFFIXES[field]}"


def local_hour(day: date, hour: int) -> datetime:
    """The wall-clock hour of an America/Denver day, in UTC (24 is the next midnight)."""
    extra, hour = divmod(hour, 24)
    target = day + timedelta(days=extra)
    return datetime(target.year, target.month, target.day, hour, tzinfo=series.DENVER).astimezone(UTC)


def on_spans(
    seed: bool | None, rows: Iterable[tuple[datetime, bool | None]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime, bool]]:
    """The light's runs over [start, end): seeded at ``start``, equal states collapsed; no state yet reads off."""
    timeline = [(start, bool(seed)), *((ts, bool(state)) for ts, state in rows if start <= ts < end)]
    return [(span.start, span.end, span.state) for span in series.lane_runs(timeline, end) if span.end > span.start]


def day_facts(
    spans: Sequence[tuple[datetime, datetime, bool]],
    lux: Mapping[datetime, float],
    policy: Mapping[str, float | None],
    day: date,
    now: datetime,
) -> LightingDay:
    """One circuit's day: on time and starts over the whole day, and the qualified minutes inside its window."""
    day_start, day_end = series.denver_midnight(day), series.denver_midnight(day + timedelta(days=1))
    until = min(day_end, now)
    on_s = 0.0
    starts = 0
    for index, (low, high, state) in enumerate(spans):
        if not state:
            continue
        on_s += max(0.0, (min(high, until) - max(low, day_start)).total_seconds())
        if index > 0 and day_start <= low < until and not spans[index - 1][2]:
            starts += 1

    def whole(field: str) -> int | None:
        value = policy.get(field)
        return None if value is None else int(round(value))

    lux_on, hysteresis = policy.get("lux_on"), policy.get("hysteresis")
    start_hour, cutoff_hour = whole("start_hour"), whole("cutoff_hour")
    window_min = qualified_min = None
    if lux_on is not None and start_hour is not None and cutoff_hour is not None:
        window_start, window_end = local_hour(day, start_hour), min(local_hour(day, cutoff_hour), until)
        window_min = qualified_min = 0
        lit = [(low, high) for low, high, state in spans if state and high > window_start and low < window_end]
        minute = window_start
        while minute < window_end:
            window_min += 1
            natural = lux.get(minute, 0.0) >= lux_on
            if natural or any(low < minute + MINUTE and high > minute for low, high in lit):
                qualified_min += 1
            minute += MINUTE
    return LightingDay(
        date=day,
        partial=until < day_end,
        on_s=int(round(on_s)),
        starts=starts,
        start_hour=start_hour,
        cutoff_hour=cutoff_hour,
        lux_on=lux_on,
        lux_off=None if lux_on is None or hysteresis is None else lux_on + hysteresis,
        target_min=whole("target_min"),
        window_min=window_min,
        qualified_min=qualified_min,
    )


def window_days(from_: datetime, end: datetime) -> list[date]:
    """The America/Denver days that [from_, end) touches."""
    return (
        [start.astimezone(series.DENVER).date() for start in series.denver_day_starts(from_, end)]
        if from_ < end
        else []
    )


async def lighting_days(
    conn: asyncpg.Connection, from_: datetime, to: datetime, *, now: datetime | None = None
) -> LightingDays:
    """API-22 ``GET /v1/lighting``: each circuit's days over [from_, to), at most 31 days, up to now."""
    now = series._now(now)
    from_, to = series._validate_window(from_, to, series.MAX_STATES_WINDOW)
    days = window_days(from_, min(to, now))
    circuits: list[LightingCircuit] = []
    policy: dict[tuple[date, str], float | None] = {}
    seeds: dict[str, bool | None] = {}
    rows: dict[str, list[tuple[datetime, bool | None]]] = {}
    lux: dict[datetime, float] = {}
    if days:
        first = series.denver_midnight(days[0])
        end = min(series.denver_midnight(days[-1] + timedelta(days=1)), now)
        ats = [min(series.denver_midnight(day + timedelta(days=1)), now) for day in days]
        names = [parameter(light, field) for light, _, _ in LIGHTS for field in POLICY_SUFFIXES]
        equipment = [name for _, name, _ in LIGHTS]
        async with series._read_transaction(conn):
            for row in await conn.fetch(POLICY_SQL, days, ats, POLICY_LOOKBACK, names):
                policy[(row["day"], row["parameter"])] = series._number(row["value"])
            seeds = {row["equipment"]: row["state"] for row in await conn.fetch(series.SEED_SQL, equipment, first)}
            for row in await conn.fetch(series.EQUIPMENT_ROWS_SQL, equipment, first, end):
                rows.setdefault(row["equipment"], []).append((row["ts"], row["state"]))
            thresholds = [
                value for (_, name), value in policy.items() if name.endswith("_lux_threshold") and value is not None
            ]
            if thresholds:
                for row in await conn.fetch(LUX_SQL, first, end, min(thresholds)):
                    if (value := series._number(row["lux"])) is not None:
                        lux[row["minute"]] = value
        for light, name, label in LIGHTS:
            spans = on_spans(seeds.get(name), rows.get(name, []), first, end)
            circuits.append(
                LightingCircuit(
                    light=light,
                    equipment=name,
                    label=label,
                    days=[
                        day_facts(
                            spans,
                            lux,
                            {field: policy.get((day, parameter(light, field))) for field in POLICY_SUFFIXES},
                            day,
                            now,
                        )
                        for day in days
                    ],
                )
            )
    else:
        circuits = [LightingCircuit(light=light, equipment=name, label=label, days=[]) for light, name, label in LIGHTS]
    return LightingDays(from_=from_, to=to, circuits=circuits)
