"""Diagnostic baseline reproduction, intentionally NOT metric acceptance tests."""

import importlib.util
from pathlib import Path

import pytest


def audit():
    spec = importlib.util.spec_from_file_location("scorecard_audit", Path("scripts/scorecard_measurement_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_reproduces_actual_writer_missingness_and_nominal_duration_defects():
    report = audit().build_report()
    assert report["synthetic_only"] and not report["outcome_acceptance"]
    cases = {row["case"]: row for row in report["cases"]}
    assert cases["empty"]["legacy_denominator"] == 1
    assert cases["empty"]["legacy_binary_pct"] == {"joint": 0, "temp": 0, "vpd": 0}
    assert cases["empty"]["independent_axis_reading_reference"]["temp"]["in_band_pct"] is None
    assert cases["missing_vpd"]["legacy_binary_pct"]["temp"] == 0
    assert cases["missing_vpd"]["independent_axis_reading_reference"]["temp"]["in_band_pct"] == 100
    duplicate = cases["duplicate_hot_minute"]
    assert duplicate["unique_observed_minutes"] == 1
    assert duplicate["legacy_nominal_stress_h"]["heat"] == 1
    assert cases["sparse_hot_samples"]["legacy_nominal_stress_h"]["heat"] == 0.03
    assert cases["nan_temperature"]["legacy_scored_readings"] == 1
    assert cases["nan_temperature"]["independent_axis_reading_reference"]["temp"]["eligible_readings"] == 0
    assert not cases["fully_observed_in_band"]["findings"]
    assert not cases["fully_observed_out_of_band"]["findings"]


def test_audit_executes_writer_source_not_a_copied_algorithm(monkeypatch, tmp_path):
    module = audit()
    source = tmp_path / "daily.py"
    source.write_text(module.SOURCE.read_text().replace("n = scored_readings or len(readings) or 1", "n = 1000"))
    monkeypatch.setattr(module, "SOURCE", source)
    cases = {row["case"]: row for row in module.build_report()["cases"]}
    assert cases["fully_observed_in_band"]["legacy_denominator"] == 1000
    assert cases["fully_observed_in_band"]["legacy_binary_pct"]["joint"] == 0.1


def test_receipt_creation_refuses_overwrite_and_check_detects_drift(monkeypatch, tmp_path):
    import sys

    module = audit()
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["audit", "--output", str(receipt)])
    assert module.main() == 0
    before = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        module.main()
    assert receipt.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["audit", "--check", str(receipt)])
    assert module.main() == 0
    receipt.write_text("{}")
    assert module.main() == 1
