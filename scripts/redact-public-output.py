#!/usr/bin/env python3
"""Apply the shared public-output prose redactor to UTF-8 text files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_public.output_policy import redact_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        redact_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
