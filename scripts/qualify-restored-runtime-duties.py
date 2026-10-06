#!/usr/bin/env python3
"""Replay the accepted six-duty fixture using actual authenticated target logins.

Owner/session-auth proxies are never executed. Qualification mutations occur only
in an explicitly admitted current-schema isolated clone and end with rollback.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

DUTIES = {"planner", "setpoint_server", "ha_backfill", "vision", "lab_publisher", "grafana"}


def target_binding(raw, cluster_uid):
    """Bind a ROOT-owned fresh native readback, not caller-invented UID labels."""
    binding = json.loads(raw)
    assert set(binding) == {"cluster", "pod", "service"}
    cluster, pod, service = (binding[key] for key in ("cluster", "pod", "service"))
    for obj in (cluster, pod, service):
        assert obj["metadata"]["namespace"] == "verdify-db-rehearsal"
        assert obj["metadata"]["uid"] and not obj["metadata"].get("deletionTimestamp")
    assert cluster["metadata"]["name"] == "verdify-cnpg-s2"
    assert cluster["metadata"]["uid"] == cluster_uid
    assert cluster["status"]["currentPrimary"] == pod["metadata"]["name"]
    assert pod["metadata"]["labels"]["cnpg.io/cluster"] == "verdify-cnpg-s2"
    assert pod["status"]["phase"] == "Running"
    assert any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"])
    assert service["metadata"]["name"] == "verdify-cnpg-s2-rw"
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert service["spec"]["selector"]["cnpg.io/cluster"] == "verdify-cnpg-s2"
    selectors = [
        service["spec"]["selector"][k] for k in ("role", "cnpg.io/instanceRole") if k in service["spec"]["selector"]
    ]
    assert selectors and all(v == "primary" for v in selectors)
    assert any(
        o.get("kind") == "Cluster" and o.get("uid") == cluster_uid
        for o in service["metadata"].get("ownerReferences", [])
    )
    assert any(p["port"] == p.get("targetPort", 5432) == 5432 for p in service["spec"]["ports"])
    address = pod["status"]["podIP"]
    assert re.fullmatch(r"10\.42\.\d{1,3}\.\d{1,3}", address)
    return address, hashlib.sha256(raw).hexdigest()


def check_target_identity(identity, address, database_oid):
    assert identity["server_addr"] == address
    assert identity["database_oid"] == database_oid
    assert identity["cluster_name"] == "verdify-cnpg-s2"
    assert identity["replica"] is False
    assert identity["server_version_num"] == "160013"


def split_statements(text):
    # These per-duty fixture blocks contain ordinary SQL and single-quoted
    # literals, not procedural bodies. Preserve doubled quotes and semicolons
    # inside strings rather than silently changing a denied SQL payload.
    text = re.sub(r"^\s*--[^\n]*(?:\n|$)", "", text, flags=re.M)
    quoted = False
    start = 0
    index = 0
    result = []
    while index < len(text):
        if text[index] == "'":
            if quoted and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        if text[index] == ";" and not quoted:
            statement = text[start:index].strip()
            if statement:
                result.append(statement)
            start = index + 1
        index += 1
    assert not quoted and not text[start:].strip(), "incomplete SQL fixture"
    return result


def cases(fixture, duty):
    assert duty in DUTIES
    login = "verdify_" + duty + "_runtime_login"
    marker = "SET SESSION AUTHORIZATION " + login + ";"
    assert fixture.count(marker) == 1
    block = fixture.split(marker, 1)[1].split("RESET SESSION AUTHORIZATION;", 1)[0]
    result = []
    for sql in split_statements(block):
        if sql.startswith("SET search_path") or sql.startswith("SELECT json_build_object"):
            continue
        match = re.fullmatch(r"SELECT pg_temp\.expect_denied\('((?:[^']|'')*)'\)", sql, re.S)
        if match:
            result.append({"expect": "42501", "sql": match.group(1).replace("''", "'")})
        else:
            assert sql.startswith(("SELECT", "INSERT", "UPDATE")), "unexpected fixture operation"
            result.append({"expect": "allowed", "sql": sql})
    assert any(row["expect"] == "allowed" for row in result)
    assert any(row["expect"] == "42501" for row in result)
    return result


async def qualify(args):
    import importlib.util

    assert args.target_host == "verdify-cnpg-s2-rw.verdify-db-rehearsal.svc.cluster.local"
    assert args.target_database == "verdify_rehearsal"
    assert re.fullmatch(r"[0-9a-f-]{36}", args.cluster_uid)
    assert args.admission_sha256 and re.fullmatch(r"[0-9a-f]{64}", args.admission_sha256)
    address, binding_sha = target_binding(args.native_binding.read_bytes(), args.cluster_uid)
    assert args.database_oid > 0
    # Credentials come from an existing target-only Secret in the consuming
    # process. They are never arguments, source artifacts or receipt contents.
    login = "verdify_" + args.duty + "_runtime_login"
    password = os.environ["QUALIFICATION_DB_PASSWORD"]
    if importlib.util.find_spec("asyncpg"):
        import asyncpg

        conn = await asyncpg.connect(
            host=args.target_host,
            database=args.target_database,
            user=login,
            password=password,
            port=5432,
            timeout=10,
            server_settings={"application_name": "verdify-s2-duty-qualification"},
        )

        async def run(sql):
            if sql.startswith("SELECT") or " RETURNING " in sql:
                rows = await conn.fetch(sql)
                return [dict(row) for row in rows], len(rows)
            tag = await conn.execute(sql)
            return [], int(tag.rsplit(" ", 1)[-1]) if tag.rsplit(" ", 1)[-1].isdigit() else None
    else:
        import psycopg
        from psycopg.rows import dict_row

        conn = await psycopg.AsyncConnection.connect(
            host=args.target_host,
            dbname=args.target_database,
            user=login,
            password=password,
            port=5432,
            connect_timeout=10,
            row_factory=dict_row,
            application_name="verdify-s2-duty-qualification",
        )

        async def run(sql):
            cursor = await conn.execute(sql)
            rows = await cursor.fetchall() if cursor.description else []
            return rows, len(rows) if cursor.description else cursor.rowcount

    outcomes = []
    try:
        await run("BEGIN READ WRITE")
        await run("SET LOCAL statement_timeout='30s'")
        await run("SET LOCAL lock_timeout='5s'")
        rows, _count = await run(
            "SELECT current_user,session_user,current_database(),pg_catalog.host(inet_server_addr()) AS server_addr,current_setting('search_path') AS search_path,current_setting('transaction_read_only') AS transaction_read_only,(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,current_setting('cluster_name') AS cluster_name,pg_is_in_recovery() AS replica,current_setting('server_version_num') AS server_version_num"
        )
        identity = rows[0]
        assert identity["current_user"] == identity["session_user"] == login
        assert identity["current_database"] == args.target_database and identity["server_addr"]
        assert identity["search_path"] == "verdify_" + args.duty + "_runtime, pg_catalog, public, pg_temp"
        assert identity["transaction_read_only"] == "off"
        check_target_identity(identity, address, args.database_oid)
        fixture = args.fixture.read_text()
        for case in cases(fixture, args.duty):
            await run("SAVEPOINT duty_case")
            state = None
            count = None
            succeeded = False
            error_type = None
            try:
                _rows, count = await run(case["sql"])
                succeeded = True
            except Exception as exc:
                state = getattr(exc, "sqlstate", None)
                error_type = type(exc).__name__
            if case["expect"] == "42501" or not succeeded:
                await run("ROLLBACK TO SAVEPOINT duty_case")
            await run("RELEASE SAVEPOINT duty_case")
            passed = state == "42501" if case["expect"] == "42501" else succeeded
            outcomes.append(
                {
                    "sql_sha256": hashlib.sha256(case["sql"].encode()).hexdigest(),
                    "expect": case["expect"],
                    "sqlstate": state,
                    "error_type": error_type,
                    "row_count": count,
                    "passed": passed,
                }
            )
            if not passed:
                break
        await run("ROLLBACK")
        return {
            "schema": "verdify-restored-runtime-duty-proof-v1",
            "cluster_uid": args.cluster_uid,
            "admission_sha256": args.admission_sha256,
            "native_binding_sha256": binding_sha,
            "identity": identity,
            "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            "duty": args.duty,
            "cases": outcomes,
            "complete_case_count": len(cases(fixture, args.duty)),
            "qualified": len(outcomes) == len(cases(fixture, args.duty)) and all(c["passed"] for c in outcomes),
            "mutations_committed": False,
            "limitations": "Sequence allocation may advance on attempted inserts; owner must compare/restore clone sequence custody. Full application config rollout/rollback and production business health remain separate proofs.",
        }
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duty", choices=sorted(DUTIES), required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--admission-sha256", required=True)
    parser.add_argument("--native-binding", type=Path, required=True)
    parser.add_argument("--database-oid", type=int, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = asyncio.run(qualify(args))
        print(json.dumps(receipt, sort_keys=True))
        raise SystemExit(0 if receipt["qualified"] else 1)
    except Exception as exc:
        print(json.dumps({"qualified": False, "error_type": type(exc).__name__, "details": "withheld"}))
        raise SystemExit(2) from None
