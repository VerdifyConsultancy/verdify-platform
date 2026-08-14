"""Frozen primary analyzer for the planner switchback (Section 8.4).

Given a resolved schedule (see ``randomization.resolve_schedule``) and per-day
outcome rows, this module computes the pair contrasts ``D_j``, the model-based
paired-t bounds for the three co-primary endpoints, every leave-one-pair-out
bound, the intersection-union pass/fail decision with the influence-sensitivity
inconclusive rule, and the exact 2^15 randomization-inversion sensitivity over
a fixed effect grid centered at each locked decision boundary.

Outcome CSV columns (one row per randomized local day, lower is better):

    local_date          YYYY-MM-DD (must match a scheduled assignment day)
    vpd_distance_kpa    daily mean VPD distance outside the common corridor, kPa
    temp_distance_f     daily mean temperature distance outside the corridor, °F
    nine_device_minutes daily nine-device runtime, device-minutes

The primary classification requires all pairs outcome-complete (Section 8.6);
an incomplete ledger is an integrity/feasibility result, and this analyzer
refuses to emit an efficacy decision for it. The randomization-inversion grid
is a design-based sensitivity only: it is exact for a sharp constant-effect
hypothesis and can never change the locked model-based decision.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import t as student_t

# Locked critical values from Section 8.4.
T_975_DF14 = 2.144786688
T_975_DF13 = 2.160368656

ONE_SIDED_ALPHA = 0.025


@dataclass(frozen=True)
class EndpointSpec:
    """One co-primary endpoint with its locked boundary and sensitivity grid."""

    name: str
    column: str
    unit: str
    boundary: float  # locked upper-bound decision boundary for AI - Frozen
    grid_step: float  # fixed randomization-inversion grid step
    grid_half_points: int  # grid = boundary + step * k, k in [-half, +half]


# The three locked co-primary boundaries (Section 8.4) and the frozen
# randomization-inversion effect grids centered on each boundary.
CO_PRIMARY_ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec("vpd_corridor_distance", "vpd_distance_kpa", "kPa", +0.05, 0.005, 100),
    EndpointSpec("temperature_corridor_distance", "temp_distance_f", "degF", +0.50, 0.05, 100),
    EndpointSpec("nine_device_runtime", "nine_device_minutes", "device-minutes", 0.0, 10.0, 100),
)

_REQUIRED_COLUMNS = ("local_date",) + tuple(spec.column for spec in CO_PRIMARY_ENDPOINTS)


def load_outcomes_csv(path: str | Path) -> dict[str, dict[str, float]]:
    """Read outcome rows keyed by local date; values must be finite floats."""
    outcomes: dict[str, dict[str, float]] = {}
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"outcome CSV missing columns: {missing}")
        for row in reader:
            local_date = row["local_date"].strip()
            if local_date in outcomes:
                raise ValueError(f"duplicate outcome row for {local_date}")
            values: dict[str, float] = {}
            for spec in CO_PRIMARY_ENDPOINTS:
                value = float(row[spec.column])
                if not math.isfinite(value):
                    raise ValueError(f"non-finite {spec.column} on {local_date}")
                values[spec.column] = value
            outcomes[local_date] = values
    return outcomes


def pair_contrasts(resolved_schedule: dict[str, Any], outcomes: dict[str, dict[str, float]]) -> dict[str, np.ndarray]:
    """``D_j = Y_AI,j - Y_Frozen,j`` for every pair, per endpoint.

    Requires every scheduled day to have an outcome row (all pairs
    outcome-complete); raises otherwise so an incomplete ledger cannot silently
    produce an efficacy classification.
    """
    assignments = resolved_schedule["assignments"]
    pairs = int(resolved_schedule["pairs"])
    by_pair: dict[int, dict[str, dict[str, float]]] = {}
    missing_days: list[str] = []
    for row in assignments:
        arm = row["physical_arm"]
        if arm not in ("A", "B"):
            raise ValueError(f"assignment {row['local_date']} has unresolved arm {arm!r}")
        outcome = outcomes.get(row["local_date"])
        if outcome is None:
            missing_days.append(row["local_date"])
            continue
        by_pair.setdefault(int(row["pair_id"]), {})[arm] = outcome
    if missing_days:
        raise ValueError(
            "outcome-incomplete ledger (integrity/feasibility result, no efficacy "
            f"classification): missing days {missing_days}"
        )
    contrasts: dict[str, list[float]] = {spec.column: [] for spec in CO_PRIMARY_ENDPOINTS}
    for j in range(pairs):
        pair = by_pair.get(j)
        if pair is None or set(pair) != {"A", "B"}:
            raise ValueError(f"pair {j} does not contain exactly one A day and one B day")
        for spec in CO_PRIMARY_ENDPOINTS:
            contrasts[spec.column].append(pair["B"][spec.column] - pair["A"][spec.column])
    return {column: np.asarray(values, dtype=float) for column, values in contrasts.items()}


def paired_t_summary(contrasts: np.ndarray, boundary: float) -> dict[str, Any]:
    """Model-based mean ± t(0.975, m-1)·sd/sqrt(m) and boundary classification."""
    m = contrasts.size
    if m < 2:
        raise ValueError("need at least two pair contrasts")
    critical = T_975_DF14 if m == 15 else float(student_t.ppf(1.0 - ONE_SIDED_ALPHA, m - 1))
    mean = float(np.mean(contrasts))
    sd = float(np.std(contrasts, ddof=1))
    half_width = critical * sd / math.sqrt(m)
    upper = mean + half_width
    lower = mean - half_width
    return {
        "pairs": m,
        "mean": mean,
        "sd": sd,
        "t_critical": critical,
        "half_width": half_width,
        "upper_bound_97_5": upper,
        "lower_bound_97_5": lower,
        "boundary": boundary,
        "passes": bool(upper < boundary),
        "evidence_against": bool(lower > boundary),
    }


def leave_one_pair_out(contrasts: np.ndarray, boundary: float) -> list[dict[str, Any]]:
    """Upper bounds after each single-pair deletion, ``t(0.975, m-2)`` critical value."""
    m = contrasts.size
    if m < 3:
        raise ValueError("need at least three pair contrasts for leave-one-out")
    critical = T_975_DF13 if m == 15 else float(student_t.ppf(1.0 - ONE_SIDED_ALPHA, m - 2))
    results = []
    for j in range(m):
        rest = np.delete(contrasts, j)
        mean = float(np.mean(rest))
        sd = float(np.std(rest, ddof=1))
        upper = mean + critical * sd / math.sqrt(m - 1)
        results.append(
            {
                "deleted_pair": j,
                "mean": mean,
                "sd": sd,
                "upper_bound_97_5": upper,
                "passes": bool(upper < boundary),
            }
        )
    return results


def _sign_matrix(m: int) -> np.ndarray:
    """All 2^m pair-sign assignments as a (+1/-1) matrix of shape (2^m, m)."""
    indices = np.arange(2**m, dtype=np.uint32)
    bits = (indices[:, None] >> np.arange(m, dtype=np.uint32)[None, :]) & 1
    return bits.astype(np.int8) * 2 - 1


def randomization_inversion(contrasts: np.ndarray, spec: EndpointSpec) -> dict[str, Any]:
    """Exact 2^m sign-flip test inverted over the fixed grid centered at the boundary.

    For each candidate constant effect tau on the locked grid, the statistic is
    ``sum_j s_j (D_j - tau)`` over all legal pair-sign vectors; the one-sided
    lower-tail p-value is the exact fraction of sign assignments at or below
    the observed statistic. The reported upper confidence bound is the largest
    grid tau not rejected at one-sided alpha 0.025 (grid-censored if every /
    no grid point is rejected). Sensitivity only; never changes the decision.
    """
    m = contrasts.size
    signs = _sign_matrix(m).astype(np.float64)
    grid = spec.boundary + spec.grid_step * np.arange(-spec.grid_half_points, spec.grid_half_points + 1)
    scale = max(1.0, float(np.max(np.abs(contrasts)))) if m else 1.0
    tolerance = 1e-9 * scale

    # signs @ (D - tau) = signs @ D - tau * (signs @ 1); the observed statistic
    # is sum(D) - m*tau, so the whole grid needs only two matrix-vector products.
    base_sums = signs @ contrasts  # (2^m,)
    row_sums = signs.sum(axis=1)  # (2^m,)
    permuted = base_sums[:, None] - row_sums[:, None] * grid[None, :]  # (2^m, grid)
    observed = float(np.sum(contrasts)) - m * grid  # (grid,)
    p_lower = np.mean(permuted <= observed[None, :] + tolerance, axis=0)

    accepted = p_lower > ONE_SIDED_ALPHA
    if accepted.any():
        upper_index = int(np.max(np.nonzero(accepted)[0]))
        upper_bound = float(grid[upper_index])
        censored = upper_index == grid.size - 1
    else:
        upper_bound = float(grid[0])
        censored = True
    boundary_index = spec.grid_half_points  # grid is centered at the boundary
    return {
        "grid_min": float(grid[0]),
        "grid_max": float(grid[-1]),
        "grid_step": spec.grid_step,
        "p_lower_at_boundary": float(p_lower[boundary_index]),
        "upper_bound_97_5": upper_bound,
        "grid_censored": bool(censored),
    }


def analyze(resolved_schedule: dict[str, Any], outcomes: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Full frozen Section 8.4 analysis: contrasts, bounds, decision, sensitivities."""
    contrasts = pair_contrasts(resolved_schedule, outcomes)
    endpoints: dict[str, Any] = {}
    influence_sensitive = False
    all_pass = True
    any_evidence_against = False
    for spec in CO_PRIMARY_ENDPOINTS:
        d = contrasts[spec.column]
        summary = paired_t_summary(d, spec.boundary)
        loo = leave_one_pair_out(d, spec.boundary)
        endpoint_influence = any(item["passes"] != summary["passes"] for item in loo)
        influence_sensitive = influence_sensitive or endpoint_influence
        all_pass = all_pass and summary["passes"]
        any_evidence_against = any_evidence_against or summary["evidence_against"]
        endpoints[spec.name] = {
            "unit": spec.unit,
            "contrasts": [float(x) for x in d],
            "primary": summary,
            "leave_one_pair_out": loo,
            "influence_sensitive": endpoint_influence,
            "randomization_inversion": randomization_inversion(d, spec),
        }

    # Intersection-union rule with the locked influence-sensitivity override:
    # any single-pair deletion that changes any co-primary pass/fail
    # classification makes the run influence-sensitive and inconclusive.
    if influence_sensitive:
        decision = "inconclusive_influence_sensitive"
    elif all_pass:
        decision = "advance"
    elif any_evidence_against:
        decision = "evidence_against"
    else:
        decision = "inconclusive"

    return {
        "study_id": resolved_schedule["study_id"],
        "pairs": int(resolved_schedule["pairs"]),
        "decision": decision,
        "influence_sensitive": influence_sensitive,
        "endpoints": endpoints,
    }
