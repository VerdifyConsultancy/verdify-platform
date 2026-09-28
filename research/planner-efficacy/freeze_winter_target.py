#!/usr/bin/env python3
"""Freeze a prospective north/east/west panel-mean crop grading reference.

Capture is read-only. Build emits a reviewable declaration SQL file; it never
executes that SQL. Finalize reads the declared row back and rejects any drift.
This target is for the separate observational winter packet only: it does not
represent per-zone compliance, an on-chip consumed band, or crop persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import winter_feasibility as winter

SNAPSHOT_SCHEMA = "verdify-winter-target-source-snapshot-v1"
DRAFT_SCHEMA = "verdify-winter-target-declaration-draft-v1"
RULE = "fixed_panel_equal_zone_ideal_mean_v1"
ZONES = ("north", "east", "west")
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
DB_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verdify-db.sh"

SNAPSHOT_SQL = """
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH profiles AS (
    SELECT p.* FROM public.crop_target_profiles p
     WHERE p.greenhouse_id = 'vallery'
), assignments AS (
    SELECT c.* FROM public.crops c WHERE c.greenhouse_id = 'vallery'
), profile_ids AS (
    SELECT p.id, r.revision_id
      FROM profiles p JOIN LATERAL (
        SELECT revision_id, operation, profile
          FROM public.crop_target_profile_revisions
         WHERE profile_id = p.id ORDER BY revision_id DESC LIMIT 1
      ) r ON r.operation <> 'delete' AND r.profile = to_jsonb(p)
), assignment_ids AS (
    SELECT c.id, r.revision_id
      FROM assignments c JOIN LATERAL (
        SELECT revision_id, operation, crop
          FROM public.crop_assignment_revisions
         WHERE crop_id = c.id ORDER BY revision_id DESC LIMIT 1
      ) r ON r.operation <> 'delete' AND r.crop = to_jsonb(c)
)
SELECT jsonb_build_object(
    'schema', 'verdify-winter-target-source-snapshot-v1',
    'captured_at', clock_timestamp(),
    'database_snapshot', pg_current_snapshot()::text,
    'greenhouse_id', 'vallery',
    'profiles', (SELECT COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]'::jsonb) FROM profiles p),
    'assignments', (SELECT COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.id), '[]'::jsonb) FROM assignments c),
    'source_profile_state_sha256', (SELECT encode(pg_catalog.sha256(convert_to(
        COALESCE(jsonb_agg(to_jsonb(p) ORDER BY p.id), '[]'::jsonb)::text, 'UTF8')), 'hex') FROM profiles p),
    'source_assignment_state_sha256', (SELECT encode(pg_catalog.sha256(convert_to(
        COALESCE(jsonb_agg(to_jsonb(c) ORDER BY c.id), '[]'::jsonb)::text, 'UTF8')), 'hex') FROM assignments c),
    'profile_revision_ids', (SELECT COALESCE(jsonb_agg(revision_id ORDER BY revision_id), '[]'::jsonb) FROM profile_ids),
    'assignment_revision_ids', (SELECT COALESCE(jsonb_agg(revision_id ORDER BY revision_id), '[]'::jsonb) FROM assignment_ids),
    'profile_row_count', (SELECT count(*) FROM profiles),
    'assignment_row_count', (SELECT count(*) FROM assignments)
)::text;
ROLLBACK;
"""


def _db_json(sql: str) -> dict:
    result = subprocess.run(
        [str(DB_SCRIPT), "prod", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    objects = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if len(objects) != 1:
        raise ValueError("database read did not return exactly one JSON object")
    return json.loads(objects[0])


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def _canonical(value: object) -> bytes:
    return winter.canonical(value)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ids(value: object, label: str) -> None:
    if not isinstance(value, list) or not value or any(type(n) is not int or n < 1 for n in value):
        raise ValueError(f"{label} requires positive revision IDs")
    if value != sorted(set(value)):
        raise ValueError(f"{label} must be unique and sorted")


def _validate_snapshot(snapshot: dict) -> None:
    required = {
        "schema",
        "captured_at",
        "database_snapshot",
        "greenhouse_id",
        "profiles",
        "assignments",
        "source_profile_state_sha256",
        "source_assignment_state_sha256",
        "profile_revision_ids",
        "assignment_revision_ids",
        "profile_row_count",
        "assignment_row_count",
    }
    if set(snapshot) != required or snapshot["schema"] != SNAPSHOT_SCHEMA or snapshot["greenhouse_id"] != "vallery":
        raise ValueError("wrong target-source snapshot contract")
    winter._timestamp(snapshot["captured_at"])
    for name in ("profiles", "assignments"):
        rows = snapshot[name]
        if (
            not isinstance(rows, list)
            or not rows
            or snapshot[f"{name[:-1] if name == 'profiles' else 'assignment'}_row_count"] != len(rows)
        ):
            raise ValueError(f"{name} snapshot is empty or incomplete")
        if any(not isinstance(row, dict) or row.get("greenhouse_id") != "vallery" for row in rows):
            raise ValueError(f"{name} contains another greenhouse")
        ids = [row.get("id") for row in rows]
        if any(type(row_id) is not int or row_id < 1 for row_id in ids) or ids != sorted(set(ids)):
            raise ValueError(f"{name} IDs must be unique and ordered")
    for key in ("source_profile_state_sha256", "source_assignment_state_sha256"):
        if not isinstance(snapshot[key], str) or not SHA.fullmatch(snapshot[key]):
            raise ValueError(f"{key} is invalid")
    for key, rows in (("profile_revision_ids", "profiles"), ("assignment_revision_ids", "assignments")):
        _ids(snapshot[key], key)
        if len(snapshot[key]) != len(snapshot[rows]):
            raise ValueError(f"{key} does not cover every source row")


def _season(day: date) -> str:
    if day.month in (3, 4, 5):
        return "spring"
    if day.month in (6, 7, 8):
        return "summer"
    if day.month in (9, 10, 11):
        return "fall"
    return "winter"


def _number(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"nonfinite or missing {label}")
    return float(value)


def _zone_band(profiles: list[dict], assignments: list[dict], *, zone: str, season: str, hour: int) -> dict:
    matching = [p for p in profiles if p.get("season") == season and p.get("hour_of_day") == hour]
    active = [c for c in assignments if c.get("is_active") is True and c.get("zone") == zone]
    joined = [
        p
        for c in active
        for p in matching
        if p.get("crop_catalog_id") is not None and p["crop_catalog_id"] == c.get("crop_catalog_id")
    ]
    if not joined:
        joined = [p for p in matching if p.get("crop_type") == "_default"]
        if len(joined) != 1:
            raise ValueError(f"missing or ambiguous _default for {zone} {season} hour {hour}")
    band = {
        "temp_low": max(_number(p.get("temp_ideal_min"), "temp_ideal_min") for p in joined),
        "temp_high": min(_number(p.get("temp_ideal_max"), "temp_ideal_max") for p in joined),
        "vpd_low": max(_number(p.get("vpd_ideal_min"), "vpd_ideal_min") for p in joined),
        "vpd_high": min(_number(p.get("vpd_ideal_max"), "vpd_ideal_max") for p in joined),
    }
    if band["temp_low"] > band["temp_high"] or band["vpd_low"] > band["vpd_high"]:
        raise ValueError(f"inverted {zone} ideal crop intersection")
    return band


def build(snapshot: dict, *, start: date, target_version: str, source_sha256: str) -> dict:
    _validate_snapshot(snapshot)
    if not IDENTIFIER.fullmatch(target_version) or not SHA.fullmatch(source_sha256):
        raise ValueError("target version or snapshot SHA-256 invalid")
    first = winter.bounds(start)[0]
    end = winter.bounds(start + timedelta(days=winter.DAY_COUNT - 1))[1]
    if winter._timestamp(snapshot["captured_at"]) >= first or datetime.now(UTC) >= first:
        raise ValueError("source capture must precede the prospective window")
    # Resolve season from each future local day, then apply fn_zone_band's global
    # fallback when that season has no joinable crop_catalog_id profile rows.
    bins = []
    for offset in range(winter.DAY_COUNT):
        day = start + timedelta(days=offset)
        window_start, _ = winter.bounds(day)
        requested = _season(day)
        season = (
            requested
            if any(p.get("season") == requested and p.get("crop_catalog_id") is not None for p in snapshot["profiles"])
            else "spring"
        )
        for quarter in range(72):
            bucket = window_start + timedelta(minutes=15 * quarter)
            local_hour = (bucket.astimezone(winter.ZONE)).hour
            zone_bands = [
                _zone_band(snapshot["profiles"], snapshot["assignments"], zone=zone, season=season, hour=local_hour)
                for zone in ZONES
            ]
            bins.append(
                {
                    "bucket_start": bucket.isoformat(),
                    **{
                        key: math.fsum(sorted(band[key] / 3 for band in zone_bands))
                        for key in ("temp_low", "temp_high", "vpd_low", "vpd_high")
                    },
                }
            )
    draft = {
        "schema": DRAFT_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": start.isoformat(),
        "source_snapshot_sha256": source_sha256,
        "source_captured_at": snapshot["captured_at"],
        "target_rule": RULE,
        "target_version": target_version,
        "effective_from": first.isoformat(),
        "effective_to": end.isoformat(),
        "source_profile_state_sha256": snapshot["source_profile_state_sha256"],
        "source_assignment_state_sha256": snapshot["source_assignment_state_sha256"],
        "profile_revision_ids": snapshot["profile_revision_ids"],
        "assignment_revision_ids": snapshot["assignment_revision_ids"],
        "target_bins_sha256": _sha(_canonical(bins)),
        "target_bins": bins,
    }
    return draft


def declaration_sql(draft: dict) -> str:
    if draft.get("schema") != DRAFT_SCHEMA or draft.get("target_rule") != RULE:
        raise ValueError("wrong declaration draft")
    bins = _canonical(draft["target_bins"]).decode()
    if "$winter_bins$" in bins:
        raise ValueError("unexpected SQL delimiter in target bins")
    arrays = [
        "ARRAY[" + ",".join(str(n) for n in draft[key]) + "]::bigint[]"
        for key in ("profile_revision_ids", "assignment_revision_ids")
    ]
    return f"""-- Review and apply only after migration 255 is live. This changes analysis source only.
