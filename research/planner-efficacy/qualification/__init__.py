"""Step-test qualification machinery (audit §8.3, #584/#588, epic #581).

- ``settling`` — the frozen disturbance-adjusted first-order settling-time
  analyzer and the qualification gate (max over 96 transitions x 3 endpoints
  <= 2 h, identity confirmed within 120 s, locked diagnostics).
- ``qualification-spec-v1.template.yaml`` — the specification instance
  template (24 edge/regime FIFO cell queues, eligibility predicates,
  45-local-day window, analyzer thresholds; revision pins marked TO-LOCK).

See README.md in this directory for the spec-hash -> create -> arm ->
worker -> analyzer -> A/A-binding flow.
"""

from qualification.settling import (
    ANALYZER_VERSION,
    ENDPOINT_BANDS,
    EXPECTED_TRANSITIONS,
    GATE_MAX_SETTLING_H,
    IDENTITY_CONFIRM_MAX_S,
    analyze,
    analyze_endpoint,
    analyze_transition,
    fit_first_order,
    result_sha256,
    settling_time_h,
)

__all__ = [
    "ANALYZER_VERSION",
    "ENDPOINT_BANDS",
    "EXPECTED_TRANSITIONS",
    "GATE_MAX_SETTLING_H",
    "IDENTITY_CONFIRM_MAX_S",
    "analyze",
    "analyze_endpoint",
    "analyze_transition",
    "fit_first_order",
    "result_sha256",
    "settling_time_h",
]
