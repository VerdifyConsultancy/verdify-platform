"""planner_routing unit tests.

Pure matrix tests over classify_severity + pick_instance + sla_for. No DB,
no HTTP, no ai.yaml dependency — uses module defaults + in-test config
overrides. Run independent of the live stack.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

from planner_routing import (  # noqa: E402
    _DEFAULT_SLA_MIN,
    RoutingConfig,
    SeverityContext,
    classify_severity,
    load_routing_config,
    pick_instance,
    required_trigger_disposition,
    sla_for,
)

from verdify_schemas.plan import classify_planner_terminal_action

LOCAL_FIRST_CONFIG = RoutingConfig(
    forecast_major_delta_vpd_kpa=0.5,
    forecast_major_delta_temp_f=10.0,
    deviation_major_threshold=0.15,
    deviation_prolonged_cycles=3,
    sla_min_by_pair=_DEFAULT_SLA_MIN,
)

# ── pick_instance ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "trigger_type,severity,expected",
    [
        # Contract v1.5: all normal triggers route local by default.
        ("SUNRISE", "minor", "local"),
        ("SUNRISE", "major", "local"),
        ("SUNSET", "minor", "local"),
        ("SUNSET", "major", "local"),
        ("MIDNIGHT", "minor", "local"),
        ("MIDNIGHT", "major", "local"),
        ("HEARTBEAT", "minor", "local"),
        ("HEARTBEAT", "major", "local"),
        ("TRANSITION", "minor", "local"),
        ("TRANSITION", "major", "local"),
        ("FORECAST", "minor", "local"),
        ("FORECAST", "major", "local"),
        ("DEVIATION", "minor", "local"),
        ("DEVIATION", "major", "local"),
    ],
)
def test_pick_instance_matrix(trigger_type, severity, expected):
    assert pick_instance(trigger_type, severity, config=LOCAL_FIRST_CONFIG) == expected


def test_pick_instance_manual_defaults_to_local():
    assert pick_instance("MANUAL", config=LOCAL_FIRST_CONFIG) == "local"


def test_pick_instance_override_always_wins():
    # Override wins even when the policy would pick the other instance
    assert pick_instance("SUNRISE", "major", override="local", config=LOCAL_FIRST_CONFIG) == "local"
    assert pick_instance("HEARTBEAT", "minor", override="opus", config=LOCAL_FIRST_CONFIG) == "opus"


def test_required_full_plan_instance_override_routes_required_cycles_only():
    cfg = RoutingConfig(
        forecast_major_delta_vpd_kpa=0.5,
        forecast_major_delta_temp_f=10.0,
        deviation_major_threshold=0.15,
        deviation_prolonged_cycles=3,
        sla_min_by_pair=_DEFAULT_SLA_MIN,
        required_full_plan_instance="opus",
    )
    assert pick_instance("SUNRISE", "minor", config=cfg) == "opus"
    assert pick_instance("SUNSET", "major", config=cfg) == "opus"
    assert pick_instance("MIDNIGHT", "minor", config=cfg) == "opus"
    assert pick_instance("FORECAST", "major", config=cfg) == "local"
    assert pick_instance("TRANSITION", "minor", config=cfg) == "local"


# ── classify_severity ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "delta_vpd,delta_temp,expected",
    [
        (None, None, "minor"),
        (0.3, 5, "minor"),  # both below thresholds
        (0.6, 5, "major"),  # vpd above 0.5 kPa
        (0.3, 12, "major"),  # temp above 10°F
        (-0.6, -12, "major"),  # negatives count via abs
        (0.5, 10, "minor"),  # at thresholds → not major (strictly greater)
    ],
)
def test_classify_severity_forecast(delta_vpd, delta_temp, expected):
    ctx = SeverityContext(forecast_delta_vpd=delta_vpd, forecast_delta_temp_f=delta_temp)
    assert classify_severity("FORECAST", ctx) == expected


@pytest.mark.parametrize(
    "max_abs_dev,consecutive,expected",
    [
        (None, None, "minor"),
        (0.10, 1, "minor"),  # both below thresholds
        (0.20, 1, "major"),  # magnitude above 0.15
        (0.10, 4, "major"),  # prolonged above 3 cycles
        (0.15, 3, "minor"),  # at thresholds → not major (strictly greater)
    ],
)
def test_classify_severity_deviation(max_abs_dev, consecutive, expected):
    ctx = SeverityContext(max_abs_deviation=max_abs_dev, consecutive_deviation_cycles=consecutive)
    assert classify_severity("DEVIATION", ctx) == expected


def test_classify_severity_non_forecast_non_deviation_is_minor():
    # SUNRISE/SUNSET/MIDNIGHT/TRANSITION/HEARTBEAT all route on type alone;
    # severity is always 'minor' (their policy doesn't branch).
    for t in ("SUNRISE", "SUNSET", "MIDNIGHT", "TRANSITION", "HEARTBEAT"):
        assert classify_severity(t, SeverityContext()) == "minor"


# ── sla_for ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "trigger_type,instance,expected_min",
    [
        ("SUNRISE", "local", 30),
        ("SUNSET", "local", 30),
        ("SOLAR_MAX", "local", 30),
        ("MIDNIGHT", "local", 30),
        ("TRANSITION", "local", 45),
        ("FORECAST", "local", 90),
        ("DEVIATION", "local", 20),
        ("FORECAST_DEVIATION", "local", 20),
        ("HEARTBEAT", "local", 15),
        # explicit cloud escalation targets
        ("SUNRISE", "opus", 15),
        ("SUNSET", "opus", 15),
        ("SOLAR_MAX", "opus", 15),
        ("MIDNIGHT", "opus", 15),
        ("FORECAST", "opus", 30),
        ("DEVIATION", "opus", 10),
        ("FORECAST_DEVIATION", "opus", 10),
    ],
)
def test_sla_for_defined_pairs(trigger_type, instance, expected_min):
    result = sla_for(trigger_type, instance)
    assert result == timedelta(minutes=expected_min)


@pytest.mark.parametrize(
    "trigger_type,instance",
    [
        ("HEARTBEAT", "opus"),  # opus doesn't do heartbeats
        ("TRANSITION", "opus"),  # explicit cloud table does not include transitions
        ("MANUAL", "opus"),  # manual: no SLA
        ("MANUAL", "local"),
    ],
)
def test_sla_for_undefined_pairs_returns_none(trigger_type, instance):
    assert sla_for(trigger_type, instance) is None


# ── load_routing_config ────────────────────────────────────────────


def test_load_routing_config_falls_back_to_defaults(tmp_path, monkeypatch):
    # Clear the lru_cache so a new path is honored
    load_routing_config.cache_clear()
    fake_path = tmp_path / "does-not-exist.yaml"
    cfg = load_routing_config(str(fake_path))
    # Defaults from contract
    assert cfg.forecast_major_delta_vpd_kpa == 0.5
    assert cfg.forecast_major_delta_temp_f == 10.0
    assert cfg.deviation_major_threshold == 0.15
    assert cfg.deviation_prolonged_cycles == 3
    # SLA table matches defaults
    assert cfg.sla_min_by_pair[("local", "SUNRISE")] == 30
    assert cfg.sla_min_by_pair[("local", "HEARTBEAT")] == 15


def test_load_routing_config_applies_yaml_overrides(tmp_path):
    load_routing_config.cache_clear()
    p = tmp_path / "ai.yaml"
    p.write_text(
        """
