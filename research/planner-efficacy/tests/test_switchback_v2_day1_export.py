from __future__ import annotations

import json
from pathlib import Path

from switchback.v2_day1_export import freeze_blinded_day_export, replay_blinded_day_export
from switchback.v2_outcomes import make_randomized_itt_row

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "protocols/assigned-day-itt-v2.fixtures.json"


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_every_checked_in_day1_case_replays_to_identical_bytes_and_hash() -> None:
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["schema"] == "verdify-experiment-v2-day1-export-fixtures-v1"
    assert {case["name"] for case in fixture["cases"]} == {
        "complete_full_fidelity",
        "fallback_zero_exposure",
        "rescue_null_outcome",
    }
    for case in fixture["cases"]:
        raw = _canonical(case["canonical_export"])
        rows = replay_blinded_day_export(raw, case["export_sha256"])
        repeated, repeated_sha = freeze_blinded_day_export(list(rows))
        assert repeated == raw
        assert repeated_sha == case["export_sha256"]
        assert len(rows) == 1


def test_exposure_is_fidelity_only_and_never_selects_the_primary_itt_row() -> None:
    common = {
        "assignment_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "local_date": "2026-11-02",
        "blinded_label": "X",
        "climate": (0.1, 0.2, None),
        "equipment": (30.0, None),
        "fallback_or_rescue": False,
    }
    zero = make_randomized_itt_row(**common, exposure_seconds=0)
    full = make_randomized_itt_row(**common, exposure_seconds=64_800)
    zero_payload = json.loads(freeze_blinded_day_export([zero])[0])["rows"][0]
    full_payload = json.loads(freeze_blinded_day_export([full])[0])["rows"][0]
    zero_fidelity = zero_payload.pop("fidelity")
    full_fidelity = full_payload.pop("fidelity")
    assert zero_payload == full_payload
    assert zero_fidelity != full_fidelity
    assert zero_payload["fixed_window_utc_start"] == "2026-11-02T13:00:00Z"
    assert zero_payload["fixed_window_utc_end"] == "2026-11-03T07:00:00Z"


def test_blinded_export_never_contains_mapping_secret_or_physical_arm() -> None:
    lowered = FIXTURE.read_text().lower()
    for forbidden in (
        '"mapping"',
        '"secret"',
        '"physical_arm"',
        '"x_physical_arm"',
        '"y_physical_arm"',
        '"efficacy"',
    ):
        assert forbidden not in lowered
