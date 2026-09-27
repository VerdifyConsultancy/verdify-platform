"""Claim boundary for the frozen resource baseline; no production access."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research/planner-efficacy"
sys.path.insert(0, str(RESEARCH))
spec = importlib.util.spec_from_file_location("resource_endpoint", RESEARCH / "resource_endpoint.py")
endpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endpoint)

LEDGER = RESEARCH / "resources-2026-08-14_2026-09-05.baseline.json"
CONTRACT = RESEARCH / "protocols/resource-endpoint-v1.json"
RECEIPT = RESEARCH / "protocols/resource-endpoint-2026-08-14_2026-09-05-v1.receipt.json"
LEDGER_HASH = "aa7ef5097efc3a44e6dd15e7ce851fba872af8b3792ab7faff93cf70e8f7d6a3"


def decide(ledger=None, contract=None):
    return endpoint.decision(
        ledger if ledger is not None else endpoint.parse(LEDGER.read_bytes()),
        contract if contract is not None else endpoint.parse(CONTRACT.read_bytes()),
        ledger_sha256=LEDGER_HASH,
        contract_sha256=endpoint.sha256(CONTRACT.read_bytes()),
    )


def test_frozen_22_day_reconciliation_and_claim_boundary():
    assert endpoint.sha256(LEDGER.read_bytes()) == LEDGER_HASH
    result = decide()
    assert result["water"]["accepted_gal"] == "5047.0"
    assert [result["water"][key] for key in ("attributed_gal", "ambiguous_gal", "manual_or_unattributed_gal")] == [
        "1361.0",
        "3465.0",
        "221.0",
    ]
    assert result["water"]["conservation_error_gal"] == "0.0"
    assert result["water"]["historical_source_eligible_days"] == 13
    assert result["electricity"]["scope"] == "partial_shelly_two_channels"
    assert result["electricity"]["historical_source_eligible_days"] == 22
    assert result["electricity"]["observed_subtotal_kwh"] == "124.384"
    assert result["electricity"]["complete_period_kwh"] is None
    assert result["runtime_model"]["observed_subtotal_kwh"] == "342.025"
    assert result["runtime_model"]["complete_period_kwh"] is None
    assert result["selected_resource_endpoint"] is None
    assert result["selected_exploratory_benefit"] == "nine_control_state_minutes"
    assert result["gas_therms"] is result["interior_dli"] is result["resource_cost_usd"] is None
    assert result["whole_resource_claim_eligible"] is result["cost_claim_eligible"] is False
    assert (
        result["water"]["scientific_endpoint_eligible"]
        is result["electricity"]["scientific_endpoint_eligible"]
        is False
    )
    assert endpoint.encode(result) == RECEIPT.read_bytes()


@pytest.mark.parametrize("key,part", [("quality_filtered_meter_gal", "water"), ("ambiguous_gal", "water")])
def test_partition_tampering_refused_even_if_reported_summary_changes(key, part):
    ledger = endpoint.parse(LEDGER.read_bytes())
    ledger["days"][0][part][key] = "10000"
    with pytest.raises(endpoint.ContractError, match="water conservation failure"):
        decide(ledger=ledger)


def test_missing_water_stays_null_with_explicit_subtotal():
    ledger = endpoint.parse(LEDGER.read_bytes())
    ledger["days"][0]["water"]["ambiguous_gal"] = None
    summary = ledger["summary"]["water"]["ambiguous_gal"]
    summary.update(observed_days=21, missing_days=1, observed_subtotal="3399.0", complete_total=None)
    result = decide(ledger=ledger)
    assert result["water"]["ambiguous_gal"] is None
    assert result["water"]["conservation_error_gal"] is None
    assert result["water"]["missing_partition_days"] == 1
    assert result["selected_resource_endpoint"] is None


def test_missing_partial_energy_stays_null_and_model_remains_separate():
    ledger = endpoint.parse(LEDGER.read_bytes())
    first = ledger["days"][0]["energy"]["measured_kwh"]
    ledger["days"][0]["energy"]["measured_kwh"] = None
    summary = ledger["summary"]["partial_electricity"]
    summary.update(
        observed_days=21,
        missing_days=1,
        observed_subtotal=str(endpoint.quantity(summary["observed_subtotal"]) - endpoint.quantity(first)),
        complete_total=None,
    )
    result = decide(ledger=ledger)
    assert result["electricity"]["complete_period_kwh"] is None
    assert result["electricity"]["missing_days"] == 1
    assert result["runtime_model"]["observed_subtotal_kwh"] == "342.025"


def test_complete_period_requires_full_meter_coverage_on_every_day():
    ledger = endpoint.parse(LEDGER.read_bytes())
    for row in ledger["days"]:
        row["energy"]["meter_coverage_pct"] = "100"
    result = decide(ledger=ledger)
    assert result["electricity"]["complete_period_kwh"] == "124.384"
    assert result["electricity"]["scientific_endpoint_eligible"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["water"].update(commissioned=True),
        lambda c: c["electricity"].update(circuit_map_evidence="unverified"),
        lambda c: c["electricity"].update(scope="whole_facility"),
        lambda c: c["electricity"].update(scientific_minimum_daily_coverage_pct=90),
        lambda c: c["water"].update(conservation_tolerance_gal="10"),
    ],
)
def test_v1_contract_cannot_silently_admit_claims(change):
    contract = endpoint.parse(CONTRACT.read_bytes())
    change(contract)
    with pytest.raises(endpoint.ContractError):
        decide(contract=contract)


def test_mixed_scope_and_summary_subtraction_refused():
    ledger = endpoint.parse(LEDGER.read_bytes())
    ledger["days"][0]["energy"]["measured_scope"] = "whole_controlled_equipment_runtime"
    with pytest.raises(endpoint.ContractError, match="mixed energy scope"):
        decide(ledger=ledger)
    ledger = endpoint.parse(LEDGER.read_bytes())
    ledger["summary"]["partial_electricity"]["complete_total"] = "217.641"
    with pytest.raises(endpoint.ContractError, match="ledger energy total mismatch"):
        decide(ledger=ledger)


def test_cli_pins_inputs_and_preserves_existing_output(tmp_path):
    output = tmp_path / "receipt.json"
    args = [
        "--ledger",
        str(LEDGER),
        "--ledger-sha256",
        LEDGER_HASH,
        "--contract",
        str(CONTRACT),
        "--contract-sha256",
        endpoint.sha256(CONTRACT.read_bytes()),
        "--output",
        str(output),
    ]
    assert endpoint.main(args) == 0
    assert output.read_bytes() == RECEIPT.read_bytes()
    assert endpoint.main(args) == 2
    assert output.read_bytes() == RECEIPT.read_bytes()
    args[3] = "0" * 64
    assert endpoint.main(args) == 2
    assert output.read_bytes() == RECEIPT.read_bytes()
