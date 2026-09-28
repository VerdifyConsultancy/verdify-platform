#!/usr/bin/env python3
"""Reproduce a bounded pre-draw climate and pair-retention sensitivity.

The private September 27 export is a fixed input. This script never connects
to a database, invokes a selector, or converts route-level bins into physical
crop outcomes. Its public output contains only aggregate counts and hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
INPUT_SHA256 = {
    "climate_bins.csv": "05c96485a34cf3cdce3a2955e62e7ed98580a1e21546162724a285443abce486",
    "climate_summary.json": "462d19ca78906f53cd72122e50537e3337e1b5a9edfa631d9e51125fcf927c49",
    "input_presence.txt": "2f9b27713efe3366cdda40b2648b0d404413c3a83e2233eff2f764d176ed3c83",
}
PERIODS = {
    "winter_analogue_2025": (date(2025, 11, 2), date(2025, 12, 31)),
    "warm_analogue_2026": (date(2026, 6, 1), date(2026, 7, 30)),
}
CSV_FIELDS = (
    "period",
    "local_date",
    "bin_start",
    "observed_minutes",
    "source_rows",
    "temp_minutes",
    "vpd_minutes",
    "joint_minutes",
    "temp_panel_mean_f",
    "vpd_panel_mean_kpa",
)
POWER_PATH = ROOT / "research/planner-efficacy/protocols/planner-switchback-v2-power.json"
TZ = ZoneInfo("America/Denver")
PAIRS = 30
BINS_PER_DAY = 72
MINUTES_PER_BIN = 15
MIN_COMPLETE_MINUTES = 12
MIN_VALID_BINS = 66
MAX_MISSING_RUN = 2


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _dates(first: date, last: date) -> list[date]:
    days = [(first + timedelta(days=offset)) for offset in range((last - first).days + 1)]
    if len(days) != 60:
        raise ValueError("analogue must contain exactly 60 local days")
    return days


def _window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(6), TZ).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), TZ).astimezone(UTC)
    if end - start != timedelta(hours=18):
        raise ValueError("analogue primary window is not 18 elapsed hours")
    return start, end


def _minutes(row: dict[str, str], field: str) -> int:
    value = int(row[field])
    if not 0 <= value <= MINUTES_PER_BIN:
        raise ValueError(f"{field} outside one 15-minute bin")
    return value


def _longest_missing(valid: list[bool]) -> int:
    current = longest = 0
    for present in valid:
        current = 0 if present else current + 1
        longest = max(longest, current)
    return longest


def summarize_bins(path: Path) -> dict[str, dict]:
    """Recompute daily climate eligibility directly from the frozen raw export."""
    grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    with path.open(newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("raw export column contract changed")
        for row in reader:
            if row["period"] not in PERIODS:
                raise ValueError("unexpected analogue period")
            grouped[row["period"]][row["local_date"]].append(row)
    if set(grouped) != set(PERIODS):
        raise ValueError("missing analogue period")
    results = {}
    for name, (first, last) in PERIODS.items():
        days = _dates(first, last)
        if set(grouped[name]) != {day.isoformat() for day in days}:
            raise ValueError("analogue day set differs from its fixed calendar")
        if len({_window(day)[0].astimezone(TZ).utcoffset() for day in days}) != 1:
            raise ValueError("analogue crosses a primary-window UTC offset change")
        daily = []
        totals = {
            key: 0
            for key in (
                "source_rows",
                "observed_minutes",
                "temp_complete_minutes",
                "vpd_complete_minutes",
                "joint_complete_minutes",
                "observed_bins",
                "temp_eligible_bins",
                "vpd_eligible_bins",
                "joint_eligible_bins",
            )
        }
        level_pairs: list[tuple[float, float]] = []
        for day in days:
            rows = sorted(grouped[name][day.isoformat()], key=lambda row: row["bin_start"])
            if len(rows) != BINS_PER_DAY:
                raise ValueError("day does not contain all 72 ordered bins")
            start, _ = _window(day)
            valid = []
            for index, row in enumerate(rows):
                observed_at = datetime.fromisoformat(row["bin_start"])
                if observed_at != start + timedelta(minutes=15 * index):
                    raise ValueError("bin timestamps do not match the exact local outcome window")
                observed = _minutes(row, "observed_minutes")
                temp = _minutes(row, "temp_minutes")
                vpd = _minutes(row, "vpd_minutes")
                joint = _minutes(row, "joint_minutes")
                if temp > observed or vpd > observed or joint > min(temp, vpd):
                    raise ValueError("panel minute counts conflict")
                source_rows = int(row["source_rows"])
                if source_rows < observed:
                    raise ValueError("raw source rows fewer than observed minute slots")
                for field, value in (
                    ("source_rows", source_rows),
                    ("observed_minutes", observed),
                    ("temp_complete_minutes", temp),
                    ("vpd_complete_minutes", vpd),
                    ("joint_complete_minutes", joint),
                    ("observed_bins", observed > 0),
                    ("temp_eligible_bins", temp >= MIN_COMPLETE_MINUTES),
                    ("vpd_eligible_bins", vpd >= MIN_COMPLETE_MINUTES),
                    ("joint_eligible_bins", joint >= MIN_COMPLETE_MINUTES),
                ):
                    totals[field] += value
                joint_valid = joint >= MIN_COMPLETE_MINUTES
                valid.append(joint_valid)
                if joint_valid and row["temp_panel_mean_f"] and row["vpd_panel_mean_kpa"]:
                    level_pair = (float(row["temp_panel_mean_f"]), float(row["vpd_panel_mean_kpa"]))
                    if any(not math.isfinite(value) for value in level_pair):
                        raise ValueError("nonfinite eligible panel level")
                    level_pairs.append(level_pair)
            valid_bins = sum(valid)
            longest = _longest_missing(valid)
            daily.append(
                {
                    "local_date": day.isoformat(),
                    "joint_valid_bins": valid_bins,
                    "longest_missing_run": longest,
                    "passes_66_bins_and_max_2_run": valid_bins >= MIN_VALID_BINS and longest <= MAX_MISSING_RUN,
                }
            )
        passed = [day["passes_66_bins_and_max_2_run"] for day in daily]
        results[name] = {
            "daily": daily,
            "totals": totals,
            "climate_eligible_days": sum(passed),
            "climate_eligible_adjacent_pairs": sum(passed[2 * i] and passed[2 * i + 1] for i in range(PAIRS)),
            "first_local_date": first.isoformat(),
            "last_local_date": last.isoformat(),
            "primary_window_elapsed_hours": 18,
            "first_full_local_day_elapsed_hours": int(
                (
                    datetime.combine(first + timedelta(days=1), time(), TZ).astimezone(UTC)
                    - datetime.combine(first, time(), TZ).astimezone(UTC)
                ).total_seconds()
                / 3600
            ),
            "raw_temperature_vpd_level_correlation": (
                statistics.correlation([pair[0] for pair in level_pairs], [pair[1] for pair in level_pairs])
                if len(level_pairs) > 1
                else None
            ),
            "level_correlation_pairs": len(level_pairs),
        }
    return results


def _presence(path: Path) -> dict[str, str]:
    rows = [line.split("|", 1) for line in path.read_text().splitlines()]
    if any(len(row) != 2 for row in rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError("source-presence receipt is malformed")
    values = dict(rows)
    for key in (
        "winter_forecast_asof_rows",
        "warm_forecast_asof_rows",
        "winter_equipment_receipts",
        "warm_equipment_receipts",
        "selector_choices",
    ):
        if key not in values or int(values[key]) < 0:
            raise ValueError("source-presence count is absent or invalid")
    return values


def retention_scenarios(provisional_power: float, reference_pair_probability: float) -> list[dict]:
    """Reweight only the provisional model's all-pair completion factor."""
    if not 0 <= provisional_power <= 1 or not 0 < reference_pair_probability <= 1:
        raise ValueError("provisional model probabilities must be bounded")
    conditional_model_pass = provisional_power / reference_pair_probability**PAIRS
    if conditional_model_pass > 1:
        raise ValueError("provisional joint power exceeds its modeled complete-pair ceiling")
    return [
        {
            "assumed_independent_complete_pair_probability": probability,
            "all_30_pairs_retained_probability": probability**PAIRS,
            "absolute_joint_advance_upper_bound_from_retention": probability**PAIRS,
            "provisional_model_only_rescaled_joint_power": conditional_model_pass * probability**PAIRS,
        }
        for probability in (0.9995, 0.99, 0.95)
    ]


