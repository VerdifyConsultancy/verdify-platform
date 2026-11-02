"""Graph reads: the metric registry and API-03 to API-06 (architecture §7).

Column names never come from the browser: a request names registry keys, and only the
columns bound in ``METRICS`` reach SQL. Every hypertable query is bounded by ``ts`` and runs in
a read-only transaction with a 10 s statement timeout (the dashboard login's own limit); a
timeout is 503 ``query_timeout``. Gaps stay null, never 0. A bucket older than a key's raw
retention is beyond retention (null, count 0), which the chart labels and never counts as a
gap. An equipment state nobody knows is ``unknown``, never ``off``.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections.abc import AsyncIterator, Hashable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_serializer

from verdify_services.labels import EQUIPMENT_LABELS, LINE_IDS
from verdify_services.problems import problem
from verdify_services.reads import collapse

DENVER = ZoneInfo("America/Denver")
STATEMENT_TIMEOUT = "10s"
DAY_S = 86400
BUCKETS_S = (60, 300, 900, 3600, 10800, DAY_S)
MAX_SERIES_KEYS = 8
POINTS_MIN, POINTS_MAX, POINTS_DEFAULT = 10, 2000, 1500
MAX_WINDOW = timedelta(days=400)
DAILY_MODE_OVER = timedelta(days=31)
MAX_STATES_WINDOW = timedelta(days=31)
BAND_STEP_UNIT_S = 300
BAND_MAX_POINTS = 2000
# A band row is written every dispatcher pass (about 300 s); carry one forward at most two
# passes, so a pass that lands a second late leaves no hole in a 300 s step.
BAND_CARRY = timedelta(seconds=600)
# The controller is reporting while its newest climate row is at most 300 s old (GET /health).
CONTROLLER_REPORTING = timedelta(seconds=300)
# The Lutron readback is polled every 300 s; older than two polls means Home Assistant is down.
LUTRON_FRESH = timedelta(seconds=600)
# Raw retention (decisions Round 2b D); daily keys are kept at least as long as the widest window.
RETENTION_DAYS = {"climate": 365, "diagnostics": 180, "setpoint_snapshot": 90}
DAILY_RETENTION_DAYS = 400
# TimescaleDB aligns time_bucket(interval, timestamptz) to this Monday; API-06 steps that do
# not divide a day must use the same origin to line up with time_bucket_gapfill.
BUCKET_ORIGIN = datetime(2000, 1, 3, tzinfo=UTC)
BAND_ROLES = ("temp_low", "temp_high", "vpd_low", "vpd_high")
SCORECARD_VIEW = "v_daily_scorecard"

Provenance = Literal["MEASURED", "DERIVED", "PLANNED", "MANUAL", "AI"]
Source = Literal["raw", "daily_summary", "scorecard", "forecast"]
IrrigationCause = Literal["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"]
LaneGroup = Literal["climate", "water", "light", "flag"]
LaneState = Literal["on", "off", "unknown"]


# ─────────────────────────────────────────────────────────────── response models (OpenAPI)
class MetricDef(BaseModel):
    key: str
    label: str
    group: str
    unit: str
    cadence_s: int
    retention_days: int
    provenance: Provenance
    zone: str | None
    daily: bool
    note: str | None


class MetricList(BaseModel):
    items: list[MetricDef]


class SeriesColumns(BaseModel):
    avg: list[float | None]
    min: list[float | None]
    max: list[float | None]
    count: list[int]
    source: Source
    retained_from: datetime | None = None
    # daily.* keys only; omitted from the JSON for every other key (the schema says array).
    reason: list[str | None] = Field(default=None)

    @model_serializer(mode="wrap")
    def _omit_absent_reason(self, handler):
        data = handler(self)
        if self.reason is None:
            data.pop("reason", None)
        return data


class SeriesGap(BaseModel):
    start: datetime
    end: datetime
    reason: str | None


class Series(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    bucket_s: Literal[60, 300, 900, 3600, 10800, 86400]
    t: list[int]
    series: dict[str, SeriesColumns]
    gaps: list[SeriesGap]
    watermark: datetime | None
    server_time: datetime


class StateInterval(BaseModel):
    start: datetime
    end: datetime
    state: LaneState
    cause: IrrigationCause | None = None


class StateLane(BaseModel):
    equipment: str
    label: str
    group: LaneGroup
    line_id: str | None = None
    intervals: list[StateInterval]


class StateLanes(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    lanes: list[StateLane]


class BandColumns(BaseModel):
    temp_low: list[float | None]
    temp_high: list[float | None]
    vpd_low: list[float | None]
    vpd_high: list[float | None]


class BandTimeline(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    step_s: int
    t: list[int]
    band: BandColumns
    crop: BandColumns


# ─────────────────────────────────────────────────────────────── the metric registry (API-03)
@dataclass(frozen=True, slots=True)
class RawBinding:
    table: Literal["climate", "diagnostics"]
    column: str


@dataclass(frozen=True, slots=True)
class DailyBinding:
    """A per-day source read by ``date`` (an America/Denver day).

    ``daily_summary`` bindings name its avg, min and max columns where the summary has them.
    Scorecard bindings name their view, the value column (``avg``) and the column that says why
    a day has no value (``reason``), so a later key such as #1184's DLI can bind its own view.
    """

    view: str
    source: Literal["daily_summary", "scorecard"]
    avg: str | None = None
    min: str | None = None
    max: str | None = None
    reason: str | None = None
    stamp: str = "captured_at"


@dataclass(frozen=True, slots=True)
class MetricBinding:
    key: str
    label: str
    group: str
    unit: str
    provenance: Provenance
    raw: RawBinding | None
    daily: DailyBinding | None
    cadence_s: int
    retention_days: int
    zone: str | None = None
    note: str | None = None

    def definition(self) -> MetricDef:
        return MetricDef(
            key=self.key,
            label=self.label,
            group=self.group,
            unit=self.unit,
            cadence_s=self.cadence_s,
            retention_days=self.retention_days,
            provenance=self.provenance,
            zone=self.zone,
            daily=self.daily is not None,
            note=self.note,
        )


def _summary(avg: str | None = None, low: str | None = None, high: str | None = None) -> DailyBinding:
    return DailyBinding("daily_summary", "daily_summary", avg=avg, min=low, max=high)


def _climate(
    column: str,
    label: str,
    group: str,
    unit: str,
    *,
    provenance: Provenance = "MEASURED",
    zone: str | None = None,
    note: str | None = None,
    daily: DailyBinding | None = None,
    cadence_s: int = 60,
) -> MetricBinding:
    return MetricBinding(
        key=f"climate.{column}",
        label=label,
        group=group,
        unit=unit,
        provenance=provenance,
        raw=RawBinding("climate", column),
        daily=daily,
        cadence_s=cadence_s,
        retention_days=RETENTION_DAYS["climate"],
        zone=zone,
        note=note,
    )


def _hydro(column: str, label: str, unit: str) -> MetricBinding:
    return _climate(
        column,
        label,
        "water and soil",
        unit,
        cadence_s=300,
        note="Hydroponic probe; written into the newest climate row every 300 s",
    )


def _diagnostic(key: str, column: str, label: str, unit: str, note: str | None = None) -> MetricBinding:
    return MetricBinding(
        key=f"diagnostics.{key}",
        label=label,
        group="controller",
        unit=unit,
        provenance="MEASURED",
        raw=RawBinding("diagnostics", column),
        daily=None,
        cadence_s=60,
        retention_days=RETENTION_DAYS["diagnostics"],
        note=note,
    )


def _scorecard(
    column: str,
    label: str,
    unit: str,
    *,
    reason: str | None = None,
    provenance: Provenance = "DERIVED",
    note: str | None = None,
) -> MetricBinding:
    return MetricBinding(
        key=f"daily.{column}",
        label=label,
        group="daily",
        unit=unit,
        provenance=provenance,
        raw=None,
        daily=DailyBinding(SCORECARD_VIEW, "scorecard", avg=column, reason=reason),
        cadence_s=DAY_S,
        retention_days=DAILY_RETENTION_DAYS,
        note=note,
    )


_HOUSE = "house climate"
_ZONES = "zones"
_WATER = "water and soil"
_LIGHT = "light"
_OUTDOOR = "outdoor"
_TARGETS = "targets"
_CONTROLLER = "controller"
_TEMPEST = "Tempest weather station, received by the controller over UDP"
_ON_CHIP = "The controller's on-chip value"

_REGISTRY: tuple[MetricBinding, ...] = (
    # house climate
    _climate("temp_avg", "Temperature", _HOUSE, "°F", zone="house", daily=_summary("temp_avg", "temp_min", "temp_max")),
    _climate("rh_avg", "Relative humidity", _HOUSE, "%", zone="house", daily=_summary("rh_avg", "rh_min", "rh_max")),
    _climate(
        "vpd_avg",
        "VPD",
        _HOUSE,
        "kPa",
        provenance="DERIVED",
        zone="house",
        daily=_summary("vpd_avg", "vpd_min", "vpd_max"),
    ),
    _climate("co2_ppm", "CO₂", _HOUSE, "ppm", zone="house", daily=_summary("co2_avg")),
    _climate(
        "temp_control",
        "Control temperature",
        _HOUSE,
        "°F",
        provenance="DERIVED",
        zone="house",
        note="What the controller regulates on: the house average, or the case probe when the average is invalid",
    ),
    _climate("vpd_control", "Control VPD", _HOUSE, "kPa", provenance="DERIVED", zone="house"),
    _climate("dew_point", "Dew point", _HOUSE, "°F", provenance="DERIVED", zone="house"),
    _climate("abs_humidity", "Absolute humidity", _HOUSE, "g/m³", provenance="DERIVED", zone="house"),
    _climate(
        "enthalpy_delta",
        "Enthalpy difference",
        _HOUSE,
        "kJ/kg",
        provenance="DERIVED",
        zone="house",
        note="Outdoor minus indoor air enthalpy",
    ),
    # zones
    *(
        _climate(f"temp_{zone}", f"{zone.capitalize()} temperature", _ZONES, "°F", zone=zone)
        for zone in ("north", "south", "east", "west")
    ),
    *(
        _climate(f"rh_{zone}", f"{zone.capitalize()} relative humidity", _ZONES, "%", zone=zone)
        for zone in ("north", "south", "east", "west")
    ),
    *(
        _climate(f"vpd_{zone}", f"{zone.capitalize()} VPD", _ZONES, "kPa", provenance="DERIVED", zone=zone)
        for zone in ("north", "south", "east", "west")
    ),
    _climate("leaf_temp_north", "North leaf temperature", _ZONES, "°F", zone="north"),
    _climate("leaf_temp_south", "South leaf temperature", _ZONES, "°F", zone="south"),
    _climate("leaf_wetness_north", "North leaf wetness", _ZONES, "%", zone="north"),
    _climate("leaf_wetness_south", "South leaf wetness", _ZONES, "%", zone="south"),
    # water and soil
    _climate("flow_gpm", "Water flow", _WATER, "gal/min", note="Main meter; whole gallons, one pulse per gallon"),
    _climate("water_total_gal", "Water meter total", _WATER, "gal", note="Main meter running total"),
    _climate("mister_water_today", "Mister water today", _WATER, "gal", provenance="DERIVED", note=_ON_CHIP),
    _climate("soil_moisture_south_1", "South 1 soil moisture", _WATER, "%", zone="south"),
    _climate("soil_temp_south_1", "South 1 soil temperature", _WATER, "°F", zone="south"),
    _climate("soil_ec_south_1", "South 1 soil EC", _WATER, "µS/cm", zone="south"),
    _climate("soil_moisture_south_2", "South 2 soil moisture", _WATER, "%", zone="south"),
    _climate("soil_temp_south_2", "South 2 soil temperature", _WATER, "°F", zone="south"),
    _climate("soil_moisture_west", "West soil moisture", _WATER, "%", zone="west"),
    _climate("soil_temp_west", "West soil temperature", _WATER, "°F", zone="west"),
    _climate("moisture_north", "North substrate moisture", _WATER, "%", zone="north"),
    _climate("moisture_south", "South substrate moisture", _WATER, "%", zone="south"),
    _climate("moisture_center", "Center substrate moisture", _WATER, "%", zone="center"),
    _climate("ph_input", "Input pH", _WATER, "pH"),
    _climate("ec_input", "Input EC", _WATER, "µS/cm"),
    _climate("ph_runoff_wall", "Wall runoff pH", _WATER, "pH"),
    _climate("ec_runoff_wall", "Wall runoff EC", _WATER, "µS/cm"),
    _climate("ph_runoff_center", "Center runoff pH", _WATER, "pH", zone="center"),
    _climate("ec_runoff_center", "Center runoff EC", _WATER, "µS/cm", zone="center"),
    _hydro("hydro_ph", "Hydroponic pH", "pH"),
    _hydro("hydro_ec_us_cm", "Hydroponic EC", "µS/cm"),
    _hydro("hydro_tds_ppm", "Hydroponic TDS", "ppm"),
    _hydro("hydro_orp_mv", "Hydroponic ORP", "mV"),
    _hydro("hydro_water_temp_f", "Hydroponic water temperature", "°F"),
    _hydro("hydro_battery_pct", "Hydroponic probe battery", "%"),
    # light
    _climate("outdoor_lux", "Outdoor illuminance", _LIGHT, "lx", note=_TEMPEST),
    _climate(
        "outdoor_illuminance",
        "Outdoor illuminance (controller copy)",
        _LIGHT,
        "lx",
        note="The controller's copy of the Tempest reading, used for grow-light automation",
    ),
    _climate("solar_irradiance_w_m2", "Solar irradiance", _LIGHT, "W/m²", note=_TEMPEST),
    _climate("uv_index", "UV index", _LIGHT, "index", note=_TEMPEST),
    _climate("solar_altitude_deg", "Sun altitude", _LIGHT, "°", provenance="DERIVED"),
    _climate("solar_azimuth_deg", "Sun azimuth", _LIGHT, "°", provenance="DERIVED"),
    _climate(
        "solar_phase",
        "Solar phase",
        _LIGHT,
        "phase",
        provenance="DERIVED",
        note="0 sunrise, 1 solar noon, 2 sunset, 3 solar midnight (on-chip)",
    ),
    _climate("solar_sunrise_min", "Sunrise", _LIGHT, "min", provenance="DERIVED", note="Minutes after local midnight"),
    _climate("solar_noon_min", "Solar noon", _LIGHT, "min", provenance="DERIVED", note="Minutes after local midnight"),
    _climate("solar_sunset_min", "Sunset", _LIGHT, "min", provenance="DERIVED", note="Minutes after local midnight"),
    # outdoor
    _climate(
        "outdoor_temp_f",
        "Outdoor temperature",
        _OUTDOOR,
        "°F",
        note=_TEMPEST,
        daily=_summary(low="outdoor_temp_min", high="outdoor_temp_max"),
    ),
    _climate("outdoor_rh_pct", "Outdoor relative humidity", _OUTDOOR, "%", note=_TEMPEST),
    _climate("pressure_hpa", "Air pressure", _OUTDOOR, "hPa", note=_TEMPEST),
    _climate("wind_speed_mph", "Wind speed", _OUTDOOR, "mph", note=_TEMPEST),
    _climate("wind_gust_mph", "Wind gust", _OUTDOOR, "mph", note=_TEMPEST),
    _climate("wind_lull_mph", "Wind lull", _OUTDOOR, "mph", note=_TEMPEST),
    _climate("wind_direction_deg", "Wind direction", _OUTDOOR, "°", note=_TEMPEST),
    _climate("precip_in", "Precipitation", _OUTDOOR, "in", note=_TEMPEST),
    _climate("lightning_count", "Lightning strikes", _OUTDOOR, "count", note=_TEMPEST),
    _climate("lightning_avg_dist_mi", "Lightning distance", _OUTDOOR, "mi", note=_TEMPEST),
    _climate("temp_intake", "Intake temperature", _OUTDOOR, "°F", note="Exterior intake probe"),
    _climate("intake_rh", "Intake relative humidity", _OUTDOOR, "%", note="Exterior intake probe"),
    _climate("intake_vpd", "Intake VPD", _OUTDOOR, "kPa", provenance="DERIVED", note="Exterior intake probe"),
    # targets: the controller's own band, a cross-check for API-06
    _climate(
        "house_temp_target_f",
        "House temperature target",
        _TARGETS,
        "°F",
        provenance="DERIVED",
        zone="house",
        note=_ON_CHIP,
    ),
    _climate(
        "house_temp_delta_f",
        "House temperature band half-width",
        _TARGETS,
        "°F",
        provenance="DERIVED",
        zone="house",
        note=_ON_CHIP,
    ),
    _climate(
        "house_vpd_target", "House VPD target", _TARGETS, "kPa", provenance="DERIVED", zone="house", note=_ON_CHIP
    ),
    _climate(
        "house_vpd_delta",
        "House VPD band half-width",
        _TARGETS,
        "kPa",
        provenance="DERIVED",
        zone="house",
        note=_ON_CHIP,
    ),
    *(
        _climate(
            f"vpd_target_{zone}",
            f"{zone.capitalize()} VPD target",
            _TARGETS,
            "kPa",
            provenance="DERIVED",
            zone=zone,
            note=_ON_CHIP,
        )
        for zone in ("center", "south", "west", "east")
    ),
    *(
        _climate(
            f"vpd_delta_{zone}",
            f"{zone.capitalize()} VPD band half-width",
            _TARGETS,
            "kPa",
            provenance="DERIVED",
            zone=zone,
            note=_ON_CHIP,
        )
        for zone in ("center", "south", "west", "east")
    ),
    # controller
    _climate("temp_case", "Case temperature", _CONTROLLER, "°F", note="Inside the controller enclosure"),
    _climate("rh_case", "Case relative humidity", _CONTROLLER, "%", note="Inside the controller enclosure"),
    _diagnostic("heap_free_kb", "heap_bytes", "Free heap", "kB", note="Stored in kB, as the controller reports it"),
    _diagnostic("heap_largest_block_kb", "heap_largest_free_block_kb", "Largest free heap block", "kB"),
    _diagnostic("rssi_dbm", "wifi_rssi", "Wi-Fi signal", "dBm"),
    _diagnostic("uptime_s", "uptime_s", "Uptime", "s"),
    # daily: the scorecard (v_daily_scorecard, one row per America/Denver day)
    _scorecard(
        "temp_in_band_pct",
        "Temperature time in band, whole day",
        "%",
        note="Share of the day's readings inside the band (daily_summary)",
    ),
    _scorecard(
        "vpd_in_band_pct",
        "VPD time in band, whole day",
        "%",
        note="Share of the day's readings inside the band (daily_summary)",
    ),
    _scorecard(
        "time_in_band_day_pct",
        "Time in band, day",
        "%",
        reason="band_reason",
        note="Graded temperature and VPD score of the house average, sunrise to sunset; kept 3 days",
    ),
    _scorecard(
        "time_in_band_night_pct",
        "Time in band, night",
        "%",
        reason="band_reason",
        note="Graded temperature and VPD score of the house average, sunset to sunrise; kept 3 days",
    ),
    _scorecard("water_gal", "Water used", "gal", reason="water_reason", provenance="MEASURED"),
    _scorecard("iris_required_written", "Iris required plans written", "count", note="MIDNIGHT, SUNRISE and SUNSET"),
    _scorecard(
        "iris_required_missed",
        "Iris required plans missed",
        "count",
        note="A required trigger that ended without a plan, an answer without one included",
    ),
    _scorecard("alerts_critical", "Critical alerts raised", "count"),
    _scorecard("alerts_warning", "Warning alerts raised", "count"),
    _scorecard("alerts_info", "Info alerts raised", "count"),
)

METRICS: dict[str, MetricBinding] = {binding.key: binding for binding in _REGISTRY}

# Every numeric climate column has a key or a reason here (tests/test_metric_registry_completeness.py).
_NO_DLI = "No DLI until the PAR meter, which adds its own column (#1184; ADR-0008)"
_HA_ONLY_TEMPEST = (
    "Home Assistant-only Tempest field (_TEMPEST_MAP, no ESP32 entity); a gap once #1163 removes tempest_sync"
)
EXCLUDED_COLUMNS: dict[str, str] = {
    "lux": _NO_DLI,
    "dli_today": _NO_DLI,
    "ppfd": _NO_DLI,
    "dli_par_today": _NO_DLI,
    "outdoor_ppfd": "Renamed to outdoor_lux by migration 027",
    "hydro_tds_ppt": "Added by migration 017, now hydro_tds_ppm",
    "wind_speed_avg_mph": _HA_ONLY_TEMPEST,
    "wind_direction_avg_deg": _HA_ONLY_TEMPEST,
    "feels_like_f": _HA_ONLY_TEMPEST,
    "wet_bulb_temp_f": _HA_ONLY_TEMPEST,
    "vapor_pressure_inhg": _HA_ONLY_TEMPEST,
    "air_density_kg_m3": _HA_ONLY_TEMPEST,
    "precip_intensity_in_h": _HA_ONLY_TEMPEST,
}


def metric_list() -> MetricList:
    """API-03: every registry key, in registry order."""
    return MetricList(items=[binding.definition() for binding in METRICS.values()])


# ─────────────────────────────────────────────────────────────── shared helpers
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def _ident(name: str) -> str:
    """Quote a registry identifier; nothing from a request ever reaches here."""
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"not a registry identifier: {name!r}")
    return f'"{name}"'


def _now(now: datetime | None) -> datetime:
    return datetime.now(UTC) if now is None else now


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _validate_window(from_: datetime, to: datetime, max_window: timedelta) -> tuple[datetime, datetime]:
    """Check a half-open window and return it in UTC."""
    for name, value in (("from", from_), ("to", to)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise problem("invalid_request", f"{name} needs a time zone offset", field=name)
    if from_ >= to:
        raise problem("invalid_request", "from must be before to", field="from")
    if to - from_ > max_window:
        raise problem(
            "window_too_large",
            f"A window covers at most {max_window.days} days; narrow it",
            field="to",
            max=max_window.days,
        )
    return from_.astimezone(UTC), to.astimezone(UTC)


@asynccontextmanager
async def _read_transaction(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """One read-only transaction with the dashboard login's 10 s statement timeout.

    Custom plans keep the window parameters constant at plan time, for chunk exclusion and
    for time_bucket_gapfill's start and finish.
    """
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(
                f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'; SET LOCAL plan_cache_mode = force_custom_plan"
            )
            yield
    except asyncpg.QueryCanceledError as exc:
        raise problem("query_timeout", "This window took too long. Narrow it.") from exc


def aligned_starts(from_: datetime, to: datetime, step_s: int) -> list[datetime]:
    """Starts of the ``step_s`` buckets that cover [from_, to), aligned as TimescaleDB aligns them."""
    step = timedelta(seconds=step_s)
    start = BUCKET_ORIGIN + ((from_ - BUCKET_ORIGIN) // step) * step
    starts = []
    while start < to:
        starts.append(start)
        start += step
    return starts


def _aligned_count(from_: datetime, to: datetime, step_s: int) -> int:
    step = timedelta(seconds=step_s)
    first = BUCKET_ORIGIN + ((from_ - BUCKET_ORIGIN) // step) * step
    return -((first - to) // step)


def denver_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=DENVER).astimezone(UTC)


def denver_day_starts(from_: datetime, to: datetime) -> list[datetime]:
    """Local midnights of the America/Denver days that cover [from_, to); 23 and 25 h days count as they ran."""
    day = from_.astimezone(DENVER).date()
    starts = []
    while (start := denver_midnight(day)) < to:
        starts.append(start)
        day += timedelta(days=1)
    return starts


def bucket_starts(from_: datetime, to: datetime, bucket_s: int) -> list[datetime]:
    return denver_day_starts(from_, to) if bucket_s == DAY_S else aligned_starts(from_, to, bucket_s)


def bucket_count(from_: datetime, to: datetime, bucket_s: int) -> int:
    return len(denver_day_starts(from_, to)) if bucket_s == DAY_S else _aligned_count(from_, to, bucket_s)


def choose_bucket(from_: datetime, to: datetime, points: int) -> int:
    """The smallest bucket that gives at most ``points`` buckets (1 d when none does)."""
    for bucket_s in BUCKETS_S:
        if bucket_count(from_, to, bucket_s) <= points:
            return bucket_s
    return BUCKETS_S[-1]


# ─────────────────────────────────────────────────────────────── API-04 series
@dataclass(frozen=True, slots=True)
class KeyRead:
    binding: MetricBinding
    source: Literal["raw", "daily_summary", "scorecard"]
    retained_from: datetime | None


@dataclass(frozen=True, slots=True)
class SeriesPlan:
    from_: datetime
    to: datetime
    bucket_s: int
    daily_mode: bool
    axis: tuple[datetime, ...]
    reads: tuple[KeyRead, ...]


def is_daily_mode(bindings: Iterable[MetricBinding], from_: datetime, to: datetime, now: datetime) -> bool:
    """Day buckets over 31 days, or when ``from_`` is older than the raw retention of a key with a
    daily binding. A ``daily.*`` key has no raw data at all, so a request that names one is daily."""
    if to - from_ > DAILY_MODE_OVER:
        return True
    for binding in bindings:
        if binding.raw is None:
            return True
        if binding.daily is not None and from_ < now - timedelta(days=binding.retention_days):
            return True
    return False


def plan_series(keys: Sequence[str], from_: datetime, to: datetime, points: int, *, now: datetime) -> SeriesPlan:
    """Validate an API-04 request and decide its buckets and each key's source (pure)."""
    if not 1 <= len(keys) <= MAX_SERIES_KEYS:
        raise problem("invalid_request", f"Ask for 1 to {MAX_SERIES_KEYS} metrics", field="metric")
    wanted = list(dict.fromkeys(keys))
    unknown = [key for key in wanted if key not in METRICS]
    if unknown:
        raise problem("unknown_target", f"Unknown metric {unknown[0]!r}; API-03 lists the keys", field="metric")
    if not POINTS_MIN <= points <= POINTS_MAX:
        raise problem(
            "invalid_request",
            f"points is {POINTS_MIN} to {POINTS_MAX}",
            field="points",
            min=POINTS_MIN,
            max=POINTS_MAX,
        )
    from_, to = _validate_window(from_, to, MAX_WINDOW)
    bindings = [METRICS[key] for key in wanted]
    daily_mode = is_daily_mode(bindings, from_, to, now)
    bucket_s = DAY_S if daily_mode else choose_bucket(from_, to, points)
    reads = []
    for binding in bindings:
        if binding.raw is None or (daily_mode and binding.daily is not None):
            reads.append(KeyRead(binding, binding.daily.source, None))
        else:
            reads.append(KeyRead(binding, "raw", now - timedelta(days=binding.retention_days)))
    return SeriesPlan(from_, to, bucket_s, daily_mode, tuple(bucket_starts(from_, to, bucket_s)), tuple(reads))


