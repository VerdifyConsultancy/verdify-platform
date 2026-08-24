from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

MODULE_DIR = Path(__file__).parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from switchback import v2_analysis as analysis
from switchback import v2_outcomes as outcomes
from switchback import v2_power as power
from switchback import v2_profiles as profiles


def test_all_profiles_have_exactly_48_source_grid_values_and_only_11_may_differ() -> None:
    artifact = profiles.build_profile_artifact(REPO_ROOT)
    built = {name: row["values"] for name, row in artifact["profiles"].items()}
    profiles.validate_profiles(built)
    assert {name: row["field_count"] for name, row in artifact["profiles"].items()} == {
        "baseline": 48,
        "moderate": 48,
        "aggressive": 48,
    }
    assert {name: row["wire_bytes"] for name, row in artifact["profiles"].items()} == {
        "baseline": 178,
        "moderate": 178,
        "aggressive": 178,
    }
    assert artifact["runtime_rounding_allowed"] is False
    assert artifact["source_evidence"]["running_device_entity_grid_verified"] is False
    assert all(set(grid) == {"min", "max", "step"} for grid in artifact["source_entity_grid"].values())
    assert artifact["source_entity_grid"]["dwell_gate_ms"] == {"min": 60_000, "max": 1_800_000, "step": 30_000}
    baseline = built["baseline"]
    for profile_id in ("moderate", "aggressive"):
        differences = {field for field in baseline if built[profile_id][field] != baseline[field]}
        assert differences <= profiles.TREATMENT_ALLOWLIST
    assert artifact["profiles"]["baseline"]["policy_state_content_sha256"] == (
        "6f823c63a56686da33bf258e7c2380a4c87292d431f09b4f79c81e71b285cf8e"
    )
    assert artifact["profiles"]["moderate"]["policy_state_content_sha256"] == (
        "816bee6d7557b2ea4dfbc8afe13184c415089bdabb13c1a0ac1f41f8099b1c73"
    )
    assert artifact["profiles"]["aggressive"]["policy_state_content_sha256"] == (
        "fa08c3e12d4951c77ac13c3584f48f010dfe48cee8385fadce612a938e8b0c1d"
    )


def test_profile_grid_rejects_values_above_exact_entity_max() -> None:
    artifact = profiles.build_profile_artifact(REPO_ROOT)
    built = {name: dict(row["values"]) for name, row in artifact["profiles"].items()}
    built["baseline"]["mister_pulse_on_s"] = 95
    with pytest.raises(ValueError, match="off-entity-grid"):
        profiles.validate_profiles(built)


def test_every_historical_off_grid_value_has_one_explicit_design_decision() -> None:
    artifact = profiles.build_profile_artifact(REPO_ROOT)
    decisions = {(row["profile_scope"], row["field"]): row for row in artifact["explicit_grid_design_decisions"]}
    assert set(decisions) == {
        ("all", "dwell_gate_ms"),
        ("all", "min_fog_on_s"),
        ("all", "mister_all_delay_s"),
        ("all", "mister_engage_delay_s"),
        ("all", "vpd_watch_dwell_s"),
        ("moderate", "mister_pulse_gap_s"),
    }
    assert decisions[("all", "dwell_gate_ms")]["to"] == 240_000
    assert decisions[("moderate", "mister_pulse_gap_s")]["to"] == 40


def _power_assumptions() -> power.PowerAssumptions:
    # Source scales are the conservative all-operational-day adjacent-pair
    # summaries checked into results-current-firmware-supplement-2026-08-14.
    # The selector mix/correlation/completeness are explicitly planning
    # assumptions pending the frozen provider replay and six-hour re-extract.
    return power.PowerAssumptions(
        paired_sd=(0.12198766625845635, 0.43532608025564046, 1062.9947787716267),
        true_nonbaseline_effect=(0.0, 0.0, -404.5981795560847),
        selector_replay=power.SelectorReplay(baseline=25, moderate=50, aggressive=20, fallback=5),
        correlation=((1.0, 0.45, -0.25), (0.45, 1.0, -0.30), (-0.25, -0.30, 1.0)),
        complete_pair_probability=0.9995,
    )


