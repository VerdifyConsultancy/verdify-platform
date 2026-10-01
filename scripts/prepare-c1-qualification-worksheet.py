#!/usr/bin/env python3
"""Bind explicit offline grid decisions to a fresh source-owned preview."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from verdify_schemas.c1_grid_projection import project_c1_grid_state
from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER


def authority_minutes(schema_version, decision_count):
    if schema_version not in (2, 3):
        raise ValueError("worksheet schema version must be 2 or 3")
    if not 1 <= decision_count <= len(CANONICAL_FIELD_ORDER):
        raise ValueError("explicit decisions must fit the complete canonical vector")
    # V3 budgets bounded delivery stages plus natural callback capture; callers
    # cannot supply a duration or extend an existing worksheet.
    return 6 if schema_version == 2 else 8 + 4 * ((decision_count + 11) // 12)


def prepare(preview, decisions, *, now, schema_version=2):
    captured = datetime.fromisoformat(preview["captured_at"])
    if captured.tzinfo is None or not timedelta(0) <= now - captured <= timedelta(seconds=60):
        raise ValueError("source-owned preview must be current within60seconds")
    if not preview["base_converged"]:
        raise ValueError("ordinary full48 baseline is not converged")
    minutes = authority_minutes(schema_version, len(decisions))
    return {
        "schema": f"verdify-c1-qualification-worksheet-v{schema_version}",
        "worksheet_id": str(uuid4()),
        "expires_at": (captured + timedelta(minutes=minutes)).isoformat(),
        "preview": preview,
        "decisions": decisions,
        "projection": project_c1_grid_state(preview["base_values"], decisions),
        "qualification_claimed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema-version", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    result = prepare(
        json.loads(args.preview.read_text()),
        json.loads(args.decisions.read_text()),
        now=datetime.now(UTC),
        schema_version=args.schema_version,
    )
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
