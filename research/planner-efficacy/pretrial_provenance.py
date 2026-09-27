"""Verify source-owned panel and crop history before a fixed-panel pretrial replay.

Offline and fail closed.  This verifies artifact bytes, chronology, coverage and
the measurement contract; it cannot certify that an external source ledger or
physical serial inventory is truthful.  No historical data is synthesized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path

from fixed_panel import BIN, ZONES, aligned, canonical, identifier, keys, sha, timestamp, validate

SCHEMA = "verdify-fixed-panel-provenance-v1"
MEMBER_SCHEMA = "verdify-contributor-history-v1"
TARGET_SCHEMA = "verdify-crop-target-history-v1"
MAX_SOURCE_BYTES = 8_000_000


def _read_json(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("provenance source exceeds the 8 MB bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provenance source must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _source(manifest_dir: Path, reference: dict) -> tuple[dict, str]:
    keys(reference, ("path", "sha256"))
    sha(reference["sha256"])
    path = reference["path"]
    if not isinstance(path, str) or not path or Path(path).is_absolute():
        raise ValueError("source path must be relative to the manifest")
    root = manifest_dir.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("source must be a file inside the manifest directory")
    value, actual = _read_json(resolved)
    if actual != reference["sha256"]:
        raise ValueError("provenance source SHA-256 mismatch")
    return value, actual


def build_contract(manifest: dict, manifest_dir: Path) -> tuple[dict, dict]:
    keys(
        manifest,
        (
            "schema",
            "greenhouse_id",
            "window_start",
            "window_end",
            "panel_version",
            "target_version",
            "minimum_minutes_per_bin",
            "member_sources",
            "target_sources",
        ),
    )
    if manifest["schema"] != SCHEMA or manifest["greenhouse_id"] != "vallery":
        raise ValueError("unsupported provenance schema or greenhouse")
    start, end = aligned(manifest["window_start"]), aligned(manifest["window_end"])
    if not start < end <= start + timedelta(days=62):
        raise ValueError("completed positive window of at most 62 days required")
    identifier(manifest["panel_version"])
    identifier(manifest["target_version"])
    minimum = manifest["minimum_minutes_per_bin"]
    if type(minimum) is not int or not 1 <= minimum <= 15:
        raise ValueError("minimum_minutes_per_bin must be an integer in [1,15]")
    if not isinstance(manifest["member_sources"], list) or len(manifest["member_sources"]) != 3:
        raise ValueError("exactly three fixed contributor sources required")
    if not isinstance(manifest["target_sources"], list) or not 1 <= len(manifest["target_sources"]) <= 128:
        raise ValueError("1–128 contemporaneous crop target history sources required")

    members = []
    contributor_ids = set()
    physical_serials = set()
    member_evidence = []
    for reference in manifest["member_sources"]:
        source, source_hash = _source(manifest_dir, reference)
        keys(
            source,
            (
                "schema",
                "greenhouse_id",
                "zone",
                "contributor_id",
                "physical_serial",
                "source_revision_sha256",
                "recorded_at",
                "exported_at",
                "valid_from",
                "valid_to",
                "temp_field",
                "vpd_field",
            ),
        )
        zone = source["zone"]
        if source["schema"] != MEMBER_SCHEMA or source["greenhouse_id"] != "vallery" or zone not in ZONES:
            raise ValueError("contributor history identity mismatch")
        identifier(source["contributor_id"])
        identifier(source["physical_serial"])
        sha(source["source_revision_sha256"])
        if (
            source["temp_field"] != f"temp_{zone}"
            or source["vpd_field"] != f"vpd_{zone}"
            or timestamp(source["recorded_at"]) > start
            or timestamp(source["exported_at"]) < end
            or timestamp(source["valid_from"]) > start
            or timestamp(source["valid_to"]) < end
        ):
            raise ValueError("contributor route or time coverage is unproven")
        if timestamp(source["recorded_at"]) > timestamp(source["exported_at"]):
            raise ValueError("contributor source chronology is invalid")
        members.append(
            {
                "zone": zone,
                "contributor_id": source["contributor_id"],
                "identity_evidence_sha256": source_hash,
                "valid_from": source["valid_from"],
                "valid_to": source["valid_to"],
                "temp_field": source["temp_field"],
                "vpd_field": source["vpd_field"],
            }
        )
        contributor_ids.add(source["contributor_id"])
        physical_serials.add(source["physical_serial"])
        member_evidence.append(source_hash)
    if {member["zone"] for member in members} != set(ZONES) or len(contributor_ids) != 3 or len(physical_serials) != 3:
        raise ValueError("fixed contributors must be distinct north/east/west identities")

    targets = {}
    target_evidence = []
    for reference in manifest["target_sources"]:
        source, source_hash = _source(manifest_dir, reference)
        keys(
            source,
            (
                "schema",
                "greenhouse_id",
                "source_revision_sha256",
                "recorded_at",
                "exported_at",
                "effective_from",
                "effective_to",
                "targets",
            ),
        )
        if source["schema"] != TARGET_SCHEMA or source["greenhouse_id"] != "vallery":
            raise ValueError("crop target history identity mismatch")
        sha(source["source_revision_sha256"])
        effective_from, effective_to = aligned(source["effective_from"]), aligned(source["effective_to"])
        recorded, exported = timestamp(source["recorded_at"]), timestamp(source["exported_at"])
        if not effective_from < effective_to or recorded > effective_from or exported < end:
            raise ValueError("crop target history is retrospective or chronologically invalid")
        if not isinstance(source["targets"], list):
            raise ValueError("crop target history targets must be an array")
        for row in source["targets"]:
            keys(row, ("bucket_start", "temp_low", "temp_high", "vpd_low", "vpd_high"))
            bucket = aligned(row["bucket_start"])
            if not start <= bucket < end or not effective_from <= bucket < effective_to or bucket in targets:
                raise ValueError("crop target bin outside source validity or repeated")
            for axis in ("temp", "vpd"):
                low, high = row[f"{axis}_low"], row[f"{axis}_high"]
                if type(low) not in (int, float) or type(high) not in (int, float):
                    raise ValueError("crop target bounds must be numeric")
                if not math.isfinite(low) or not math.isfinite(high) or low > high:
                    raise ValueError("crop target bounds must be finite and ordered")
            targets[bucket] = row
        target_evidence.append(source_hash)
    expected = {start + index * BIN for index in range((end - start) // BIN)}
    if set(targets) != expected:
        raise ValueError("crop target history must cover every declared 15-minute bin")

    contract = {
        "contract_version": 1,
        "panel_version": manifest["panel_version"],
        "target_version": manifest["target_version"],
        "target_basis": "frozen_historical_crop_definition",
        "target_evidence_sha256": hashlib.sha256(canonical(target_evidence)).hexdigest(),
        "minimum_minutes_per_bin": minimum,
        "members": sorted(members, key=lambda item: ZONES.index(item["zone"])),
        "targets": [targets[bucket] for bucket in sorted(targets)],
    }
    # Reuse the existing calculator's exact contract validation, without raw rows.
    validate(
        {
            "contract_version": 1,
            "sample_basis": "database_flush_snapshot",
            "greenhouse_id": "vallery",
            "exported_at": end.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "rows": [],
        },
        contract,
    )
    receipt = {
        "schema": SCHEMA,
        "manifest_canonical_sha256": hashlib.sha256(canonical(manifest)).hexdigest(),
        "member_evidence_sha256": member_evidence,
        "target_evidence_sha256": target_evidence,
        "contract_sha256": hashlib.sha256(canonical(contract)).hexdigest(),
        "physical_truth_authenticated": False,
    }
    return contract, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--contract-output", type=Path, required=True)
    args = parser.parse_args()
    if args.manifest.resolve() == args.contract_output.resolve():
        raise ValueError("output must not overwrite the manifest")
    manifest, manifest_file_sha256 = _read_json(args.manifest)
    contract, receipt = build_contract(manifest, args.manifest.parent)
    receipt["manifest_file_sha256"] = manifest_file_sha256
    with args.contract_output.open("x") as handle:
        json.dump(contract, handle, sort_keys=True, separators=(",", ":"), allow_nan=False)
        handle.write("\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
