#!/usr/bin/env python3
"""Audit the ClimateIntent schema against the final controller design doc."""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.climate_intent import (  # noqa: E402
    CLIMATE_ACTIONS,
    CLIMATE_INTENT_FIELDS,
    CLIMATE_PRIORITY_ORDER,
    CLIMATE_RELAY_FIELD_DENYLIST,
    ClimateIntent,
)

DESIGN_DOC = REPO_ROOT / "docs" / "firmware-climate-intent-controller-final-design-2026-05-24.md"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _table_codes(section: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        match = re.match(r"\| `([^`]+)`", line)
        if match:
            out.append(match.group(1))
    return tuple(out)


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    text = DESIGN_DOC.read_text()
    doc_actions = _table_codes(_section(text, "## Physical Action Set", "## Candidate Evaluation"))
    doc_fields = _table_codes(_section(text, "## ClimateIntent Surface", "## Context Inputs For AI"))

    if doc_actions != CLIMATE_ACTIONS:
        _fail(f"candidate action drift: doc={doc_actions} schema={CLIMATE_ACTIONS}")
    if doc_fields != CLIMATE_INTENT_FIELDS:
        _fail(f"ClimateIntent field drift: doc={doc_fields} schema={CLIMATE_INTENT_FIELDS}")
    if set(ClimateIntent.model_fields) != set(CLIMATE_INTENT_FIELDS):
        _fail("ClimateIntent model fields do not match CLIMATE_INTENT_FIELDS")

    relay_overlap = sorted(set(CLIMATE_INTENT_FIELDS) & CLIMATE_RELAY_FIELD_DENYLIST)
    if relay_overlap:
        _fail(f"AI intent surface includes raw relay fields: {relay_overlap}")
    if CLIMATE_PRIORITY_ORDER != ("safety", "temp", "vpd", "resource"):
        _fail(f"priority order drift: {CLIMATE_PRIORITY_ORDER}")

    print(f"climate_intent_fields={len(CLIMATE_INTENT_FIELDS)}")
    print(f"climate_actions={len(CLIMATE_ACTIONS)}")
    print("OK")


if __name__ == "__main__":
    main()
