#!/usr/bin/env python3
"""Replay a synthetic fixture's exact SQL export twice; never physical evidence."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research/planner-efficacy"))
analyze = importlib.import_module("switchback.v2_analysis").analyze_revealed_sql_export

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("export", type=Path)
parser.add_argument("--sha256", required=True)
parser.add_argument("--x-physical-arm", required=True, choices=("A", "B"))
args = parser.parse_args()
raw = args.export.read_bytes()
kwargs = dict(
    expected_experiment_id="21521421-4214-4214-8214-214214214217",
    expected_analyzer_environment_sha256="e" * 64,
    locked_pairs=3,
    ai_label="X" if args.x_physical_arm == "B" else "Y",
)
first = analyze(raw, args.sha256, **kwargs)
second = analyze(raw, args.sha256, **kwargs)
assert first == second
assert first["assigned_days"] == 6
assert first["no_pair_replacement"] is True
assert first["decision"] == "inconclusive_null_endpoint"
print(json.dumps(first, sort_keys=True, indent=2))
