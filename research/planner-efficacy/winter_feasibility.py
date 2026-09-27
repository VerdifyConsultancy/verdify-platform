#!/usr/bin/env python3
"""Read-only, prospective 60-local-day winter feasibility evidence freezer.

Register an instance before its first observation day. After each 06:00–24:00
Denver window, collect one immutable database snapshot. The final manifest
accounts for every calendar day, including days without an extract. Neither
this tool nor its output authorizes an experiment, a device write, or an
efficacy/resource-savings claim.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Denver")
STUDY_ID = "verdify-winter-feasibility-2026-27"
INSTANCE_SCHEMA = "verdify-winter-feasibility-instance-v1"
DAY_SCHEMA = "verdify-winter-feasibility-day-v1"
MANIFEST_SCHEMA = "verdify-winter-feasibility-manifest-v1"
TARGET_SCHEMA = "verdify-winter-frozen-crop-target-v1"
DAY_COUNT = 60
DAILY_COLLECTION_GRACE = timedelta(hours=24)
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "research/planner-efficacy/protocols/seasonal-decision-2026-09-27.md"
SOURCE = Path(__file__).resolve()

# Every query is a bounded SELECT under one repeatable-read READ ONLY snapshot.
# Climate rows are database flush snapshots, not independently timed probes.
QUERIES = {
    "climate_flush": """
        SELECT ts, temp_north, vpd_north, temp_east, vpd_east,
               temp_west, vpd_west
          FROM public.climate
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, temp_north NULLS FIRST, temp_east NULLS FIRST,
                  temp_west NULLS FIRST, vpd_north NULLS FIRST,
                  vpd_east NULLS FIRST, vpd_west NULLS FIRST
    """,
    "cfg_readback": """
        SELECT ts, parameter, value
          FROM public.setpoint_snapshot
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
           AND zone IS NULL AND band_role IS NULL AND target_value IS NULL
           AND parameter = ANY($3::text[])
         ORDER BY ts, parameter, value
    """,
    "served_band": """
        SELECT ts, parameter, zone, band_role, value, target_value
          FROM public.setpoint_snapshot
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
           AND zone IS NOT NULL AND band_role IS NOT NULL
         ORDER BY ts, parameter, zone, band_role, value
    """,
    "forecast_asof": """
        SELECT DISTINCT ON (date_trunc('hour', ts))
               ts, fetched_at, temp_f, vpd_kpa, rh_pct, wind_speed_mph,
               solar_w_m2, precip_prob_pct
          FROM public.weather_forecast
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
           AND fetched_at <= ts
         ORDER BY date_trunc('hour', ts), fetched_at DESC, ts DESC
    """,
    "outdoor_observed": """
        SELECT ts, source, temp_f, rh_pct, wind_speed_mph,
               solar_irradiance_w_m2, precip_in
          FROM public.weather_station
         WHERE ts >= $1 AND ts < $2
         ORDER BY ts, source
    """,
    "firmware_observed": """
        SELECT ts, firmware_version, active_probe_count
          FROM public.diagnostics
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, firmware_version
    """,
    "equipment_edges": """
        SELECT ts, equipment, state
          FROM public.equipment_state
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, equipment, state
    """,
    "equipment_seed": """
        SELECT DISTINCT ON (equipment) ts, equipment, state
          FROM public.equipment_state
         WHERE greenhouse_id = 'vallery' AND ts < $1
         ORDER BY equipment, ts DESC, state
    """,
    "power_observed": """
        SELECT ts, watts_total, watts_heat, watts_fans, watts_other, kwh_today
          FROM public.energy
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, watts_total NULLS FIRST
    """,
    "water_events": """
        SELECT id, ts, prior_ts, source, meter_id, event_type,
               prior_total_gal, total_gal, delta_gal, quality_flag,
               materializer_revision, attribution_class, attributed_scope,
               attribution_quality, candidate_run_count
          FROM public.water_meter_events
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, id
    """,
    "safety_events": """
        SELECT id, ts, alert_type, severity, source, category
          FROM public.alert_log
         WHERE greenhouse_id = 'vallery' AND ts >= $1 AND ts < $2
         ORDER BY ts, id
    """,
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must have an explicit UTC offset")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must have an explicit UTC offset")
    return parsed.astimezone(UTC)


def _day(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("local date must be canonical YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("local date must be canonical YYYY-MM-DD")
    return parsed


def bounds(local_day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(local_day, time(6), tzinfo=ZONE).astimezone(UTC)
    end = datetime.combine(local_day + timedelta(days=1), time(), tzinfo=ZONE).astimezone(UTC)
    if end - start != timedelta(hours=18):
        raise ValueError("observation day crosses a Denver offset transition")
    return start, end


def _source_identity() -> tuple[str, str, str]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    if not GIT_SHA.fullmatch(commit):
        raise ValueError("source Git revision is invalid")
    for path in (SOURCE, PROTOCOL):
        relative = path.relative_to(REPO).as_posix()
        try:
            committed = subprocess.check_output(
                ["git", "show", f"HEAD:{relative}"], cwd=REPO, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"source file is not committed at HEAD: {relative}") from exc
        if committed != path.read_bytes():
            raise ValueError(f"source file differs from HEAD: {relative}")
    return commit, digest(SOURCE.read_bytes()), digest(PROTOCOL.read_bytes())


def validate_instance(instance: dict, *, require_current_source: bool = True) -> tuple[date, str]:
    expected = {
        "schema",
        "study_id",
        "greenhouse_id",
        "timezone",
        "start_local_date",
        "day_count",
        "registered_at",
        "source_git_sha",
        "extractor_sha256",
        "protocol_sha256",
        "panel_source_sha256",
        "crop_target_source_sha256",
        "cfg_schema_sha256",
        "firmware_revision",
        "observer_role",
        "archive_id",
    }
    if not isinstance(instance, dict) or set(instance) != expected:
        raise ValueError("instance field set differs from the frozen contract")
    if (
        instance["schema"],
        instance["study_id"],
        instance["greenhouse_id"],
        instance["timezone"],
        instance["day_count"],
    ) != (INSTANCE_SCHEMA, STUDY_ID, "vallery", "America/Denver", DAY_COUNT):
        raise ValueError("instance identity or 60-day schedule mismatch")
    start = _day(instance["start_local_date"])
    offsets = set()
    for offset in range(DAY_COUNT):
        day = start + timedelta(days=offset)
        bounds(day)
        offsets.add(datetime.combine(day, time(6), tzinfo=ZONE).utcoffset())
    offsets.add(datetime.combine(start + timedelta(days=DAY_COUNT), time(), tzinfo=ZONE).utcoffset())
    if len(offsets) != 1:
        raise ValueError("60-day calendar crosses a Denver offset transition")
    registered = _timestamp(instance["registered_at"])
    if registered >= bounds(start)[0]:
        raise ValueError("instance was not registered before the first observation")
    for key in (
        "extractor_sha256",
        "protocol_sha256",
        "panel_source_sha256",
        "crop_target_source_sha256",
        "cfg_schema_sha256",
    ):
        if not isinstance(instance[key], str) or not SHA256.fullmatch(instance[key]):
            raise ValueError(f"{key} must be a SHA-256")
    if not isinstance(instance["source_git_sha"], str) or not GIT_SHA.fullmatch(instance["source_git_sha"]):
        raise ValueError("source_git_sha must be a full Git SHA")
    for key in ("firmware_revision", "observer_role", "archive_id"):
        if not isinstance(instance[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", instance[key]):
            raise ValueError(f"{key} must be a bounded concrete identifier")
    if require_current_source:
        current = _source_identity()
        if current != (instance["source_git_sha"], instance["extractor_sha256"], instance["protocol_sha256"]):
            raise ValueError("running source bytes or Git revision differ from registration")
    return start, digest(canonical(instance))


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite database value; preserve failure and investigate")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _coverage(
    sources: dict[str, list[dict]], start: datetime, end: datetime, canonical_fields: tuple[str, ...]
) -> dict:
    """Describe database coverage without promoting it to physical eligibility."""
    complete_minutes: set[datetime] = set()
    fields = ("temp_north", "vpd_north", "temp_east", "vpd_east", "temp_west", "vpd_west")
    for row in sources["climate_flush"]:
        if all(row[field] is not None for field in fields):
            minute = _timestamp(row["ts"]).replace(second=0, microsecond=0)
            if start <= minute < end:
                complete_minutes.add(minute)
    bins = [start + timedelta(minutes=15 * index) for index in range(72)]
    eligible_bins = sum(
        sum(bucket <= minute < bucket + timedelta(minutes=15) for minute in complete_minutes) >= 12 for bucket in bins
    )
    by_batch: dict[str, set[str]] = {}
    for row in sources["cfg_readback"]:
        by_batch.setdefault(row["ts"], set()).add(row["parameter"])
    expected = set(canonical_fields)
    complete_batches = sorted(ts for ts, fields_seen in by_batch.items() if fields_seen == expected)
    return {
        "sample_basis": "database_flush_snapshot_only",
        "unique_complete_six_field_minutes": len(complete_minutes),
        "bins_meeting_12_of_15_database_minutes": eligible_bins,
        "expected_15_minute_bins": 72,
        "complete_48_cfg_flush_batches": len(complete_batches),
        "latest_complete_48_cfg_flush_batch": complete_batches[-1] if complete_batches else None,
        "cfg_generation": "unobservable_in_setpoint_snapshot",
        "physical_panel_eligible": False,
    }


async def capture(instance: dict, local_day: date, dsn: str) -> dict:
    start_day, instance_sha = validate_instance(instance)
    if not start_day <= local_day < start_day + timedelta(days=DAY_COUNT):
        raise ValueError("day is outside the registered 60-day calendar")
    window_start, window_end = bounds(local_day)
    if datetime.now(UTC) < window_end:
        raise ValueError("observation window is not complete")
    import asyncpg  # optional until a read-only collection is actually run

    from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER

    conn = await asyncpg.connect(dsn=dsn, timeout=10)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '30s'")
            await conn.execute("SET LOCAL lock_timeout = '2s'")
            extracted_at = await conn.fetchval("SELECT statement_timestamp()")
            snapshot = await conn.fetchval("SELECT pg_current_snapshot()::text")
            sources = {}
            for name, sql in QUERIES.items():
                args = (
                    (window_start,)
                    if name == "equipment_seed"
                    else (
                        (window_start, window_end, list(CANONICAL_FIELD_ORDER))
                        if name == "cfg_readback"
                        else (window_start, window_end)
                    )
                )
                rows = await conn.fetch(sql, *args)
                sources[name] = [_json_safe(dict(row)) for row in rows]
    finally:
        await conn.close()
    return {
        "schema": DAY_SCHEMA,
        "study_id": STUDY_ID,
        "instance_sha256": instance_sha,
        "local_day": local_day.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "extracted_at": _json_safe(extracted_at),
        "daily_collection_timely": extracted_at <= window_end + DAILY_COLLECTION_GRACE,
        "database_snapshot": snapshot,
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "unobservable": [
            "onchip_consumed_band_edges",
            "per_probe_callback_freshness",
            "virtual_selector_choice_fallback",
        ],
        "resource_scope": "raw_observations_only_no_complete_metered_total_or_savings_claim",
        "coverage": _coverage(sources, window_start, window_end, CANONICAL_FIELD_ORDER),
        "sources": sources,
    }


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def validate_target_source(raw: bytes, *, start: date, registered_at: datetime) -> dict:
    """Require one pre-window target revision with every scheduled quarter hour."""
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("crop target source is not valid JSON") from exc
    required = {
        "schema",
        "study_id",
        "greenhouse_id",
        "timezone",
        "start_local_date",
        "recorded_at",
        "effective_from",
        "effective_to",
        "target_version",
        "fixed_panel_target_revision_id",
        "source_profile_state_sha256",
        "profile_revision_ids",
        "crop_assignment_revision_sha256",
        "target_bins_sha256",
        "target_bins",
    }
    if not isinstance(value, dict) or set(value) != required or canonical(value) != raw:
        raise ValueError("crop target source is not the canonical frozen-target contract")
    if (value["schema"], value["study_id"], value["greenhouse_id"], value["timezone"], value["start_local_date"]) != (
        TARGET_SCHEMA,
        STUDY_ID,
        "vallery",
        "America/Denver",
        start.isoformat(),
    ):
        raise ValueError("crop target source study identity differs from registration")
    first_start = bounds(start)[0]
    last_end = bounds(start + timedelta(days=DAY_COUNT - 1))[1]
    recorded = _timestamp(value["recorded_at"])
    if not recorded <= registered_at.astimezone(UTC) < first_start:
        raise ValueError("crop target revision was not recorded before registration and first observation")
    if _timestamp(value["effective_from"]) != first_start or _timestamp(value["effective_to"]) != last_end:
        raise ValueError("crop target effective interval differs from 60-day calendar")
    if not isinstance(value["target_version"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value["target_version"]
    ):
        raise ValueError("crop target version is not a concrete bounded identifier")
    revision = value["fixed_panel_target_revision_id"]
    if type(revision) is not int or revision < 1:
        raise ValueError("positive fixed-panel target revision ID required")
    revisions = value["profile_revision_ids"]
    if (
        not isinstance(revisions, list)
        or not revisions
        or len(revisions) > 10_000
        or any(type(item) is not int or item < 1 for item in revisions)
        or revisions != sorted(set(revisions))
    ):
        raise ValueError("source profile revision IDs must be positive, unique and sorted")
    for key in ("source_profile_state_sha256", "crop_assignment_revision_sha256", "target_bins_sha256"):
        if not isinstance(value[key], str) or not SHA256.fullmatch(value[key]):
            raise ValueError(f"{key} must be a SHA-256")
    bins = value["target_bins"]
    if not isinstance(bins, list) or len(bins) != DAY_COUNT * 72:
        raise ValueError("crop target source must contain exactly 4,320 scheduled bins")
    expected_keys = {"bucket_start", "temp_low", "temp_high", "vpd_low", "vpd_high"}
    index = 0
    for offset in range(DAY_COUNT):
        window_start, _ = bounds(start + timedelta(days=offset))
        for quarter in range(72):
            item = bins[index]
            expected_ts = window_start + timedelta(minutes=15 * quarter)
            if (
                not isinstance(item, dict)
                or set(item) != expected_keys
                or _timestamp(item["bucket_start"]) != expected_ts
            ):
                raise ValueError(f"crop target bin {index} is missing, reordered or outside the calendar")
            for axis in ("temp", "vpd"):
                low, high = item[f"{axis}_low"], item[f"{axis}_high"]
                if (
                    type(low) not in (float, int)
                    or type(high) not in (float, int)
                    or not math.isfinite(low)
                    or not math.isfinite(high)
                    or low > high
                ):
                    raise ValueError(f"crop target bin {index} has invalid {axis} bounds")
            index += 1
    if digest(canonical(bins)) != value["target_bins_sha256"]:
        raise ValueError("frozen crop target bins hash mismatch")
    return value


def register(
    *,
    start: date,
    panel_source: Path,
    crop_target_source: Path,
    cfg_schema_source: Path,
    firmware_revision: str,
    observer_role: str,
    archive_id: str,
    now: datetime,
) -> dict:
    """Create a concrete preregistration from preserved source artifact bytes."""
    validate_target_source(crop_target_source.read_bytes(), start=start, registered_at=now)
    commit, extractor_sha, protocol_sha = _source_identity()
    instance = {
        "schema": INSTANCE_SCHEMA,
        "study_id": STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": start.isoformat(),
        "day_count": DAY_COUNT,
        "registered_at": now.astimezone(UTC).isoformat(),
        "source_git_sha": commit,
        "extractor_sha256": extractor_sha,
        "protocol_sha256": protocol_sha,
        "panel_source_sha256": digest(panel_source.read_bytes()),
        "crop_target_source_sha256": digest(crop_target_source.read_bytes()),
        "cfg_schema_sha256": digest(cfg_schema_source.read_bytes()),
        "firmware_revision": firmware_revision,
        "observer_role": observer_role,
        "archive_id": archive_id,
    }
    validate_instance(instance)
    if now.astimezone(UTC) >= bounds(start)[0]:
        raise ValueError("candidate start already began; choose a future prospective interval")
    return instance


ARCHIVED_SOURCES = {
    "panel_source_sha256": "panel-source",
    "crop_target_source_sha256": "crop-target-source",
    "cfg_schema_sha256": "cfg-schema-source",
}


def verify_archived_sources(instance: dict, out_dir: Path) -> None:
    for field, name in ARCHIVED_SOURCES.items():
        path = out_dir / "sources" / name
        if not path.is_file() or digest(path.read_bytes()) != instance[field]:
            raise ValueError(f"registered source bytes missing or changed: {name}")


def manifest(instance: dict, instance_raw: bytes, days_dir: Path, *, as_of: datetime) -> dict:
    start, instance_sha = validate_instance(instance)
    if digest(instance_raw) != instance_sha or as_of.tzinfo is None:
        raise ValueError("instance bytes or as-of timestamp invalid")
    calendar = []
    for offset in range(DAY_COUNT):
        day = start + timedelta(days=offset)
        window_start, window_end = bounds(day)
        path = days_dir / f"{day.isoformat()}.json"
        row = {
            "local_day": day.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        if path.exists():
            raw = path.read_bytes()
            payload = json.loads(raw)
            if (
                canonical(payload) != raw
                or payload.get("schema") != DAY_SCHEMA
                or payload.get("instance_sha256") != instance_sha
                or payload.get("local_day") != day.isoformat()
                or payload.get("window_start") != row["window_start"]
                or payload.get("window_end") != row["window_end"]
            ):
                raise ValueError(f"day artifact identity or canonical bytes mismatch: {path.name}")
            extracted_at = _timestamp(payload.get("extracted_at"))
            if not window_end <= extracted_at <= as_of.astimezone(UTC):
                raise ValueError(f"day artifact chronology differs from manifest as-of: {path.name}")
            timely = extracted_at <= window_end + DAILY_COLLECTION_GRACE
            if payload.get("daily_collection_timely") is not timely:
                raise ValueError(f"day artifact collection timing mismatch: {path.name}")
            counts = {name: len(payload["sources"][name]) for name in QUERIES}
            reason = (
                "daily_extract_late"
                if not timely
                else "no_climate_flush_rows"
                if counts["climate_flush"] == 0
                else None
            )
            row.update(
                status="captured" if timely else "captured_late",
                file=path.name,
                sha256=digest(raw),
                rows=counts,
                missing_reason=reason,
            )
        else:
            row.update(
                status="scheduled" if as_of.astimezone(UTC) < window_end else "uncollected_completed",
                missing_reason=None if as_of.astimezone(UTC) < window_end else "no_immutable_day_artifact",
            )
        calendar.append(row)
    return {
        "schema": MANIFEST_SCHEMA,
        "study_id": STUDY_ID,
        "instance_sha256": instance_sha,
        "as_of": as_of.astimezone(UTC).isoformat(),
        "calendar": calendar,
        "interpretation": "observational_feasibility_only_no_randomization_or_causal_effect",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "collect", "manifest"))
    parser.add_argument("--instance", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--day", help="completed Denver local date for collect")
    parser.add_argument("--as-of", help="explicit UTC-offset timestamp for reproducible manifest")
    parser.add_argument("--start", help="future Denver local date for register")
    parser.add_argument("--panel-source", type=Path)
    parser.add_argument("--crop-target-source", type=Path)
    parser.add_argument("--cfg-schema-source", type=Path)
    parser.add_argument("--firmware-revision")
    parser.add_argument("--observer-role")
    parser.add_argument("--archive-id")
    args = parser.parse_args()
    try:
        if args.command == "register":
            needed = (
                args.start,
                args.panel_source,
                args.crop_target_source,
                args.cfg_schema_source,
                args.firmware_revision,
                args.observer_role,
                args.archive_id,
            )
            if args.instance or args.day or args.as_of or not all(needed):
                parser.error("register requires --start, three source files, firmware, observer and archive ID")
            instance = register(
                start=_day(args.start),
                panel_source=args.panel_source,
                crop_target_source=args.crop_target_source,
                cfg_schema_source=args.cfg_schema_source,
                firmware_revision=args.firmware_revision,
                observer_role=args.observer_role,
                archive_id=args.archive_id,
                now=datetime.now(UTC),
            )
            if args.output_dir.exists():
                raise ValueError("registration archive directory already exists")
            args.output_dir.mkdir(mode=0o700, parents=True)
            for source, name in (
                (args.panel_source, "panel-source"),
                (args.crop_target_source, "crop-target-source"),
                (args.cfg_schema_source, "cfg-schema-source"),
            ):
                _write_exclusive(args.output_dir / "sources" / name, source.read_bytes())
            verify_archived_sources(instance, args.output_dir)
            target = args.output_dir / "instance.json"
            _write_exclusive(target, canonical(instance))
            print(f"{target} sha256={digest(target.read_bytes())}")
            return
        if not args.instance:
            parser.error("collect and manifest require --instance")
        raw = args.instance.read_bytes()
        instance = json.loads(raw)
        validate_instance(instance)
        if canonical(instance) != raw:
            parser.error("instance must use canonical JSON bytes")
        if args.instance.resolve() != (args.output_dir / "instance.json").resolve():
            raise ValueError("instance must be the registered archive instance.json")
        verify_archived_sources(instance, args.output_dir)
        if args.command == "collect":
            if args.as_of or not args.day:
                parser.error("collect requires --day and no --as-of")
            dsn = os.environ.get("DB_DSN")
            if not dsn:
                parser.error("DB_DSN must identify an existing read-only DB connection")
            day = _day(args.day)
            payload = asyncio.run(capture(instance, day, dsn))
            target = args.output_dir / "days" / f"{day.isoformat()}.json"
            _write_exclusive(target, canonical(payload))
            print(f"{target} sha256={digest(target.read_bytes())}")
        else:
            if args.day or not args.as_of:
                parser.error("manifest requires --as-of and no --day")
            payload = manifest(instance, raw, args.output_dir / "days", as_of=_timestamp(args.as_of))
            target = args.output_dir / f"manifest-{args.as_of.replace(':', '').replace('+', '_')}.json"
            _write_exclusive(target, canonical(payload))
            print(f"{target} sha256={digest(target.read_bytes())}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"winter feasibility extraction refused: {exc}\n")


if __name__ == "__main__":
    main()
