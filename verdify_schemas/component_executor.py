"""Fail-closed contracts for the confirmed-component experiment executor.

This module deliberately does not know about PostgreSQL, ESPHome clients, or
experiment randomization.  It is the small source-locked boundary between an
L3 resolver and the L1 physical executor:

* the 48-field deployed entity grid is explicit (wire quantization is not an
  entity-grid proof);
* routine work may differ from baseline only on the frozen 11-field allowlist;
* every setter/readback route is required at import time; and
* activation, rollback, and full-recovery orders are stable tuples.

Values are rejected rather than rounded or clamped.  A future deployed-grid
revision must replace this module atomically with its corresponding replay/HIL
evidence; silently deriving steps from the finer policy-wire scale is unsafe.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.tunable_registry import REGISTRY

type ComponentValue = bool | float
type RawComponentValue = bool | int | float | Decimal
type EntityType = Literal["number", "switch"]

GRID_REVISION = "source-grid-parity/main-and-historical-09ee886-live-unverified-v1"
ORDER_REVISION = "candidate-fixed-order-v1-prefix-replay-unqualified"

_QUALIFIED_GRID_REVISION = re.compile(r"^live-entity-grid-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")
_QUALIFIED_ORDER_REVISION = re.compile(r"^prefix-replay-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")


class ComponentContractError(ValueError):
    """A target/state/work contract is unsafe to deliver."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EntityGrid:
    minimum: Decimal | None
    maximum: Decimal | None
    step: Decimal | None
    entity_type: EntityType = "number"


def physical_execution_qualified(grid_revision: str, observed_grid_revision: str | None = None) -> bool:
    """True only for source-bound live-grid and prefix-replay evidence.

    The checked constants deliberately remain provisional for the OFF release.
    Physical work therefore cannot pass this boundary until a reviewed change
    replaces both values with evidence-addressed qualified revisions, L3
    resolves work against that exact grid revision, and the current ingestor
    connection independently attests the same live grid. A source constant or
    arbitrary 64-hex string cannot substitute for current runtime evidence.
    """
    return (
        grid_revision == GRID_REVISION
        and observed_grid_revision == GRID_REVISION
        and _QUALIFIED_GRID_REVISION.fullmatch(GRID_REVISION) is not None
        and _QUALIFIED_ORDER_REVISION.fullmatch(ORDER_REVISION) is not None
    )


def _grid(minimum: str, maximum: str, step: str) -> EntityGrid:
    return EntityGrid(Decimal(minimum), Decimal(maximum), Decimal(step))


_SWITCH = EntityGrid(None, None, None, "switch")

