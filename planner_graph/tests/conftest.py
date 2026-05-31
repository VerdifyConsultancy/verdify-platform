"""Pytest suite configuration and shared fixtures.

This module holds test-wide setup that should be available without repeating it
inside every individual test file. It connects the whole test suite to a common
execution environment.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def postgres_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is required for Postgres integration tests")

    container_name = f"planner-graph-test-{uuid4().hex[:10]}"
    run_result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            "POSTGRES_USER=planner",
            "-e",
            "POSTGRES_PASSWORD=planner",
            "-e",
            "POSTGRES_DB=planner_graph_test",
            "-p",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    container_id = run_result.stdout.strip()

    inspect_result = subprocess.run(
        ["docker", "port", container_id, "5432/tcp"],
        check=True,
        capture_output=True,
        text=True,
    )
    port = inspect_result.stdout.strip().rsplit(":", maxsplit=1)[-1]
    dsn = f"postgresql://planner:planner@127.0.0.1:{port}/planner_graph_test"

    deadline = time.time() + 30
    while True:
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            break
        except psycopg.OperationalError:
            if time.time() >= deadline:
                raise
            time.sleep(0.25)

    try:
        yield dsn
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            check=False,
            capture_output=True,
            text=True,
        )
