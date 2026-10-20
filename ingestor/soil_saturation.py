"""Alert-decision foundation for #297. No irrigation commands or DB writes.

No saturation target is commissioned today. The scheduled status probe reports
unavailable, without querying the database or emitting a synthetic alert. A later
commissioning change must supply measured targets, source/calibration identity,
and an independently reviewed alert lifecycle adapter before enabling evaluation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SaturationTarget:
    sensor_id: str
    calibration_id: str
    evidence_ref: str
    high_pct: float
    recovery_pct: float
    hold_s: int
    recovery_s: int
    max_gap_s: int
    max_age_s: int


@dataclass(frozen=True)
class Sample:
    at: datetime
    pct: float | None
    sensor_id: str
    calibration_id: str


@dataclass(frozen=True)
class Decision:
    available: bool
    active: bool
    transition: str | None
    reason: str


def evaluate_saturation(
    samples: list[Sample], target: SaturationTarget | None, now: datetime, *, previous_active: bool = False
) -> Decision:
    """Fail closed on missing truth; unavailable data never clears an active latch."""

    def unavailable(reason):
        return Decision(False, previous_active, None, reason)

    if target is None:
        return unavailable("uncommissioned_targets")
    numbers = (
        target.high_pct,
        target.recovery_pct,
        target.hold_s,
        target.recovery_s,
        target.max_gap_s,
        target.max_age_s,
    )
    if (
        not target.sensor_id
        or not target.calibration_id
        or not target.evidence_ref
        or not all(math.isfinite(n) for n in numbers)
        or not 0 < target.recovery_pct < target.high_pct <= 100
        or min(target.hold_s, target.recovery_s, target.max_gap_s, target.max_age_s) <= 0
        or target.max_gap_s > min(target.hold_s, target.recovery_s)
    ):
        return unavailable("invalid_measured_target")
    if now.tzinfo is None or not samples or len(samples) > 4096:
        return unavailable("missing_or_unbounded_samples")
    duration = target.recovery_s if previous_active else target.hold_s
    start = now - timedelta(seconds=duration)
    ordered = sorted(samples, key=lambda sample: sample.at) if all(s.at.tzinfo for s in samples) else []
    if not ordered:
        return unavailable("naive_sample_timestamp")
    # Include the immediately preceding anchor to prove continuous coverage at
    # the start boundary, without allowing old history to invalidate recovery.
    before = [s for s in ordered if s.at <= start]
    window = ([before[-1]] if before else []) + [s for s in ordered if s.at > start]
    if not before or (start - before[-1].at).total_seconds() > target.max_gap_s:
        return unavailable("insufficient_window_coverage")
    for sample in window:
        if sample.at > now or (sample.sensor_id, sample.calibration_id) != (target.sensor_id, target.calibration_id):
            return unavailable("source_or_time_mismatch")
        if (
            sample.pct is None
            or isinstance(sample.pct, bool)
            or not math.isfinite(sample.pct)
            or not 0 < sample.pct <= 100
        ):
            return unavailable("invalid_probe_reading")
    if len(window) < 2 or (now - window[-1].at).total_seconds() > min(target.max_age_s, target.max_gap_s):
        return unavailable("stale_latest_sample")
    if any(not 0 < (b.at - a.at).total_seconds() <= target.max_gap_s for a, b in zip(window, window[1:], strict=False)):
        return unavailable("sample_gap_or_duplicate")
    if previous_active:
        recovered = all(s.pct <= target.recovery_pct for s in window)
        return Decision(
            True, not recovered, "clear" if recovered else None, "sustained_recovery" if recovered else "latched"
        )
    saturated = all(s.pct >= target.high_pct for s in window)
    return Decision(
        True, saturated, "open" if saturated else None, "sustained_saturation" if saturated else "below_threshold"
    )


async def saturation_evaluation_status(_pool):
    """Explicit disabled posture, visible from the actual ingestor scheduler."""
    decision = evaluate_saturation([], None, datetime.now(UTC))
    log.info(
        "soil_saturation available=%s active=%s reason=%s actuation=disabled",
        decision.available,
        decision.active,
        decision.reason,
    )
    return decision
