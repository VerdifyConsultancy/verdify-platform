"""The controller's irr_* text parses with #1151's parsers (VD-45, #1160).

The engine wiring's native tests (firmware/test/test_irrigation_program.cpp) pin the exact
irr_program_id, irr_state and irr_events text the controller publishes, each as a literal of
EXPECT_IRR_PROGRAM_ID, EXPECT_IRR_STATE or EXPECT_IRR_EVENTS. The C++ test proves the formatters
produce those strings; this test proves every one of them is in the grammar the platform parses
(verdify_schemas/irrigation.py), in its canonical token order, so the controller can never publish
text the ingestor would treat as no reading.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from verdify_schemas.irrigation import (
    DEVICE_IDX,
    IRR_TEXT_MAX_LEN,
    IrrigationCause,
    format_irr_events,
    format_irr_program_id,
    format_irr_state,
    parse_irr_events,
    parse_irr_program_id,
    parse_irr_state,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_TEST = REPO_ROOT / "firmware" / "test" / "test_irrigation_program.cpp"
_LITERAL = r'"((?:[^"\\]|\\.)*)"'


def _literals(macro: str) -> list[str]:
    source = NATIVE_TEST.read_text()
    found = re.findall(rf"\b{macro}\(\s*[^,]+,\s*{_LITERAL}\s*\)", source)
    return [text.encode().decode("unicode_escape") for text in found]


PROGRAM_IDS = _literals("EXPECT_IRR_PROGRAM_ID")
STATES = _literals("EXPECT_IRR_STATE")
EVENTS = _literals("EXPECT_IRR_EVENTS")


def test_the_native_tests_pin_enough_text() -> None:
    """The extraction found the macros: a refactor that renames them must not empty this test."""
    assert len(PROGRAM_IDS) >= 10
    assert len(STATES) >= 20
    assert len(EVENTS) >= 6
    assert "#define EXPECT_IRR_STATE(actual, literal)" in NATIVE_TEST.read_text()


@pytest.mark.parametrize("text", sorted(set(PROGRAM_IDS)))
def test_program_id_parses(text: str) -> None:
    program_id = parse_irr_program_id(text)
    assert format_irr_program_id(program_id) == text


@pytest.mark.parametrize("text", sorted(set(STATES)))
def test_state_parses_in_canonical_order(text: str) -> None:
    assert len(text) <= IRR_TEXT_MAX_LEN
    state = parse_irr_state(text)
    assert format_irr_state(state) == text


@pytest.mark.parametrize("text", sorted(set(EVENTS)))
def test_events_parse_in_canonical_order(text: str) -> None:
    events = parse_irr_events(text)
    assert format_irr_events(events) == text


def test_the_pinned_states_cover_every_device_cause_and_both_manifolds() -> None:
    """Real engine states pinned by the traces: every cause the controller publishes, a run on each
    manifold, a waiting run with and without an end time, and push rejects."""
    states = [parse_irr_state(text) for text in STATES]
    runs = [run for state in states for run in state.runs]
    causes = {run.cause for run in runs}
    assert {
        IrrigationCause.VPD,
        IrrigationCause.SCHEDULE,
        IrrigationCause.RUN_NOW,
        IrrigationCause.FEED,
        IrrigationCause.PREWET,
        IrrigationCause.FLUSH,
        IrrigationCause.FALLBACK,
    } <= causes
    assert {run.manifold for run in runs} == {"drip", "mist"}
    waiting = [item for state in states for item in state.waiting]
    assert any(item.until_epoch is None for item in waiting) and any(item.until_epoch for item in waiting)
    assert {"occ", "vok", "man"} <= {item.reason for item in waiting}
    assert {"bad_hash", "bad_length", "bad_magic"} <= {state.reject for state in states if state.reject}
    assert any(state.faults for state in states)


def test_the_pinned_program_ids_are_rom_and_the_golden_vectors() -> None:
    parsed = {parse_irr_program_id(text) for text in PROGRAM_IDS}
    assert {(p.src, p.hash16) for p in parsed} == {
        ("rom", "0000000000000000"),
        ("nvs", "7ae03efe564082b7"),
        ("nvs", "3b76a2316e2f39d8"),
    }


def test_no_event_names_a_line_that_takes_no_runs() -> None:
    for text in EVENTS:
        for event in parse_irr_events(text):
            assert DEVICE_IDX[event.line_id] not in (DEVICE_IDX["L03"], DEVICE_IDX["L11"])
