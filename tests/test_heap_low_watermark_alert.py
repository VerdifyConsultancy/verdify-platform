"""The heap low-water-mark warning raises on a new low and resolves on healthy current heap.

heap_min_free_kb is the ESP32's since-boot minimum, so it stays low until a
reboot. Runs the owning _heap_low_watermark_episode from ingestor/tasks/alerts.py
(AST-extracted, no ingestor import or database) against the shape of the
2026-11-05 22:06Z event.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"_HEAP_LOW_WATERMARK_KB", "_heap_low_watermark_episode"}


def _episode_fn():
    source = ast.parse((ROOT / "ingestor/tasks/alerts.py").read_text())
    body = [
        node
        for node in source.body
        if (isinstance(node, ast.FunctionDef) and node.name in NAMES)
        or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NAMES for t in node.targets))
    ]
    assert len(body) == len(NAMES)
    ns: dict = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), "owning-heap-episode", "exec"), ns)  # noqa: S102
    return ns["_heap_low_watermark_episode"]


EPISODE = _episode_fn()
T0 = datetime(2026, 11, 5, 22, 0, tzinfo=UTC)


def sample(minute, free, min_free, largest, uptime=18000.0):
    return {
        "ts": T0 + timedelta(minutes=minute),
        "heap_bytes": free,
        "heap_min_free_kb": min_free,
        "heap_largest_free_block_kb": largest,
        "uptime_s": uptime + minute * 60,
    }


def run(samples):
    return EPISODE(samples, healthy_free_kb=25.0, healthy_largest_kb=20.0, healthy_samples=2)


BEFORE = [sample(m, 72.0, 49.4, 56.0) for m in range(6)]
DIP = [sample(6, 16.5, 2.5, 12.0)]


def test_new_low_raises_and_records_event_value():
    episode = run(BEFORE + DIP)
    assert episode["active"] is True
    assert episode["event_ts"] == T0 + timedelta(minutes=6)
    assert episode["event_low_kb"] == 2.5
    assert episode["healthy_after"] == 0


def test_one_healthy_sample_keeps_it_open():
    episode = run(BEFORE + DIP + [sample(7, 67.8, 2.5, 54.0)])
    assert episode["active"] is True
    assert episode["healthy_after"] == 1


def test_resolves_after_consecutive_healthy_samples_while_mark_stays_low():
    recovered = [sample(m, 66.0, 2.5, 54.0) for m in range(7, 12)]
    episode = run(BEFORE + DIP + recovered)
    assert episode["active"] is False
    assert episode["event_low_kb"] == 2.5
    assert episode["healthy_after"] == 5


def test_unhealthy_sample_resets_the_run():
    tail = [sample(7, 66.0, 2.5, 54.0), sample(8, 20.0, 2.5, 54.0), sample(9, 66.0, 2.5, 14.0)]
    assert run(BEFORE + DIP + tail)["active"] is True


def test_an_unchanged_low_mark_does_not_re_raise_but_a_lower_one_does():
    recovered = [sample(m, 66.0, 2.5, 54.0) for m in range(7, 12)]
    assert run(BEFORE + DIP + recovered + [sample(12, 66.0, 2.5, 54.0)])["active"] is False
    again = run(BEFORE + DIP + recovered + [sample(12, 18.0, 1.9, 15.0)])
    assert again["active"] is True
    assert again["event_low_kb"] == 1.9


def test_reboot_rearms_and_healthy_boot_has_no_event():
    assert run(BEFORE) is None
    rebooted = [sample(m, 70.0, 48.0, 56.0, uptime=0.0) for m in range(7, 10)]
    assert run(rebooted) is None
    low_at_boot = run(BEFORE + DIP + [sample(7, 66.0, 2.5, 54.0)] + [sample(8, 20.0, 4.0, 15.0, uptime=-480.0)])
    assert low_at_boot["active"] is True
    assert low_at_boot["event_low_kb"] == 4.0


def test_partial_rows_without_current_heap_do_not_break_the_healthy_run():
    tail = [sample(7, 66.0, 2.5, 54.0), sample(8, None, 2.5, 52.0), sample(9, 66.0, 2.5, 54.0)]
    episode = run(BEFORE + DIP + tail)
    assert episode["active"] is False
    assert episode["healthy_after"] == 2


def test_warning_details_schema_accepts_the_event_fields():
    import sys

    sys.path.insert(0, str(ROOT))
    from verdify_schemas.alerts import HeapPressureWarningDetails

    details = HeapPressureWarningDetails(
        equipment="heap_pressure_warning",
        heap_free_kb=67.8,
        heap_min_free_kb=2.5,
        heap_low_watermark_warning=True,
        healthy_heap_samples_after_event=0,
        heap_low_watermark_event_ts=T0 + timedelta(minutes=6),
        heap_low_watermark_event_kb=2.5,
        warning_logs_30m=0,
    )
    assert details.heap_low_watermark_event_kb == 2.5