def test_fixed_predraw_power_quantifies_dilution_and_rejects_15_pairs() -> None:
    assumptions = _power_assumptions()
    assert assumptions.selector_replay.physical_nonbaseline_frequency == pytest.approx(0.70)
    assert assumptions.diluted_true_effect[2] == pytest.approx(-283.2187256892593)
    selection = power.choose_fixed_pairs(
        assumptions,
        candidates=(15, 150),
        repetitions=5_000,
        seed=588_639,
    )
    assert selection["chosen_pairs"] == 150
    assert selection["chosen_local_days"] == 300
    assert selection["no_adaptive_sample_size"] is True
    assert selection["evaluations"][0]["joint_advance_power"] < 0.80
    assert selection["evaluations"][1]["joint_advance_power"] >= 0.80


@pytest.mark.parametrize("field", ["baseline", "moderate", "aggressive", "fallback"])
@pytest.mark.parametrize("invalid", [-1, True, 1.0, "1"])
def test_selector_replay_requires_exact_nonnegative_integer_counts(field: str, invalid: object) -> None:
    values: dict[str, object] = {"baseline": 1, "moderate": 1, "aggressive": 1, "fallback": 1}
    values[field] = invalid
    with pytest.raises(ValueError, match="exact nonnegative integer"):
        power.SelectorReplay(**values)  # type: ignore[arg-type]


def test_selector_replay_fallback_flags_must_be_exact_booleans() -> None:
    with pytest.raises(ValueError, match="exact booleans"):
        power.summarize_selector_replay(["baseline"], [1])  # type: ignore[list-item]


def test_selector_replay_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="at least one context"):
        power.SelectorReplay(baseline=0, moderate=0, aggressive=0, fallback=0)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("paired_sd", (1.0, 2.0, "3")),
        ("paired_sd", (1.0, 2.0, float("inf"))),
        ("paired_sd", (1.0, 2.0, 10**1_000)),
        ("paired_sd", [1.0, 2.0, 3.0]),
        ("true_nonbaseline_effect", (0.0, True, -1.0)),
        ("true_nonbaseline_effect", (0.0, 0.0, float("nan"))),
        ("true_nonbaseline_effect", (0.0, 0.0)),
        ("correlation", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, float("nan")))),
        ("correlation", [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
        ("complete_pair_probability", float("nan")),
        ("complete_pair_probability", True),
        ("one_sided_confidence_level", "0.975"),
        ("minimum_joint_power", float("inf")),
        ("selector_replay", {"baseline": 1, "moderate": 1, "aggressive": 1, "fallback": 1}),
    ],
)
def test_power_assumptions_require_exact_typed_finite_values(field: str, invalid: object) -> None:
    assumptions = replace(_power_assumptions(), **{field: invalid})
    with pytest.raises((TypeError, ValueError)):
        assumptions.validate()


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("pairs", True),
        ("pairs", 2.0),
        ("pairs", 1),
        ("pairs", power.MAX_SIMULATION_PAIRS + 1),
        ("repetitions", True),
        ("repetitions", 1.0),
        ("repetitions", 0),
        ("repetitions", power.MAX_SIMULATION_REPETITIONS + 1),
        ("batch_size", True),
        ("batch_size", 1.0),
        ("batch_size", 0),
        ("batch_size", power.MAX_SIMULATION_BATCH_SIZE + 1),
        ("seed", True),
        ("seed", 1.0),
        ("seed", -1),
        ("seed", power.MAX_SIMULATION_SEED + 1),
    ],
)
def test_power_simulation_requires_exact_bounded_integer_inputs(field: str, invalid: object) -> None:
    values: dict[str, object] = {
        "pairs": 2,
        "repetitions": 1,
        "seed": 1,
        "batch_size": 1,
    }
    values[field] = invalid
    with pytest.raises(ValueError, match=f"{field} must be an exact integer"):
        power.simulate_joint_power(assumptions=_power_assumptions(), **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "candidates",
    [
        [15, 30],
        (15, True),
        (15, 30.0),
        (15, power.MAX_SIMULATION_PAIRS + 1),
        (15,) * (power.MAX_CANDIDATE_COUNTS + 1),
    ],
)
def test_fixed_pair_candidates_require_exact_bounded_integer_tuple(candidates: object) -> None:
    with pytest.raises(ValueError, match="candidate|candidates"):
        power.choose_fixed_pairs(
            _power_assumptions(),
            candidates=candidates,  # type: ignore[arg-type]
            repetitions=1,
            seed=1,
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("repetitions", True),
        ("repetitions", power.MAX_SIMULATION_REPETITIONS + 1),
        ("seed", True),
        ("seed", power.MAX_SIMULATION_SEED),
    ],
)
def test_fixed_pair_selection_requires_bounded_exact_simulation_inputs(field: str, invalid: object) -> None:
    values: dict[str, object] = {"candidates": (15, 30), "repetitions": 1, "seed": 1}
    values[field] = invalid
    with pytest.raises(ValueError, match=f"{field} must be an exact integer"):
        power.choose_fixed_pairs(_power_assumptions(), **values)  # type: ignore[arg-type]