def build(input_dir: Path) -> dict:
    for name, expected in INPUT_SHA256.items():
        if _sha((input_dir / name).read_bytes()) != expected:
            raise ValueError(f"frozen private input hash differs: {name}")
    bins = summarize_bins(input_dir / "climate_bins.csv")
    summary = json.loads((input_dir / "climate_summary.json").read_text())
    if set(summary) != set(PERIODS):
        raise ValueError("daily summary period set differs")
    periods = {}
    for name, computed in bins.items():
        recorded = summary[name]
        if recorded["daily"] != computed["daily"] or any(
            recorded["summary"][key] != value for key, value in computed["totals"].items()
        ):
            raise ValueError("frozen summary differs from direct raw-bin recomputation")
        if recorded["summary"]["days_passing_joint_daily_rule"] != computed["climate_eligible_days"]:
            raise ValueError("frozen daily eligibility total differs")
        recorded_correlation = recorded["summary"]["raw_bin_panel_temperature_vpd_pearson"]
        computed_correlation = computed["raw_temperature_vpd_level_correlation"]
        if recorded["summary"]["correlation_pairs"] != computed["level_correlation_pairs"] or not (
            recorded_correlation == computed_correlation
            or (
                recorded_correlation is not None
                and computed_correlation is not None
                and math.isclose(recorded_correlation, computed_correlation, abs_tol=1e-12)
            )
        ):
            raise ValueError("frozen panel-level correlation differs from raw bins")
        periods[name] = {key: value for key, value in computed.items() if key != "daily" and key != "totals"}
        periods[name]["joint_eligible_bins"] = computed["totals"]["joint_eligible_bins"]
        periods[name]["joint_complete_minutes"] = computed["totals"]["joint_complete_minutes"]
        periods[name]["expected_bins"] = 60 * BINS_PER_DAY
        periods[name]["expected_minutes"] = 60 * BINS_PER_DAY * MINUTES_PER_BIN
        periods[name]["correlation_scope"] = (
            "descriptive eligible bin levels; not randomized paired endpoint covariance"
        )
    presence = _presence(input_dir / "input_presence.txt")
    power_raw = POWER_PATH.read_bytes()
    power = json.loads(power_raw)
    if (
        power.get("schema") != "verdify-switchback-v2-power-design"
        or power.get("artifact_sha256") != _sha(_canonical({k: v for k, v in power.items() if k != "artifact_sha256"}))
        or power.get("primary_window_local") != "[06:00,24:00)"
    ):
        raise ValueError("provisional power artifact identity or window differs")
    thirty = [row for row in power["selection"]["evaluations"] if row["pairs"] == PAIRS]
    if len(thirty) != 1 or not math.isclose(
        thirty[0]["complete_all_pairs_probability_model"],
        power["assumptions"]["complete_pair_probability"] ** PAIRS,
        abs_tol=1e-12,
    ):
        raise ValueError("provisional 30-pair model is absent or inconsistent")
    for key in (
        "winter_forecast_asof_rows",
        "warm_forecast_asof_rows",
        "winter_equipment_receipts",
        "warm_equipment_receipts",
        "selector_choices",
    ):
        if int(presence[key]) != 0:
            raise ValueError("source-presence state changed; this frozen no-replay artifact must be superseded")
    source_paths = (
        "research/planner-efficacy/protocols/direct-launch-basis-v1.json",
        "research/planner-efficacy/protocols/planner-switchback-v2.template.yaml",
        "research/planner-efficacy/switchback/v2_power.py",
        "research/planner-efficacy/switchback/v2_selector.py",
        "research/planner-efficacy/switchback/v2_outcomes.py",
    )
    return {
        "schema": "verdify-c2-pre-draw-seasonal-sensitivity-v1",
        "status": "bounded_pre_draw_sensitivity_not_design_lock",
        "study_scope": "accepted exploratory 30 adjacent pairs / 60 local days; this script makes no draw",
        "calendar_timezone": "America/Denver",
        "fixed_panel_routes": ["north", "east", "west"],
        "climate_rule": {
            "window_local": "[06:00,24:00)",
            "complete_minutes_per_bin": 12,
            "eligible_bins_per_day": 66,
            "max_contiguous_missing_bins": 2,
        },
        "source_sha256": {
            **INPUT_SHA256,
            "planner-switchback-v2-power.json": _sha(power_raw),
            **{path: _sha((ROOT / path).read_bytes()) for path in source_paths},
            "pre_draw_sensitivity.py": _sha(Path(__file__).read_bytes()),
        },
        "historical_analogues": periods,
        "selector_input_presence": {
            "historical_asof_forecast_rows": 0,
            "historical_equipment_receipts": 0,
            "actual_selector_choices": 0,
            "empirical_profile_mix": None,
            "empirical_fallback_rate": None,
            "selector_effect_dilution": None,
            "interpretation": "source absence; zero observed choices does not mean zero fallback",
        },
        "three_endpoint_estimands": {
            "frozen_historical_crop_targets_available": False,
            "complete_nine_state_direct_source_available": False,
            "paired_endpoint_effects": None,
            "paired_endpoint_variance": None,
            "paired_three_endpoint_covariance": None,
            "source_faithful_joint_advance_power": None,
        },
        "provisional_model": {
            "thirty_pair_joint_advance_power": thirty[0]["joint_advance_power"],
            "reference_complete_pair_probability": power["assumptions"]["complete_pair_probability"],
            "reference_monte_carlo_standard_error": thirty[0]["monte_carlo_standard_error"],
            "conditional_pass_probability_in_provisional_model_only": (
                thirty[0]["joint_advance_power"] / thirty[0]["complete_all_pairs_probability_model"]
            ),
            "retention_sensitivity": retention_scenarios(
                thirty[0]["joint_advance_power"], power["assumptions"]["complete_pair_probability"]
            ),
            "interpretation": "assumption-only reweighting; historical effects, selector mix and covariance are unavailable",
        },
        "claim_limits": [
            "Historical climate eligibility is necessary only; it does not authenticate probe identity or freshness.",
            "Historical target lineage and as-of selector/equipment inputs are absent in both analogue windows.",
            "The warm bin-level temperature/VPD level correlation is not paired randomized endpoint covariance.",
            "Retention scenarios assume independent identical pair completion and keep provisional conditional power fixed.",
            "The provisional model uses historical 22-hour scales while the protocol outcome window is 18 hours.",
            "No effect, endpoint variance, causal efficacy, physical proof or experimental readiness is estimated.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = json.dumps(build(args.input_dir), allow_nan=False, sort_keys=True, indent=2).encode() + b"\n"
    if args.check:
        if args.output.read_bytes() != raw:
            raise ValueError("frozen pre-draw sensitivity artifact differs from exact source inputs")
    else:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)


if __name__ == "__main__":
    main()
