"""Reconcile a frozen resource ledger into a claim-limited endpoint decision.

Offline only. The input hash binds a historical extraction, not a physical
sensor. This v1 contract cannot commission an unverified water or power meter.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from resource_ledger import ContractError, encode, iso_date, parse, read_bytes, require, sha256, valid_hash

VERSION = "resource-endpoint-v1"
LEDGER_VERSION = "historical-resource-ledger-v1"
SCOPE_PARTIAL = "partial_shelly_two_channels"
SCOPE_MODELED = "whole_controlled_equipment_runtime"
WATER_PARTS = ("attributed_gal", "ambiguous_gal", "manual_or_unattributed_gal")
ATTRIBUTED_PARTS = (
    "climate_wetting_gal",
    "wall_irrigation_gal",
    "wall_fertigation_gal",
    "unsupported_path_gal",
)
TOLERANCE = Decimal("0.001")


def quantity(value, *, nullable=False, nonnegative=True):
    if nullable and value is None:
        return None
    require(type(value) in (str, int, Decimal) and not isinstance(value, bool), "invalid quantity type")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ContractError("invalid quantity") from None
    require(result.is_finite() and (not nonnegative or result >= 0), "invalid quantity")
    return result


def contract_check(contract):
    require(isinstance(contract, dict) and contract.get("contract_version") == VERSION, "wrong contract version")
    require(contract.get("greenhouse_id") == "vallery", "wrong greenhouse")
    require(contract.get("local_day_timezone") == "America/Denver", "wrong timezone")
    water, power, model = (contract.get(key) for key in ("water", "electricity", "runtime_model"))
    require(all(isinstance(value, dict) for value in (water, power, model)), "missing scope contract")
    require(
        water.get("meter_id") == "main_pulse"
        and water.get("source") == "climate.water_total_gal"
        and water.get("unit") == "gal"
        and quantity(water.get("conservation_tolerance_gal")) == TOLERANCE,
        "unsupported water source",
    )
    require(
        power.get("scope") == SCOPE_PARTIAL
        and power.get("input_unit") == "W"
        and power.get("output_unit") == "kWh"
        and power.get("channels")
        == [
            "sensor.shellyproem50_ac15186daafc_energy_meter_0_power",
            "sensor.shellyproem50_ac15186daafc_energy_meter_1_power",
        ]
        and power.get("diagnostic_minimum_daily_coverage_pct") == 90
        and power.get("scientific_minimum_daily_coverage_pct") == 100,
        "unsupported power source",
    )
    require(model.get("scope") == SCOPE_MODELED and model.get("unit") == "kWh", "unsupported model scope")
    for item, evidence_fields in (
        (water, ("calibration_evidence", "physical_counter_epoch_evidence", "measurement_uncertainty_gal")),
        (
            power,
            (
                "circuit_map_evidence",
                "clock_validation_evidence",
                "calibration_evidence",
                "measurement_uncertainty_kwh",
            ),
        ),
    ):
        require(item.get("commissioned") is False, "v1 cannot commission a source")
        require(
            all(field in item and item[field] is None for field in evidence_fields), "v1 cannot assert missing evidence"
        )
    require(
        all(
            isinstance(contract.get(key), str) and contract[key]
            for key in ("cost_policy", "missing_policy", "claim_policy")
        ),
        "missing claim policy",
    )


def decision(ledger, contract, *, ledger_sha256, contract_sha256):
    contract_check(contract)
    require(isinstance(ledger, dict) and ledger.get("contract_version") == LEDGER_VERSION, "wrong ledger version")
    require(ledger.get("greenhouse_id") == "vallery", "wrong ledger greenhouse")
    window = ledger.get("window")
    require(isinstance(window, dict) and window.get("timezone") == "America/Denver", "wrong ledger window")
    first, stop = iso_date(window.get("start_inclusive")), iso_date(window.get("end_exclusive"))
    days = ledger.get("days")
    require(isinstance(days, list) and 0 < len(days) <= 62, "invalid ledger days")
    require(stop - first == timedelta(days=len(days)) and window.get("days") == len(days), "ledger window mismatch")
    totals = {key: Decimal(0) for key in ("quality_filtered_meter_gal", *WATER_PARTS, *ATTRIBUTED_PARTS)}
    water_observed = dict.fromkeys(totals, 0)
    measured_total, modeled_subtotal = Decimal(0), Decimal(0)
    measured_days = modeled_days = water_source_days = water_quality_days = energy_source_days = 0
    fully_covered_measured_days = 0
    water_missing = measured_missing = modeled_missing = 0
    for index, row in enumerate(days):
        require(
            isinstance(row, dict) and row.get("date") == (first + timedelta(days=index)).isoformat(),
            "ledger day mismatch",
        )
        water, energy = row.get("water"), row.get("energy")
        require(isinstance(water, dict) and isinstance(energy, dict), "missing resource day")
        require(
            energy.get("measured_scope") == SCOPE_PARTIAL and energy.get("modeled_scope") == SCOPE_MODELED,
            "mixed energy scope",
        )
        current = {key: quantity(water.get(key), nullable=True) for key in totals}
        if any(value is None for value in current.values()):
            water_missing += 1
        else:
            require(
                abs(current["quality_filtered_meter_gal"] - sum((current[key] for key in WATER_PARTS), Decimal(0)))
                <= TOLERANCE,
                "water conservation failure",
            )
            require(
                abs(current["attributed_gal"] - sum((current[key] for key in ATTRIBUTED_PARTS), Decimal(0)))
                <= TOLERANCE,
                "water attribution failure",
            )
        for key, value in current.items():
            if value is not None:
                totals[key] += value
                water_observed[key] += 1
        source_flag = water.get("source_available_for_scoring")
        require(source_flag is None or type(source_flag) is bool, "invalid water source flag")
        water_source_days += source_flag is True
        water_quality_days += water.get("ledger_quality") == water.get("resource_quality") == "ok"
        measured = quantity(energy.get("measured_kwh"), nullable=True, nonnegative=False)
        modeled = quantity(energy.get("modeled_kwh"), nullable=True)
        if measured is None:
            measured_missing += 1
        else:
            measured_total += measured
            measured_days += 1
        if modeled is None:
            modeled_missing += 1
        else:
            modeled_subtotal += modeled
            modeled_days += 1
        coverage = quantity(energy.get("meter_coverage_pct"), nullable=True)
        require(coverage is None or coverage <= 100, "invalid meter coverage")
        fully_covered_measured_days += measured is not None and coverage == 100
        energy_source_flag = energy.get("source_measured_available_for_scoring")
        require(energy_source_flag is None or type(energy_source_flag) is bool, "invalid energy source flag")
        energy_source_days += energy_source_flag is True
    summary = ledger.get("summary")
    require(isinstance(summary, dict) and isinstance(summary.get("water"), dict), "missing ledger summary")
    require(
        isinstance(summary.get("source_eligible_water"), dict)
        and summary["source_eligible_water"].get("selected_days") == water_source_days
        and summary.get("source_measured_eligible_days") == energy_source_days,
        "ledger eligibility summary mismatch",
    )
    for key, subtotal in totals.items():
        stated = summary["water"].get(key)
        require(isinstance(stated, dict), "ledger summary mismatch")
        require(
            stated.get("observed_days") == water_observed[key]
            and stated.get("missing_days") == len(days) - water_observed[key],
            "ledger summary days mismatch",
        )
        require(
            quantity(stated.get("observed_subtotal"), nullable=True) == (subtotal if water_observed[key] else None),
            "ledger water subtotal mismatch",
        )
        require(
            quantity(stated.get("complete_total"), nullable=True)
            == (subtotal if water_observed[key] == len(days) else None),
            "ledger water total mismatch",
        )
    for name, scope, subtotal, energy_observed, missing in (
        ("partial_electricity", SCOPE_PARTIAL, measured_total, measured_days, measured_missing),
        ("modeled_electricity", SCOPE_MODELED, modeled_subtotal, modeled_days, modeled_missing),
    ):
        stated = summary.get(name)
        require(isinstance(stated, dict) and stated.get("scope") == scope, "ledger summary scope mismatch")
        require(
            stated.get("observed_days") == energy_observed and stated.get("missing_days") == missing,
            "ledger summary days mismatch",
        )
        require(
            quantity(stated.get("observed_subtotal"), nullable=True, nonnegative=name != "partial_electricity")
            == (subtotal if energy_observed else None),
            "ledger energy subtotal mismatch",
        )
        require(
            quantity(stated.get("complete_total"), nullable=True, nonnegative=name != "partial_electricity")
            == (subtotal if missing == 0 else None),
            "ledger energy total mismatch",
        )

    def complete_water(key):
        return str(totals[key]) if water_observed[key] == len(days) else None

    return {
        "contract_version": VERSION,
        "input_sha256": {"ledger": ledger_sha256, "endpoint_contract": contract_sha256},
        "tool_sha256": sha256(Path(__file__).read_bytes()),
        "window": window,
        "water": {
            "scope": "main_pulse_shared_supply",
            "unit": "gal",
            "accepted_gal": complete_water("quality_filtered_meter_gal"),
            "attributed_gal": complete_water("attributed_gal"),
            "ambiguous_gal": complete_water("ambiguous_gal"),
            "manual_or_unattributed_gal": complete_water("manual_or_unattributed_gal"),
            "attributed_by_scope_gal": {key: complete_water(key) for key in ATTRIBUTED_PARTS},
            "conservation_error_gal": (
                str(totals["quality_filtered_meter_gal"] - sum((totals[key] for key in WATER_PARTS), Decimal(0)))
                if water_missing == 0
                else None
            ),
            "missing_partition_days": water_missing,
            "historical_source_eligible_days": water_source_days,
            "historical_quality_ok_days": water_quality_days,
            "analyzed_days": len(days),
            "scientific_endpoint_eligible": False,
            "reason": "calibration_counter_epoch_and_uncertainty_unverified; shared_supply_attribution_incomplete",
        },
        "electricity": {
            "scope": SCOPE_PARTIAL,
            "unit": "kWh",
            "observed_subtotal_kwh": str(measured_total) if measured_days else None,
            "complete_period_kwh": (
                str(measured_total) if measured_days == len(days) and fully_covered_measured_days == len(days) else None
            ),
            "observed_days": measured_days,
            "missing_days": measured_missing,
            "historical_source_eligible_days": energy_source_days,
            "scientific_endpoint_eligible": False,
            "reason": "circuit_map_clock_calibration_and_uncertainty_unverified",
        },
        "runtime_model": {
            "scope": SCOPE_MODELED,
            "observed_subtotal_kwh": str(modeled_subtotal) if modeled_days else None,
            "complete_period_kwh": str(modeled_subtotal) if modeled_days and modeled_missing == 0 else None,
            "observed_days": modeled_days,
            "missing_days": modeled_missing,
            "scientific_endpoint_eligible": False,
        },
        "selected_resource_endpoint": None,
        "selected_exploratory_benefit": "nine_control_state_minutes",
        "whole_resource_claim_eligible": False,
        "cost_claim_eligible": False,
        "gas_therms": None,
        "interior_dli": None,
        "resource_cost_usd": None,
        "measured_uncertainty": None,
        "claim_limit": "Historical diagnostics only; no measured or modeled resource savings, cost, gas, DLI or whole-facility claim.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new file only")
    args = parser.parse_args(argv)
    try:
        require(valid_hash(args.ledger_sha256) and valid_hash(args.contract_sha256), "input SHA256 required")
        ledger_raw = read_bytes(args.ledger, 5_000_000)
        contract_raw = read_bytes(args.contract, 100_000)
        require(sha256(ledger_raw) == args.ledger_sha256, "ledger hash mismatch")
        require(sha256(contract_raw) == args.contract_sha256, "contract hash mismatch")
        result = decision(
            parse(ledger_raw),
            parse(contract_raw),
            ledger_sha256=args.ledger_sha256,
            contract_sha256=args.contract_sha256,
        )
        raw = encode(result)
        with args.output.open("xb") as stream:
            stream.write(raw)
    except (ContractError, OSError, OverflowError):
        print("Resource endpoint refused: invalid input or existing output; no raw values disclosed.", file=sys.stderr)
        return 2
    print(f"{VERSION}: {result['window']['days']} days; receipt sha256={sha256(raw)}; resource eligible=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