def raw_series_sql(table: str, columns: Sequence[str], *, day_buckets: bool) -> str:
    """One gap-filled aggregate per table: avg, min, max and count per column, bounded by ts.

    $1 and $2 bound the window ([$1, $2)); $3 is the bucket width except for day buckets,
    which are America/Denver days.
    """
    if day_buckets:
        bucket = "time_bucket_gapfill('1 day'::interval, ts, 'America/Denver', $1::timestamptz, $2::timestamptz)"
    else:
        bucket = "time_bucket_gapfill($3::interval, ts, $1::timestamptz, $2::timestamptz)"
    aggregates = []
    for index, column in enumerate(columns):
        quoted = _ident(column)
        aggregates.append(
            f"avg({quoted})::double precision AS a{index}, min({quoted})::double precision AS n{index}, "
            f"max({quoted})::double precision AS x{index}, count({quoted}) AS c{index}"
        )
    any_value = " OR ".join(f"{_ident(column)} IS NOT NULL" for column in columns)
    return (
        f"SELECT {bucket} AS bucket, {', '.join(aggregates)}, max(ts) FILTER (WHERE {any_value}) AS last_ts "
        f"FROM public.{_ident(table)} WHERE ts >= $1::timestamptz AND ts < $2::timestamptz GROUP BY 1 ORDER BY 1"
    )


