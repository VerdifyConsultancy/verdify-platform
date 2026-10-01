"""Offline custody regressions; these create no live or assigned outcomes."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive", ROOT / "scripts/experiment-v2-reconcile-archive.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def receipt():
    return {
        "schema": "verdify-experiment-v2-daily-reconciliation-v1",
        "experiment_id": ID,
        "as_of": "2026-10-01T20:01:50+00:00",
        "design_lock_sha256": None,
        "timezone": "America/Denver",
        "window_local": "[06:00,24:00)",
        "expected_seconds": 64800,
        "locked_assigned_day_denominator": None,
        "observed_assignments": 0,
        "missing_calendar_days": [],
        "completed_days_missing_freeze_or_evidence": [],
        "rows": [],
        "export_verified": False,
        "export_sha256": None,
        "status": "not_design_locked",
        "claim_scope": "blinded integrity only; no efficacy, physical qualification or launch claim",
    }


def save(path, payload):
    path.write_text(
        json.dumps(
            {"receipt": payload, "sha256": hashlib.sha256(m.RECONCILE.DOMAIN + m.canonical(payload)).hexdigest()}
        )
    )


def test_empty_and_actual_shape_draft_are_never_outcomes(tmp_path):
    assert m.index_archive(tmp_path, ID)["retained_file_count"] == 0
    save(tmp_path / "receipt-a.json", receipt())
    result = m.index_archive(tmp_path, ID)
    assert result["retained_file_count"] == 1 and result["design_lock_sha256"] is None
    assert result["entries"][0]["observed_assignments"] == 0
    assert "no assigned outcome" in result["claim_scope"]


def test_duplicate_files_preserved_and_order_independent(tmp_path):
    save(tmp_path / "receipt-b.json", receipt())
    save(tmp_path / "receipt-a.json", receipt())
    result = m.index_archive(tmp_path, ID)
    assert result["retained_file_count"] == 2 and result["unique_snapshot_count"] == 1
    assert result == m.index_archive(tmp_path, ID)


@pytest.mark.parametrize("case", ["hash", "wrong_study", "restricted", "conflict", "symlink", "mixed_lock"])
def test_refuses_tamper_or_mixed_custody_without_modifying_original(tmp_path, case):
    payload = receipt()
    path = tmp_path / "receipt-a.json"
    save(path, payload)
    if case == "hash":
        path.write_text(path.read_text().replace("not_design_locked", "incomplete"))
    elif case == "wrong_study":
        payload["experiment_id"] = "00000000-0000-0000-0000-000000000001"
        save(path, payload)
    elif case == "restricted":
        payload["secret"] = "forbidden"
        save(path, payload)
    elif case == "conflict":
        payload["status"] = "incomplete"
        save(tmp_path / "receipt-b.json", payload)
    elif case == "symlink":
        (tmp_path / "receipt-link.json").symlink_to(path)
    elif case == "mixed_lock":
        payload["design_lock_sha256"] = "a" * 64
        save(path, payload)
        other = copy.deepcopy(payload)
        other["as_of"] = "2026-10-02T20:00:00+00:00"
        other["design_lock_sha256"] = "b" * 64
        save(tmp_path / "receipt-b.json", other)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("receipt-*.json")}
    with pytest.raises((ValueError, m.jsonschema.ValidationError)):
        m.index_archive(tmp_path, ID)
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob("receipt-*.json")}


def test_cli_refusal_never_echoes_restricted_payload(tmp_path):
    import subprocess
    import sys

    payload = receipt()
    payload["secret"] = "must-not-appear-in-output"
    save(tmp_path / "receipt-a.json", payload)
    output = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/experiment-v2-reconcile-archive.py"),
            "--directory",
            str(tmp_path),
            "--experiment-id",
            ID,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and not output.exists()
    assert "must-not-appear-in-output" not in result.stdout + result.stderr
