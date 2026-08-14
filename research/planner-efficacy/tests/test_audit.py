from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

MODULE_PATH = Path(__file__).parents[1] / "audit.py"
SPEC = importlib.util.spec_from_file_location("planner_efficacy_audit", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def test_absolute_humidity_vpd_round_trip() -> None:
    temperature = 72.0
    relative_humidity = 60.0
    humidity = AUDIT.absolute_humidity_g_m3(temperature, relative_humidity)
    expected = AUDIT.saturation_vapor_pressure_kpa(temperature) * 0.40
    assert AUDIT.vpd_from_state(temperature, humidity) == pytest.approx(expected, abs=1e-8)


def test_pid_safety_preempts_wetting_when_cold() -> None:
    spec = AUDIT.PIDSpec("test", 72.0, 0.95, 0.35, 0.02, 0.05, 2.5, 0.1, 0.2)
    policy = AUDIT.CoordinatedPID(spec)
    fixed = {name: 0.0 for name in AUDIT.MODEL_EQUIPMENT}
    actions = policy.actions(np.asarray([40.0, 4.0]), 30.0, 3.0, fixed)
    assert actions["heat1"] == 1.0
    assert actions["vent"] == 0.0
    assert actions["fog"] == 0.0
    assert actions["mister_center"] == 0.0


def test_bootstrap_reports_paired_effect_direction() -> None:
    result = AUDIT.bootstrap_mean_ci(np.asarray([1.0, 2.0, 3.0]), draws=2000)
    assert result["mean"] == 2.0
    assert result["low_95"] > 0.0
    assert result["probability_gt_zero"] == 1.0


def test_transition_reconstruction_uses_fractional_bucket_duty(tmp_path: Path) -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    telemetry = AUDIT.Telemetry(
        times=[start],
        epoch=np.asarray([start.timestamp()]),
        columns={},
        actions={},
        imputation_counts={},
    )
    transitions = tmp_path / "transitions.csv"
    transitions.write_text(
        "ts,equipment,state\n"
        "2026-08-01T00:00:00+00:00,fan1,false\n"
        "2026-08-01T00:05:00+00:00,fan1,true\n"
        "2026-08-01T00:10:00+00:00,fan1,false\n",
        encoding="utf-8",
    )
    AUDIT.attach_equipment_duty(telemetry, transitions)
    assert telemetry.actions["fan1"][0] == pytest.approx(1.0 / 3.0)


def test_counterfactual_gate_requires_every_model_and_headline_direction() -> None:
    passing = {"passes": True}
    models = {
        "one": {"validation": passing, "residuals": passing, "support": passing},
        "two": {"validation": passing, "residuals": passing, "support": passing},
    }
    directions = {
        "climate_actuator_minutes": True,
        "temp_degree_hours_outside": True,
        "vpd_kpa_hours_outside": True,
    }
    assert AUDIT.model_counterfactual_eligible(models, directions)
    models["two"] = {"validation": {"passes": False}, "residuals": passing, "support": passing}
    assert not AUDIT.model_counterfactual_eligible(models, directions)
