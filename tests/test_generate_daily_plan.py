from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_generate_daily_plan():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate-daily-plan.py"
    spec = importlib.util.spec_from_file_location("generate_daily_plan_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_db_query_uses_structured_psql_command(monkeypatch):
    module = load_generate_daily_plan()
    sql = "select 'planner rows'"
    calls = []

    class Result:
        returncode = 0
        stdout = "planner rows\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setenv("VERDIFY_DAILY_PLAN_DB_CMD", "psql -t -A")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.db_query(sql) == "planner rows"

    cmd, kwargs = calls[0]
    assert cmd == ["psql", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql]
    assert "input" not in kwargs
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