def test_climate_72_bin_hand_calculation_and_missingness_rule() -> None:
    tz = ZoneInfo("America/Denver")
    start, _end = outcomes._local_window("2026-09-01", "America/Denver")
    corridor = outcomes.Corridor(70.0, 74.0, 0.8, 1.2)
    corridors = {start + timedelta(minutes=15 * index): corridor for index in range(72)}
    samples = [outcomes.ClimateSample(start + timedelta(minutes=minute), 75.0, 1.0) for minute in range(18 * 60)]
    bins = outcomes.climate_bins(
        samples,
        local_date="2026-09-01",
        timezone="America/Denver",
        corridors=corridors,
        temperature_duplicate_tolerance_f=0.1,
        vpd_duplicate_tolerance_kpa=0.01,
    )
    assert len(bins) == 72 and all(row.minute_slots == 15 for row in bins)
    temp, vpd, reason = outcomes.daily_climate_outcome(bins)
    assert temp == pytest.approx(1.0) and vpd == pytest.approx(0.0) and reason is None
    broken = list(bins)
    for index in range(3):
        broken[index] = outcomes.ClimateBin(broken[index].bucket_start, 0, None, None, "incomplete_bin")
    assert outcomes.daily_climate_outcome(broken) == (None, None, "climate_completeness")
    assert start.tzinfo == tz


def test_nonfinite_climate_inputs_and_bins_fail_closed() -> None:
    start, _end = outcomes._local_window("2026-09-01", "America/Denver")
    with pytest.raises(ValueError, match="duplicate tolerance must be finite"):
        outcomes.climate_bins(
            [],
            local_date="2026-09-01",
            timezone="America/Denver",
            corridors={},
            temperature_duplicate_tolerance_f=float("nan"),
            vpd_duplicate_tolerance_kpa=0.01,
        )
    with pytest.raises(ValueError, match="corridor low must be finite"):
        outcomes.climate_bins(
            [],
            local_date="2026-09-01",
            timezone="America/Denver",
            corridors={start: outcomes.Corridor(float("nan"), 74.0, 0.8, 1.2)},
            temperature_duplicate_tolerance_f=0.1,
            vpd_duplicate_tolerance_kpa=0.01,
        )
    with pytest.raises(ValueError, match="corridor low exceeds high"):
        outcomes.climate_bins(
            [],
            local_date="2026-09-01",
            timezone="America/Denver",
            corridors={start: outcomes.Corridor(75.0, 74.0, 0.8, 1.2)},
            temperature_duplicate_tolerance_f=0.1,
            vpd_duplicate_tolerance_kpa=0.01,
        )
    nonfinite_bins = [
        outcomes.ClimateBin(start + timedelta(minutes=15 * index), 15, float("nan"), 0.0) for index in range(72)
    ]
    assert outcomes.daily_climate_outcome(nonfinite_bins) == (None, None, "climate_completeness")

    duplicate_grid = [outcomes.ClimateBin(start, 15, 1.0, 2.0) for _index in range(72)]
    with pytest.raises(ValueError, match="exact ordered 15-minute grid"):
        outcomes.daily_climate_outcome(duplicate_grid)

    invalid_rows = [
        outcomes.ClimateBin(
            start + timedelta(minutes=15 * index),
            0,
            -1.0,
            -2.0,
            "conflicting_duplicate_timestamp",
        )
        for index in range(72)
    ]
    assert outcomes.daily_climate_outcome(invalid_rows) == (None, None, "climate_completeness")


