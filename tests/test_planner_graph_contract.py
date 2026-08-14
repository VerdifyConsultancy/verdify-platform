"""planner_graph Tier-1 contract regeneration drift guard (#585, audit §8.8).

planner_graph/verdify_contract.py carried a stale hand-copy of the Tier-1
surface (39 old defaults vs the canonical wire-v2 set). The block is now
GENERATED from verdify_schemas.tunable_registry by
scripts/gen-planner-graph-contract.py; these tests fail CI whenever the
committed file drifts from the registry.

Loads verdify_contract.py by FILE (not through the planner_graph package —
its __init__ pulls langgraph/fastapi, which the logic-CI venv deliberately
does not install).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONTRACT_PATH = REPO_ROOT / "planner_graph" / "verdify_contract.py"
GENERATOR = REPO_ROOT / "scripts" / "gen-planner-graph-contract.py"


def _load_contract():
    name = "planner_graph_verdify_contract_drift_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolves cls.__module__ via sys.modules
    spec.loader.exec_module(module)
    return module


def test_generator_check_passes_on_the_committed_file():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_contract_matches_the_canonical_registry_exactly():
    from verdify_schemas.tunable_registry import REGISTRY, TIER1_REG, WIRE_SCHEMA_VERSION

    contract = _load_contract()
    assert set(contract.TIER1_PLAN_DEFAULTS) == set(TIER1_REG)
    assert len(contract.TIER1_PLAN_DEFAULTS) == 39  # wire v2 Tier-1 surface
    for name, default in contract.TIER1_PLAN_DEFAULTS.items():
        assert default == float(REGISTRY[name].default), name
    assert contract.TIER1_CONTRACT_WIRE_SCHEMA_VERSION == WIRE_SCHEMA_VERSION
    assert contract.TIER1_CONTRACT_FIELD_COUNT == len(TIER1_REG)


def test_obsolete_and_retired_names_are_gone():
    contract = _load_contract()
    for gone in (
        "direct_wet_stress_latest_hour",  # retired by wire schema v2 (#588)
        "fog_stress_min_dew_margin_f",
        "fog_stress_window_latest_hour",
        "sw_fog_stress_window_extend_enabled",
    ):
        assert gone not in contract.TIER1_PLAN_DEFAULTS, gone
    # And the previously missing canonical fields are present.
    for present in (
        "band_track_fraction",
        "cool_stage2_exit_hysteresis_f",
        "night_vpd_bias_kpa",
        "vent_exchange_fraction",
    ):
        assert present in contract.TIER1_PLAN_DEFAULTS, present


def test_build_climate_intent_still_produces_the_full_bounded_surface():
    contract = _load_contract()
    intent = contract.build_climate_intent(
        {**contract.TIER1_PLAN_DEFAULTS, "temp_low": 68.0, "temp_high": 76.0, "vpd_low": 0.7, "vpd_high": 1.3}
    )
    assert tuple(intent) == contract.CLIMATE_INTENT_FIELD_NAMES
    for name, value in intent.items():
        lo, hi = contract.CLIMATE_INTENT_RANGES[name]
        assert lo <= value <= hi, name