BEGIN;
WITH source_bins AS (SELECT $winter_bins${bins}$winter_bins$::jsonb AS bins)
INSERT INTO public.fixed_panel_target_revisions
    (greenhouse_id, target_version, effective_from, effective_to,
     source_profile_state_sha256, source_assignment_state_sha256,
     profile_revision_ids, assignment_revision_ids, target_rule,
     target_bins, db_target_bins_sha256)
SELECT 'vallery', '{draft["target_version"]}', '{draft["effective_from"]}', '{draft["effective_to"]}',
       '{draft["source_profile_state_sha256"]}', '{draft["source_assignment_state_sha256"]}',
       {arrays[0]}, {arrays[1]}, '{RULE}', bins,
       encode(pg_catalog.sha256(convert_to(bins::text, 'UTF8')), 'hex')
FROM source_bins
RETURNING revision_id, recorded_at, target_version, db_target_bins_sha256;
COMMIT;
"""


def declared_row(target_version: str) -> dict:
    if not IDENTIFIER.fullmatch(target_version):
        raise ValueError("invalid target version")
    sql = f"""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT jsonb_build_object(
    'revision_id', revision_id, 'recorded_at', recorded_at,
    'target_version', target_version, 'effective_from', effective_from,
    'effective_to', effective_to, 'source_profile_state_sha256', source_profile_state_sha256,
    'source_assignment_state_sha256', source_assignment_state_sha256,
    'profile_revision_ids', profile_revision_ids, 'assignment_revision_ids', assignment_revision_ids,
    'target_rule', target_rule, 'target_bins', target_bins,
    'db_target_bins_sha256', db_target_bins_sha256)::text