def test_nine_stream_right_continuous_seed_counter_and_vent_open_semantics() -> None:
    start, end = outcomes._local_window("2026-09-01", "America/Denver")
    states = [
        outcomes.StateObservation(start - timedelta(minutes=1), False),
        outcomes.StateObservation(start, True),
        outcomes.StateObservation(start + timedelta(hours=1), False),
    ]
    stream_results = []
    for stream in outcomes.EQUIPMENT_STREAMS:
        result = outcomes.equipment_stream_outcome(
            stream,
            states,
            local_date="2026-09-01",
            timezone="America/Denver",
            start_counter=outcomes.CounterSample(start - timedelta(minutes=1), 0.0, "boot-1"),
            end_counter=outcomes.CounterSample(end - timedelta(minutes=1), 60.0, "boot-1"),
        )
        assert result.valid and result.active_or_open_minutes == pytest.approx(60.0)
        stream_results.append(result)
    burden, reason = outcomes.nine_stream_burden(stream_results)
    assert burden == pytest.approx(540.0) and reason is None
    # `vent=true` contributes open-state minutes exactly like the eight relay
    # active streams; this endpoint makes no motor-energy claim.
    assert stream_results[outcomes.EQUIPMENT_STREAMS.index("vent")].active_or_open_minutes == 60.0


def test_duplicate_conflict_and_counter_reset_fail_closed() -> None:
    start, end = outcomes._local_window("2026-09-01", "America/Denver")
    conflict = [
        outcomes.StateObservation(start - timedelta(minutes=1), False),
        outcomes.StateObservation(start, True),
        outcomes.StateObservation(start, False),
    ]
    conflict_result = outcomes.equipment_stream_outcome(
        "heat1",
        conflict,
        local_date="2026-09-01",
        timezone="America/Denver",
        start_counter=outcomes.CounterSample(start - timedelta(minutes=1), 0.0, "boot-1"),
        end_counter=outcomes.CounterSample(end - timedelta(minutes=1), 0.0, "boot-1"),
    )
    assert not conflict_result.valid and conflict_result.reason == "conflicting_same_timestamp_state"
    clean = [outcomes.StateObservation(start - timedelta(minutes=1), False)]
    reset_result = outcomes.equipment_stream_outcome(
        "heat1",
        clean,
        local_date="2026-09-01",
        timezone="America/Denver",
        start_counter=outcomes.CounterSample(start - timedelta(minutes=1), 0.0, "boot-1"),
        end_counter=outcomes.CounterSample(end - timedelta(minutes=1), 0.0, "boot-2"),
    )
    assert not reset_result.valid and reset_result.reason == "counter_reset_or_wrap"


@pytest.mark.parametrize("reset_epoch", [None, "", True, "boot-e\N{COMBINING ACUTE ACCENT}"])
def test_counter_reset_epoch_requires_exact_nonempty_nfc_string(reset_epoch: object) -> None:
    start, end = outcomes._local_window("2026-09-01", "America/Denver")
    result = outcomes.equipment_stream_outcome(
        "heat1",
        [outcomes.StateObservation(start - timedelta(minutes=1), False)],
        local_date="2026-09-01",
        timezone="America/Denver",
        start_counter=outcomes.CounterSample(
            start - timedelta(minutes=1),
            0.0,
            reset_epoch,  # type: ignore[arg-type]
        ),
        end_counter=outcomes.CounterSample(
            end - timedelta(minutes=1),
            0.0,
            reset_epoch,  # type: ignore[arg-type]
        ),
    )
    assert not result.valid and result.reason == "invalid_counter_reset_epoch"


