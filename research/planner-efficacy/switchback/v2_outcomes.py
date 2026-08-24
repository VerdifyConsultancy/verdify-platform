"""Frozen protocol-v2 outcome and randomized-ITT interface.

Pure functions implement the [06:00,24:00) climate buckets and the selected
nine-stream heterogeneous control-state burden.  Exposure coverage is carried
only as fidelity metadata: it never selects, drops, truncates, or weights the
primary ITT row.
"""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

WINDOW_START = time(6, 0)
WINDOW_END = time(0, 0)
BUCKET_MINUTES = 15
EXPECTED_CLIMATE_BINS = 72
MIN_VALID_CLIMATE_BINS = 66
MIN_MINUTE_SLOTS_PER_BIN = 12
MAX_CONTIGUOUS_MISSING_BINS = 2  # 30 minutes; a longer gap invalidates the day.
ANALYZED_SECONDS = 64_800
PER_PROTOCOL_EXPOSURE_SECONDS = 61_560

EQUIPMENT_STREAMS: tuple[str, ...] = (
    "heat1",
    "heat2",
    "vent",
    "fan1",
    "fan2",
    "fog",
    "mister_south",
    "mister_west",
    "mister_center",
)
SELECTED_BENEFIT_ENDPOINT = "nine_control_state_minutes"


@dataclass(frozen=True)
class ClimateSample:
    observed_at: datetime
    temperature_f: float
    vpd_kpa: float


@dataclass(frozen=True)
class Corridor:
    temperature_low_f: float
    temperature_high_f: float
    vpd_low_kpa: float
    vpd_high_kpa: float


@dataclass(frozen=True)
class ClimateBin:
    bucket_start: datetime
    minute_slots: int
    temperature_distance_f: float | None
    vpd_distance_kpa: float | None
    integrity_deviation: str | None = None


@dataclass(frozen=True)
class StateObservation:
    observed_at: datetime
    state: bool


@dataclass(frozen=True)
class CounterSample:
    observed_at: datetime
    value_minutes: float
    reset_epoch: str


@dataclass(frozen=True)
class StreamOutcome:
    stream: str
    active_or_open_minutes: float | None
    valid: bool
    reason: str | None


@dataclass(frozen=True)
class RandomizedIttRow:
    assignment_id: str
    local_date: str
    blinded_label: Literal["X", "Y"]
    execution_phase: Literal["randomized"]
    temperature_corridor_distance_f: float | None
    vpd_corridor_distance_kpa: float | None
    nine_control_state_minutes: float | None
    outcome_complete: bool
    fallback_or_rescue: bool
    exposure_seconds: int
    per_protocol_exposure_complete: bool
    missing_reason: str | None


def _local_window(local_date: str, timezone: str) -> tuple[datetime, datetime]:
    parsed = date.fromisoformat(local_date)
    if parsed.isoformat() != local_date:
        raise ValueError("local_date must be canonical YYYY-MM-DD")
    tz = ZoneInfo(timezone)
    start = datetime.combine(parsed, WINDOW_START, tzinfo=tz)
    end = datetime.combine(parsed + timedelta(days=1), WINDOW_END, tzinfo=tz)
    if start.utcoffset() != end.utcoffset() or (end - start).total_seconds() != ANALYZED_SECONDS:
        raise ValueError("protocol v2 outcome window may not cross a UTC-offset transition")
    return start, end


