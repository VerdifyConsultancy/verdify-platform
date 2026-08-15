"""twin/report_agreement.py math tests (#587, audit §8.9 live-shadow gate)."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


report = _load("twin_report_agreement", REPO_ROOT / "twin" / "report_agreement.py")

START = date(2026, 8, 1)


def day_row(day: date, **overrides) -> dict:
    row = {
        "day": day,
        "ticks": 1440,
        "agreement": 1400,
        "divergence": 0,
        "warm_up": 20,
        "unmatched_state": 0,
        "gap": 20,
        "comparable_hash_mismatch": 0,
        "distinct_reasons": 2,
        "distinct_policies": 1,
    }
    row.update(overrides)
    return row


def window(days: int, mutate: dict[int, dict] | None = None) -> list[dict]:
    rows = []
    for i in range(days):
        overrides = (mutate or {}).get(i, {})
        rows.append(day_row(START + timedelta(days=i), **overrides))
    return rows


def compute(rows, days: int, min_coverage: float = 0.9):
    return report.compute_report(
        rows,
        start=START,
        end=START + timedelta(days=days - 1),
        env="prod",
        greenhouse="vallery",
        min_coverage=min_coverage,
    )


# ── Per-day accounting ───────────────────────────────────────────────────────


def test_day_summary_counts_and_coverage():
    d = report.day_summary(day_row(START), 0.9)
    assert d["comparable"] == 1400
    assert d["coverage"] == round(1400 / 1440, 6)
    assert d["policy_hash_identical"] is True
    assert d["passes"] is True


def test_day_fails_on_any_divergence():
    d = report.day_summary(day_row(START, divergence=1, agreement=1399), 0.9)
    assert d["passes"] is False


def test_day_fails_on_unmatched_state():
    d = report.day_summary(day_row(START, unmatched_state=3, agreement=1397), 0.9)
    assert d["passes"] is False


def test_day_fails_below_min_coverage_gaps_never_count_as_agreement():
    # 50% gaps: agreement count alone would look perfect — coverage fails.
    d = report.day_summary(day_row(START, agreement=700, gap=740, warm_up=0), 0.9)
    assert d["coverage"] < 0.9
    assert d["passes"] is False


def test_day_fails_on_comparable_hash_mismatch():
    d = report.day_summary(day_row(START, comparable_hash_mismatch=1), 0.9)
    assert d["policy_hash_identical"] is False
    assert d["passes"] is False


def test_empty_day_fails():
    d = report.day_summary(
        day_row(START, ticks=0, agreement=0, warm_up=0, gap=0, distinct_policies=0, distinct_reasons=0), 0.9
    )
    assert d["coverage"] == 0.0
    assert d["passes"] is False


# ── Window gate ──────────────────────────────────────────────────────────────


def test_gate_passes_on_clean_seven_day_window():
    r = compute(window(7), 7)
    assert r["gate_passes"] is True
    assert r["failures"] == []
    assert r["window"]["days"] == 7
    assert r["totals"]["ticks"] == 7 * 1440
    assert len(r["days"]) == 7
    assert r["missing_days"] == []


def test_gate_passes_at_fourteen_days_boundary():
    assert compute(window(14), 14)["gate_passes"] is True


def test_gate_fails_below_seven_days():
    r = compute(window(6), 6)
    assert r["gate_passes"] is False
    assert any(f.startswith("window_length:") for f in r["failures"])


def test_gate_fails_above_fourteen_days():
    r = compute(window(15), 15)
    assert r["gate_passes"] is False
    assert any(f.startswith("window_length:") for f in r["failures"])


def test_gate_fails_when_a_day_has_no_rows():
    rows = window(7)
    del rows[3]
    r = compute(rows, 7)
    assert r["gate_passes"] is False
    assert r["missing_days"] == [(START + timedelta(days=3)).isoformat()]
    assert any(f.startswith("days_without_ticks:") for f in r["failures"])


def test_gate_fails_on_single_divergent_day():
    r = compute(window(7, {4: {"divergence": 2, "agreement": 1398}}), 7)
    assert r["gate_passes"] is False
    assert f"day_failed:{(START + timedelta(days=4)).isoformat()}" in r["failures"]


def test_gate_fails_on_hash_mismatch_day_even_with_action_agreement():
    r = compute(window(7, {0: {"comparable_hash_mismatch": 5}}), 7)
    assert r["gate_passes"] is False


def test_totals_aggregate_across_days():
    r = compute(window(7, {1: {"gap": 100, "agreement": 1320}}), 7)
    assert r["totals"]["gap"] == 6 * 20 + 100
    assert r["totals"]["agreement"] == 6 * 1400 + 1320


# ── Machine-readable output + hash ───────────────────────────────────────────


def test_finalize_hash_is_deterministic_and_canonical():
    from verdify_schemas.policy_vector import canonical_json_bytes

    r = compute(window(7), 7)
    doc1 = report.finalize(r)
    doc2 = report.finalize(compute(window(7), 7))
    assert doc1["report_sha256"] == doc2["report_sha256"]
    assert doc1["report_sha256"] == hashlib.sha256(canonical_json_bytes(r)).hexdigest()


def test_finalize_hash_changes_with_content():
    a = report.finalize(compute(window(7), 7))
    b = report.finalize(compute(window(7, {0: {"gap": 21, "agreement": 1399}}), 7))
    assert a["report_sha256"] != b["report_sha256"]


def test_gate_constants_match_89():
    assert report.GATE_MIN_DAYS == 7
    assert report.GATE_MAX_DAYS == 14
