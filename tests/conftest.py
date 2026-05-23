"""
Shared fixtures for Verdify smoke tests.
All tests run against the live production stack.
"""

import asyncio
import os
import shutil
import subprocess

import pytest

DB_DSN = os.environ.get("DB_DSN", "postgresql://verdify:verdify@localhost:5432/verdify")
DB_QUERY_TIMEOUT_S = int(os.environ.get("VERDIFY_DB_QUERY_TIMEOUT_S", "90"))


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