def _finite(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _distance(value: float, low: float, high: float) -> float:
    value = _finite(value, "corridor observation")
    low = _finite(low, "corridor low")
    high = _finite(high, "corridor high")
    if low > high:
        raise ValueError("corridor low exceeds high")
    return max(low - value, 0.0, value - high)


def climate_bins(
    samples: list[ClimateSample],
    *,
    local_date: str,
    timezone: str,
    corridors: dict[datetime, Corridor],
    temperature_duplicate_tolerance_f: float,
    vpd_duplicate_tolerance_kpa: float,
) -> list[ClimateBin]:
    """Aggregate exact minute slots; extra polling never adds slot weight."""
    temperature_duplicate_tolerance_f = _finite(temperature_duplicate_tolerance_f, "temperature duplicate tolerance")
    vpd_duplicate_tolerance_kpa = _finite(vpd_duplicate_tolerance_kpa, "VPD duplicate tolerance")
    if temperature_duplicate_tolerance_f < 0 or vpd_duplicate_tolerance_kpa < 0:
        raise ValueError("duplicate tolerances must be nonnegative")
    start, end = _local_window(local_date, timezone)
    for corridor in corridors.values():
        _distance(0.0, corridor.temperature_low_f, corridor.temperature_high_f)
        _distance(0.0, corridor.vpd_low_kpa, corridor.vpd_high_kpa)
    by_exact: dict[datetime, list[ClimateSample]] = defaultdict(list)
    for sample in samples:
        if sample.observed_at.tzinfo is None or sample.observed_at.utcoffset() is None:
            raise ValueError("climate timestamps must be timezone-aware")
        if start <= sample.observed_at.astimezone(start.tzinfo) < end:
            by_exact[sample.observed_at].append(sample)
    invalid_minutes: set[datetime] = set()
    exact_collapsed: list[ClimateSample] = []
    for observed_at, rows in by_exact.items():
        temps = [_finite(row.temperature_f, "temperature_f") for row in rows]
        vpds = [_finite(row.vpd_kpa, "vpd_kpa") for row in rows]
        minute = observed_at.replace(second=0, microsecond=0)
        if (
            max(temps) - min(temps) > temperature_duplicate_tolerance_f
            or max(vpds) - min(vpds) > vpd_duplicate_tolerance_kpa
        ):
            invalid_minutes.add(minute)
            continue
        exact_collapsed.append(ClimateSample(observed_at, sum(temps) / len(temps), sum(vpds) / len(vpds)))
    by_minute: dict[datetime, list[ClimateSample]] = defaultdict(list)
    for sample in exact_collapsed:
        minute = sample.observed_at.replace(second=0, microsecond=0)
        if minute not in invalid_minutes:
            by_minute[minute].append(sample)
    minute_means = {
        minute: (
            sum(row.temperature_f for row in rows) / len(rows),
            sum(row.vpd_kpa for row in rows) / len(rows),
        )
        for minute, rows in by_minute.items()
    }
    results: list[ClimateBin] = []
    for index in range(EXPECTED_CLIMATE_BINS):
        bucket_start = start + timedelta(minutes=index * BUCKET_MINUTES)
        values = [
            minute_means[bucket_start + timedelta(minutes=offset)]
            for offset in range(15)
            if bucket_start + timedelta(minutes=offset) in minute_means
        ]
        deviation = (
            "conflicting_duplicate_timestamp"
            if any(bucket_start <= minute < bucket_start + timedelta(minutes=15) for minute in invalid_minutes)
            else None
        )
        corridor = corridors.get(bucket_start)
        if len(values) < MIN_MINUTE_SLOTS_PER_BIN or corridor is None or deviation:
            results.append(ClimateBin(bucket_start, len(values), None, None, deviation or "incomplete_bin"))
            continue
        temp = sum(value[0] for value in values) / len(values)
        vpd = sum(value[1] for value in values) / len(values)
        results.append(
            ClimateBin(
                bucket_start,
                len(values),
                _distance(temp, corridor.temperature_low_f, corridor.temperature_high_f),
                _distance(vpd, corridor.vpd_low_kpa, corridor.vpd_high_kpa),
            )
        )
    return results


def daily_climate_outcome(bins: list[ClimateBin]) -> tuple[float | None, float | None, str | None]:
    if len(bins) != EXPECTED_CLIMATE_BINS:
        raise ValueError(f"expected exactly {EXPECTED_CLIMATE_BINS} locked climate bins")

    first = bins[0].bucket_start
    if first.tzinfo is None or first.utcoffset() is None:
        raise ValueError("climate bin timestamps must be timezone-aware")
    if (first.hour, first.minute, first.second, first.microsecond) != (6, 0, 0, 0):
        raise ValueError("locked climate bins must begin exactly at 06:00 local")
    for index, row in enumerate(bins):
        if row.bucket_start != first + timedelta(minutes=index * BUCKET_MINUTES):
            raise ValueError("climate bins must be one exact ordered 15-minute grid")
        if type(row.minute_slots) is not int or not 0 <= row.minute_slots <= BUCKET_MINUTES:
            raise ValueError("climate bin minute_slots must be an integer in [0,15]")

    def complete(row: ClimateBin) -> bool:
        return (
            row.minute_slots >= MIN_MINUTE_SLOTS_PER_BIN
            and row.integrity_deviation is None
            and isinstance(row.temperature_distance_f, (int, float))
            and not isinstance(row.temperature_distance_f, bool)
            and math.isfinite(row.temperature_distance_f)
            and row.temperature_distance_f >= 0
            and isinstance(row.vpd_distance_kpa, (int, float))
            and not isinstance(row.vpd_distance_kpa, bool)
            and math.isfinite(row.vpd_distance_kpa)
            and row.vpd_distance_kpa >= 0
        )

    valid = [row for row in bins if complete(row)]
    longest = current = 0
    for row in bins:
        if not complete(row):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    if len(valid) < MIN_VALID_CLIMATE_BINS or longest > MAX_CONTIGUOUS_MISSING_BINS:
        return None, None, "climate_completeness"
    return (
        sum(row.temperature_distance_f or 0.0 for row in valid) / len(valid),
        sum(row.vpd_distance_kpa or 0.0 for row in valid) / len(valid),
        None,
    )


def _dedupe_states(rows: list[StateObservation]) -> tuple[list[StateObservation], str | None]:
    grouped: dict[datetime, set[bool]] = defaultdict(set)
    for row in rows:
        if type(row.state) is not bool:
            return [], "invalid_state_type"
        if row.observed_at.tzinfo is None or row.observed_at.utcoffset() is None:
            return [], "naive_state_timestamp"
        grouped[row.observed_at].add(row.state)
    if any(len(states) != 1 for states in grouped.values()):
        return [], "conflicting_same_timestamp_state"
    return [StateObservation(moment, next(iter(states))) for moment, states in sorted(grouped.items())], None


def _integrate(rows: list[StateObservation], interval_start: datetime, interval_end: datetime) -> float:
    before = [row for row in rows if row.observed_at <= interval_start]
    if not before:
        raise ValueError("missing state seed")
    state = before[-1].state
    cursor = interval_start
    active_seconds = 0.0
    for row in rows:
        if row.observed_at <= interval_start:
            continue
        if row.observed_at >= interval_end:
            break
        if state:
            active_seconds += (row.observed_at - cursor).total_seconds()
        cursor = row.observed_at
        state = row.state
    if state:
        active_seconds += (interval_end - cursor).total_seconds()
    return active_seconds / 60.0


def equipment_stream_outcome(
    stream: str,
    states: list[StateObservation],
    *,
    local_date: str,
    timezone: str,
    start_counter: CounterSample,
    end_counter: CounterSample,
) -> StreamOutcome:
    """Integrate right-continuous state and reconcile an exact counter delta."""
    if stream not in EQUIPMENT_STREAMS:
        raise ValueError(f"unknown equipment stream {stream!r}")
    start, end = _local_window(local_date, timezone)
    seed_lo = start - timedelta(seconds=90)
    for sample in (start_counter, end_counter):
        if (
            type(sample.reset_epoch) is not str
            or not sample.reset_epoch
            or unicodedata.normalize("NFC", sample.reset_epoch) != sample.reset_epoch
        ):
            return StreamOutcome(stream, None, False, "invalid_counter_reset_epoch")
        if sample.observed_at.tzinfo is None or sample.observed_at.utcoffset() is None:
            return StreamOutcome(stream, None, False, "naive_counter_timestamp")
    if not seed_lo <= start_counter.observed_at.astimezone(start.tzinfo) <= start:
        return StreamOutcome(stream, None, False, "invalid_start_counter_time")
    end_local = end_counter.observed_at.astimezone(start.tzinfo)
    if not end - timedelta(seconds=90) <= end_local < end:
        return StreamOutcome(stream, None, False, "invalid_end_counter_time")
    if start_counter.reset_epoch != end_counter.reset_epoch:
        return StreamOutcome(stream, None, False, "counter_reset_or_wrap")
    clean, reason = _dedupe_states(states)
    if reason:
        return StreamOutcome(stream, None, False, reason)
    seed_rows = [row for row in clean if seed_lo <= row.observed_at.astimezone(start.tzinfo) <= start]
    if not seed_rows:
        return StreamOutcome(stream, None, False, "missing_fresh_direct_state_seed")
    try:
        window_minutes = _integrate(clean, start, end)
        counter_interval_minutes = _integrate(clean, start_counter.observed_at, end_counter.observed_at)
    except ValueError as exc:
        return StreamOutcome(stream, None, False, str(exc).replace(" ", "_"))
    try:
        delta = _finite(end_counter.value_minutes, "end counter") - _finite(
            start_counter.value_minutes, "start counter"
        )
    except ValueError:
        return StreamOutcome(stream, None, False, "invalid_counter_value")
    tolerance = max(1.0, abs(counter_interval_minutes) * 0.01)
    if delta < 0 or abs(delta - counter_interval_minutes) > tolerance:
        return StreamOutcome(stream, None, False, "counter_state_reconciliation")
    return StreamOutcome(stream, window_minutes, True, None)


def nine_stream_burden(streams: list[StreamOutcome]) -> tuple[float | None, str | None]:
    by_stream = {row.stream: row for row in streams}
    if len(streams) != len(EQUIPMENT_STREAMS) or len(by_stream) != len(EQUIPMENT_STREAMS):
        return None, "missing_or_extra_equipment_stream"
    if set(by_stream) != set(EQUIPMENT_STREAMS):
        return None, "missing_or_extra_equipment_stream"
    invalid = []
    for name in EQUIPMENT_STREAMS:
        row = by_stream[name]
        value = row.active_or_open_minutes
        if (
            row.valid is not True
            or row.reason is not None
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1_080
        ):
            invalid.append(name)
    if invalid:
        return None, "invalid_streams:" + ",".join(invalid)
    return sum(float(by_stream[name].active_or_open_minutes) for name in EQUIPMENT_STREAMS), None


def make_randomized_itt_row(
    *,
    assignment_id: str,
    local_date: str,
    blinded_label: Literal["X", "Y"],
    climate: tuple[float | None, float | None, str | None],
    equipment: tuple[float | None, str | None],
    fallback_or_rescue: bool,
    exposure_seconds: int,
) -> RandomizedIttRow:
    """Always emit one assigned-day row, even with fallback/rescue/no exposure."""
    if not isinstance(assignment_id, str) or not assignment_id:
        raise ValueError("assignment_id must be a nonempty string")
    try:
        parsed_date = date.fromisoformat(local_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("local_date must be canonical YYYY-MM-DD") from exc
    if parsed_date.isoformat() != local_date:
        raise ValueError("local_date must be canonical YYYY-MM-DD")
    if blinded_label not in ("X", "Y"):
        raise ValueError("blinded_label must be exactly X or Y")
    if type(fallback_or_rescue) is not bool:
        raise TypeError("fallback_or_rescue must be an exact boolean")
    if type(exposure_seconds) is not int or not 0 <= exposure_seconds <= ANALYZED_SECONDS:
        raise ValueError(f"exposure_seconds must be an integer in [0,{ANALYZED_SECONDS}]")
    temp, vpd, climate_reason = climate
    burden, equipment_reason = equipment
    for value, name, maximum in (
        (temp, "temperature corridor outcome", None),
        (vpd, "VPD corridor outcome", None),
        (burden, "nine-stream burden", 9 * 1_080),
    ):
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (maximum is not None and value > maximum)
        ):
            raise ValueError(f"{name} must be finite, nonnegative, and within its locked bound")
    complete = temp is not None and vpd is not None and burden is not None
    missing = None if complete else ";".join(reason for reason in (climate_reason, equipment_reason) if reason)
    return RandomizedIttRow(
        assignment_id=assignment_id,
        local_date=local_date,
        blinded_label=blinded_label,
        execution_phase="randomized",
        temperature_corridor_distance_f=temp,
        vpd_corridor_distance_kpa=vpd,
        nine_control_state_minutes=burden,
        outcome_complete=complete,
        fallback_or_rescue=fallback_or_rescue,
        exposure_seconds=exposure_seconds,
        per_protocol_exposure_complete=exposure_seconds >= PER_PROTOCOL_EXPOSURE_SECONDS,
        missing_reason=missing,
    )


VALID_PHASE_KIND_WORK: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("shadow", "shadow_preview", "preview"),
        ("commissioning", "commissioning_probe", "readiness_operation"),
        ("commissioning", "commissioning_canary", "readiness_operation"),
        ("aa_rehearsal", "aa_baseline_rehearsal", "readiness_operation"),
        ("commissioning", "baseline_recovery", "recovery_operation"),
        ("aa_rehearsal", "baseline_recovery", "recovery_operation"),
        ("randomized", "randomized_assignment", "assignment"),
        ("randomized", "baseline_recovery", "recovery_operation"),
    }
)


def validate_phase_kind_work(execution_phase: str, operation_kind: str, work_type: str) -> None:
    if (execution_phase, operation_kind, work_type) not in VALID_PHASE_KIND_WORK:
        raise ValueError(f"illegal protocol-v2 phase/kind/work pairing: {execution_phase}/{operation_kind}/{work_type}")