# Permanent wire-id order.  Wire id 6 is retired and intentionally absent.
ENTITY_GRIDS: dict[str, EntityGrid] = {
    "band_track_fraction": _grid("0", "1", "0.05"),
    "cold_vent_guard_delta_f": _grid("0", "15", "0.5"),
    "cool_exit_hysteresis_f": _grid("0.3", "3", "0.1"),
    "cool_stage2_exit_hysteresis_f": _grid("0.3", "3", "0.1"),
    "cool_stage2_over_high_f": _grid("0", "3", "0.1"),
    "direct_wet_stress_min_dew_margin_f": _grid("3", "15", "0.5"),
    "direct_wet_stress_vpd_margin_kpa": _grid("0", "0.5", "0.05"),
    "dwell_gate_ms": _grid("60000", "1800000", "30000"),
    "enthalpy_close": _grid("-5", "20", "0.5"),
    "enthalpy_open": _grid("-5", "0", "0.5"),
    "fog_escalation_kpa": _grid("0.1", "0.5", "0.1"),
    "heat_hysteresis": _grid("0", "3", "0.1"),
    "min_fan_off_s": _grid("30", "300", "10"),
    "min_fan_on_s": _grid("30", "300", "10"),
    "min_fog_off_s": _grid("15", "300", "15"),
    "min_fog_on_s": _grid("15", "300", "15"),
    "min_heat_off_s": _grid("60", "600", "10"),
    "min_heat_on_s": _grid("30", "300", "10"),
    "min_vent_off_s": _grid("10", "300", "10"),
    "min_vent_on_s": _grid("10", "300", "10"),
    "mist_backoff_s": _grid("60", "3600", "60"),
    "mist_max_closed_vent_s": _grid("120", "900", "60"),
    "mist_thermal_relief_s": _grid("30", "300", "30"),
    "mister_all_delay_s": _grid("60", "600", "30"),
    "mister_all_kpa": _grid("1", "2.5", "0.05"),
    "mister_center_penalty": _grid("0", "1", "0.1"),
    "mister_engage_delay_s": _grid("30", "300", "30"),
    "mister_engage_kpa": _grid("0.5", "2.5", "0.05"),
    "mister_min_off_s": _grid("30", "120", "5"),
    "mister_pulse_gap_s": _grid("10", "60", "5"),
    "mister_pulse_on_s": _grid("30", "90", "5"),
    "mister_vpd_weight": _grid("0.5", "3", "0.5"),
    "mister_water_budget_gal": _grid("100", "300", "10"),
    "night_vpd_bias_kpa": _grid("0", "0.25", "0.01"),
    "outdoor_staleness_max_s": _grid("120", "1800", "30"),
    "sw_cool_all_fans_at_high_enabled": _SWITCH,
    "sw_direct_wet_gate_enabled": _SWITCH,
    "sw_direct_wet_stress_override_enabled": _SWITCH,
    "sw_dwell_gate_enabled": _SWITCH,
    "sw_fog_closes_vent": _SWITCH,
    "sw_mister_closes_vent": _SWITCH,
    "sw_summer_vent_enabled": _SWITCH,
    "temp_hysteresis": _grid("0.5", "3", "0.1"),
    "vent_exchange_fraction": _grid("0.1", "0.6", "0.05"),
    "vent_prefer_dp_delta_f": _grid("2", "15", "0.5"),
    "vent_prefer_temp_delta_f": _grid("2", "15", "0.5"),
    "vpd_hysteresis": _grid("0.05", "0.5", "0.05"),
    "vpd_watch_dwell_s": _grid("15", "120", "15"),
}

CANONICAL_FIELD_ORDER: tuple[str, ...] = tuple(field.name for field in wire_fields())

TREATMENT_FIELD_ORDER: tuple[str, ...] = (
    "cool_stage2_over_high_f",
    "sw_cool_all_fans_at_high_enabled",
    "fog_escalation_kpa",
    "min_fog_on_s",
    "min_fog_off_s",
    "mister_engage_kpa",
    "mister_all_kpa",
    "mister_all_delay_s",
    "mister_pulse_gap_s",
    "mister_pulse_on_s",
    "mister_water_budget_gal",
)

# Kept as separate constants so reviewed replay/HIL qualification can replace
# either candidate order without changing caller code.  These are deterministic
# source orders, not a claim that physical prefix replay has passed.
ACTIVATION_ORDER: tuple[str, ...] = TREATMENT_FIELD_ORDER
ROLLBACK_ORDER: tuple[str, ...] = TREATMENT_FIELD_ORDER
RECOVERY_ORDER: tuple[str, ...] = CANONICAL_FIELD_ORDER
COMMON_FIELDS: frozenset[str] = frozenset(CANONICAL_FIELD_ORDER) - frozenset(TREATMENT_FIELD_ORDER)

WORK_KIND_PREVIEW = "shadow_preview"
WORK_KIND_COMMISSIONING_PROBE = "commissioning_probe"
WORK_KIND_COMMISSIONING_CANARY = "commissioning_canary"
WORK_KIND_AA = "aa_baseline_rehearsal"
WORK_KIND_ASSIGNMENT = "randomized_assignment"
WORK_KIND_RECOVERY = "baseline_recovery"

