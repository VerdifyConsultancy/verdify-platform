#!/usr/bin/env python3
"""Read-only blinded daily reconciliation; never invokes lifecycle or reveal.

Uses the existing operator read-only DB runner, not the analyst-only role (its
current view omits unfrozen assignments). No new grants or live configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

SCHEMA = "verdify-experiment-v2-daily-reconciliation-v1"
DOMAIN = b"verdify-experiment-v2-daily-reconciliation-v1\0"
SQL_EXPORT_DOMAIN = b"verdify-experiment-v2-frozen-export-v1\0"
EVIDENCE_DOMAIN = b"verdify-experiment-v2-evidence-bundle-v1\0"
DAY_DOMAIN = b"verdify-experiment-v2-day-evidence-v1\0"
TIMEZONE = ZoneInfo("America/Denver")
PARTS = ("deviation", "fidelity", "environment", "integrity")


def query(experiment_id: str) -> str:
    experiment_id = str(UUID(experiment_id))
    return f"""SELECT json_build_object(
      'experiment_id',e.experiment_id,'as_of',statement_timestamp(),
      'start',e.study_start_local_date,'pairs',e.randomized_pair_count,
      'design_lock_sha256',e.design_lock_sha256,
      'analyzer_environment_sha256',e.analyzer_environment_sha256,
      'rows',COALESCE((SELECT json_agg(r ORDER BY r.local_date,r.assignment_id) FROM (
        SELECT a.assignment_id, a.pair_index,
          (lower(a.valid_range) AT TIME ZONE 'America/Denver')::date AS local_date,
          o.assigned_local_date AS outcome_local_date,o.blinded_arm,
          lower(o.itt_range) AS itt_start,upper(o.itt_range) AS itt_end,
          f.delivery_failed,f.fallback_used,f.facility_rescue,
          f.zero_value_retained,f.null_value_retained,f.exposure_seconds,
          f.expected_seconds,f.outcome_sha256,f.frozen_at AS outcome_frozen_at,
          CASE WHEN f.assignment_id IS NOT NULL THEN jsonb_build_object(
            'delivery_failed',f.delivery_failed,'facility_rescue',f.facility_rescue,
            'fallback_used',f.fallback_used,'null_value_retained',f.null_value_retained,
            'outcome',f.outcome_payload,'zero_value_retained',f.zero_value_retained)::text END AS outcome_preimage,
          d.deviation_payload::text AS deviation_preimage,d.deviation_sha256,
          d.fidelity_payload::text AS fidelity_preimage,d.fidelity_sha256,
          d.environment_payload::text AS environment_preimage,d.environment_sha256,
          d.integrity_payload::text AS integrity_preimage,d.integrity_sha256,
          d.evidence_bundle_sha256,d.integrity_passed,d.frozen_at AS evidence_frozen_at
        FROM public.control_assignments a
        LEFT JOIN public.experiment_v2_outcomes o ON o.assignment_id=a.assignment_id AND o.experiment_id=a.experiment_id
        LEFT JOIN public.experiment_v2_outcome_freezes f ON f.assignment_id=a.assignment_id
        LEFT JOIN public.experiment_v2_day_evidence d ON d.assignment_id=a.assignment_id AND d.experiment_id=a.experiment_id
        WHERE a.experiment_id=e.experiment_id) r),'[]'::json),
      'export',(SELECT json_build_object('raw',x.export_payload::text,
          'sha256',x.export_sha256,'evidence_bundle_sha256',x.evidence_bundle_sha256,
          'analyzer_environment_sha256',x.analyzer_environment_sha256)
        FROM public.v_experiment_v2_frozen_analyzer_input x WHERE x.experiment_id=e.experiment_id)
      ) AS snapshot FROM public.control_experiments e
      WHERE e.experiment_id='{experiment_id}'::uuid"""


def digest(domain: bytes, raw: str, identity: str | None = None) -> str:
    return hashlib.sha256(domain + (UUID(identity).bytes if identity else b"") + raw.encode()).hexdigest()


def reconcile(snapshot: dict) -> dict:
    """Keep all assignment rows, independent of exposure and endpoint availability."""
    as_of = datetime.fromisoformat(snapshot["as_of"])
    if as_of.tzinfo is None:
        raise ValueError("snapshot clock must be timezone-aware")
    rows = sorted(snapshot["rows"], key=lambda r: (r["local_date"], r["assignment_id"]))
    ids = set()
    days = set()
    output = []
    problems = []
    locked = snapshot["design_lock_sha256"] is not None
    start = date.fromisoformat(snapshot["start"]) if locked else None
    pairs = snapshot["pairs"] if locked else None
    if locked and (type(pairs) is not int or not 0 < pairs <= 150):
        raise ValueError("locked pair denominator missing")
    for row in rows:
        identity = str(UUID(row["assignment_id"]))
        day = date.fromisoformat(row["local_date"])
        if identity in ids or day in days:
            raise ValueError("duplicate assignment or assigned local day")
        ids.add(identity)
        days.add(day)
        if not locked or not start <= day < start + timedelta(days=2 * pairs):
            raise ValueError("assignment outside locked calendar")
        if row["pair_index"] != (day - start).days // 2:
            raise ValueError("assignment pair differs from locked calendar")
        begin = datetime.combine(day, datetime.min.time(), TIMEZONE) + timedelta(hours=6)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), TIMEZONE)
        if (end.astimezone(UTC) - begin.astimezone(UTC)).total_seconds() != 64800:
            raise ValueError("outcome is not an 18-hour window")
        has_outcome = row["outcome_local_date"] is not None
        frozen = row["outcome_sha256"] is not None
        if has_outcome and (
            row["outcome_local_date"] != day.isoformat()
            or row["blinded_arm"] not in ("X", "Y")
            or datetime.fromisoformat(row["itt_start"]) != begin
            or datetime.fromisoformat(row["itt_end"]) != end
        ):
            raise ValueError("outcome assignment or fixed window changed")
        hashes = {}
        if frozen:
            if (
                not has_outcome
                or datetime.fromisoformat(row["outcome_frozen_at"]) < end
                or datetime.fromisoformat(row["outcome_frozen_at"]) > as_of
            ):
                raise ValueError("freeze outside completed snapshot window")
            if (
                digest(b"verdify-experiment-v2-assigned-day-outcome-v1\0", row["outcome_preimage"], identity)
                != row["outcome_sha256"]
            ):
                raise ValueError("outcome hash mismatch")
            if row["expected_seconds"] != 64800 or not 0 <= row["exposure_seconds"] <= 64800:
                raise ValueError("fidelity window mismatch")
            preimage = json.loads(row["outcome_preimage"])
            if any(
                type(row[p]) is not bool or preimage.get(p) != row[p]
                for p in (
                    "delivery_failed",
                    "fallback_used",
                    "facility_rescue",
                    "zero_value_retained",
                    "null_value_retained",
                )
            ):
                raise ValueError("outcome flag lineage mismatch")
            hashes["outcome_sha256"] = row["outcome_sha256"]
        evidence = row["evidence_bundle_sha256"] is not None
        if evidence:
            if (
                not frozen
                or row["integrity_passed"] is not True
                or not end <= datetime.fromisoformat(row["evidence_frozen_at"]) <= as_of
            ):
                raise ValueError("evidence without terminal verified outcome")
            for part in PARTS:
                domain = f"verdify-experiment-v2-{part}-v1".encode() + b"\0"
                if digest(domain, row[part + "_preimage"], identity) != row[part + "_sha256"]:
                    raise ValueError(part + " hash mismatch")
                hashes[part + "_sha256"] = row[part + "_sha256"]
            expected = hashlib.sha256(
                DAY_DOMAIN
                + UUID(identity).bytes
                + b"".join(bytes.fromhex(hashes[p + "_sha256"]) for p in ("outcome", *PARTS))
            ).hexdigest()
            if expected != row["evidence_bundle_sha256"]:
                raise ValueError("day evidence lineage mismatch")
            integrity = json.loads(row["integrity_preimage"])
            if (
                any(
                    integrity.get(p + "_sha256") != hashes[p + "_sha256"]
                    for p in ("outcome", "deviation", "fidelity", "environment")
                )
                or integrity.get("assignment_id") != identity
            ):
                raise ValueError("integrity component lineage mismatch")
            hashes["evidence_bundle_sha256"] = expected
        state = (
            "scheduled"
            if as_of < end
            else (
                "frozen_verified"
                if evidence
                else "missing_evidence"
                if frozen
                else "missing_freeze"
                if has_outcome
                else "missing_outcome"
            )
        )
        if state.startswith("missing"):
            problems.append(identity)
        output.append(
            {
                "assignment_id": identity,
                "local_date": day.isoformat(),
                "pair_index": row["pair_index"],
                "blinded_label": row["blinded_arm"],
                "state": state,
                "hashes": hashes,
                **{
                    p: row[p]
                    for p in (
                        "delivery_failed",
                        "fallback_used",
                        "facility_rescue",
                        "zero_value_retained",
                        "null_value_retained",
                        "exposure_seconds",
                    )
                },
            }
        )
    missing_days = (
        [(start + timedelta(days=i)).isoformat() for i in range(2 * pairs) if start + timedelta(days=i) not in days]
        if locked
        else []
    )
    export = snapshot["export"]
    export_verified = False
    if export:
        raw = export["raw"]
        payload = json.loads(raw)
        if (
            set(payload) != {"experiment_id", "rows", "evidence_bundle_sha256", "analyzer_environment_sha256"}
            or payload["experiment_id"] != snapshot["experiment_id"]
            or digest(SQL_EXPORT_DOMAIN, raw) != export["sha256"]
        ):
            raise ValueError("export bytes/hash/identity mismatch")
        if (
            payload["analyzer_environment_sha256"] != export["analyzer_environment_sha256"]
            or export["analyzer_environment_sha256"] != snapshot["analyzer_environment_sha256"]
        ):
            raise ValueError("export analyzer lineage mismatch")
        exported = payload["rows"]
        if missing_days or len(exported) != len(rows) or {r["assignment_id"] for r in exported} != ids:
            raise ValueError("export excludes or adds an assignment")
        by_id = {r["assignment_id"]: r for r in rows}
        for index, r in enumerate(exported, 1):
            expected_fields = {
                "assigned_local_date",
                "assignment_id",
                "blinded_arm",
                "day_index",
                "delivery_failed",
                "deviation_sha256",
                "environment_sha256",
                "evidence_bundle_sha256",
                "facility_rescue",
                "fallback_used",
                "fidelity_sha256",
                "integrity_sha256",
                "itt_range",
                "null_value_retained",
                "outcome",
                "outcome_sha256",
                "pair_index",
                "zero_value_retained",
            }
            if set(r) != expected_fields:
                raise ValueError("export row shape mismatch")
            source = by_id[r["assignment_id"]]
            if (
                r["day_index"] != index
                or r["assigned_local_date"] != source["local_date"]
                or r["pair_index"] != source["pair_index"]
                or r["blinded_arm"] != source["blinded_arm"]
            ):
                raise ValueError("export assignment lineage mismatch")
            for field in (
                "outcome_sha256",
                "evidence_bundle_sha256",
                *(p + "_sha256" for p in PARTS),
                "delivery_failed",
                "fallback_used",
                "facility_rescue",
                "zero_value_retained",
                "null_value_retained",
            ):
                if r[field] != source[field] or source["evidence_bundle_sha256"] is None:
                    raise ValueError("export frozen row lineage mismatch")
            import re

            bounds = re.fullmatch(r'\["([^"]+)","([^"]+)"\)', r["itt_range"])
            if (
                not bounds
                or datetime.fromisoformat(bounds[1]) != datetime.fromisoformat(source["itt_start"])
                or datetime.fromisoformat(bounds[2]) != datetime.fromisoformat(source["itt_end"])
            ):
                raise ValueError("export changed fixed window")
            if r["outcome"] != json.loads(source["outcome_preimage"])["outcome"]:
                raise ValueError("export changed outcome values")
        bundle = digest(EVIDENCE_DOMAIN, "".join(r["evidence_bundle_sha256"] for r in exported))
        if bundle != payload["evidence_bundle_sha256"] or bundle != export["evidence_bundle_sha256"]:
            raise ValueError("export evidence lineage mismatch")
        export_verified = True
    return {
        "schema": SCHEMA,
        "experiment_id": snapshot["experiment_id"],
        "as_of": as_of.isoformat(),
        "design_lock_sha256": snapshot["design_lock_sha256"],
        "timezone": "America/Denver",
        "window_local": "[06:00,24:00)",
        "expected_seconds": 64800,
        "locked_assigned_day_denominator": 2 * pairs if locked else None,
        "observed_assignments": len(rows),
        "missing_calendar_days": missing_days,
        "completed_days_missing_freeze_or_evidence": problems,
        "rows": output,
        "export_verified": export_verified,
        "export_sha256": export["sha256"] if export else None,
        "status": "not_design_locked" if not locked else "incomplete" if problems or missing_days else "reconciled",
        "claim_scope": "blinded integrity only; no efficacy, physical qualification or launch claim",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "reconcile_db_runner", Path(__file__).with_name("experiment-aa-gates.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    records = module.run_sql_json(query(str(args.experiment_id)), timeout=30)
    if len(records) != 1:
        raise ValueError("experiment not found")
    payload = reconcile(records[0]["snapshot"])
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    receipt = {"receipt": payload, "sha256": hashlib.sha256(DOMAIN + raw).hexdigest()}
    with args.output.open("x") as f:
        os.chmod(args.output, 0o600)
        json.dump(receipt, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    directory = os.open(args.output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 1 if payload["status"] == "incomplete" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"reconciliation refused: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
