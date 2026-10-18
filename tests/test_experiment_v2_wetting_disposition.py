"""Pure adversarial coverage; synthetic source receipts never qualify a live device."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import experiment_v2_wetting_disposition as wetting

ROOT = Path(__file__).resolve().parents[1]
APP = "b9b7a9dd12c07e53f4370b629774c73125f035f7"
EXP = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
NOW = datetime(2026, 8, 30, 12, 1, 35, tzinfo=UTC)


def qualified_fixture_root(path: Path) -> Path:
    """Synthetic hash-custody only; production validator still denies these bytes."""
    policy = json.loads((ROOT / wetting.POLICY_PATH).read_text())
    report = path / policy["source_analysis"]["path"]
    report.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / policy["source_analysis"]["path"], report)
    for criterion in sorted(wetting.CRITERIA):
        measurement = path / f"fixture-measurement-{criterion}.json"
        measurement.write_text(json.dumps({"synthetic_unit_fixture": True, "criterion": criterion}))
        value = {
            "schema": "verdify-wetting-criterion-qualification-v1",
            "criterion": criterion,
            "result": "qualified",
            "incident_issue": 778,
            "device_id": wetting.DEVICE,
            "source_correction_commit": wetting.CORRECTION,
            "application_source_revision": APP,
            "experiment_id": EXP,
            "observed_at": NOW.isoformat(),
            "firmware_binary_sha256": "f" * 64,
            "measurement_receipt": {
                "path": measurement.name,
                "sha256": hashlib.sha256(measurement.read_bytes()).hexdigest(),
            },
        }
        receipt = path / f"fixture-qualification-{criterion}.json"
        receipt.write_text(json.dumps(value))
        policy["criteria"][criterion] = {
            "path": receipt.name,
            "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        }
    (path / wetting.POLICY_PATH).write_text(json.dumps(policy))
    # Existing causal-source hashes are independently checked by the guard.
    for name in [
        "scripts/verify_component_proof_packet.py",
        "research/planner-efficacy/switchback/v2_selector.py",
        "ingestor/tasks/component_experiment.py",
        "research/planner-efficacy/switchback/v2_outcomes.py",
    ]:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return path


def evaluate(packet, root=ROOT, now=NOW):
    return wetting.blockers(packet, root=root, application_source=APP, experiment_id=EXP, now=now)


def test_current_source_manifest_cannot_qualify_any_physical_criterion():
    packet = wetting.collect(ROOT, application_source=APP, experiment_id=EXP)
    assert set(packet["criterion_evidence"].values()) == {None}
    assert len(evaluate(packet)) == 4
    assert all(":missing:" in b for b in evaluate(packet))


def test_packet_boolean_and_identity_edits_cannot_create_source_evidence():
    assert "typed_evidence_missing" in evaluate(None)[0]
    packet = wetting.collect(ROOT, application_source=APP, experiment_id=EXP)
    for key, value in [
        ("experiment_id", "other"),
        ("application_source_revision", "a" * 40),
        ("manifest_sha256", "a" * 64),
        ("criterion_evidence", {}),
    ]:
        edited = copy.deepcopy(packet)
        edited[key] = value
        assert "source_or_identity_mismatch" in evaluate(edited)[0]


def test_source_approved_fixture_receipts_require_all_matching_current_identity(tmp_path):
    local = qualified_fixture_root(tmp_path)
    packet = wetting.collect(local, application_source=APP, experiment_id=EXP)
    assert len(evaluate(packet, local)) == 4
    assert all("unsupported_measurement_contract" in b for b in evaluate(packet, local))
    assert any(":invalid:" in b for b in evaluate(packet, local, NOW + timedelta(seconds=1801)))
    assert any(":invalid:" in b for b in evaluate(packet, local, NOW - timedelta(seconds=1)))
    assert evaluate(packet, ROOT)  # fixture files never authorize accepted source


@pytest.mark.parametrize("mutation", ["hash", "measurement", "binary", "unknown", "incident", "status"])
def test_source_and_measurement_corruption_fail_closed(tmp_path, mutation):
    local = qualified_fixture_root(tmp_path)
    policy_path = local / wetting.POLICY_PATH
    policy = json.loads(policy_path.read_text())
    criterion = sorted(wetting.CRITERIA)[0]
    receipt_path = local / policy["criteria"][criterion]["path"]
    receipt = json.loads(receipt_path.read_text())
    if mutation == "hash":
        receipt_path.write_text(receipt_path.read_text() + " ")
    elif mutation == "unknown":
        policy["criteria"]["unknown"] = None
    elif mutation == "measurement":
        (local / receipt["measurement_receipt"]["path"]).write_text("changed")
    else:
        receipt[{"binary": "firmware_binary_sha256", "incident": "incident_issue", "status": "result"}[mutation]] = {
            "binary": "e" * 64,
            "incident": 779,
            "status": "reviewed",
        }[mutation]
        receipt_path.write_text(json.dumps(receipt))
        policy["criteria"][criterion]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    policy_path.write_text(json.dumps(policy))
    try:
        packet = wetting.collect(local, application_source=APP, experiment_id=EXP)
    except ValueError:
        packet = {}
    assert evaluate(packet, local)


def test_nondevice_image_carries_manifest_analysis_and_typed_validator():
    dockerfile = (ROOT / "experiment_orchestrator/Dockerfile").read_text()
    for source in [
        "scripts/experiment_v2_wetting_disposition.py",
        wetting.POLICY_PATH,
        "research/planner-efficacy/wetting-incident-2026-09-04-disposition-v4.md",
    ]:
        assert source in dockerfile and (ROOT / source).is_file()
