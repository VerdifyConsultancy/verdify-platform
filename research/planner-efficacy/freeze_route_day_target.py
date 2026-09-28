#!/usr/bin/env python3
"""Prepare one future Denver 06:00–24:00 native-route target declaration.

The production query is read-only. Output is a private source snapshot and
reviewable SQL; this command never inserts a declaration or enables capture.
It is independent of the separate 60-day winter feasibility packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Denver")
REPO = Path(__file__).resolve().parents[2]
DB_SCRIPT = REPO / "scripts/verdify-db.sh"
ZONES = ("north", "east", "west")
SNAPSHOT_SQL = """
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH profiles AS (
    SELECT p.* FROM public.crop_target_profiles p WHERE p.greenhouse_id='vallery'
), assignments AS (
    SELECT c.* FROM public.crops c WHERE c.greenhouse_id='vallery'
), profile_ids AS (
    SELECT p.id,r.revision_id FROM profiles p JOIN LATERAL (
        SELECT revision_id,operation,profile FROM public.crop_target_profile_revisions
         WHERE profile_id=p.id ORDER BY revision_id DESC LIMIT 1
    ) r ON r.operation<>'delete' AND r.profile=to_jsonb(p)
), assignment_ids AS (
    SELECT c.id,r.revision_id FROM assignments c JOIN LATERAL (
        SELECT revision_id,operation,crop FROM public.crop_assignment_revisions
         WHERE crop_id=c.id ORDER BY revision_id DESC LIMIT 1
    ) r ON r.operation<>'delete' AND r.crop=to_jsonb(c)
)
SELECT jsonb_build_object(
    'schema','verdify-route-day-target-source-snapshot-v1',
    'captured_at',clock_timestamp(), 'database_snapshot',pg_current_snapshot()::text,
    'greenhouse_id','vallery',
    'profiles',(SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id),'[]'::jsonb) FROM profiles p),
    'assignments',(SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY c.id),'[]'::jsonb) FROM assignments c),
    'source_profile_state_sha256',(SELECT encode(pg_catalog.sha256(convert_to(
        coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id),'[]'::jsonb)::text,'UTF8')),'hex') FROM profiles p),
    'source_assignment_state_sha256',(SELECT encode(pg_catalog.sha256(convert_to(
        coalesce(jsonb_agg(to_jsonb(c) ORDER BY c.id),'[]'::jsonb)::text,'UTF8')),'hex') FROM assignments c),
    'profile_revision_ids',(SELECT coalesce(jsonb_agg(revision_id ORDER BY revision_id),'[]'::jsonb) FROM profile_ids),
    'assignment_revision_ids',(SELECT coalesce(jsonb_agg(revision_id ORDER BY revision_id),'[]'::jsonb) FROM assignment_ids)
)::text;
ROLLBACK;
"""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(6), ZONE).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), ZONE).astimezone(UTC)
    if end - start != timedelta(hours=18):
        raise ValueError("local day is not an 18-hour target window")
    return start, end


def _number(row: dict, key: str) -> float:
    value = row.get(key)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"missing or nonfinite {key}")
    return float(value)


def zone_band(profiles: list[dict], assignments: list[dict], zone: str, season: str, hour: int) -> dict:
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
            raise ValueError(f"missing or ambiguous default for {zone}/{season}/{hour}")
    result = {
        "temp_low": max(_number(p, "temp_ideal_min") for p in joined),
        "temp_high": min(_number(p, "temp_ideal_max") for p in joined),
        "vpd_low": max(_number(p, "vpd_ideal_min") for p in joined),
        "vpd_high": min(_number(p, "vpd_ideal_max") for p in joined),
    }
    if result["temp_low"] > result["temp_high"] or result["vpd_low"] > result["vpd_high"]:
        raise ValueError("inverted crop ideal intersection")
    return result


def build(snapshot: dict, day: date, now: datetime | None = None) -> dict:
    start, end = bounds(day)
    now = now or datetime.now(UTC)
    if snapshot.get("schema") != "verdify-route-day-target-source-snapshot-v1":
        raise ValueError("wrong target source snapshot")
    if snapshot.get("greenhouse_id") != "vallery":
        raise ValueError("wrong greenhouse")
    captured = datetime.fromisoformat(snapshot["captured_at"])
    if captured.tzinfo is None or captured >= start or now >= start:
        raise ValueError("target source must be captured before the future window")
    for key, revisions in (("profiles", "profile_revision_ids"), ("assignments", "assignment_revision_ids")):
        rows = snapshot[key]
        ids = [r.get("id") for r in rows]
        revs = snapshot[revisions]
        if not rows or ids != sorted(set(ids)) or len(revs) != len(rows) or revs != sorted(set(revs)):
            raise ValueError(f"incomplete or unordered {key} source lineage")
    season = (
        "fall"
        if day.month in (9, 10, 11)
        else ("winter" if day.month in (12, 1, 2) else "spring" if day.month in (3, 4, 5) else "summer")
    )
    if not any(p.get("season") == season and p.get("crop_catalog_id") is not None for p in snapshot["profiles"]):
        season = "spring"  # the fn_zone_band global fallback
    bins = []
    for quarter in range(72):
        bucket = start + timedelta(minutes=quarter * 15)
        hour = bucket.astimezone(ZONE).hour
        bands = [zone_band(snapshot["profiles"], snapshot["assignments"], z, season, hour) for z in ZONES]
        bins.append(
            {
                "bucket_start": bucket.isoformat(),
                **{
                    key: math.fsum(sorted(b[key] / 3 for b in bands))
                    for key in ("temp_low", "temp_high", "vpd_low", "vpd_high")
                },
            }
        )
    return {
        "schema": "verdify-route-day-target-draft-v1",
        "local_day": day.isoformat(),
        "greenhouse_id": "vallery",
        "target_version": f"native-route-{day.isoformat()}",
        "target_rule": "fixed_panel_equal_zone_ideal_mean_v1",
        "source_captured_at": snapshot["captured_at"],
        "source_snapshot_sha256": sha(canonical(snapshot)),
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat(),
        "source_profile_state_sha256": snapshot["source_profile_state_sha256"],
        "source_assignment_state_sha256": snapshot["source_assignment_state_sha256"],
        "profile_revision_ids": snapshot["profile_revision_ids"],
        "assignment_revision_ids": snapshot["assignment_revision_ids"],
        "target_bins_sha256": sha(canonical(bins)),
        "target_bins": bins,
    }


def declaration_sql(draft: dict) -> str:
    if draft.get("schema") != "verdify-route-day-target-draft-v1" or len(draft["target_bins"]) != 72:
        raise ValueError("one-day 72-bin target required")
    bins = canonical(draft["target_bins"]).decode()
    if "$route_bins$" in bins:
        raise ValueError("invalid target JSON delimiter")
    arrays = [
        "ARRAY[" + ",".join(str(int(i)) for i in draft[key]) + "]::bigint[]"
        for key in ("profile_revision_ids", "assignment_revision_ids")
    ]
    return f"""-- Review source snapshot and run only before {draft["effective_from"]}.
