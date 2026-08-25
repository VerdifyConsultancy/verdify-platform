from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

from tasks import heartbeat  # noqa: E402

RUN_COMPLETED = "run_00000000000000000000000000000001"
RUN_FAILED = "run_00000000000000000000000000000002"
RUN_CANCELLED = "run_00000000000000000000000000000003"
RUN_RUNNING = "run_00000000000000000000000000000004"


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_limit: int | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.payload[:limit]


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self, rows: list[dict[str, object]], update_results: list[int | None]) -> None:
        self.rows = rows
        self.update_results = list(update_results)
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        return self.rows

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls.append((sql, args))
        return self.update_results.pop(0)

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))

    def transaction(self):
        return _Transaction()


class _Acquire:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_normalize_run_status_retains_only_whitelisted_terminal_scalars():
    normalized = heartbeat._normalize_hermes_run_status(
        {
            "object": "hermes.run",
            "run_id": RUN_COMPLETED,
            "status": "completed",
            "last_event": "run.completed",
            "output": "arbitrary assistant output must not be persisted",
            "error": "arbitrary provider error must not be persisted",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 34,
                "total_tokens": 46,
                "provider_detail": "not retained",
            },
            "model": "not retained",
        },
        RUN_COMPLETED,
    )

    assert normalized == {
        "status": "completed",
        "last_event": "run.completed",
        "usage": {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
    }


def test_normalize_run_status_rejects_identity_mismatch_and_arbitrary_event():
    assert heartbeat._normalize_hermes_run_status({}, RUN_COMPLETED) is None
    assert (
        heartbeat._normalize_hermes_run_status(
            {"object": "hermes.run", "run_id": RUN_FAILED, "status": "failed"},
            RUN_COMPLETED,
        )
        is None
    )
    normalized = heartbeat._normalize_hermes_run_status(
        {
            "object": "hermes.run",
            "run_id": RUN_FAILED,
            "status": "failed",
            "last_event": "provider supplied arbitrary text",
            "usage": {"input_tokens": True, "output_tokens": -1, "total_tokens": 5},
        },
        RUN_FAILED,
    )
    assert normalized == {"status": "failed", "usage": {"total_tokens": 5}}
    assert (
        heartbeat._normalize_hermes_run_status(
            {"object": "hermes.run", "run_id": RUN_FAILED, "status": "provider-invented"},
            RUN_FAILED,
        )
        is None
    )
    assert (
        heartbeat._normalize_hermes_run_status(
            {"object": "hermes.run", "run_id": RUN_FAILED, "status": {"not": "scalar"}},
            RUN_FAILED,
        )
        is None
    )


def test_fetch_run_status_uses_auth_and_bounded_read_without_retaining_body(monkeypatch):
    test_credential = "test-only-not-a-real-secret"
    response = _Response(
        json.dumps(
            {
                "object": "hermes.run",
                "run_id": RUN_FAILED,
                "status": "failed",
                "last_event": "run.failed",
                "error": test_credential,
                "output": test_credential,
            }
        ).encode()
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(heartbeat, "HERMES_URL", "http://hermes.internal:8642")
    monkeypatch.setattr(heartbeat, "HERMES_API_KEY", test_credential)
    monkeypatch.setattr(heartbeat.urllib.request, "urlopen", fake_urlopen)

    status = heartbeat._fetch_hermes_run_status(RUN_FAILED)

    assert status == {"status": "failed", "last_event": "run.failed"}
    assert test_credential not in json.dumps(status)
    assert captured["request"].full_url.endswith(f"/v1/runs/{RUN_FAILED}")
    assert captured["request"].get_header("Authorization") == f"Bearer {test_credential}"
    assert captured["timeout"] == heartbeat._HERMES_RUN_STATUS_TIMEOUT_SECONDS
    assert response.read_limit == heartbeat._HERMES_RUN_STATUS_MAX_BYTES + 1


def test_fetch_run_status_404_and_malformed_id_are_non_terminal(monkeypatch):
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

    monkeypatch.setattr(heartbeat.urllib.request, "urlopen", fake_urlopen)

    assert heartbeat._fetch_hermes_run_status(RUN_RUNNING) is None
    assert heartbeat._fetch_hermes_run_status("../../not-a-run") is None
    assert calls == 1


@pytest.mark.asyncio
async def test_reconcile_terminal_runs_fails_every_no_action_outcome_and_syncs_ledger(monkeypatch):
    rows = [
        {"id": 11, "hermes_run_id": RUN_COMPLETED},
        {"id": 12, "hermes_run_id": RUN_FAILED},
        {"id": 13, "hermes_run_id": RUN_CANCELLED},
        {"id": 14, "hermes_run_id": RUN_RUNNING},
    ]
    conn = _Connection(rows, [11, 12, 13])
    statuses = {
        RUN_COMPLETED: {
            "status": "completed",
            "last_event": "run.completed",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        },
        RUN_FAILED: {"status": "failed", "last_event": "run.failed"},
        RUN_CANCELLED: {"status": "cancelled", "last_event": "run.cancelled"},
        RUN_RUNNING: {"status": "running"},
    }
    monkeypatch.setattr(heartbeat, "_fetch_hermes_run_status", statuses.__getitem__)

    reconciled = await heartbeat._reconcile_hermes_run_terminals(_Pool(conn))

    assert reconciled == 3
    select_sql, select_args = conn.fetch_calls[0]
    assert "status = 'pending'" in select_sql
    assert "terminal_action IS NULL" in select_sql
    assert "resulting_plan_id IS NULL" in select_sql
    assert "hermes_run_id IS NOT NULL" in select_sql
    assert select_args == (
        heartbeat._HERMES_RUN_STATUS_LIMIT,
        heartbeat._HERMES_RUN_STATUS_LOOKBACK_MINUTES,
    )

    assert len(conn.fetchval_calls) == 3
    expected_classes = [
        "hermes_run_completed_without_planner_action",
        "hermes_run_failed",
        "hermes_run_cancelled",
    ]
    for (sql, args), expected_class in zip(conn.fetchval_calls, expected_classes, strict=True):
        assert "status = 'delivery_failed'" in sql
        assert "AND status = 'pending'" in sql
        assert "AND terminal_action IS NULL" in sql
        assert "AND resulting_plan_id IS NULL" in sql
        assert args[2] == expected_class
        audit = json.loads(args[3])
        assert set(audit) <= {"hermes_run_status", "last_event", "usage"}

    assert len(conn.execute_calls) == 1
    assert "UPDATE planner_trigger_ledger" in conn.execute_calls[0][0]


@pytest.mark.asyncio
async def test_reconcile_atomic_fence_preserves_concurrent_mcp_success(monkeypatch):
    conn = _Connection([{"id": 21, "hermes_run_id": RUN_COMPLETED}], [None])
    monkeypatch.setattr(
        heartbeat,
        "_fetch_hermes_run_status",
        lambda _run_id: {"status": "completed", "last_event": "run.completed"},
    )

    reconciled = await heartbeat._reconcile_hermes_run_terminals(_Pool(conn))

    assert reconciled == 0
    assert len(conn.fetchval_calls) == 1
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_reconcile_active_or_unavailable_runs_do_not_mutate(monkeypatch):
    rows = [
        {"id": 31, "hermes_run_id": RUN_RUNNING},
        {"id": 32, "hermes_run_id": RUN_FAILED},
    ]
    conn = _Connection(rows, [])
    statuses = {RUN_RUNNING: {"status": "running"}, RUN_FAILED: None}
    monkeypatch.setattr(heartbeat, "_fetch_hermes_run_status", statuses.__getitem__)

    reconciled = await heartbeat._reconcile_hermes_run_terminals(_Pool(conn))

    assert reconciled == 0
    assert conn.fetchval_calls == []
    assert conn.execute_calls == []