def daily_rows_sql(view: str, columns: Sequence[str], stamp: str) -> str:
    selected = ", ".join(_ident(column) for column in columns)
    return (
        f"SELECT date, {selected}, {_ident(stamp)} AS stamp FROM public.{_ident(view)} "
        "WHERE date >= $1::date AND date <= $2::date ORDER BY date"
    )


GAPS_SQL = (
    "SELECT start_ts, end_ts, reason FROM public.data_gaps "
    "WHERE start_ts < $2::timestamptz AND end_ts > $1::timestamptz ORDER BY start_ts, id"
)


def _empty_columns(size: int, source: str, retained_from: datetime | None) -> SeriesColumns:
    return SeriesColumns(
        avg=[None] * size,
        min=[None] * size,
        max=[None] * size,
        count=[0] * size,
        source=source,
        retained_from=retained_from,
    )


def raw_columns(
    axis: Sequence[datetime], rows: Iterable[Any], reads: Sequence[KeyRead]
) -> tuple[dict[str, SeriesColumns], datetime | None]:
    """Place one table's gap-filled rows on the axis (pure).

    An empty bucket is null with count 0. A bucket that starts before a key's
    ``retained_from`` is beyond retention: null and count 0 whatever the table still holds.
    """
    index = {_epoch(start): position for position, start in enumerate(axis)}
    columns = {read.binding.key: _empty_columns(len(axis), "raw", read.retained_from) for read in reads}
    watermark = None
    for row in rows:
        position = index.get(_epoch(row["bucket"]))
        if position is None:
            continue
        kept = False
        for column_index, read in enumerate(reads):
            if read.retained_from is not None and axis[position] < read.retained_from:
                continue
            target = columns[read.binding.key]
            target.avg[position] = _number(row[f"a{column_index}"])
            target.min[position] = _number(row[f"n{column_index}"])
            target.max[position] = _number(row[f"x{column_index}"])
            target.count[position] = int(row[f"c{column_index}"] or 0)
            kept = True
        if kept and row["last_ts"] is not None:
            watermark = row["last_ts"] if watermark is None else max(watermark, row["last_ts"])
    return columns, watermark


