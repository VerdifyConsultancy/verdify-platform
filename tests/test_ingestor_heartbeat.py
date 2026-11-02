"""VD-54: the ingestor heartbeat (decisions Q-01).

One validated system_state row (entity ingestor_heartbeat) every 60 s from the
ingestor's own task loop, through the view the ingestor login already writes;
the tasks package re-exports the task without shadowing its submodule; the
dashboard's Now read looks for the same entity.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
INGESTOR = ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))

import shared  # noqa: E402
import tasks  # noqa: E402
import tasks.liveness as liveness  # noqa: E402

from verdify_services import reads  # noqa: E402

INSERT = "INSERT INTO v_runtime_system_state_write (ts, entity, value) VALUES ($1, 'ingestor_heartbeat', 'alive')"


class _Conn:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.executes.append((query, args))
        return "INSERT 0 1"


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_one_heartbeat_row_through_the_ingestor_write_view():
    pool = _Pool()
    before = datetime.now(UTC)
    asyncio.run(liveness.ingestor_heartbeat(pool))
    [(query, (ts,))] = pool.conn.executes
    assert query == INSERT
    assert ts.tzinfo is not None and before <= ts <= datetime.now(UTC)


def test_the_row_validates_through_system_state_row_first(monkeypatch):
    built = []
    real = liveness.SystemStateRow

    def spy(**fields):
        built.append(fields)
        return real(**fields)

    monkeypatch.setattr(liveness, "SystemStateRow", spy)
    pool = _Pool()
    asyncio.run(liveness.ingestor_heartbeat(pool))
    assert [(fields["entity"], fields["value"]) for fields in built] == [("ingestor_heartbeat", "alive")]
    assert pool.conn.executes[0][1] == (built[0]["ts"],)

    class _NaiveClock:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 11, 2, 12, 0)  # no zone: SystemStateRow refuses it

    monkeypatch.setattr(liveness, "datetime", _NaiveClock)
    refused = _Pool()
    with pytest.raises(ValidationError):
        asyncio.run(liveness.ingestor_heartbeat(refused))
    assert refused.conn.executes == []


def test_a_shadow_ingestor_never_writes_a_heartbeat():
    # Shadow mode no-ops every write statement, so a shadow cannot stand in for a dead writer.
    assert shared._is_write_sql(INSERT)


def _task_loop_tasks() -> list[tuple]:
    tree = ast.parse((INGESTOR / "ingestor.py").read_text())
    [loop] = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "task_loop"]
    [assign] = [
        node
        for node in ast.walk(loop)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TASKS" for t in node.targets)
    ]
    return [
        tuple(ast.literal_eval(elt) if isinstance(elt, ast.Constant) else ast.unparse(elt) for elt in entry.elts)
        for entry in assign.value.elts
    ]


def test_the_task_loop_runs_the_heartbeat_every_60_s():
    assert ("ingestor_heartbeat", 60, "ingestor_heartbeat") in _task_loop_tasks()
    tree = ast.parse((INGESTOR / "ingestor.py").read_text())
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "tasks"
        for alias in node.names
    }
    assert "ingestor_heartbeat" in imported


def test_tasks_reexports_the_task_and_keeps_its_submodule():
    assert tasks.ingestor_heartbeat is liveness.ingestor_heartbeat
    assert isinstance(tasks.liveness, types.ModuleType)
    assert not hasattr(tasks, "ingestor_heartbeat") or not isinstance(tasks.ingestor_heartbeat, types.ModuleType)


def test_the_dashboard_reads_the_same_entity():
    assert liveness.HEARTBEAT_ENTITY == reads.HEARTBEAT_ENTITY == "ingestor_heartbeat"
    assert liveness.HEARTBEAT_VALUE == "alive"
    assert f"'{liveness.HEARTBEAT_ENTITY}'" in INSERT and f"'{liveness.HEARTBEAT_VALUE}'" in INSERT
