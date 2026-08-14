#!/usr/bin/env python3
"""Current-firmware planner mechanism and stale-policy analysis.

This supplement deliberately does not turn an operational interruption into a
causal estimate. It reproduces a tightly matched hypothesis-generating contrast,
then audits forecast response, waypoint survival, and effective tunable posture.
Raw operational inputs remain outside Git; only the aggregate JSON is committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

# isort: split

import audit

FIRMWARE_VERSION = "2026.7.10.1500.09ee886"
FIRMWARE_EPOCH_START = "2026-07-10T21:03:12.991915+00:00"
OUTCOME_END = "2026-08-14T06:00:00+00:00"
DENVER = ZoneInfo("America/Denver")

STALE_DAYS = {date(2026, 8, day) for day in range(6, 10)}
EXCLUDED_CONTROL_DAYS = STALE_DAYS | {
    date(2026, 7, 25),  # same-build panic/reboot and transient defaults
    date(2026, 8, 5),  # delivery-failure onset
    date(2026, 8, 10),  # dispatcher delivery recovery transition
    date(2026, 8, 11),  # fresh AI delivery recovery transition
}
CONTROL_START = date(2026, 7, 11)
CONTROL_END = date(2026, 8, 14)
MATCH_FEATURES = ("outdoor_temp_f", "outdoor_rh_pct", "solar_w_m2", "wind_mph")
SIX_CORE = ("heat1", "heat2", "vent", "fan1", "fan2", "fog")
WET_EQUIPMENT = ("fog", "mister_south", "mister_west", "mister_center")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_float(row: dict[str, str], key: str) -> float:
    value = audit.maybe_float(row.get(key))
    if not math.isfinite(value):
        raise ValueError(f"{key} is missing in a required aggregate row")
    return value


def json_number(value: float) -> float:
    return float(value)


def partial_correlation(x: np.ndarray, y: np.ndarray, controls: np.ndarray) -> float:
    design = np.column_stack((np.ones(len(x)), controls))
    x_residual = x - design @ np.linalg.lstsq(design, x, rcond=None)[0]
    y_residual = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    if np.std(x_residual) == 0 or np.std(y_residual) == 0:
        return math.nan
    return float(np.corrcoef(x_residual, y_residual)[0, 1])


def nearest_same_slot_pairs(
    features: np.ndarray,
    local_days: np.ndarray,
    local_slots: np.ndarray,
    stale_indices: np.ndarray,
    control_start: date,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    in_control_window = (local_days >= control_start) & (local_days < CONTROL_END)
    control_indices = np.flatnonzero(in_control_window & ~np.isin(local_days, list(EXCLUDED_CONTROL_DAYS)))
    control_mean = np.mean(features[control_indices], axis=0)
    control_sd = np.std(features[control_indices], axis=0, ddof=1)
    standardized = (features - control_mean) / control_sd

    pairs: list[tuple[int, int, float]] = []
    for stale_index in stale_indices:
        candidates = control_indices[local_slots[control_indices] == local_slots[stale_index]]
        squared_distance = np.sum((standardized[candidates] - standardized[stale_index]) ** 2, axis=1)
        matched_index = int(candidates[int(np.argmin(squared_distance))])
        axis_distance = np.abs(standardized[matched_index] - standardized[stale_index])
        # Preserve the declared nearest-first-then-caliper algorithm. Filtering
        # to the caliper first would be a different matching specification.
        if float(np.max(axis_distance)) <= 0.35:
            pairs.append((int(stale_index), matched_index, float(math.sqrt(np.min(squared_distance)))))
    return (
        control_indices,
        control_mean,
        control_sd,
        np.asarray([item[0] for item in pairs], dtype=int),
        np.asarray([item[1] for item in pairs], dtype=int),
        np.asarray([item[2] for item in pairs], dtype=float),
    )


def stale_policy_match(climate_path: Path, equipment_path: Path) -> dict[str, Any]:
    raw_rows = read_rows(climate_path)
    telemetry = audit.load_climate(climate_path)
    audit.attach_equipment_duty(telemetry, equipment_path)

    local_days = np.asarray([when.astimezone(DENVER).date() for when in telemetry.times], dtype=object)
    local_slots = np.asarray(
        [when.astimezone(DENVER).hour * 4 + when.astimezone(DENVER).minute // 15 for when in telemetry.times]
    )
    in_epoch = (local_days >= CONTROL_START) & (local_days < CONTROL_END)
    stale_indices = np.flatnonzero(in_epoch & np.isin(local_days, list(STALE_DAYS)))
    features = np.column_stack([telemetry.columns[name] for name in MATCH_FEATURES])
    control_indices, control_mean, control_sd, stale_matched, controls_matched, match_distances = (
        nearest_same_slot_pairs(features, local_days, local_slots, stale_indices, CONTROL_START)
    )
    if len(stale_indices) != 384 or len(control_indices) != 2496:
        raise ValueError(
            f"unexpected stale/control bins: {len(stale_indices)}/{len(control_indices)}; "
            "the fixed study snapshot requires 384/2496"
        )
    if len(stale_matched) != 93:
        raise ValueError(f"unexpected retained matched pairs: {len(stale_matched)}; expected 93")
    reuse = Counter(controls_matched.tolist())
    if len(reuse) != 85 or max(reuse.values()) != 2:
        raise ValueError("matched-control reuse changed from the declared 85 unique / maximum reuse 2")

    raw_complete = np.asarray(
        [all(math.isfinite(audit.maybe_float(row.get(name))) for name in MATCH_FEATURES) for row in raw_rows],
        dtype=bool,
    )
    if not raw_complete[stale_matched].all() or not raw_complete[controls_matched].all():
        raise ValueError("a selected match uses an imputed weather feature")

    temp_distance = np.maximum(telemetry.columns["eval_temp_low_f"] - telemetry.columns["temp_f"], 0.0) + np.maximum(
        telemetry.columns["temp_f"] - telemetry.columns["eval_temp_high_f"], 0.0
    )
    vpd_distance = np.maximum(telemetry.columns["eval_vpd_low_kpa"] - telemetry.columns["vpd_kpa"], 0.0) + np.maximum(
        telemetry.columns["vpd_kpa"] - telemetry.columns["eval_vpd_high_kpa"], 0.0
    )

    def device_minutes(equipment: tuple[str, ...]) -> np.ndarray:
        return 15.0 * sum(telemetry.actions[name] for name in equipment)

    outcomes = {
        "temp_distance_outside_corridor_f": temp_distance,
        "vpd_distance_outside_corridor_kpa": vpd_distance,
        "six_core_device_minutes_per_bin": device_minutes(SIX_CORE),
        "nine_climate_device_minutes_per_bin": device_minutes(audit.CLIMATE_EQUIPMENT),
        "wet_device_minutes_per_bin": device_minutes(WET_EQUIPMENT),
    }

    def contrast_rows(stale: np.ndarray, matched: np.ndarray, include_days: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, values in outcomes.items():
            stale_mean = float(np.mean(values[stale]))
            matched_mean = float(np.mean(values[matched]))
            difference = stale_mean - matched_mean
            row: dict[str, Any] = {
                "stale_mean": stale_mean,
                "matched_fresh_mean": matched_mean,
                "stale_minus_matched_fresh": difference,
                "stale_pct_above_matched_fresh": 100.0 * difference / matched_mean if matched_mean else None,
                "matched_fresh_pct_below_stale": 100.0 * difference / stale_mean if stale_mean else None,
            }
            if include_days:
                row["daily_stale_minus_matched_fresh"] = {
                    day.isoformat(): float(
                        np.mean(values[stale[local_days[stale] == day]] - values[matched[local_days[stale] == day]])
                    )
                    for day in sorted(STALE_DAYS)
                }
            result[name] = row
        return result

    contrasts = contrast_rows(stale_matched, controls_matched, include_days=True)
    sensitivity: dict[str, Any] = {}
    for label, control_start in (
        ("post_service_roll_from_2026_07_12", date(2026, 7, 12)),
        ("forecast_archive_from_2026_07_15", date(2026, 7, 15)),
    ):
        _, _, _, stale_sensitivity, control_sensitivity, _ = nearest_same_slot_pairs(
            features, local_days, local_slots, stale_indices, control_start
        )
        sensitivity[label] = {
            "control_start": control_start.isoformat(),
            "retained_pairs": len(stale_sensitivity),
            "contrasts": contrast_rows(stale_sensitivity, control_sensitivity, include_days=False),
        }

    balance = {
        name: {
            "control_pool_mean": json_number(control_mean[index]),
            "control_pool_sample_sd": json_number(control_sd[index]),
            "post_match_smd": json_number(
                (np.mean(features[stale_matched, index]) - np.mean(features[controls_matched, index]))
                / control_sd[index]
            ),
        }
        for index, name in enumerate(MATCH_FEATURES)
    }
    per_day_retained = {day.isoformat(): int(np.sum(local_days[stale_matched] == day)) for day in sorted(STALE_DAYS)}
    return {
        "label": "fresh adaptation versus stale last-confirmed policy during a shared delivery interruption",
        "causal_status": "hypothesis-generating only; not randomized and not an AI-versus-Frozen-FSM estimate",
        "stale_days": [day.isoformat() for day in sorted(STALE_DAYS)],
        "excluded_transition_days": [day.isoformat() for day in sorted(EXCLUDED_CONTROL_DAYS - STALE_DAYS)],
        "candidate_stale_bins": len(stale_indices),
        "candidate_control_bins": len(control_indices),
        "retained_pairs": len(stale_matched),
        "retained_hours": len(stale_matched) * 0.25,
        "retention_pct": 100.0 * len(stale_matched) / len(stale_indices),
        "retained_by_stale_day": per_day_retained,
        "unique_control_bins": len(reuse),
        "maximum_control_reuse": max(reuse.values()),
        "caliper_max_abs_control_sd": 0.35,
        "match_distance": {
            "median": float(np.median(match_distances)),
            "p95": float(np.quantile(match_distances, 0.95)),
            "max": float(np.max(match_distances)),
        },
        "balance": balance,
        "selected_weather_features_all_raw_measured": True,
        "contrasts": contrasts,
        "control_window_sensitivity": sensitivity,
        "inference_warning": (
            "Only four sequential stale-policy days contribute. No minute-level p-value or causal savings "
            "claim is valid; weather support retains only 24.2% of stale bins and policy carryover is unresolved."
        ),
    }


def forecast_response(path: Path) -> dict[str, Any]:
    rows = read_rows(path)
    if len(rows) != 70:
        raise ValueError(f"unexpected forecast-response rows: {len(rows)}; expected 70")
    forecast_features = ("forecast_temp_max_f", "forecast_vpd_max_kpa", "forecast_solar_max_w_m2")
    controls = (
        "current_temp_f",
        "current_vpd_kpa",
        "current_solar_w_m2",
        "current_outdoor_temp_f",
        "local_hour",
    )
    tunables = (
        "cool_stage2_over_high_f",
        "all_fans_enabled",
        "fog_escalation_kpa",
        "mister_engage_kpa",
        "mister_all_kpa",
        "mister_all_delay_s",
        "mister_pulse_gap_s",
        "mister_pulse_on_s",
        "mister_water_budget_gal",
        "min_fog_on_s",
        "resource_sensitivity",
        "relay_churn_penalty",
    )

    arrays = {
        name: np.asarray([as_float(row, name) for row in rows], dtype=float)
        for name in forecast_features + controls + tunables
    }
    control_matrix = np.column_stack([arrays[name] for name in controls])
    correlations: dict[str, Any] = {}
    for tunable in tunables:
        correlations[tunable] = {}
        for forecast_name in forecast_features:
            correlations[tunable][forecast_name] = {
                "pearson": float(np.corrcoef(arrays[forecast_name], arrays[tunable])[0, 1]),
                "partial_pearson_controlling_current_state_and_local_hour": partial_correlation(
                    arrays[forecast_name], arrays[tunable], control_matrix
                ),
            }
    return {
        "plans_with_at_least_20_as_of_forecast_hours": len(rows),
        "forecast_vintages_as_of_plan_creation": True,
        "correlations": correlations,
        "interpretation": (
            "Descriptive mechanism evidence: planned posture varies with the as-of 24-hour forecast, including "
            "after linear adjustment for current indoor/outdoor state, solar, and local hour. It is not an "
            "outcome-benefit or causal estimate."
        ),
    }


def waypoint_summary(path: Path) -> dict[str, Any]:
    rows = read_rows(path)
    plans: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mismatch: list[float] = []
    thermal_positive = 0
    forecast_temp_negative = 0
    forecast_vpd_negative = 0
    forecast_vpd_floor = 0
    night_bias_zero = 0
    for row in rows:
        intent = json.loads(row["climate_intent"])
        materialized = json.loads(row["materialized_params"])
        plans[row["plan_id"]].append(materialized)
        thermal_positive += float(intent["thermal_lead_time_min"]) > 0
        forecast_temp_negative += float(intent["forecast_temp_bias_f"]) < 0
        forecast_vpd = float(intent["forecast_vpd_bias_kpa"])
        forecast_vpd_negative += forecast_vpd < 0
        forecast_vpd_floor += math.isclose(forecast_vpd, -0.4)
        night_bias_zero += math.isclose(float(materialized["night_vpd_bias_kpa"]), 0.0)
        expected_engage = as_float(row, "band_vpd_high_at_waypoint") + float(intent["moisture_engage_vpd_excess_kpa"])
        mismatch.append(abs(float(materialized["mister_engage_kpa"]) - expected_engage))

    varying_counts: list[int] = []
    materialized_counts: list[int] = []
    for materialized_rows in plans.values():
        keys = set.intersection(*(set(item) for item in materialized_rows))
        materialized_counts.append(len(keys))
        varying_counts.append(sum(len({float(item[key]) for item in materialized_rows}) > 1 for key in keys))
    mismatch_array = np.asarray(mismatch)
    scheduled = sum(row["scheduled_while_governing"].lower() in {"t", "true", "1"} for row in rows)
    superseded = sum(row["superseded_before_scheduled"].lower() in {"t", "true", "1"} for row in rows)
    already_due = sum(row["already_due_at_creation"].lower() in {"t", "true", "1"} for row in rows)
    cutoff = audit.parse_ts(OUTCOME_END)
    due_rows = [row for row in rows if audit.parse_ts(row["waypoint_ts"]) < cutoff]
    due_already = sum(row["already_due_at_creation"].lower() in {"t", "true", "1"} for row in due_rows)
    due_scheduled = sum(row["scheduled_while_governing"].lower() in {"t", "true", "1"} for row in due_rows)
    due_superseded = sum(row["superseded_before_scheduled"].lower() in {"t", "true", "1"} for row in due_rows)
    genuinely_future_due = len(due_rows) - due_already
    future_mismatch_array = np.asarray(
        [
            value
            for row, value in zip(rows, mismatch, strict=True)
            if audit.parse_ts(row["waypoint_ts"]) < cutoff
            and row["already_due_at_creation"].lower() not in {"t", "true", "1"}
        ]
    )

    def mismatch_summary(values: np.ndarray) -> dict[str, Any]:
        return {
            "waypoints": len(values),
            "mean_abs": float(np.mean(values)),
            "p90_abs": float(np.quantile(values, 0.90)),
            "max_abs": float(np.max(values)),
            "over_0_05_kpa": int(np.sum(values > 0.05)),
        }

    return {
        "plans": len(plans),
        "waypoints": len(rows),
        "scheduled_while_plan_governed": scheduled,
        "scheduled_while_plan_governed_pct": 100.0 * scheduled / len(rows),
        "superseded_before_scheduled": superseded,
        "superseded_before_scheduled_pct": 100.0 * superseded / len(rows),
        "already_due_at_creation": already_due,
        "due_before_outcome_cutoff": {
            "waypoints": len(due_rows),
            "already_due_at_creation": due_already,
            "future_scheduled_while_plan_governed": due_scheduled,
            "future_superseded_before_scheduled": due_superseded,
            "genuinely_future_waypoints": genuinely_future_due,
            "future_superseded_pct": 100.0 * due_superseded / genuinely_future_due,
        },
        "materialized_parameters_per_plan": {
            "min": min(materialized_counts),
            "max": max(materialized_counts),
        },
        "parameters_varied_within_plan": {
            "mean": float(np.mean(varying_counts)),
            "median": float(np.median(varying_counts)),
            "min": min(varying_counts),
            "max": max(varying_counts),
        },
        "intent_field_counts": {
            "thermal_lead_time_positive": thermal_positive,
            "forecast_temp_bias_negative": forecast_temp_negative,
            "forecast_vpd_bias_negative": forecast_vpd_negative,
            "forecast_vpd_bias_at_negative_floor": forecast_vpd_floor,
            "materialized_night_vpd_bias_zero": night_bias_zero,
        },
        "all_waypoint_band_materialization_mismatch_kpa": mismatch_summary(mismatch_array),
        "future_due_band_materialization_mismatch_kpa": mismatch_summary(future_mismatch_array),
    }


def aggregate_rows(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in read_rows(path):
        converted: dict[str, Any] = {}
        for key, value in row.items():
            try:
                converted[key] = float(value) if value not in {"", None} else None
            except ValueError:
                converted[key] = value
        output.append(converted)
    return output


def run(args: argparse.Namespace) -> dict[str, Any]:
    climate = Path(args.climate)
    equipment = Path(args.equipment)
    forecast = Path(args.forecast_response)
    waypoints = Path(args.waypoints)
    accuracy = Path(args.forecast_vpd_accuracy)
    tunables = Path(args.effective_tunables)
    triggers = Path(args.trigger_outcomes)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "study": {
            "firmware_version": FIRMWARE_VERSION,
            "firmware_epoch_start": FIRMWARE_EPOCH_START,
            "outcome_end_exclusive": OUTCOME_END,
            "complete_denver_days": 34,
        },
        "inputs": {
            "climate": audit.file_manifest(climate),
            "equipment": audit.file_manifest(equipment),
            "forecast_response": audit.file_manifest(forecast),
            "waypoints": audit.file_manifest(waypoints),
            "forecast_vpd_accuracy": audit.file_manifest(accuracy),
            "effective_tunables": audit.file_manifest(tunables),
            "trigger_outcomes": audit.file_manifest(triggers),
        },
        "stale_policy_interruption": stale_policy_match(climate, equipment),
        "forecast_responsiveness": forecast_response(forecast),
        "waypoint_survival_and_semantics": waypoint_summary(waypoints),
        "forecast_vpd_calibration": {
            "rows": aggregate_rows(accuracy),
            "source_bug": (
                "The production accuracy view subtracts indoor house VPD from an outdoor Open-Meteo VPD forecast; "
                "correct outdoor-reference values are reported alongside it."
            ),
        },
        "effective_tunable_posture": aggregate_rows(tunables),
        "trigger_outcomes": aggregate_rows(triggers),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--climate", required=True)
    parser.add_argument("--equipment", required=True)
    parser.add_argument("--forecast-response", required=True)
    parser.add_argument("--waypoints", required=True)
    parser.add_argument("--forecast-vpd-accuracy", required=True)
    parser.add_argument("--effective-tunables", required=True)
    parser.add_argument("--trigger-outcomes", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(args)
    output = Path(args.output)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "matched_pairs": result["stale_policy_interruption"]["retained_pairs"]}))


if __name__ == "__main__":
    main()