WORK_KIND_PHASES: dict[str, frozenset[str]] = {
    WORK_KIND_PREVIEW: frozenset({"shadow"}),
    WORK_KIND_COMMISSIONING_PROBE: frozenset({"commissioning"}),
    WORK_KIND_COMMISSIONING_CANARY: frozenset({"commissioning"}),
    WORK_KIND_AA: frozenset({"aa_rehearsal"}),
    WORK_KIND_ASSIGNMENT: frozenset({"randomized"}),
    WORK_KIND_RECOVERY: frozenset({"commissioning", "aa_rehearsal", "randomized"}),
}
PHYSICAL_WORK_KINDS: frozenset[str] = frozenset(WORK_KIND_PHASES) - {WORK_KIND_PREVIEW}


@dataclass(frozen=True)
class ComponentChange:
    field_name: str
    object_id: str
    value: ComponentValue
    entity_type: EntityType
    wire_id: int


def _numeric_decimal(field_name: str, value: RawComponentValue) -> Decimal:
    if isinstance(value, bool):
        raise ComponentContractError("wrong_value_type", f"{field_name} requires a number, not bool")
    if isinstance(value, float) and not math.isfinite(value):
        raise ComponentContractError("non_finite_value", field_name)
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ComponentContractError("wrong_value_type", f"{field_name} requires a finite number") from exc
    if not decimal_value.is_finite():
        raise ComponentContractError("non_finite_value", field_name)
    return decimal_value


def normalize_component_value(field_name: str, value: RawComponentValue) -> ComponentValue:
    """Validate one value against the locked deployed entity grid.

    No rounding, clamping, bool coercion, or wire-scale substitution is
    permitted.  The returned float is therefore exactly the accepted decimal
    grid point represented by the JSON/DB input.
    """
    grid = ENTITY_GRIDS.get(field_name)
    if grid is None:
        raise ComponentContractError("unknown_component", field_name)
    if grid.entity_type == "switch":
        if type(value) is not bool:  # noqa: E721 - exact bool rejects integer 0/1
            raise ComponentContractError("wrong_value_type", f"{field_name} requires bool")
        return value

    decimal_value = _numeric_decimal(field_name, value)
    assert grid.minimum is not None and grid.maximum is not None and grid.step is not None
    if decimal_value < grid.minimum or decimal_value > grid.maximum:
        raise ComponentContractError(
            "value_outside_entity_grid",
            f"{field_name}={decimal_value} not in [{grid.minimum},{grid.maximum}]",
        )
    if (decimal_value - grid.minimum) % grid.step != 0:
        raise ComponentContractError(
            "value_off_entity_grid",
            f"{field_name}={decimal_value} step={grid.step} origin={grid.minimum}",
        )
    return float(decimal_value)


def normalize_complete_state(values: Mapping[str, RawComponentValue]) -> dict[str, ComponentValue]:
    expected = frozenset(CANONICAL_FIELD_ORDER)
    provided = frozenset(values)
    if provided != expected:
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        raise ComponentContractError("incomplete_state", f"missing={missing} extra={extra}")
    return {field: normalize_component_value(field, values[field]) for field in CANONICAL_FIELD_ORDER}


def validate_routine_target(
    baseline: Mapping[str, RawComponentValue],
    target: Mapping[str, RawComponentValue],
) -> tuple[dict[str, ComponentValue], dict[str, ComponentValue]]:
    """Return normalized states iff target changes only the 11 treatment fields."""
    normalized_baseline = normalize_complete_state(baseline)
    normalized_target = normalize_complete_state(target)
    common_drift = [
        field
        for field in CANONICAL_FIELD_ORDER
        if field in COMMON_FIELDS and normalized_target[field] != normalized_baseline[field]
    ]
    if common_drift:
        raise ComponentContractError("routine_target_changes_common_field", ",".join(common_drift))
    return normalized_baseline, normalized_target


