"""PlannerProposal (#1350): the Cortex planner's output contract."""

import copy

import pytest

from cortex_planner.fixture import sample_inputs, sample_proposal
from cortex_planner.pack import anchors
from verdify_schemas.planner_proposal import INTENT_FIELDS, ProposalRejected, proposal_json_schema, validate_proposal

ANCHORS = anchors(sample_inputs())


def proposal(**changes):
    return sample_proposal(ANCHORS, **changes)


def test_intent_fields_add_night_bias_and_drop_wet_cutoff():
    assert "night_vpd_bias_kpa" in INTENT_FIELDS and "wet_cutoff_hour" not in INTENT_FIELDS
    assert len(INTENT_FIELDS) == 15


def test_valid_proposal_passes():
    parsed = validate_proposal(proposal(), ANCHORS)
    assert parsed.waypoints[0].climate_intent.night_vpd_bias_kpa == 0.1


@pytest.mark.parametrize("value", [-0.01, 0.26])
def test_night_bias_outside_registry_is_rejected(value):
    raw = proposal()
    raw["waypoints"][0]["climate_intent"]["night_vpd_bias_kpa"] = value
    with pytest.raises(ProposalRejected):
        validate_proposal(raw, ANCHORS)


def test_wet_cutoff_hour_is_rejected():
    raw = proposal()
    raw["waypoints"][1]["climate_intent"]["wet_cutoff_hour"] = 19.0
    with pytest.raises(ProposalRejected, match="wet_cutoff_hour"):
        validate_proposal(raw, ANCHORS)


def test_waypoint_off_anchor_or_out_of_order_is_rejected():
    raw = proposal()
    raw["waypoints"][1]["ts"] = "2026-11-05T09:01:00-07:00"
    with pytest.raises(ProposalRejected, match="not one of the offered anchors"):
        validate_proposal(raw, ANCHORS)
    raw = proposal()
    raw["waypoints"][1], raw["waypoints"][2] = copy.deepcopy(raw["waypoints"][2]), copy.deepcopy(raw["waypoints"][1])
    with pytest.raises(ProposalRejected, match="not after the previous"):
        validate_proposal(raw, ANCHORS)


def test_moisture_ladder_and_change_bookkeeping():
    raw = proposal()
    raw["waypoints"][0]["climate_intent"].update(moisture_engage_vpd_excess_kpa=0.3, all_zone_vpd_excess_kpa=0.2)
    with pytest.raises(ProposalRejected, match="all_zone_vpd_excess_kpa"):
        validate_proposal(raw, ANCHORS)
    raw = proposal()
    raw["waypoints"][0]["reasons"] = {}
    with pytest.raises(ProposalRejected, match="without a reason"):
        validate_proposal(raw, ANCHORS)


def test_shape_limits():
    with pytest.raises(ProposalRejected):
        validate_proposal(proposal(waypoints=proposal()["waypoints"][:2]), ANCHORS)
    with pytest.raises(ProposalRejected):
        validate_proposal(proposal(confidence=1.5), ANCHORS)
    with pytest.raises(ProposalRejected):
        validate_proposal(proposal(extra="x"), ANCHORS)
    assert proposal_json_schema()["title"] == "PlannerProposal"
