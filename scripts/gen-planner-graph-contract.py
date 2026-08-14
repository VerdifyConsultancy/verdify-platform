#!/usr/bin/env python3
"""Regenerate the planner_graph Tier-1 contract block from the canonical registry.

planner_graph/verdify_contract.py historically carried a HAND-COPY of the
Tier-1 tunable surface, and it drifted (audit §8.8: 39 stale defaults vs the
canonical set — missing band_track_fraction, cool_stage2_exit_hysteresis_f,
night_vpd_bias_kpa, vent_exchange_fraction; retaining the obsolete
fog_stress_min_dew_margin_f, fog_stress_window_latest_hour,
sw_fog_stress_window_extend_enabled — plus the wire-v2-retired
direct_wet_stress_latest_hour). This generator rewrites the sentinel-marked
block in that file from ``verdify_schemas.tunable_registry.TIER1_REG`` so the
graph path's contract can never silently diverge again.

Usage:
    python scripts/gen-planner-graph-contract.py            # rewrite in place
    python scripts/gen-planner-graph-contract.py --check    # CI drift gate

The drift test (tests/test_planner_graph_contract.py) runs the --check path
and additionally pins the field set/defaults to the registry.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CONTRACT_PATH = REPO_ROOT / "planner_graph" / "verdify_contract.py"
BEGIN_MARKER = "# --- BEGIN GENERATED TIER1 (scripts/gen-planner-graph-contract.py) ---"
END_MARKER = "# --- END GENERATED TIER1 ---"


def render_block() -> str:
    """The generated block, markers included, exactly as committed."""
    from verdify_schemas.tunable_registry import (
        REGISTRY,
        TIER1_REG,
        WIRE_SCHEMA_VERSION,
    )

    lines = [
        BEGIN_MARKER,
        "# GENERATED from verdify_schemas.tunable_registry (TIER1_REG) — DO NOT EDIT",
        "# BY HAND. Regenerate with:",
        "#     python scripts/gen-planner-graph-contract.py",
        "# A drift test (tests/test_planner_graph_contract.py) fails CI when this",
        "# block diverges from the canonical registry. The planner is standalone, so",
        "# the values are materialized here instead of importing Verdify packages at",
        "# runtime — but the SOURCE is the registry, never a hand-copy (#585,",
        "# audit §8.8).",
        f"TIER1_CONTRACT_WIRE_SCHEMA_VERSION = {WIRE_SCHEMA_VERSION}",
        f"TIER1_CONTRACT_FIELD_COUNT = {len(TIER1_REG)}",
        "",
        "TIER1_PLAN_DEFAULTS: dict[str, float] = {",
    ]
    for name in sorted(TIER1_REG):
        lines.append(f'    "{name}": {float(REGISTRY[name].default)!r},')
    lines.append("}")
    lines.append(END_MARKER)
    return "\n".join(lines)


def apply(source: str) -> str:
    begin = source.index(BEGIN_MARKER)
    end = source.index(END_MARKER) + len(END_MARKER)
    return source[:begin] + render_block() + source[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed contract matches the registry")
    args = parser.parse_args(argv)

    source = CONTRACT_PATH.read_text()
    if BEGIN_MARKER not in source or END_MARKER not in source:
        print(f"ERROR: sentinel markers missing from {CONTRACT_PATH}", file=sys.stderr)
        return 2
    regenerated = apply(source)
    if args.check:
        if regenerated != source:
            print(
                "ERROR: planner_graph/verdify_contract.py Tier-1 block drifted from "
                "verdify_schemas.tunable_registry — run scripts/gen-planner-graph-contract.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {CONTRACT_PATH.relative_to(REPO_ROOT)} Tier-1 block matches the canonical registry")
        return 0
    if regenerated != source:
        CONTRACT_PATH.write_text(regenerated)
        print(f"rewrote {CONTRACT_PATH.relative_to(REPO_ROOT)}")
    else:
        print(f"{CONTRACT_PATH.relative_to(REPO_ROOT)} already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
