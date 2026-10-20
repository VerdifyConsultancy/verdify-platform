import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from ingestor.soil_saturation import Sample, SaturationTarget, evaluate_saturation, saturation_evaluation_status

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
# Synthetic fixture only: never a production commissioned target.
TARGET = SaturationTarget("probe", "fixture-calibration", "test-only-measurement", 80, 70, 600, 300, 60, 60)


def samples(value=80):
    return [Sample(NOW - timedelta(seconds=s), value, "probe", "fixture-calibration") for s in range(600, -1, -60)]


def test_uncommissioned_evaluator_and_runtime_probe_never_touch_database():
    result = evaluate_saturation(samples(), None, NOW)
    assert not result.available and not result.active and result.reason == "uncommissioned_targets"
    pool = Mock()
    assert asyncio.run(saturation_evaluation_status(pool)) == result
    assert not pool.mock_calls


def test_sustained_threshold_and_hysteretic_recovery():
    opening = evaluate_saturation(samples(), TARGET, NOW)
    assert opening.available and opening.active and opening.transition == "open"
    assert not evaluate_saturation(samples(79.9), TARGET, NOW).active
    latched = evaluate_saturation(samples(75), TARGET, NOW, previous_active=True)
    assert latched.active and latched.transition is None
    clearing = evaluate_saturation(samples(70), TARGET, NOW, previous_active=True)
    assert clearing.available and not clearing.active and clearing.transition == "clear"
    assert evaluate_saturation(samples(), TARGET, NOW, previous_active=True).transition is None


def test_short_spike_does_not_open_and_short_recovery_does_not_clear():
    window = samples(75)
    window[-1] = replace(window[-1], pct=90)
    assert not evaluate_saturation(window, TARGET, NOW).active
    window = samples(90)
    window[-1] = replace(window[-1], pct=60)
    assert evaluate_saturation(window, TARGET, NOW, previous_active=True).active


@pytest.mark.parametrize("value", [None, 0, -1, 101, float("nan"), float("inf"), True])
def test_invalid_readings_do_not_clear_active_alert(value):
    window = samples(60)
    window[-1] = replace(window[-1], pct=value)
    result = evaluate_saturation(window, TARGET, NOW, previous_active=True)
    assert not result.available and result.active and result.transition is None


@pytest.mark.parametrize(
    "kind", ["gap", "duplicate", "future", "stale", "wrong_source", "wrong_calibration", "short", "naive"]
)
def test_incomplete_or_wrong_truth_stays_unavailable(kind):
    window = samples()
    if kind == "gap":
        window.pop(5)
    if kind == "duplicate":
        window.insert(5, window[5])
    if kind == "future":
        window.append(replace(window[-1], at=NOW + timedelta(seconds=1)))
    if kind == "stale":
        window = window[:-2]
    if kind == "wrong_source":
        window[-1] = replace(window[-1], sensor_id="other")
    if kind == "wrong_calibration":
        window[-1] = replace(window[-1], calibration_id="other")
    if kind == "short":
        window = window[1:]
    if kind == "naive":
        window[-1] = replace(window[-1], at=NOW.replace(tzinfo=None))
    assert not evaluate_saturation(window, TARGET, NOW).available


def test_empty_calibration_evidence_and_inverted_threshold_are_not_commissioning():
    for target in [
        replace(TARGET, evidence_ref=""),
        replace(TARGET, calibration_id=""),
        replace(TARGET, recovery_pct=90),
    ]:
        assert not evaluate_saturation(samples(), target, NOW).available
