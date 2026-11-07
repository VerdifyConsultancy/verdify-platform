"""The 16 fixed pack sections, their order and their Qwen-token budgets (#1366, pack v1).

Static sections come first for prefix-cache reuse, data in the middle, and the task and output
contract last (Lost in the Middle; Du et al. 2025). A section over its budget is reduced by its
own reducer (fewer rows, coarser rows) and only then cut with an explicit marker. The pack as a
whole stays under PACK_HARD_CAP, and every request under REQUEST_TOKEN_CAP with max_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionSpec:
    key: str
    title: str
    budget: int
    # In the minimum pack used by the repair attempt.
    minimum: bool


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("S0", "System", 700, True),
    SectionSpec("S1", "Decision surface", 1800, True),
    SectionSpec("S2", "Greenhouse card", 1100, True),
    SectionSpec("S3", "Playbook digest", 1300, True),
    SectionSpec("S4", "Trigger and clock", 400, True),
    SectionSpec("S5", "Now", 800, True),
    SectionSpec("S6", "Forecast with band", 3400, True),
    SectionSpec("S7", "Observed 72 h", 2700, False),
    SectionSpec("S8", "Night VPD card", 600, False),
    SectionSpec("S9", "Last 6 h", 1600, False),
    SectionSpec("S10", "Response priors", 900, False),
    SectionSpec("S11", "Served-band scorecard", 1400, False),
    SectionSpec("S12", "Plan history", 2200, False),
    SectionSpec("S13", "Alerts", 500, False),
    SectionSpec("S14", "Crops", 200, False),
    SectionSpec("S15", "Task and output contract", 700, True),
)
SECTION_KEYS = tuple(spec.key for spec in SECTIONS)
BY_KEY = {spec.key: spec for spec in SECTIONS}
PACK_BUDGET = sum(spec.budget for spec in SECTIONS)  # 20,300
PACK_HARD_CAP = 25_000
# Whole-pack drop order when the sum ever exceeds PACK_HARD_CAP (budgets edited, estimate drift).
DROP_ORDER = ("S14", "S10", "S9", "S12", "S6")
# Completed medium-effort answers used 6,185-8,941 reasoning plus 1,499-2,067 answer tokens; one
# SUNRISE primary spent all of 12,000 in reasoning (2026-11-07, #1480). 24,000 is 2x that cut.
MAX_TOKENS = 24_000
REQUEST_TOKEN_CAP = 72_000  # pack + max_tokens; the route's context is 98,304
