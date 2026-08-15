"""Golden/boundary tests for the §8.3 qualification regime classifier.

The four regimes must be mutually exclusive and exhaustive with the audit's
exact boundary semantics: night ``solar < 20`` (strict); hot/bright
``solar >= 400`` and ``temp >= 80`` (inclusive); humidity ratio ``<= 0.012``
dry vs ``> 0.012`` humid.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verdify_schemas.experiment_regimes import (  # noqa: E402
    DEFAULT_STATION_PRESSURE_KPA,
    EDGES,
    REGIMES,
    Regime,
    TemplateKind,
    cell_index,
    classify_regime,
    edge_for_cell,
    humidity_ratio_kg_per_kg,
    maybe_classify_regime,
    regime_for_cell,
    saturation_vapor_pressure_kpa,
)

P = DEFAULT_STATION_PRESSURE_KPA


# --- psychrometrics ---------------------------------------------------------


def test_saturation_vapor_pressure_matches_forecast_task_constants():
    """Same Tetens constants as ingestor/tasks/forecast.py:_outdoor_vpd_kpa."""
    for temp_f in (32.0, 50.0, 68.0, 80.0, 95.0):
        temp_c = (temp_f - 32.0) * 5.0 / 9.0
        expected = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
        assert saturation_vapor_pressure_kpa(temp_f) == pytest.approx(expected, rel=1e-12)


def test_saturation_vapor_pressure_reference_point():
    # Tetens at 25°C (77°F) is ~3.167 kPa (textbook reference value).
    assert saturation_vapor_pressure_kpa(77.0) == pytest.approx(3.167, abs=0.01)


def test_humidity_ratio_reference_values():
    # 80°F ~ 26.667°C, psat ~ 3.497 kPa. At 40% RH and the 84.0 kPa site
    # pressure: pv ~ 1.3988 kPa, W = 0.62198 * 1.3988 / (84.0 - 1.3988)
    # ~ 0.010533.
    w = humidity_ratio_kg_per_kg(80.0, 40.0, P)
    assert w == pytest.approx(0.010533, abs=2e-4)
    # Same air at 60% RH crosses the 0.012 split (~0.01593).
    w_humid = humidity_ratio_kg_per_kg(80.0, 60.0, P)
    assert w_humid > 0.012
    # Dry air has ratio 0; saturated hot air is large but finite.
    assert humidity_ratio_kg_per_kg(80.0, 0.0, P) == 0.0
    assert humidity_ratio_kg_per_kg(100.0, 100.0, P) > 0.04


def test_humidity_ratio_lower_pressure_raises_ratio():
    # Site altitude (lower station pressure) increases W for identical T/RH.
    w_sea = humidity_ratio_kg_per_kg(85.0, 45.0, 101.325)
    w_alt = humidity_ratio_kg_per_kg(85.0, 45.0, 81.0)  # ~1900 m
    assert w_alt > w_sea


@pytest.mark.parametrize(
    ("temp_f", "rh", "pressure"),
    [
        (float("nan"), 50.0, P),
        (80.0, float("inf"), P),
        (80.0, -1.0, P),
        (80.0, 101.0, P),
        (80.0, 50.0, 0.0),
        (80.0, 50.0, -5.0),
        (212.0, 100.0, 50.0),  # vapor pressure exceeds station pressure
    ],
)
def test_humidity_ratio_rejects_invalid_inputs(temp_f, rh, pressure):
    with pytest.raises(ValueError):
        humidity_ratio_kg_per_kg(temp_f, rh, pressure)


# --- regime boundaries ------------------------------------------------------


def test_night_boundary_is_strict():
    assert classify_regime(19.999, 90.0, 10.0, P) is Regime.NIGHT
    assert classify_regime(0.0, 90.0, 10.0, P) is Regime.NIGHT
    # Exactly 20 W/m² is NOT night.
    assert classify_regime(20.0, 70.0, 10.0, P) is Regime.OTHER_DAYLIGHT


def test_hot_bright_boundaries_are_inclusive():
    # Exactly solar=400 and temp=80 qualifies as hot/bright.
    assert classify_regime(400.0, 80.0, 20.0, P) is Regime.HOT_BRIGHT_DRY
    # Just under either threshold falls back to other daylight.
    assert classify_regime(399.999, 80.0, 20.0, P) is Regime.OTHER_DAYLIGHT
    assert classify_regime(400.0, 79.999, 20.0, P) is Regime.OTHER_DAYLIGHT


def test_dry_humid_split_at_0_012_inclusive_dry():
    # Find RH values straddling W = 0.012 at 85°F, sea level.
    lo, hi = 0.0, 100.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if humidity_ratio_kg_per_kg(85.0, mid, P) <= 0.012:
            lo = mid
        else:
            hi = mid
    # lo yields W <= 0.012 (dry, inclusive); hi yields W > 0.012 (humid).
    assert classify_regime(500.0, 85.0, lo, P) is Regime.HOT_BRIGHT_DRY
    assert classify_regime(500.0, 85.0, hi, P) is Regime.HOT_BRIGHT_HUMID


def test_regime_golden_table():
    goldens = [
        # (solar, temp_f, rh_pct, pressure_kpa) -> regime
        ((5.0, 60.0, 80.0, P), Regime.NIGHT),
        ((19.9, 95.0, 5.0, P), Regime.NIGHT),
        ((50.0, 95.0, 5.0, P), Regime.OTHER_DAYLIGHT),
        ((399.0, 95.0, 5.0, P), Regime.OTHER_DAYLIGHT),
        ((450.0, 75.0, 5.0, P), Regime.OTHER_DAYLIGHT),
        ((450.0, 85.0, 20.0, P), Regime.HOT_BRIGHT_DRY),
        ((450.0, 85.0, 70.0, P), Regime.HOT_BRIGHT_HUMID),
        ((1000.0, 100.0, 5.0, P), Regime.HOT_BRIGHT_DRY),
        ((1000.0, 100.0, 90.0, P), Regime.HOT_BRIGHT_HUMID),
    ]
    for args, expected in goldens:
        assert classify_regime(*args) is expected, args


def test_regimes_partition_grid():
    """Every finite input maps to exactly one regime (exhaustive partition)."""
    for solar in (0.0, 19.99, 20.0, 100.0, 399.99, 400.0, 800.0):
        for temp in (40.0, 79.99, 80.0, 95.0):
            for rh in (5.0, 50.0, 95.0):
                regime = classify_regime(solar, temp, rh, P)
                assert isinstance(regime, Regime)


def test_classify_rejects_invalid_solar():
    with pytest.raises(ValueError):
        classify_regime(float("nan"), 80.0, 50.0, P)
    with pytest.raises(ValueError):
        classify_regime(-1.0, 80.0, 50.0, P)


def test_maybe_classify_missing_inputs():
    assert maybe_classify_regime(None, 80.0, 50.0, P) is None
    # Night needs only solar.
    assert maybe_classify_regime(5.0, None, None, None) is Regime.NIGHT
    # Other-daylight needs solar + temp but not humidity inputs.
    assert maybe_classify_regime(100.0, 70.0, None, None) is Regime.OTHER_DAYLIGHT
    assert maybe_classify_regime(100.0, None, None, None) is None
    # Hot/bright without humidity inputs cannot be proven -> None.
    assert maybe_classify_regime(500.0, 90.0, None, None) is None
    assert maybe_classify_regime(500.0, 90.0, 40.0, None) is None
    # 90°F at 40% RH sits just over the 0.012 kg/kg split (W ~ 0.01205);
    # 30% RH is clearly dry (W ~ 0.0090).
    assert maybe_classify_regime(500.0, 90.0, 40.0, P) is Regime.HOT_BRIGHT_HUMID
    assert maybe_classify_regime(500.0, 90.0, 30.0, P) is Regime.HOT_BRIGHT_DRY
    # Invalid values behave as missing, never raise.
    assert maybe_classify_regime(float("nan"), 80.0, 50.0, P) is None
    assert maybe_classify_regime(500.0, 90.0, 150.0, P) is None


# --- 24-cell canonical index ------------------------------------------------


def test_cell_index_layout_is_locked():
    assert len(EDGES) == 6
    assert len(REGIMES) == 4
    # Locked orderings (spec instances hash these; never reorder).
    assert EDGES[0] == (TemplateKind.BASELINE, TemplateKind.MODERATE)
    assert EDGES[5] == (TemplateKind.AGGRESSIVE, TemplateKind.MODERATE)
    assert REGIMES[0] is Regime.NIGHT
    assert REGIMES[3] is Regime.OTHER_DAYLIGHT

    seen = set()
    for edge in EDGES:
        for regime in REGIMES:
            idx = cell_index(edge, regime)
            assert 0 <= idx <= 23
            assert edge_for_cell(idx) == edge
            assert regime_for_cell(idx) is regime
            seen.add(idx)
    assert seen == set(range(24))
    # Golden anchors.
    assert cell_index((TemplateKind.BASELINE, TemplateKind.MODERATE), Regime.NIGHT) == 0
    assert cell_index((TemplateKind.AGGRESSIVE, TemplateKind.MODERATE), Regime.OTHER_DAYLIGHT) == 23


def test_cell_index_rejects_unknown_edge():
    with pytest.raises(ValueError):
        cell_index((TemplateKind.BASELINE, TemplateKind.BASELINE), Regime.NIGHT)
    with pytest.raises(ValueError):
        edge_for_cell(24)
    with pytest.raises(ValueError):
        regime_for_cell(-1)
