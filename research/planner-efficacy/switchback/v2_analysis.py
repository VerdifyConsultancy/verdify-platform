"""Frozen protocol-v2 paired analyzer interface (no data access).

The analyzer accepts only the complete, frozen assigned-day export.  It has no
database/provider/device client and no exposure-completeness filter.  Missing
locked pairs produce an inconclusive integrity result instead of pair
replacement, imputation, or a changed denominator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.stats import t as student_t

from .v2_day1_export import replay_blinded_paired_day_export

ONE_SIDED_CONFIDENCE_LEVEL = 0.975


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    unit: str
    boundary: float


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec("vpd_corridor_distance_kpa", "kPa", 0.05),
    EndpointSpec("temperature_corridor_distance_f", "degF", 0.50),
    EndpointSpec("nine_control_state_minutes", "active-or-open-state minutes", 0.0),
)


@dataclass(frozen=True)
class PairContrast:
    pair_index: int
    values: dict[str, float | None]


def paired_upper_bound(values: list[float], boundary: float) -> dict[str, float | bool | int]:
    if len(values) < 2 or any(not math.isfinite(value) for value in values):
        raise ValueError("paired upper bound requires at least two finite locked contrasts")
    data = np.asarray(values, dtype=float)
    pairs = data.size
    mean = float(data.mean())
    sd = float(data.std(ddof=1))
    critical = float(student_t.ppf(ONE_SIDED_CONFIDENCE_LEVEL, pairs - 1))
    standard_error = sd / math.sqrt(pairs)
    upper = mean + critical * standard_error
    return {
        "pairs": pairs,
        "mean": mean,
        "sample_sd": sd,
        "standard_error": standard_error,
        "t_critical": critical,
        "upper_bound": upper,
        "boundary": boundary,
        "passes": upper < boundary,
    }


def analyze_frozen_pairs(rows: list[PairContrast], *, locked_pairs: int) -> dict[str, Any]:
    indexes = [row.pair_index for row in rows]
    if indexes != list(range(locked_pairs)):
        return {
            "decision": "inconclusive_incomplete_locked_pairs",
            "locked_pairs": locked_pairs,
            "observed_pair_indexes": indexes,
            "no_pair_replacement": True,
        }
    missing = [
        (row.pair_index, endpoint.name)
        for row in rows
        for endpoint in ENDPOINTS
        if row.values.get(endpoint.name) is None
    ]
    if missing:
        return {
            "decision": "inconclusive_null_endpoint",
            "locked_pairs": locked_pairs,
            "missing": missing,
            "no_pair_replacement": True,
        }
    summaries = {
        endpoint.name: paired_upper_bound(
            [float(row.values[endpoint.name]) for row in rows],
            endpoint.boundary,
        )
        for endpoint in ENDPOINTS
    }
    return {
        "decision": "advance" if all(summary["passes"] for summary in summaries.values()) else "do_not_advance",
        "locked_pairs": locked_pairs,
        "one_sided_confidence_level": ONE_SIDED_CONFIDENCE_LEVEL,
        "endpoints": summaries,
        "primary_itt_includes_every_assignment": True,
        "exposure_is_not_an_analyzer_input": True,
        "no_pair_replacement": True,
    }


def frozen_interface_manifest() -> dict[str, Any]:
    return {
        "schema": "verdify-switchback-v2-analyzer-interface",
        "version": 1,
        "pair_contrast": "AI physical admission minus Frozen baseline, independent of chronological order",
        "required_pair_fields": [endpoint.name for endpoint in ENDPOINTS],
        "selected_benefit_endpoint": "nine_control_state_minutes",
        "one_sided_confidence_level": ONE_SIDED_CONFIDENCE_LEVEL,
        "missingness": "any null locked pair is inconclusive; no imputation/replacement/denominator change",
        "exposure_filter": "forbidden",
    }


def analyze_revealed_paired_export(
    raw: bytes, expected_sha256: str, *, locked_pairs: int, ai_label: Literal["X", "Y"]
) -> dict[str, Any]:
    """Consume the exact frozen bytes after one-way arm reveal; retain ITT rows."""
    if type(locked_pairs) is not int or locked_pairs < 2 or ai_label not in ("X", "Y"):
        raise ValueError("revealed analysis needs locked pair count and exact X/Y mapping")
    assigned = replay_blinded_paired_day_export(raw, expected_sha256)
    by_pair: dict[int, dict[str, Any]] = {}
    for index, row in assigned:
        if index >= locked_pairs:
            raise ValueError("frozen assignment exceeds the locked pair count")
        by_pair.setdefault(index, {})[row.blinded_label] = row
    if any(set(by_pair.get(index, {})) != {"X", "Y"} for index in range(locked_pairs)):
        return {
            "decision": "inconclusive_incomplete_locked_pairs",
            "locked_pairs": locked_pairs,
            "assigned_days": len(assigned),
            "observed_pair_indexes": sorted(by_pair),
            "source_export_sha256": expected_sha256,
            "no_pair_replacement": True,
        }
    frozen_label = "Y" if ai_label == "X" else "X"
    contrasts = []
    for index in range(locked_pairs):
        ai = by_pair[index][ai_label]
        frozen = by_pair[index][frozen_label]
        values = {}
        for endpoint in ENDPOINTS:
            a = getattr(ai, endpoint.name)
            b = getattr(frozen, endpoint.name)
            values[endpoint.name] = None if a is None or b is None else a - b
        contrasts.append(PairContrast(index, values))
    return {
        **analyze_frozen_pairs(contrasts, locked_pairs=locked_pairs),
        "assigned_days": len(assigned),
        "source_export_sha256": expected_sha256,
        "reveal_applied_after_freeze": True,
    }