-- This inserts one analysis target; it does not change control setpoints.
BEGIN;
LOCK TABLE public.fixed_panel_target_revisions IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$ BEGIN
  IF EXISTS (SELECT 1 FROM public.fixed_panel_target_revisions
              WHERE greenhouse_id='vallery'
                AND effective_from < '{draft["effective_to"]}'::timestamptz
                AND effective_to > '{draft["effective_from"]}'::timestamptz) THEN
    RAISE EXCEPTION 'route day target interval already declared';
  END IF;
END $preflight$;
WITH source_bins AS (SELECT $route_bins${bins}$route_bins$::jsonb AS bins)
INSERT INTO public.fixed_panel_target_revisions
  (greenhouse_id,target_version,effective_from,effective_to,
   source_profile_state_sha256,source_assignment_state_sha256,
   profile_revision_ids,assignment_revision_ids,target_rule,target_bins,db_target_bins_sha256)
SELECT 'vallery','{draft["target_version"]}','{draft["effective_from"]}',
       '{draft["effective_to"]}','{draft["source_profile_state_sha256"]}',
       '{draft["source_assignment_state_sha256"]}',{arrays[0]},{arrays[1]},
       '{draft["target_rule"]}',bins,
       encode(pg_catalog.sha256(convert_to(bins::text,'UTF8')),'hex')
  FROM source_bins
RETURNING revision_id,recorded_at,target_version,db_target_bins_sha256;
COMMIT;
"""


def write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    start, _ = bounds(args.day)
    if datetime.now(UTC) >= start:
        raise SystemExit("prospective window already began")
    result = subprocess.run(
        [str(DB_SCRIPT), "prod", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", SNAPSHOT_SQL],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise SystemExit("source capture did not return exactly one snapshot")
    snapshot = json.loads(lines[0])
    draft = build(snapshot, args.day)
    write_new(args.out / "target-source-snapshot.json", canonical(snapshot) + b"\n")
    write_new(args.out / "target-draft.json", canonical(draft) + b"\n")
    write_new(args.out / "target-declaration.sql", declaration_sql(draft).encode())
    print(
        json.dumps(
            {
                "day": draft["local_day"],
                "bins": len(draft["target_bins"]),
                "snapshot_sha256": draft["source_snapshot_sha256"],
                "target_bins_sha256": draft["target_bins_sha256"],
                "window_start": draft["effective_from"],
                "window_end": draft["effective_to"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