FROM public.fixed_panel_target_revisions WHERE target_version = '{target_version}';
ROLLBACK;"""
    return _db_json(sql)


def finalize(draft: dict, row: dict) -> dict:
    if draft.get("schema") != DRAFT_SCHEMA or row.get("target_version") != draft.get("target_version"):
        raise ValueError("target declaration identity mismatch")
    for key in (
        "effective_from",
        "effective_to",
        "source_profile_state_sha256",
        "source_assignment_state_sha256",
        "profile_revision_ids",
        "assignment_revision_ids",
        "target_rule",
        "target_bins",
    ):
        if row.get(key) != draft[key]:
            raise ValueError(f"declared {key} differs from source draft")
    if not isinstance(row.get("revision_id"), int) or row["revision_id"] < 1:
        raise ValueError("missing positive database target revision")
    if not SHA.fullmatch(row.get("db_target_bins_sha256", "")):
        raise ValueError("missing database target-bin hash")
    artifact = {
        "schema": winter.TARGET_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": draft["start_local_date"],
        "recorded_at": row["recorded_at"],
        "effective_from": draft["effective_from"],
        "effective_to": draft["effective_to"],
        "target_version": draft["target_version"],
        "fixed_panel_target_revision_id": row["revision_id"],
        "source_profile_state_sha256": draft["source_profile_state_sha256"],
        "profile_revision_ids": draft["profile_revision_ids"],
        "crop_assignment_revision_sha256": draft["source_assignment_state_sha256"],
        "target_bins_sha256": draft["target_bins_sha256"],
        "target_bins": draft["target_bins"],
    }
    winter.validate_target_source(
        _canonical(artifact), start=date.fromisoformat(draft["start_local_date"]), registered_at=datetime.now(UTC)
    )
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="read-only same-snapshot DB source export")
    capture.add_argument("--out", type=Path, required=True)
    prepare = sub.add_parser("build", help="offline target bins and reviewable declaration SQL")
    prepare.add_argument("--snapshot", type=Path, required=True)
    prepare.add_argument("--start", type=date.fromisoformat, required=True)
    prepare.add_argument("--target-version", required=True)
    prepare.add_argument("--draft-out", type=Path, required=True)
    prepare.add_argument("--sql-out", type=Path, required=True)
    finish = sub.add_parser("finalize", help="read-only DB declaration verification")
    finish.add_argument("--draft", type=Path, required=True)
    finish.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        snapshot = _db_json(SNAPSHOT_SQL)
        _validate_snapshot(snapshot)
        _write(args.out, _canonical(snapshot))
        print(f"snapshot_sha256={_sha(args.out.read_bytes())}")
    elif args.command == "build":
        raw = args.snapshot.read_bytes()
        snapshot = json.loads(raw)
        if _canonical(snapshot) != raw:
            raise ValueError("snapshot must be canonical JSON")
        draft = build(snapshot, start=args.start, target_version=args.target_version, source_sha256=_sha(raw))
        _write(args.draft_out, _canonical(draft))
        _write(args.sql_out, declaration_sql(draft).encode())
        print(f"draft_sha256={_sha(args.draft_out.read_bytes())} bins={len(draft['target_bins'])}")
    else:
        raw = args.draft.read_bytes()
        draft = json.loads(raw)
        if _canonical(draft) != raw:
            raise ValueError("draft must be canonical JSON")
        artifact = finalize(draft, declared_row(draft["target_version"]))
        _write(args.out, _canonical(artifact))
        print(
            f"target_source_sha256={_sha(args.out.read_bytes())} revision={artifact['fixed_panel_target_revision_id']}"
        )


if __name__ == "__main__":
    main()
