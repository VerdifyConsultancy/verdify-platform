#!/usr/bin/env python3
"""Freeze synthetic #371 counterexamples against the actual legacy writer block.

Read-only with respect to DB, devices and historical summaries. This is a
diagnostic baseline, NOT a corrected metric, physical-duration estimate or trial
endpoint. No provider credentials or production environment are consumed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ingestor/tasks/daily.py"
BANDS = {"temp_low": 60.0, "temp_high": 80.0, "vpd_low": 0.5, "vpd_high": 1.5}


class _NoGradedCredit:
    def add_reading(self, *args, **kwargs):
        pass


def writer_block(source: str) -> ast.Module:
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_refresh_daily_summary_for_date"
    )

    # Select the existing assignments/loop verbatim. Do not copy the algorithm
    # into a purported production-equivalent reference implementation.
    def assigns(node, name):
        return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)

    start = next(i for i, node in enumerate(function.body) if assigns(node, "heat_s"))
    end = next(i for i, node in enumerate(function.body) if assigns(node, "stress"))
    if end <= start:
        raise ValueError("writer block changed: review audit extraction")
    return ast.Module(body=function.body[start : end + 1], type_ignores=[])


def _row(minute=0, *, temp=70.0, vpd=1.0):
    return {
        "ts": datetime(2026, 9, 4, tzinfo=UTC) + timedelta(minutes=minute),
        "temp_avg": temp,
        "vpd_avg": vpd,
    }


def cases():
    return {
        "empty": ([], BANDS),
        "missing_vpd": ([_row(vpd=None)], BANDS),
        "missing_temperature": ([_row(temp=None)], BANDS),
        "missing_vpd_target": ([_row()], {k: v for k, v in BANDS.items() if k != "vpd_high"}),
        "duplicate_hot_minute": ([_row(temp=90)] * 60, BANDS),
        "sparse_hot_samples": ([_row(temp=90), _row(360, temp=90)], BANDS),
        "nan_temperature": ([_row(temp=float("nan"))], BANDS),
        "fully_observed_in_band": ([_row()], BANDS),
        "fully_observed_out_of_band": ([_row(temp=90, vpd=2)], BANDS),
    }


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def axis_reference(readings, bands, axis):
    """Only an eligible-reading reference; deliberately no duration inference."""
    lo, hi = bands.get(f"{axis}_low"), bands.get(f"{axis}_high")
    if not (_finite(lo) and _finite(hi) and lo <= hi):
        return {"eligible_readings": 0, "in_band_pct": None}
    values = [r[f"{axis}_avg"] for r in readings if _finite(r[f"{axis}_avg"])]
    return {
        "eligible_readings": len(values),
        "in_band_pct": 100 * sum(lo <= v <= hi for v in values) / len(values) if values else None,
    }


def build_report():
    source = SOURCE.read_text()
    module = writer_block(source)
    code = compile(module, str(SOURCE), "exec")
    results = []
    for name, (readings, bands) in cases().items():
        namespace = {
            "readings": readings,
            "_band_at": lambda parameter, ts: bands.get(parameter),
            "grade_acc": _NoGradedCredit(),
            "zone_bands": {},
            "relay_state_at": None,
        }
        exec(code, namespace)  # noqa: S102 — fixed repository source, never user-supplied code/path
        references = {axis: axis_reference(readings, bands, axis) for axis in ("temp", "vpd")}
        findings = []
        for axis, reference in references.items():
            actual = namespace[f"{axis}_compliance_pct"]
            if actual != reference["in_band_pct"]:
                findings.append(f"{axis}_eligible_reading_fraction_mismatch")
        unique_minutes = len({r["ts"].replace(second=0, microsecond=0) for r in readings})
        if len(readings) > unique_minutes:
            findings.append("duplicate_rows_counted_as_distinct_nominal_minutes")
        if name == "sparse_hot_samples":
            findings.append("nominal_row_hours_do_not_establish_elapsed_exposure")
        results.append(
            {
                "case": name,
                "input_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "bands": bands,
                            "rows": [
                                {
                                    key: (
                                        value.isoformat()
                                        if isinstance(value, datetime)
                                        else "nonfinite:NaN"
                                        if isinstance(value, float) and math.isnan(value)
                                        else value
                                    )
                                    for key, value in row.items()
                                }
                                for row in readings
                            ],
                        },
                        sort_keys=True,
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
                "input_rows": len(readings),
                "unique_observed_minutes": unique_minutes,
                "legacy_scored_readings": namespace["scored_readings"],
                "legacy_denominator": namespace["n"],
                "legacy_binary_pct": {
                    "joint": namespace["compliance_pct"],
                    "temp": namespace["temp_compliance_pct"],
                    "vpd": namespace["vpd_compliance_pct"],
                },
                "legacy_nominal_stress_h": namespace["stress"],
                "independent_axis_reading_reference": references,
                "findings": findings,
            }
        )
    return {
        "schema": "verdify-scorecard-writer-counterexamples-v1",
        "source_path": "ingestor/tasks/daily.py",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "executed_ast_sha256": hashlib.sha256(ast.dump(module).encode()).hexdigest(),
        "synthetic_only": True,
        "outcome_acceptance": False,
        "input_contract": {
            "bands": BANDS,
            "temperature_unit": "degF",
            "vpd_unit": "kPa",
            "target_basis": "synthetic desired history; not frozen crop targets or device consumption",
        },
        "limitations": [
            "No DB query, role, SQL migration or runtime adoption is tested.",
            "No fixed sensor panel, physical elapsed exposure or historical target validity is inferred.",
            "Graded accumulation is stubbed; only the actual binary writer block is executed.",
            "Repair must preserve prior metric revisions instead of silently rewriting history.",
        ],
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="create a new synthetic receipt, refusing overwrite")
    parser.add_argument("--check", type=Path, help="compare a frozen receipt to current source execution")
    args = parser.parse_args()
    if args.output and args.check:
        parser.error("choose --output or --check")
    report = build_report()
    if args.check:
        if json.loads(args.check.read_text()) != report:
            print("receipt differs: retain baseline and review source/measurement revision")
            return 1
        print("frozen counterexample receipt reproduced; NOT outcome acceptance")
    elif args.output:
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(f"created synthetic counterexample receipt: {args.output}")
    else:
        print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
