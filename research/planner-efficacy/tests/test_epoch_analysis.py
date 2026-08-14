from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import nct, t

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR))
import epoch_analysis as EPOCH


def test_partial_correlation_removes_linear_current_state() -> None:
    current = np.arange(1.0, 9.0)
    residual = np.asarray([-1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0])
    forecast = 2.0 * current + residual
    tunable = 3.0 * current - residual
    assert np.corrcoef(forecast, tunable)[0, 1] > 0.9
    assert EPOCH.partial_correlation(forecast, tunable, current.reshape(-1, 1)) == pytest.approx(-1.0)


def test_nearest_same_slot_match_applies_declared_caliper() -> None:
    features = np.asarray([[0.2], [10.0], [0.0], [2.0], [4.0]])
    local_days = np.asarray(
        [date(2026, 8, 6), date(2026, 8, 7), date(2026, 7, 11), date(2026, 7, 12), date(2026, 7, 13)],
        dtype=object,
    )
    local_slots = np.asarray([8, 8, 8, 8, 8])
    controls, _, _, stale, matched, distances = EPOCH.nearest_same_slot_pairs(
        features,
        local_days,
        local_slots,
        np.asarray([0, 1]),
        date(2026, 7, 11),
    )
    assert controls.tolist() == [2, 3, 4]
    assert stale.tolist() == [0]
    assert matched.tolist() == [2]
    assert distances.tolist() == pytest.approx([0.1])


def test_paired_screening_mde_uses_uncentered_adjacent_day_scale_and_stable_filter() -> None:
    values = np.asarray([1.0, 3.0, 4.0, 3.0, 2.0, 6.0, 9.0, 9.0])
    pair_dates = [
        (date(2026, 7, 11), date(2026, 7, 12)),
        (date(2026, 7, 13), date(2026, 7, 14)),
        (date(2026, 7, 15), date(2026, 7, 16)),
        (date(2026, 7, 17), date(2026, 7, 18)),
    ]
    result = EPOCH.paired_screening_mde(values, pair_dates)
    assert result["historical_pairs"] == 4
    assert result["adjacent_day_uncentered_scale"] == pytest.approx(np.sqrt((4 + 1 + 16 + 0) / 3))
    assert result["distance_from_decision_boundary_for_80pct_power"] == pytest.approx(
        EPOCH.EXPERIMENT_MDE_NONCENTRAL_T_LAMBDA * result["adjacent_day_uncentered_scale"] / np.sqrt(15)
    )
    assert EPOCH.one_sided_lower_better_power(
        result["distance_from_decision_boundary_for_80pct_power"], result["adjacent_day_uncentered_scale"]
    ) == pytest.approx(0.80)
    assert EPOCH.one_sided_lower_better_power(0.0, result["adjacent_day_uncentered_scale"]) == pytest.approx(0.025)

    stable = EPOCH.paired_screening_mde(values, pair_dates, excluded_days={date(2026, 7, 15)})
    assert stable["historical_pairs"] == 3
    assert stable["adjacent_day_uncentered_scale"] == pytest.approx(np.sqrt((4 + 1 + 0) / 2))


def test_noncentral_t_lambda_solves_declared_power_equation() -> None:
    degrees_of_freedom = EPOCH.EXPERIMENT_TARGET_PAIRS - 1
    critical = t.ppf(0.975, degrees_of_freedom)
    achieved_power = nct.sf(critical, degrees_of_freedom, EPOCH.EXPERIMENT_MDE_NONCENTRAL_T_LAMBDA)
    assert achieved_power == pytest.approx(0.80, abs=1e-12)


def test_waypoint_summary_counts_survival_and_future_band_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "waypoints.csv"
    fields = [
        "plan_id",
        "scheduled_while_governing",
        "superseded_before_scheduled",
        "already_due_at_creation",
        "waypoint_ts",
        "band_vpd_high_at_waypoint",
        "climate_intent",
        "materialized_params",
    ]
    intent = {
        "thermal_lead_time_min": 30,
        "forecast_temp_bias_f": -1,
        "forecast_vpd_bias_kpa": -0.4,
        "moisture_engage_vpd_excess_kpa": 0.1,
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "plan_id": "one",
                "scheduled_while_governing": "true",
                "superseded_before_scheduled": "false",
                "already_due_at_creation": "false",
                "waypoint_ts": "2026-08-01T00:00:00+00:00",
                "band_vpd_high_at_waypoint": "1.2",
                "climate_intent": json.dumps(intent),
                "materialized_params": json.dumps({"mister_engage_kpa": 1.3, "night_vpd_bias_kpa": 0}),
            }
        )
        writer.writerow(
            {
                "plan_id": "one",
                "scheduled_while_governing": "false",
                "superseded_before_scheduled": "true",
                "already_due_at_creation": "false",
                "waypoint_ts": "2026-08-02T00:00:00+00:00",
                "band_vpd_high_at_waypoint": "1.4",
                "climate_intent": json.dumps(intent),
                "materialized_params": json.dumps({"mister_engage_kpa": 1.3, "night_vpd_bias_kpa": 0}),
            }
        )
    result = EPOCH.waypoint_summary(path)
    assert result["plans"] == 1
    assert result["waypoints"] == 2
    assert result["scheduled_while_plan_governed"] == 1
    assert result["superseded_before_scheduled"] == 1
    assert result["parameters_varied_within_plan"]["median"] == 0
    assert result["future_due_band_materialization_mismatch_kpa"]["waypoints"] == 2
    assert result["future_due_band_materialization_mismatch_kpa"]["max_abs"] == pytest.approx(0.2)
