"""Qualification spec template <-> code constant agreement (#584/#588).

The §8.3 specification instance encodes the eligibility predicates, scheduler
intervals, regime thresholds, and analyzer thresholds. The worker and the
frozen analyzer hold the same numbers as module constants; this test pins the
template and the code to each other so neither can drift silently. A change
to any of these values is a new spec version (§8.3: revision changes void the
qualification).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT, REPO_ROOT / "ingestor", REPO_ROOT / "research" / "planner-efficacy"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from qualification import settling  # noqa: E402
from tasks import experiment_qualification as worker  # noqa: E402

from verdify_schemas import experiment_regimes as regimes  # noqa: E402

TEMPLATE = yaml.safe_load(
    (REPO_ROOT / "research" / "planner-efficacy" / "qualification" / "qualification-spec-v1.template.yaml").read_text(
        encoding="utf-8"
    )
)


def test_spec_name_and_window():
    assert TEMPLATE["spec"]["name"] == worker.QUALIFICATION_SPEC_VERSION
    assert TEMPLATE["spec"]["window_local_days"] == worker.WINDOW_LOCAL_DAYS == 45


def test_regime_thresholds_match_classifier():
    thresholds = TEMPLATE["regimes"]["thresholds"]
    assert thresholds["night_solar_max_w_m2"] == regimes.NIGHT_SOLAR_MAX_W_M2
    assert thresholds["hot_bright_solar_min_w_m2"] == regimes.HOT_BRIGHT_SOLAR_MIN_W_M2
    assert thresholds["hot_bright_temp_min_f"] == regimes.HOT_BRIGHT_TEMP_MIN_F
    assert thresholds["humidity_ratio_split_kg_kg"] == regimes.HUMID_RATIO_SPLIT_KG_KG
    pressure = TEMPLATE["regimes"]["psychrometrics"]["station_pressure_kpa_fallback"]
    assert pressure == regimes.DEFAULT_STATION_PRESSURE_KPA
    codes = TEMPLATE["regimes"]["codes"]
    assert list(codes) == [r.value for r in regimes.REGIMES]
    assert [codes[r.value] for r in regimes.REGIMES] == [0, 1, 2, 3]


def test_cell_layout_matches_locked_orderings():
    cells = TEMPLATE["cells"]
    assert cells["slots_per_cell"] == 4
    assert [tuple(edge) for edge in cells["edges"]] == [(a.value, b.value) for a, b in regimes.EDGES]
    assert cells["regime_order"] == [r.value for r in regimes.REGIMES]


def test_scheduler_intervals_match_worker():
    intervals = TEMPLATE["scheduler"]["intervals"]
    assert intervals["positioning_hours"] == worker.POSITIONING_HOURS
    assert intervals["baseline_recovery_hours"] == worker.RECOVERY_HOURS
    assert intervals["analyzed_hours"] == worker.ANALYZED_HOURS
    assert intervals["identity_hold_minutes"] == worker.IDENTITY_HOLD_MINUTES
    assert TEMPLATE["scheduler"]["boundary_backdate_grace_s"] == worker.BOUNDARY_BACKDATE_GRACE_S
    assert TEMPLATE["scheduler"]["worker"] == worker.SCHEDULER_REF


def test_eligibility_predicates_match_worker():
    predicates = TEMPLATE["scheduler"]["eligibility_predicates"]
    assert predicates["inputs_fresh_max_age_s"] == worker.INPUT_FRESHNESS_S
    assert predicates["override_lookback_minutes"] == worker.OVERRIDE_LOOKBACK_MINUTES
    assert predicates["pretrace_minutes"] == worker.PRETRACE_MINUTES
    assert predicates["pretrace_max_snapshot_gap_s"] == worker.PRETRACE_MAX_GAP_S
    failure_rules = TEMPLATE["scheduler"]["analyzed_failure_rules"]
    assert failure_rules["delivery_confirm_grace_s"] == worker.DELIVERY_CONFIRM_GRACE_S
    assert failure_rules["post_step_max_snapshot_gap_s"] == worker.POST_STEP_MAX_GAP_S


def test_producer_is_declared_in_spec_and_migration():
    assert worker.PRODUCER in TEMPLATE["permitted_producers"]


def test_analyzer_thresholds_match_settling():
    analyzer = TEMPLATE["analyzer"]
    assert analyzer["name"] == settling.ANALYZER_VERSION
    assert analyzer["post_step_bins"] == settling.POST_STEP_BINS
    assert analyzer["bin_hours"] == settling.BIN_HOURS
    assert analyzer["endpoint_bands"] == settling.ENDPOINT_BANDS
    gate = analyzer["gate"]
    assert gate["max_settling_h"] == settling.GATE_MAX_SETTLING_H
    assert gate["identity_confirm_max_s"] == settling.IDENTITY_CONFIRM_MAX_S
    assert gate["expected_transitions"] == settling.EXPECTED_TRANSITIONS
    fit = analyzer["fit"]
    assert fit["tau_grid_min_h"] == settling.TAU_GRID_MIN_H
    assert fit["tau_grid_max_h"] == settling.TAU_GRID_MAX_H
    assert fit["tau_grid_points"] == settling.TAU_GRID_POINTS
    assert fit["tau_refine_iterations"] == settling.TAU_REFINE_ITERATIONS
    assert fit["tau_edge_pin_fraction"] == settling.TAU_EDGE_PIN_FRACTION
    diagnostics = analyzer["diagnostics"]
    assert diagnostics["min_r_squared"] == settling.DIAG_MIN_R_SQUARED
    assert diagnostics["max_resid_lag1_autocorr"] == settling.DIAG_MAX_RESID_LAG1_AUTOCORR
    assert diagnostics["max_asymptote_ci_band_ratio"] == settling.DIAG_MAX_ASYMPTOTE_CI_BAND_RATIO


def test_revision_pins_are_marked_to_lock():
    # A template must never ship pre-resolved revisions.
    for key, value in TEMPLATE["revisions"].items():
        assert value == "TO-LOCK", f"revisions.{key} must stay TO-LOCK in the template"
    for key, value in TEMPLATE["templates"].items():
        assert value == "TO-LOCK", f"templates.{key} must stay TO-LOCK in the template"
