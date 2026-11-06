"""The entry type every ``setting_help`` part module uses (kept apart so the parts import no package state)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpEntry:
    """Operator text for one setting, plus where the code shows it.

    ``summary`` is one line: what the setting is. ``effect`` says what the controller does with it, naming the
    mode or equipment it drives. ``raise_`` and ``lower`` say what happens to the greenhouse when the value goes
    up or down (for a switch: turning it on, turning it off). ``caution`` is an optional interaction or safety
    note. ``label`` replaces the registry label only where that label is the raw setting name. ``guardrail``
    marks a hard safety limit the dashboard confirms before saving.

    ``refs`` is never served: each item is ``"path"`` or ``"path::token"``, a repo file (and a token in it) that
    shows the effect described, checked by ``verdify_schemas/tests/test_setting_help.py``.
    """

    summary: str
    effect: str
    raise_: str | None = None
    lower: str | None = None
    caution: str | None = None
    label: str | None = None
    guardrail: bool = False
    refs: tuple[str, ...] = ()
