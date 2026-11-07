"""VD-29: snapping, the readback comparators and the architecture §5.2 rule.

Pure unit tests: no database, ingestor import or firmware build.
"""

from __future__ import annotations

import ast
import dataclasses
import math
import pathlib
import struct
from collections.abc import KeysView
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from verdify_schemas.setting_values import (
    SECOND_READBACK_ABS_TOLERANCE_PARAMS,
    Resolved,
    binary32_ulp,
    readback_abs_tolerance,
    readback_matches,
    readback_tolerance,
    readback_values_equivalent,
    resolve_effective,
    snap,
    snap_setting,
    within_relative_deadband,
)
from verdify_schemas.tunable_registry import REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _float32(x: float) -> float:
    """The binary32 image of ``x``, as an ESPHome number state carries it."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _next_float32(x: float) -> float:
    """The binary32 value just above ``float32(x)`` (x >= 0)."""
    bits = struct.unpack("<I", struct.pack("<f", x))[0]
    return struct.unpack("<f", struct.pack("<I", bits + 1))[0]


# ── snap ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (1.53, 0.05, 1.55),  # US-SET-03: 1.53 is saved as 1.55
        (1.525, 0.05, 1.5),  # tie: 30.5 steps → 30
        (1.575, 0.05, 1.6),  # tie: 31.5 steps → 32
        (2.5, 1, 2.0),  # tie → even
        (3.5, 1, 4.0),  # tie → even
        (845.4, 1, 845.0),
        (48.75, 30, 60.0),  # LD-10: an off-grid Iris value on a 30 s step
        (75000, 30000, 60000.0),  # tie: 2.5 steps → 2
        (61.25, 0.1, 61.2),  # tie: 612.5 steps → 612
        (0.734, 0.01, 0.73),
        (-22.5, 15, -30.0),  # tie: -1.5 steps → -2
        (1.55, 0.05, 1.55),  # on the grid already
    ],
)
def test_snap_vectors(value: float, step: float, expected: float) -> None:
    assert snap(value, step) == expected


def test_snap_never_returns_negative_zero() -> None:
    for value in (-0.01, -7.5, -0.0):
        result = snap(value, 15 if value == -7.5 else 0.05)
        assert result == 0.0
        assert math.copysign(1.0, result) == 1.0


def test_snap_without_a_step_returns_the_value_unchanged() -> None:
    assert snap(1.234, None) == 1.234
    assert snap(7, None, lo=8, hi=9) == 7.0


def test_snap_returns_the_bound_when_the_snapped_value_falls_outside() -> None:
    # gl_main_lux_threshold: min_value 100 sits off its 500-lux grid.
    assert snap(120, 500, lo=100, hi=50000) == 100.0
    assert snap(100, 500, lo=100, hi=50000) == 100.0
    assert snap(2.49, 0.05, lo=0.5, hi=2.45) == 2.45
    assert snap(760, 500, lo=100, hi=50000) == 1000.0


@pytest.mark.parametrize(
    ("value", "step"),
    [(math.nan, 0.05), (math.inf, 0.05), (-math.inf, None), (1.0, 0), (1.0, -0.05), (1.0, math.nan), (1.0, math.inf)],
)
def test_snap_rejects_non_finite_values_and_bad_steps(value: float, step: float | None) -> None:
    with pytest.raises(ValueError):
        snap(value, step)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("mister_engage_kpa", 1.53, 1.55),
        ("site_pressure_hpa", 845.4, 845.0),
        ("mister_engage_delay_s", 48.75, 60.0),
        ("gl_main_lux_threshold", 120, 100.0),  # off-grid min stays reachable
        ("gl_grow_lux_threshold", 250, 100.0),  # 0.5 steps → 0, clamped up to the min 100
        ("band_vpd_low_sr", 0.734, 0.73),  # anchors snap to the push quanta
        ("band_temp_low_sr", 61.26, 61.3),
        ("zone_priority_center", 2.4, 2.0),
        ("sw_fog_closes_vent", 1, 1.0),
        ("fog_time_window_start", 7.3, 7.3),  # no entity, no step
    ],
)
def test_snap_setting_uses_the_registry_step_and_bounds(name: str, value: float, expected: float) -> None:
    assert snap_setting(name, value) == expected


def test_snap_setting_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="not registered"):
        snap_setting("not_a_setting", 1.0)


# ── the 1 % dead-band and the moved dispatcher comparator ────────────────────


@pytest.mark.parametrize(
    ("last", "val", "within"),
    [
        (0.7501, 0.75, True),
        (0.70, 0.75, False),
        (0.0, 0.0, True),
        (0.000005, 0.0, True),  # abs_floor: 5e-6 / 1e-3 < 1 %
        (0.00002, 0.0, False),
        (840.0, 845.0, True),  # ACC-08: a one-step change hides inside 1 %
    ],
)
def test_within_relative_deadband(last: float, val: float, within: bool) -> None:
    assert within_relative_deadband(last, val) is within


def test_within_relative_deadband_takes_its_tolerances() -> None:
    assert within_relative_deadband(1.04, 1.0, rel=0.05)
    assert not within_relative_deadband(0.02, 0.0, abs_floor=1.0, rel=0.01)
    assert within_relative_deadband(0.005, 0.0, abs_floor=1.0, rel=0.01)


def test_readback_abs_tolerance_covers_only_the_second_based_params() -> None:
    assert SECOND_READBACK_ABS_TOLERANCE_PARAMS <= set(REGISTRY)
    for param in SECOND_READBACK_ABS_TOLERANCE_PARAMS:
        assert readback_abs_tolerance(param) == 1.0
    assert readback_abs_tolerance("site_pressure_hpa") == 0.0
    assert readback_abs_tolerance("not_a_setting") == 0.0


@pytest.mark.parametrize(
    ("parameter", "observed", "desired", "equivalent"),
    [
        ("mister_vpd_weight", 0.75, 0.75, True),
        ("mister_vpd_weight", 0.7501, 0.75, True),
        ("mister_vpd_weight", 0.70, 0.75, False),
        ("mister_engage_delay_s", 44.0, 45.0, True),
        ("mister_engage_delay_s", 43.0, 45.0, False),
        ("mister_pulse_on_s", 33.0, 33.75, True),
        ("mister_pulse_on_s", 30.0, 33.75, False),
        ("site_pressure_hpa", 840.0, 845.0, True),
        ("mister_vpd_weight", None, 0.75, False),
        ("mister_vpd_weight", "n/a", 0.75, False),
    ],
)
def test_readback_values_equivalent_keeps_its_behaviour(
    parameter: str, observed: object, desired: float, equivalent: bool
) -> None:
    assert readback_values_equivalent(parameter, observed, desired) is equivalent  # type: ignore[arg-type]


# ── binary32 ULP and the grid-aware comparator ───────────────────────────────


@pytest.mark.parametrize(
    ("x", "ulp"),
    [
        (1.6, 2.0**-23),
        (1.0, 2.0**-23),
        (0.5, 2.0**-24),
        (845.0, 2.0**-14),
        (-1.6, 2.0**-23),
        (0.0, 2.0**-149),
        (3.4028234663852886e38, 2.0**104),  # the largest finite binary32 value
    ],
)
def test_binary32_ulp(x: float, ulp: float) -> None:
    assert binary32_ulp(x) == ulp


def test_binary32_ulp_covers_the_transport_rounding_of_1_6() -> None:
    assert _float32(1.6) == 1.600000023841858
    assert abs(_float32(1.6) - 1.6) <= binary32_ulp(1.6)


@pytest.mark.parametrize("x", [math.nan, math.inf, -math.inf, 1e39])
def test_binary32_ulp_rejects_values_binary32_cannot_hold(x: float) -> None:
    with pytest.raises(ValueError):
        binary32_ulp(x)


@pytest.mark.parametrize(
    ("param", "value", "tolerance"),
    [
        ("site_pressure_hpa", 845.0, 0.5),  # half of step 1
        ("mister_pulse_on_s", 30.0, 2.5),  # half of step 5 beats the 1 s tolerance
        ("mister_engage_delay_s", 60.0, 15.0),
        ("band_vpd_low_sr", 0.73, 0.005),  # anchor push quantum 0.01
        ("sw_fog_closes_vent", 1.0, 0.5),
        ("fog_time_window_start", 1.6, 2.0**-23),  # no step: the binary32 ULP
        ("not_a_setting", 1.6, 2.0**-23),
    ],
)
def test_readback_tolerance(param: str, value: float, tolerance: float) -> None:
    assert readback_tolerance(param, value) == tolerance


@pytest.mark.parametrize(
    ("param", "readback", "value", "matches"),
    [
        # ACC-08 / US-SET-02: a one-step change is a difference, not a confirmation.
        ("site_pressure_hpa", 840.0, 845.0, False),
        ("site_pressure_hpa", 845.0, 845.0, True),
        ("site_pressure_hpa", _float32(845.0), 845.0, True),
        ("mister_engage_kpa", _float32(1.6), 1.6, True),
        ("mister_engage_kpa", _float32(1.55), 1.6, False),
        # The binary32 ULP term confirms a transport-rounded value with no step.
        ("fog_time_window_start", _float32(1.6), 1.6, True),
        ("not_a_setting", 1.600000023841858, 1.6, True),
        ("fog_time_window_start", _next_float32(1.6), 1.6, False),
        ("mister_engage_delay_s", 30.0, 60.0, False),
        ("mister_engage_delay_s", 44.0, 45.0, True),
        ("band_vpd_low_sr", _float32(0.73), 0.73, True),
        ("band_vpd_low_sr", _float32(0.74), 0.73, False),
        ("sw_fog_closes_vent", 1.0, 0.0, False),
        ("sw_fog_closes_vent", True, 1.0, True),
        # Missing, non-numeric, non-finite or out-of-range sides never match.
        ("site_pressure_hpa", None, 845.0, False),
        ("site_pressure_hpa", 845.0, None, False),
        ("site_pressure_hpa", math.nan, 845.0, False),
        ("site_pressure_hpa", 845.0, math.inf, False),
        ("site_pressure_hpa", "845", 845.0, True),
        ("site_pressure_hpa", "unavailable", 845.0, False),
        ("not_a_setting", 1e39, 1e39, False),
    ],
)
def test_readback_matches(param: str, readback: object, value: object, matches: bool) -> None:
    assert readback_matches(param, readback, value) is matches  # type: ignore[arg-type]


def test_the_grid_comparator_sees_what_the_dead_band_hides() -> None:
    """ACC-08: 840 → 845 hPa reads confirmed under 1 %, different on the grid."""
    assert readback_values_equivalent("site_pressure_hpa", 840.0, 845.0)
    assert not readback_matches("site_pressure_hpa", 840.0, 845.0)


# ── resolve_effective: architecture §5.2 ──────────────────────────────────────

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EARLIER = T0 - timedelta(minutes=5)
LATER = T0 + timedelta(minutes=5)


def _desired(value: float | None, manual: bool, created_at: datetime) -> dict[str, object]:
    return {"value": value, "manual": manual, "created_at": created_at}


def _plan(value: float, created_at: datetime) -> dict[str, object]:
    return {"value": value, "created_at": created_at}


@pytest.mark.parametrize(
    ("desired", "plan", "expected"),
    [
        # No desired row: today's plan, band or default applies.
        (None, None, None),
        (None, _plan(1.2, LATER), None),
        # Released (value NULL): back to plan, band or default.
        (_desired(None, False, T0), _plan(1.2, EARLIER), None),
        (_desired(None, False, T0), None, None),
        # Manual beats Iris's plan whatever its age.
        (_desired(1.55, True, T0), _plan(1.2, LATER), Resolved(1.55, "desired")),
        (_desired(1.55, True, T0), _plan(1.2, EARLIER), Resolved(1.55, "desired")),
        (_desired(1.55, True, T0), None, Resolved(1.55, "desired")),
        # Not manual: last write wins, and a tie goes to the desired row.
        (_desired(1.55, False, T0), _plan(1.2, LATER), Resolved(1.2, "plan")),
        (_desired(1.55, False, T0), _plan(1.2, EARLIER), Resolved(1.55, "desired")),
        (_desired(1.55, False, T0), _plan(1.2, T0), Resolved(1.55, "desired")),
        (_desired(1.55, False, T0), None, Resolved(1.55, "desired")),
    ],
)
def test_resolve_effective_truth_table(
    desired: dict[str, object] | None, plan: dict[str, object] | None, expected: Resolved | None
) -> None:
    assert resolve_effective(desired, plan) == expected


class _RecordLike:
    """The asyncpg.Record shape: key access and keys(), no column attributes."""

    def __init__(self, **values: object) -> None:
        self._values = values

    def keys(self) -> KeysView[str]:
        return self._values.keys()

    def __getitem__(self, key: str) -> object:
        return self._values[key]


def test_resolve_effective_reads_records_and_attribute_rows() -> None:
    desired = SimpleNamespace(value=2, manual=False, created_at=T0)
    assert resolve_effective(desired, _RecordLike(value=3, created_at=LATER)) == Resolved(3.0, "plan")
    manual = _RecordLike(value=2, manual=True, created_at=T0)
    assert resolve_effective(manual, SimpleNamespace(value=3, created_at=LATER)) == Resolved(2.0, "desired")
    resolved = resolve_effective({"value": 2, "manual": False, "created_at": T0}, None)
    assert resolved is not None and isinstance(resolved.value, float)


def test_resolved_is_frozen() -> None:
    resolved = Resolved(1.55, "desired")
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.value = 1.6  # type: ignore[misc]


# ── one copy: the ingestor uses these helpers (read as source, not imported) ──


def _module(relpath: str) -> ast.Module:
    return ast.parse((REPO_ROOT / relpath).read_text())


def _imported_from(tree: ast.Module, module: str) -> set[str]:
    return {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(function: ast.FunctionDef, callee: str) -> bool:
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == callee
        for node in ast.walk(function)
    )


def test_the_ingestor_keeps_one_copy_of_each_rule() -> None:
    dispatcher = _module("ingestor/tasks/dispatcher.py")
    assert {"readback_abs_tolerance", "readback_values_equivalent", "within_relative_deadband"} <= _imported_from(
        dispatcher, "verdify_schemas.setting_values"
    )
    defined = {node.name for node in dispatcher.body if isinstance(node, ast.FunctionDef)}
    assert not {"readback_abs_tolerance", "readback_values_equivalent"} & defined
    assert _calls(_function(dispatcher, "_should_skip"), "within_relative_deadband")

    band_anchors = _module("ingestor/tasks/band_anchors.py")
    assert _calls(_function(band_anchors, "_values_equivalent"), "within_relative_deadband")

    common = _module("ingestor/tasks/_common.py")
    assert "SECOND_READBACK_ABS_TOLERANCE_PARAMS" in _imported_from(common, "verdify_schemas.setting_values")
    assigned = {
        target.id
        for node in common.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "SECOND_READBACK_ABS_TOLERANCE_PARAMS" not in assigned
    all_node = next(
        node
        for node in common.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    )
    assert "SECOND_READBACK_ABS_TOLERANCE_PARAMS" in ast.literal_eval(all_node.value)
