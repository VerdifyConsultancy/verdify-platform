"""Setting values: snapping, readback comparison and the control rule (VD-29).

One copy of each rule that every setting write and readback uses: the
dispatcher in the ingestor, the settings services, the readback states and the
catalog. Everything here is pure (no database, clock or file reads). The
ingestor image carries ``verdify_schemas`` but not ``verdify_services``, so the
rules live here.

* ``snap`` / ``snap_setting`` round a value to the registry ``step``, half to
  even: the grid the controller holds (architecture §5.3 step 4).
* ``readback_values_equivalent`` is the dispatcher's comparator for plan values:
  the 1 % relative dead-band (``within_relative_deadband``), then the 1 s
  duration tolerance.
* ``readback_matches`` is the grid-aware comparator for desired values and the
  readback states (architecture §5.5): a readback matches within half a grid
  step, the value's binary32 ULP or the duration tolerance, so a one-step change
  such as ``site_pressure_hpa`` 840 → 845 is a difference, not a match.
* ``resolve_effective`` is the architecture §5.2 resolution rule: last write
  wins, a manual row beats Iris's plan, NULL releases.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from verdify_schemas.tunable_registry import get, planner_effective_bounds

# Second-based tunables whose cfg_* readbacks the firmware quantizes to whole
# seconds, so a readback within 1 s of the requested value is the same value.
SECOND_READBACK_ABS_TOLERANCE_PARAMS = frozenset(
    {
        "mister_engage_delay_s",
        "mister_all_delay_s",
        "mister_pulse_on_s",
        "mister_pulse_gap_s",
        "min_fog_on_s",
        "min_fog_off_s",
        "mist_backoff_s",
        "mist_max_closed_vent_s",
        "mist_thermal_relief_s",
        "vpd_watch_dwell_s",
    }
)

_BINARY32 = struct.Struct("<f")
_UINT32 = struct.Struct("<I")


# ── Snapping ────────────────────────────────────────────────────────────────


def snap(value: float, step: float | None, lo: float | None = None, hi: float | None = None) -> float:
    """Round ``value`` to the nearest multiple of ``step``, ties half to even.

    Decimal arithmetic on the shortest decimal form of each number, so 1.53 on
    a 0.05 grid is 1.55 and the tie 1.525 is 1.5. A ``None`` step returns the
    value unchanged. A snapped result outside [``lo``, ``hi``] returns that
    bound: two entities have a ``min_value`` off their step grid
    (``gl_main_lux_threshold`` and ``gl_grow_lux_threshold``), and their
    minimum must stay reachable.

    Raises ``ValueError`` for a non-finite value or a step that is not a
    positive finite number.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"cannot snap a non-finite value: {value!r}")
    if step is None:
        return numeric
    quantum = float(step)
    if not (math.isfinite(quantum) and quantum > 0):
        raise ValueError(f"step must be a positive finite number, not {step!r}")
    grid = Decimal(str(quantum))
    count = (Decimal(str(numeric)) / grid).to_integral_value(rounding=ROUND_HALF_EVEN)
    snapped = float(count * grid) + 0.0  # + 0.0 turns -0.0 into 0.0
    if lo is not None and snapped < lo:
        return float(lo)
    if hi is not None and snapped > hi:
        return float(hi)
    return snapped


def snap_setting(name: str, value: float) -> float:
    """``snap`` with the registry ``step`` and ``planner_effective_bounds(name)``."""
    spec = get(name)
    if spec is None:
        raise ValueError(f"{name!r} is not registered in tunable_registry")
    lo, hi = planner_effective_bounds(name)
    return snap(value, spec.step, lo, hi)


# ── Readback comparison ─────────────────────────────────────────────────────


def within_relative_deadband(last: float, val: float, rel: float = 0.01, abs_floor: float = 1e-3) -> bool:
    """True when ``last`` is within ``rel`` (1 %) of ``val``, relative to |val|.

    DI-1 (Sprint 18): the dispatcher's proportional dead-band. ``abs_floor``
    keeps a value at or near zero from making any difference look large. This
    is the one copy of the rule: the dispatcher's ``_should_skip`` and the
    band-anchor ``_values_equivalent`` call it.
    """
    return abs(last - val) / max(abs(val), abs_floor) < rel


