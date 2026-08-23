"""Fixed pre-draw selector-dilution and joint advance-power tooling.

The simulator evaluates the *joint* intersection-union event: both climate
upper bounds clear their noninferiority margins and the selected nine-stream
benefit upper bound clears zero in the same replicate.  Missing any locked pair
is conservatively an inconclusive replicate.  It selects only from a declared
candidate list before randomization; there is no outcome-driven adaptation.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.stats import t as student_t

ENDPOINTS = ("vpd_corridor_distance_kpa", "temperature_corridor_distance_f", "nine_control_state_minutes")
BOUNDARIES = np.asarray((0.05, 0.50, 0.0), dtype=float)
DEFAULT_CANDIDATE_PAIRS = (15, 30, 60, 90, 120, 150, 180)


@dataclass(frozen=True)
class SelectorReplay:
    baseline: int
    moderate: int
    aggressive: int
    fallback: int

    @property
    def total(self) -> int:
        return self.baseline + self.moderate + self.aggressive + self.fallback

    @property
    def physical_nonbaseline_frequency(self) -> float:
        if self.total <= 0:
            raise ValueError("selector replay must contain at least one context")
        return (self.moderate + self.aggressive) / self.total

    def frequencies(self) -> dict[str, float]:
        if self.total <= 0:
            raise ValueError("selector replay must contain at least one context")
        return {name: getattr(self, name) / self.total for name in ("baseline", "moderate", "aggressive", "fallback")}


@dataclass(frozen=True)
class PowerAssumptions:
    paired_sd: tuple[float, float, float]
    true_nonbaseline_effect: tuple[float, float, float]
    selector_replay: SelectorReplay
    correlation: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
    complete_pair_probability: float
    one_sided_confidence_level: float = 0.975
    minimum_joint_power: float = 0.80

    def validate(self) -> None:
        sd = np.asarray(self.paired_sd, dtype=float)
        if sd.shape != (3,) or not np.all(np.isfinite(sd)) or np.any(sd <= 0):
            raise ValueError("paired_sd must contain three finite positive values")
        corr = np.asarray(self.correlation, dtype=float)
        if corr.shape != (3, 3) or not np.allclose(corr, corr.T) or not np.allclose(np.diag(corr), 1):
            raise ValueError("correlation must be a symmetric 3x3 correlation matrix")
        if np.min(np.linalg.eigvalsh(corr)) < -1e-12:
            raise ValueError("correlation matrix must be positive semidefinite")
        if not 0 < self.complete_pair_probability <= 1:
            raise ValueError("complete_pair_probability must be in (0,1]")
        if not 0.5 < self.one_sided_confidence_level < 1 or not 0 < self.minimum_joint_power < 1:
            raise ValueError("invalid confidence/power target")
        self.selector_replay.frequencies()

    @property
    def diluted_true_effect(self) -> tuple[float, float, float]:
        dilution = self.selector_replay.physical_nonbaseline_frequency
        return tuple(effect * dilution for effect in self.true_nonbaseline_effect)


def summarize_selector_replay(profiles: list[str], fallback_flags: list[bool]) -> SelectorReplay:
    if len(profiles) != len(fallback_flags):
        raise ValueError("profile/fallback replay arrays must have equal length")
    counts = {"baseline": 0, "moderate": 0, "aggressive": 0, "fallback": 0}
    for profile, fallback in zip(profiles, fallback_flags, strict=True):
        if profile not in ("baseline", "moderate", "aggressive"):
            raise ValueError(f"invalid frozen selector output {profile!r}")
        if fallback:
            counts["fallback"] += 1
        else:
            counts[profile] += 1
    return SelectorReplay(**counts)


def simulate_joint_power(
    pairs: int,
    assumptions: PowerAssumptions,
    *,
    repetitions: int,
    seed: int,
    batch_size: int = 2_000,
) -> dict[str, Any]:
    assumptions.validate()
    if pairs < 2 or repetitions < 1 or batch_size < 1:
        raise ValueError("pairs>=2 and positive repetitions/batch_size required")
    sd = np.asarray(assumptions.paired_sd, dtype=float)
    mean = np.asarray(assumptions.diluted_true_effect, dtype=float)
    corr = np.asarray(assumptions.correlation, dtype=float)
    covariance = corr * np.outer(sd, sd)
    critical = float(student_t.ppf(assumptions.one_sided_confidence_level, pairs - 1))
    rng = np.random.default_rng(seed)
    joint_pass = 0
    marginal_pass = np.zeros(3, dtype=np.int64)
    complete = 0
    remaining = repetitions
    while remaining:
        batch = min(batch_size, remaining)
        draws = rng.multivariate_normal(mean, covariance, size=(batch, pairs))
        upper = draws.mean(axis=1) + critical * draws.std(axis=1, ddof=1) / math.sqrt(pairs)
        passed = upper < BOUNDARIES
        complete_mask = rng.random(batch) < assumptions.complete_pair_probability**pairs
        complete += int(complete_mask.sum())
        marginal_pass += (passed & complete_mask[:, None]).sum(axis=0)
        joint_pass += int((passed.all(axis=1) & complete_mask).sum())
        remaining -= batch
    joint_power = joint_pass / repetitions
    return {
        "pairs": pairs,
        "repetitions": repetitions,
        "seed": seed,
        "diluted_true_effect": dict(zip(ENDPOINTS, assumptions.diluted_true_effect, strict=True)),
        "complete_all_pairs_probability_model": assumptions.complete_pair_probability**pairs,
        "simulated_complete_fraction": complete / repetitions,
        "marginal_unconditional_power": dict(zip(ENDPOINTS, (marginal_pass / repetitions).tolist(), strict=True)),
        "joint_advance_power": joint_power,
        "monte_carlo_standard_error": math.sqrt(joint_power * (1 - joint_power) / repetitions),
        "passes_minimum_joint_power": joint_power >= assumptions.minimum_joint_power,
    }


def choose_fixed_pairs(
    assumptions: PowerAssumptions,
    *,
    candidates: tuple[int, ...] = DEFAULT_CANDIDATE_PAIRS,
    repetitions: int = 25_000,
    seed: int = 588_639,
) -> dict[str, Any]:
    """Choose once from an ordered preregistered candidate set."""
    if not candidates or tuple(sorted(set(candidates))) != candidates or candidates[0] != 15:
        raise ValueError("candidate pair counts must be unique/increasing and begin at 15")
    evaluations = []
    chosen = None
    for index, pairs in enumerate(candidates):
        result = simulate_joint_power(
            pairs,
            assumptions,
            repetitions=repetitions,
            seed=seed + index,
        )
        evaluations.append(result)
        if result["passes_minimum_joint_power"]:
            chosen = pairs
            break
    if chosen is None:
        raise ValueError("no preregistered fixed pair count meets the joint-power gate")
    return {
        "chosen_pairs": chosen,
        "chosen_local_days": chosen * 2,
        "no_adaptive_sample_size": True,
        "evaluations": evaluations,
    }


def artifact_sha256(value: dict[str, Any]) -> str:
    payload = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def build_power_artifact(
    assumptions: PowerAssumptions,
    selection: dict[str, Any],
    *,
    source_files_sha256: dict[str, str],
    status: str,
    limitations: list[str],
) -> dict[str, Any]:
    assumptions.validate()
    body: dict[str, Any] = {
        "schema": "verdify-switchback-v2-power-design",
        "version": 1,
        "status": status,
        "source_files_sha256": source_files_sha256,
        "assumptions": {
            **asdict(assumptions),
            "diluted_true_effect": assumptions.diluted_true_effect,
            "selector_frequencies": assumptions.selector_replay.frequencies(),
        },
        "selection": selection,
        "limitations": limitations,
        "primary_window_local": "[06:00,24:00)",
        "expected_climate_bins": 72,
        "selected_benefit_endpoint": "nine_control_state_minutes",
        "claim": "planning operating characteristics only; no randomized efficacy data were read or computed",
    }
    body["artifact_sha256"] = artifact_sha256(body)
    return body
