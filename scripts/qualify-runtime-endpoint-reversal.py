"""Actual owning connection construction, S2 -> frozen B -> S2, SELECT only.

Run in each owning image. No normal app entrypoint, owner proxy or provider loop.
Grafana uses its separately rendered actual server and datasource HTTP adapter.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROLES = ("api", "ingestor", "mcp", "planner", "setpoint_server", "ha_backfill", "vision", "lab_publisher")
CLUSTERS = {
    "verdify-cnpg-s2": "4f697776-df25-4e22-b930-0b76cf35496e",
    "verdify-cnpg-s2-pitr-b-frozen": "3981d0f2-4c72-48ac-9e09-826fe6bd4ed6",
}
IDENTITY = """SELECT current_user::text AS current_user,session_user::text AS session_user,
current_database() AS database,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
current_setting('cluster_name') AS cluster_name,pg_catalog.host(inet_server_addr()) AS backend_address,
pg_catalog.host(inet_client_addr()) AS client_address,pg_backend_pid() AS backend_pid,
current_setting('search_path') AS search_path,current_setting('statement_timeout') AS statement_timeout,
current_setting('default_transaction_read_only') AS default_read_only,current_setting('transaction_read_only') AS transaction_read_only,
pg_is_in_recovery() AS replica,
has_table_privilege(current_user,'public.schema_migrations','DELETE') AS ledger_delete_allowed,
(SELECT rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname=current_user) AS elevated,
(SELECT array_agg(d.rolname::text ORDER BY d.rolname) FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid WHERE m.member=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS memberships"""
HOT = {
    "api": "SELECT temp_low,temp_high,vpd_low,vpd_high,temp_target,vpd_target FROM fn_band_setpoints('2026-10-06T05:00:00Z'::timestamptz)",
    "ingestor": "SELECT max(ts) AS latest_ts FROM climate WHERE ts<'2026-10-06T00:00:00Z'",
    "mcp": "SELECT ts,temp_avg,vpd_avg,rh_avg FROM climate WHERE ts<'2026-10-06T00:00:00Z' ORDER BY ts DESC LIMIT 1",
    "planner": "SELECT * FROM climate WHERE ts<'2026-10-06T00:00:00Z' ORDER BY ts DESC LIMIT 10",
    "setpoint_server": "SELECT * FROM fn_lighting_minutes_policy('2000-01-01T00:00:00Z'::timestamptz,'vallery')",
    "ha_backfill": "SELECT * FROM climate WHERE ts<'2026-10-06T00:00:00Z' ORDER BY ts DESC LIMIT 10",
    "vision": "SELECT * FROM climate WHERE ts<'2026-10-06T00:00:00Z' ORDER BY ts DESC LIMIT 10",
    "lab_publisher": "SELECT * FROM crops ORDER BY id LIMIT 10",
}
DENY = {
    "mapping": "SELECT * FROM public.experiment_v2_randomization WHERE FALSE",
    "reveal": "SELECT * FROM public.experiment_v2_reveals WHERE FALSE",
    "owner": "SET ROLE verdify",
    "unrelated_dml_plan": "EXPLAIN (FORMAT JSON) DELETE FROM public.schema_migrations WHERE FALSE",
}


def deny_cases(role):
    other = "planner" if role != "planner" else "api"
    return {**DENY, "cross_workload": "SET ROLE verdify_" + other + "_runtime"}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def wait_transport(host, timeout=15):
    """Credential-free startup transport retry; no SQL deadline change."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, 5432), timeout=min(2, max(0.01, deadline - time.monotonic()))):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("bounded database transport unavailable") from None
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def checked_endpoints(binding):
    require(binding["schema"] == "cnpg-current275-nine-endpoint-reversal-v1", "new275 reversal binding required")
    endpoints = binding["endpoints"]
    require(set(endpoints) == set(CLUSTERS), "exact S2 and frozen-B endpoints required")
    for name, endpoint in endpoints.items():
        require(
            endpoint["cluster_uid"] == CLUSTERS[name] and endpoint["database_oid"] == 16447, "wrong target authority"
        )
        require(
            endpoint["host"] == name + "-rw.verdify-db-rehearsal.svc.cluster.local",
            "production or alternate route refused",
        )
        require(
            endpoint["new275_admission_installed"] is True and endpoint["full_data_sequence_seal"] is True,
            "both complete installed275 admissions required",
        )
        require(
            re.fullmatch(r"[0-9a-f]{64}", endpoint["admission_sha256"])
            and re.fullmatch(r"[0-9a-f]{64}", endpoint["installation_sha256"]),
            "admission custody required",
        )
    return endpoints


def checked_identity(identity, role, endpoint):
    require(
        identity["current_user"] == identity["session_user"] == "verdify_" + role + "_runtime_login",
        "actual ordinary login required",
    )
    require(
        identity["database"] == "verdify_rehearsal"
        and identity["database_oid"] == 16447
        and identity["cluster_name"] == endpoint["cluster"]
        and identity["backend_address"] == endpoint["primary_ip"]
        and identity["replica"] is False,
        "actual backend identity changed",
    )
    require(
        identity["default_read_only"] == identity["transaction_read_only"] == "on", "readonly startup fence missing"
    )
    expected = (
        "pg_catalog, public, pg_temp"
        if role in {"api", "ingestor", "mcp"}
        else "verdify_" + role + "_runtime, pg_catalog, public, pg_temp"
    )
    require(identity["search_path"] == expected, "default runtime schema changed")
    require(
        identity["elevated"] is False and identity["memberships"] == ["verdify_" + role + "_runtime"],
        "bounded runtime posture changed",
    )
    require(identity["ledger_delete_allowed"] is False, "unrelated ledger DML permission unexpectedly granted")
    require(0 < timeout_ms(identity["statement_timeout"]) <= 30000, "source statement fence exceeded30s or disabled")


def timeout_ms(value):
    match = re.fullmatch(r"([0-9]+)(ms|s|min|h)?", value)
    require(match is not None, "unexpected statement fence encoding")
    return int(match[1]) * {None: 1, "ms": 1, "s": 1000, "min": 60000, "h": 3600000}[match[2]]


def verify_source_files(profile):
    paths = {profile["module_path"]: profile["module_sha256"], **profile.get("supporting_source_sha256", {})}
    for name, expected in paths.items():
        path = Path(name)
        require(path.suffix in {".py", ".sh"}, "only owning source files may be inventoried")
        require(re.fullmatch(r"[0-9a-f]{64}", expected), "exact source hash required")
        require(digest(path.read_bytes()) == expected, "owning module/supporting source changed")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_constructor(path, role, scope):
    """Execute only the owning connect Call AST, never surrounding service loops."""
    tree = ast.parse(path.read_text())
    matches = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            if (node.func.value.id, node.func.attr) == (
                "asyncpg",
                "create_pool" if role in {"ingestor", "setpoint_server"} else "connect",
            ):
                # Main ingestor has one ordinary pool; optional other credential
                # constructors are explicit host/user keyword calls and refused.
                if role == "ingestor" and not (
                    node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "DB_DSN"
                ):
                    continue
                matches.append(node)
    require(len(matches) == 1, "owning connection constructor shape changed")
    call = matches[0]
    expression = ast.Expression(body=ast.Await(value=call))
    expression = ast.fix_missing_locations(expression)
    code = compile(expression, str(path), "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    return eval(code, scope), digest(ast.get_source_segment(path.read_text(), call).encode())  # noqa: S307 - exact source-pinned owning constructor only


def configure(role, endpoint, password):
    login = "verdify_" + role + "_runtime_login"
    options = "-c default_transaction_read_only=on -c statement_timeout=30000"
    dsn = (
        "postgresql://"
        + login
        + ":"
        + urllib.parse.quote(password, safe="")
        + "@"
        + endpoint["host"]
        + ":5432/verdify_rehearsal?"
        + urllib.parse.urlencode({"options": options})
    )
    values = {
        "DB_HOST": endpoint["host"],
        "DB_PORT": "5432",
        "DB_NAME": "verdify_rehearsal",
        "DB_USER": login,
        "DB_PASSWORD": password,
        "DB_PASS": password,
        "DB_DSN": dsn,
        "DATABASE_URL": dsn,
        "VERDIFY_DB_DSN": dsn,
        "PLANNER_DB_DSN": dsn,
        "PLANNER_MEMORY_DB_DSN": dsn,
        "PGHOST": endpoint["host"],
        "PGPORT": "5432",
        "PGDATABASE": "verdify_rehearsal",
        "PGUSER": login,
        "PGPASSWORD": password,
        "PGOPTIONS": options,
        "VERDIFY_DEVICE_WRITE_ENABLED": "0",
        "VERDIFY_" + role.upper() + "_RUNTIME_DB_ROLE_REQUIRED": "1",
    }
    os.environ.update(values)
    return dsn


async def async_probe(role, profile, endpoint, password, wave):
    import asyncpg

    dsn = configure(role, endpoint, password)
    module_path = Path(profile["module_path"])
    require(digest(module_path.read_bytes()) == profile["module_sha256"], "actual owning source changed")
    sys.path.insert(0, str(module_path.parent))
    pool = None
    context = None
    conn = None
    constructor_sha = None
    consumer = None
    context_entered = False
    result = None
    try:
        if role == "api":
            consumer = load_module(module_path, "endpoint_api_" + wave)
            require(
                not any(
                    os.environ.get(k)
                    for k in ("VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD", "OPENAI_API_KEY", "ESP32_API_KEY")
                ),
                "extra authority refused",
            )
            context = consumer.lifespan(consumer.app)
            await context.__aenter__()
            context_entered = True
            pool = consumer.pool
            require(consumer.experiment_lifecycle_pool is None, "extra lifecycle pool refused")
            constructor_sha = digest(inspect_source(module_path, "lifespan"))
        elif role == "mcp":
            consumer = load_module(module_path, "endpoint_mcp_" + wave)
            pool = await consumer._kpi_fanout_pool_get()
            constructor_sha = digest(inspect_source(module_path, "_kpi_fanout_pool_get"))
        elif role == "ingestor":
            consumer = load_module(module_path, "endpoint_ingestor_" + wave)
            require(
                consumer._fanout_publisher is None and consumer.shared.esp32.get("client") is None,
                "normal writer activity refused",
            )
            pending, constructor_sha = source_constructor(module_path, role, {"asyncpg": asyncpg, "DB_DSN": dsn})
            pool = await pending
            await consumer.attest_ordinary_ingestor_runtime_role(pool)
        else:
            adapter = load_module(Path("/qualification/qualify-ordinary-runtime-login.py"), "endpoint_dsn_" + wave)
            actual_dsn = adapter.application_dsn(role, module_path)
            parsed = urllib.parse.urlsplit(actual_dsn)
            require(
                parsed.hostname == endpoint["host"]
                and urllib.parse.unquote(parsed.username) == "verdify_" + role + "_runtime_login",
                "owning builder chose an unauthorized endpoint/login",
            )
            # Only required builder functions are compiled; source constructor Call
            # invokes that exact builder result, without executing its normal main.
            builder = adapter.SOURCES[role][1]
            scope = {"asyncpg": asyncpg, builder: lambda: actual_dsn}
            pending, constructor_sha = source_constructor(module_path, role, scope)
            client = await pending
            if role == "setpoint_server":
                pool = client
            else:
                conn = client
        if pool is not None:
            async with pool.acquire() as acquired:
                result = await run_async_queries(acquired, role, endpoint)
        else:
            result = await run_async_queries(conn, role, endpoint)
        result.update(source_connection_constructor_sha256=constructor_sha, actual_source_pool_or_client=True)
    finally:
        if context is not None and context_entered:
            await context.__aexit__(None, None, None)
        elif role == "api" and consumer is not None and consumer.pool is not None:
            await asyncio.wait_for(consumer.pool.close(), 10)
        elif pool is not None:
            await asyncio.wait_for(pool.close(), 10)
        elif conn is not None:
            await conn.close()
    require(pool is None or pool.is_closing(), "owning pool did not close")
    result["complete_pool_or_client_close"] = True
    return result


def inspect_source(path, name):
    tree = ast.parse(path.read_text())
    matches = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    require(len(matches) == 1, "owning pool source missing")
    return ast.get_source_segment(path.read_text(), matches[0]).encode()


async def run_async_queries(conn, role, endpoint):
    identity = dict(await conn.fetchrow(IDENTITY))
    checked_identity(identity, role, endpoint)
    rows = [dict(v) for v in await conn.fetch(HOT[role])]
    outcomes = []
    for label, sql in deny_cases(role).items():
        try:
            await conn.fetch(sql)
        except async_error_types() as error:
            require(error.sqlstate == "42501", "expected privilege denial missing")
            outcomes.append({"case": label, "sqlstate": error.sqlstate})
        else:
            raise RuntimeError("forbidden capability was accepted")
    return {
        "identity": identity,
        "hot_row_count": len(rows),
        "hot_rows_sha256": digest(json.dumps(rows, default=str, sort_keys=True).encode()),
        "deny": outcomes,
        "committed_row_writes": False,
    }


def async_error_types():
    import asyncpg

    return (asyncpg.PostgresError,)


def sync_probe(role, profile, endpoint, password):
    configure(role, endpoint, password)
    path = Path(profile["module_path"])
    require(digest(path.read_bytes()) == profile["module_sha256"], "owning source changed")
    if role == "lab_publisher":
        return publisher_probe(path, endpoint)
    sys.path.insert(0, "/app")
    from planner_graph.clients.db import VerdifyReadClient
    from planner_graph.config import _build_postgres_dsn

    conn = VerdifyReadClient(_build_postgres_dsn())._connect()
    constructor_sha = digest(inspect_source(Path("/app/planner_graph/clients/db.py"), "_connect"))
    try:
        identity = dict(conn.execute(IDENTITY).fetchone())
        checked_identity(identity, role, endpoint)
        rows = conn.execute(HOT[role]).fetchall()
        outcomes = []
        for label, sql in deny_cases(role).items():
            try:
                with conn.transaction():
                    conn.execute(sql)
            except Exception as error:
                require(getattr(error, "sqlstate", None) == "42501", "expected privilege denial missing")
                outcomes.append({"case": label, "sqlstate": "42501", "planned_dml_only": label == "unrelated_dml_plan"})
            else:
                raise RuntimeError("forbidden capability accepted")
        conn.rollback()
    finally:
        conn.close()
    return {
        "identity": identity,
        "hot_row_count": len(rows),
        "hot_rows_sha256": digest(json.dumps(rows, default=str, sort_keys=True).encode()),
        "deny": outcomes,
        "source_connection_constructor_sha256": constructor_sha,
        "complete_pool_or_client_close": conn.closed,
        "actual_source_pool_or_client": True,
        "committed_row_writes": False,
    }


def publisher_probe(path, endpoint):
    expected = (
        'export VERDIFY_DAILY_PLAN_DB_CMD="${VERDIFY_DAILY_PLAN_DB_CMD:-psql -U ${PGUSER} -d ${PGDATABASE} -t -A}"'
    )
    require(expected in path.read_text(), "owning publisher psql command changed")
    command = [
        "psql",
        "-X",
        "-U",
        os.environ["PGUSER"],
        "-d",
        os.environ["PGDATABASE"],
        "-t",
        "-A",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        "VERBOSITY=sqlstate",
    ]
    hot = (
        "SELECT json_build_object('row_count',count(*),'rows_sha256',encode(sha256(convert_to(COALESCE(jsonb_agg(to_jsonb(q))::text,'[]'),'UTF8')),'hex')) FROM ("
        + HOT["lab_publisher"]
        + ") q;"
    )
    identity = "SELECT row_to_json(q) FROM (" + IDENTITY + ") q;"
    result = subprocess.run(
        command, input=("BEGIN READ ONLY;" + identity + hot + "ROLLBACK;").encode(), capture_output=True, timeout=40
    )
    require(result.returncode == 0, "owning psql hot read refused; private errors withheld")
    rows = [json.loads(v) for v in result.stdout.decode().splitlines()]
    require(len(rows) == 2, "owning psql metadata response changed")
    checked_identity(rows[0], "lab_publisher", endpoint)
    outcomes = []
    for label, sql in deny_cases("lab_publisher").items():
        r = subprocess.run(
            command,
            input=("BEGIN READ ONLY;" + identity + sql + ";ROLLBACK;").encode(),
            capture_output=True,
            timeout=40,
        )
        identities = [json.loads(v) for v in r.stdout.decode().splitlines() if v.startswith("{")]
        require(len(identities) == 1, "same-backend denial identity missing")
        checked_identity(identities[0], "lab_publisher", endpoint)
        states = [v.decode() for v in re.findall(rb"ERROR:\s+([A-Z0-9]{5})", r.stderr)]
        require(r.returncode != 0 and states == ["42501"], "exact owning psql privilege denial missing")
        outcomes.append(
            {
                "case": label,
                "sqlstate": "42501",
                "backend_pid": identities[0]["backend_pid"],
                "planned_dml_only": label == "unrelated_dml_plan",
            }
        )
    return {
        "identity": rows[0],
        "hot_row_count": rows[1]["row_count"],
        "hot_rows_sha256": rows[1]["rows_sha256"],
        "deny": outcomes,
        "source_connection_constructor_sha256": digest(expected.encode()),
        "actual_source_pool_or_client": True,
        "complete_pool_or_client_close": True,
        "committed_row_writes": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--duty", choices=ROLES, required=True)
    args = parser.parse_args()
    binding = json.loads(args.binding.read_text())
    endpoints = checked_endpoints(binding)
    profile = binding["profiles"][args.duty]
    verify_source_files(profile)
    require(os.environ.get("VERDIFY_DEVICE_WRITE_ENABLED") == "0", "device authority refused")
    if profile["identity_kind"] == "baked-image":
        require(
            os.environ.get("VERDIFY_GIT_SHA") == profile["image_source_sha"],
            "unknown or changed baked identity refused",
        )
    else:
        require(
            profile["identity_kind"] == "mounted-source" and profile["mounted_source_sha"],
            "explicit mounted source identity required",
        )
    passwords = {
        "before": os.environ["SOURCE_DB_PASSWORD"],
        "target": os.environ["TARGET_DB_PASSWORD"],
        "restored": os.environ["SOURCE_DB_PASSWORD"],
    }
    require(passwords["before"] == passwords["target"], "credential custody differs; no credential flip allowed")
    waves = []
    for wave, cluster in (
        ("before", "verdify-cnpg-s2"),
        ("target", "verdify-cnpg-s2-pitr-b-frozen"),
        ("restored", "verdify-cnpg-s2"),
    ):
        endpoint = dict(endpoints[cluster], cluster=cluster)
        started = time.monotonic()
        wait_transport(endpoint["host"])
        if args.duty in {"planner", "lab_publisher"}:
            result = sync_probe(args.duty, profile, endpoint, passwords[wave])
        else:
            result = asyncio.run(async_probe(args.duty, profile, endpoint, passwords[wave], wave))
        result.update(wave=wave, elapsed_seconds=time.monotonic() - started)
        waves.append(result)
    require(len({w["hot_rows_sha256"] for w in waves}) == 1, "hot-read parity or reversal differed")
    print(
        json.dumps(
            {
                "qualified": True,
                "duty": args.duty,
                "waves": waves,
                "production_endpoint_changed": False,
                "normal_service_loop_started": False,
            }
        )
    )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main()
    except BaseException as error:
        print(
            json.dumps(
                {
                    "qualified": False,
                    "error_category": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None),
                }
            )
        )
        raise SystemExit(1) from None