def test_equipment_state_type_and_duplicate_streams_fail_closed() -> None:
    start, end = outcomes._local_window("2026-09-01", "America/Denver")
    invalid_state = outcomes.equipment_stream_outcome(
        "heat1",
        [outcomes.StateObservation(start - timedelta(minutes=1), 1)],  # type: ignore[arg-type]
        local_date="2026-09-01",
        timezone="America/Denver",
        start_counter=outcomes.CounterSample(start - timedelta(minutes=1), 0.0, "boot-1"),
        end_counter=outcomes.CounterSample(end - timedelta(minutes=1), 0.0, "boot-1"),
    )
    assert not invalid_state.valid and invalid_state.reason == "invalid_state_type"

    valid_rows = [outcomes.StreamOutcome(name, 0.0, True, None) for name in outcomes.EQUIPMENT_STREAMS]
    duplicate = [*valid_rows, outcomes.StreamOutcome("heat1", 1.0, True, None)]
    assert outcomes.nine_stream_burden(duplicate) == (None, "missing_or_extra_equipment_stream")

    invalid_counter = outcomes.equipment_stream_outcome(
        "heat1",
        [outcomes.StateObservation(start - timedelta(minutes=1), False)],
        local_date="2026-09-01",
        timezone="America/Denver",
        start_counter=outcomes.CounterSample(start - timedelta(minutes=1), float("nan"), "boot-1"),
        end_counter=outcomes.CounterSample(end - timedelta(minutes=1), 0.0, "boot-1"),
    )
    assert not invalid_counter.valid and invalid_counter.reason == "invalid_counter_value"

    for malformed in (None, -1.0, float("nan"), 1_081.0, True):
        rows = [outcomes.StreamOutcome(name, 0.0, True, None) for name in outcomes.EQUIPMENT_STREAMS]
        rows[0] = outcomes.StreamOutcome("heat1", malformed, True, None)  # type: ignore[arg-type]
        burden, reason = outcomes.nine_stream_burden(rows)
        assert burden is None and reason == "invalid_streams:heat1"

    rows = [outcomes.StreamOutcome(name, 0.0, True, None) for name in outcomes.EQUIPMENT_STREAMS]
    rows[0] = outcomes.StreamOutcome("heat1", 0.0, 1, None)  # type: ignore[arg-type]
    assert outcomes.nine_stream_burden(rows) == (None, "invalid_streams:heat1")


def test_primary_itt_row_never_filters_on_exposure_or_fallback() -> None:
    row = outcomes.make_randomized_itt_row(
        assignment_id="assignment-1",
        local_date="2026-09-01",
        blinded_label="X",
        climate=(1.0, 0.1, None),
        equipment=(540.0, None),
        fallback_or_rescue=True,
        exposure_seconds=0,
    )
    assert row.outcome_complete
    assert row.fallback_or_rescue and row.exposure_seconds == 0
    assert not row.per_protocol_exposure_complete
    missing = outcomes.make_randomized_itt_row(
        assignment_id="assignment-2",
        local_date="2026-09-02",
        blinded_label="Y",
        climate=(None, None, "climate_completeness"),
        equipment=(None, "counter_reset"),
        fallback_or_rescue=False,
        exposure_seconds=0,
    )
    assert not missing.outcome_complete and missing.missing_reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"assignment_id": ""},
        {"local_date": "2026-9-1"},
        {"blinded_label": "A"},
        {"climate": (float("nan"), 0.1, None)},
        {"equipment": (-1.0, None)},
        {"fallback_or_rescue": 1},
        {"exposure_seconds": -1},
        {"exposure_seconds": outcomes.ANALYZED_SECONDS + 1},
    ],
)
def test_randomized_itt_row_rejects_malformed_runtime_values(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "assignment_id": "assignment-1",
        "local_date": "2026-09-01",
        "blinded_label": "X",
        "climate": (1.0, 0.1, None),
        "equipment": (540.0, None),
        "fallback_or_rescue": False,
        "exposure_seconds": 0,
    }
    values.update(overrides)
    with pytest.raises((TypeError, ValueError)):
        outcomes.make_randomized_itt_row(**values)  # type: ignore[arg-type]


