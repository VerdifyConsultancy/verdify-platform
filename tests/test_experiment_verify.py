"""Tests for the A/A gate checker + rollout verification harness (#587/#588).

Covers, without any database:
  * the §8.6 bin-eligibility helpers (12/15 samples, 80/88 bins, 30-min gap,
    two-hour washout → 88 bins/day);
  * every gate evaluator's pass AND fail paths on synthetic fixtures;
  * the canonical result hash (deterministic, excludes wall-clock fields);
  * the verify harness's pure helpers (day alignment, overlaps, ledger diff,
    config intent overlay merge);
  * the §8.7 blinding contract of the "Controlled experiment (blinded)"
    dashboard row — forbidden treatment-revealing identifiers must not appear
    in any of its queries, and no staged NULL placeholder may remain.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(script: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO / "scripts" / script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gates = _load("experiment-aa-gates.py", "experiment_aa_gates_under_test")
verify = _load("experiment-verify.py", "experiment_verify_under_test")

DENVER = ZoneInfo("America/Denver")
EXP_ID = "11111111-2222-3333-4444-555555555555"
BASELINE = "a" * 64


# ──────────────────────────────────────────────────────────────────────────────
# §8.6 bin-eligibility helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_two_hour_washout_yields_88_bins():
    assert gates.BINS_PER_DAY == 88
    bounds = gates.day_bin_bounds(date(2026, 8, 1), DENVER)
    assert len(bounds) == 88
    first_start = bounds[0][0].astimezone(DENVER)
    assert (first_start.hour, first_start.minute) == (2, 0)
    last_end = bounds[-1][1].astimezone(DENVER)
    assert (last_end.hour, last_end.minute) == (0, 0)
    assert last_end.date() == date(2026, 8, 2)
    # contiguous 15-minute bins
    for (s, e), (s2, _) in zip(bounds, bounds[1:], strict=False):
        assert e == s2
        assert (e - s) == timedelta(minutes=15)


def test_bin_eligibility_thresholds():
    assert gates.eligible_bin(15)
    assert gates.eligible_bin(12)
    assert not gates.eligible_bin(11)
    assert not gates.eligible_bin(0)


def test_max_invalid_gap_minutes():
    assert gates.max_invalid_gap_minutes([True] * 10) == 0
    assert gates.max_invalid_gap_minutes([True, False, False, True]) == 30
    assert gates.max_invalid_gap_minutes([False, False, False, True]) == 45
    assert gates.max_invalid_gap_minutes([True, False, True, False, False, True]) == 30


def test_day_eligibility_rules():
    all_valid = [True] * 88
    report = gates.day_eligibility(all_valid)
    assert report == {"valid_bins": 88, "max_gap_minutes": 0, "eligible": True}

    # 80 valid bins with gaps <= 30 min still eligible (8 isolated misses)
    spaced = [True] * 88
    for idx in range(0, 80, 10):
        spaced[idx] = False
    assert gates.day_eligibility(spaced)["eligible"]

    # 79 valid bins fails the 80/88 rule
    short = [True] * 88
    for idx in range(9):
        short[idx * 9] = False
    assert sum(short) == 79
    assert not gates.day_eligibility(short)["eligible"]

    # three consecutive invalid bins = 45-minute gap fails even at 85 valid
    gap = [True] * 88
    gap[10] = gap[11] = gap[12] = False
    assert not gates.day_eligibility(gap)["eligible"]

    with pytest.raises(ValueError):
        gates.day_eligibility([True] * 87)


def test_interval_union_merges_overlaps():
    t0 = datetime(2026, 8, 1, tzinfo=UTC)
    union = gates.interval_union_seconds(
        [
            (t0, t0 + timedelta(hours=1)),
            (t0 + timedelta(minutes=30), t0 + timedelta(hours=2)),  # overlaps
            (t0 + timedelta(hours=3), t0 + timedelta(hours=4)),  # disjoint
            (t0 + timedelta(hours=5), t0 + timedelta(hours=5)),  # empty
        ]
    )
    assert union == 3 * 3600


def test_result_hash_deterministic_and_excludes_wall_clock():
    payload = {
        "schema": "aa-gates/v1",
        "experiment_id": EXP_ID,
        "gates": [{"gate": 1, "passed": True}],
        "overall_pass": True,
        "computed_at": "2026-08-15T00:00:00+00:00",
    }
    h1 = gates.result_sha256(payload)
    h2 = gates.result_sha256({**payload, "computed_at": "2027-01-01T00:00:00+00:00"})
    assert h1 == h2
    assert h1 != gates.result_sha256({**payload, "overall_pass": False})
    # inserting the hash itself must not change the hash (self-excluding)
    assert gates.result_sha256({**payload, "result_sha256": h1}) == h1


# ──────────────────────────────────────────────────────────────────────────────
# Gate 1 — lanes compile the identical baseline
# ──────────────────────────────────────────────────────────────────────────────


def _lane(lane, content=BASELINE, stored=None, n=3):
    return {"lane": lane, "content_sha256": content, "bytes_sha256": stored or content, "n_vectors": n}


def test_gate1_pass():
    result = gates.gate1_lane_baseline_identity([_lane("L1"), _lane("L2")], BASELINE)
    assert result.passed
    assert result.metrics["lanes"] == {"L1": 3, "L2": 3}


def test_gate1_fails_on_single_lane():
    assert not gates.gate1_lane_baseline_identity([_lane("L1")], BASELINE).passed


def test_gate1_fails_on_hash_divergence():
    result = gates.gate1_lane_baseline_identity([_lane("L1"), _lane("L2", content="b" * 64)], BASELINE)
    assert not result.passed
    assert result.metrics["distinct_content_hashes"] == 2


def test_gate1_fails_on_bytes_hash_disagreement():
    result = gates.gate1_lane_baseline_identity([_lane("L1", stored="c" * 64), _lane("L2")], BASELINE)
    assert not result.passed


def test_gate1_fails_without_locked_baseline():
    assert not gates.gate1_lane_baseline_identity([_lane("L1"), _lane("L2")], None).passed


# ──────────────────────────────────────────────────────────────────────────────
# Gate 2 — boundary confirmation + minute coverage
# ──────────────────────────────────────────────────────────────────────────────


def _aa_days(n=7, start=datetime(2026, 8, 1, 6, tzinfo=UTC)):
    return [
        {
            "assignment_id": f"a{i}",
            "start": (start + timedelta(days=i)).isoformat(),
            "end": (start + timedelta(days=i + 1)).isoformat(),
        }
        for i in range(n)
    ]


def _exposure(assignment, start, end, confirmed=True, hash_ok=True):
    return {
        "assignment_id": assignment,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat() if end else None,
        "identity_confirmed": confirmed,
        "hash_ok": hash_ok,
    }


def test_gate2_pass_full_coverage():
    assignments = _aa_days()
    exposures = []
    for i, a in enumerate(assignments):
        s = datetime.fromisoformat(a["start"])
        exposures.append(_exposure(f"a{i}", s + timedelta(seconds=60), datetime.fromisoformat(a["end"])))
    now = datetime.fromisoformat(assignments[-1]["end"])
    result = gates.gate2_boundary_coverage(assignments, exposures, now)
    assert result.passed
    assert result.metrics["coverage_fraction"] >= 0.99


def test_gate2_fails_on_late_boundary_confirmation():
    assignments = _aa_days(1)
    s = datetime.fromisoformat(assignments[0]["start"])
    e = datetime.fromisoformat(assignments[0]["end"])
    exposures = [_exposure("a0", s + timedelta(seconds=200), e)]  # >120 s
    result = gates.gate2_boundary_coverage(assignments, exposures, e)
    assert not result.passed
    assert any("no identity-confirmed activation" in v for v in result.violations)


def test_gate2_fails_below_99_percent_coverage():
    assignments = _aa_days(1)
    s = datetime.fromisoformat(assignments[0]["start"])
    e = datetime.fromisoformat(assignments[0]["end"])
    exposures = [_exposure("a0", s + timedelta(seconds=30), e - timedelta(hours=1))]
    result = gates.gate2_boundary_coverage(assignments, exposures, e)
    assert not result.passed
    assert result.metrics["coverage_fraction"] < 0.99


def test_gate2_ignores_wrong_hash_exposures():
    assignments = _aa_days(1)
    s = datetime.fromisoformat(assignments[0]["start"])
    e = datetime.fromisoformat(assignments[0]["end"])
    exposures = [_exposure("a0", s + timedelta(seconds=30), e, hash_ok=False)]
    assert not gates.gate2_boundary_coverage(assignments, exposures, e).passed


def test_gate2_skips_future_assignments():
    assignments = _aa_days(7)
    now = datetime.fromisoformat(assignments[0]["start"])  # nothing elapsed
    result = gates.gate2_boundary_coverage(assignments, [], now)
    assert not result.passed  # nothing confirmable is itself a failure
    assert result.metrics["assignments_elapsed"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Gate 3 — unauthorized writers
# ──────────────────────────────────────────────────────────────────────────────


def test_gate3_pass():
    assert gates.gate3_unauthorized_writers(0, [], []).passed


def test_gate3_fails_on_lineage_mismatch():
    result = gates.gate3_unauthorized_writers(4, [], [])
    assert not result.passed
    assert result.metrics["lineage_mismatches"] == 4


def test_gate3_fails_on_override_event():
    events = [{"event_kind": "override", "severity": "warning", "recorded_at": "2026-08-02T03:00:00Z"}]
    assert not gates.gate3_unauthorized_writers(0, events, []).passed


def test_gate3_fails_on_foreign_setpoint_writer():
    rows = [{"source": "plan", "parameter": "temp_target_f", "n": 12}]
    result = gates.gate3_unauthorized_writers(0, [], rows)
    assert not result.passed
    assert any("experiment-owned parameter" in v for v in result.violations)


# ──────────────────────────────────────────────────────────────────────────────
# Gate 4 — climate bins + actuator streams
# ──────────────────────────────────────────────────────────────────────────────


def _full_bins(days, n_valid=15):
    return [
        {"day": d.isoformat(), "bin_index": idx, "n_samples": 15, "n_valid": n_valid}
        for d in days
        for idx in range(gates.BINS_PER_DAY)
    ]


def _full_streams(days, actuators=gates.DEFAULT_ACTUATORS):
    return [{"day": d.isoformat(), "actuator": a, "n": 88} for d in days for a in actuators]


def test_gate4_pass():
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(7)]
    result = gates.gate4_bins_and_streams(_full_bins(days), _full_streams(days), days)
    assert result.passed
    assert result.metrics["valid_fraction"] == 1.0


def test_gate4_fails_below_98_percent_bins():
    days = [date(2026, 8, 1)]
    bins = _full_bins(days)
    for row in bins[:3]:  # 85/88 = 96.6% < 98%
        row["n_valid"] = 0
    result = gates.gate4_bins_and_streams(bins, _full_streams(days), days)
    assert not result.passed
    assert result.metrics["valid_bins"] == 85


def test_gate4_counts_missing_bins_as_invalid():
    days = [date(2026, 8, 1)]
    bins = _full_bins(days)[:80]  # 8 bins simply absent from the DB
    result = gates.gate4_bins_and_streams(bins, _full_streams(days), days)
    assert not result.passed


def test_gate4_fails_on_absent_actuator_stream():
    days = [date(2026, 8, 1)]
    streams = [r for r in _full_streams(days) if r["actuator"] != "heat1"]
    result = gates.gate4_bins_and_streams(_full_bins(days), streams, days)
    assert not result.passed
    assert any("heat1" in v for v in result.violations)


def test_gate4_day_rule_reported():
    days = [date(2026, 8, 1)]
    bins = _full_bins(days)
    for row in bins[10:13]:  # 45-minute gap → day ineligible
        row["n_valid"] = 0
    result = gates.gate4_bins_and_streams(bins, _full_streams(days), days)
    report = result.metrics["day_eligibility"]["2026-08-01"]
    assert report["max_gap_minutes"] == 45
    assert not report["eligible"]


# ──────────────────────────────────────────────────────────────────────────────
# Gate 5 — action-row vector joins
# ──────────────────────────────────────────────────────────────────────────────


def test_gate5_pass():
    counts = {"n_eligible": 5000, "n_null_vector": 0, "n_missing_vector_row": 0, "n_unconfirmed_vector": 0}
    assert gates.gate5_action_vector_joins(counts).passed


@pytest.mark.parametrize(
    "bad",
    [
        {"n_eligible": 0},
        {"n_eligible": 100, "n_null_vector": 1},
        {"n_eligible": 100, "n_missing_vector_row": 2},
        {"n_eligible": 100, "n_unconfirmed_vector": 3},
    ],
)
def test_gate5_fail_paths(bad):
    counts = {"n_null_vector": 0, "n_missing_vector_row": 0, "n_unconfirmed_vector": 0, **bad}
    assert not gates.gate5_action_vector_joins(counts).passed


# ──────────────────────────────────────────────────────────────────────────────
# Gate 6 — manual replay/HIL attestation
# ──────────────────────────────────────────────────────────────────────────────


def _attestation(**overrides):
    doc = {
        "experiment_id": EXP_ID,
        "replay_pass": True,
        "hil_pass": True,
        "added_safety_events": 0,
        "signed_off_by": "jason",
        "date": "2026-08-15",
    }
    doc.update(overrides)
    return doc


def test_gate6_pass():
    assert gates.gate6_attestation(_attestation(), EXP_ID).passed


def test_gate6_fails_without_attestation():
    result = gates.gate6_attestation(None, EXP_ID)
    assert not result.passed
    assert any("--attestation" in v for v in result.violations)


@pytest.mark.parametrize(
    "overrides",
    [
        {"experiment_id": "99999999-0000-0000-0000-000000000000"},
        {"replay_pass": False},
        {"hil_pass": None},
        {"added_safety_events": 1},
        {"signed_off_by": ""},
        {"date": ""},
    ],
)
def test_gate6_fail_paths(overrides):
    assert not gates.gate6_attestation(_attestation(**overrides), EXP_ID).passed


# ──────────────────────────────────────────────────────────────────────────────
# Verify-harness pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_assignment_day_alignment():
    start = datetime(2026, 8, 1, 0, 0, tzinfo=DENVER).astimezone(UTC)
    end = datetime(2026, 8, 2, 0, 0, tzinfo=DENVER).astimezone(UTC)
    assert verify.assignment_day_aligned(start, end, DENVER)
    assert not verify.assignment_day_aligned(start + timedelta(hours=1), end, DENVER)
    assert not verify.assignment_day_aligned(start, end + timedelta(days=1), DENVER)


def test_find_overlaps_and_consecutive_days():
    d0 = datetime(2026, 8, 1, 6, tzinfo=UTC)
    ranges = [(d0 + timedelta(days=i), d0 + timedelta(days=i + 1)) for i in range(7)]
    assert verify.find_overlaps(ranges) == []
    assert verify.consecutive_local_days([s for s, _ in ranges], DENVER)

    overlapping = ranges + [(d0 + timedelta(hours=12), d0 + timedelta(hours=36))]
    assert verify.find_overlaps(overlapping)
    gappy = [ranges[0][0], ranges[3][0]]
    assert not verify.consecutive_local_days(gappy, DENVER)


def test_classify_migrations():
    repo_files = [("001-a.sql", "sha1"), ("002-b.sql", "sha2"), ("003-c.sql", "sha3"), ("004-d.sql", "sha4")]
    ledger = {
        "db/migrations/001-a.sql": "sha1",  # current
        "db/migrations/002-b.sql": None,  # baseline stamp
        "db/migrations/003-c.sql": "EDITED",  # mismatch
        # 004-d.sql absent → pending
    }
    result = verify.classify_migrations(repo_files, ledger)
    assert result == {
        "current": ["001-a.sql"],
        "baseline": ["002-b.sql"],
        "mismatch": ["003-c.sql"],
        "pending": ["004-d.sql"],
    }


def test_effective_config_intent_overlay_wins(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: verdify-config\n"
        'data:\n  VERDIFY_POLICY_VECTOR_MODE: "off"\n  VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED: "1"\n'
    )
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: verdify-config\n"
        'data:\n  VERDIFY_POLICY_VECTOR_MODE: "shadow"\n  VERDIFY_ACTIVE_EXPERIMENT_ID: "abc"\n'
    )
    intent = verify.effective_config_intent([base, overlay, tmp_path / "missing.yaml"])
    assert intent["VERDIFY_POLICY_VECTOR_MODE"] == "shadow"
    assert intent["VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED"] == "1"
    assert intent["VERDIFY_ACTIVE_EXPERIMENT_ID"] == "abc"


# ──────────────────────────────────────────────────────────────────────────────
# §8.7 blinding contract of the dashboard row
# ──────────────────────────────────────────────────────────────────────────────

# Identifiers that could reveal treatment/content identity if they appeared in a
# blinded-row query. "arm" alone is NOT forbidden ('armed' is a public lifecycle
# status); the treatment-revealing identifiers are.
FORBIDDEN_IN_BLINDED_SQL = (
    "content_sha256",
    "canonical_bytes",
    "arm_label",
    "physical_arm",
    "control_arm_resolutions",
    "policy_templates",
    "policy_template_components",
    "policy_proposals",
    "template_id",
    "proposal_id",
    "producer",
    "moderate",
    "aggressive",
    "prompt",
    "model_id",
    "normalized_value",
    "encoded_value",
    "mapping_commitment",
    "beacon",
    "detail",  # experiment_events.detail may carry identifying lineage
)


def _blinded_row():
    dashboard = json.loads((REPO / "grafana/dashboards/site-intelligence-planning.json").read_text())
    rows = [p for p in dashboard["panels"] if p.get("id") == 118]
    assert len(rows) == 1, "the 'Controlled experiment (blinded)' row (id 118) must exist"
    return rows[0]


def test_blinded_row_queries_are_live_and_blinded():
    row = _blinded_row()
    assert row["title"] == "Controlled experiment (blinded)"
    query_panels = [p for p in row["panels"] if p.get("targets")]
    assert len(query_panels) >= 6, "coverage/identity/receipts/outbox/events/state panels expected"
    for panel in query_panels:
        for target in panel["targets"]:
            sql = target["rawSql"]
            lowered = sql.lower()
            assert "pending lane b" not in lowered, f"panel {panel['id']} still carries staged SQL"
            assert "select null" not in lowered, f"panel {panel['id']} still returns staged NULLs"
            for token in FORBIDDEN_IN_BLINDED_SQL:
                assert token not in lowered, (
                    f"panel {panel['id']} ({panel['title']!r}) query references forbidden "
                    f"identifier {token!r} — §8.7 blinding violation"
                )


def test_blinded_row_identity_panel_renders_boolean_only():
    row = _blinded_row()
    panel = next(p for p in row["panels"] if p["id"] == 121)
    sql = panel["targets"][0]["rawSql"]
    # the equality must be computed in SQL; the hash columns may be compared but
    # only a boolean column may be selected out
    select_clause = sql.split("FROM")[0]
    assert "BOOL_AND" in select_clause
    assert select_clause.strip().lower().endswith("as identity_ok")


def test_blinded_row_descriptions_do_not_promise_treatment_data():
    row = _blinded_row()
    for panel in row["panels"]:
        text = json.dumps(panel.get("description", "")) + json.dumps(panel.get("options", {}).get("content", ""))
        assert "A/B" not in text.replace("X/Y→A/B", "").replace("X/Y\\u2192A/B", ""), (
            f"panel {panel['id']} description references unblinded arms"
        )
