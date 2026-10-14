#!/usr/bin/env python3
"""Initial-only custody for the two fixed #643 maintenance duties."""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ordinary_bootstrap", Path(__file__).with_name("bootstrap-six-runtime-roles.py")
)
_ordinary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ordinary)
BootstrapError = _ordinary.BootstrapError
psql = _ordinary.psql
DUTIES = ("writer_watchdog", "matview_refresh")
# Exact bodies are bound from migration276. No arbitrary role/function argument.
FUNCTIONS = {
    "verdify_writer_watchdog_runtime.fn_attest_login()": (
        "0e9f2537fcd731c1fcb590499ac68b9ef766bd2f82f9f4c01e495c4dbc731715",
        False,
    ),
    "verdify_matview_refresh_runtime.fn_attest_login()": (
        "c85d97f4d25545f2909c6a650d4fd21a389a34b0c0f301eb40053e3abbc197d1",
        False,
    ),
    "verdify_writer_watchdog_runtime.fn_sweep(text,bigint)": (
        "a1ffe27b41489c311d2059d0a387a913599f1106d5fc3a0cb5aa24d63e34eee0",
        True,
    ),
    "verdify_matview_refresh_runtime.fn_refresh_mv_band_curve()": (
        "ece76e4b881969b892cabcabc6bceefae9a640645174f740d0e6102ef2b42c44",
        True,
    ),
    "verdify_matview_refresh_runtime.fn_refresh_mv_equipment_runtime_daily()": (
        "3551187d0a5e4d5bcc35ab98a91e86e0ba4381509e96058393b47b4e2ca7697e",
        True,
    ),
    "verdify_matview_refresh_runtime.fn_refresh_mv_daily_kpi()": (
        "1fd126a9c6bc31b84bb5de0b5bb3faff10a02fa436f7abb0f592c2bedc7aebbd",
        True,
    ),
}


