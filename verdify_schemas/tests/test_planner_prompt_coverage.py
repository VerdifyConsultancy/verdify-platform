"""CI gate: every planner-pushable tunable must be documented in the prompt.

Closes the eval finding that a chunk of `PLANNER_PUSHABLE_REG` knobs were
writable by MCP `set_tunable` but never named anywhere in the Iris planner
prompt (`ingestor/iris_planner.py`). An undocumented-but-pushable knob is a
dead lever: the planner cannot reason about a control it is never told exists,
so the knob silently rots while the dispatcher/band layer drives the behavior.

This test is the structural guard that keeps the writable surface and the
prompt in lockstep: if a future change marks a tunable `planner_pushable`
(lands it in `PLANNER_PUSHABLE_REG`) without naming it in the prompt, or drops
its mention from the prompt while it stays pushable, CI fails. The planner-prompt
owner (genai) adds the doc; the registry owner (here) keeps the contract.

Pure text parsing — no ESPHome build, no DB, no module import side effects.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG, SCHEDULED_POLICY_REG

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PLANNER_PATH = REPO_ROOT / "ingestor" / "iris_planner.py"

# The assembled prompt is the concatenation of these three module-level
# string constants (see iris_planner.py: directives + CORE + EXTENDED).
_PROMPT_CONSTANTS = ("_STANDING_DIRECTIVES", "_PLANNER_EXTENDED", "_PLANNER_CORE")

# Tunables appear in the prompt as backtick-quoted snake_case identifiers,
# e.g. `mister_engage_kpa`. Matches the convention used throughout the prompt.
_BACKTICK_IDENT_RE = re.compile(r"`([a-z][a-z0-9_]+)`")


def _prompt_text() -> str:
    """Concatenate the bodies of the three triple-quoted prompt constants.

    Slicing the constant bodies (rather than scanning the whole file) keeps the
    guard honest: a tunable named only in a Python comment or unrelated code
    does not count as "documented for the planner".
    """
    src = PLANNER_PATH.read_text()
    bodies: list[str] = []
    for const in _PROMPT_CONSTANTS:
        m = re.search(rf'{const}\s*=\s*"""(?P<body>.*?)"""', src, re.DOTALL)
        assert m, f"Could not locate prompt constant {const} in {PLANNER_PATH}"
        bodies.append(m.group("body"))
    text = "\n".join(bodies)
    assert text.strip(), "Assembled planner prompt text is empty"
    return text


@pytest.fixture(scope="module")
def prompt_identifiers() -> set[str]:
    return set(_BACKTICK_IDENT_RE.findall(_prompt_text()))


def test_every_planner_pushable_tunable_is_named_in_prompt(prompt_identifiers: set[str]) -> None:
    """Each planner-writable knob must be named in the planner prompt.

    Fails loudly with the exact undocumented names so the prompt owner can add
    them (or the registry owner can reclassify the knob out of the writable
    surface). This is the gate that keeps a pushable tunable from becoming a
    dead lever the planner is never told about.
    """
    undocumented = sorted(name for name in PLANNER_PUSHABLE_REG if name not in prompt_identifiers)
    assert not undocumented, (
        f"{len(undocumented)} planner-pushable tunable(s) are not named anywhere in the "
        f"Iris planner prompt ({PLANNER_PATH}): {undocumented}. Either document each in the "
        f"prompt's Tunable Dictionary / moisture ladder, or reclassify it out of "
        f"planner_pushable (push_owner='schedule'/'band'/'operator') if the band/schedule "
        f"layer truly owns it."
    )


def test_scheduled_policy_knobs_are_not_planner_pushable(prompt_identifiers: set[str]) -> None:
    """Schedule-layer knobs stay readable context but never planner-writable.

    The lighting (`gl_main_*`/`gl_grow_*`) and watering-schedule
    (`irrig_*`/`direct_wet_*` offsets/masks) clusters were reclassified out of
    the writable surface because the band/schedule layer owns them (the planner
    pushed 0 of them over 30d live). They must not regress back into
    `PLANNER_PUSHABLE_REG`. They MAY still be named in the prompt as read-only
    schedule context, so this guard checks writability, not documentation.
    """
    leaked = sorted(SCHEDULED_POLICY_REG & PLANNER_PUSHABLE_REG)
    assert not leaked, (
        f"{len(leaked)} scheduled-policy knob(s) leaked back into the planner-writable "
        f"surface: {leaked}. Schedule-layer knobs must keep planner_pushable=False."
    )


def test_planner_pushable_surface_excludes_lighting_and_watering_schedule() -> None:
    """Sanity-check the reclassification held: no per-circuit lighting or
    watering-schedule offset/mask knob is planner-writable.
    """
    offenders = sorted(
        name
        for name in PLANNER_PUSHABLE_REG
        if name.startswith(("gl_main_", "gl_grow_", "sw_gl_"))
        or ((name.startswith("irrig_") or name.startswith("direct_wet_")) and not name.startswith("direct_wet_stress_"))
    )
    assert not offenders, (
        f"Lighting/watering-schedule knobs are planner-writable again: {offenders}. "
        f"These belong to the band/schedule layer (push_owner='schedule')."
    )
