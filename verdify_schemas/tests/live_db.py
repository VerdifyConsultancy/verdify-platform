"""Reuse the repository's fail-closed read-only live-test transport."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_support = runpy.run_path(str(REPO_ROOT / "conftest.py"), run_name="_schema_live_db_support")

db_query: Callable[[str], str] = _support["db_query"]
db_query_rows: Callable[[str], list[str]] = _support["db_query_rows"]
