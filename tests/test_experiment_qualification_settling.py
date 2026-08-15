"""Frozen §8.3 settling-time analyzer — synthetic-trace tests (#588).

Known first-order traces with analytically known settling boundaries, a
noisy trace, a >2h failure case, an identity-late failure case,
disturbance adjustment, unanalyzable inputs, and result-hash determinism.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research" / "planner-efficacy"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from qualification.settling import (  # noqa: E402
    ENDPOINT_BANDS,
    GATE_MAX_SETTLING_H,
    POST_STEP_BINS,
    analyze,
    analyze_endpoint,
    analyze_transition,
    fit_first_order,
    settling_time_h,
)

T_H = [round(0.25 * (i + 1), 6) for i in range(POST_STEP_BINS)]


def _first_order_bins(y_inf, amplitude, tau_h, *, noise=None, disturbance=None, beta=0.0):
    bins = []
    for i, t in enumerate(T_H):
        value = y_inf + amplitude * math.exp(-t / tau_h)
        if disturbance is not None:
            d_bar = sum(disturbance) / len(disturbance)
            value += beta * (disturbance[i] - d_bar)
        if noise is not None:
            value += noise[i]
        item = {"t_h": t, "value": value}
        if disturbance is not None:
            item["disturbance"] = disturbance[i]
        bins.append(item)
    return bins


def _transition(
    *,
    transition_id="t-1",
    identity_s=30.0,
    vpd=(1.0, 0.2, 0.5),
    temp=(75.0, 2.0, 0.4),
    duty=(40.0, 30.0, 0.4),
    **overrides,
):
    endpoints = {
        "vpd_kpa": {"bins": _first_order_bins(*vpd)},
        "temp_f": {"bins": _first_order_bins(*temp)},
        "duty_devmin": {"bins": _first_order_bins(*duty)},
    }
    endpoints.update(overrides)
    return {
        "transition_id": transition_id,
        "slot_id": "s-1",
        "cell_index": 1,
        "edge": "baseline->moderate",
        "regime": "hot_bright_dry",
        "step_time_utc": "2026-09-01T12:00:00Z",
        "identity_confirm_s": identity_s,
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# Fit + settling on clean traces
# ---------------------------------------------------------------------------


def test_clean_first_order_recovers_parameters_and_settling():
    bins = _first_order_bins(1.0, 0.2, 0.5)
    result = analyze_endpoint("vpd_kpa", bins)
    assert result["analyzable"] and result["pass"], result["failures"]
    assert result["tau_h"] == pytest.approx(0.5, abs=0.01)
    assert result["asymptote"] == pytest.approx(1.0, abs=1e-3)
    assert result["amplitude"] == pytest.approx(0.2, abs=1e-3)
    # 0.2*exp(-t/0.5) <= 0.025 first holds at t >= ln(8)/2 = 1.0397 -> 1.25 h.
    assert result["settling_h"] == 1.25


def test_settling_time_analytic_boundaries():
    # |A| already inside the band settles at the first boundary.
    assert settling_time_h(0.01, 1.0, 0.025) == 0.25
    # Never settles within six hours -> None.
    assert settling_time_h(5.0, 50.0, 0.025) is None
    # Duty: 30 dev-min, tau 0.4 h, band 6.75 -> t >= 0.4*ln(30/6.75)=0.597 -> 0.75.
    assert settling_time_h(30.0, 0.4, 6.75) == 0.75


def test_duty_endpoint_settling():
    result = analyze_endpoint("duty_devmin", _first_order_bins(40.0, 30.0, 0.4))
    assert result["pass"]
    assert result["settling_h"] == 0.75


def test_noisy_trace_still_recovers_settling():
    noise = [0.004 * (1 if i % 2 == 0 else -1) for i in range(POST_STEP_BINS)]
    result = analyze_endpoint("vpd_kpa", _first_order_bins(1.0, 0.2, 0.5, noise=noise))
    assert result["analyzable"]
    assert result["settling_h"] == 1.25
    assert result["tau_h"] == pytest.approx(0.5, abs=0.08)
    assert result["diagnostics"]["r_squared_ok"]


def test_disturbance_adjustment_removes_covariate_bias():
    # A strong linear outdoor ramp contaminates the trace; the frozen model
    # regresses it out and recovers the true asymptote/settling.
    ramp = [float(i) for i in range(POST_STEP_BINS)]
    bins = _first_order_bins(1.0, 0.2, 0.5, disturbance=ramp, beta=0.03)
    result = analyze_endpoint("vpd_kpa", bins)
    assert result["pass"], result["failures"]
    assert result["asymptote"] == pytest.approx(1.0, abs=5e-3)
    assert result["beta"] == pytest.approx(0.03, abs=1e-3)
    assert result["settling_h"] == 1.25


def test_immaterial_transient_is_diagnostic_exempt():
    # Amplitude below the band: settles at the first boundary; R^2 is
    # meaningless for a flat response and must not fail the endpoint.
    noise = [1e-4 * (1 if i % 3 == 0 else -1) for i in range(POST_STEP_BINS)]
    result = analyze_endpoint("vpd_kpa", _first_order_bins(1.0, 0.01, 0.5, noise=noise))
    assert result["analyzable"]
    assert result["settling_h"] == 0.25
    assert not result["diagnostics"]["material_transient"]
    assert result["pass"], result["failures"]


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------


def test_slow_response_over_two_hours_fails():
    # tau=1.2 h, A=0.3: settles at t >= 1.2*ln(12) = 2.98 -> 3.0 h > 2 h.
    result = analyze_endpoint("vpd_kpa", _first_order_bins(1.0, 0.3, 1.2))
    assert result["settling_h"] == 3.0
    assert "settling_over_2h" in result["failures"]
    assert not result["pass"]

    outcome = analyze({"transitions": [_transition(vpd=(1.0, 0.3, 1.2))]}, expected_transitions=1)
    gate = outcome["result"]["gate"]
    assert not gate["pass"]
    assert "max_settling_over_2h" in gate["failures"]
    assert gate["max_settling_h"] == 3.0


def test_never_settling_response_fails():
    result = analyze_endpoint("vpd_kpa", _first_order_bins(1.0, 5.0, 50.0))
    assert result["settling_h"] is None
    assert "unsettled_within_6h" in result["failures"]


def test_identity_late_fails_transition_and_gate():
    outcome = analyze({"transitions": [_transition(identity_s=150.0)]}, expected_transitions=1)
    transition = outcome["result"]["transitions"][0]
    assert not transition["identity_ok"]
    assert "identity_not_confirmed_within_120s" in transition["failures"]
    gate = outcome["result"]["gate"]
    assert not gate["pass"]
    assert "identity_gate" in gate["failures"]
    # Settling itself was fine — the gate is an AND of all conditions.
    assert gate["max_settling_h"] is not None
    assert gate["max_settling_h"] <= GATE_MAX_SETTLING_H


def test_missing_bins_are_unanalyzable_and_fail_gate():
    bad = _transition()
    bad["endpoints"]["temp_f"]["bins"] = bad["endpoints"]["temp_f"]["bins"][:-1]
    outcome = analyze({"transitions": [bad]}, expected_transitions=1)
    gate = outcome["result"]["gate"]
    assert not gate["pass"]
    assert "unanalyzable_transitions" in gate["failures"]


def test_missing_endpoint_fails():
    result = analyze_transition(
        {
            "transition_id": "t-x",
            "identity_confirm_s": 10.0,
            "endpoints": {"vpd_kpa": {"bins": _first_order_bins(1.0, 0.1, 0.4)}},
        }
    )
    assert not result["pass"]
    assert "endpoint_missing:temp_f" in result["failures"]
    assert "endpoint_missing:duty_devmin" in result["failures"]


def test_transition_count_must_match_expected():
    outcome = analyze({"transitions": [_transition()]}, expected_transitions=96)
    gate = outcome["result"]["gate"]
    assert not gate["pass"]
    assert any(f.startswith("transition_count:") for f in gate["failures"])


def test_full_pass_gate():
    transitions = [_transition(transition_id=f"t-{i}", identity_s=15.0 + i) for i in range(3)]
    outcome = analyze({"transitions": transitions}, expected_transitions=3)
    gate = outcome["result"]["gate"]
    assert gate["pass"], gate["failures"]
    assert gate["observed_transitions"] == 3
    assert gate["max_identity_confirm_s"] == 17.0
    assert gate["max_settling_h"] <= GATE_MAX_SETTLING_H


# ---------------------------------------------------------------------------
# Hash determinism
# ---------------------------------------------------------------------------


def test_result_hash_is_deterministic_and_sensitive():
    payload = {"study_id": "study-x", "transitions": [_transition()]}
    first = analyze(payload, expected_transitions=1)
    second = analyze(payload, expected_transitions=1)
    assert first["result_sha256"] == second["result_sha256"]
    assert len(first["result_sha256"]) == 64

    perturbed = {"study_id": "study-x", "transitions": [_transition(identity_s=31.0)]}
    third = analyze(perturbed, expected_transitions=1)
    assert third["result_sha256"] != first["result_sha256"]


def test_fit_rejects_degenerate_input_gracefully():
    # Constant series: fit succeeds with ~zero amplitude (never None here);
    # settle at the first boundary, immaterial.
    result = analyze_endpoint("temp_f", _first_order_bins(70.0, 0.0, 1.0))
    assert result["analyzable"]
    assert result["settling_h"] == 0.25


def test_bin_grid_is_validated():
    bins = _first_order_bins(1.0, 0.2, 0.5)
    bins[3]["t_h"] = 0.9  # off-grid
    result = analyze_endpoint("vpd_kpa", bins)
    assert not result["analyzable"]
    assert not result["pass"]

    good = fit_first_order(T_H, [b["value"] for b in _first_order_bins(1.0, 0.2, 0.5)], None)
    assert good is not None


def test_endpoint_bands_are_frozen():
    assert ENDPOINT_BANDS == {"vpd_kpa": 0.025, "temp_f": 0.25, "duty_devmin": 6.75}
