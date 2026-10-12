#!/usr/bin/env python3
"""Reproduce #782's fixed-30 planning sensitivity without a provider or device.

Only three explicitly named, hash-pinned private historical inputs are read.
No study schedule is created and no empirical endpoint is inferred from the
provisional planning assumptions. The existing 80 percent sample-size chooser
is deliberately not called for the accepted exploratory 30-pair design.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path

import numpy
import scipy

ROOT = Path(__file__).resolve().parents[4]
RESEARCH = ROOT / "research/planner-efficacy"
sys.path.insert(0, str(RESEARCH))

from switchback.v2_power import PowerAssumptions, SelectorReplay, simulate_joint_power


def canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, allow_nan=False, sort_keys=True, indent=2) + "\n")


def build(input_dir: Path) -> tuple[dict, dict]:
    spec = importlib.util.spec_from_file_location("pre_draw_sensitivity", RESEARCH / "pre_draw_sensitivity.py")
    assert spec and spec.loader
    historical = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(historical)
    # Existing calculator verifies all private hashes and every raw bin/summary.
    climate = historical.build(input_dir)

    contract_path = RESEARCH / "protocols/warm-predraw-analysis-contract-v1.json"
    contract = read_json(contract_path)
    source_hashes = {name: digest((ROOT / name).read_bytes()) for name in contract["files"]}
    if source_hashes != contract["files"]:
        raise ValueError("current warm analysis contract source hashes changed")
    if contract["power"]["minimum_power_launch_gate"] is not False:
        raise ValueError("accepted exploratory contract acquired a minimum-power gate")
    if contract["calendar"] != {
        "start": "2027-06-01",
        "end_inclusive": "2027-07-30",
        "timezone": "America/Denver",
        "days": 60,
        "pairs": 30,
        "daily_window": "[06:00,24:00)",
        "seconds": 64800,
    }:
        raise ValueError("fixed warm candidate changed")

    power_path = RESEARCH / "protocols/planner-switchback-v2-power.json"
    provisional = read_json(power_path)
    if provisional["artifact_sha256"] != digest(
        canonical({key: value for key, value in provisional.items() if key != "artifact_sha256"})
    ):
        raise ValueError("provisional planning artifact hash differs")
    assumptions = provisional["assumptions"]
    base = PowerAssumptions(
        paired_sd=tuple(assumptions["paired_sd"]),
        true_nonbaseline_effect=tuple(assumptions["true_nonbaseline_effect"]),
        selector_replay=SelectorReplay(**assumptions["selector_replay"]),
        correlation=tuple(tuple(row) for row in assumptions["correlation"]),
        complete_pair_probability=assumptions["complete_pair_probability"],
        one_sided_confidence_level=assumptions["one_sided_confidence_level"],
        minimum_joint_power=assumptions["minimum_joint_power"],
    )
    simulations = []
    for completeness in (0.9995, 0.99, 0.95):
        scenario = replace(base, complete_pair_probability=completeness)
        first = simulate_joint_power(30, scenario, repetitions=25_000, seed=588_640)
        second = simulate_joint_power(30, scenario, repetitions=25_000, seed=588_640)
        if canonical(first) != canonical(second):
            raise ValueError("fixed-seed numerical simulation did not reproduce")
        # This diagnostic threshold is not the accepted study's launch gate.
        first.pop("passes_minimum_joint_power")
        simulations.append(
            {
                "assumed_independent_complete_pair_probability": completeness,
                "result": first,
                "repeat_canonical_sha256": digest(canonical(first)),
                "two_repetitions_byte_identical": True,
            }
        )

    reference = next(row for row in provisional["selection"]["evaluations"] if row["pairs"] == 30)
    reference_without_threshold = {
        key: value for key, value in reference.items() if key != "passes_minimum_joint_power"
    }
    if simulations[0]["result"] != reference_without_threshold:
        raise ValueError("fixed-30 reference replay differs from the preserved provisional calculation")

    source_hashes.update(
        {
            str(contract_path.relative_to(ROOT)): digest(contract_path.read_bytes()),
            str(power_path.relative_to(ROOT)): digest(power_path.read_bytes()),
            str(Path(historical.__file__).relative_to(ROOT)): digest(Path(historical.__file__).read_bytes()),
            str(Path(__file__).relative_to(ROOT)): digest(Path(__file__).read_bytes()),
        }
    )
    receipt = {
        "schema": "verdify-782-fixed30-source-eligibility-20261012-v1",
        "source_revision": "b5bdb3c65b243c230b7864fa81b95f926b2e68ff",
        "scope": "Recomputed immutable historical climate eligibility and provisional fixed-30 planning sensitivity only",
        "source_sha256": source_hashes,
        "private_input_sha256": historical.INPUT_SHA256,
        "calendar": contract["calendar"],
        "historical_climate_sha256": digest(canonical(climate)),
        "historical_analogues": climate["historical_analogues"],
        "selector_input_presence": climate["selector_input_presence"],
        "input_eligibility": {
            "historical_18h_route_climate_coverage": "authentic fixed-input recomputation; necessary climate coverage only",
            "historical_crop_target_and_physical_contributor_identity": "unavailable",
            "asof_forecast_and_actual_frozen_selector_replay": "unavailable in these fixed historical inputs",
            "direct_nine_state_equipment_receipts": "unavailable in these fixed historical inputs",
            "actual_18h_three_endpoint_paired_variance_and_covariance": None,
            "empirical_joint_advance_power": None,
            "physical_missing_endpoint_bounds": None,
        },
        "provisional_fixed30_simulations": simulations,
        "retention_only_reweighting": historical.retention_scenarios(0.13776, 0.9995),
        "reference_fixed30_reproduced": True,
        "minimum_power_launch_gate": False,
        "sample_size_chooser_called": False,
        "actual_provider_calls": 0,
        "database_connections": 0,
        "device_calls": 0,
        "study_schedule_draws": 0,
        "design_lock": None,
        "analysis_environment": {
            "kind": "operator host numerical reproduction; not a frozen design-lock analyzer image",
            "python": platform.python_version(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "analyzer_image_digest": None,
        },
        "limits": [
            "Provisional paired scales describe the old 22-hour window, not an empirical 18-hour treatment endpoint.",
            "Selector proportions, endpoint correlation/effects and completeness are planning assumptions.",
            "Warm historical climate eligibility does not authenticate crop targets, probe identity or future readiness.",
            "Deterministic numerical simulations create no blinded study schedule or randomized assignment.",
            "The winter passive observer remains separate; neither #782 nor physical/launch acceptance is claimed.",
        ],
    }
    return climate, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    climate, receipt = build(args.input_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "historical-climate-recomputed.json", climate)
    write_json(args.output_dir / "fixed30-eligibility.json", receipt)
    print(
        "Reproduced immutable historical climate inputs and three fixed-30 scenarios twice; empirical gaps remain null."
    )


if __name__ == "__main__":
    main()
