"""
Shared fixtures for Verdify smoke tests.
All tests run against the live production stack.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DB_DSN = os.environ.get("DB_DSN", "postgresql://verdify:verdify@localhost:5432/verdify")
DB_QUERY_TIMEOUT_S = int(os.environ.get("VERDIFY_DB_QUERY_TIMEOUT_S", "90"))

_REPO_ROOT = Path(__file__).resolve().parents[1]


def tasks_source_text(repo_root: Path | str | None = None) -> str:
    """Return the full source of the ``tasks`` implementation.

    Issue #46 split the former single-file ``ingestor/tasks.py`` into the
    ``ingestor/tasks/`` package. Source-string invariant tests that used to
    ``Path("ingestor/tasks.py").read_text()`` must now read the whole package
    so the same string assertions still enforce the same invariant. This helper
    concatenates every module in the package (or reads the legacy single file if
    it ever reappears), so callers stay agnostic to the layout.
    """
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    pkg = root / "ingestor" / "tasks"
    legacy = root / "ingestor" / "tasks.py"
    if pkg.is_dir():
        parts = [p.read_text() for p in sorted(pkg.glob("*.py"))]
        return "\n".join(parts)
    return legacy.read_text()


def tasks_module_path(repo_root: Path | str | None = None) -> Path:
    """Return the package dir (or legacy file) backing the tasks implementation."""
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    pkg = root / "ingestor" / "tasks"
    return pkg if pkg.is_dir() else (root / "ingestor" / "tasks.py")


# Docker exec wrapper for DB queries (works even if pg port isn't exposed to host)
def db_query(sql: str) -> str:
    """Run a SQL query via docker exec and return stdout."""
    if os.environ.get("VERDIFY_DB_QUERY_MODE") == "direct" and shutil.which("psql"):
        cmd = ["psql", "-t", "-A", "-c", sql]
    else:
        cmd = ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify", "-t", "-A", "-c", sql]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=DB_QUERY_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"DB query failed: {result.stderr.strip()}")
    return result.stdout.strip()


def db_query_rows(sql: str) -> list[str]:
    """Run a SQL query and return non-empty lines."""
    raw = db_query(sql)
    return [line for line in raw.split("\n") if line.strip()]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
