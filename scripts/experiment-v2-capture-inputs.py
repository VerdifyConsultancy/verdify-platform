#!/usr/bin/env python3
"""Preserve actual as-of/prior completed 18h observational inputs, read-only.

No provider, selector cycle, freeze, lifecycle, draw or authority function is called.
The snapshot is not a warm assignment/outcome or a qualification for launch/power.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "research/planner-efficacy/observational-input-capture.sql"
MAX_BYTES = 32 * 1024 * 1024
DOMAIN = b"verdify-experiment-v2-observational-input-capture-v1\0"
CLAIM = (
    "Observational input availability only; not assigned outcomes, physical identity or chain continuity, "
    "selector admission/mix or power"
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def unique_object(pairs: list) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timestamp lacks offset")
    return result


def query(experiment_id: str) -> str:
    return SQL_PATH.read_text().format(experiment_id=str(UUID(experiment_id)))


def summarize(snapshot: dict, experiment_id: str) -> dict:
    if snapshot["experiment_id"] != str(UUID(experiment_id)) or snapshot["claim_scope"] != CLAIM:
        raise ValueError("snapshot study/scope differs")
    as_of = stamp(snapshot["as_of"])
    local = as_of.astimezone(ZoneInfo("America/Denver"))
    end = datetime.combine(local.date(), time(), local.tzinfo)
    start = datetime.combine(local.date() - timedelta(days=1), time(6), local.tzinfo)
    boundary = datetime.combine(local.date() + timedelta(days=1), time(6), local.tzinfo)
    if (
        stamp(snapshot["prior_completed_window_start"]) != start
        or stamp(snapshot["prior_completed_window_end"]) != end
        or stamp(snapshot["context_cutoff_at"]) != as_of
        or stamp(snapshot["context_boundary_at"]) != boundary
        or (end - start).total_seconds() != 64800
    ):
        raise ValueError("actual cutoff or completed 18h window differs")
    # This observational next-day capture is never reclassified as the prospective pre06 receipt.
    if snapshot["cutoff_kind"] != "actual_current_clock_not_preregistered_pre06":
        raise ValueError("cannot substitute an observational capture for pre06 qualification")
    context = snapshot["context"]
    raw = bytes.fromhex(context["context_canonical_hex"])
    if hashlib.sha256(raw).hexdigest() != context["context_sha256"]:
        raise ValueError("context bytes hash differs")
    if (
        raw != context["context_payload_pg_text"].encode()
        or json.loads(raw, object_pairs_hook=unique_object) != context["context_payload"]
    ):
        raise ValueError("context canonical payload differs")
    payload = context["context_payload"]
    if stamp(payload["context_cutoff_at"]) != as_of or stamp(payload["boundary_at"]) != boundary:
        raise ValueError("context embedded cutoff differs")
    sources = payload.get("climate_observations", []) + payload.get("forecast_vintage", [])
    pg_texts = context["source_row_pg_texts"]
    if len(sources) != len(pg_texts):
        raise ValueError("context source canonical denominator differs")
    for row, pg_text in zip(sources, pg_texts, strict=True):
        if json.loads(pg_text, object_pairs_hook=unique_object) != {
            key: value for key, value in row.items() if key != "source_row_sha256"
        }:
            raise ValueError("source payload differs from original canonical text")
        digest = hashlib.sha256(b"verdify-experiment-v2-selector-source-v1\0" + pg_text.encode()).hexdigest()
        if digest != row["source_row_sha256"]:
            raise ValueError("context source hash differs")
        when = row.get("fetched_at", row.get("observed_at"))
        if when is None or stamp(when) > as_of:
            raise ValueError("source beyond actual cutoff")
    if context["context_status"] == "frozen":
        bundle = b"verdify-experiment-v2-selector-source-bundle-v1\0" + "".join(
            row["source_row_sha256"] for row in sources
        ).encode("ascii")
    elif context["context_status"] == "unavailable":
        bundle = b"verdify-experiment-v2-selector-source-unavailable-v1\0" + raw
    else:
        raise ValueError("unknown context status")
    if hashlib.sha256(bundle).hexdigest() != context["source_bundle_sha256"]:
        raise ValueError("context source bundle differs")
    if context["source_max_at"] is not None and stamp(context["source_max_at"]) > as_of:
        raise ValueError("context source maximum beyond cutoff")
    minutes = set()
    for row in snapshot["climate_source_rows"]:
        ts = stamp(row["ts"])
        if not start <= ts < end or row["greenhouse_id"] != "vallery":
            raise ValueError("climate outside source window")
        values = [row.get(f"{field}_{zone}") for zone in ("north", "east", "west") for field in ("temp", "vpd")]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            minutes.add(int((ts - start).total_seconds() // 60))
    bins = Counter(minute // 15 for minute in minutes)
    longest = run = 0
    for minute in range(1080):
        run = 0 if minute in minutes else run + 1
        longest = max(longest, run)
    receipts = snapshot["equipment_receipts"]
    for row in receipts:
        value = row["events_canonical"]
        if not value.startswith("\\x") or hashlib.sha256(bytes.fromhex(value[2:])).hexdigest() != row["events_sha256"]:
            raise ValueError("original equipment event bytes hash differs")
    keys = (
        "climate_source_rows",
        "fixed_targets",
        "contributors",
        "profile_revisions",
        "equipment_receipts",
        "direct_snapshots",
        "counter_samples",
        "native_source_groups",
        "native_frozen_day_receipts",
    )
    return {
        "as_of": snapshot["as_of"],
        "cutoff_kind": snapshot["cutoff_kind"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "context_status": context["context_status"],
        "failure_reason": context["failure_reason"],
        "context_sha256": context["context_sha256"],
        "source_bundle_sha256": context["source_bundle_sha256"],
        "counts": {key: len(snapshot[key]) for key in keys},
        "fixed_panel_climate": {
            "distinct_complete_minutes": len(minutes),
            "eligible_bins_min12minutes": sum(n >= 12 for n in bins.values()),
            "expected_bins": 72,
            "longest_missing_complete_minute_run": longest,
        },
        "equipment": {
            "event_byte_hashes_verified": len(receipts),
            "gap_before_rows": sum(bool(row["gap_before"]) for row in receipts),
            "gap_reasons": dict(Counter(row["gap_reason"] for row in receipts if row["gap_before"])),
        },
        "scientific_inputs": {
            "qualified_target_contributor_coverage": None,
            "physical_source_continuity": None,
            "selector_admission_and_mix": None,
            "paired_18h_effects_covariance_carryover": None,
            "joint_power": None,
        },
        "claim_scope": CLAIM,
    }


def save(path: Path, raw: bytes) -> str:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def publish(directory: Path, snapshot: dict, experiment_id: str) -> dict:
    raw = canonical(snapshot) + b"\n"
    if len(raw) > MAX_BYTES:
        raise ValueError("snapshot exceeds bound; do not truncate source rows")
    summary = summarize(snapshot, experiment_id)
    directory.mkdir(mode=0o700)  # Exclusive new custody directory; never overwrite a capture.
    files = {
        "snapshot.json": save(directory / "snapshot.json", raw),
        "query.sql": save(directory / "query.sql", (query(experiment_id) + "\n").encode()),
    }
    receipt = {
        "schema": "verdify-experiment-v2-observational-input-capture-v1",
        "experiment_id": str(UUID(experiment_id)),
        "summary": summary,
        "files": files,
        "capture_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sql_template_sha256": hashlib.sha256(SQL_PATH.read_bytes()).hexdigest(),
        "installed_context_functions": snapshot["installed_context_functions"],
        "custody_scope": "local exclusive0600 file/directory fsync; no off-host backup acceptance",
    }
    save(
        directory / "receipt.json",
        canonical({"receipt": receipt, "sha256": hashlib.sha256(DOMAIN + canonical(receipt)).hexdigest()}) + b"\n",
    )
    for path in (directory, directory.parent):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("input_capture_db", ROOT / "scripts/experiment-aa-gates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    records = module.run_sql_json(query(str(args.experiment_id)), timeout=30)
    if len(records) != 1:
        raise ValueError("exactly one actual snapshot required")
    receipt = publish(args.directory, records[0]["snapshot"], str(args.experiment_id))
    print(json.dumps({"as_of": receipt["summary"]["as_of"], "directory": str(args.directory), "claim_scope": CLAIM}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print(f"input capture refused: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
