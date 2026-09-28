"""Exact frozen assignment-pair lineage into the post-reveal analyzer (#783)."""

from __future__ import annotations

import json

import pytest
from switchback.v2_analysis import analyze_revealed_paired_export
from switchback.v2_day1_export import freeze_blinded_paired_day_export, replay_blinded_paired_day_export
from switchback.v2_outcomes import make_randomized_itt_row


def _rows():
    values = (
        (0, "2026-11-02", "X", (0.10, 0.10, None), (100.0, None), False, 64_800),
        (0, "2026-11-03", "Y", (0.12, 0.11, None), (120.0, None), True, 0),
        (1, "2026-11-04", "Y", (0.20, 0.20, None), (200.0, None), False, 64_800),
        (1, "2026-11-05", "X", (0.21, 0.21, None), (210.0, None), False, 64_800),
    )
    return [
        (
            index,
            make_randomized_itt_row(
                assignment_id=f"00000000-0000-4000-8000-{day[-2:]:0>12}",
                local_date=day,
                blinded_label=label,
                climate=climate,
                equipment=equipment,
                fallback_or_rescue=fallback,
                exposure_seconds=exposure,
            ),
        )
        for index, day, label, climate, equipment, fallback, exposure in values
    ]


def test_exact_paired_export_replays_twice_and_analyzer_retains_zero_exposure():
    assignments = _rows()
    raw, digest = freeze_blinded_paired_day_export(assignments)
    assert digest == "f513de4b786724d751398c37655287cfa80c2b05b434b07a3694d956bdc2e8e1"
    assert freeze_blinded_paired_day_export(assignments[::-1]) == (raw, digest)
    assert replay_blinded_paired_day_export(raw, digest) == tuple(assignments)
    assert b'"physical_arm"' not in raw and b'"mapping"' not in raw
    result = analyze_revealed_paired_export(raw, digest, locked_pairs=2, ai_label="X")
    assert result["assigned_days"] == 4
    assert result["source_export_sha256"] == digest
    assert result["primary_itt_includes_every_assignment"] is True
    assert result["endpoints"]["nine_control_state_minutes"]["pairs"] == 2
    assert assignments[1][1].exposure_seconds == 0


def test_first_day_missing_pair_and_null_rescue_are_inconclusive_without_replacement():
    assignments = _rows()
    partial, partial_sha = freeze_blinded_paired_day_export(assignments[:3])
    incomplete = analyze_revealed_paired_export(partial, partial_sha, locked_pairs=2, ai_label="X")
    assert incomplete["decision"] == "inconclusive_incomplete_locked_pairs"
    assert incomplete["assigned_days"] == 3
    assert incomplete["no_pair_replacement"] is True

    rescue = make_randomized_itt_row(
        assignment_id=assignments[3][1].assignment_id,
        local_date=assignments[3][1].local_date,
        blinded_label="X",
        climate=(None, None, "climate_completeness"),
        equipment=(None, "source_unavailable"),
        fallback_or_rescue=True,
        exposure_seconds=0,
    )
    raw, digest = freeze_blinded_paired_day_export([*assignments[:3], (1, rescue)])
    result = analyze_revealed_paired_export(raw, digest, locked_pairs=2, ai_label="X")
    assert result["decision"] == "inconclusive_null_endpoint"
    assert result["assigned_days"] == 4
    assert result["no_pair_replacement"] is True


def test_pair_and_mapping_shape_fail_closed():
    assignments = _rows()
    with pytest.raises(ValueError, match="blinded label"):
        freeze_blinded_paired_day_export([assignments[0], (0, assignments[3][1])])
    with pytest.raises(ValueError, match="source uint32 pair indexes"):
        freeze_blinded_paired_day_export([(True, assignments[0][1])])
    raw, digest = freeze_blinded_paired_day_export(assignments)
    changed = json.loads(raw)
    changed["rows"][0]["physical_arm"] = "AI"
    altered = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="byte/hash binding"):
        replay_blinded_paired_day_export(altered, digest)
    with pytest.raises(ValueError, match="locked pair count"):
        analyze_revealed_paired_export(raw, digest, locked_pairs=1, ai_label="X")


def test_pair_identity_comes_from_source_index_not_date_or_assignment_sort():
    rows = _rows()
    source_pairs = [(1, rows[0][1]), (1, rows[1][1]), (0, rows[2][1]), (0, rows[3][1])]
    raw, digest = freeze_blinded_paired_day_export(source_pairs)
    replayed = replay_blinded_paired_day_export(raw, digest)
    assert [(index, row.local_date) for index, row in replayed] == [
        (0, "2026-11-04"),
        (0, "2026-11-05"),
        (1, "2026-11-02"),
        (1, "2026-11-03"),
    ]
