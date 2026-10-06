#!/usr/bin/env python3
"""Initial-only verifier bootstrap for #643; never rotate an existing verifier."""

from __future__ import annotations

import json
import os
import re
import subprocess

DUTIES = ("grafana", "planner", "setpoint_server", "ha_backfill", "lab_publisher", "vision")
PREDECESSORS = {
    "api": "ad619765f93959500d7ed438f90000ceaf614b2f744553d4a4e269a7b15103d3",
    "ingestor": "8bf588e5381e236f68aabc6672f61d982321a444f418973089d6a10a5a1efcf6",
    "mcp": "81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45",
}
MIGRATION_SHA = "aed9c4e562ff0420e315d14211224d1fff6469b9e0d2a541aedbc0bc562447e0"

OPS_MIGRATION_SHA = "672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532"

# A bootstrap accepts only catalog/source profiles qualified together. Unknown
# or intermediate migration states never inherit authority from a receipt.
SUCCESSOR_273 = {
    "api": "aef9e39647d84c313d76795f15b382eb5ebccb5828eecac83e73cbb97002e10e",
    "ingestor": "98e59209b41ba7a445150fde66be889bdc98689c43c80aa8bc5d0c3f7a677ef7",
    "mcp": "79e5bd322c1b9c60104c26b82b3d302d89fda26366031f7871f697f3c97ccf4b",
}
SUCCESSOR_MIGRATIONS = (
    (271, "271-legacy-band-trace-deprecation.sql", "8ddc650184a178b59102c46a6cf1c6b706578ce08d6680294a425694397f7642"),
    (
        272,
        "272-current-climate-scorecard-snapshot.sql",
        "ca2e6cd2ae157aa6ebdda58964fc3eb52cbdde70d7c69f158fa0eef6294d19aa",
    ),
    (
        273,
        "273-native-route-measurement-reader.sql",
        "a0e4fe7bea3d27ea0d4218c5d423707e19b821e2684d5884c1280b03f78df76a",
    ),
)

SUCCESSOR_274 = {
    "api": "67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e",
    "ingestor": "126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75",
    "mcp": "7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68",
}
PHYSICAL_MIGRATION = (
    274,
    "274-qualified-physical-crop-band-publication.sql",
    "0b939df1a79e3ce71835e59aedb5d1898805cd43cae72b79fa905f77067a8e4f",
)


SUCCESSOR_275 = {
    "api": "5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba",
    "ingestor": "8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743",
    "mcp": SUCCESSOR_274["mcp"],
}
LIGHTING_MIGRATION = (
    275,
    "275-lighting-minutes-policy-bounded-jit.sql",
    "f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4",
)


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


def ledger_row_sql(seq, filename, sha256):
    return (
        "EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' "
        f"AND seq={seq} AND filename='db/migrations/{filename}' AND sha256='{sha256}' AND stamp_method='runner')"
    )


def native_reader_scope_sql():
    # The protected catalog seals also bind this definition. Explicit capability
    # checks prevent a grant or raw-table shortcut from becoming bootstrap authority.
    reader = "to_regprocedure('public.fn_fixed_panel_native_route_measurement(date,text)')"
    permissions = [
        f"has_function_privilege('verdify_{d}_runtime_login',{reader},'EXECUTE') IS NOT DISTINCT FROM {str(allowed).upper()}"
        for d, allowed in (
            ("api", True),
            ("mcp", True),
            ("grafana", True),
            ("ingestor", False),
            ("planner", False),
            ("setpoint_server", False),
            ("ha_backfill", False),
            ("lab_publisher", False),
            ("vision", False),
        )
    ]
    no_raw_reads = [
        f"NOT has_table_privilege('verdify_{d}_runtime_login','public.fixed_panel_native_events','SELECT')"
        for d in ("api", "mcp", "ingestor", *DUTIES)
    ]
    properties = (
        "EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid="
        + reader
        + " AND p.prosecdef AND pg_get_userbyid(p.proowner)='verdify' "
        + "AND 'search_path=pg_catalog, public, pg_temp'=ANY(p.proconfig) "
        + "AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
        + "WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))"
    )
    return "(" + " AND ".join([properties, *permissions, *no_raw_reads]) + ")"


def seal_profile_sql(seq, digests):
    expressions = [
        f"(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')={seq}",
        *[
            f"encode(public.fn_runtime_ordinary_boundary_digest('verdify_{d}_runtime_login'),'hex')='{digests[d]}'"
            for d in ("api", "ingestor")
        ],
        f"encode(public.fn_mcp_runtime_boundary_digest(),'hex')='{digests['mcp']}'",
        *[
            "EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.login_name='verdify_"
            + d
            + "_runtime_login' AND r.boundary_sha256=decode('"
            + digests[d]
            + "','hex'))"
            for d in ("api", "ingestor")
        ],
        "EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r WHERE singleton "
        + "AND r.boundary_sha256=decode('"
        + digests["mcp"]
        + "','hex'))",
    ]
    return "(" + " AND ".join(expressions) + ")"


