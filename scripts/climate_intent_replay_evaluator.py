#!/usr/bin/env python3
"""Replay-only evaluator for the ClimateIntent controller design.

This script consumes the existing firmware replay corpus and projects the new
candidate-action contract without commanding relays. It is intentionally simple:
the first model uses current bands, outdoor context, dew margin, solar pressure,
and occupancy to produce a deterministic replay action and compliance/resource
summary for historical rows.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.climate_intent import (
    ClimateActionDecision,
    ClimateCandidateProjection,
    ClimateIntent,
    ClimateResourceCostEstimate,
    choose_climate_candidate,
)

DEFAULT_REPLAY_CORPUS = REPO_ROOT / "firmware" / "test" / "data" / "replay_overrides.csv.gz"
GREENHOUSE_TZ = ZoneInfo("America/Denver")


def _float(value: str | None, default: float | None = None) -> float | None:
    if value is None or value == "" or value in {"\\N", "NULL"}:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.lower() in {"1", "t", "true", "yes", "on"}


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _band_error(value: float, low: float, high: float) -> float:
    if value < low:
        return low - value
    if value > high:
        return value - high
    return 0.0


def _parse_local_hour(ts: str) -> int:
    if not ts:
        return 12
    try:
        parsed = datetime.fromisoformat(ts.replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(GREENHOUSE_TZ).hour
    except ValueError:
        if len(ts) >= 13:
            try:
                return int(ts[11:13])
            except ValueError:
                pass
    return 12


def _open_replay(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="")
    return path.open(newline="")


@dataclass(frozen=True)
class ReplayClimateRow:
    ts: str
    temp_f: float
    vpd_kpa: float
    rh_pct: float
    dew_point_f: float | None
    outdoor_temp_f: float | None
    outdoor_dewpoint_f: float | None
    solar_w_m2: float
    occupied: bool
    greenhouse_state: str
    mode_reason: str
    temp_low: float
    temp_high: float
    vpd_low: float
    vpd_high: float
    safety_min: float
    safety_max: float
    vpd_min_safe: float
    vpd_max_safe: float
    fog_escalation_kpa: float
    eq_fog: bool
    eq_vent: bool
    eq_fan1: bool
    eq_fan2: bool
    eq_heat1: bool
    eq_heat2: bool
    eq_mister_south: bool
    eq_mister_west: bool
    eq_mister_center: bool

    @classmethod
    def from_csv(cls, row: dict[str, str]) -> ReplayClimateRow | None:
        temp_f = _float(row.get("temp_avg"))
        vpd_kpa = _float(row.get("vpd_avg"))
        rh_pct = _float(row.get("rh_avg"))
        if temp_f is None or vpd_kpa is None or rh_pct is None:
            return None
        if not (-10.0 <= temp_f <= 140.0 and 0.0 <= vpd_kpa <= 10.0 and 0.0 <= rh_pct <= 100.0):
            return None

        temp_low = _float(row.get("sp_temp_low"), 60.0) or 60.0
        temp_high = _float(row.get("sp_temp_high"), 78.0) or 78.0
        vpd_low = _float(row.get("sp_vpd_low"), 0.55) or 0.55
        vpd_high = _float(row.get("sp_vpd_high"), 1.25) or 1.25

        return cls(
            ts=row.get("ts", ""),
            temp_f=temp_f,
            vpd_kpa=vpd_kpa,
            rh_pct=rh_pct,
            dew_point_f=_float(row.get("indoor_dew_point")),
            outdoor_temp_f=_float(row.get("outdoor_temp_f")),
            outdoor_dewpoint_f=_float(row.get("outdoor_dewpoint_f")),
            solar_w_m2=_float(row.get("solar_irradiance_w_m2"), 0.0) or 0.0,
            occupied=_bool(row.get("occupied")),
            greenhouse_state=row.get("greenhouse_state", "") or "unknown",
            mode_reason=row.get("mode_reason", "") or "unknown",
            temp_low=temp_low,
            temp_high=temp_high,
            vpd_low=vpd_low,
            vpd_high=vpd_high,
            safety_min=_float(row.get("sp_safety_min"), 40.0) or 40.0,
            safety_max=_float(row.get("sp_safety_max"), 95.0) or 95.0,
            vpd_min_safe=_float(row.get("sp_vpd_min_safe"), 0.35) or 0.35,
            vpd_max_safe=_float(row.get("sp_vpd_max_safe"), 2.8) or 2.8,
            fog_escalation_kpa=_float(row.get("sp_fog_escalation_kpa"), 0.25) or 0.25,
            eq_fog=_bool(row.get("eq_fog")),
            eq_vent=_bool(row.get("eq_vent")),
            eq_fan1=_bool(row.get("eq_fan1")),
            eq_fan2=_bool(row.get("eq_fan2")),
            eq_heat1=_bool(row.get("eq_heat1")),
            eq_heat2=_bool(row.get("eq_heat2")),
            eq_mister_south=_bool(row.get("eq_mister_south")),
            eq_mister_west=_bool(row.get("eq_mister_west")),
            eq_mister_center=_bool(row.get("eq_mister_center")),
        )

    @property
    def dew_margin_f(self) -> float | None:
        if self.dew_point_f is None:
            return None
        return self.temp_f - self.dew_point_f

    @property
    def wet_relays_on(self) -> bool:
        return self.eq_fog or self.eq_mister_south or self.eq_mister_west or self.eq_mister_center

    @property
    def relay_signature(self) -> str:
        relays = {
            "fog": self.eq_fog,
            "vent": self.eq_vent,
            "fan1": self.eq_fan1,
            "fan2": self.eq_fan2,
            "heat1": self.eq_heat1,
            "heat2": self.eq_heat2,
            "mister_south": self.eq_mister_south,
            "mister_west": self.eq_mister_west,
            "mister_center": self.eq_mister_center,
        }
        active = [name for name, enabled in relays.items() if enabled]
        return ",".join(active) if active else "none"


def read_replay_rows(path: Path) -> Iterator[ReplayClimateRow]:
    with _open_replay(path) as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for raw in reader:
            row = ReplayClimateRow.from_csv(raw)
            if row is not None:
                yield row


def intent_from_replay_row(row: ReplayClimateRow) -> ClimateIntent:
    """Build bounded semantic intent from active bands plus weather pressure."""

    temp_width = _clamp(row.temp_high - row.temp_low, 3.0, 12.0)
    vpd_width = _clamp(row.vpd_high - row.vpd_low, 0.35, 1.2)
    temp_target = _clamp((row.temp_low + row.temp_high) / 2.0, 35.0, 95.0)
    vpd_target = _clamp((row.vpd_low + row.vpd_high) / 2.0, 0.35, 2.8)

    solar_precool_gain_f = _clamp(row.solar_w_m2 / 225.0, 0.0, 4.0)
    outdoor_heat_pressure = max(0.0, (row.outdoor_temp_f or row.temp_f) - row.temp_high)
    forecast_temp_bias_f = -_clamp((solar_precool_gain_f * 0.55) + (outdoor_heat_pressure * 0.15), 0.0, 4.0)

    dewpoint_advantage = 0.0
    if row.dew_point_f is not None and row.outdoor_dewpoint_f is not None:
        dewpoint_advantage = row.dew_point_f - row.outdoor_dewpoint_f
    forecast_vpd_bias_kpa = _clamp(dewpoint_advantage / 40.0, -0.4, 0.4)

    return ClimateIntent(
        temp_target_f=temp_target,
        temp_band_f=temp_width,
        vpd_target_kpa=vpd_target,
        vpd_band_kpa=vpd_width,
        forecast_temp_bias_f=forecast_temp_bias_f,
        forecast_vpd_bias_kpa=forecast_vpd_bias_kpa,
        solar_precool_gain_f=solar_precool_gain_f,
        thermal_lead_time_min=60.0 if solar_precool_gain_f >= 2.0 or outdoor_heat_pressure > 3.0 else 15.0,
        economizer_temp_advantage_f=_clamp(row.temp_f - (row.outdoor_temp_f or row.temp_f), 1.0, 15.0),
        economizer_dewpoint_advantage_f=_clamp(dewpoint_advantage, 1.0, 15.0),
        moisture_engage_vpd_excess_kpa=0.05,
        mist_duty_limit_pct=35.0 if row.vpd_kpa > row.vpd_high else 15.0,
        fog_escalate_vpd_excess_kpa=_clamp(row.fog_escalation_kpa, 0.1, 0.8),
        dew_margin_floor_f=8.0,
        wet_cutoff_hour=19.0,
        daily_mist_budget_gal=300.0,
        resource_sensitivity=0.7 if row.solar_w_m2 < 150.0 and row.vpd_kpa <= row.vpd_high else 0.35,
        relay_churn_penalty=0.5,
    )


@dataclass(frozen=True)
class ReplayEvaluation:
    row: ReplayClimateRow
    intent: ClimateIntent
    decision: ClimateActionDecision
    candidates: tuple[ClimateCandidateProjection, ...]


def _projection(
    *,
    action: str,
    row: ReplayClimateRow,
    temp_effect_f: float,
    vpd_effect_kpa: float,
    resource_cost: float,
    relay_churn_cost: float,
    blocked_reasons: tuple[str, ...] = (),
    prior_action: str | None = None,
) -> ClimateCandidateProjection:
    temp_after = row.temp_f + temp_effect_f
    vpd_after = row.vpd_kpa + vpd_effect_kpa
    return ClimateCandidateProjection(
        action=action,  # type: ignore[arg-type]
        safety_ok=not blocked_reasons,
        blocked_reasons=blocked_reasons,
        projected_temp_error_f=_band_error(temp_after, row.temp_low, row.temp_high),
        projected_vpd_error_kpa=_band_error(vpd_after, row.vpd_low, row.vpd_high),
        resource_cost=resource_cost,
        relay_churn_cost=relay_churn_cost,
        confidence=0.65,
        prior_action_hold_preference=1.0 if action == prior_action else 0.0,
    )


def evaluate_replay_row(row: ReplayClimateRow, prior_action: str | None = None) -> ReplayEvaluation:
    intent = intent_from_replay_row(row)
    dew_margin = row.dew_margin_f
    hour = _parse_local_hour(row.ts)
    wet_blockers: list[str] = []
    if row.occupied:
        wet_blockers.append("occupancy")
    if dew_margin is None or dew_margin < intent.dew_margin_floor_f:
        wet_blockers.append("dew_margin")
    if hour >= intent.wet_cutoff_hour:
        wet_blockers.append("time_window")
    if intent.daily_mist_budget_gal <= 0.0 or intent.mist_duty_limit_pct <= 0.0:
        wet_blockers.append("resource_budget")

    dry_excess = max(0.0, row.vpd_kpa - row.vpd_high)
    temp_high_excess = max(0.0, row.temp_f - row.temp_high)
    temp_low_excess = max(0.0, row.temp_low - row.temp_f)
    outdoor_cooling_advantage = max(0.0, row.temp_f - (row.outdoor_temp_f or row.temp_f))
    vent_cooling_effect = -_clamp((outdoor_cooling_advantage * 0.35) + 0.6, 0.2, 4.0)

    candidates: list[ClimateCandidateProjection] = []
    sensor_fault = not (-10.0 <= row.temp_f <= 140.0 and 0.0 <= row.vpd_kpa <= 10.0 and 0.0 <= row.rh_pct <= 100.0)
    candidates.append(
        _projection(
            action="SENSOR_FAULT",
            row=row,
            temp_effect_f=0.0,
            vpd_effect_kpa=0.0,
            resource_cost=0.0,
            relay_churn_cost=0.0,
            blocked_reasons=() if sensor_fault else ("not_faulted",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="SAFETY_HEAT",
            row=row,
            temp_effect_f=2.5,
            vpd_effect_kpa=0.05,
            resource_cost=8.0,
            relay_churn_cost=1.0,
            blocked_reasons=() if row.temp_f <= row.safety_min else ("not_safety_heat",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="SAFETY_COOL",
            row=row,
            temp_effect_f=vent_cooling_effect - 0.5,
            vpd_effect_kpa=-0.05 if dry_excess > 0 else 0.05,
            resource_cost=4.0,
            relay_churn_cost=1.0,
            blocked_reasons=() if row.temp_f >= row.safety_max else ("not_safety_cool",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="HEAT",
            row=row,
            temp_effect_f=min(2.0, temp_low_excess),
            vpd_effect_kpa=0.05,
            resource_cost=6.0,
            relay_churn_cost=1.0,
            blocked_reasons=() if temp_low_excess > 0 else ("temp_not_low",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="IDLE",
            row=row,
            temp_effect_f=0.0,
            vpd_effect_kpa=0.0,
            resource_cost=0.0,
            relay_churn_cost=0.0,
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="VENT_COOL",
            row=row,
            temp_effect_f=vent_cooling_effect,
            vpd_effect_kpa=0.08
            if row.outdoor_dewpoint_f is not None and row.outdoor_dewpoint_f < (row.dew_point_f or 99.0)
            else 0.0,
            resource_cost=2.0,
            relay_churn_cost=1.0,
            blocked_reasons=() if temp_high_excess > 0 else ("temp_not_high",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="VENT_COOL_MIST_ASSIST",
            row=row,
            temp_effect_f=vent_cooling_effect - 0.4,
            vpd_effect_kpa=-min(dry_excess, 0.18),
            resource_cost=5.0,
            relay_churn_cost=1.3,
            blocked_reasons=tuple(wet_blockers)
            if temp_high_excess > 0 and dry_excess >= intent.moisture_engage_vpd_excess_kpa
            else ("temp_or_vpd_below_assist",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="VENT_COOL_FOG_ASSIST",
            row=row,
            temp_effect_f=vent_cooling_effect - 0.7,
            vpd_effect_kpa=-min(dry_excess, 0.3),
            resource_cost=7.0,
            relay_churn_cost=1.5,
            blocked_reasons=tuple(wet_blockers)
            if temp_high_excess > 0 and dry_excess >= intent.fog_escalate_vpd_excess_kpa
            else ("below_threshold",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="SEALED_HUMIDIFY",
            row=row,
            temp_effect_f=0.2 if temp_high_excess > 0 else 0.0,
            vpd_effect_kpa=-min(dry_excess, 0.16),
            resource_cost=4.0,
            relay_churn_cost=1.2,
            blocked_reasons=tuple(wet_blockers)
            if temp_high_excess <= 0 and dry_excess > 0
            else ("temp_priority_blocks_seal",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="SEALED_FOG",
            row=row,
            temp_effect_f=0.3 if temp_high_excess > 0 else 0.0,
            vpd_effect_kpa=-min(dry_excess, 0.3),
            resource_cost=6.0,
            relay_churn_cost=1.4,
            blocked_reasons=tuple(wet_blockers)
            if temp_high_excess <= 0 and dry_excess >= intent.fog_escalate_vpd_excess_kpa
            else ("below_threshold",),
            prior_action=prior_action,
        )
    )
    candidates.append(
        _projection(
            action="DEHUM_VENT",
            row=row,
            temp_effect_f=vent_cooling_effect * 0.4,
            vpd_effect_kpa=0.15,
            resource_cost=2.5,
            relay_churn_cost=1.0,
            blocked_reasons=() if row.vpd_kpa < row.vpd_low else ("vpd_not_low",),
            prior_action=prior_action,
        )
    )

    if sensor_fault:
        selected = _candidate_by_action(candidates, "SENSOR_FAULT")
    elif row.temp_f >= row.safety_max:
        selected = _candidate_by_action(candidates, "SAFETY_COOL")
    elif row.temp_f <= row.safety_min:
        selected = _candidate_by_action(candidates, "SAFETY_HEAT")
    else:
        selected = choose_climate_candidate(candidates)
    temp_error_now = _band_error(row.temp_f, row.temp_low, row.temp_high)
    vpd_error_now = _band_error(row.vpd_kpa, row.vpd_low, row.vpd_high)
    if sensor_fault or selected.action.startswith("SAFETY_"):
        priority_axis = "safety"
    elif temp_error_now > 0.0:
        priority_axis = "temp"
    elif vpd_error_now > 0.0:
        priority_axis = "vpd"
    else:
        priority_axis = "resource"

    fog_block_reasons = _fog_block_reasons(row, intent, dry_excess, wet_blockers)
    decision = ClimateActionDecision(
        climate_action=selected.action,
        priority_axis=priority_axis,  # type: ignore[arg-type]
        temp_error_f=temp_error_now,
        vpd_error_kpa=vpd_error_now,
        candidate_summary=_candidate_summary(selected, candidates),
        moisture_assist_state=_moisture_assist_state(selected.action, wet_blockers, dry_excess),
        moisture_zone="center" if selected.action in {"VENT_COOL_MIST_ASSIST", "SEALED_HUMIDIFY"} else "none",
        next_mist_eligible_s=0.0 if selected.action in {"VENT_COOL_MIST_ASSIST", "SEALED_HUMIDIFY"} else None,
        fog_margin_kpa=dry_excess - intent.fog_escalate_vpd_excess_kpa,
        fog_block_reason=",".join(fog_block_reasons),
        resource_cost_estimate=_resource_estimate(selected.action),
    )
    return ReplayEvaluation(row=row, intent=intent, decision=decision, candidates=tuple(candidates))


def _fog_block_reasons(
    row: ReplayClimateRow, intent: ClimateIntent, dry_excess: float, wet_blockers: list[str]
) -> tuple[str, ...]:
    reasons = list(wet_blockers)
    if dry_excess < intent.fog_escalate_vpd_excess_kpa:
        reasons.append("below_threshold")
    if row.temp_f < row.temp_low:
        reasons.append("temp_low")
    if row.rh_pct >= 95.0:
        reasons.append("rh_ceiling")
    unique = tuple(dict.fromkeys(reasons))
    return unique or ("none",)


def _candidate_by_action(candidates: Iterable[ClimateCandidateProjection], action: str) -> ClimateCandidateProjection:
    for candidate in candidates:
        if candidate.action == action:
            return candidate
    raise ValueError(f"missing climate candidate: {action}")


def _resource_estimate(action: str) -> ClimateResourceCostEstimate:
    """Return a conservative per-row resource estimate for replay reporting.

    These are deliberately coarse until water/electric/gas metering is wired to
    candidate actions. Candidate selection still uses the relative
    `resource_cost` score; this estimate is only for historical report scale.
    """

    water_gal = 0.0
    if "MIST" in action or "HUMIDIFY" in action:
        water_gal = 0.04
    if "FOG" in action:
        water_gal = max(water_gal, 0.02)

    electric_kwh = 0.0
    if action in {"VENT_COOL", "VENT_COOL_MIST_ASSIST", "VENT_COOL_FOG_ASSIST", "DEHUM_VENT", "SAFETY_COOL"}:
        electric_kwh = 0.006
    if "FOG" in action:
        electric_kwh += 0.002

    gas_therm = 0.002 if action in {"HEAT", "SAFETY_HEAT"} else 0.0
    return ClimateResourceCostEstimate(water_gal=water_gal, electric_kwh=electric_kwh, gas_therm=gas_therm)


def _moisture_assist_state(action: str, wet_blockers: list[str], dry_excess: float) -> str:
    if wet_blockers:
        return "blocked"
    if action in {"VENT_COOL_MIST_ASSIST", "VENT_COOL_FOG_ASSIST", "SEALED_HUMIDIFY", "SEALED_FOG"}:
        return "served"
    if dry_excess > 0.0:
        return "engage_delay"
    return "inactive"


def _candidate_summary(selected: ClimateCandidateProjection, candidates: Iterable[ClimateCandidateProjection]) -> str:
    first_blocked = next((candidate for candidate in candidates if not candidate.safety_ok), None)
    if first_blocked is None:
        return f"{selected.action} selected; no rejected candidates"
    return f"{selected.action} selected; {first_blocked.action} blocked by {','.join(first_blocked.blocked_reasons)}"


def evaluate_rows(rows: Iterable[ReplayClimateRow], *, limit: int | None = None) -> Iterator[ReplayEvaluation]:
    prior_action: str | None = None
    count = 0
    for row in rows:
        evaluation = evaluate_replay_row(row, prior_action=prior_action)
        prior_action = evaluation.decision.climate_action
        yield evaluation
        count += 1
        if limit is not None and count >= limit:
            return


def summarize(evaluations: Iterable[ReplayEvaluation]) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    priority_counts: Counter[str] = Counter()
    firmware_state_counts: Counter[str] = Counter()
    mode_reason_counts: Counter[str] = Counter()
    relay_signature_counts: Counter[str] = Counter()
    temp_out = 0
    vpd_out = 0
    hot_dry = 0
    wet_when_replay_blocked = 0
    rows = 0
    replay_transitions = 0
    firmware_transitions = 0
    previous_replay: str | None = None
    previous_firmware: str | None = None
    water_cost = 0.0
    electric_cost = 0.0
    gas_cost = 0.0

    for evaluation in evaluations:
        rows += 1
        row = evaluation.row
        action = evaluation.decision.climate_action
        action_counts[action] += 1
        priority_counts[evaluation.decision.priority_axis] += 1
        firmware_state_counts[row.greenhouse_state] += 1
        mode_reason_counts[row.mode_reason] += 1
        relay_signature_counts[row.relay_signature] += 1
        temp_out += int(evaluation.decision.temp_error_f > 0.0)
        vpd_out += int(evaluation.decision.vpd_error_kpa > 0.0)
        hot_dry += int(row.temp_f > row.temp_high and row.vpd_kpa > row.vpd_high)
        wet_when_replay_blocked += int(row.wet_relays_on and evaluation.decision.moisture_assist_state == "blocked")
        replay_transitions += int(previous_replay is not None and previous_replay != action)
        firmware_transitions += int(previous_firmware is not None and previous_firmware != row.greenhouse_state)
        previous_replay = action
        previous_firmware = row.greenhouse_state
        water_cost += evaluation.decision.resource_cost_estimate.water_gal
        electric_cost += evaluation.decision.resource_cost_estimate.electric_kwh
        gas_cost += evaluation.decision.resource_cost_estimate.gas_therm

    return {
        "rows": rows,
        "replay_action_counts": dict(action_counts.most_common()),
        "priority_axis_counts": dict(priority_counts.most_common()),
        "firmware_state_counts": dict(firmware_state_counts.most_common(12)),
        "mode_reason_counts": dict(mode_reason_counts.most_common(12)),
        "relay_signature_counts": dict(relay_signature_counts.most_common(12)),
        "temp_out_of_band_rows": temp_out,
        "vpd_out_of_band_rows": vpd_out,
        "hot_dry_rows": hot_dry,
        "wet_relay_rows_when_replay_blocked": wet_when_replay_blocked,
        "replay_action_transitions": replay_transitions,
        "firmware_state_transitions": firmware_transitions,
        "resource_estimate": {
            "water_gal": round(water_cost, 3),
            "electric_kwh": round(electric_cost, 3),
            "gas_therm": round(gas_cost, 3),
        },
    }


def _print_text(summary: dict[str, Any]) -> None:
    print("ClimateIntent replay report")
    for key in (
        "rows",
        "temp_out_of_band_rows",
        "vpd_out_of_band_rows",
        "hot_dry_rows",
        "wet_relay_rows_when_replay_blocked",
        "replay_action_transitions",
        "firmware_state_transitions",
    ):
        print(f"{key}={summary[key]}")
    print(f"resource_estimate={json.dumps(summary['resource_estimate'], sort_keys=True)}")
    for key in ("replay_action_counts", "priority_axis_counts", "firmware_state_counts", "mode_reason_counts"):
        print(f"{key}={json.dumps(summary[key], sort_keys=True)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_REPLAY_CORPUS, help="Replay corpus .csv or .csv.gz")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows for smoke tests")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    summary = summarize(evaluate_rows(read_replay_rows(args.csv), limit=args.limit))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
