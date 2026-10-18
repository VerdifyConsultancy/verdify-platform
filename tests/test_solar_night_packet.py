"""Cheap offline source/metadata checks for the bounded #410 diagnostic packet."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

PACKET = Path(__file__).resolve().parents[1] / "planning/reviews/2026-10-18-s3b-execution/p3-solar-night"


def load(name):
    spec = importlib.util.spec_from_file_location(name, PACKET / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load("verify_identity")
SUMMARY = load("summarize_pilot")
CONTRACT = json.loads((PACKET / "source-contract.json").read_text())
OBSERVED = [json.loads(line) for line in (PACKET / "identity-observed.jsonl").read_text().splitlines()]
IDENTITY = json.loads((PACKET / "identity-verification.json").read_text())


def lines(rows):
    return "\n".join(json.dumps(row) for row in rows).encode()


def test_actual_retained_identity_and_owning_source_match():
    VERIFY.verify_source(CONTRACT)
    result = VERIFY.verify(lines(OBSERVED), CONTRACT)
    assert result["status"] == "INSTALLED_SOURCE_AND_LEDGER_MATCH"
    assert result["preselected_pilot_night"] == "2026-10-16"
    assert not result["episode_data_read"]
    assert result["migration_stamp_methods"] == ["baseline"]


@pytest.mark.parametrize("field", ["prosrc_sha256", "definition_sha256", "argument_types", "owner", "config"])
def test_function_drift_fails_closed(field):
    rows = copy.deepcopy(OBSERVED)
    rows[1][field] = "changed"
    with pytest.raises(ValueError, match="mismatch"):
        VERIFY.verify(lines(rows), CONTRACT)


def test_overload_or_missing_function_fails_closed():
    rows = copy.deepcopy(OBSERVED)
    rows[2] = rows[1]
    with pytest.raises(ValueError, match="duplicate"):
        VERIFY.verify(lines(rows), CONTRACT)


def test_ledger_hash_change_fails_closed():
    rows = copy.deepcopy(OBSERVED)
    rows[-1]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="migration hash mismatch"):
        VERIFY.verify(lines(rows), CONTRACT)


def pilot(episodes):
    return [
        {
            "kind": "pilot_identity_guard",
            "passed": 1,
            "read_only": "on",
            "database": "verdify",
            "postmaster_started_at": IDENTITY["context"]["postmaster_started_at"],
            "identity_receipt_sha256": IDENTITY["identity_sha256"],
            "night_date": "2026-10-16",
        },
        {
            "kind": "episodes",
            "greenhouse_id": "vallery",
            "night_date": "2026-10-16",
            "identity_receipt_sha256": IDENTITY["identity_sha256"],
            "row_cap": 500,
            "row_cap_exceeded": False,
            "episode_rows_returned": len(episodes),
            "episodes": episodes,
            "window_start": "2026-10-16T06:00:00+00:00",
            "window_end": "2026-10-18T06:00:00+00:00",
        },
    ]


def episode(number, safety):
    return {
        "night_date": "2026-10-16",
        "episode_id": number,
        "safety_gate_status": safety,
        "dryout_disposition": "insufficient_evidence",
        "evidence_status": "confounded",
        "admission_status": "blocked",
        "daytime_dry_action_samples": 20,
        "daytime_hold_admission_samples": 0,
        "observed_vpd_delta_10_20m_kpa": None,
        "confound_reasons": ["simultaneous_wetting"],
    }


def test_no_episodes_is_unknown_not_zero_measured_hold():
    result = SUMMARY.summarize(lines(pilot([])), IDENTITY)
    assert result["status"] == "NO_EPISODE_ROWS"
    assert result["aggregate_safety_status"] == "pending"
    assert result["daytime_hold_admission_samples"] is None


def test_confounds_nulls_and_repeated_day_counts_preserved():
    rows = [episode(1, "pass"), episode(2, "incomplete")]
    result = SUMMARY.summarize(lines(pilot(rows)), IDENTITY)
    assert result["episodes"] == rows
    assert result["aggregate_safety_status"] == "incomplete"
    assert result["daytime_dry_action_samples"] == 20
    assert not result["whole_issue_acceptance"]


def test_overflow_is_not_complete_evidence():
    rows = pilot([episode(1, "pass")])
    rows[1]["row_cap_exceeded"] = True
    with pytest.raises(ValueError, match="truncated"):
        SUMMARY.summarize(lines(rows), IDENTITY)


def test_guard_only_timeout_is_not_no_episode_result():
    with pytest.raises(ValueError, match="complete episode envelope"):
        SUMMARY.summarize(lines(pilot([])[:1]), IDENTITY)
