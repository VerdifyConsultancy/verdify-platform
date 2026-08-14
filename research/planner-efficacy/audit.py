#!/usr/bin/env python3
"""Counterfactual planner efficacy audit.

The analysis deliberately keeps raw telemetry outside Git. It reconstructs
15-minute relay duty from transition rows, fits two actuator-aware greenhouse
models, validates recursive held-out rollouts, and compares observed actions to
a fully specified fixed-setpoint coordinated PID policy. Numeric PID outcomes
are eligibility-gated; a failed model or support gate produces "not estimable"
rather than a superiority claim.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

STEP_MINUTES = 15
STEP_SECONDS = STEP_MINUTES * 60

CLIMATE_EQUIPMENT = (
    "heat1",
    "heat2",
    "vent",
    "fan1",
    "fan2",
    "fog",
    "mister_south",
    "mister_west",
    "mister_center",
)
EXOGENOUS_EQUIPMENT = ("grow_light_main", "grow_light_grow")
MODEL_EQUIPMENT = CLIMATE_EQUIPMENT + EXOGENOUS_EQUIPMENT

ENERGY_WATTS = {
    "fan1": (102.0, 113.0, 124.0),
    "fan2": (102.0, 113.0, 124.0),
    "fog": (315.0, 468.0, 620.0),
    "heat1": (1350.0, 1436.0, 1500.0),
    "vent": (8.0, 10.0, 12.0),
}
HEAT2_BTU_H = (75000.0, 75000.0, 75000.0)


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def maybe_float(value: str | None) -> float:
    if value is None or value.strip() in {"", "NULL", "\\N"}:
        return math.nan
    return float(value)


def file_manifest(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    rows = -1
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = max(0, sum(1 for _ in handle) - 1)
    return {"file": path.name, "sha256": digest.hexdigest(), "rows": rows, "bytes": path.stat().st_size}


def saturation_vapor_pressure_kpa(temp_f: np.ndarray | float) -> np.ndarray | float:
    temp_c = (np.asarray(temp_f) - 32.0) * 5.0 / 9.0
    result = 0.6108 * np.exp(17.27 * temp_c / (temp_c + 237.3))
    return float(result) if np.ndim(result) == 0 else result


def absolute_humidity_g_m3(temp_f: np.ndarray | float, rh_pct: np.ndarray | float) -> np.ndarray | float:
    temp_c = (np.asarray(temp_f) - 32.0) * 5.0 / 9.0
    vapor_hpa = 10.0 * saturation_vapor_pressure_kpa(temp_f) * np.asarray(rh_pct) / 100.0
    result = 216.7 * vapor_hpa / (temp_c + 273.15)
    return float(result) if np.ndim(result) == 0 else result


def vpd_from_state(temp_f: np.ndarray | float, abs_humidity: np.ndarray | float) -> np.ndarray | float:
    temp_c = (np.asarray(temp_f) - 32.0) * 5.0 / 9.0
    vapor_kpa = np.asarray(abs_humidity) * (temp_c + 273.15) / 216.7 / 10.0
    result = np.maximum(0.0, saturation_vapor_pressure_kpa(temp_f) - vapor_kpa)
    return float(result) if np.ndim(result) == 0 else result


def dewpoint_f(temp_f: float, abs_humidity: float) -> float:
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    vapor_hpa = max(0.01, abs_humidity * (temp_c + 273.15) / 216.7)
    log_term = math.log(vapor_hpa / 6.112)
    dew_c = 243.5 * log_term / (17.67 - log_term)
    return dew_c * 9.0 / 5.0 + 32.0


def interpolate(values: np.ndarray) -> tuple[np.ndarray, int]:
    missing = ~np.isfinite(values)
    if not missing.any():
        return values, 0
    valid = ~missing
    if valid.sum() < 2:
        raise ValueError("cannot interpolate a column with fewer than two observations")
    out = values.copy()
    out[missing] = np.interp(np.flatnonzero(missing), np.flatnonzero(valid), values[valid])
    return out, int(missing.sum())


@dataclass
class Telemetry:
    times: list[datetime]
    epoch: np.ndarray
    columns: dict[str, np.ndarray]
    actions: dict[str, np.ndarray]
    imputation_counts: dict[str, int]

    @property
    def n(self) -> int:
        return len(self.times)


def load_climate(path: Path) -> Telemetry:
    numeric_columns = (
        "sample_count",
        "temp_f",
        "abs_humidity_g_m3",
        "vpd_kpa",
        "rh_pct",
        "outdoor_temp_f",
        "outdoor_rh_pct",
        "solar_w_m2",
        "wind_mph",
        "solar_altitude_deg",
        "eval_temp_low_f",
        "eval_temp_target_f",
        "eval_temp_high_f",
        "eval_vpd_low_kpa",
        "eval_vpd_target_kpa",
        "eval_vpd_high_kpa",
    )
    times: list[datetime] = []
    values: dict[str, list[float]] = {name: [] for name in numeric_columns}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            times.append(parse_ts(row["bucket"]))
            for name in numeric_columns:
                values[name].append(maybe_float(row.get(name)))
    columns = {name: np.asarray(series, dtype=float) for name, series in values.items()}
    imputation_counts: dict[str, int] = {}
    for name in ("outdoor_temp_f", "outdoor_rh_pct", "wind_mph"):
        columns[name], imputation_counts[name] = interpolate(columns[name])
    # Measured solar starts later than the retained climate series. Zero is not
    # a defensible daytime imputation, so use a clear-sky shape from solar
    # altitude, scaled to the median measured irradiance/altitude ratio.
    solar = columns["solar_w_m2"]
    missing_solar = ~np.isfinite(solar)
    columns["solar_observed"] = (~missing_solar).astype(float)
    positive = np.isfinite(solar) & (columns["solar_altitude_deg"] > 2.0) & (solar > 0.0)
    scale = float(np.median(solar[positive] / np.sin(np.deg2rad(columns["solar_altitude_deg"][positive]))))
    clear_sky = np.maximum(0.0, np.sin(np.deg2rad(columns["solar_altitude_deg"]))) * scale
    solar[missing_solar] = clear_sky[missing_solar]
    columns["solar_w_m2"] = solar
    imputation_counts["solar_w_m2"] = int(missing_solar.sum())
    epoch = np.asarray([item.timestamp() for item in times], dtype=float)
    return Telemetry(times, epoch, columns, {}, imputation_counts)


def attach_equipment_duty(telemetry: Telemetry, path: Path) -> None:
    events: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            equipment = row["equipment"]
            if equipment not in MODEL_EQUIPMENT:
                continue
            events[equipment].append((parse_ts(row["ts"]).timestamp(), row["state"].lower() in {"t", "true", "1"}))

    for equipment in MODEL_EQUIPMENT:
        equipment_events = sorted(events[equipment])
        duty = np.zeros(telemetry.n, dtype=float)
        pointer = 0
        state = False
        for index, start in enumerate(telemetry.epoch):
            end = start + STEP_SECONDS
            while pointer < len(equipment_events) and equipment_events[pointer][0] <= start:
                state = equipment_events[pointer][1]
                pointer += 1
            cursor = start
            on_seconds = 0.0
            inner = pointer
            local_state = state
            while inner < len(equipment_events) and equipment_events[inner][0] < end:
                event_ts, next_state = equipment_events[inner]
                if local_state:
                    on_seconds += max(0.0, event_ts - cursor)
                cursor = max(cursor, event_ts)
                local_state = next_state
                inner += 1
            if local_state:
                on_seconds += max(0.0, end - cursor)
            duty[index] = min(1.0, max(0.0, on_seconds / STEP_SECONDS))
            pointer = inner
            state = local_state
        telemetry.actions[equipment] = duty


def contiguous_mask(telemetry: Telemetry) -> np.ndarray:
    return np.isclose(np.diff(telemetry.epoch), STEP_SECONDS, atol=1.0)


def in_window(telemetry: Telemetry, start: datetime, end: datetime) -> np.ndarray:
    lo, hi = start.timestamp(), end.timestamp()
    return (telemetry.epoch >= lo) & (telemetry.epoch < hi)


FEATURE_NAMES = (
    "temp_f",
    "abs_humidity_g_m3",
    "temp_out_delta_f",
    "humidity_out_delta_g_m3",
    "outdoor_temp_f",
    "outdoor_abs_humidity_g_m3",
    "solar_kw_m2",
    "wind_mph",
    "sin_hour",
    "cos_hour",
    "sin_year",
    "cos_year",
    *MODEL_EQUIPMENT,
    "vent_temp_exchange",
    "fan_temp_exchange",
    "vent_moisture_exchange",
    "fan_moisture_exchange",
    "wet_duty",
    "heat_duty",
)


def feature_row(
    when: datetime,
    state: np.ndarray,
    outdoor_temp: float,
    outdoor_abs_humidity: float,
    solar: float,
    wind: float,
    actions: dict[str, float],
) -> np.ndarray:
    hour = when.hour + when.minute / 60.0
    day = when.timetuple().tm_yday + hour / 24.0
    temp_delta = state[0] - outdoor_temp
    humidity_delta = state[1] - outdoor_abs_humidity
    vent = actions["vent"]
    fans = max(actions["fan1"], actions["fan2"])
    wet = actions["fog"] + actions["mister_south"] + actions["mister_west"] + actions["mister_center"]
    heat = actions["heat1"] + actions["heat2"]
    return np.asarray(
        [
            state[0],
            state[1],
            temp_delta,
            humidity_delta,
            outdoor_temp,
            outdoor_abs_humidity,
            solar / 1000.0,
            wind,
            math.sin(2.0 * math.pi * hour / 24.0),
            math.cos(2.0 * math.pi * hour / 24.0),
            math.sin(2.0 * math.pi * day / 365.25),
            math.cos(2.0 * math.pi * day / 365.25),
            *(actions[name] for name in MODEL_EQUIPMENT),
            vent * temp_delta,
            fans * temp_delta,
            vent * humidity_delta,
            fans * humidity_delta,
            wet,
            heat,
        ],
        dtype=float,
    )


def row_context(telemetry: Telemetry, index: int) -> tuple[float, float, float, float]:
    outdoor_temp = telemetry.columns["outdoor_temp_f"][index]
    outdoor_rh = telemetry.columns["outdoor_rh_pct"][index]
    outdoor_ah = absolute_humidity_g_m3(outdoor_temp, outdoor_rh)
    return outdoor_temp, float(outdoor_ah), telemetry.columns["solar_w_m2"][index], telemetry.columns["wind_mph"][index]


def observed_actions(telemetry: Telemetry, index: int) -> dict[str, float]:
    return {name: float(telemetry.actions[name][index]) for name in MODEL_EQUIPMENT}


def build_training_matrix(telemetry: Telemetry, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    continuity = contiguous_mask(telemetry)
    indices = np.flatnonzero(mask[:-1] & mask[1:] & continuity)
    rows: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    state = np.column_stack((telemetry.columns["temp_f"], telemetry.columns["abs_humidity_g_m3"]))
    for index in indices:
        context = row_context(telemetry, index)
        rows.append(feature_row(telemetry.times[index], state[index], *context, observed_actions(telemetry, index)))
        targets.append(state[index + 1] - state[index])
    x = np.asarray(rows)
    y = np.asarray(targets)
    finite = np.isfinite(x).all(axis=1) & np.isfinite(y).all(axis=1)
    return x[finite], y[finite], indices[finite]


class DynamicsModel:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.scaler = StandardScaler()
        self.models: list[Any] = []
        self.delta_bounds = np.zeros((2, 2), dtype=float)

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        scaled = self.scaler.fit_transform(x)
        self.delta_bounds[:, 0] = np.quantile(y, 0.001, axis=0)
        self.delta_bounds[:, 1] = np.quantile(y, 0.999, axis=0)
        if self.kind == "ridge":
            model = Ridge(alpha=10.0)
            model.fit(scaled, y)
            self.models = [model]
        elif self.kind == "hist_gradient_boosting":
            self.models = []
            for axis in range(2):
                model = HistGradientBoostingRegressor(
                    learning_rate=0.05,
                    max_iter=250,
                    max_leaf_nodes=15,
                    min_samples_leaf=25,
                    l2_regularization=2.0,
                    random_state=20260814 + axis,
                )
                model.fit(scaled, y[:, axis])
                self.models.append(model)
        else:
            raise ValueError(f"unknown model kind: {self.kind}")

    def predict_delta(self, row: np.ndarray) -> np.ndarray:
        scaled = self.scaler.transform(row.reshape(1, -1))
        if self.kind == "ridge":
            result = np.asarray(self.models[0].predict(scaled)[0], dtype=float)
        else:
            result = np.asarray([model.predict(scaled)[0] for model in self.models], dtype=float)
        return np.clip(result, self.delta_bounds[:, 0], self.delta_bounds[:, 1])


@dataclass(frozen=True)
class PIDSpec:
    name: str
    temp_target_f: float
    vpd_target_kpa: float
    temp_kp: float
    temp_ki: float
    temp_kd: float
    vpd_kp: float
    vpd_ki: float
    vpd_kd: float


GAIN_PROFILES = {
    "p_only": (0.35, 0.0, 0.0, 2.5, 0.0, 0.0),
    "conservative": (0.20, 0.01, 0.02, 1.5, 0.05, 0.10),
    "balanced": (0.35, 0.02, 0.05, 2.5, 0.10, 0.20),
    "aggressive": (0.55, 0.04, 0.08, 4.0, 0.20, 0.30),
}
TARGETS = {
    "current_spec": (72.0, 0.95),
    "legacy_midpoint": (67.5, 1.15),
    "warm_dry": (76.0, 1.10),
}


class CoordinatedPID:
    """Two PID errors with one deterministic, safety-aware allocator."""

    def __init__(self, spec: PIDSpec) -> None:
        self.spec = spec
        self.temp_integral = 0.0
        self.vpd_integral = 0.0
        self.prev_temp_error = 0.0
        self.prev_vpd_error = 0.0

    @staticmethod
    def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
        return min(high, max(low, value))

    def actions(
        self, state: np.ndarray, outdoor_temp: float, outdoor_ah: float, fixed: dict[str, float]
    ) -> dict[str, float]:
        temp, humidity = float(state[0]), float(state[1])
        vpd = vpd_from_state(temp, humidity)
        temp_error = self.spec.temp_target_f - temp
        # Positive means too dry and calls for wetting.
        vpd_error = vpd - self.spec.vpd_target_kpa
        if abs(temp_error) < 1.0:
            temp_error = 0.0
        if abs(vpd_error) < 0.08:
            vpd_error = 0.0
        self.temp_integral = self._clamp(self.temp_integral + temp_error * 0.25, -10.0, 10.0)
        self.vpd_integral = self._clamp(self.vpd_integral + vpd_error * 0.25, -2.0, 2.0)
        temp_output = self._clamp(
            self.spec.temp_kp * temp_error
            + self.spec.temp_ki * self.temp_integral
            + self.spec.temp_kd * (temp_error - self.prev_temp_error) / 0.25
        )
        wet_output = self._clamp(
            self.spec.vpd_kp * vpd_error
            + self.spec.vpd_ki * self.vpd_integral
            + self.spec.vpd_kd * (vpd_error - self.prev_vpd_error) / 0.25
        )
        self.prev_temp_error = temp_error
        self.prev_vpd_error = vpd_error

        result = {name: 0.0 for name in MODEL_EQUIPMENT}
        for name in EXOGENOUS_EQUIPMENT:
            result[name] = fixed[name]

        heat = max(0.0, temp_output)
        cool = max(0.0, -temp_output)
        wet = max(0.0, wet_output)
        dehumidify = max(0.0, -wet_output)

        # Deterministic coordination: use couplings that help both axes first.
        if temp < 45.0:
            heat = 1.0
            cool = wet = dehumidify = 0.0
        elif temp > 95.0:
            cool = 1.0
            heat = 0.0
        if heat > 0.0:
            result["heat1"] = min(1.0, heat / 0.65)
            result["heat2"] = max(0.0, (heat - 0.65) / 0.35)
        if cool > 0.0:
            result["vent"] = cool
            result["fan1"] = min(1.0, cool / 0.55)
            result["fan2"] = max(0.0, (cool - 0.55) / 0.45)

        dew_margin = temp - dewpoint_f(temp, humidity)
        if wet > 0.0 and dew_margin >= 8.0:
            # Fog first; rotate higher-flow misters only for stage two.
            result["fog"] = min(1.0, wet / 0.70)
            mister_stage = max(0.0, (wet - 0.70) / 0.30) / 3.0
            result["mister_south"] = mister_stage
            result["mister_west"] = mister_stage
            result["mister_center"] = mister_stage
        if dehumidify > 0.0:
            if outdoor_ah + 0.25 < humidity:
                result["vent"] = max(result["vent"], dehumidify)
                result["fan1"] = max(result["fan1"], dehumidify)
            elif temp < 82.0 and result["vent"] == 0.0:
                result["heat1"] = max(result["heat1"], min(1.0, dehumidify))
        return result


def candidate_pid_specs() -> list[PIDSpec]:
    candidates: list[PIDSpec] = []
    for target_name, (temp_target, vpd_target) in TARGETS.items():
        for gain_name, gains in GAIN_PROFILES.items():
            candidates.append(PIDSpec(f"{target_name}:{gain_name}", temp_target, vpd_target, *gains))
    return candidates


def complete_day_starts(telemetry: Telemetry, mask: np.ndarray, anchor_minute_utc: int = 0) -> list[int]:
    continuity = contiguous_mask(telemetry)
    starts: list[int] = []
    for index in np.flatnonzero(mask):
        minute_utc = telemetry.times[index].hour * 60 + telemetry.times[index].minute
        if minute_utc != anchor_minute_utc:
            continue
        end = index + 96
        if end <= telemetry.n and mask[index:end].all() and continuity[index : end - 1].all():
            starts.append(index)
    return starts


def simulate_day(
    telemetry: Telemetry,
    model: DynamicsModel,
    start: int,
    policy: str,
    pid_spec: PIDSpec | None = None,
) -> dict[str, Any]:
    state = np.asarray([telemetry.columns["temp_f"][start], telemetry.columns["abs_humidity_g_m3"][start]])
    states = [state.copy()]
    action_rows: list[dict[str, float]] = []
    feature_rows: list[np.ndarray] = []
    pid = CoordinatedPID(pid_spec) if policy == "pid" and pid_spec is not None else None
    for index in range(start, start + 95):
        context = row_context(telemetry, index)
        actual = observed_actions(telemetry, index)
        if pid is None:
            actions = actual
        else:
            actions = pid.actions(state, context[0], context[1], actual)
        row = feature_row(telemetry.times[index], state, *context, actions)
        feature_rows.append(row)
        state = state + model.predict_delta(row)
        state[0] = np.clip(state[0], 35.0, 115.0)
        state[1] = np.clip(state[1], 1.0, 35.0)
        states.append(state.copy())
        action_rows.append(actions)
    # The final bucket's action contributes runtime but not a modeled next state.
    last_actual = observed_actions(telemetry, start + 95)
    if pid is None:
        action_rows.append(last_actual)
    else:
        context = row_context(telemetry, start + 95)
        action_rows.append(pid.actions(state, context[0], context[1], last_actual))
    return {"states": np.asarray(states), "actions": action_rows, "features": np.asarray(feature_rows)}


def observed_day(telemetry: Telemetry, start: int) -> dict[str, Any]:
    states = np.column_stack(
        (
            telemetry.columns["temp_f"][start : start + 96],
            telemetry.columns["abs_humidity_g_m3"][start : start + 96],
        )
    )
    actions = [observed_actions(telemetry, index) for index in range(start, start + 96)]
    return {"states": states, "actions": actions}


def open_loop_pid_day(telemetry: Telemetry, start: int, pid_spec: PIDSpec) -> dict[str, Any]:
    """Run PID decisions against factual indoor state without changing that state.

    This identifies controller-decision divergence and requested duty only. It
    is intentionally not labeled as a physical outcome counterfactual.
    """

    states = observed_day(telemetry, start)["states"]
    pid = CoordinatedPID(pid_spec)
    action_rows: list[dict[str, float]] = []
    for offset, state in enumerate(states):
        index = start + offset
        context = row_context(telemetry, index)
        actual = observed_actions(telemetry, index)
        action_rows.append(pid.actions(state, context[0], context[1], actual))
    return {"states": states, "actions": action_rows}


def summarize_day(telemetry: Telemetry, start: int, trajectory: dict[str, Any]) -> dict[str, float]:
    states = trajectory["states"]
    temp = states[:, 0]
    vpd = vpd_from_state(temp, states[:, 1])
    sl = slice(start, start + len(states))
    temp_low = telemetry.columns["eval_temp_low_f"][sl]
    temp_high = telemetry.columns["eval_temp_high_f"][sl]
    vpd_low = telemetry.columns["eval_vpd_low_kpa"][sl]
    vpd_high = telemetry.columns["eval_vpd_high_kpa"][sl]
    temp_distance = np.maximum(temp_low - temp, 0.0) + np.maximum(temp - temp_high, 0.0)
    vpd_distance = np.maximum(vpd_low - vpd, 0.0) + np.maximum(vpd - vpd_high, 0.0)
    result = {
        "temp_compliance_pct": float(100.0 * np.mean(temp_distance <= 1e-9)),
        "vpd_compliance_pct": float(100.0 * np.mean(vpd_distance <= 1e-9)),
        "temp_degree_hours_outside": float(np.sum(temp_distance) * STEP_MINUTES / 60.0),
        "vpd_kpa_hours_outside": float(np.sum(vpd_distance) * STEP_MINUTES / 60.0),
    }
    for name in CLIMATE_EQUIPMENT:
        result[f"runtime_{name}_min"] = float(sum(row[name] for row in trajectory["actions"]) * STEP_MINUTES)
    result["climate_actuator_minutes"] = sum(result[f"runtime_{name}_min"] for name in CLIMATE_EQUIPMENT)
    for bound_index, label in enumerate(("low", "nominal", "high")):
        kwh = 0.0
        for name, watts in ENERGY_WATTS.items():
            kwh += result[f"runtime_{name}_min"] / 60.0 * watts[bound_index] / 1000.0
        result[f"modeled_electric_kwh_{label}"] = kwh
        result[f"modeled_gas_therms_{label}"] = result["runtime_heat2_min"] / 60.0 * HEAT2_BTU_H[bound_index] / 100000.0
    return result


def aggregate_metric(rows: list[dict[str, float]], metric: str) -> float:
    return float(np.mean([row[metric] for row in rows]))


def bootstrap_mean_ci(values: np.ndarray, seed: int = 20260814, draws: int = 10000) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    if len(values) == 0:
        return {"mean": math.nan, "low_95": math.nan, "high_95": math.nan, "probability_gt_zero": math.nan}
    sampled = rng.choice(values, size=(draws, len(values)), replace=True).mean(axis=1)
    return {
        "mean": float(np.mean(values)),
        "low_95": float(np.quantile(sampled, 0.025)),
        "high_95": float(np.quantile(sampled, 0.975)),
        "probability_gt_zero": float(np.mean(sampled > 0.0)),
    }


def validate_model(telemetry: Telemetry, model: DynamicsModel, starts: list[int]) -> dict[str, Any]:
    temp_errors: list[np.ndarray] = []
    vpd_errors: list[np.ndarray] = []
    persistence_temp: list[np.ndarray] = []
    persistence_vpd: list[np.ndarray] = []
    daily: list[dict[str, float]] = []
    for start in starts:
        actual = observed_day(telemetry, start)["states"]
        replay = simulate_day(telemetry, model, start, "actual")["states"]
        actual_vpd = vpd_from_state(actual[:, 0], actual[:, 1])
        replay_vpd = vpd_from_state(replay[:, 0], replay[:, 1])
        temp_error = np.abs(replay[:, 0] - actual[:, 0])
        vpd_error = np.abs(replay_vpd - actual_vpd)
        temp_errors.append(temp_error)
        vpd_errors.append(vpd_error)
        persistence_temp.append(np.abs(actual[0, 0] - actual[:, 0]))
        persistence_vpd.append(np.abs(actual_vpd[0] - actual_vpd))
        daily.append({"temp_mae_f": float(temp_error.mean()), "vpd_mae_kpa": float(vpd_error.mean())})
    temp_all = np.concatenate(temp_errors)
    vpd_all = np.concatenate(vpd_errors)
    p_temp = np.concatenate(persistence_temp)
    p_vpd = np.concatenate(persistence_vpd)
    horizon_metrics: dict[str, dict[str, float]] = {}
    for name, offset in (("1h", 4), ("6h", 24), ("24h", 95)):
        horizon_metrics[name] = {
            "temp_mae_f": float(np.mean([row[offset] for row in temp_errors])),
            "vpd_mae_kpa": float(np.mean([row[offset] for row in vpd_errors])),
            "persistence_temp_mae_f": float(np.mean([row[offset] for row in persistence_temp])),
            "persistence_vpd_mae_kpa": float(np.mean([row[offset] for row in persistence_vpd])),
        }
    gate = bool(
        temp_all.mean() <= 2.5
        and vpd_all.mean() <= 0.25
        and temp_all.mean() < p_temp.mean()
        and vpd_all.mean() < p_vpd.mean()
    )
    return {
        "days": len(starts),
        "rollout_temp_mae_f": float(temp_all.mean()),
        "rollout_vpd_mae_kpa": float(vpd_all.mean()),
        "persistence_temp_mae_f": float(p_temp.mean()),
        "persistence_vpd_mae_kpa": float(p_vpd.mean()),
        "horizons": horizon_metrics,
        "daily": daily,
        "gate_thresholds": {"temp_mae_f_max": 2.5, "vpd_mae_kpa_max": 0.25, "must_beat_persistence": True},
        "passes": gate,
    }


def tune_pid(
    telemetry: Telemetry, models: list[DynamicsModel], starts: list[int]
) -> tuple[PIDSpec, list[dict[str, Any]]]:
    candidates = candidate_pid_specs()
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        model_scores: list[float] = []
        for model in models:
            day_scores: list[float] = []
            for start in starts:
                metrics = summarize_day(telemetry, start, simulate_day(telemetry, model, start, "pid", candidate))
                normalized_loss = (
                    metrics["temp_degree_hours_outside"] / 120.0
                    + metrics["vpd_kpa_hours_outside"] / 8.0
                    + metrics["climate_actuator_minutes"] / 20000.0
                )
                day_scores.append(normalized_loss)
            model_scores.append(float(np.mean(day_scores)))
        scored.append({"name": candidate.name, "score": float(np.mean(model_scores)), "model_scores": model_scores})
    scored.sort(key=lambda row: row["score"])
    winner_name = scored[0]["name"]
    winner = next(candidate for candidate in candidates if candidate.name == winner_name)
    return winner, scored


def open_loop_sensitivity(telemetry: Telemetry, starts: list[int], selected: PIDSpec) -> dict[str, Any]:
    observed = [summarize_day(telemetry, start, observed_day(telemetry, start)) for start in starts]
    factual_runtime = aggregate_metric(observed, "climate_actuator_minutes")
    candidates: list[dict[str, Any]] = []
    for spec in candidate_pid_specs():
        rows = [summarize_day(telemetry, start, open_loop_pid_day(telemetry, start, spec)) for start in starts]
        candidates.append(
            {
                "name": spec.name,
                "requested_climate_actuator_minutes_per_day": aggregate_metric(rows, "climate_actuator_minutes"),
                "requested_modeled_electric_kwh_nominal_per_day": aggregate_metric(
                    rows, "modeled_electric_kwh_nominal"
                ),
                "requested_modeled_gas_therms_nominal_per_day": aggregate_metric(rows, "modeled_gas_therms_nominal"),
            }
        )
    selected_row = next(row for row in candidates if row["name"] == selected.name)
    return {
        "warning": "Observed-state/open-loop replay: requested duty only; indoor trajectory is held factual.",
        "executed_climate_actuator_minutes_per_day": factual_runtime,
        "selected_pid": selected_row,
        "candidate_min_requested_minutes_per_day": min(
            row["requested_climate_actuator_minutes_per_day"] for row in candidates
        ),
        "candidate_max_requested_minutes_per_day": max(
            row["requested_climate_actuator_minutes_per_day"] for row in candidates
        ),
        "candidates": candidates,
    }


def support_fraction(
    train_x: np.ndarray,
    telemetry: Telemetry,
    model: DynamicsModel,
    starts: list[int],
    pid_spec: PIDSpec,
) -> dict[str, float]:
    scaler = StandardScaler().fit(train_x)
    train_scaled = scaler.transform(train_x)
    neighbors = NearestNeighbors(n_neighbors=5).fit(train_scaled)
    actual_features: list[np.ndarray] = []
    pid_features: list[np.ndarray] = []
    for start in starts:
        actual = simulate_day(telemetry, model, start, "actual")["features"]
        pid = simulate_day(telemetry, model, start, "pid", pid_spec)["features"]
        actual_features.extend(actual)
        pid_features.extend(pid)
    actual_distance = neighbors.kneighbors(scaler.transform(np.asarray(actual_features)))[0][:, -1]
    pid_distance = neighbors.kneighbors(scaler.transform(np.asarray(pid_features)))[0][:, -1]
    threshold = float(np.quantile(actual_distance, 0.99))
    return {
        "threshold_actual_validation_p99": threshold,
        "actual_in_support_pct": float(100.0 * np.mean(actual_distance <= threshold)),
        "pid_in_support_pct": float(100.0 * np.mean(pid_distance <= threshold)),
        "passes": bool(np.mean(pid_distance <= threshold) >= 0.90),
    }


def residual_diagnostics(model: DynamicsModel, x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    predicted = np.asarray([model.predict_delta(row) for row in x])
    residual = y - predicted
    lag1 = []
    for axis in range(2):
        lag1.append(float(np.corrcoef(residual[:-1, axis], residual[1:, axis])[0, 1]))
    return {
        "one_step_temp_mae_f": float(np.mean(np.abs(residual[:, 0]))),
        "one_step_abs_humidity_mae_g_m3": float(np.mean(np.abs(residual[:, 1]))),
        "residual_lag1": {"temp": lag1[0], "abs_humidity": lag1[1]},
        "passes": bool(max(abs(value) for value in lag1) < 0.50),
    }


def compare_policy(
    telemetry: Telemetry,
    model: DynamicsModel,
    starts: list[int],
    pid_spec: PIDSpec,
) -> dict[str, Any]:
    actual_observed: list[dict[str, float]] = []
    actual_modeled: list[dict[str, float]] = []
    pid_modeled: list[dict[str, float]] = []
    for start in starts:
        actual_observed.append(summarize_day(telemetry, start, observed_day(telemetry, start)))
        actual_modeled.append(summarize_day(telemetry, start, simulate_day(telemetry, model, start, "actual")))
        pid_modeled.append(summarize_day(telemetry, start, simulate_day(telemetry, model, start, "pid", pid_spec)))
    metrics = tuple(actual_observed[0])
    aggregate: dict[str, Any] = {}
    day_effects: dict[str, Any] = {}
    for metric in metrics:
        observed_mean = aggregate_metric(actual_observed, metric)
        replay_mean = aggregate_metric(actual_modeled, metric)
        pid_mean = aggregate_metric(pid_modeled, metric)
        effects = np.asarray(
            [pid[metric] - replay[metric] for pid, replay in zip(pid_modeled, actual_modeled, strict=True)]
        )
        aggregate[metric] = {
            "executed_observed_mean_per_day": observed_mean,
            "executed_model_replay_mean_per_day": replay_mean,
            "pid_model_mean_per_day": pid_mean,
            "pid_minus_executed_model": float(np.mean(effects)),
        }
        day_effects[metric] = bootstrap_mean_ci(effects)
    return {"aggregate": aggregate, "paired_day_bootstrap": day_effects}


def plan_summary(path: Path, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            created_at = parse_ts(row["created_at"])
            if start is not None and created_at < start:
                continue
            if end is not None and created_at >= end:
                continue
            rows.append(row)
    scored = [row for row in rows if row["outcome_score"] and row["anchor_score"]]
    return {
        "plans": len(rows),
        "lifecycle": dict(
            sorted(
                defaultdict(
                    int,
                    {
                        key: sum(row["lifecycle_status"] == key for row in rows)
                        for key in {row["lifecycle_status"] for row in rows}
                    },
                ).items()
            )
        ),
        "validated": sum(bool(row["validated_at"]) for row in rows),
        "climate_intent": sum(row["has_climate_intent"].lower() in {"t", "true", "1"} for row in rows),
        "structured_hypothesis": sum(row["has_structured_hypothesis"].lower() in {"t", "true", "1"} for row in rows),
        "self_score_mean": float(np.mean([float(row["outcome_score"]) for row in scored])) if scored else None,
        "anchor_score_mean": float(np.mean([float(row["anchor_score"]) for row in scored])) if scored else None,
        "self_anchor_mean_abs_difference": (
            float(np.mean([abs(float(row["outcome_score"]) - float(row["anchor_score"])) for row in scored]))
            if scored
            else None
        ),
        "score_pair_count": len(scored),
        "score_warning": "Journal scores are workflow diagnostics, not causal efficacy outcomes.",
    }


def strong_window_daily_summary(path: Path, start: date, end: date) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            day = datetime.fromisoformat(row["date"]).date()
            if start <= day < end:
                rows.append(row)

    def numbers(name: str) -> list[float]:
        return [maybe_float(row[name]) for row in rows if math.isfinite(maybe_float(row[name]))]

    runtime_fields = (
        "runtime_fan1_min",
        "runtime_fan2_min",
        "runtime_heat1_min",
        "runtime_heat2_min",
        "runtime_fog_min",
        "runtime_vent_min",
    )
    cycle_fields = ("cycles_fan1", "cycles_fan2", "cycles_heat1", "cycles_heat2", "cycles_fog", "cycles_vent")
    six_core_runtime_sum = sum(sum(numbers(name)) for name in runtime_fields)
    runtime_sum = six_core_runtime_sum + 60.0 * sum(
        sum(numbers(name)) for name in ("runtime_mister_south_h", "runtime_mister_west_h", "runtime_mister_center_h")
    )
    return {
        "days": len(rows),
        "attributable_compliance_pct_mean": float(np.mean(numbers("compliance_v2_attributable_pct"))),
        "graded_temp_compliance_pct_mean": float(np.mean(numbers("graded_temp_compliance_pct"))),
        "graded_vpd_compliance_pct_mean": float(np.mean(numbers("graded_vpd_compliance_pct"))),
        "graded_stress_hours_total": float(sum(numbers("graded_stress_hours"))),
        "six_core_runtime_minutes_total": six_core_runtime_sum,
        "climate_runtime_including_misters_minutes_total": runtime_sum,
        "core_cycles_total": int(sum(sum(numbers(name)) for name in cycle_fields)),
        "water_meter_scoring_eligible_days": sum(
            row["meter_available_for_scoring"].lower() in {"t", "true", "1"} for row in rows
        ),
        "water_attribution_scoring_eligible_days": sum(
            row["water_eligible"].lower() in {"t", "true", "1"} for row in rows
        ),
        "quality_filtered_meter_gallons_total": float(sum(numbers("quality_filtered_meter_gal"))),
        "water_meter_gap_events": int(sum(numbers("meter_gap_events"))),
        "water_meter_reset_events": int(sum(numbers("meter_reset_events"))),
        "energy_scoring_eligible_days": sum(row["energy_eligible"].lower() in {"t", "true", "1"} for row in rows),
        "runtime_modeled_kwh_total": float(sum(numbers("modeled_kwh"))),
        "warning": "Factual aggregates only; daily metrics and resource scopes retain their database quality limits.",
    }


def standardized_mean_differences(left: np.ndarray, right: np.ndarray) -> list[float]:
    pooled = np.sqrt((np.var(left, axis=0, ddof=1) + np.var(right, axis=0, ddof=1)) / 2.0)
    pooled[pooled == 0.0] = 1.0
    return [float(value) for value in ((np.mean(left, axis=0) - np.mean(right, axis=0)) / pooled)]


def historical_weather_match(telemetry: Telemetry) -> dict[str, Any]:
    full_mask = in_window(telemetry, parse_ts("2025-08-15T06:00:00+00:00"), parse_ts("2026-08-14T06:00:00+00:00"))
    starts = complete_day_starts(telemetry, full_mask, 6 * 60)
    records: list[dict[str, Any]] = []
    for start in starts:
        sl = slice(start, start + 96)
        solar_coverage = float(np.mean(telemetry.columns["solar_observed"][sl]))
        if solar_coverage < 0.40:
            continue
        outdoor_temp = telemetry.columns["outdoor_temp_f"][sl]
        outdoor_rh = telemetry.columns["outdoor_rh_pct"][sl]
        weather = np.asarray(
            [
                np.mean(outdoor_temp),
                np.max(outdoor_temp) - np.min(outdoor_temp),
                np.sum(telemetry.columns["solar_w_m2"][sl]) * STEP_MINUTES / 60.0 / 1000.0,
                np.mean(absolute_humidity_g_m3(outdoor_temp, outdoor_rh)),
                np.mean(telemetry.columns["wind_mph"][sl]),
            ],
            dtype=float,
        )
        records.append(
            {
                "start": telemetry.times[start],
                "weather": weather,
                "outcomes": summarize_day(telemetry, start, observed_day(telemetry, start)),
            }
        )
    boundary = parse_ts("2026-03-24T06:00:00+00:00")
    pre = [row for row in records if row["start"] < boundary]
    ai = [row for row in records if row["start"] >= boundary]
    pre_weather = np.asarray([row["weather"] for row in pre])
    ai_weather = np.asarray([row["weather"] for row in ai])
    scaler = StandardScaler().fit(pre_weather)
    neighbor = NearestNeighbors(n_neighbors=1).fit(scaler.transform(pre_weather))
    distances, indices = neighbor.kneighbors(scaler.transform(ai_weather))
    matched = [pre[index] for index in indices[:, 0]]
    matched_control_counts: dict[int, int] = defaultdict(int)
    for index in indices[:, 0]:
        matched_control_counts[int(index)] += 1
    matched_weather = np.asarray([row["weather"] for row in matched])
    outcome_names = (
        "climate_actuator_minutes",
        "temp_compliance_pct",
        "vpd_compliance_pct",
        "temp_degree_hours_outside",
        "vpd_kpa_hours_outside",
    )
    outcomes: dict[str, Any] = {}
    for name in outcome_names:
        paired = np.asarray(
            [ai_row["outcomes"][name] - pre_row["outcomes"][name] for ai_row, pre_row in zip(ai, matched, strict=True)]
        )
        outcomes[name] = {"descriptive_mean": float(np.mean(paired))}
    weather_names = ("outdoor_temp_mean_f", "outdoor_temp_range_f", "solar_kwh_m2", "outdoor_ah", "wind_mph")
    return {
        "warning": (
            "Nearest-weather historical comparison is descriptive only: firmware, crop, season, sensors, and the "
            "open-window configuration remain uncontrolled; pre-AI solar is partly clear-sky-imputed."
        ),
        "pre_ai_days": len(pre),
        "ai_days": len(ai),
        "matching_with_replacement": True,
        "unique_matched_control_days": len(matched_control_counts),
        "maximum_control_day_reuse": max(matched_control_counts.values()),
        "inference": (
            "Not computed: control-day reuse and serial dependence make an IID paired-day bootstrap invalid."
        ),
        "solar_coverage_required_pct": 40.0,
        "nearest_neighbor_distance_median": float(np.median(distances)),
        "nearest_neighbor_distance_p95": float(np.quantile(distances, 0.95)),
        "weather_smd_before": dict(
            zip(weather_names, standardized_mean_differences(ai_weather, pre_weather), strict=True)
        ),
        "weather_smd_after": dict(
            zip(weather_names, standardized_mean_differences(ai_weather, matched_weather), strict=True)
        ),
        "ai_minus_matched_pre": outcomes,
    }


def model_counterfactual_eligible(model_results: dict[str, Any], direction_stable: dict[str, bool]) -> bool:
    """Apply the implemented model, residual, support, and direction gates."""

    headline_metrics = ("climate_actuator_minutes", "temp_degree_hours_outside", "vpd_kpa_hours_outside")
    model_gates_pass = all(
        result["validation"]["passes"] and result["residuals"]["passes"] and result["support"]["passes"]
        for result in model_results.values()
    )
    return bool(model_gates_pass and all(direction_stable[name] for name in headline_metrics))


def run(args: argparse.Namespace) -> dict[str, Any]:
    climate_path = Path(args.climate)
    equipment_path = Path(args.equipment)
    daily_path = Path(args.daily)
    plans_path = Path(args.plans)
    telemetry = load_climate(climate_path)
    attach_equipment_duty(telemetry, equipment_path)

    train_start = parse_ts(args.train_start)
    train_end = parse_ts(args.train_end)
    eval_start = parse_ts(args.eval_start)
    eval_end = parse_ts(args.eval_end)
    factual_start = date.fromisoformat(args.factual_start)
    factual_end = date.fromisoformat(args.factual_end)
    plan_start = parse_ts(args.plan_start) if args.plan_start else None
    plan_end = parse_ts(args.plan_end) if args.plan_end else None
    train_mask = in_window(telemetry, train_start, train_end)
    eval_mask = in_window(telemetry, eval_start, eval_end)
    train_x, train_y, _ = build_training_matrix(telemetry, train_mask)
    eval_x, eval_y, _ = build_training_matrix(telemetry, eval_mask)
    eval_anchor = eval_start.hour * 60 + eval_start.minute
    train_anchor = train_start.hour * 60 + train_start.minute
    eval_starts = complete_day_starts(telemetry, eval_mask, eval_anchor)
    calibration_starts = complete_day_starts(telemetry, train_mask, train_anchor)[-7:]
    if len(eval_starts) < 7 or len(calibration_starts) < 3:
        raise ValueError("insufficient complete days for train/evaluation windows")

    models: list[DynamicsModel] = []
    model_results: dict[str, Any] = {}
    for kind in ("ridge", "hist_gradient_boosting"):
        model = DynamicsModel(kind)
        model.fit(train_x, train_y)
        models.append(model)
        model_results[kind] = {
            "validation": validate_model(telemetry, model, eval_starts),
            "residuals": residual_diagnostics(model, eval_x, eval_y),
        }

    pid_spec, tuning = tune_pid(telemetry, models, calibration_starts)
    effect_signs: dict[str, list[float]] = defaultdict(list)
    for model in models:
        support = support_fraction(train_x, telemetry, model, eval_starts, pid_spec)
        comparison = compare_policy(telemetry, model, eval_starts, pid_spec)
        model_results[model.kind]["support"] = support
        model_results[model.kind]["comparison"] = comparison
        for metric, row in comparison["aggregate"].items():
            effect_signs[metric].append(row["pid_minus_executed_model"])

    direction_stable = {
        metric: bool(all(value >= 0 for value in values) or all(value <= 0 for value in values))
        for metric, values in effect_signs.items()
    }
    all_gates = model_counterfactual_eligible(model_results, direction_stable)
    open_loop = open_loop_sensitivity(telemetry, eval_starts, pid_spec)
    historical_match = None if args.skip_historical_match else historical_weather_match(telemetry)

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "study": {
            "estimand": "PID-C minus executed-policy outcome under common observed weather, conditional on accepted plant models",
            "step_minutes": STEP_MINUTES,
            "train_start": train_start.isoformat(),
            "train_end": train_end.isoformat(),
            "evaluation_start": eval_start.isoformat(),
            "evaluation_end": eval_end.isoformat(),
            "evaluation_complete_days": len(eval_starts),
            "model_training_rows": len(train_x),
            "model_evaluation_rows": len(eval_x),
            "primary_era": args.era_label,
            "firmware_version": args.firmware_version,
            "firmware_epoch_start": args.firmware_epoch_start,
            "factual_start_date": factual_start.isoformat(),
            "factual_end_date_exclusive": factual_end.isoformat(),
        },
        "inputs": {
            "climate": file_manifest(climate_path),
            "equipment": file_manifest(equipment_path),
            "daily": file_manifest(daily_path),
            "plans": file_manifest(plans_path),
        },
        "imputation_counts": telemetry.imputation_counts,
        "pid": {
            "selected_on_training_only": True,
            "selected": pid_spec.__dict__,
            "selection_status": (
                "Nominal audit specification ranked on calibration data through model classes that later failed "
                "validation; not a validated optimum."
            ),
            "candidate_ranking": tuning,
            "sample_period_minutes": STEP_MINUTES,
            "anti_windup": "clamped integrators",
            "safety": "45F heat and 95F cool preemption; 8F dew-margin wet block; coordinated allocator",
        },
        "open_loop_decision_replay": open_loop,
        "models": model_results,
        "direction_stable_across_models": direction_stable,
        "counterfactual_eligible": all_gates,
        "interpretation": (
            "eligible conditional engineering estimate; not randomized causal proof"
            if all_gates
            else "not estimable: at least one declared validation/support/robustness gate failed"
        ),
        "plans": plan_summary(plans_path, plan_start, plan_end),
        "factual_strong_window": strong_window_daily_summary(daily_path, factual_start, factual_end),
        "historical_observed_weather_match": historical_match,
        "resource_evidence": {
            "electricity": "runtime-modeled low/nominal/high; production marks all days scoring-ineligible",
            "gas": "nameplate runtime model, not metered",
            "water": "counterfactual gallons not estimated; eligible factual meter attribution exists only for a subset of days",
            "crop_or_yield": "not measured",
        },
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--climate", required=True)
    parser.add_argument("--equipment", required=True)
    parser.add_argument("--daily", required=True)
    parser.add_argument("--plans", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-start", default="2026-06-20T06:00:00+00:00")
    parser.add_argument("--train-end", default="2026-07-10T06:00:00+00:00")
    parser.add_argument("--eval-start", default="2026-07-15T06:00:00+00:00")
    parser.add_argument("--eval-end", default="2026-08-14T06:00:00+00:00")
    parser.add_argument("--factual-start", default="2026-07-15")
    parser.add_argument("--factual-end", default="2026-08-14")
    parser.add_argument("--plan-start")
    parser.add_argument("--plan-end")
    parser.add_argument("--firmware-version")
    parser.add_argument("--firmware-epoch-start")
    parser.add_argument("--era-label", default="open screen/window; stable firmware after 2026-07-10")
    parser.add_argument("--skip-historical-match", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counterfactual_eligible": result["counterfactual_eligible"]}))


if __name__ == "__main__":
    main()
