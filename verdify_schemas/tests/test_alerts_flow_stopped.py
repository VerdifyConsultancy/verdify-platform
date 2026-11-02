"""flow_stopped_at: the leak alert's "Flow stopped at HH:MM" (VD-72, shared with VD-54)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from verdify_schemas.alerts import flow_stopped_at

T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def readings(*gpm):
    """One reading a minute from T0, oldest first."""
    return [(T0 + timedelta(minutes=i), value) for i, value in enumerate(gpm)]


def at(minute):
    return T0 + timedelta(minutes=minute)


def test_second_reading_of_the_trailing_quiet_run():
    assert flow_stopped_at(readings(1.2, 1.1, 0.05, 0.0)) == at(3)
    assert flow_stopped_at(readings(1.2, 0.0, 0.0, 0.0, 0.0)) == at(2)  # the run's second, not the last


def test_one_quiet_reading_is_not_yet_stopped():
    assert flow_stopped_at([]) is None
    assert flow_stopped_at(readings(1.2, 0.0)) is None
    assert flow_stopped_at(readings(0.0)) is None


def test_flow_that_starts_again_ends_the_run():
    assert flow_stopped_at(readings(1.2, 0.0, 0.0, 0.4)) is None
    assert flow_stopped_at(readings(1.2, 0.0, 0.0, 0.4, 0.0, 0.0)) == at(5)


def test_threshold_is_strict_and_configurable():
    assert flow_stopped_at(readings(1.0, 0.1, 0.1)) is None  # exactly 0.1 is still flow
    assert flow_stopped_at(readings(1.0, 0.099, 0.099)) == at(2)
    assert flow_stopped_at(readings(1.0, 0.3, 0.3), below_gpm=0.5) == at(2)


def test_missing_readings_are_skipped():
    assert flow_stopped_at(readings(1.0, 0.0, None, None, 0.0)) == at(4)
    assert flow_stopped_at(readings(None, None)) is None


def test_needed_readings_and_any_iterable():
    assert flow_stopped_at(readings(1.0, 0.0, 0.0, 0.0), needed=3) == at(3)
    assert flow_stopped_at(readings(1.0, 0.0, 0.0), needed=3) is None
    assert flow_stopped_at(iter(readings(1.0, 0.0)), needed=1) == at(1)
    with pytest.raises(ValueError):
        flow_stopped_at(readings(0.0, 0.0), needed=0)
