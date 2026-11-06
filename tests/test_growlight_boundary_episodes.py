"""#386: the grow-light boundary detector classifies intervals from exact intent edges."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "growlight_boundary", Path(__file__).resolve().parents[1] / "scripts/growlight-boundary-episodes.py"
)
gl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gl)

T0 = 1_800_000_000.0


def data(on_s, off_s, reasons, boots=(), physical_lag=10.0):
    edges = [
        {"ts": T0 + on_s, "eq": "grow_light_grow_intent", "on": True},
        {"ts": T0 + on_s + physical_lag, "eq": "grow_light_grow", "on": True},
        {"ts": T0 + off_s, "eq": "grow_light_grow_intent", "on": False},
        {"ts": T0 + off_s + physical_lag, "eq": "grow_light_grow", "on": False},
    ]
    rows = [{"ts": T0 + t, "entity": "gl_grow_reason", "value": v} for t, v in reasons]
    return {
        "edges": edges,
        "reasons": rows,
        "min_on": {"gl_grow_min_on_s": 120, "gl_main_min_on_s": 120},
        "boots": [T0 + b for b in boots],
        "since": T0 - 1,
    }


@pytest.mark.parametrize(
    ("on_s", "off_s", "reasons", "boots", "kind"),
    [
        # The window closes 30 s after ON: minimum-on holds, OFF on the decision after 120 s.
        (0, 120, [(0, "plant_lux_low"), (60, "min_on_hold"), (120, "outside_window")], (), "boundary_ok"),
        # The hold lasted 200 s past its first row: the light overran minimum-on.
        (0, 300, [(0, "plant_lux_low"), (60, "min_on_hold"), (300, "outside_window")], (), "overrun"),
        # OFF after 60 s with no auto-disable or reboot: premature cycling.
        (0, 60, [(0, "plant_lux_low"), (60, "lux_sufficient")], (), "premature"),
        # The same short interval with a reboot inside it is a reset, not a cycle.
        (0, 90, [(0, "plant_lux_low"), (90, "lux_sufficient")], (40,), "reset"),
        # Auto-disable stays dominant over minimum-on.
        (0, 30, [(0, "plant_lux_low"), (30, "auto_disabled")], (), "auto_disabled"),
        # A long ON that ends without any hold is ordinary; an old boot does not matter.
        (0, 7200, [(0, "plant_lux_low"), (7200, "outside_window")], (100,), "ordinary"),
    ],
)
def test_classify(on_s, off_s, reasons, boots, kind):
    (episode,) = gl.classify(data(on_s, off_s, reasons, boots))
    assert episode["kind"] == kind
    assert episode["duration_s"] == off_s - on_s
    assert episode["readback_on_lag_s"] == 10.0 and episode["readback_off_lag_s"] == 10.0


def test_intervals_ignore_repeated_rows_and_keep_an_open_interval():
    edges = [
        {"ts": 1.0, "eq": "x", "on": True},
        {"ts": 2.0, "eq": "x", "on": True},
        {"ts": 3.0, "eq": "x", "on": False},
        {"ts": 4.0, "eq": "x", "on": True},
    ]
    assert gl.intervals(edges, "x") == [(1.0, 3.0), (4.0, None)]


def test_since_is_validated_before_any_query():
    with pytest.raises(SystemExit):
        gl.fetch("1 day; DROP TABLE climate")
