"""desired_write_rule(): architecture §5.3 step 1 without the anchor-row check (VD-33, VD-67).

The dispatcher skips desired_state rows this rule refuses, and the settings
services return ``read_only`` (or ``unknown_target``) for them, so both run
one copy of the rule.
"""

from __future__ import annotations

import pytest

from verdify_schemas.tunable_registry import (
    BAND_OWNER_SWITCH,
    IRRIGATION_SITE_PARAMS,
    LEGACY_WIDE_LIMIT_PARAMS,
    READ_ONLY_REASONS,
    REGISTRY,
    WRITABLE_CONTROL_CLASSES,
    ZONE_VPD_TARGET_PARAMS,
    desired_write_kind,
    desired_write_rule,
)

# VD-47 retired these rows with their routes, so the rule refuses them as unknown names.
WEEKLY_FEED_ROWS = (
    "sw_wall_fertigation_commissioned",
    "wall_commissioning_revision",
    "wall_commissioning_evidence_mask",
    "wall_commissioning_valid_until_epoch_s",
    "wall_flow_lpm",
    "wall_flow_valid_until_epoch_s",
    "wall_prewet_liters",
    "wall_feed_liters",
    "wall_flush_liters",
)
# #1165's follow-up retired the L01 bridge's rows and routes once the irrigation engine was last-good.
L01_BRIDGE_ROWS = (
    "sw_irrig_enabled",
    "sw_irrig_wall_enabled",
    "sw_irrig_center_enabled",
    "sw_irrig_south_enabled",
    "sw_irrig_west_enabled",
    "sw_irrigation_enabled",
    "sw_irrigation_wall_enabled",
    "sw_irrigation_center_enabled",
    "sw_irrigation_weather_skip",
    "irrig_wall_duration_min",
    "irrig_center_duration_min",
)


@pytest.mark.parametrize(
    "name",
    [
        "mister_engage_kpa",  # planner_policy
        "gl_main_lux_threshold",  # scheduled_policy
        "gl_main_target_light_minutes",  # per-circuit gl_* rows stay writable: a release hands them back to the policy
        "site_pressure_hpa",  # controller_safety
        "zone_priority_south",  # crop_band; the services check its crop_band_anchors row
        *sorted(IRRIGATION_SITE_PARAMS),
    ],
)
def test_writable_rows(name: str) -> None:
    assert desired_write_rule(name) == (True, None)


@pytest.mark.parametrize("name", ["temp_low", "temp_high", "vpd_low", "vpd_high"])
def test_the_legacy_wide_limits_are_read_only(name: str) -> None:
    writable, reason = desired_write_rule(name)
    assert not writable and reason.startswith("legacy wide limit")


@pytest.mark.parametrize("name", ["vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"])
def test_the_zone_vpd_targets_are_read_only_and_not_legacy(name: str) -> None:
    """The controller still reads vpd_target_<zone> (controls.yaml), so it is not a legacy limit."""
    assert desired_write_kind(name) == "zone_vpd_target"
    assert desired_write_rule(name) == (False, READ_ONLY_REASONS["zone_vpd_target"])


@pytest.mark.parametrize(
    "name",
    [
        "activity_start_hour",
        "activity_start_minute",
        "activity_duration_min",
        "gl_dli_target",
        "gl_lux_threshold",
        "gl_lux_hysteresis",
        "gl_sunrise_hour",
        "gl_sunset_hour",
        "sw_gl_auto_mode",
        "gl_main_dli_target",
        "gl_grow_dli_target",
    ],
)
def test_a_stale_row_naming_a_value_retired_by_1477_is_refused(name: str) -> None:
    """#1477: the activity mirror and the DLI/legacy lighting values left the firmware and the registry."""
    assert name not in REGISTRY
    assert desired_write_rule(name) == (False, READ_ONLY_REASONS["unknown"])


@pytest.mark.parametrize("name", WEEKLY_FEED_ROWS)
def test_a_stale_row_naming_a_retired_weekly_feed_value_is_refused(name: str) -> None:
    assert name not in REGISTRY
    assert desired_write_rule(name) == (False, READ_ONLY_REASONS["unknown"])


@pytest.mark.parametrize("name", L01_BRIDGE_ROWS)
def test_a_stale_row_naming_a_retired_l01_bridge_value_is_refused(name: str) -> None:
    assert name not in REGISTRY
    assert desired_write_rule(name) == (False, READ_ONLY_REASONS["unknown"])


def test_band_ownership_retired_readback_and_unknown_rows_are_not_writable() -> None:
    assert desired_write_rule(BAND_OWNER_SWITCH) == (False, READ_ONLY_REASONS["band_owner"])
    retired = next(name for name, spec in REGISTRY.items() if spec.control_class == "retired")
    assert desired_write_rule(retired) == (False, READ_ONLY_REASONS["retired"])
    assert desired_write_rule("sw_fsm_controller_enabled")[0] is False  # retired, forced on by the dispatcher
    context = next(
        name
        for name, spec in REGISTRY.items()
        if spec.control_class == "readback_context" and name != BAND_OWNER_SWITCH
    )
    assert desired_write_rule(context) == (False, READ_ONLY_REASONS["readback_context"])
    assert desired_write_rule("no_such_setting") == (False, READ_ONLY_REASONS["unknown"])


def test_every_named_read_only_row_is_registered_and_of_a_writable_class() -> None:
    """A typo would leave a row writable; a row of a read-only class needs no entry."""
    assert BAND_OWNER_SWITCH in REGISTRY  # readback_context today; refused by name all the same
    for name in LEGACY_WIDE_LIMIT_PARAMS | ZONE_VPD_TARGET_PARAMS:
        assert name in REGISTRY, name
        assert REGISTRY[name].control_class in WRITABLE_CONTROL_CLASSES, name


def test_the_rule_matches_the_class_split_for_every_row() -> None:
    named = LEGACY_WIDE_LIMIT_PARAMS | ZONE_VPD_TARGET_PARAMS | {BAND_OWNER_SWITCH}
    for name, spec in REGISTRY.items():
        writable, reason = desired_write_rule(name)
        expected = name not in named and (
            spec.control_class == "irrigation_site"
            or (spec.control_class in WRITABLE_CONTROL_CLASSES and spec.esp_object_id is not None)
        )
        assert writable is expected, name
        assert (reason is None) is writable, name
