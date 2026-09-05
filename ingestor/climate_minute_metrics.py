"""Versioned observed-minute diagnostics; deliberately not crop/experiment outcomes.

House averages may have changing sensor membership. Setpoint-log references
are not frozen crop targets or observed firmware consumption. No interpolation,
hold-last-value exposure, nominal row-duration or physical stress hours here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFINITION = "house-average-observed-minute-v1"
AXES = {"temp": "degF", "vpd": "kPa"}
CALCULATION_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

WINDOW_SQL = """
SELECT $1::date::timestamp AT TIME ZONE 'America/Denver' AS start,
       LEAST(($1::date + 1)::timestamp AT TIME ZONE 'America/Denver',
             date_trunc('minute', current_timestamp)) AS end
"""
READINGS_SQL = """
SELECT ts, greenhouse_id, temp_avg, vpd_avg FROM public.climate
WHERE greenhouse_id = $3 AND ts >= $1 AND ts < $2
ORDER BY ts
"""
EVENTS_SQL = """
WITH prior AS (
    SELECT parameter, max(ts) AS ts FROM public.setpoint_changes
    WHERE greenhouse_id = $3 AND ts < $1
      AND parameter = ANY($4::text[]) GROUP BY parameter
)
SELECT e.parameter, e.value, e.ts, e.greenhouse_id, e.source, e.expired_at, e.superseded_by_ts
FROM public.setpoint_changes e
WHERE e.greenhouse_id = $3 AND e.parameter = ANY($4::text[]) AND e.ts < $2
  AND (e.ts >= $1 OR EXISTS (SELECT 1 FROM prior p WHERE p.parameter = e.parameter AND p.ts = e.ts))
