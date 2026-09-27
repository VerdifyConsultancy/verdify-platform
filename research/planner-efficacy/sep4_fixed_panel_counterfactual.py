"""Freeze a September 4 database-column panel and a hypothetical house reference.

This is an offline sensitivity input for fixed_panel.py. The reference uses the
source-pinned canonical house anchor curve; it is not historical crop-target,
firmware-consumption, per-probe freshness, or hardware-identity evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ingestor"))
from solar import BandAnchors, band_value_at_phase, compute_solar_times, solar_phase  # noqa: E402

OUTPUT = Path(__file__).with_name("sep4-fixed-panel")
START = datetime(2026, 9, 4, 6, tzinfo=UTC)
END = START + timedelta(days=1)
SOURCE_COMMIT_ON_DAY = "963ea818aad09b02259509cffa6bfdafb48d1702"
ANALYSIS_BASE = "b638e4d688cefbd93cf75617deb6eb5e9023db16"
SOURCES = {
    "verdify_schemas/band_defaults.yaml": "d368f8fe5b5b29b0301bf53d78d084487560b3d5b2d323528d318898994dd148",
    "ingestor/solar.py": "6c9551098fb4f54817614e97ad6d6b159319d21445fc9ccc4620c6221b5914ab",
    "ingestor/entity_map.py": "981911a36464158238ebde098c9852f53691e9182244b585e6467bd2c9b4843f",
    "firmware/greenhouse/sensors.yaml": "1ab62506a9ebc01d909eceaf29a5a32c2f55276db50580d5d503204b097849dc",
    "firmware/greenhouse/hardware.yaml": "f19f5cb3e2add304fc5992e48b25cd64ccf42643c637fdcc1fdba2283884ab71",
}
ANCHORS = {
    "temp_low": (62.50, 73.79, 72.00, 60.71),
    "temp_high": (72.50, 83.79, 82.00, 70.71),
    "vpd_low": (0.755, 0.862, 0.845, 0.738),
    "vpd_high": (1.185, 1.292, 1.275, 1.168),
}


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_sources() -> None:
    for path, expected in SOURCES.items():
        if sha((ROOT / path).read_bytes()) != expected:
            raise ValueError(f"source drift: {path}")


def artifacts() -> dict[str, bytes]:
    verify_sources()
    target_source = {
        "schema": "verdify-sep4-fixed-counterfactual-source-v1",
        "source_commit_on_day": SOURCE_COMMIT_ON_DAY,
        "analysis_base_commit": ANALYSIS_BASE,
        "source_sha256": {key: SOURCES[key] for key in ("verdify_schemas/band_defaults.yaml", "ingestor/solar.py")},
        "hypothesis": "canonical house band defaults evaluated at each bin start as a shared hypothetical reference",
        "target_basis": "fixed_counterfactual_crop_definition",
        "historical_crop_target_verified": False,
        "served_or_consumed_band_verified": False,
        "value_rounding_decimal_places": 6,
    }
    panel_source = {
        "schema": "verdify-sep4-database-column-panel-source-v1",
        "source_commit_on_day": SOURCE_COMMIT_ON_DAY,
        "analysis_base_commit": ANALYSIS_BASE,
        "source_sha256": {
            key: SOURCES[key]
            for key in (
                "ingestor/entity_map.py",
                "firmware/greenhouse/sensors.yaml",
                "firmware/greenhouse/hardware.yaml",
            )
        },
        "members": {
            zone: {"contributor_id": f"climate-column-{zone}", "temp_field": f"temp_{zone}", "vpd_field": f"vpd_{zone}"}
            for zone in ("north", "east", "west")
        },
        "identity_scope": "database columns and source routes only",
        "historical_hardware_identity_verified": False,
        "per_probe_freshness_verified": False,
    }
    target_bytes = encoded(target_source)
    panel_bytes = encoded(panel_source)
    members = [
        {
            "zone": zone,
            "contributor_id": f"climate-column-{zone}",
            "identity_evidence_sha256": sha(panel_bytes),
            "valid_from": START.isoformat(),
            "valid_to": END.isoformat(),
            "temp_field": f"temp_{zone}",
            "vpd_field": f"vpd_{zone}",
        }
        for zone in ("north", "east", "west")
    ]
    targets = []
    denver = ZoneInfo("America/Denver")
    for bin_number in range(96):
        ts = START + bin_number * timedelta(minutes=15)
        local = ts.astimezone(denver)
        offset = int(local.utcoffset().total_seconds() // 60)
        solar = compute_solar_times(local.timetuple().tm_yday, local.year, utc_offset_min=offset)
        phase = solar_phase(local.hour * 60 + local.minute, solar)
        target = {"bucket_start": ts.isoformat()}
        for key, values in ANCHORS.items():
            target[key] = round(band_value_at_phase(BandAnchors(*values), phase), 6)
        targets.append(target)
    contract = {
        "contract_version": 1,
        "panel_version": "sep4-climate-db-columns-v1",
        "target_version": "sep4-canonical-house-band-counterfactual-v1",
        "target_basis": "fixed_counterfactual_crop_definition",
        "target_evidence_sha256": sha(target_bytes),
        "minimum_minutes_per_bin": 12,
        "members": members,
        "targets": targets,
    }
    return {
        "target-source.json": target_bytes,
        "panel-source.json": panel_bytes,
        "contract.json": encoded(contract),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="create versioned inputs, refusing overwrite")
    mode.add_argument("--check", action="store_true", help="compare the committed bytes with pinned source")
    args = parser.parse_args()
    results = artifacts()
    if args.write:
        OUTPUT.mkdir(exist_ok=True)
        for name, data in results.items():
            with (OUTPUT / name).open("xb") as output:
                output.write(data)
    else:
        for name, data in results.items():
            if (OUTPUT / name).read_bytes() != data:
                raise SystemExit(f"generated evidence drift: {name}")
    for name, data in results.items():
        print(name, sha(data))


if __name__ == "__main__":
    main()
