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


def prepare(preview, decisions, *, now):
    captured = datetime.fromisoformat(preview["captured_at"])
    if captured.tzinfo is None or not timedelta(0) <= now - captured <= timedelta(seconds=60):
        raise ValueError("source-owned preview must be current within60seconds")
    if not preview["base_converged"]:
        raise ValueError("ordinary full48 baseline is not converged")
    if not 1 <= len(decisions) <= len(CANONICAL_FIELD_ORDER):
        raise ValueError("explicit decisions must fit the complete canonical vector")
    return {
        "schema": "verdify-c1-qualification-worksheet-v2",
        "worksheet_id": str(uuid4()),
        "expires_at": (captured + timedelta(minutes=6)).isoformat(),
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
    args = parser.parse_args()
    result = prepare(
        json.loads(args.preview.read_text()), json.loads(args.decisions.read_text()), now=datetime.now(UTC)
    )
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
