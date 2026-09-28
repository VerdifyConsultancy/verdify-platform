"""Route-only prospective observations never become physical or controller credit."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.physical_crop_band import RouteOnlyCropBandEvidence

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research/planner-efficacy"
sys.path.insert(0, str(RESEARCH))
spec = importlib.util.spec_from_file_location("route_only_crop_band", RESEARCH / "route_only_crop_band.py")
assert spec and spec.loader
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)
winter = route.winter
DAY = date(2026, 11, 2)
START, END = winter.bounds(DAY)


def fixture():
    panel = {
        "schema": "verdify-winter-route-only-panel-candidate-v1",
        "study_id": winter.STUDY_ID,
        "qualification": "source_route_only_observational",
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "physical_hardware_identity_verified": False,
        "per_probe_freshness_verified": False,
        "source_file_sha256": {
            "firmware/greenhouse/hardware.yaml": "1" * 64,
            "firmware/greenhouse/sensors.yaml": "2" * 64,
            "ingestor/entity_map.py": "3" * 64,
        },
        "members": [
            {
                "zone": zone,
                "route_id": f"{zone}_wall_probe",
                "modbus_address": address,
                "temp_field": f"temp_{zone}",
                "vpd_field": f"vpd_{zone}",
                "physical_serial": None,
            }
            for zone, address in zip(("north", "east", "west"), (2, 5, 3), strict=True)
        ],
    }
    bins = [
        {
            "bucket_start": (winter.bounds(DAY + timedelta(days=day))[0] + timedelta(minutes=15 * quarter)).isoformat(),
            "temp_low": 60.0,
            "temp_high": 80.0,
            "vpd_low": 0.5,
            "vpd_high": 1.5,
        }
        for day in range(60)
        for quarter in range(72)
    ]
    target = {
        "schema": winter.TARGET_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": DAY.isoformat(),
        "recorded_at": "2026-09-27T15:00:00+00:00",
        "effective_from": START.isoformat(),
        "effective_to": winter.bounds(DAY + timedelta(days=59))[1].isoformat(),
        "target_version": "synthetic-frozen-panel-mean-v1",
        "fixed_panel_target_revision_id": 17,
        "source_profile_state_sha256": "a" * 64,
        "profile_revision_ids": [1, 2],
        "crop_assignment_revision_sha256": "b" * 64,
        "target_bins_sha256": winter.digest(winter.canonical(bins)),
        "target_bins": bins,
    }
    day = {
        "schema": winter.DAY_SCHEMA,
        "study_id": winter.STUDY_ID,
        "instance_sha256": None,
        "local_day": DAY.isoformat(),
        "window_start": START.isoformat(),
        "window_end": END.isoformat(),
        "extracted_at": (END + timedelta(minutes=1)).isoformat(),
        "daily_collection_timely": True,
        "database_snapshot": "synthetic-snapshot",
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "sources": {
            **{name: [] for name in winter.QUERIES if name != "climate_flush"},
            "climate_flush": [
                {
                    "ts": (START + timedelta(minutes=minute)).isoformat(),
                    "temp_north": 105.0,
                    "temp_east": 105.0,
                    "temp_west": 105.0,
                    "vpd_north": 3.0,
                    "vpd_east": 3.0,
                    "vpd_west": 3.0,
                }
                for minute in range(12)
            ],
        },
    }
    instance = {
        "schema": winter.INSTANCE_SCHEMA,
        "study_id": winter.STUDY_ID,
        "greenhouse_id": "vallery",
        "timezone": "America/Denver",
        "start_local_date": DAY.isoformat(),
        "day_count": 60,
        "registered_at": "2026-09-27T16:00:00+00:00",
        "source_git_sha": "a" * 40,
        "extractor_sha256": "b" * 64,
        "protocol_sha256": "c" * 64,
        "panel_source_sha256": winter.digest(winter.canonical(panel)),
        "crop_target_source_sha256": winter.digest(winter.canonical(target)),
        "cfg_schema_sha256": "d" * 64,
        "firmware_revision": "2026.7.10.1500.09ee886",
        "observer_role": "read_only_research",
        "archive_id": "synthetic-winter-v1",
    }
    day["instance_sha256"] = winter.digest(winter.canonical(instance))
    return [winter.canonical(value) for value in (instance, day, panel, target)]


def test_high_controller_credit_does_not_mask_low_route_observation():
    raw = fixture()
    observation = route.build(*raw)
    assert observation.expected_bins == 72
    assert observation.temp.eligible_bins == observation.vpd.eligible_bins == observation.joint.eligible_bins == 1
    assert observation.joint.in_band_bins == 0
    assert observation.joint.in_band_pct == 0
    assert observation.temp.high_miss_bins == 1 and observation.vpd.high_miss_bins == 1
    assert observation.physical_proof_eligible is False
    assert observation.physical_hardware_identity_verified is False
    assert observation.per_probe_freshness_verified is False
    card = ScorecardResponse.from_metric_rows(
        [("scorecard_contract_version", 2), ("compliance_pct", 96), ("compliance_v2_attributable_pct", 99)]
    )
    card.route_only_crop_band_evidence = RouteOnlyCropBandEvidence(
        availability="observational",
        unavailable_reason=None,
        day=DAY,
        served_at=END + timedelta(hours=1),
        revision_id=7,
        recorded_at=END + timedelta(minutes=2),
        diagnostic=observation,
    )
    evidence = card.climate_evidence()
    assert evidence["both_axis_compliance_pct"] == 96
    assert evidence["graded_compliance_attributable_pct"] == 99
    assert evidence["route_only_crop_band_evidence"]["diagnostic"]["joint"]["in_band_pct"] == 0
    assert evidence["physical_crop_band_evidence"]["availability"] == "unavailable"
    assert route.build(*raw).model_dump(mode="json") == observation.model_dump(mode="json")


def test_missing_history_or_changed_source_never_becomes_observation():
    instance, day, panel, target = fixture()
    with pytest.raises(ValueError, match="source artifact differs"):
        route.build(instance, day, panel, winter.canonical({"qualified_prospective_crop_target": True}))
    source = copy.deepcopy(json.loads(target))
    source["target_bins"].pop()
    bad_target = winter.canonical(source)
    registered = json.loads(instance)
    registered["crop_target_source_sha256"] = winter.digest(bad_target)
    changed_instance = winter.canonical(registered)
    changed_day = json.loads(day)
    changed_day["instance_sha256"] = winter.digest(changed_instance)
    with pytest.raises(ValueError, match="exactly 4,320"):
        route.build(changed_instance, winter.canonical(changed_day), panel, bad_target)


def test_missing_registered_source_hash_rejects_without_fallback():
    raw = fixture()
    instance = json.loads(raw[0])
    del instance["panel_source_sha256"]
    with pytest.raises(ValueError, match="instance field set differs"):
        route.build(winter.canonical(instance), *raw[1:])
    instance["panel_source_sha256"] = None
    with pytest.raises(ValueError, match="must be a SHA-256"):
        route.build(winter.canonical(instance), *raw[1:])


def test_worst_zone_is_null_only_when_every_zone_is_in_band():
    raw = fixture()
    day = json.loads(raw[1])
    for row in day["sources"]["climate_flush"]:
        row.update(temp_north=70.0, temp_east=70.0, temp_west=70.0)
    all_inside = route.build(raw[0], winter.canonical(day), raw[2], raw[3])
    assert all_inside.temp.in_band_bins == 1
    assert all_inside.temp.worst_measured_zone is None
    for row in day["sources"]["climate_flush"]:
        row.update(temp_north=100.0, temp_east=70.0, temp_west=70.0)
    panel_inside_zone_outside = route.build(raw[0], winter.canonical(day), raw[2], raw[3])
    assert panel_inside_zone_outside.temp.in_band_bins == 1
    assert panel_inside_zone_outside.temp.worst_measured_zone == "north"


def test_route_only_claims_are_enforced_and_unavailable_history_stays_null():
    raw = fixture()
    panel = json.loads(raw[2])
    panel["members"][0]["physical_serial"] = "asserted-serial"
    bad_panel = winter.canonical(panel)
    instance = json.loads(raw[0])
    instance["panel_source_sha256"] = winter.digest(bad_panel)
    altered_instance = winter.canonical(instance)
    day = json.loads(raw[1])
    day["instance_sha256"] = winter.digest(altered_instance)
    with pytest.raises(ValueError, match="route-only member"):
        route.build(altered_instance, winter.canonical(day), bad_panel, raw[3])
    held = RouteOnlyCropBandEvidence(day=date(2026, 9, 4))
    assert held.availability == "unavailable" and held.diagnostic is None
    with pytest.raises(ValidationError):
        RouteOnlyCropBandEvidence(
            availability="observational",
            unavailable_reason=None,
            day=date(2026, 9, 4),
            served_at=datetime(2026, 9, 5, tzinfo=UTC),
            diagnostic=None,
        )


def test_cli_preserves_private_inputs_and_refuses_output_overwrite(tmp_path):
    raw = fixture()
    inputs = [tmp_path / name for name in ("instance.json", "day.json", "panel.json", "target.json")]
    for path, source in zip(inputs, raw, strict=True):
        path.write_bytes(source)
    output = tmp_path / "observation.json"
    command = [
        sys.executable,
        str(RESEARCH / "route_only_crop_band.py"),
        "--instance",
        str(inputs[0]),
        "--day",
        str(inputs[1]),
        "--panel-source",
        str(inputs[2]),
        "--target-source",
        str(inputs[3]),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    expected = winter.canonical(route.build(*raw).model_dump(mode="json"))
    assert output.read_bytes() == expected
    assert [path.read_bytes() for path in inputs] == raw
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert second.returncode != 0 and output.read_bytes() == expected