def readback_abs_tolerance(param: str) -> float:
    """Absolute cfg-readback tolerance for quantized firmware readbacks."""
    return 1.0 if param in SECOND_READBACK_ABS_TOLERANCE_PARAMS else 0.0


def readback_values_equivalent(param: str, observed: float, desired: float) -> bool:
    """Return true when a cfg_* readback is close enough to the requested value."""
    try:
        observed_f = float(observed)
        desired_f = float(desired)
    except (TypeError, ValueError):
        return False
    if within_relative_deadband(observed_f, desired_f):
        return True
    return abs(observed_f - desired_f) <= readback_abs_tolerance(param)


def binary32_ulp(x: float) -> float:
    """Gap from ``float32(x)`` to the next binary32 value away from zero.

    ESPHome number states travel as IEEE-754 binary32, so a value of 1.6 reads
    back as 1.600000023841858; this is the width of that rounding near ``x``.
    Raises ``ValueError`` for a non-finite value or one outside the binary32
    range.
    """
    magnitude = abs(float(x))
    if not math.isfinite(magnitude):
        raise ValueError(f"binary32_ulp needs a finite value, not {x!r}")
    try:
        bits = _UINT32.unpack(_BINARY32.pack(magnitude))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"{x!r} is outside the binary32 range") from exc
    here = _BINARY32.unpack(_UINT32.pack(bits))[0]
    above = _BINARY32.unpack(_UINT32.pack(bits + 1))[0]
    if math.isinf(above):  # float32(x) is the largest finite binary32 value
        return here - _BINARY32.unpack(_UINT32.pack(bits - 1))[0]
    return above - here


def readback_tolerance(param: str, value: float) -> float:
    """Largest |readback − value| that still confirms ``value`` for ``param``.

    Half the registry step, the value's binary32 ULP or the duration tolerance,
    whichever is largest. A parameter outside the registry has no step. Raises
    ``ValueError`` like ``binary32_ulp`` for a value binary32 cannot hold.
    """
    spec = get(param)
    step = spec.step if spec is not None else None
    return max(step / 2 if step else 0.0, binary32_ulp(value), readback_abs_tolerance(param))


def _finite(value: object) -> float | None:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def readback_matches(param: str, readback: float | None, value: float | None) -> bool:
    """Grid-aware comparator: does ``readback`` confirm ``value`` for ``param``?

    False when either side is ``None``, not a number or not finite; otherwise
    ``abs(readback - value) <= readback_tolerance(param, value)``. A value the
    controller cannot hold (outside the binary32 range) never matches.
    """
    observed = _finite(readback)
    desired = _finite(value)
    if observed is None or desired is None:
        return False
    try:
        tolerance = readback_tolerance(param, desired)
    except ValueError:
        return False
    return abs(observed - desired) <= tolerance


# ── Resolution ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolved:
    """The value in force for one setting and the row it came from."""

    value: float
    applied_from: Literal["desired", "plan"]


def _field(row: object, name: str) -> Any:
    """One column of a mapping row (dict, asyncpg.Record) or an attribute row."""
    if isinstance(row, Mapping) or callable(getattr(row, "keys", None)):
        return row[name]  # type: ignore[index]
    return getattr(row, name)


def resolve_effective(desired: object | None, plan: object | None) -> Resolved | None:
    """Architecture §5.2: the one control rule (decisions, Direction).

    ``desired`` is the parameter's ``v_desired_state_current`` row (``value``,
    ``manual``, ``created_at``) or ``None``; ``plan`` is its ``v_active_plan``
    row (``value``, ``created_at``) or ``None``. A row may be a mapping (a dict
    or an asyncpg.Record) or an object with those attributes.

    * No desired row, or a released one (``value`` NULL): ``None``, so today's
      plan, band or default applies.
    * A manual desired row: its value, whatever the plan's age.
    * A plan newer than the desired row: the plan value (last write wins).
    * Otherwise the desired value; a tie goes to the desired row.

    Whether the parameter is writable now is the caller's check (§5.3 step 1).
    """
    if desired is None:
        return None
    desired_value = _field(desired, "value")
    if desired_value is None:
        return None
    if _field(desired, "manual"):
        return Resolved(float(desired_value), "desired")
    if plan is not None and _field(plan, "created_at") > _field(desired, "created_at"):
        return Resolved(float(_field(plan, "value")), "plan")
    return Resolved(float(desired_value), "desired")