def daily_columns(
    axis: Sequence[datetime], rows: Iterable[Any], reads: Sequence[KeyRead]
) -> tuple[dict[str, SeriesColumns], datetime | None]:
    """Place one daily view's rows on the day axis (pure).

    A ``daily_summary`` key takes avg, min and max from its bound columns. A scorecard key's one
    value is avg, min and max at once, and its reason column says why a null day is null. A
    day with no row is a gap: null, count 0, no reason.
    """
    index = {start.astimezone(DENVER).date(): position for position, start in enumerate(axis)}
    columns = {read.binding.key: _empty_columns(len(axis), read.source, None) for read in reads}
    for read in reads:
        if read.source == "scorecard":
            columns[read.binding.key].reason = [None] * len(axis)
    watermark = None
    for row in rows:
        position = index.get(row["date"])
        if position is None:
            continue
        for read in reads:
            daily = read.binding.daily
            target = columns[read.binding.key]
            if read.source == "scorecard":
                value = _number(row[daily.avg])
                target.avg[position] = target.min[position] = target.max[position] = value
                target.count[position] = 0 if value is None else 1
                if value is None and daily.reason is not None:
                    target.reason[position] = row[daily.reason]
            else:
                values = [_number(row[column]) if column else None for column in (daily.avg, daily.min, daily.max)]
                target.avg[position], target.min[position], target.max[position] = values
                target.count[position] = int(any(value is not None for value in values))
        if row["stamp"] is not None:
            watermark = row["stamp"] if watermark is None else max(watermark, row["stamp"])
    return columns, watermark


