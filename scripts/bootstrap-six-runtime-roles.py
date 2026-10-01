#!/usr/bin/env python3
"""Initial-only verifier bootstrap for #643; never rotate an existing verifier."""

from __future__ import annotations

import json
import os
import re
import subprocess

DUTIES = ("grafana", "planner", "setpoint_server", "ha_backfill", "lab_publisher", "vision")
PREDECESSORS = {
    "api": "fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81",
    "ingestor": "15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e",
    "mcp": "81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45",
}
MIGRATION_SHA = "aed9c4e562ff0420e315d14211224d1fff6469b9e0d2a541aedbc0bc562447e0"


class BootstrapError(RuntimeError):
    pass


def psql(user, password, commands, *, password_input="", transactional=False):
    # Values stay in environment/stdin. Never relay raw errors/SQL/verifiers.
    args = [
        "psql",
        "-X",
        "--no-password",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--set=ON_ERROR_STOP=1",
        "-h",
        os.environ["DB_HOST"],
        "-p",
        os.environ["DB_PORT"],
        "-d",
        os.environ["DB_NAME"],
        "-U",
        user,
    ]
    if transactional:
        args.append("--single-transaction")
    for command in commands:
        args.extend(["--command", command])
    env = dict(os.environ, PGPASSWORD=password)
    result = subprocess.run(
        args, input=password_input, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, check=False
    )
    if result.returncode:
        raise BootstrapError("database bootstrap operation refused")
    return result.stdout.strip()


def sealed_sql():
    expressions = [
        f"encode(public.fn_runtime_ordinary_boundary_digest('verdify_{d}_runtime_login'),'hex')='{h}'"
        for d, h in PREDECESSORS.items()
        if d != "mcp"
    ]
    expressions += [
        "(SELECT count(*)=2 FROM public.runtime_ordinary_login_attestation_receipts)",
        f"encode(public.fn_mcp_runtime_boundary_digest(),'hex')='{PREDECESSORS['mcp']}'",
        "NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))",
        "EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r WHERE r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())",
        f"EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=268 AND filename='db/migrations/268-six-runtime-workload-role-boundaries.sql' AND sha256='{MIGRATION_SHA}' AND stamp_method='runner')",
    ]
    for duty in ("api", "ingestor"):
        expressions.append(
            "EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.login_name='verdify_"
            + duty
            + "_runtime_login' AND r.boundary_sha256=decode('"
            + PREDECESSORS[duty]
            + "','hex'))"
        )
    return " AND ".join(expressions)


def identity_sql(login, duty):
    return f"""current_user=session_user AND current_user='{login}'
      AND current_setting('search_path')='{duty}, pg_catalog, public, pg_temp'
      AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication
      AND NOT r.rolbypassrls AND r.rolcanlogin AND r.rolinherit
      AND NOT has_database_privilege(current_user,current_database(),'CREATE')
      AND NOT has_schema_privilege(current_user,'public','CREATE')
      AND (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid)=1
      AND EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles d ON d.oid=m.roleid
        WHERE m.member=r.oid AND d.rolname='{duty}' AND NOT m.admin_option
          AND m.inherit_option AND m.set_option AND NOT d.rolcanlogin AND NOT d.rolinherit
          AND NOT d.rolsuper AND NOT d.rolcreatedb AND NOT d.rolcreaterole AND NOT d.rolreplication AND NOT d.rolbypassrls)"""


def admin_role_contract_sql():
    checks = []
    for duty in DUTIES:
        role = "verdify_" + duty + "_runtime"
        login = role + "_login"
        common = identity_sql(login, role).split("AND NOT r.rolsuper", 1)[1]
        common = "NOT r.rolsuper" + common.replace("current_user", "r.rolname")
        config = "search_path=" + role + ", pg_catalog, public, pg_temp"
        checks.append("(r.rolname='" + login + "' AND '" + config + "'=ANY(r.rolconfig) AND " + common + ")")
    names = ",".join("'verdify_" + d + "_runtime_login'" for d in DUTIES)
    return (
        "(SELECT count(*)=6 AND bool_and(COALESCE(("
        + " OR ".join(checks)
        + "),FALSE)) FROM pg_roles r WHERE r.rolname IN ("
        + names
        + "))"
    )


