"""Planner startup fails closed without repairing schema or accepting an owner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "planner_runtime_boundary", Path(__file__).resolve().parents[1] / "planner_graph/runtime_db_boundary.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SCHEMA_COLUMNS = module.SCHEMA_COLUMNS
guard_connection = module.guard_connection
verify_schema = module.verify_schema


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.table = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.conn.statements.append((sql, params))
        assert sql.strip().startswith("SELECT"), "runtime tried to mutate migration-owned structure"
        if params and isinstance(params[0], str) and params[0].startswith("public."):
            self.table = params[0].split(".", 1)[1]

    def fetchone(self):
        return {"allowed": self.conn.allowed}

    def fetchall(self):
        return [{"attname": c} for c in self.conn.columns.get(self.table, set())]


class Connection:
    def __init__(self, *, allowed=True, columns=None):
        self.allowed = allowed
        self.columns = columns or SCHEMA_COLUMNS
        self.statements = []
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True


def test_runtime_identity_refusal_closes_connection_without_ddl(monkeypatch):
    monkeypatch.setenv("VERDIFY_PLANNER_RUNTIME_DB_ROLE_REQUIRED", "1")
    conn = Connection(allowed=False)
    with pytest.raises(RuntimeError, match="bounded runtime duty"):
        guard_connection(conn)
    assert conn.closed
    assert len(conn.statements) == 1


def test_native_identity_check_binds_login_session_and_exact_nonadmin_membership(monkeypatch):
    monkeypatch.setenv("VERDIFY_PLANNER_RUNTIME_DB_ROLE_REQUIRED", "1")
    conn = Connection()
    assert guard_connection(conn) is conn
    sql, params = conn.statements[0]
    assert params == ("verdify_planner_runtime_login", "verdify_planner_runtime_login", "verdify_planner_runtime")
    for predicate in [
        "current_user=%s",
        "session_user=%s",
        "NOT r.rolsuper",
        "NOT r.rolcreaterole",
        "NOT r.rolbypassrls",
        "m.inherit_option",
        "m.set_option",
        "NOT m.admin_option",
        "count(*)",
    ]:
        assert predicate in sql


def test_existing_schema_is_read_only_verified():
    conn = Connection()
    verify_schema(conn, list(SCHEMA_COLUMNS))
    assert len(conn.statements) == 3
    assert not conn.closed


def test_missing_migration_owned_memory_column_fails_without_runtime_repair():
    columns = {k: set(v) for k, v in SCHEMA_COLUMNS.items()}
    columns["planner_memory_items"].remove("trust_level")
    conn = Connection(columns=columns)
    with pytest.raises(RuntimeError, match="planner_memory_items"):
        verify_schema(conn, ["planner_memory_items"])
    assert len(conn.statements) == 1
