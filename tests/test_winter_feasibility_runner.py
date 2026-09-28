"""Bounded daily scheduling for the read-only winter packet."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "deploy/k8s/components/winter-feasibility/run-daily.py"
spec = importlib.util.spec_from_file_location("winter_daily", SOURCE)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_daily_due_day_and_no_prewindow_collection(tmp_path):
    instance = {"start_local_date": "2026-11-02"}
    assert runner.due_days(instance, datetime(2026, 11, 2, 14, tzinfo=UTC), tmp_path) == []
    assert runner.due_days(instance, datetime(2026, 11, 3, 8, tzinfo=UTC), tmp_path) == [date(2026, 11, 2)]


def test_catch_up_is_bounded_and_keeps_latest_day(tmp_path):
    instance = {"start_local_date": "2026-11-02"}
    assert runner.due_days(instance, datetime(2026, 11, 5, 8, tzinfo=UTC), tmp_path) == [
        date(2026, 11, 2),
        date(2026, 11, 4),
    ]
    (tmp_path / "2026-11-02.json").touch()
    (tmp_path / "2026-11-04.json").touch()
    assert runner.due_days(instance, datetime(2026, 11, 6, 8, tzinfo=UTC), tmp_path) == [
        date(2026, 11, 3),
        date(2026, 11, 5),
    ]


def test_postwindow_catch_up_stops_at_sixtieth_day(tmp_path):
    instance = {"start_local_date": "2026-11-02"}
    assert runner.due_days(instance, datetime(2027, 1, 3, 8, tzinfo=UTC), tmp_path) == [
        date(2026, 11, 2),
        date(2026, 12, 31),
    ]