def bootstrap():
    if os.environ.get("DB_ADMIN_USER") != "verdify" or not os.environ.get("DB_ADMIN_PASSWORD"):
        raise BootstrapError("owner credential contract mismatch")
    credentials = {d: os.environ.get(d.upper() + "_DB_PASSWORD", "") for d in DUTIES}
    if any(not re.fullmatch("[A-Za-z0-9_-]{64}", value) for value in credentials.values()):
        raise BootstrapError("runtime credential shape mismatch")
    if len(set(credentials.values())) != 6 or os.environ["DB_ADMIN_PASSWORD"] in credentials.values():
        raise BootstrapError("runtime credentials must be distinct from each other and owner")
    admin = os.environ["DB_ADMIN_PASSWORD"]
    if psql("verdify", admin, ["SELECT " + sealed_sql() + " AND " + admin_role_contract_sql()]) != "t":
        raise BootstrapError("immutable migration/seal prerequisite mismatch")
    names = ",".join("'verdify_" + d + "_runtime_login'" for d in DUTIES)
    state = json.loads(
        psql(
            "verdify",
            admin,
            [f"SELECT json_object_agg(rolname,rolpassword IS NULL) FROM pg_authid WHERE rolname IN ({names})"],
        )
    )
    expected = {"verdify_" + d + "_runtime_login" for d in DUTIES}
    if set(state) != expected or any(type(v) is not bool for v in state.values()):
        raise BootstrapError("six exact runtime roles required")

    # Existing verifiers must authenticate the supplied custody value before
    # anything is installed; a mismatch grants no password-change authority.
    def attest(duty):
        role = "verdify_" + duty + "_runtime"
        login = role + "_login"
        query = "SELECT " + identity_sql(login, role) + " FROM pg_roles r WHERE r.rolname=current_user"
        if psql(login, credentials[duty], [query]) != "t":
            raise BootstrapError("actual runtime login/duty attestation refused")

    for duty in DUTIES:
        if not state["verdify_" + duty + "_runtime_login"]:
            attest(duty)
    missing = [d for d in DUTIES if state["verdify_" + d + "_runtime_login"]]
    if missing:
        # Lock the shared role catalog before rechecking state. Any competing
        # verifier installation stops this transaction rather than rotating it.
        checks = " OR ".join(
            f"(SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='{name}') IS DISTINCT FROM {'TRUE' if value else 'FALSE'}"
            for name, value in state.items()
        )
        guard = (
            "LOCK TABLE pg_catalog.pg_authid IN SHARE ROW EXCLUSIVE MODE; DO $guard$ BEGIN IF ("
            + sealed_sql()
            + " AND "
            + admin_role_contract_sql()
            + ") IS DISTINCT FROM TRUE OR "
            + checks
            + " THEN RAISE EXCEPTION 'bootstrap authority changed'; END IF; END $guard$; SET password_encryption='scram-sha-256';"
        )
        commands = [guard] + ["\\password verdify_" + d + "_runtime_login" for d in missing]
        secret_input = "".join(credentials[d] + "\n" + credentials[d] + "\n" for d in missing)
        psql("verdify", admin, commands, password_input=secret_input, transactional=True)
    for duty in DUTIES:
        attest(duty)
    if psql("verdify", admin, ["SELECT " + sealed_sql() + " AND " + admin_role_contract_sql()]) != "t":
        raise BootstrapError("existing ordinary seals changed")
    print(
        "[six-runtime-role-bootstrap] six actual logins attested; initial-only verifiers; existing ordinary seals unchanged"
    )


if __name__ == "__main__":
    try:
        bootstrap()
    except Exception:
        raise SystemExit("[six-runtime-role-bootstrap] fail-closed; credential/database detail withheld") from None