def test_phase_kind_work_pairings_are_exact() -> None:
    outcomes.validate_phase_kind_work("aa_rehearsal", "aa_baseline_rehearsal", "readiness_operation")
    for pairing in outcomes.VALID_PHASE_KIND_WORK:
        outcomes.validate_phase_kind_work(*pairing)
    for illegal in (
        ("shadow", "randomized_assignment", "assignment"),
        ("commissioning", "randomized_assignment", "assignment"),
        ("aa_rehearsal", "commissioning_canary", "readiness_operation"),
        ("aa_rehearsal", "aa_rehearsal", "readiness_operation"),
        ("randomized", "commissioning_canary", "assignment"),
    ):
        with pytest.raises(ValueError, match="illegal"):
            outcomes.validate_phase_kind_work(*illegal)


def test_power_source_manifest_hash_is_current() -> None:
    path = REPO_ROOT / "research/planner-efficacy/results-current-firmware-supplement-2026-08-14.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest()


def test_checked_in_generated_profiles_and_provisional_power_are_source_bound() -> None:
    profile_path = REPO_ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
    checked_profiles = json.loads(profile_path.read_text())
    assert checked_profiles == profiles.build_profile_artifact(REPO_ROOT)
    power_path = REPO_ROOT / "research/planner-efficacy/protocols/planner-switchback-v2-power.json"
    checked_power = json.loads(power_path.read_text())
    assert checked_power["status"].startswith("PROVISIONAL PRE-DRAW")
    assert checked_power["selection"]["chosen_pairs"] == 150
    assert checked_power["selection"]["evaluations"][0]["pairs"] == 15
    assert checked_power["selection"]["evaluations"][0]["joint_advance_power"] < 0.80
    assert checked_power["selection"]["evaluations"][-1]["joint_advance_power"] >= 0.80
    claimed_hash = checked_power.pop("artifact_sha256")
    assert power.artifact_sha256(checked_power) == claimed_hash
    for relative, expected in checked_power["source_files_sha256"].items():
        assert hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest() == expected


def test_checked_in_analyzer_golden_matches_frozen_interface() -> None:
    path = REPO_ROOT / "research/planner-efficacy/protocols/analyzer-interface-v2.golden.json"
    golden = json.loads(path.read_text())
    assert golden["expected"] == analysis.paired_upper_bound(
        golden["input"]["pair_contrasts"], golden["input"]["boundary"]
    )


def test_frozen_analyzer_hand_calculation_and_null_pair_inconclusive() -> None:
    contrasts = [-3.0, -2.0, -1.0]
    summary = analysis.paired_upper_bound(contrasts, boundary=0.0)
    # Hand calculation: mean=-2, sample SD=1, SE=1/sqrt(3), t(.975,2)=4.30265.
    assert summary["mean"] == pytest.approx(-2.0)
    assert summary["sample_sd"] == pytest.approx(1.0)
    assert summary["standard_error"] == pytest.approx(1 / 3**0.5)
    assert summary["upper_bound"] == pytest.approx(-2 + 4.302652729696142 / 3**0.5)
    rows = [
        analysis.PairContrast(
            index,
            {
                "vpd_corridor_distance_kpa": -0.02,
                "temperature_corridor_distance_f": -0.10,
                "nine_control_state_minutes": value - 28.0,
            },
        )
        for index, value in enumerate(contrasts)
    ]
    assert analysis.analyze_frozen_pairs(rows, locked_pairs=3)["decision"] == "advance"
    rows[1] = analysis.PairContrast(1, {**rows[1].values, "nine_control_state_minutes": None})
    report = analysis.analyze_frozen_pairs(rows, locked_pairs=3)
    assert report["decision"] == "inconclusive_null_endpoint"
    assert report["no_pair_replacement"] is True
    assert analysis.frozen_interface_manifest()["exposure_filter"] == "forbidden"