async def series(
    conn: asyncpg.Connection,
    keys: Sequence[str],
    from_: datetime,
    to: datetime,
    points: int = POINTS_DEFAULT,
    *,
    now: datetime | None = None,
) -> Series:
    """API-04: up to 8 registry keys over [from_, to) as uPlot columns, with the window's outages."""
    now = _now(now)
    plan = plan_series(keys, from_, to, points, now=now)
    from_, to = plan.from_, plan.to
    axis = plan.axis
    columns: dict[str, SeriesColumns] = {}
    stamps: list[datetime] = []
    async with _read_transaction(conn):
        for table in ("climate", "diagnostics"):
            reads = [read for read in plan.reads if read.source == "raw" and read.binding.raw.table == table]
            if not reads:
                continue
            # Rows older than every key's retention are never read; raw_columns marks the rest.
            low = max(from_, min(read.retained_from for read in reads))
            rows: list[Any] = []
            if low < to:
                sql = raw_series_sql(
                    table, [read.binding.raw.column for read in reads], day_buckets=plan.bucket_s == DAY_S
                )
                arguments = [low, to] if plan.bucket_s == DAY_S else [low, to, timedelta(seconds=plan.bucket_s)]
                rows = await conn.fetch(sql, *arguments)
            placed, stamp = raw_columns(axis, rows, reads)
            columns.update(placed)
            if stamp is not None:
                stamps.append(stamp)
        daily_reads: dict[tuple[str, str], list[KeyRead]] = {}
        for read in plan.reads:
            if read.source != "raw":
                daily_reads.setdefault((read.binding.daily.view, read.binding.daily.stamp), []).append(read)
        for (view, stamp_column), reads in daily_reads.items():
            needed: list[str] = []
            for read in reads:
                daily = read.binding.daily
                for column in (daily.avg, daily.min, daily.max, daily.reason):
                    if column and column not in needed:
                        needed.append(column)
            first_day = axis[0].astimezone(DENVER).date()
            last_day = axis[-1].astimezone(DENVER).date()
            rows = await conn.fetch(daily_rows_sql(view, needed, stamp_column), first_day, last_day)
            placed, stamp = daily_columns(axis, rows, reads)
            columns.update(placed)
            if stamp is not None:
                stamps.append(stamp)
        gap_rows = await conn.fetch(GAPS_SQL, from_, to)
    return Series(
        from_=from_,
        to=to,
        bucket_s=plan.bucket_s,
        t=[_epoch(start) for start in axis],
        series={read.binding.key: columns[read.binding.key] for read in plan.reads},
        gaps=[SeriesGap(start=row["start_ts"], end=row["end_ts"], reason=row["reason"]) for row in gap_rows],
        watermark=max(stamps) if stamps else None,
        server_time=now,
    )


