#!/usr/bin/env python3
"""Run inside an ordinary workload: real DSN/TCP login and zero-row ACL probes.

No owner SET ROLE proxy, model/device requests or committed data changes.
Allowed write behavior and rollout/rollback remain separate restored-data proofs.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import hmac
import json
import os
import urllib.parse
from pathlib import Path

ROLES = {
    d: f"verdify_{d}_runtime_login"
    for d in (
        "api",
        "ingestor",
        "mcp",
        "planner",
        "setpoint_server",
        "ha_backfill",
        "lab_publisher",
        "vision",
    )
}
SOURCES = {
    "api": ("/app/main.py", "get_db_dsn"),
    "ingestor": ("/app/ingestor/config.py", "get_db_dsn"),
    "mcp": ("/app/mcp/server.py", None),
    "planner": ("/app/planner_graph/config.py", "_build_postgres_dsn"),
    "setpoint_server": ("/app/scripts/setpoint-server.py", "get_db_url"),
    "ha_backfill": ("/scripts/backfill-ha-gaps.py", "build_db_dsn"),
    "vision": ("/vsrc/analyze-greenhouse-snapshot.py", "get_db_url"),
    "lab_publisher": ("/app/scripts/lab-publish-k3s.sh", None),
}


def application_dsn(duty, path):
    source = path.read_text()
    scope = {"os": os, "hmac": hmac, "urllib": urllib, "Path": Path}
    if duty in {"mcp", "lab_publisher"}:
        # MCP requires an explicit DB_DSN in its production profile. Publisher
        # exports this exact DSN from PG*/DB* in its shell before its DB clients.
        if duty == "mcp":
            return os.environ["DB_DSN"]
        return os.environ.get("DB_DSN") or (
            f"postgresql://{os.environ.get('PGUSER') or os.environ['DB_USER']}:"
            f"{os.environ.get('PGPASSWORD') or os.environ.get('DB_PASSWORD') or os.environ['POSTGRES_PASSWORD']}@"
            f"{os.environ.get('PGHOST') or os.environ['DB_HOST']}:"
            f"{os.environ.get('PGPORT') or os.environ.get('DB_PORT', '5432')}/"
            f"{os.environ.get('PGDATABASE') or os.environ.get('DB_NAME', 'verdify')}"
        )
    # Compile the actual image's connection builder only. Importing the whole
    # app may start background handlers or read provider/device credentials.
    tree = ast.parse(source)
    function = SOURCES[duty][1]
    names = {function}
    if duty == "api":
        names |= {"_api_runtime_db_role_required", "API_RUNTIME_DB_ROLE_REQUIRED_ENV", "API_RUNTIME_DB_LOGIN"}
    if duty == "ingestor":
        names |= {"DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS", "DB_DSN"}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)  # noqa: S102 - only reviewed image DSN builders
    if duty == "planner":
        base = scope[function]()
        dsns = [os.environ.get(name) or base for name in ("PLANNER_DB_DSN", "VERDIFY_DB_DSN", "PLANNER_MEMORY_DB_DSN")]
        assert len(set(dsns)) == 1, "separate planner DSNs require distinct qualification"
        return dsns[0]
    return scope[function]()


IDENTITY = """SELECT current_user AS current_user,session_user AS session_user,
 current_database() AS database,inet_server_addr()::text AS server_addr,
 inet_client_addr()::text AS client_addr,current_setting('search_path') AS search_path,
 current_setting('transaction_read_only') AS transaction_read_only,
 r.rolsuper,r.rolbypassrls,r.rolcreatedb,r.rolcreaterole
 FROM pg_roles r WHERE rolname=current_user"""
TABLES = """SELECT n.nspname,c.relname,c.relkind::text,
 has_table_privilege(current_user,c.oid,'SELECT') AS sel,
 has_table_privilege(current_user,c.oid,'INSERT') AS ins,
 has_table_privilege(current_user,c.oid,'UPDATE') AS upd,
 has_table_privilege(current_user,c.oid,'DELETE') AS del
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE (n.nspname='public' OR n.nspname LIKE 'verdify%runtime')
 AND c.relkind IN ('r','v','m','p') ORDER BY 1,2"""


def quote(name):
    return '"' + name.replace('"', '""') + '"'


async def qualify(duty, path):
    dsn = application_dsn(duty, path)
    parsed = urllib.parse.urlsplit(dsn)
    assert urllib.parse.unquote(parsed.username or "") == ROLES[duty]
    assert parsed.hostname and parsed.hostname not in {"localhost", "127.0.0.1"}
    assert parsed.path == "/verdify", "production probe only; use separate restored qualification"
    if duty == "planner":
        import psycopg
        from psycopg.rows import dict_row

        conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row, connect_timeout=10)

        async def execute(sql):
            cursor = await conn.execute(sql)
            return await cursor.fetchall() if cursor.description else []
    else:
        import asyncpg

        conn = await asyncpg.connect(
            dsn, timeout=10, server_settings={"application_name": "verdify-runtime-qualification"}
        )

        async def execute(sql):
            records = await conn.fetch(sql)
            return [dict(row) for row in records]

    outcomes = []
    try:
        await execute("BEGIN READ WRITE")
        await execute("SET LOCAL statement_timeout='5s'")
        await execute("SET LOCAL lock_timeout='1s'")
        identity = (await execute(IDENTITY))[0]
        assert identity["current_user"] == identity["session_user"] == ROLES[duty]
        assert identity["transaction_read_only"] == "off", "read-only rejection cannot prove privilege denial"
        assert not any(identity[k] for k in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole"))
        tables = await execute(TABLES)
        # Exercise real effective SELECT privileges without emitting row values.
        for table in tables:
            if table["sel"]:
                name = quote(table["nspname"]) + "." + quote(table["relname"])
                await execute(f"SELECT * FROM {name} WHERE FALSE")
        negatives = {
            "owner_role": "SET ROLE verdify",
            "mapping_read": "SELECT * FROM public.experiment_v2_randomization WHERE FALSE",
            "blinded_reveal_read": "SELECT * FROM public.experiment_v2_reveals WHERE FALSE",
            "base_delete": "DELETE FROM public.climate WHERE FALSE",
            "base_update": "UPDATE public.climate SET ts=ts WHERE FALSE",
        }
        for other in ("planner", "setpoint_server", "ha_backfill", "vision", "lab_publisher", "grafana"):
            if other != duty:
                negatives[f"cross_schema_{other}"] = (
                    f"SELECT * FROM verdify_{other}_runtime."
                    + {
                        "planner": "planner_graph_runs",
                        "setpoint_server": "v_runtime_equipment_state_write",
                        "ha_backfill": "climate",
                        "vision": "image_observations",
                        "lab_publisher": "climate",
                        "grafana": "v_system_health_score",
                    }[other]
                    + " WHERE FALSE"
                )
        for name, sql in negatives.items():
            await execute("SAVEPOINT boundary_probe")
            state = None
            try:
                await execute(sql)
            except Exception as exc:
                state = getattr(exc, "sqlstate", None)
            finally:
                await execute("ROLLBACK TO SAVEPOINT boundary_probe")
                await execute("RELEASE SAVEPOINT boundary_probe")
            outcomes.append({"probe": name, "sqlstate": state, "denied": state == "42501"})
        await execute("ROLLBACK")
        return {
            "schema": "verdify-runtime-login-qualification-v1",
            "qualification_scope": "actual application DSN/TCP identity, granted zero-row reads and explicit privilege denials",
            "transport": {"host": parsed.hostname, "port": parsed.port or 5432, "database": parsed.path[1:]},
            "duty": duty,
            "application_source_path": str(path),
            "application_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "identity": identity,
            "allowed_read_objects": sum(t["sel"] for t in tables),
            "table_privileges": tables,
            "deny_probes": outcomes,
            "qualified": all(p["denied"] for p in outcomes),
            "writes": "no rows written; allowed write/rollback behavior requires restored-data qualification",
        }
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duty", required=True, choices=ROLES)
    parser.add_argument("--source-path", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(qualify(args.duty, args.source_path or Path(SOURCES[args.duty][0])))
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(0 if result["qualified"] else 1)
    except Exception as exc:
        # PostgreSQL messages and URLs can contain credential-bearing input.
        print(json.dumps({"qualified": False, "error_type": type(exc).__name__, "detail": "withheld"}))
        raise SystemExit(2) from None