ORDER BY e.parameter, e.ts
"""


async def refresh_observed_minute_metrics(conn, target_day):
    """Add a repeatable-read diagnostic revision; no writes to legacy metrics.

    A mixed rollout without migration245 returns unavailable. Once installed,
    SQL/capture failures propagate, never silently discard the evidence write.
    """
    installed = await conn.fetchval("""
        SELECT EXISTS (SELECT 1 FROM pg_attribute
        WHERE attrelid = 'public.daily_summary'::regclass
          AND attname = 'climate_observed_minute_metrics' AND NOT attisdropped)
    """)
    if not installed:
        return None
    async with conn.transaction(isolation="repeatable_read"):
        window = await conn.fetchrow(WINDOW_SQL, target_day)
        start, end = window["start"], window["end"]
        if end < start:
            raise ValueError("cannot measure a future local day")
        rows = await conn.fetch(READINGS_SQL, start, end, "vallery")
        events = await conn.fetch(EVENTS_SQL, start, end, "vallery", ["temp_low", "temp_high", "vpd_low", "vpd_high"])
        result = measure_observed_minutes(rows, events, start=start, end=end)
        status = await conn.execute(
            """
            UPDATE public.daily_summary SET climate_observed_minute_metrics = $2::jsonb
            WHERE date = $1 AND greenhouse_id = 'vallery'
        """,
            target_day,
            json.dumps(result, allow_nan=False),
        )
        if status != "UPDATE 1":
            raise ValueError("observed-minute revision requires exactly one vallery daily row")
    return result


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware datetimes")
    return value.astimezone(UTC)


def _number_token(value):
    if _finite(value):
        return 0.0 if value == 0 else float(value)
    # Preserve invalid/missing input identity without emitting nonstandard JSON.
    return {"invalid_type": type(value).__name__, "representation": repr(value)}


def measure_observed_minutes(readings, band_events, *, start, end, greenhouse_id="vallery"):
    start, end = _utc(start), _utc(end)
    if start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError("window boundaries must align to UTC minutes")
    expected = int((end - start).total_seconds() / 60)
    if not 0 <= expected <= 26 * 60:
        raise ValueError("window must contain 0..1560 minutes")
    if not isinstance(greenhouse_id, str) or not greenhouse_id:
        raise ValueError("greenhouse identity is required")
    slots = [start + timedelta(minutes=i) for i in range(expected)]
    grouped = defaultdict(list)
    canonical_rows, canonical_events = [], []
    ignored_rows = 0
    for row in readings:
        ts = _utc(row["ts"])
        if row.get("greenhouse_id") != greenhouse_id or not start <= ts < end:
            ignored_rows += 1
            continue
        grouped[ts.replace(second=0, microsecond=0)].append(row)
        canonical_rows.append([ts.isoformat(), *(_number_token(row.get(f"{axis}_avg")) for axis in AXES)])
    events = defaultdict(lambda: defaultdict(list))
    for event in band_events:
        ts = _utc(event["ts"])
        parameter = event.get("parameter")
        if (
            event.get("greenhouse_id") != greenhouse_id
            or ts >= end
            or parameter not in {"temp_low", "temp_high", "vpd_low", "vpd_high"}
        ):
            continue
        expiry = event.get("expired_at")
        superseded = event.get("superseded_by_ts")
        limits = [_utc(t) for t in (expiry, superseded) if t is not None]
        valid_until = min(limits) if limits else None
        events[parameter][ts].append((event.get("value"), valid_until))
        canonical_events.append(
            [
                parameter,
                ts.isoformat(),
                _number_token(event.get("value")),
                _utc(expiry).isoformat() if expiry is not None else None,
                _utc(superseded).isoformat() if superseded is not None else None,
                event.get("source"),
            ]
        )

    def bound_at(parameter, minute):
        available = [ts for ts in events[parameter] if ts <= minute]
        if not available:
            return None
        records = events[parameter][max(available)]
        values = [v for v, valid_until in records]
        if (
            not all(_finite(v) for v in values)
            or len(set(values)) != 1
            or len({limit for _, limit in records}) != 1
            or any(limit is not None and minute >= limit for _, limit in records)
        ):
            return None  # latest missing/conflicting event is never backfilled
        return float(values[0])

    minute_results = {axis: {} for axis in AXES}
    rejected = {axis: {"conflicting_timestamp": 0, "missing_value": 0, "missing_or_invalid_bounds": 0} for axis in AXES}
    for minute in slots:
        for axis in AXES:
            by_ts = defaultdict(list)
            for row in grouped[minute]:
                by_ts[_utc(row["ts"])].append(row.get(f"{axis}_avg"))
            values = []
            conflict = False
            for duplicates in by_ts.values():
                tokens = {json.dumps(_number_token(v), sort_keys=True) for v in duplicates}
                if len(tokens) != 1:
                    conflict = True
                    break
                if _finite(duplicates[0]):
                    values.append(float(duplicates[0]))
            if conflict:
                rejected[axis]["conflicting_timestamp"] += 1
                continue
            if not values:
                rejected[axis]["missing_value"] += 1
                continue
            low, high = (bound_at(f"{axis}_{edge}", minute) for edge in ("low", "high"))
            if low is None or high is None or low > high:
                rejected[axis]["missing_or_invalid_bounds"] += 1
                continue
            average = math.fsum(v / len(values) for v in values)
            low_miss, high_miss = max(low - average, 0), max(average - high, 0)
            if not all(math.isfinite(v) for v in (average, low_miss, high_miss)):
                rejected[axis]["missing_value"] += 1
                continue
            minute_results[axis][minute] = (low_miss, high_miss)

    def longest_gap(valid):
        longest = run = 0
        for minute in slots:
            run = 0 if minute in valid else run + 1
            longest = max(longest, run)
        return longest

    summaries = {}
    for axis, unit in AXES.items():
        valid = minute_results[axis]
        n = len(valid)
        low_count = sum(v[0] > 0 for v in valid.values())
        high_count = sum(v[1] > 0 for v in valid.values())
        summaries[axis] = {
            "unit": unit,
            "eligible_minutes": n,
            "in_band_minutes": n - low_count - high_count,
            "in_band_pct": 100 * (n - low_count - high_count) / n if n else None,
            "low_miss_observed_minutes": low_count,
            "high_miss_observed_minutes": high_count,
            "mean_low_distance": math.fsum(v[0] / n for v in valid.values()) if n else None,
            "mean_high_distance": math.fsum(v[1] / n for v in valid.values()) if n else None,
            "mean_outside_distance": math.fsum((v[0] + v[1]) / n for v in valid.values()) if n else None,
            "coverage_fraction": n / expected if expected else None,
            "longest_ineligible_run_minutes": longest_gap(valid),
            "ineligible_minutes": rejected[axis],
        }
    joint = minute_results["temp"].keys() & minute_results["vpd"].keys()
    both_ok = sum(all(minute_results[axis][minute] == (0, 0) for axis in AXES) for minute in joint)
    canonical = {
        "definition": DEFINITION,
        "greenhouse_id": greenhouse_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "readings": sorted(canonical_rows, key=lambda row: json.dumps(row, sort_keys=True)),
        "band_events": sorted(canonical_events, key=lambda row: json.dumps(row, sort_keys=True)),
    }
    return {
        "definition": DEFINITION,
        "calculation_source_sha256": CALCULATION_SOURCE_SHA256,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "greenhouse_id": greenhouse_id,
        "input_sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "input_rows": len(canonical_rows),
        "ignored_rows": ignored_rows,
        "expected_minutes": expected,
        "observed_minutes": len([m for m in slots if grouped[m]]),
        "axes": summaries,
        "joint": {
            "eligible_minutes": len(joint),
            "in_band_minutes": both_ok,
            "in_band_pct": 100 * both_ok / len(joint) if joint else None,
            "coverage_fraction": len(joint) / expected if expected else None,
            "longest_ineligible_run_minutes": longest_gap(joint),
        },
        "target_basis": "latest_setpoint_log_event_as_of_minute_start_provenance_unqualified_not_frozen_crop_targets",
        "sample_basis": "mean_of_finite_unique_timestamp_house_averages_per_UTC_minute",
        "duration_basis": "observed_minute_slots_not_continuous_physical_exposure",
        "fixed_sensor_panel": False,
        "duration_weighted": False,
        "physical_proof_eligible": False,
        "crop_outcome_eligible": False,
        "experiment_endpoint_eligible": False,
        "worst_measured_zone": None,
    }
