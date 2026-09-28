#!/usr/bin/env python3
"""Project a completed winter day into route-only fixed-panel observations.

This compares six database-flush columns with a prospectively frozen panel-mean
crop reference. It cannot authenticate a physical probe, callback freshness,
crop placement, continuous exposure, or a causal experiment endpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import fixed_panel
import winter_feasibility as winter

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from verdify_schemas.physical_crop_band import RouteOnlyCropBandDiagnostic

FIELDS = tuple(f"{axis}_{zone}" for zone in fixed_panel.ZONES for axis in fixed_panel.AXES)


def _canonical_json(raw: bytes) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict) or winter.canonical(value) != raw:
        raise ValueError("source artifact is not canonical JSON")
    return value


def _panel(raw: bytes) -> dict:
    value = _canonical_json(raw)
    if (
        value.get("schema") != "verdify-winter-route-only-panel-candidate-v1"
        or value.get("study_id") != winter.STUDY_ID
        or value.get("qualification") != "source_route_only_observational"
        or value.get("sample_basis") != "database_flush_snapshot_not_per_probe_observation_time"
        or value.get("physical_hardware_identity_verified") is not False
        or value.get("per_probe_freshness_verified") is not False
    ):
        raise ValueError("panel is not the registered route-only observational source")
    members = value.get("members")
    if not isinstance(members, list) or len(members) != 3:
        raise ValueError("route-only panel must have three members")
    source_hashes = value.get("source_file_sha256")
    if (
        not isinstance(source_hashes, dict)
        or set(source_hashes)
        != {"firmware/greenhouse/hardware.yaml", "firmware/greenhouse/sensors.yaml", "ingestor/entity_map.py"}
        or any(not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) for sha in source_hashes.values())
    ):
        raise ValueError("route-only panel lacks pinned source-file identities")
    routes = set()
    addresses = set()
    for zone, member in zip(fixed_panel.ZONES, members, strict=True):
        if (
            not isinstance(member, dict)
            or member.get("zone") != zone
            or member.get("physical_serial") is not None
            or member.get("temp_field") != f"temp_{zone}"
            or member.get("vpd_field") != f"vpd_{zone}"
            or not isinstance(member.get("route_id"), str)
            or not member["route_id"]
            or type(member.get("modbus_address")) is not int
            or not 1 <= member["modbus_address"] <= 247
        ):
            raise ValueError("route-only member identity or field binding differs")
        routes.add(member["route_id"])
        addresses.add(member["modbus_address"])
    if len(routes) != 3 or len(addresses) != 3:
        raise ValueError("route-only source routes must be distinct")
    return value


def _axis(report: dict, axis: str) -> dict:
    eligible = [row["axes"][axis] for row in report["bins"] if row["axes"][axis]["panel"] is not None]
    high = sum(row["panel"]["high_distance"] > 0 for row in eligible)
    low = sum(row["panel"]["low_distance"] > 0 for row in eligible)
    # An individual zone can be worst even while the equal-weight panel is in band.
    zone_distance = {
        zone: sum(row["zones"][zone]["outside_distance"] for row in eligible) for zone in fixed_panel.ZONES
    }
    worst = max(fixed_panel.ZONES, key=lambda zone: zone_distance[zone]) if any(zone_distance.values()) else None
    summary = report["summary"][axis]
    return {
        "eligible_bins": len(eligible),
        "in_band_bins": sum(row["panel"]["in_band"] for row in eligible),
        "in_band_pct": summary["panel_in_band_bin_pct"],
        "high_miss_bins": high,
        "low_miss_bins": low,
        "mean_high_distance": summary["mean_panel_high_distance"],
        "mean_low_distance": summary["mean_panel_low_distance"],
        "mean_outside_distance": summary["mean_panel_outside_distance"],
        "worst_measured_zone": worst,
    }


def build(instance_raw: bytes, day_raw: bytes, panel_raw: bytes, target_raw: bytes) -> RouteOnlyCropBandDiagnostic:
    instance = _canonical_json(instance_raw)
    start, instance_sha = winter.validate_instance(instance, require_current_source=False)
    if instance_raw != winter.canonical(instance):
        raise ValueError("instance is not canonical")
    if (
        winter.digest(panel_raw) != instance["panel_source_sha256"]
        or winter.digest(target_raw) != instance["crop_target_source_sha256"]
    ):
        raise ValueError("source artifact differs from preregistration")
    panel = _panel(panel_raw)
    target = winter.validate_target_source(
        target_raw, start=start, registered_at=winter._timestamp(instance["registered_at"])
    )
    day_artifact = _canonical_json(day_raw)
    day = date.fromisoformat(day_artifact["local_day"])
    window_start, window_end = winter.bounds(day)
    extracted_at = winter._timestamp(day_artifact.get("extracted_at"))
    if (
        not start <= day < start + timedelta(days=winter.DAY_COUNT)
        or day_artifact.get("schema") != winter.DAY_SCHEMA
        or day_artifact.get("study_id") != winter.STUDY_ID
        or day_artifact.get("local_day") != day.isoformat()
        or day_artifact.get("instance_sha256") != instance_sha
        or winter._timestamp(day_artifact.get("window_start")) != window_start
        or winter._timestamp(day_artifact.get("window_end")) != window_end
        or extracted_at < window_end
        or day_artifact.get("sample_basis") != "database_flush_snapshot_not_per_probe_observation_time"
        or day_artifact.get("daily_collection_timely")
        is not (extracted_at <= window_end + winter.DAILY_COLLECTION_GRACE)
        or not isinstance(day_artifact.get("database_snapshot"), str)
        or not day_artifact["database_snapshot"]
    ):
        raise ValueError("daily artifact is not a completed registered window")
    sources = day_artifact.get("sources")
    climate = sources.get("climate_flush") if isinstance(sources, dict) else None
    if not isinstance(sources, dict) or set(sources) != set(winter.QUERIES) or not isinstance(climate, list):
        raise ValueError("daily artifact has no climate-flush source")
    rows = []
    for row in climate:
        if not isinstance(row, dict) or set(row) != {"ts", *FIELDS}:
            raise ValueError("climate source fields differ from the six-field route contract")
        rows.append({**row, "greenhouse_id": "vallery"})
    targets = [
        row for row in target["target_bins"] if window_start <= winter._timestamp(row["bucket_start"]) < window_end
    ]
    if len(targets) != 72:
        raise ValueError("prospective frozen target has no complete daily window")
    contract = {
        "contract_version": 1,
        "panel_version": "registered-route-only-panel",
        "target_version": target["target_version"],
        "target_basis": "prospective_frozen_panel_mean_crop_reference",
        "target_evidence_sha256": winter.digest(target_raw),
        "minimum_minutes_per_bin": 12,
        "members": [
            {
                "zone": member["zone"],
                "contributor_id": member["route_id"],
                "identity_evidence_sha256": winter.digest(panel_raw),
                "valid_from": window_start.isoformat(),
                "valid_to": window_end.isoformat(),
                "temp_field": member["temp_field"],
                "vpd_field": member["vpd_field"],
            }
            for member in panel["members"]
        ],
        "targets": targets,
    }
    bundle = {
        "contract_version": 1,
        "sample_basis": "database_flush_snapshot",
        "greenhouse_id": "vallery",
        "exported_at": day_artifact["extracted_at"],
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "rows": rows,
    }
    report = fixed_panel.analyze(bundle, contract)
    joint = [row["joint"] for row in report["bins"] if row["joint"]["both_axes_in_band"] is not None]
    diagnostic = {
        "definition": "fixed-panel-route-observation-v1",
        "greenhouse_id": "vallery",
        "day": day,
        "window_start": window_start,
        "window_end": window_end,
        "expected_bins": 72,
        "target_version": target["target_version"],
        "target_revision_id": target["fixed_panel_target_revision_id"],
        "target_source_sha256": winter.digest(target_raw),
        "panel_source_sha256": winter.digest(panel_raw),
        "day_artifact_sha256": winter.digest(day_raw),
        "calculation_source_sha256": report["calculation_source_sha256"],
        "target_basis": "prospective_frozen_panel_mean_crop_reference",
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "contributor_scope": "source_route_only_no_physical_identity",
        "collection_timely": day_artifact["daily_collection_timely"],
        "panel_members": list(fixed_panel.ZONES),
        "panel_routes": [
            {
                "zone": member["zone"],
                "route_id": member["route_id"],
                "modbus_address": member["modbus_address"],
            }
            for member in panel["members"]
        ],
        "crop_placement_verified": False,
        "physical_hardware_identity_verified": False,
        "per_probe_freshness_verified": False,
        "physical_proof_eligible": False,
        "experiment_endpoint_eligible": False,
        "causal_effect_estimate": False,
        "center_probe_measured": False,
        "temp": _axis(report, "temp"),
        "vpd": _axis(report, "vpd"),
        "joint": {
            "eligible_bins": len(joint),
            "in_band_bins": sum(row["both_axes_in_band"] for row in joint),
            "in_band_pct": report["summary"]["joint"]["both_axes_in_band_bin_pct"],
        },
        "temp_unavailable_reasons": dict(
            sorted(
                Counter(
                    row["axes"]["temp"]["unavailable_reason"]
                    for row in report["bins"]
                    if row["axes"]["temp"]["unavailable_reason"]
                ).items()
            )
        ),
        "vpd_unavailable_reasons": dict(
            sorted(
                Counter(
                    row["axes"]["vpd"]["unavailable_reason"]
                    for row in report["bins"]
                    if row["axes"]["vpd"]["unavailable_reason"]
                ).items()
            )
        ),
        "joint_unavailable_reasons": dict(
            sorted(
                Counter(
                    row["joint"]["unavailable_reason"] for row in report["bins"] if row["joint"]["unavailable_reason"]
                ).items()
            )
        ),
    }
    return RouteOnlyCropBandDiagnostic.model_validate(diagnostic)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("instance", "day", "panel_source", "target_source", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", required=True, type=Path)
    args = parser.parse_args()
    evidence = build(
        args.instance.read_bytes(),
        args.day.read_bytes(),
        args.panel_source.read_bytes(),
        args.target_source.read_bytes(),
    )
    winter._write_exclusive(args.output, winter.canonical(evidence.model_dump(mode="json")))


if __name__ == "__main__":
    main()
