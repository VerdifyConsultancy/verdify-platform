"""desired_write_rule(): architecture §5.3 step 1 without the anchor-row check (VD-33, VD-67).

The dispatcher skips desired_state rows this rule refuses, and the settings
services return ``read_only`` (or ``unknown_target``) for them, so both run
one copy of the rule.
"""

from __future__ import annotations

import pytest

from verdify_schemas.tunable_registry import (
    DESIRED_READ_ONLY_REASONS,
    DESIRED_WRITABLE_CLASSES,
    IRRIGATION_SITE_PARAMS,
    REGISTRY,
    desired_write_rule,
)

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


@pytest.mark.parametrize(
    "name",
    [
        "mister_engage_kpa",  # planner_policy
        "gl_main_lux_threshold",  # scheduled_policy
        "gl_main_dli_target",  # gl_* rows stay writable: a release hands them back to the policy
        "site_pressure_hpa",  # controller_safety
        "irrig_wall_duration_min",  # stays writable for the L01 bridge
        "sw_irrig_enabled",
        "zone_priority_south",  # crop_band; the services check its crop_band_anchors row
        *sorted(IRRIGATION_SITE_PARAMS),
    ],
)
def test_writable_rows(name: str) -> None:
    assert desired_write_rule(name) == (True, None)


@pytest.mark.parametrize(
    "name",
    ["temp_low", "temp_high", "vpd_low", "vpd_high", "vpd_target_south", "vpd_target_center"],
)
def test_the_legacy_wide_limits_are_read_only(name: str) -> None:
    writable, reason = desired_write_rule(name)
    assert not writable and reason.startswith("legacy wide limit")


@pytest.mark.parametrize("name", ["activity_start_hour", "activity_start_minute", "activity_duration_min"])
def test_the_activity_mirror_is_read_only(name: str) -> None:
    writable, reason = desired_write_rule(name)
    assert not writable and "activity mirror" in reason


@pytest.mark.parametrize("name", WEEKLY_FEED_ROWS)
def test_the_nine_weekly_feed_rows_are_read_only(name: str) -> None:
    writable, reason = desired_write_rule(name)
    assert not writable and "weekly-feed" in reason


def test_band_ownership_retired_readback_and_unknown_rows_are_not_writable() -> None:
    assert desired_write_rule("sw_onchip_band_enabled") == (False, "decides which side owns the band")
    retired = next(name for name, spec in REGISTRY.items() if spec.control_class == "retired")
    assert desired_write_rule(retired) == (False, "retired; the controller no longer reads it")
    assert desired_write_rule("sw_fsm_controller_enabled")[0] is False  # retired, forced on by the dispatcher
    context = next(
        name
        for name, spec in REGISTRY.items()
        if spec.control_class == "readback_context" and name not in DESIRED_READ_ONLY_REASONS
    )
    writable, reason = desired_write_rule(context)
    assert not writable and reason.startswith("readback only")
    assert desired_write_rule("no_such_setting") == (False, "not in the tunable registry")


def test_every_named_read_only_row_is_registered_and_of_a_writable_class() -> None:
    """A typo would leave a row writable; a row of a read-only class needs no entry."""
    assert set(WEEKLY_FEED_ROWS) <= set(DESIRED_READ_ONLY_REASONS)
    for name in DESIRED_READ_ONLY_REASONS:
        assert name in REGISTRY, name
        if name != "sw_onchip_band_enabled":  # readback_context today; listed by name all the same
            assert REGISTRY[name].control_class in DESIRED_WRITABLE_CLASSES, name


def test_the_rule_matches_the_class_split_for_every_row() -> None:
    for name, spec in REGISTRY.items():
        writable, reason = desired_write_rule(name)
        expected = name not in DESIRED_READ_ONLY_REASONS and (
            spec.control_class == "irrigation_site"
            or (spec.control_class in DESIRED_WRITABLE_CLASSES and spec.esp_object_id is not None)
        )
        assert writable is expected, name
        assert (reason is None) is writable, name
