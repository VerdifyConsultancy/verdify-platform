"""The C1 proposal cannot silently turn ordinary desired values into commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdify_schemas.c1_grid_projection import C1ProjectionError, project_c1_grid_state
from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, normalize_complete_state

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def on_grid_source() -> dict[str, float | bool]:
    artifact = json.loads(
        (REPO_ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json").read_text()
    )
    return artifact["profiles"]["baseline"]["values"]


def test_explicit_three_field_projection_has_complete_receipt(on_grid_source: dict[str, float | bool]) -> None:
    source = dict(on_grid_source)
    source["cool_stage2_over_high_f"] = 1.8025
    source["cool_exit_hysteresis_f"] = 1.87
    source["dwell_gate_ms"] = 225000
    decisions = {
        "cool_stage2_over_high_f": {
            "from": 1.8025,
            "to": 1.8,
            "rationale": "select lower stage-two cooling point for C1",
        },
        "cool_exit_hysteresis_f": {"from": 1.87, "to": 1.9, "rationale": "select upper exit hysteresis point for C1"},
        "dwell_gate_ms": {
            "from": 225000,
            "to": 240000,
            "rationale": "use the longer anti-chatter dwell from COMMON_GRID_DECISIONS",
        },
    }
    result = project_c1_grid_state(source, decisions)
    assert result["field_count"] == 48
    assert result["explicit_selection_count"] == 3
    assert result["qualification_claimed"] is False
    assert result["proposed_values"] == normalize_complete_state(result["proposed_values"])
    rows = result["field_decisions"]
    assert len(rows) == 48
    assert [row["field"] for row in rows] == list(CANONICAL_FIELD_ORDER)
    assert {row["field"] for row in rows if row["action"] == "explicit_selection"} == set(decisions)
    assert result == project_c1_grid_state(
        dict(reversed(list(source.items()))), dict(reversed(list(decisions.items())))
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda source, decisions: decisions.clear(), "requires from, to, and rationale"),
        (lambda source, decisions: decisions["dwell_gate_ms"].update({"from": 210000}), "does not match"),
        (lambda source, decisions: decisions["dwell_gate_ms"].update({"to": 230000}), "value_off_entity_grid"),
        (lambda source, decisions: decisions["dwell_gate_ms"].update({"to": "240000"}), "finite JSON number"),
        (lambda source, decisions: decisions["dwell_gate_ms"].update({"to": 9999999}), "value_outside_entity_grid"),
        (lambda source, decisions: decisions["dwell_gate_ms"].update({"rationale": " "}), "rationale"),
        (
            lambda source, decisions: decisions.update(
                {"mister_all_delay_s": {"from": 90, "to": 120, "rationale": "change"}}
            ),
            "on-grid source",
        ),
        (lambda source, decisions: source.update({"dwell_gate_ms": 230000}), "does not match"),
        (lambda source, decisions: source.update({"unknown": 1}), "exactly 48 fields"),
    ],
)
def test_projection_fails_closed_on_unreviewed_or_stale_choice(on_grid_source, edit, message) -> None:
    source = dict(on_grid_source)
    source["dwell_gate_ms"] = 225000
    decisions = {
        "dwell_gate_ms": {
            "from": 225000,
            "to": 240000,
            "rationale": "select the longer anti-chatter point from COMMON_GRID_DECISIONS",
        }
    }
    edit(source, decisions)
    with pytest.raises(C1ProjectionError, match=message):
        project_c1_grid_state(source, decisions)


def test_out_of_bounds_source_cannot_be_clamped(on_grid_source: dict[str, float | bool]) -> None:
    source = dict(on_grid_source)
    source["cool_stage2_over_high_f"] = 4.0
    with pytest.raises(C1ProjectionError, match="does not clamp"):
        project_c1_grid_state(source, {})


def test_complete_on_grid_source_needs_no_decisions(on_grid_source: dict[str, float | bool]) -> None:
    artifact = project_c1_grid_state(on_grid_source, {})
    assert artifact["explicit_selection_count"] == 0
    assert all(row["action"] == "retained" for row in artifact["field_decisions"])


def test_selected_state_preserves_mister_threshold_order(on_grid_source: dict[str, float | bool]) -> None:
    source = dict(on_grid_source)
    source["mister_engage_kpa"] = 2.0
    source["mister_all_kpa"] = 1.5
    with pytest.raises(C1ProjectionError, match="exceeds mister_all_kpa"):
        project_c1_grid_state(source, {})
