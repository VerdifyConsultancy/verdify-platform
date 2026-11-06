"""Cortex planner pack (#1366): section order and budgets, determinism, the minimum pack, anchors."""

from datetime import datetime

import pytest

from cortex_planner import PACK_VERSION, tokens
from cortex_planner.fixture import AS_OF, sample_inputs
from cortex_planner.inputs import PackInputs
from cortex_planner.pack import anchors, build_pack
from cortex_planner.run import DEFAULT_EVENT_TYPES
from cortex_planner.sections import BY_KEY, PACK_BUDGET, PACK_HARD_CAP, SECTION_KEYS, SECTIONS


def test_sixteen_fixed_sections_with_a_budget_of_about_20k():
    assert SECTION_KEYS == tuple(f"S{i}" for i in range(16))
    assert PACK_BUDGET == sum(s.budget for s in SECTIONS) == 20_300
    assert PACK_BUDGET < PACK_HARD_CAP
    assert [s.key for s in SECTIONS if s.minimum] == ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S15"]
    # The forecast is the largest section: the pack is forecast-centred.
    assert max(SECTIONS, key=lambda s: s.budget).key == "S6"


@pytest.mark.parametrize("event_type", DEFAULT_EVENT_TYPES)
@pytest.mark.parametrize("variant", ["v1", "minimum"])
def test_every_section_fits_its_budget(event_type, variant):
    pack = build_pack(sample_inputs(event_type), variant)
    assert pack.version == PACK_VERSION and pack.variant == variant
    for section in pack.sections:
        assert section.tokens <= BY_KEY[section.key].budget, (section.key, section.tokens)
        assert section.tokens == tokens.count(section.text)
    assert pack.total_tokens <= PACK_HARD_CAP
    expected = SECTION_KEYS if variant == "v1" else tuple(s.key for s in SECTIONS if s.minimum)
    assert tuple(s.key for s in pack.sections) == expected


def test_order_system_first_task_last():
    pack = build_pack(sample_inputs())
    assert pack.system.startswith("## S0 ")
    assert pack.user.startswith("## S1 ")
    assert "## S15 " in pack.user and pack.user.index("## S15 ") > pack.user.index("## S14 ")
    positions = [pack.user.index(f"## {key} ") for key in SECTION_KEYS[1:]]
    assert positions == sorted(positions)


def test_build_is_deterministic_and_inputs_round_trip():
    inputs = sample_inputs("SUNSET")
    first, second = build_pack(inputs), build_pack(inputs)
    assert first.text == second.text and first.sha256 == second.sha256
    restored = PackInputs.from_json(inputs.to_json())
    assert build_pack(restored).sha256 == first.sha256


def test_repair_pack_quotes_the_errors_before_the_task():
    errors = ["waypoints.0.ts is not one of the offered anchors", "confidence: too large"]
    pack = build_pack(sample_inputs(), "minimum", repair_errors=errors)
    assert pack.user.index("## Repair") < pack.user.index("## S15 ")
    for error in errors:
        assert error in pack.user
    meta = pack.sections_meta()
    assert meta[-1]["key"] == "repair" and pack.total_tokens > build_pack(sample_inputs(), "minimum").total_tokens


def test_anchors_are_future_local_times_with_offsets():
    found = anchors(sample_inputs())
    assert 3 <= len(found) <= 6
    times = [datetime.fromisoformat(a) for a in found]
    assert all(t.utcoffset() is not None for t in times)
    assert all(t > AS_OF for t in times) and times == sorted(times)
    assert times[0].minute % 15 == 0


def test_token_estimate_is_calibrated_not_chars_over_four():
    text = build_pack(sample_inputs()).text
    assert tokens.estimate(text) > len(text) / 4  # chars/4 undercounts dense tables by ~40 %
    assert tokens.method() in ("estimate-v1",) or tokens.method().startswith("qwen-tokenizer@")
