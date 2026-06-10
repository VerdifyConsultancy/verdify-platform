"""MCP `lessons_manage` state-machine boundary tests (G8, issue #44).

Exercises the transition guards in mcp.server.lessons_manage with a stubbed
asyncpg connection — no live DB required. Verifies that legal transitions
mutate the table and illegal transitions (validating/superseding a terminal
lesson) are rejected before any write.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# mcp/server.py runs as a top-level script (systemd: `python mcp/server.py`),
# and `import mcp.server` resolves to the installed MCP SDK, not the repo file.
# Load the repo module directly from its path under a unique name, mirroring how
# tests/test_12_fidelity.py loads iris_planner/ingestor. DSN env default keeps
# the import side-effect-free with no live DB.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

_SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
_spec = importlib.util.spec_from_file_location("verdify_mcp_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def _fake_conn(state_row: dict | None, new_exists: int | None = 1) -> AsyncMock:
    """An AsyncMock asyncpg connection.

    `_lesson_state_row` issues the multi-column SELECT (returns `state_row`);
    the supersede path also issues a `SELECT 1 ... WHERE id = new_id`
    (`fetchval` returns `new_exists`). `execute` is a no-op spy.
    """
    conn = AsyncMock()
    conn.fetchrow.return_value = state_row
    conn.fetchval.return_value = new_exists
    conn.execute.return_value = "UPDATE 1"
    conn.close.return_value = None
    return conn


def _run(coro):
    import asyncio

    return asyncio.run(coro)


PROPOSED = {
    "id": 7,
    "is_active": True,
    "superseded_by": None,
    "times_validated": 1,
    "has_independent_validation": False,
}
VALIDATED = {**PROPOSED, "times_validated": 4, "has_independent_validation": True}
SUPERSEDED = {**PROPOSED, "is_active": False, "superseded_by": 9}
RETIRED = {**PROPOSED, "is_active": False, "superseded_by": None}


class TestValidateGuard:
    def test_validate_proposed_legal(self):
        conn = _fake_conn(PROPOSED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("validate", lesson_id=7)))
        assert out["ok"] is True
        assert out["state"] == "validated"
        conn.execute.assert_awaited()  # the increment ran

    def test_validate_validated_legal_idempotent(self):
        conn = _fake_conn(VALIDATED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("validate", lesson_id=7)))
        assert out["ok"] is True

    def test_validate_superseded_rejected(self):
        conn = _fake_conn(SUPERSEDED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("validate", lesson_id=7)))
        assert "Illegal transition" in out["error"]
        assert out["state"] == "superseded"
        conn.execute.assert_not_awaited()  # rejected before any write

    def test_validate_retired_rejected(self):
        conn = _fake_conn(RETIRED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("validate", lesson_id=7)))
        assert "Illegal transition" in out["error"]
        assert out["state"] == "retired"
        conn.execute.assert_not_awaited()

    def test_validate_missing_lesson(self):
        conn = _fake_conn(None)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("validate", lesson_id=7)))
        assert out["error"] == "Lesson not found"


class TestDeactivateGuard:
    def test_deactivate_proposed_legal(self):
        conn = _fake_conn(PROPOSED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("deactivate", lesson_id=7)))
        assert out["state"] == "retired"
        conn.execute.assert_awaited()

    def test_deactivate_superseded_rejected(self):
        conn = _fake_conn(SUPERSEDED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("deactivate", lesson_id=7)))
        assert "Illegal transition" in out["error"]
        conn.execute.assert_not_awaited()

    def test_deactivate_already_retired_idempotent(self):
        conn = _fake_conn(RETIRED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("deactivate", lesson_id=7)))
        assert out["state"] == "retired"
        # idempotent: no write needed
        conn.execute.assert_not_awaited()


class TestSupersedeAction:
    def test_supersede_proposed_legal(self):
        conn = _fake_conn(PROPOSED, new_exists=1)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("supersede", lesson_id=7, data=json.dumps({"new_id": 9}))))
        assert out["ok"] is True
        assert out["state"] == "superseded"
        assert out["superseded_by"] == 9
        conn.execute.assert_awaited()

    def test_supersede_self_rejected(self):
        conn = _fake_conn(PROPOSED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("supersede", lesson_id=7, data=json.dumps({"new_id": 7}))))
        assert "cannot supersede itself" in out["error"]
        conn.execute.assert_not_awaited()

    def test_supersede_missing_new_lesson_rejected(self):
        conn = _fake_conn(PROPOSED, new_exists=None)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("supersede", lesson_id=7, data=json.dumps({"new_id": 9}))))
        assert "not found" in out["error"]
        conn.execute.assert_not_awaited()

    def test_supersede_already_superseded_rejected(self):
        conn = _fake_conn(SUPERSEDED, new_exists=1)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("supersede", lesson_id=7, data=json.dumps({"new_id": 9}))))
        assert "Illegal transition" in out["error"]
        assert out["state"] == "superseded"
        conn.execute.assert_not_awaited()

    def test_supersede_bad_payload_rejected(self):
        conn = _fake_conn(PROPOSED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("supersede", lesson_id=7, data=json.dumps({"new_id": 0}))))
        assert "LessonSupersede validation failed" in out["error"]


class TestUnknownAction:
    def test_unknown_action_lists_supersede(self):
        conn = _fake_conn(PROPOSED)
        with patch.object(server, "_db", AsyncMock(return_value=conn)):
            out = json.loads(_run(server.lessons_manage("archive", lesson_id=7)))
        assert "supersede" in out["error"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