# ─────────────────────────────────────────────────────────────── API-05 equipment states
LANES: tuple[tuple[str, LaneGroup], ...] = (
    ("fan1", "climate"),
    ("fan2", "climate"),
    ("vent", "climate"),
    ("heat1", "climate"),
    ("heat2", "climate"),
    *((equipment, "water") for equipment in LINE_IDS),
    ("grow_light_main", "light"),
    ("grow_light_grow", "light"),
    ("occupancy", "flag"),
    ("leak_detected", "flag"),
    ("water_flowing", "flag"),
)
_LANE_GROUPS = dict(LANES)

SEED_SQL = (
    "SELECT lane.equipment, public.fn_equip_at(lane.equipment, $2::timestamptz) AS state "
    "FROM unnest($1::text[]) AS lane(equipment)"
)
LIGHT_SEED_SQL = (
    "SELECT equipment, max(ts) AS ts FROM public.equipment_state "
    "WHERE equipment = ANY($1::text[]) AND ts > $2::timestamptz AND ts <= $3::timestamptz GROUP BY equipment"
)
EQUIPMENT_ROWS_SQL = (
    "SELECT equipment, ts, state FROM public.equipment_state "
    "WHERE equipment = ANY($1::text[]) AND ts > $2::timestamptz AND ts < $3::timestamptz ORDER BY equipment, ts"
)
# The first and last climate rows of the window and every row that ends a silence longer than $3.
CLIMATE_EDGES_SQL = (
    "SELECT prev_ts, ts, next_ts FROM ("
    "SELECT ts, lag(ts) OVER w AS prev_ts, lead(ts) OVER w AS next_ts FROM public.climate "
    "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz WINDOW w AS (ORDER BY ts)) edges "
    "WHERE prev_ts IS NULL OR next_ts IS NULL OR ts - prev_ts > $3::interval ORDER BY ts"
)


