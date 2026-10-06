from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location("qualification", ROOT / "scripts/qualify-ordinary-runtime-login.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Denied(Exception):
    sqlstate = "42501"


class Readonly(Exception):
    sqlstate = "25006"


class Undefined(Exception):
    sqlstate = "42P01"


class Connection:
    def __init__(self, exception=Denied, readonly="off"):
        self.sql = []
        self.exception = exception
        self.readonly = readonly
        self.closed = False

    async def fetch(self, sql):
        self.sql.append(sql)
        if "session_user AS session_user" in sql:
            return [
                {
                    "current_user": "verdify_mcp_runtime_login",
                    "session_user": "verdify_mcp_runtime_login",
                    "transaction_read_only": self.readonly,
                    "rolsuper": False,
                    "rolbypassrls": False,
                    "rolcreatedb": False,
                    "rolcreaterole": False,
                }
            ]
        if "FROM pg_class c" in sql:
            return [
                {"nspname": "public", "relname": "v_runtime_climate_read", "sel": True},
                {"nspname": "public", "relname": "control_assignments", "sel": True},
            ]
        if sql.startswith(("SET ROLE", "DELETE", "UPDATE")) or (
            sql.startswith("SELECT *") and "v_runtime_climate_read" not in sql and "control_assignments" not in sql
        ):
            raise self.exception()
        return []

    async def close(self):
        self.closed = True


def run(monkeypatch, tmp_path, connection):
    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))
    monkeypatch.setenv("DB_DSN", "postgresql://verdify_mcp_runtime_login:fixture@verdify-db:5432/verdify")
    source = tmp_path / "server.py"
    source.write_text("# exact candidate connection source")
    return asyncio.run(module().qualify("mcp", source))


def test_real_privilege_denials_require_42501_and_rollback_every_savepoint(monkeypatch, tmp_path):
    conn = Connection()
    result = run(monkeypatch, tmp_path, conn)
    assert result["qualified"] is True
    assert conn.sql[0] == "BEGIN READ WRITE"
    assert conn.sql[-1] == "ROLLBACK"
    assert conn.sql.count("SAVEPOINT boundary_probe") == len(result["deny_probes"])
    assert conn.sql.count("ROLLBACK TO SAVEPOINT boundary_probe") == len(result["deny_probes"])
    assert conn.closed
    assert all("WHERE FALSE" in sql for sql in conn.sql if sql.startswith(("DELETE", "UPDATE")))
    assert "fixture" not in str(result)


@pytest.mark.parametrize("exception", [Readonly, Undefined])
def test_readonly_or_missing_object_errors_never_count_as_privilege_denial(monkeypatch, tmp_path, exception):
    result = run(monkeypatch, tmp_path, Connection(exception=exception))
    assert result["qualified"] is False
    assert not any(probe["denied"] for probe in result["deny_probes"])


def test_readonly_transaction_fails_before_negative_probes(monkeypatch, tmp_path):
    conn = Connection(readonly="on")
    with pytest.raises(AssertionError):
        run(monkeypatch, tmp_path, conn)
    assert conn.closed
    assert not any(sql.startswith(("DELETE", "UPDATE", "SET ROLE")) for sql in conn.sql)
