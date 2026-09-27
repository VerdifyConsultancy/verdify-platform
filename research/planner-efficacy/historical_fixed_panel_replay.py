"""Reproduce August weather support with a fixed panel, without crop claims.

The historical crop target and physical contributor identity are unavailable.
This diagnostic repeats the original weather matcher, joins the exact six-field
database-flush panel, and withholds band distance, efficacy, and causal savings.
Raw input and bin-level reports must remain outside Git.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

# isort: split

import audit
import epoch_analysis as epoch
from fixed_panel import canonical, digest, timestamp
from fixed_panel_coverage import coverage


def replay(climate_path: Path, six_field_path: Path) -> dict:
    climate_bytes = climate_path.read_bytes()
    export_bytes = six_field_path.read_bytes()
    climate_rows = epoch.read_rows(climate_path)
    panel = coverage(json.loads(export_bytes))
    by_bucket = {timestamp(row["bucket_start"]): row for row in panel["bins"]}
    telemetry = audit.load_climate(climate_path)
    if len(climate_rows) != len(telemetry.times) or len(climate_rows) != len(by_bucket):
        raise ValueError("weather and six-field exports have different bin populations")
    if set(telemetry.times) != set(by_bucket) or len(set(telemetry.times)) != len(telemetry.times):
        raise ValueError("weather and six-field bins must match exactly and uniquely")

    local_days = np.asarray([when.astimezone(epoch.DENVER).date() for when in telemetry.times], dtype=object)
    local_slots = np.asarray(
        [
            when.astimezone(epoch.DENVER).hour * 4 + when.astimezone(epoch.DENVER).minute // 15
            for when in telemetry.times
        ]
    )
    in_epoch = (local_days >= epoch.CONTROL_START) & (local_days < epoch.CONTROL_END)
    stale = np.flatnonzero(in_epoch & np.isin(local_days, list(epoch.STALE_DAYS)))
    features = np.column_stack([telemetry.columns[name] for name in epoch.MATCH_FEATURES])
    controls, control_mean, control_sd, stale_matched, controls_matched, distances = epoch.nearest_same_slot_pairs(
        features, local_days, local_slots, stale, epoch.CONTROL_START
    )
    # Retain the original matching algorithm, including its candidate-pool
    # interpolation. Selected weather features must still be raw measured.
    raw_weather = np.asarray(
        [all(math.isfinite(audit.maybe_float(row.get(name))) for name in epoch.MATCH_FEATURES) for row in climate_rows]
    )
    if not raw_weather[stale_matched].all() or not raw_weather[controls_matched].all():
        raise ValueError("a weather-selected pair uses an imputed feature")

    def bin_at(index: int) -> dict:
        return by_bucket[telemetry.times[int(index)]]

    matched = list(zip(stale_matched.tolist(), controls_matched.tolist(), strict=True))
    panel_retained = [
        (s, c)
        for s, c in matched
        if bin_at(s)["complete_minutes"]["joint"] >= 12 and bin_at(c)["complete_minutes"]["joint"] >= 12
    ]
    excluded_panel = Counter(
        "both_incomplete"
        if bin_at(s)["complete_minutes"]["joint"] < 12 and bin_at(c)["complete_minutes"]["joint"] < 12
        else "stale_incomplete"
        if bin_at(s)["complete_minutes"]["joint"] < 12
        else "control_incomplete"
        for s, c in matched
        if (s, c) not in panel_retained
    )
    per_day = defaultdict(list)
    for s, c in panel_retained:
        per_day[local_days[s].isoformat()].append(bin_at(s)["panel_mean"]["vpd"] - bin_at(c)["panel_mean"]["vpd"])
    day_means = {day: float(np.mean(values)) for day, values in sorted(per_day.items())}
    # Four sequential days are the largest available time unit; this SD is a
    # descriptive design input and cannot identify a treatment standard error.
    day_sd = float(np.std(list(day_means.values()), ddof=1)) if len(day_means) >= 2 else None

    def selected_context(indices: list[int]) -> dict:
        executed_vpd = [audit.maybe_float(climate_rows[i].get("executed_vpd_target_kpa")) for i in indices]
        executed_vpd = [value for value in executed_vpd if math.isfinite(value)]
        return {
            "bins": len(indices),
            "south_vpd_present": sum(
                math.isfinite(audit.maybe_float(climate_rows[i].get("vpd_south_kpa"))) for i in indices
            ),
            "south_temp_present": sum(
                math.isfinite(audit.maybe_float(climate_rows[i].get("temp_south_f"))) for i in indices
            ),
            "plan_id_present": sum(bool(climate_rows[i].get("plan_id")) for i in indices),
            "planner_instances": dict(
                sorted(Counter(climate_rows[i].get("planner_instance") or "missing" for i in indices).items())
            ),
            "executed_vpd_target_present": sum(
                math.isfinite(audit.maybe_float(climate_rows[i].get("executed_vpd_target_kpa"))) for i in indices
            ),
            "executed_house_vpd_target_mean_kpa": float(np.mean(executed_vpd)) if executed_vpd else None,
            "executed_house_vpd_target_range_kpa": [min(executed_vpd), max(executed_vpd)] if executed_vpd else None,
            "current_resolver_vpd_bounds_present": sum(
                all(
                    math.isfinite(audit.maybe_float(climate_rows[i].get(name)))
                    for name in ("eval_vpd_low_kpa", "eval_vpd_high_kpa")
                )
                for i in indices
            ),
        }

    stale_indices = [s for s, _ in panel_retained]
    control_indices = [c for _, c in panel_retained]
    return {
        "schema": "verdify-august-fixed-panel-support-v1",
        "climate_file_sha256": digest(climate_bytes),
        "six_field_file_sha256": digest(export_bytes),
        "six_field_canonical_sha256": panel["input_canonical_sha256"],
        "source_sha256": {
            "historical_fixed_panel_replay.py": digest(Path(__file__).read_bytes()),
            "fixed_panel_coverage.py": digest(Path(coverage.__code__.co_filename).read_bytes()),
            "epoch_analysis.py": digest(Path(epoch.__file__).read_bytes()),
            "audit.py": digest(Path(audit.__file__).read_bytes()),
        },
        "window_start": panel["window_start"],
        "window_end": panel["window_end"],
        "sample_basis": panel["sample_basis"],
        "weather_match": {
            "candidate_stale_bins": len(stale),
            "candidate_control_bins": len(controls),
            "retained_pairs_before_panel": len(matched),
            "excluded_stale_no_weather_common_support": len(stale) - len(matched),
            "selected_weather_features_all_raw_measured": True,
            "candidate_pool_imputation_counts": telemetry.imputation_counts,
            "unique_control_bins": len(set(controls_matched.tolist())),
            "maximum_control_reuse": max(Counter(controls_matched.tolist()).values()) if len(controls_matched) else 0,
            "caliper_max_abs_control_sd": 0.35,
            "control_weather_mean": dict(zip(epoch.MATCH_FEATURES, control_mean.tolist(), strict=True)),
            "control_weather_sd": dict(zip(epoch.MATCH_FEATURES, control_sd.tolist(), strict=True)),
            "post_match_weather_smd": {
                name: float(
                    (np.mean(features[stale_matched, index]) - np.mean(features[controls_matched, index]))
                    / control_sd[index]
                )
                for index, name in enumerate(epoch.MATCH_FEATURES)
            },
            "match_distance_median": float(np.median(distances)) if len(distances) else None,
        },
        "fixed_panel": {
            "expected_bins": panel["summary"]["expected_bins"],
            "joint_eligible_bins_12_of_15": panel["summary"]["joint_eligible_bins_12_of_15"],
            "joint_complete_minutes": panel["summary"]["joint_complete_minutes"],
            "matched_pairs_after_panel": len(panel_retained),
            "excluded_selected_pairs": dict(sorted(excluded_panel.items())),
            "stale_selected_joint_minutes_min": min(
                (bin_at(s)["complete_minutes"]["joint"] for s in stale_indices), default=None
            ),
            "control_selected_joint_minutes_min": min(
                (bin_at(c)["complete_minutes"]["joint"] for c in control_indices), default=None
            ),
            "south_not_used_in_panel": True,
        },
        "selected_context": {
            "stale": selected_context(stale_indices),
            "control": selected_context(control_indices),
            "target_caveat": "executed house VPD target is a setpoint, not an authenticated historical crop band; eval bounds are current-resolver calculations",
            "delivery_caveat": "plan_id interval coverage does not prove confirmed delivery; stale interval overlapped the dispatcher/band outage",
        },
        "raw_vpd_level_sensitivity": {
            "unit": "kPa",
            "per_stale_day_paired_bin_mean_difference": day_means,
            "between_four_day_descriptive_sd": day_sd,
            "interpretation": "fixed-panel raw level only; no crop band, intervention contrast, or causal standard error",
        },
        "historical_target_verified": False,
        "historical_contributor_identity_verified": False,
        "served_or_consumed_band_verified": False,
        "delivery_effect_separable": False,
        "causal_effect_estimate": False,
        "corrected_crop_band_effect": None,
        "corrected_resource_savings": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--climate", type=Path, required=True)
    parser.add_argument("--six-field", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = replay(args.climate, args.six_field)
        with args.output.open("xb") as output:
            output.write(canonical(report) + b"\n")
    except (ValueError, TypeError, OverflowError, OSError):
        parser.exit(2, "historical fixed-panel replay rejected; no scientific acceptance claimed\n")


if __name__ == "__main__":
    main()