def _clip(
    spans: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    clipped = []
    for low, high in spans:
        low, high = max(low, start), min(high, end)
        if low < high:
            clipped.append((low, high))
    return clipped


def not_reporting_spans(
    first: datetime | None,
    last: datetime | None,
    silences: Iterable[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
    max_age: timedelta = CONTROLLER_REPORTING,
) -> list[tuple[datetime, datetime]]:
    """Where the controller was not reporting: its newest climate row was over ``max_age`` old.

    ``first`` and ``last`` are the first and last climate rows from ``start - max_age`` to
    ``end``; ``silences`` are consecutive rows (earlier, later) more than ``max_age`` apart.
    """
    if first is None or last is None:
        return [(start, end)] if start < end else []
    spans = []
    if first > start:
        spans.append((start, first))
    spans.extend((earlier + max_age, later) for earlier, later in silences)
    spans.append((last + max_age, end))
    return _clip(spans, start, end)


def stale_spans(
    times: Iterable[datetime],
    seed: datetime | None,
    start: datetime,
    end: datetime,
    max_age: timedelta = LUTRON_FRESH,
) -> list[tuple[datetime, datetime]]:
    """Where a polled readback is unknown: more than ``max_age`` after its newest row.

    ``seed`` is the newest row at most ``max_age`` before ``start``, or None.
    """
    marks = sorted([*([seed] if seed is not None else []), *times])
    if not marks:
        return [(start, end)] if start < end else []
    spans = []
    if marks[0] > start:
        spans.append((start, marks[0]))
    spans.extend(
        (earlier + max_age, later)
        for earlier, later in zip(marks, marks[1:], strict=False)
        if later - earlier > max_age
    )
    spans.append((marks[-1] + max_age, end))
    return _clip(spans, start, end)


def _state_word(state: bool | None) -> LaneState:
    return "unknown" if state is None else ("on" if state else "off")


def overlay_unknown(
    timeline: Sequence[tuple[datetime, Hashable]],
    unknown: Sequence[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
) -> list[tuple[LaneState, datetime, datetime]]:
    """Collapse a lane's (ts, state) timeline over [start, end) and mark the unknown spans (pure)."""
    base = collapse(timeline, end=end)
    if not base or start >= end:
        return []
    # Each piece runs from one cut to the next; no state change or unknown edge falls inside it.
    cuts = {start}
    cuts.update(span.start for span in base if start < span.start < end)
    for low, high in unknown:
        cuts.update(point for point in (low, high) if start < point < end)
    starts = [span.start for span in base]
    pieces: list[tuple[datetime, Hashable]] = []
    for cut in sorted(cuts):
        if any(low <= cut < high for low, high in unknown):
            state: Hashable = "unknown"
        else:
            state = base[max(bisect_right(starts, cut) - 1, 0)].state
        pieces.append((cut, state))
    return [(span.state, span.start, span.end) for span in collapse(pieces, end=end)]


def lane_intervals(
    seed: bool | None,
    rows: Sequence[tuple[datetime, bool]],
    unknown: Sequence[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
) -> list[tuple[LaneState, datetime, datetime]]:
    """A lane seeded with its state at ``start`` (fn_equip_at; None before the first row ever)."""
    timeline = [(start, _state_word(seed)), *((ts, _state_word(state)) for ts, state in rows)]
    return overlay_unknown(timeline, unknown, start, end)


def _select_lanes(equipment: Sequence[str] | None) -> tuple[tuple[str, LaneGroup], ...]:
    if not equipment:
        return LANES
    wanted = set(equipment)
    unknown = sorted(wanted - _LANE_GROUPS.keys())
    if unknown:
        raise problem("unknown_target", f"Unknown equipment {unknown[0]!r}", field="equipment")
    return tuple(lane for lane in LANES if lane[0] in wanted)


async def states(
    conn: asyncpg.Connection,
    from_: datetime,
    to: datetime,
    equipment: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
) -> StateLanes:
    """API-05: on/off intervals per lane over [from_, to), at most 31 days.

    Each lane is seeded with fn_equip_at(equipment, from_) and its equal states collapse. A
    span is unknown before a lane's first row, while the controller was not reporting (no
    climate row for more than 300 s) and, on the Lutron light lanes, more than 600 s after a
    readback row. Intervals end at the window's end or now. A wet line's cause stays null
    until #1155's run_causes() fills it.
    """
    now = _now(now)
    from_, to = _validate_window(from_, to, MAX_STATES_WINDOW)
    lanes = _select_lanes(equipment)
    end = min(to, now)
    seeds: dict[str, bool | None] = {}
    light_seeds: dict[str, datetime] = {}
    rows: dict[str, list[tuple[datetime, bool]]] = {}
    controller: list[tuple[datetime, datetime]] = []
    if from_ < end:
        names = [name for name, _ in lanes]
        lights = [name for name, group in lanes if group == "light"]
        async with _read_transaction(conn):
            seeds = {row["equipment"]: row["state"] for row in await conn.fetch(SEED_SQL, names, from_)}
            if lights:
                light_rows = await conn.fetch(LIGHT_SEED_SQL, lights, from_ - LUTRON_FRESH, from_)
                light_seeds = {row["equipment"]: row["ts"] for row in light_rows}
            for row in await conn.fetch(EQUIPMENT_ROWS_SQL, names, from_, end):
                rows.setdefault(row["equipment"], []).append((row["ts"], row["state"]))
            if len(lights) < len(names):
                edges = await conn.fetch(CLIMATE_EDGES_SQL, from_ - CONTROLLER_REPORTING, end, CONTROLLER_REPORTING)
                first = next((row["ts"] for row in edges if row["prev_ts"] is None), None)
                last = next((row["ts"] for row in edges if row["next_ts"] is None), None)
                silences = [
                    (row["prev_ts"], row["ts"])
                    for row in edges
                    if row["prev_ts"] is not None and row["ts"] - row["prev_ts"] > CONTROLLER_REPORTING
                ]
                controller = not_reporting_spans(first, last, silences, from_, end)
    result = []
    for name, group in lanes:
        intervals: list[StateInterval] = []
        if from_ < end:
            lane_rows = rows.get(name, [])
            if group == "light":
                unknown = stale_spans((ts for ts, _ in lane_rows), light_seeds.get(name), from_, end)
            else:
                unknown = controller
            intervals = [
                StateInterval(start=low, end=high, state=state)
                for state, low, high in lane_intervals(seeds.get(name), lane_rows, unknown, from_, end)
            ]
        result.append(
            StateLane(
                equipment=name,
                label=EQUIPMENT_LABELS[name],
                group=group,
                line_id=LINE_IDS.get(name),
                intervals=intervals,
            )
        )
    return StateLanes(from_=from_, to=to, lanes=result)


# ─────────────────────────────────────────────────────────────── API-06 band
# emit_band_audit names each row band_<zone>_<role>. Filtering on that parameter lets
# idx_snapshot_param (parameter, ts DESC) read only the band rows; zone and band_role alone
# would scan every readback row in the window (about 400,000 a day).
BAND_PARAMETERS = tuple(f"band_house_{role}" for role in BAND_ROLES)
_BAND_ROWS = (
    "FROM public.setpoint_snapshot WHERE parameter IN ("
    + ", ".join(f"'{name}'" for name in BAND_PARAMETERS)
    + ") AND zone = 'house' AND band_role IN ("
    + ", ".join(f"'{role}'" for role in BAND_ROLES)
    + ") AND ts >= $1::timestamptz AND ts < $2::timestamptz"
)
BAND_SQL = (
    "SELECT time_bucket_gapfill($3::interval, ts, $1::timestamptz, $2::timestamptz) AS bucket, "
    + ", ".join(
        f"last(value, ts) FILTER (WHERE band_role = '{role}') AS {role}, "
        f"max(ts) FILTER (WHERE band_role = '{role}') AS {role}_ts"
        for role in BAND_ROLES
    )
    + f" {_BAND_ROWS} GROUP BY 1 ORDER BY 1"
)
BAND_SEED_SQL = f"SELECT DISTINCT ON (band_role) band_role, value, ts {_BAND_ROWS} ORDER BY band_role, ts DESC"
# fn_band_timeline's crop columns: fn_band_setpoints evaluates the same house curves of
# fn_crop_band_value, without the timeline's per-step telemetry joins.
CROP_SQL = (
    "SELECT step.ts, crop.temp_low, crop.temp_high, crop.vpd_low, crop.vpd_high "
    "FROM unnest($1::timestamptz[]) AS step(ts) CROSS JOIN LATERAL public.fn_band_setpoints(step.ts) AS crop"
)


def band_step(from_: datetime, to: datetime) -> int:
    """The smallest multiple of 300 s that gives at most 2,000 aligned points."""
    multiple = max(1, math.ceil((to - from_).total_seconds() / (BAND_MAX_POINTS * BAND_STEP_UNIT_S)))
    while _aligned_count(from_, to, multiple * BAND_STEP_UNIT_S) > BAND_MAX_POINTS:
        multiple += 1
    return multiple * BAND_STEP_UNIT_S


def band_columns(
    axis: Sequence[datetime],
    rows: Iterable[Any],
    seeds: dict[str, tuple[float, datetime]],
    retained_from: datetime,
    carry: timedelta = BAND_CARRY,
) -> BandColumns:
    """The band in force per step (pure).

    A step takes its bucket's last row; an empty step carries the newest earlier row while that
    row is at most ``carry`` old, and stays null after that. Steps before the band rows'
    retention are null.
    """
    index = {_epoch(start): position for position, start in enumerate(axis)}
    cells: dict[str, list[tuple[float, datetime] | None]] = {role: [None] * len(axis) for role in BAND_ROLES}
    for row in rows:
        position = index.get(_epoch(row["bucket"]))
        if position is None:
            continue
        for role in BAND_ROLES:
            if row[role] is not None and row[f"{role}_ts"] is not None:
                cells[role][position] = (float(row[role]), row[f"{role}_ts"])
    out: dict[str, list[float | None]] = {}
    for role in BAND_ROLES:
        newest = seeds.get(role)
        column: list[float | None] = []
        for position, start in enumerate(axis):
            cell = cells[role][position]
            if cell is not None:
                newest = cell
            if start < retained_from:
                column.append(None)
            elif cell is not None:
                column.append(_number(cell[0]))
            elif newest is not None and start - newest[1] <= carry:
                column.append(_number(newest[0]))
            else:
                column.append(None)
        out[role] = column
    return BandColumns(**out)


def crop_columns(axis: Sequence[datetime], rows: Iterable[Any]) -> BandColumns:
    index = {_epoch(start): position for position, start in enumerate(axis)}
    out: dict[str, list[float | None]] = {role: [None] * len(axis) for role in BAND_ROLES}
    for row in rows:
        position = index.get(_epoch(row["ts"]))
        if position is None:
            continue
        for role in BAND_ROLES:
            out[role][position] = _number(row[role])
    return BandColumns(**out)


async def band(
    conn: asyncpg.Connection,
    from_: datetime,
    to: datetime,
    *,
    now: datetime | None = None,
) -> BandTimeline:
    """API-06 for the house: the band in force (DERIVED, kept 90 days) and the crop band
    reconstructed from today's anchors, one point per step."""
    now = _now(now)
    from_, to = _validate_window(from_, to, MAX_WINDOW)
    step_s = band_step(from_, to)
    axis = aligned_starts(from_, to, step_s)
    retained_from = now - timedelta(days=RETENTION_DAYS["setpoint_snapshot"])
    low = max(axis[0], retained_from)
    band_rows: list[Any] = []
    seeds: dict[str, tuple[float, datetime]] = {}
    async with _read_transaction(conn):
        if low < to:
            band_rows = await conn.fetch(BAND_SQL, low, to, timedelta(seconds=step_s))
            seeds = {
                row["band_role"]: (row["value"], row["ts"])
                for row in await conn.fetch(BAND_SEED_SQL, low - BAND_CARRY, low)
            }
        crop_rows = await conn.fetch(CROP_SQL, list(axis))
    return BandTimeline(
        from_=from_,
        to=to,
        step_s=step_s,
        t=[_epoch(start) for start in axis],
        band=band_columns(axis, band_rows, seeds, retained_from),
        crop=crop_columns(axis, crop_rows),
    )
