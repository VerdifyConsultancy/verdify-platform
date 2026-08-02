"""Explicit current-production read-only checks.

This file is skipped during portable branch validation. ``make test-live`` is
the only supported caller; it enables these bounded public-route and pod-local
transaction-read-only SELECT probes without touching the device/setpoints path.
"""

from __future__ import annotations

import os
import subprocess

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VERDIFY_TEST_LIVE") != "1",
    reason="current-production probes require make test-live",
)


@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        ("https://api.verdify.ai/health", 200),
        ("https://lab.verdify.ai/", 200),
        ("https://graphs.verdify.ai/api/health", 200),
        ("https://mcp.verdify.ai/mcp", 406),
        ("https://lab-stage.verdify.ai/", 200),
    ],
)
@pytest.mark.live_http
def test_public_route_and_tls(url: str, expected_status: int):
    response = httpx.get(url, timeout=15, follow_redirects=False)
    assert response.status_code == expected_status


@pytest.mark.live_db
def test_prod_database_accepts_transaction_read_only_query():
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            "verdify-prod",
            "verdify-db-0",
            "-c",
            "postgres",
            "--",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "BEGIN READ ONLY; SELECT current_database(), current_setting('transaction_read_only'); ROLLBACK;",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "verdify|on" in result.stdout.splitlines()
