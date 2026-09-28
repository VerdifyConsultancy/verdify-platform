"""Fixed 30-pair retention and DST boundaries for the #782 pre-draw audit."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "pre_draw_sensitivity.py"
SPEC = importlib.util.spec_from_file_location("pre_draw_sensitivity", SOURCE)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_fall_clock_change_keeps_primary_window_at_eighteen_hours():
    start, end = AUDIT._window(date(2025, 11, 2))
    assert end - start == timedelta(hours=18)
    assert start.isoformat() == "2025-11-02T13:00:00+00:00"
    assert end.isoformat() == "2025-11-03T07:00:00+00:00"
    assert AUDIT._window(date(2026, 6, 1))[1] - AUDIT._window(date(2026, 6, 1))[0] == timedelta(hours=18)


def test_retention_sensitivity_is_bounded_and_does_not_claim_empirical_power():
    scenarios = AUDIT.retention_scenarios(0.13776, 0.9995)
    assert [row["all_30_pairs_retained_probability"] for row in scenarios] == pytest.approx(
        [0.9851082442083701, 0.7397003733882802, 0.21463876394293727]
    )
    assert scenarios[0]["provisional_model_only_rescaled_joint_power"] == pytest.approx(0.13776)
    assert scenarios[1]["provisional_model_only_rescaled_joint_power"] == pytest.approx(0.10344154973533586)
    assert scenarios[2]["provisional_model_only_rescaled_joint_power"] == pytest.approx(0.030015621424973764)
    for row in scenarios:
        assert (
            row["provisional_model_only_rescaled_joint_power"]
            <= row["absolute_joint_advance_upper_bound_from_retention"]
        )
    with pytest.raises(ValueError, match="exceeds"):
        AUDIT.retention_scenarios(0.99, 0.95)
