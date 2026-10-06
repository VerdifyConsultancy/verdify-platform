#!/usr/bin/env python3
"""Emit guarded, exact-ID #49 maintenance SQL; never connect or read credentials."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

TAG = "s2_49_historical_suppressed"
FIELDS = {"id", "created_at", "updated_at", "resolved_by", "resolution_sha256"}


def validate(snapshot: dict) -> list[dict]:
    cutoff = dt.datetime.fromisoformat(snapshot["historical_before"])
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must have timezone")
    rows = snapshot["rows"]
    if not rows or len(rows) > 1000:
        raise ValueError("reviewed scope must contain 1–1000 rows")
    ids = set()
    for row in rows:
        if set(row) != FIELDS or type(row["id"]) is not int or row["id"] <= 0:
            raise ValueError("invalid reviewed row")
        if row["id"] in ids:
            raise ValueError("duplicate reviewed ID")
        ids.add(row["id"])
        for field in ["created_at", "updated_at"]:
            stamp = dt.datetime.fromisoformat(row[field])
            if stamp.tzinfo is None or stamp >= cutoff:
                raise ValueError("row is outside historical cutoff")
        if row["resolved_by"] is not None:
            raise ValueError("existing resolver needs separate review")
        if not re.fullmatch(r"[0-9a-f]{64}", row["resolution_sha256"]):
            raise ValueError("resolution must be represented by SHA256 only")
    return sorted(rows, key=lambda row: row["id"])


def render(snapshot: dict, *, dry_run: bool = False) -> str:
    rows = validate(snapshot)
    data = json.dumps(rows, ensure_ascii=True).replace("'", "''")
    count = len(rows)
    return f"""-- #49 exact reviewed snapshot SHA256: {hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()}
\\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='3s';
SELECT pg_advisory_xact_lock(490061);
CREATE TEMP TABLE reviewed_49 ON COMMIT DROP AS
SELECT * FROM jsonb_to_recordset('{data}'::jsonb) AS x(
 id integer, created_at timestamptz, updated_at timestamptz,
 resolved_by text, resolution_sha256 text);
-- Serialize only the reviewed rows; the normal lifecycle trigger stays enabled.
SELECT a.id FROM public.alert_log a JOIN reviewed_49 r USING(id) ORDER BY a.id FOR UPDATE OF a;
CREATE TEMP TABLE before_49 ON COMMIT DROP AS
SELECT a.id,a.created_at,a.updated_at,a.resolved_at,a.resolved_by,
 encode(sha256(convert_to(coalesce(a.resolution,''),'UTF8')),'hex') AS resolution_sha256
FROM public.alert_log a JOIN reviewed_49 r USING(id);
CREATE TEMP TABLE canonical_before_49 ON COMMIT DROP AS SELECT id FROM public.v_open_alerts;
DO $guard$
BEGIN
 IF (SELECT count(*) FROM before_49) <> {count} THEN
  RAISE EXCEPTION 'reviewed IDs missing; no maintenance admitted';
 END IF;
 IF EXISTS (
  SELECT 1 FROM public.alert_log a JOIN reviewed_49 r USING(id)
  WHERE a.alert_type <> 'sensor_offline' OR a.disposition <> 'suppressed'
   OR a.created_at IS DISTINCT FROM r.created_at
   OR encode(sha256(convert_to(coalesce(a.resolution,''),'UTF8')),'hex') <> r.resolution_sha256
   OR NOT ((a.resolved_at IS NULL AND a.resolved_by IS NULL AND a.updated_at=r.updated_at)
       OR (a.resolved_at=r.updated_at AND a.resolved_by='{TAG}'))
 ) THEN RAISE EXCEPTION 'reviewed state changed; no maintenance admitted'; END IF;
END $guard$;
CREATE TEMP TABLE changed_49 ON COMMIT DROP AS
WITH changed AS (
 UPDATE public.alert_log a SET resolved_at=r.updated_at, resolved_by='{TAG}'
 FROM reviewed_49 r WHERE a.id=r.id AND a.resolved_at IS NULL
 RETURNING a.id
) SELECT * FROM changed;
DO $verify$
BEGIN
 IF EXISTS ((SELECT id FROM canonical_before_49 EXCEPT SELECT id FROM public.v_open_alerts)
  UNION ALL (SELECT id FROM public.v_open_alerts EXCEPT SELECT id FROM canonical_before_49))
 THEN RAISE EXCEPTION 'canonical alert membership changed'; END IF;
 IF EXISTS (SELECT 1 FROM public.alert_log a JOIN reviewed_49 r USING(id)
  WHERE a.resolved_at IS DISTINCT FROM r.updated_at OR a.resolved_by IS DISTINCT FROM '{TAG}'
   OR encode(sha256(convert_to(coalesce(a.resolution,''),'UTF8')),'hex') <> r.resolution_sha256)
 THEN RAISE EXCEPTION 'maintenance postcondition failed'; END IF;
END $verify$;
SELECT json_build_object('kind','receipt','dry_run',{str(dry_run).lower()},
 'reviewed_ids',(SELECT json_agg(id ORDER BY id) FROM reviewed_49),
 'changed_ids',(SELECT coalesce(json_agg(id ORDER BY id),'[]'::json) FROM changed_49),
 'changed_count',(SELECT count(*) FROM changed_49),
 'canonical_count',(SELECT count(*) FROM public.v_open_alerts),
 'canonical_membership_unchanged',true);
SELECT json_build_object('kind','before','data',to_jsonb(b)) FROM before_49 b ORDER BY id;
SELECT json_build_object('kind','after','data',json_build_object(
 'id',a.id,'created_at',a.created_at,'updated_at',a.updated_at,
 'resolved_at',a.resolved_at,'resolved_by',a.resolved_by,
 'resolution_sha256',encode(sha256(convert_to(coalesce(a.resolution,''),'UTF8')),'hex')))
 FROM public.alert_log a JOIN reviewed_49 r USING(id) ORDER BY a.id;
{"ROLLBACK" if dry_run else "COMMIT"};
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(render(json.loads(args.snapshot.read_text()), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
