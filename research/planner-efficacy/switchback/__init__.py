"""Switchback randomization, commitment, and frozen analysis tooling (Lane G).

Implements Sections 8.3, 8.4, and the Section 8.9 treatment octets of
docs/research/planner-efficacy-current-firmware-2026-08-14.md. See
``randomization`` for the byte-exact derivations and ``analysis`` for the
frozen Section 8.4 analyzer. CLI: ``python -m switchback --help`` (run with
``uv run --project research/planner-efficacy`` from that directory).
"""

from switchback.randomization import (  # noqa: F401
    aa_treatment_bytes,
    arm_mapping,
    assignment_uuid,
    blinded_schedule,
    mapping_commitment,
    pair_order,
    qualification_treatment_bytes,
    randomized_treatment_bytes,
    resolve_schedule,
    rfc8785_canonicalize,
    rfc8785_sha256,
)
