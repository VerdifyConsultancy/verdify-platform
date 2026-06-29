#!/usr/bin/env python3
"""Repo-local wrapper for the HA gap backfill component script."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/k8s/components/ha-gap-backfill/backfill-ha-gaps.py"


if __name__ == "__main__":
    runpy.run_path(str(SCRIPT), run_name="__main__")