def sealed_sql_270_273():
    expressions = [
        "(SELECT count(*)=2 FROM public.runtime_ordinary_login_attestation_receipts)",
        "(SELECT count(*)=1 FROM public.mcp_runtime_boundary_receipt)",
        "NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))",
        "EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r WHERE singleton AND r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())",
        ledger_row_sql(268, "268-six-runtime-workload-role-boundaries.sql", MIGRATION_SHA),
        ledger_row_sql(270, "270-facility-safe-ops-projection.sql", OPS_MIGRATION_SHA),
    ]
    current = seal_profile_sql(270, PREDECESSORS)
    successor = (
        "("
        + " AND ".join(
            [
                seal_profile_sql(273, SUCCESSOR_273),
                *(ledger_row_sql(*migration) for migration in SUCCESSOR_MIGRATIONS),
                native_reader_scope_sql(),
            ]
        )
        + ")"
    )
    expressions.append("(" + current + " OR " + successor + ")")
    return " AND ".join(expressions)


def physical_reader_scope_sql():
    reader = "to_regprocedure('public.fn_physical_crop_band_evidence(date,text)')"
    private_functions = (
        "fn_publish_physical_crop_band_evidence(jsonb,jsonb)",
        "fn_validate_physical_crop_band_diagnostic(jsonb)",
        "fn_physical_crop_band_revision_immutable()",
    )
    checks = []
    for signature, definer in (
        ("fn_physical_crop_band_evidence(date,text)", True),
        *((name, False) for name in private_functions),
    ):
        function = "to_regprocedure('public." + signature + "')"
        checks.append(
            "EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid="
            + function
            + " AND p.prosecdef="
            + str(definer).upper()
            + " AND pg_get_userbyid(p.proowner)='verdify'"
            + " AND 'search_path=pg_catalog, public, pg_temp'=ANY(p.proconfig)"
            + " AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a"
            + " WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))"
        )
    for duty in ("api", "mcp", "ingestor", *DUTIES):
        login = "verdify_" + duty + "_runtime_login"
        checks.append(
            "has_function_privilege('"
            + login
            + "',"
            + reader
            + ",'EXECUTE') IS NOT DISTINCT FROM "
            + str(duty in ("api", "mcp")).upper()
        )
        for signature in private_functions:
            checks.append(
                "has_function_privilege('"
                + login
                + "',to_regprocedure('public."
                + signature
                + "'),'EXECUTE') IS NOT DISTINCT FROM FALSE"
            )
        checks.append(
            "has_table_privilege('"
            + login
            + "',to_regclass('public.physical_crop_band_revisions'),"
            + "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') IS NOT DISTINCT FROM FALSE"
        )
        checks.append(
            "has_sequence_privilege('"
            + login
            + "',to_regclass('public.physical_crop_band_revisions_revision_id_seq'),"
            + "'USAGE,SELECT,UPDATE') IS NOT DISTINCT FROM FALSE"
        )
    checks.append(
        "EXISTS(SELECT 1 FROM pg_class c WHERE c.oid=to_regclass('public.physical_crop_band_revisions')"
        + " AND pg_get_userbyid(c.relowner)='verdify'"
        + " AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a WHERE a.grantee=0))"
    )
    return "(" + " AND ".join(checks) + ")"


def sealed_sql_270_274():
    # Preserve the independently qualified predecessor expression byte-for-byte.
    # The new profile correlates exact catalog seals, ledger bytes and authority.
    successor = [
        "(SELECT count(*)=2 FROM public.runtime_ordinary_login_attestation_receipts)",
        "(SELECT count(*)=1 FROM public.mcp_runtime_boundary_receipt)",
        "NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))",
        "EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r WHERE singleton AND r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())",
        ledger_row_sql(268, "268-six-runtime-workload-role-boundaries.sql", MIGRATION_SHA),
        ledger_row_sql(270, "270-facility-safe-ops-projection.sql", OPS_MIGRATION_SHA),
        seal_profile_sql(274, SUCCESSOR_274),
        *(ledger_row_sql(*migration) for migration in SUCCESSOR_MIGRATIONS),
        ledger_row_sql(*PHYSICAL_MIGRATION),
        native_reader_scope_sql(),
        physical_reader_scope_sql(),
    ]
    return "((" + sealed_sql_270_273() + ") OR (" + " AND ".join(successor) + "))"


def sealed_sql_275():
    # Explicitly qualified successor only; never infer authority from later rows.
    expressions = [
        "(SELECT count(*)=2 FROM public.runtime_ordinary_login_attestation_receipts)",
        "(SELECT count(*)=1 FROM public.mcp_runtime_boundary_receipt)",
        "NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))",
        "EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r WHERE singleton AND r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())",
        ledger_row_sql(268, "268-six-runtime-workload-role-boundaries.sql", MIGRATION_SHA),
        ledger_row_sql(270, "270-facility-safe-ops-projection.sql", OPS_MIGRATION_SHA),
        seal_profile_sql(275, SUCCESSOR_275),
        *(ledger_row_sql(*migration) for migration in SUCCESSOR_MIGRATIONS),
        ledger_row_sql(*PHYSICAL_MIGRATION),
        ledger_row_sql(*LIGHTING_MIGRATION),
        native_reader_scope_sql(),
        physical_reader_scope_sql(),
    ]
    return "(" + " AND ".join(expressions) + ")"


def sealed_sql():
    return "(" + sealed_sql_270_274() + " OR " + sealed_sql_275() + ")"


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
