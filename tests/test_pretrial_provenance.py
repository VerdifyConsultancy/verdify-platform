"""Synthetic provenance cases; never evidence of production target or sensor history."""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/planner-efficacy/pretrial_provenance.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("pretrial_provenance", SCRIPT)
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)

START = datetime(2026, 10, 1, tzinfo=UTC)
END = START + timedelta(hours=1)
ZERO = "0" * 64


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def source(tmp_path, name, value):
    raw = json.dumps(value, sort_keys=True).encode()
    (tmp_path / name).write_bytes(raw)
    return {"path": name, "sha256": sha256(raw).hexdigest()}


def fixture(tmp_path):
    members = []
    for zone in ("north", "east", "west"):
        members.append(
            source(
                tmp_path,
                f"{zone}.json",
                {
                    "schema": provenance.MEMBER_SCHEMA,
                    "greenhouse_id": "vallery",
                    "zone": zone,
                    "contributor_id": f"serial-{zone}",
                    "physical_serial": f"hardware-{zone}",
                    "source_revision_sha256": ZERO,
                    "recorded_at": iso(START - timedelta(days=1)),
                    "exported_at": iso(END + timedelta(minutes=1)),
                    "valid_from": iso(START - timedelta(days=1)),
                    "valid_to": iso(END + timedelta(days=1)),
                    "temp_field": f"temp_{zone}",
                    "vpd_field": f"vpd_{zone}",
                },
            )
        )
    targets = [
        {
            "bucket_start": iso(START + index * timedelta(minutes=15)),
            "temp_low": 65,
            "temp_high": 85,
            "vpd_low": 0.5,
            "vpd_high": 1.5,
        }
        for index in range(4)
    ]
    target = source(
        tmp_path,
        "target.json",
        {
            "schema": provenance.TARGET_SCHEMA,
            "greenhouse_id": "vallery",
            "source_revision_sha256": ZERO,
            "recorded_at": iso(START - timedelta(days=1)),
            "exported_at": iso(END + timedelta(minutes=1)),
            "effective_from": iso(START),
            "effective_to": iso(END),
            "targets": targets,
        },
    )
    return {
        "schema": provenance.SCHEMA,
        "greenhouse_id": "vallery",
        "window_start": iso(START),
        "window_end": iso(END),
        "panel_version": "synthetic-panel-v1",
        "target_version": "synthetic-target-v1",
        "minimum_minutes_per_bin": 12,
        "member_sources": members,
        "target_sources": [target],
    }


def test_verified_sources_emit_existing_fixed_panel_contract(tmp_path):
    manifest = fixture(tmp_path)
    contract, receipt = provenance.build_contract(manifest, tmp_path)
    assert contract["target_basis"] == "frozen_historical_crop_definition"
    assert len(contract["targets"]) == 4
    assert [item["zone"] for item in contract["members"]] == ["north", "east", "west"]
    assert receipt["contract_sha256"] == sha256(provenance.canonical(contract)).hexdigest()
    assert receipt["physical_truth_authenticated"] is False


def test_changed_source_bytes_fail_hash_binding(tmp_path):
    manifest = fixture(tmp_path)
    (tmp_path / "target.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        provenance.build_contract(manifest, tmp_path)


def test_retrospective_target_history_cannot_be_promoted(tmp_path):
    manifest = fixture(tmp_path)
    target = json.loads((tmp_path / "target.json").read_text())
    target["recorded_at"] = iso(START + timedelta(minutes=1))
    manifest["target_sources"] = [source(tmp_path, "target.json", target)]
    with pytest.raises(ValueError, match="retrospective"):
        provenance.build_contract(manifest, tmp_path)


def test_missing_target_bin_fails_closed(tmp_path):
    manifest = fixture(tmp_path)
    target = json.loads((tmp_path / "target.json").read_text())
    target["targets"].pop()
    manifest["target_sources"] = [source(tmp_path, "target.json", target)]
    with pytest.raises(ValueError, match="cover every"):
        provenance.build_contract(manifest, tmp_path)


def test_replaced_or_uncorroborated_contributor_fails(tmp_path):
    manifest = fixture(tmp_path)
    member = json.loads((tmp_path / "west.json").read_text())
    member["valid_to"] = iso(END - timedelta(minutes=1))
    manifest["member_sources"][2] = source(tmp_path, "west.json", member)
    with pytest.raises(ValueError, match="unproven"):
        provenance.build_contract(manifest, tmp_path)


def test_relative_source_cannot_escape_manifest_directory(tmp_path):
    manifest = fixture(tmp_path)
    manifest["member_sources"][0]["path"] = "../outside.json"
    with pytest.raises(ValueError, match="inside"):
        provenance.build_contract(manifest, tmp_path)
