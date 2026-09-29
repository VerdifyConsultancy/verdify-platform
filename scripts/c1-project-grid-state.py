#!/usr/bin/env python3
"""Build an offline C1 grid proposal from a 48-field ordinary desired map.

Usage: python3 scripts/c1-project-grid-state.py --source source.json \
  --decisions decisions.json --output c1-proposal.json

The source file is a direct field/value JSON object, not cfg readbacks.  The
decisions file is a field-keyed JSON object whose entries have exact ``from``,
selected ``to``, and a specific ``rationale``.  Only off-grid fields get an
entry.  This tool does not read the live controller or submit a device command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verdify_schemas.c1_grid_projection import C1ProjectionError, project_c1_grid_state


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise C1ProjectionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_object(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    if not raw or len(raw) > 1_048_576:
        raise C1ProjectionError(f"{path}: input empty or exceeds 1 MiB")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise C1ProjectionError(f"{path}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise C1ProjectionError(f"{path}: input must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        source = _read_object(args.source)
        decisions = _read_object(args.decisions)
        artifact = project_c1_grid_state(source, decisions)  # type: ignore[arg-type]
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(artifact, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
    except (OSError, C1ProjectionError, ValueError) as exc:
        parser.exit(2, f"C1 projection failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