planner_routing:
  required_full_plan_instance: opus
  forecast_major_delta_vpd_kPa: 0.8
  deviation_prolonged_cycles: 5
planner_sla:
  opus:
    SUNRISE: 20
  local:
    HEARTBEAT: 25
"""
    )
    cfg = load_routing_config(str(p))
    # Overridden
    assert cfg.forecast_major_delta_vpd_kpa == 0.8
    assert cfg.deviation_prolonged_cycles == 5
    assert cfg.required_full_plan_instance == "opus"
    assert cfg.sla_min_by_pair[("opus", "SUNRISE")] == 20
    assert cfg.sla_min_by_pair[("local", "HEARTBEAT")] == 25
    # Unchanged where YAML didn't specify
    assert cfg.forecast_major_delta_temp_f == 10.0
    assert cfg.deviation_major_threshold == 0.15
    assert cfg.sla_min_by_pair[("opus", "SUNSET")] == 15


def test_defaults_match_contract_literals():
    # Sanity check the module defaults match the published §2.F table.
    # If this drifts, either the contract changed or the module did —
    # investigate before updating this test.
    expected_pairs = {
        ("local", "SUNRISE"): 30,
        ("local", "SUNSET"): 30,
        ("local", "MIDNIGHT"): 30,
        ("local", "SOLAR_MAX"): 30,
        ("local", "TRANSITION"): 45,
        ("local", "FORECAST"): 90,
        ("local", "DEVIATION"): 20,
        ("local", "FORECAST_DEVIATION"): 20,
        ("local", "HEARTBEAT"): 15,
        ("opus", "SUNRISE"): 15,
        ("opus", "SUNSET"): 15,
        ("opus", "MIDNIGHT"): 15,
        ("opus", "SOLAR_MAX"): 15,
        ("opus", "FORECAST"): 30,
        ("opus", "DEVIATION"): 10,
        ("opus", "FORECAST_DEVIATION"): 10,
    }
    assert _DEFAULT_SLA_MIN == expected_pairs


# ── RoutingConfig equality / caching ───────────────────────────────


def test_routing_config_is_frozen():
    cfg = RoutingConfig(
        forecast_major_delta_vpd_kpa=0.5,
        forecast_major_delta_temp_f=10.0,
        deviation_major_threshold=0.15,
        deviation_prolonged_cycles=3,
        sla_min_by_pair={},
    )
    with pytest.raises((AttributeError, Exception)):  # dataclass frozen raises FrozenInstanceError
        cfg.forecast_major_delta_vpd_kpa = 0.8  # type: ignore[misc]


@pytest.mark.parametrize(
    "actual,valid,neutral,status,terminal,satisfies",
    [
        ("set_plan", True, False, "plan_written", "set_plan", True),
        ("set_tunable", False, False, "wrong_action", "wrong_action", False),
        ("acknowledge_trigger", False, False, "wrong_action", "wrong_action", False),
        (
            "acknowledge_trigger",
            False,
            True,
            "neutral_fallback",
            "neutral_fallback",
            False,
        ),
    ],
)
def test_required_set_plan_terminal_actions_are_truthful(actual, valid, neutral, status, terminal, satisfies):
    result = classify_planner_terminal_action(
        expected_action="set_plan",
        actual_action=actual,
        valid_full_plan=valid,
        explicit_neutral=neutral,
    )

    assert result.status == status
    assert result.terminal_action == terminal
    assert result.satisfies_required_plan is satisfies


def test_routine_actions_remain_distinct_terminal_classes():
    tunable = classify_planner_terminal_action(expected_action="any", actual_action="set_tunable")
    ack = classify_planner_terminal_action(expected_action="any", actual_action="acknowledge_trigger")

    assert (tunable.status, tunable.terminal_action) == ("action_completed", "set_tunable")
    assert (ack.status, ack.terminal_action) == ("acked", "acknowledge_trigger")


@pytest.mark.parametrize(
    "status,terminal,due_delta,expected",
    [
        ("plan_written", "set_plan", 30, "complete"),
        ("delivered", None, 30, "wait"),
        ("delivered", None, -1, "retry"),
        ("neutral_fallback", "neutral_fallback", 30, "wait"),
        ("neutral_fallback", "neutral_fallback", -1, "retry"),
        ("wrong_action", "wrong_action", 30, "retry"),
        ("timed_out", "timeout", -1, "retry"),
        ("delivery_failed", "delivery_failed", -1, "retry"),
        ("action_completed", "set_tunable", 30, "retry"),
        ("acked", "acknowledge_trigger", 30, "retry"),
    ],
)
def test_required_trigger_terminal_outcomes_do_not_become_retry_dead_ends(status, terminal, due_delta, expected):
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)

    assert (
        required_trigger_disposition(
            status=status,
            terminal_action=terminal,
            due_at=now + timedelta(minutes=due_delta),
            now=now,
        )
        == expected
    )
