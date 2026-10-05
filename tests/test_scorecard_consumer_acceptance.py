"""Consumer agreement cannot hide credit aliases, unstable rows or missing MCP."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scorecard_acceptance", ROOT / "scripts/scorecard-consumer-acceptance.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def metrics():
    return {"scorecard_contract_version": 2, "compliance_pct": 6.1, "compliance_v2_attributable_pct": 85.8}


def test_transport_agreement_preserves_large_credit_small_binary_and_does_not_qualify_physical():
    values = metrics()
    result = probe.compare(values, values, values, {"pod": values})
    assert result["agreement_proven"] is True
    assert result["physical_publication_qualified"] is False


def test_credit_alias_cannot_pass_as_binary_compliance():
    values = metrics()
    mislabeled = {**values, "compliance_pct": 85.8}
    result = probe.compare(values, values, mislabeled, {"pod": values})
    assert result["agreement_proven"] is False
    assert result["metric_mismatches"][0]["metric"] == "compliance_pct"


def test_unstable_snapshot_or_missing_mcp_prevents_agreement_claim():
    values = metrics()
    assert probe.compare(values, {**values, "compliance_pct": 7}, values, {"pod": values})["agreement_proven"] is False
    assert probe.compare(values, values, values, {})["agreement_proven"] is False
    assert probe.compare(values, values, values, {"pod": None})["agreement_proven"] is False


def test_native_agreement_ignores_only_read_time_and_detects_axis_drift():
    import copy
    import json

    value = json.loads((ROOT / "tests/fixtures/native-route-sep29-measurement.json").read_text())
    later = {**value, "served_at": "2026-10-05T23:00:00+00:00"}
    result = probe.compare_native(value, later, {"api": later, "mcp": value})
    assert result["agreement_proven"] is True
    assert result["physical_publication_qualified"] is False
    drift = copy.deepcopy(value)
    drift["diagnostic"]["temp"]["worst_measured_zone"] = "east"
    result = probe.compare_native(value, later, {"api": later, "mcp": drift})
    assert result["agreement_proven"] is False
    assert result["consumer_mismatches"] == ["mcp"]
    assert probe.compare_native(value, drift, {"api": value})["agreement_proven"] is False
    assert probe.compare_native(value, later, {"api": None})["agreement_proven"] is False
    assert probe.compare_native(None, None, {"api": None})["agreement_proven"] is False
