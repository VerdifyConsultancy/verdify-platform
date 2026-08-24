"""Pure contract tests for the 48-grid / 11-treatment executor boundary."""

from __future__ import annotations

from decimal import Decimal

import pytest

from verdify_schemas.component_executor import (
    ACTIVATION_ORDER,
    CANONICAL_FIELD_ORDER,
    COMMON_FIELDS,
    ENTITY_GRIDS,
    RECOVERY_ORDER,
    ROLLBACK_ORDER,
    TREATMENT_FIELD_ORDER,
    ComponentContractError,
    fixed_order_complete_bundle,
    fixed_order_differences,
    normalize_complete_state,
    normalize_component_value,
    physical_execution_qualified,
    validate_routine_target,
    validate_work_phase,
)
from verdify_schemas.tunable_registry import REGISTRY


def minimum_state() -> dict[str, bool | float]:
    return {
        field: False if grid.entity_type == "switch" else float(grid.minimum) for field, grid in ENTITY_GRIDS.items()
    }


def maximum_state() -> dict[str, bool | float]:
    return {
        field: True if grid.entity_type == "switch" else float(grid.maximum) for field, grid in ENTITY_GRIDS.items()
    }


def test_registry_routes_grid_and_treatment_ownership_are_exact() -> None:
    assert tuple(ENTITY_GRIDS) == CANONICAL_FIELD_ORDER
    assert len(CANONICAL_FIELD_ORDER) == 48
    assert len(TREATMENT_FIELD_ORDER) == 11
    assert len(COMMON_FIELDS) == 37
    for field in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field]
        assert definition.esp_object_id
        assert definition.cfg_readback_object_id
        assert definition.wire_id is not None
    for field in TREATMENT_FIELD_ORDER:
        definition = REGISTRY[field]
        assert definition.push_owner == "planner"
        assert definition.planner_pushable is True
        assert definition.tier == 1


def test_provisional_grid_and_prefix_order_cannot_arm_physical_execution() -> None:
    assert physical_execution_qualified("source-grid-r1") is False


def test_every_numeric_grid_accepts_bounds_and_one_step_without_rounding() -> None:
    for field, grid in ENTITY_GRIDS.items():
        if grid.entity_type == "switch":
            assert normalize_component_value(field, False) is False
            assert normalize_component_value(field, True) is True
            with pytest.raises(ComponentContractError, match="wrong_value_type"):
                normalize_component_value(field, 1)
            continue
        assert grid.minimum is not None and grid.maximum is not None and grid.step is not None
        assert normalize_component_value(field, grid.minimum) == float(grid.minimum)
        assert normalize_component_value(field, grid.maximum) == float(grid.maximum)
        if grid.minimum + grid.step <= grid.maximum:
            assert normalize_component_value(field, grid.minimum + grid.step) == float(grid.minimum + grid.step)
        with pytest.raises(ComponentContractError, match="value_off_entity_grid"):
            normalize_component_value(field, grid.minimum + grid.step / Decimal(2))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dwell_gate_ms", 225000),
        ("min_fog_on_s", 59),
        ("mister_all_delay_s", 80),
        ("mister_engage_delay_s", 40),
        ("mister_pulse_gap_s", 38),
        ("vpd_watch_dwell_s", 56),
    ],
)
def test_known_protocol_candidate_discrepancies_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ComponentContractError, match="value_off_entity_grid"):
        normalize_component_value(field, value)


def test_unknown_incomplete_nonfinite_and_clamped_values_fail_closed() -> None:
    state = minimum_state()
    state.pop("dwell_gate_ms")
    with pytest.raises(ComponentContractError, match="incomplete_state"):
        normalize_complete_state(state)
    with pytest.raises(ComponentContractError, match="unknown_component"):
        normalize_component_value("not_a_component", 1)
    with pytest.raises(ComponentContractError, match="non_finite_value"):
        normalize_component_value("mister_all_kpa", float("nan"))
    with pytest.raises(ComponentContractError, match="value_outside_entity_grid"):
        normalize_component_value("mister_all_kpa", 999)


def test_routine_target_may_change_only_treatment_fields() -> None:
    baseline = minimum_state()
    treatment = dict(baseline)
    treatment["mister_all_kpa"] = 1.05
    validate_routine_target(baseline, treatment)

    common = dict(treatment)
    common["cold_vent_guard_delta_f"] = 0.5
    with pytest.raises(ComponentContractError, match="routine_target_changes_common_field"):
        validate_routine_target(baseline, common)


def test_fixed_orders_emit_only_differences_and_recovery_covers_all_48() -> None:
    low = minimum_state()
    high = maximum_state()
    treatment = fixed_order_differences(low, high, order=ACTIVATION_ORDER)
    assert tuple(change.field_name for change in treatment) == ACTIVATION_ORDER
    assert (
        tuple(change.field_name for change in fixed_order_differences(high, low, order=ROLLBACK_ORDER))
        == ROLLBACK_ORDER
    )
    recovery = fixed_order_differences(low, high, order=RECOVERY_ORDER)
    assert tuple(change.field_name for change in recovery) == CANONICAL_FIELD_ORDER
    unconditional = fixed_order_complete_bundle(low, order=RECOVERY_ORDER)
    assert tuple(change.field_name for change in unconditional) == CANONICAL_FIELD_ORDER


def test_every_activation_rollback_and_recovery_prefix_is_exactly_ordered() -> None:
    for order in (ACTIVATION_ORDER, ROLLBACK_ORDER, RECOVERY_ORDER):
        for prefix_length in range(len(order) + 1):
            prefix = order[:prefix_length]
            assert prefix == tuple(order[index] for index in range(prefix_length))
            assert not set(prefix) & set(order[prefix_length:])


def test_phase_kind_pairs_are_closed() -> None:
    validate_work_phase("shadow_preview", "shadow")
    validate_work_phase("commissioning_probe", "commissioning")
    validate_work_phase("commissioning_canary", "commissioning")
    validate_work_phase("aa_baseline_rehearsal", "aa_rehearsal")
    validate_work_phase("randomized_assignment", "randomized")
    validate_work_phase("baseline_recovery", "randomized")
    with pytest.raises(ComponentContractError, match="work_phase_mismatch"):
        validate_work_phase("randomized_assignment", "commissioning")
    with pytest.raises(ComponentContractError, match="unknown_operation_kind"):
        validate_work_phase("assignment", "randomized")