def contract_sql():
    checks = [_ordinary.sealed_sql_276()]
    for duty in DUTIES:
        role = "verdify_" + duty + "_runtime"
        login = role + "_login"
        common = _ordinary.identity_sql(login, role).split("AND NOT r.rolsuper", 1)[1]
        common = "NOT r.rolsuper" + common.replace("current_user", "r.rolname")
        checks.append(
            "EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname='"
            + login
            + "' AND "
            + common
            + " AND 'search_path="
            + role
            + ", pg_catalog, public, pg_temp'=ANY(r.rolconfig))"
        )
        checks.extend(
            [
                "NOT has_schema_privilege('" + login + "','" + role + "','CREATE')",
                "(SELECT count(*)=0 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='"
                + role
                + "'))",
                "(SELECT count(*)=1 FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='"
                + role
                + "'))",
                "NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner IN (SELECT oid FROM pg_roles WHERE rolname IN ('"
                + role
                + "','"
                + login
                + "')))",
                "NOT EXISTS(SELECT 1 FROM pg_class WHERE relowner IN (SELECT oid FROM pg_roles WHERE rolname IN ('"
                + role
                + "','"
                + login
                + "')))",
                "NOT EXISTS(SELECT 1 FROM pg_proc WHERE proowner IN (SELECT oid FROM pg_roles WHERE rolname IN ('"
                + role
                + "','"
                + login
                + "')))",
            ]
        )
        for table in (
            "alert_log",
            "climate",
            "control_assignments",
            "effective_policy_vectors",
            "policy_delivery_outbox",
            "policy_exposures",
        ):
            checks.extend(
                [
                    "NOT has_table_privilege('"
                    + login
                    + "','public."
                    + table
                    + "','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                    "NOT has_any_column_privilege('"
                    + login
                    + "','public."
                    + table
                    + "','SELECT,INSERT,UPDATE,REFERENCES')",
                ]
            )
    for signature, (body_sha, definer) in FUNCTIONS.items():
        role = signature.split(".")[0]
        checks.append(
            "EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid=to_regprocedure('"
            + signature
            + "')"
            + " AND pg_get_userbyid(p.proowner)='verdify' AND p.prosecdef="
            + str(definer).upper()
            + " AND 'search_path=pg_catalog, public, pg_temp'=ANY(p.proconfig)"
            + " AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='"
            + body_sha
            + "'"
            + " AND has_function_privilege('"
            + role
            + "',p.oid,'EXECUTE')"
            + " AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a"
            + " WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_roles WHERE rolname='"
            + role
            + "'))))"
        )
    return "(" + " AND ".join(checks) + ")"


def bootstrap():
    admin = os.environ.get("DB_ADMIN_PASSWORD", "")
    if os.environ.get("DB_ADMIN_USER") != "verdify" or not os.environ.get("DB_NAME") or not admin:
        raise BootstrapError("owner/configured database custody required")
    credentials = {d: os.environ.get(d.upper() + "_DB_PASSWORD", "") for d in DUTIES}
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{64}", v) for v in credentials.values()):
        raise BootstrapError("credential shape refused")
    if len(set(credentials.values())) != 2 or admin in credentials.values():
        raise BootstrapError("distinct bounded custody required")
    guard = contract_sql()
    if psql("verdify", admin, ["SELECT " + guard]) != "t":
        raise BootstrapError("exact migration/role/function contract refused")
    names = ",".join("'verdify_" + d + "_runtime_login'" for d in DUTIES)
    state = json.loads(
        psql(
            "verdify",
            admin,
            ["SELECT json_object_agg(rolname,rolpassword IS NULL) FROM pg_authid WHERE rolname IN (" + names + ")"],
        )
    )
    if (
        not isinstance(state, dict)
        or set(state) != {"verdify_" + d + "_runtime_login" for d in DUTIES}
        or any(type(v) is not bool for v in state.values())
    ):
        raise BootstrapError("exact two role states required")

    def attest(duty):
        role = "verdify_" + duty + "_runtime"
        if psql(role + "_login", credentials[duty], ["SELECT " + role + ".fn_attest_login()"]) != "t":
            raise BootstrapError("actual bounded login attestation refused")

    for duty in DUTIES:
        if not state["verdify_" + duty + "_runtime_login"]:
            attest(duty)  # A mismatched existing credential grants no rotation.
    missing = [d for d in DUTIES if state["verdify_" + d + "_runtime_login"]]
    if missing:
        unchanged = " AND ".join(
            "(SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='"
            + name
            + "') IS NOT DISTINCT FROM "
            + str(value).upper()
            for name, value in state.items()
        )
        # No verifier-bearing utility/query/parameter retention, including an
        # overriding pgAudit setting. All values stay in private stdin/env.
        logging = (
            "SET statement_timeout='30s'; SET lock_timeout='5s';"
            "SET log_statement='none'; SET log_min_error_statement='panic';"
            "SET log_min_duration_statement=-1; SET log_duration=off;"
            "SET log_parameter_max_length=0; SET log_parameter_max_length_on_error=0;"
            "SET log_statement_stats=off; SET pg_stat_statements.track_utility=off;"
        )
        locked = (
            logging
            + "LOCK TABLE pg_catalog.pg_authid IN SHARE ROW EXCLUSIVE MODE; DO $guard$ BEGIN IF ("
            + guard
            + " AND "
            + unchanged
            + ") IS DISTINCT FROM TRUE"
            + " OR coalesce(nullif(current_setting('pgaudit.log',true),''),'none')<>'none'"
            + " THEN RAISE EXCEPTION 'maintenance custody changed or audit retention overrides'; END IF; END $guard$;"
            + "SET password_encryption='scram-sha-256';"
        )
        psql(
            "verdify",
            admin,
            [locked] + ["\\password verdify_" + d + "_runtime_login" for d in missing],
            password_input="".join(credentials[d] + "\n" + credentials[d] + "\n" for d in missing),
            transactional=True,
        )
    for duty in DUTIES:
        attest(duty)
    if psql("verdify", admin, ["SELECT " + guard]) != "t":
        raise BootstrapError("ending maintenance contract changed")
    print("[maintenance-runtime-bootstrap] two exact logins attested; initial-only custody; prior seals unchanged")


if __name__ == "__main__":
    try:
        bootstrap()
    except Exception:
        raise SystemExit("[maintenance-runtime-bootstrap] fail-closed; credential/database detail withheld") from None