def fixed_order_differences(
    observed: Mapping[str, RawComponentValue],
    target: Mapping[str, RawComponentValue],
    *,
    order: Sequence[str],
) -> tuple[ComponentChange, ...]:
    """Build an exact setter list from two complete, grid-valid states."""
    normalized_observed = normalize_complete_state(observed)
    normalized_target = normalize_complete_state(target)
    if len(order) != len(frozenset(order)) or not set(order).issubset(ENTITY_GRIDS):
        raise ComponentContractError("invalid_component_order")

    changes: list[ComponentChange] = []
    for field_name in order:
        if normalized_observed[field_name] == normalized_target[field_name]:
            continue
        definition = REGISTRY[field_name]
        grid = ENTITY_GRIDS[field_name]
        if definition.esp_object_id is None or definition.cfg_readback_object_id is None:
            raise ComponentContractError("component_route_missing", field_name)
        if definition.wire_id is None:
            raise ComponentContractError("component_wire_id_missing", field_name)
        changes.append(
            ComponentChange(
                field_name=field_name,
                object_id=definition.esp_object_id,
                value=normalized_target[field_name],
                entity_type=grid.entity_type,
                wire_id=definition.wire_id,
            )
        )
    return tuple(changes)


def fixed_order_complete_bundle(
    target: Mapping[str, RawComponentValue],
    *,
    order: Sequence[str],
) -> tuple[ComponentChange, ...]:
    """Build an unconditional fixed-order bundle over a complete target.

    Baseline recovery uses this surface because reboot/reset/initial state is
    unknown: replaying only apparent differences would preserve an unobserved
    wrong component.  Every requested component is still exact-grid checked.
    """
    normalized_target = normalize_complete_state(target)
    if tuple(order) != CANONICAL_FIELD_ORDER:
        raise ComponentContractError("recovery_order_not_full_canonical")
    changes: list[ComponentChange] = []
    for field_name in order:
        definition = REGISTRY[field_name]
        grid = ENTITY_GRIDS[field_name]
        if definition.esp_object_id is None or definition.cfg_readback_object_id is None:
            raise ComponentContractError("component_route_missing", field_name)
        if definition.wire_id is None:
            raise ComponentContractError("component_wire_id_missing", field_name)
        changes.append(
            ComponentChange(
                field_name=field_name,
                object_id=definition.esp_object_id,
                value=normalized_target[field_name],
                entity_type=grid.entity_type,
                wire_id=definition.wire_id,
            )
        )
    return tuple(changes)


def validate_work_phase(operation_kind: str, execution_phase: str) -> None:
    phases = WORK_KIND_PHASES.get(operation_kind)
    if phases is None:
        raise ComponentContractError("unknown_operation_kind", operation_kind)
    if execution_phase not in phases:
        raise ComponentContractError(
            "work_phase_mismatch",
            f"operation_kind={operation_kind} execution_phase={execution_phase}",
        )


# Import-time fail-closed assertions: a registry drift must prevent executor
# startup, not silently reduce the delivered/observed surface.
if tuple(ENTITY_GRIDS) != CANONICAL_FIELD_ORDER:
    raise RuntimeError("component entity-grid order does not exactly match the 48-field wire registry")
if len(TREATMENT_FIELD_ORDER) != 11 or not set(TREATMENT_FIELD_ORDER).issubset(ENTITY_GRIDS):
    raise RuntimeError("component treatment allowlist must contain exactly 11 canonical fields")
for _field_name in CANONICAL_FIELD_ORDER:
    _definition = REGISTRY[_field_name]
    if _definition.esp_object_id is None or _definition.cfg_readback_object_id is None:
        raise RuntimeError(f"component route incomplete: {_field_name}")
for _field_name in TREATMENT_FIELD_ORDER:
    _definition = REGISTRY[_field_name]
    if _definition.push_owner != "planner" or not _definition.planner_pushable or _definition.tier != 1:
        raise RuntimeError(f"treatment field is not planner-owned Tier 1: {_field_name}")
