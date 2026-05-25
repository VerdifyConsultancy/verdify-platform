from __future__ import annotations

from pathlib import Path

from scripts.climate_intent_shadow_evaluator import (
    ReplayClimateRow,
    evaluate_rows,
    evaluate_shadow_row,
    read_replay_rows,
    summarize,
)


def _row(**overrides) -> ReplayClimateRow:
    base = {
        "ts": "2026-05-24 18:00:00+00",
        "temp_f": 76.0,
        "vpd_kpa": 1.25,
        "rh_pct": 50.0,
        "dew_point_f": 55.0,
        "outdoor_temp_f": 70.0,
        "outdoor_dewpoint_f": 35.0,
        "solar_w_m2": 650.0,
        "occupied": False,
        "greenhouse_state": "VENTILATE",
        "mode_reason": "temp_high",
        "temp_low": 67.0,
        "temp_high": 73.0,
        "vpd_low": 0.7,
        "vpd_high": 1.05,
        "safety_min": 40.0,
        "safety_max": 95.0,
        "vpd_min_safe": 0.35,
        "vpd_max_safe": 2.8,
        "fog_escalation_kpa": 0.35,
        "eq_fog": False,
        "eq_vent": True,
        "eq_fan1": True,
        "eq_fan2": True,
        "eq_heat1": False,
        "eq_heat2": False,
        "eq_mister_south": False,
        "eq_mister_west": False,
        "eq_mister_center": False,
    }
    base.update(overrides)
    return ReplayClimateRow(**base)


def test_shadow_prefers_temp_compliance_over_sealed_vpd_recovery() -> None:
    evaluation = evaluate_shadow_row(_row())

    assert evaluation.decision.priority_axis == "temp"
    assert evaluation.decision.climate_action == "VENT_COOL_MIST_ASSIST"
    assert "SEALED_HUMIDIFY" in {candidate.action for candidate in evaluation.candidates}


def test_shadow_blocks_wet_actions_when_occupied() -> None:
    evaluation = evaluate_shadow_row(_row(occupied=True))

    assert evaluation.decision.climate_action == "VENT_COOL"
    assert evaluation.decision.moisture_assist_state == "blocked"
    assert "occupancy" in evaluation.decision.fog_block_reason


def test_wet_cutoff_uses_greenhouse_local_time() -> None:
    before_cutoff = evaluate_shadow_row(_row(ts="2026-05-24 18:00:00+00"))
    after_cutoff = evaluate_shadow_row(_row(ts="2026-05-25 02:00:00+00"))

    assert before_cutoff.decision.climate_action == "VENT_COOL_MIST_ASSIST"
    assert "time_window" not in before_cutoff.decision.fog_block_reason
    assert after_cutoff.decision.climate_action == "VENT_COOL"
    assert "time_window" in after_cutoff.decision.fog_block_reason


def test_shadow_uses_safety_before_band_compliance() -> None:
    evaluation = evaluate_shadow_row(_row(temp_f=97.0, safety_max=95.0, vpd_kpa=1.5))

    assert evaluation.decision.priority_axis == "safety"
    assert evaluation.decision.climate_action == "SAFETY_COOL"


def test_replay_reader_and_summary_use_existing_corpus_shape(tmp_path: Path) -> None:
    corpus = tmp_path / "replay.tsv"
    corpus.write_text(
        "\t".join(
            (
                "ts",
                "temp_avg",
                "vpd_avg",
                "rh_avg",
                "indoor_dew_point",
                "outdoor_temp_f",
                "outdoor_dewpoint_f",
                "solar_irradiance_w_m2",
                "sp_temp_low",
                "sp_temp_high",
                "sp_vpd_low",
                "sp_vpd_high",
                "sp_safety_min",
                "sp_safety_max",
                "sp_vpd_min_safe",
                "sp_vpd_max_safe",
                "sp_fog_escalation_kpa",
                "occupied",
                "greenhouse_state",
                "mode_reason",
                "eq_fog",
                "eq_vent",
                "eq_fan1",
                "eq_fan2",
                "eq_heat1",
                "eq_heat2",
                "eq_mister_south",
                "eq_mister_west",
                "eq_mister_center",
            )
        )
        + "\n"
        + "\t".join(
            (
                "2026-05-24 18:00:00+00",
                "76.0",
                "1.25",
                "50.0",
                "55.0",
                "70.0",
                "35.0",
                "650.0",
                "67.0",
                "73.0",
                "0.7",
                "1.05",
                "40.0",
                "95.0",
                "0.35",
                "2.8",
                "0.35",
                "f",
                "VENTILATE",
                "temp_high",
                "0",
                "1",
                "1",
                "1",
                "0",
                "0",
                "0",
                "0",
                "0",
            )
        )
        + "\n"
    )

    rows = list(read_replay_rows(corpus))
    summary = summarize(evaluate_rows(rows))

    assert len(rows) == 1
    assert summary["rows"] == 1
    assert summary["shadow_action_counts"] == {"VENT_COOL_MIST_ASSIST": 1}
    assert summary["firmware_state_counts"] == {"VENTILATE": 1}
