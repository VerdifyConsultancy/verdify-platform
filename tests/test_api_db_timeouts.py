from importlib import util
from pathlib import Path

import asyncpg
import pytest

_SPEC = util.spec_from_file_location("verdify_api_main", Path(__file__).parents[1] / "api" / "main.py")
assert _SPEC and _SPEC.loader
main = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self, *, cancel_fetch: bool = False):
        self.cancel_fetch = cancel_fetch
        self.executed: list[str] = []

    async def execute(self, statement: str):
        self.executed.append(statement)

    def transaction(self):
        return _Transaction()

    async def fetch(self, statement: str, scorecard_date):
        if self.cancel_fetch:
            raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")
        return [{"metric": "planner_score", "value": 91.0}]


@pytest.mark.asyncio
async def test_api_pool_checkout_reapplies_query_safety_settings():
    conn = _Connection()

    await main._setup_db_connection(conn)

    assert conn.executed == [
        "SET application_name = 'verdify-api'",
        "SET statement_timeout = '15000ms'",
    ]


@pytest.mark.asyncio
async def test_scorecard_statement_timeout_degrades_to_empty_metrics():
    conn = _Connection(cancel_fetch=True)

    rows = await main._fetch_planner_scorecard(conn)

    assert rows == []
    assert conn.executed == ["SET LOCAL statement_timeout = '6000ms'"]
