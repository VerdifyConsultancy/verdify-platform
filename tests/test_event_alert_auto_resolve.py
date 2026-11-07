"""Event alerts that no producer revisits must still close.

forecast_deviation rows were delivered only inside a five-minute window, and
esp32_push_failed rows had no resolver at all, so rows from June to November
stayed open after the condition had cleared. The functions run from their
source (AST) against a recording connection; the SQL was checked read-only on
production on 2026-11-07 (it selected exactly the seven stale push-failure rows,
each of whose parameters had been confirmed on the controller since).
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORECAST = ROOT / "ingestor/tasks/forecast.py"
ALERTS = ROOT / "ingestor/tasks/alerts.py"


def _load(path: Path, names: set[str]) -> dict:
    tree = ast.parse(path.read_text())
    body = [
        node
        for node in tree.body
        if (isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names)
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", None) in names for t in node.targets))
    ]
    assert {getattr(n, "name", None) or n.targets[0].id for n in body} == names
    namespace: dict = {"asyncpg": type("asyncpg", (), {"Connection": object})}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    return namespace


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql))
        return None


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_stale_forecast_deviation_rows_expire_before_the_pending_read():
    ns = _load(FORECAST, {"_expire_stale_forecast_deviation_alerts", "_pending_forecast_deviation_alert"})
    conn = Recorder()
    assert asyncio.run(ns["_pending_forecast_deviation_alert"](conn)) is None
    (kind1, expire), (kind2, pending) = conn.calls
    assert (kind1, kind2) == ("execute", "fetchrow")
    expire, pending = _norm(expire), _norm(pending)
    # The expiry closes exactly what the pending read can no longer see.
    assert "created_at <= now() - interval '5 minutes'" in expire
    assert "created_at > now() - interval '5 minutes'" in pending
    assert "alert_type = 'forecast_deviation'" in expire
    assert "disposition IN ('open', 'acknowledged')" in expire
    assert "resolved_at IS NULL" in expire
    assert "SET disposition = 'resolved'" in expire


def test_push_failures_resolve_only_when_every_parameter_was_confirmed_since():
    ns = _load(ALERTS, {"_RESOLVE_RECOVERED_PUSH_FAILURES_SQL", "_resolve_recovered_push_failures"})
    conn = Recorder()
    asyncio.run(ns["_resolve_recovered_push_failures"](conn))
    ((kind, sql),) = conn.calls
    sql = _norm(sql)
    assert kind == "execute"
    assert "f.alert_type = 'esp32_push_failed'" in sql
    assert "f.disposition IN ('open', 'acknowledged')" in sql
    # Dispatcher rows list `parameters`; setpoint-listener rows name one `parameter`.
    assert "f.details -> 'parameters'" in sql and "f.details ->> 'parameter'" in sql
    # A row naming no parameter (validation fallback) stays open.
    assert "cardinality(p.params) > 0" in sql
    # Every parameter, not any: NOT EXISTS an unconfirmed one.
    assert "NOT EXISTS ( SELECT 1 FROM unnest(p.params) AS q(param) WHERE NOT EXISTS" in sql
    assert "s.confirmed_at > p.created_at" in sql


def test_alert_monitor_runs_the_push_failure_resolver_each_cycle():
    tree = ast.parse(ALERTS.read_text())
    monitor = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "alert_monitor")
    src = ast.unparse(monitor)
    assert "await _resolve_recovered_push_failures(conn)" in src
    assert src.index("_resolve_recovered_push_failures") < src.index("await _sync_monitor_alerts(")
