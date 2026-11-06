#!/usr/bin/env python3
"""Initial-only verifier bootstrap for #643; never rotate an existing verifier."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

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


# Linux caps one argv string at MAX_ARG_STRLEN (128 KiB), and exec fails with E2BIG
# before psql starts. The sealed profile gains an alternative per successor
# migration and passed that limit at 280, so longer SQL goes through --file on the
# Job's private /tmp. psql runs --command and --file actions in the order given.
# bootstrap-maintenance-runtime-roles.py shares this helper.
# No command carries a credential: values stay in environment and stdin.
COMMAND_ARG_MAX_BYTES = 64 * 1024


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
    env = dict(os.environ, PGPASSWORD=password)
    with tempfile.TemporaryDirectory(prefix="bootstrap-sql-") as workdir:
        for index, command in enumerate(commands):
            if len(command.encode()) <= COMMAND_ARG_MAX_BYTES:
                args.extend(["--command", command])
                continue
            path = os.path.join(workdir, f"{index}.sql")
            with open(path, "w", encoding="utf-8") as sql_file:
                sql_file.write(command)
            args.extend(["--file", path])
        result = subprocess.run(
            args,
            input=password_input,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            check=False,
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


MAINTENANCE_MIGRATION = (
    276,
    "276-ordinary-maintenance-runtime-boundaries.sql",
    "7509d3f84e64c92ef0539be15c3a64c4fb60f9f84bb2e053b4246d9ae4331764",
)


def sealed_sql_276():
    # No prior role, function, receipt or grant changes: only exact new private
    # maintenance duties. Keep every275 predicate; bind the explicit276 ledger.
    predecessor = sealed_sql_275()
    current = seal_profile_sql(275, SUCCESSOR_275)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(276, SUCCESSOR_275), 1)
        + " AND "
        + ledger_row_sql(*MAINTENANCE_MIGRATION)
        + ")"
    )


SUCCESSOR_277 = {
    "api": SUCCESSOR_275["api"],
    "ingestor": SUCCESSOR_275["ingestor"],
    "mcp": "1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02",
}
GETTER_MIGRATION = (
    277,
    "277-realized-solar-night-readback-timeline.sql",
    "a8a0bc063bd655d2e96dd3d424768169edb4ea714c7a1b51dca2b41cc39b29a1",
)


def sealed_sql_277():
    # Preserve the qualified276 expression. Admit only the measured getter
    # successor with its exact ledger row, three seals and existing authority.
    predecessor = sealed_sql_276()
    current = seal_profile_sql(276, SUCCESSOR_275)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(277, SUCCESSOR_277), 1)
        + " AND "
        + ledger_row_sql(*GETTER_MIGRATION)
        + ")"
    )


GRAFANA_TIMEOUT_MIGRATION = (
    278,
    "278-grafana-public-saved-query-bounds.sql",
    "3ba5b19da2695d6656cfd5174dac0028eba917738f5498b83af0914ec5d86312",
)


def sealed_sql_278():
    # Keep every277 authority predicate, bind the exact278 runner stamp, and
    # retain all three natively measured seals. Only Grafana role defaults change.
    predecessor = sealed_sql_277()
    current = seal_profile_sql(277, SUCCESSOR_277)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(278, SUCCESSOR_277), 1)
        + " AND "
        + ledger_row_sql(*GRAFANA_TIMEOUT_MIGRATION)
        + ")"
    )


SOURCE_CLOCK_MIGRATION = (
    279,
    "279-authentic-meter-source-clock-consumers.sql",
    "e610493bc9c8b9ec376b520e0c304f7bbd9f4d73d6d98e765e54408758eb3114",
)
SUCCESSOR_279 = {
    "api": "9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e",
    "ingestor": "0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7",
    "mcp": "1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02",
}


def sealed_sql_279():
    predecessor = sealed_sql_278()
    current = seal_profile_sql(278, SUCCESSOR_277)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(279, SUCCESSOR_279), 1)
        + " AND "
        + ledger_row_sql(*SOURCE_CLOCK_MIGRATION)
        + ")"
    )


DESIRED_STATE_MIGRATION = (
    280,
    "280-desired-state-activity-log.sql",
    "44531a0c3a9ddd48f64ce639f9f9f1c2d0f948960ba65a68943d5947881b8bfe",
)
SUCCESSOR_280 = {
    "api": "af3647c841270c17097fd00c5d27d8dd6b46413a15838f08fd5b6cbbcda343d7",
    "ingestor": "4697e11eb2e66a73f727304df3419d3dd4635199bc346ba5e62cfe2f2a194103",
    "mcp": "685212a93b79c980c422db5baacb8f61309af392a9a7a8e9a46baf8e55fde291",
}


def sealed_sql_280():
    # Keep every279 predicate and bind the exact280 runner stamp with the three
    # natively qualified seals its desired-state and activity-log grants move.
    predecessor = sealed_sql_279()
    current = seal_profile_sql(279, SUCCESSOR_279)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(280, SUCCESSOR_280), 1)
        + " AND "
        + ledger_row_sql(*DESIRED_STATE_MIGRATION)
        + ")"
    )


IRRIGATION_MINUTES_MIGRATION = (
    281,
    "281-irrigation-minutes-by-zone.sql",
    "45a6d18db74d6ae5f3744049b77a91f9fc243a099483949eb369a6a0e8d2f3f8",
)
SUCCESSOR_281 = {
    "api": "aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7",
    "ingestor": "2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db",
    "mcp": "9cb30130e47e95af13af14f2fc08c89e2422e89e14bd3b448e4448849ab194b3",
}


def sealed_sql_281():
    # Keep every 280 predicate and bind the exact 281 runner stamp with the three
    # seals its minutes-by-zone view and reader grants move.
    predecessor = sealed_sql_280()
    current = seal_profile_sql(280, SUCCESSOR_280)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(281, SUCCESSOR_281), 1)
        + " AND "
        + ledger_row_sql(*IRRIGATION_MINUTES_MIGRATION)
        + ")"
    )


DASHBOARD_LOGIN_MIGRATION = (
    282,
    "282-dashboard-runtime-login.sql",
    "b58f0df3b66f79dc968aee502d41b4860e269285c5369dc0a06273c44908377a",
)
# 282 grants only on the objects it creates, so it moves no seal: its profile is
# 281's qualified successor, as 276 kept 275's. The rolled-back native
# qualification, with 280 and 281 applied first, measures it.
SUCCESSOR_282 = SUCCESSOR_281


def sealed_sql_282():
    # Keep every 281 predicate and bind the exact 282 runner stamp; the dashboard
    # duty, its schema and its sessions table change no seal.
    predecessor = sealed_sql_281()
    current = seal_profile_sql(281, SUCCESSOR_281)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(282, SUCCESSOR_282), 1)
        + " AND "
        + ledger_row_sql(*DASHBOARD_LOGIN_MIGRATION)
        + ")"
    )


IRRIGATION_TABLES_MIGRATION = (
    283,
    "283-irrigation-tables.sql",
    "0b07a5042edc29cb90da4c800da6514e47517240d765c0da0217b68cc7fe92c2",
)
# 283's grants reach 282's dashboard duty on relations the ingestor reads, so its
# ordinary seals hash that duty's production OID; the rolled-back native
# qualification, run once 282 is applied, measures all three.
SUCCESSOR_283 = {
    "api": "030df35ca3b0bc8b94de5f394f96c2292674b2df58aea2d46990c53ad3bd1ad7",
    "ingestor": "c03d427cbbf327c8843496c744d718e1fca70c1e6d98963022ac87e308954096",
    "mcp": "6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e",
}


def sealed_sql_283():
    # Keep every 282 predicate and bind the exact 283 runner stamp with the three
    # seals its irrigation grants move.
    predecessor = sealed_sql_282()
    current = seal_profile_sql(282, SUCCESSOR_282)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(283, SUCCESSOR_283), 1)
        + " AND "
        + ledger_row_sql(*IRRIGATION_TABLES_MIGRATION)
        + ")"
    )


DASHBOARD_GRANTS_MIGRATION = (
    284,
    "284-dashboard-runtime-grants.sql",
    "559d8260ec7138a3bec502f7b29f50587f6a752cc5144fa31e15b406c4ddcaf4",
)
# 284's grants move both ordinary seals, which hash the dashboard duty's production
# OID, and leave the MCP seal at 283's; the rolled-back native qualification, run
# once 282 is applied, measures them.
SUCCESSOR_284 = {
    "api": "03d2a7ce5efd2eecee965672175585bad43194b5fea85d99739efe70e01a81a8",
    "ingestor": "c571ff1bdc212ca84815718406aaa6b7242ba9f86a2727d3855168dcd4ed81ad",
    "mcp": SUCCESSOR_283["mcp"],
}


def sealed_sql_284():
    # Keep every 283 predicate and bind the exact 284 runner stamp with the two
    # ordinary seals its dashboard grants move.
    predecessor = sealed_sql_283()
    current = seal_profile_sql(283, SUCCESSOR_283)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(284, SUCCESSOR_284), 1)
        + " AND "
        + ledger_row_sql(*DASHBOARD_GRANTS_MIGRATION)
        + ")"
    )


DAILY_SCORECARD_MIGRATION = (
    285,
    "285-daily-scorecard-view.sql",
    "89db770e96f030d2836fe033dce035e8c244d7b08a52c7f6ac378f5526c9ca77",
)
# One verdify-owned view, granted to the dashboard duty alone: neither digest takes it in, so all
# three seals should stay 284's. These are 284's values as stand-ins until the rolled-back native
# qualification measures 285; its fixture records which they are.
SUCCESSOR_285 = SUCCESSOR_284


def sealed_sql_285():
    # Keep every 284 predicate and bind the exact 285 runner stamp.
    predecessor = sealed_sql_284()
    current = seal_profile_sql(284, SUCCESSOR_284)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(285, SUCCESSOR_285), 1)
        + " AND "
        + ledger_row_sql(*DAILY_SCORECARD_MIGRATION)
        + ")"
    )


LIGHTING_MANUAL_MIGRATION = (
    286,
    "286-lighting-policy-ignores-manual.sql",
    "6bacb419d3aa6d1d16f05c5a1d451f78001db1cedf3315539853f69bf3144eee",
)
# Both lighting policy functions are in the ordinary digest's helper closure, so the
# api and ingestor seals move, as 275's jit change did; the MCP seal should stay 285's.
# The rolled-back native qualification measured them.
SUCCESSOR_286 = {
    "api": "6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c",
    "ingestor": "6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc",
    "mcp": SUCCESSOR_285["mcp"],
}


def sealed_sql_286():
    # Keep every 285 predicate and bind the exact 286 runner stamp with the two
    # ordinary seals its lighting policy replacements move.
    predecessor = sealed_sql_285()
    current = seal_profile_sql(285, SUCCESSOR_285)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(286, SUCCESSOR_286), 1)
        + " AND "
        + ledger_row_sql(*LIGHTING_MANUAL_MIGRATION)
        + ")"
    )


RETIRE_HIGH_WIND_MIGRATION = (
    287,
    "287-retire-high-wind-rule.sql",
    "c95444af3859465b89b6d3659de6aad2b91a00ade552ecfdca3072beec516933",
)
# One forecast_action_rules row is disabled. Data only, and neither digest hashes table rows,
# so all three seals stay 286's. The rolled-back native qualification measured them.
SUCCESSOR_287 = SUCCESSOR_286


def sealed_sql_287():
    # Keep every 286 predicate and bind the exact 287 runner stamp.
    predecessor = sealed_sql_286()
    current = seal_profile_sql(286, SUCCESSOR_286)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(287, SUCCESSOR_287), 1)
        + " AND "
        + ledger_row_sql(*RETIRE_HIGH_WIND_MIGRATION)
        + ")"
    )


RETIRE_IRRIGATION_REGISTRY_MIGRATION = (
    288,
    "288-retire-irrigation-registry-rows.sql",
    "124d2b86136db77eb453fb854485cb2248a994e55dd12711c2b833b9c54245f2",
)
# Six sensor_registry rows go inactive. Data only, and neither digest hashes table rows,
# so all three seals stay 287's. The rolled-back native qualification measures them.
SUCCESSOR_288 = SUCCESSOR_287


def sealed_sql_288():
    # Keep every 287 predicate and bind the exact 288 runner stamp.
    predecessor = sealed_sql_287()
    current = seal_profile_sql(287, SUCCESSOR_287)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(288, SUCCESSOR_288), 1)
        + " AND "
        + ledger_row_sql(*RETIRE_IRRIGATION_REGISTRY_MIGRATION)
        + ")"
    )


ALERT_SILENCE_MIGRATION = (
    289,
    "289-alert-silences.sql",
    "e9173348a9476a69b6ca7285cdfd1e14820ac00af373fa37f6b0e32104314475",
)
# alert_silence's SELECT for the ingestor duty puts the table in both ordinary digests, so the api
# and ingestor seals move; the MCP digest takes in no ingestor entry, so its seal stays 288's. These
# are a read-only projection on production at exact288 until the rolled-back native qualification
# measures them.
SUCCESSOR_289 = {
    "api": "a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e",
    "ingestor": "b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011",
    "mcp": SUCCESSOR_288["mcp"],
}


def sealed_sql_289():
    # Keep every 288 predicate and bind the exact 289 runner stamp with the two
    # ordinary seals its grant moves.
    predecessor = sealed_sql_288()
    current = seal_profile_sql(288, SUCCESSOR_288)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(289, SUCCESSOR_289), 1)
        + " AND "
        + ledger_row_sql(*ALERT_SILENCE_MIGRATION)
        + ")"
    )


BAND_COMPLIANCE_MIGRATION = (
    290,
    "290-band-compliance.sql",
    "557d8d03a61a284e05e1b9bc26db7e3748898531ea7b80f0a6b4e458c922e6be",
)
# daily_band_compliance, its functions and the scorecard view are verdify's; the one EXECUTE goes to the
# matview-refresh duty in its own schema, which neither ordinary login nor an MCP role can use. All three
# seals stay 289's. The rolled-back native qualification measures them.
SUCCESSOR_290 = SUCCESSOR_289


def sealed_sql_290():
    # Keep every 289 predicate and bind the exact 290 runner stamp.
    predecessor = sealed_sql_289()
    current = seal_profile_sql(289, SUCCESSOR_289)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(290, SUCCESSOR_290), 1)
        + " AND "
        + ledger_row_sql(*BAND_COMPLIANCE_MIGRATION)
        + ")"
    )


ENERGY_DAILY_MIGRATION = (
    291,
    "291-energy-daily-load-magnitude.sql",
    "afd21ce741451d2530f78e94fb82bf1f7a3159476350cd6eb527a1543ae664ae",
)
# v_energy_daily's new text and two appended columns are in its relation entry, which both ordinary
# digests take in, so the api and ingestor seals move; no MCP role or PUBLIC holds an entry on the view,
# so the MCP seal stays 290's. The rolled-back native qualification measures them.
SUCCESSOR_291 = {
    "api": "b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76",
    "ingestor": "2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806",
    "mcp": SUCCESSOR_290["mcp"],
}


def sealed_sql_291():
    # Keep every 290 predicate and bind the exact 291 runner stamp with the two
    # ordinary seals the view replacement moves.
    predecessor = sealed_sql_290()
    current = seal_profile_sql(290, SUCCESSOR_290)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(291, SUCCESSOR_291), 1)
        + " AND "
        + ledger_row_sql(*ENERGY_DAILY_MIGRATION)
        + ")"
    )


CROP_PLANTINGS_MIGRATION = (
    293,
    "293-crop-plantings.sql",
    "fe591976baeb81eb7676b6ff3198a9f370ba6bb7db8f69218c1d5f47ddbd48b7",
)
# 293's layout columns, crop_occupancy and grants move all three seals. It chains from 291 (292 is a gap).
# The rolled-back native qualification on production at 291 measured them
# (tests/fixtures/293-crop-plantings-native-qualification.json).
SUCCESSOR_293 = {
    "api": "f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547",
    "ingestor": "533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e",
    "mcp": "2f5f7ccadf8006a351353c4fce8aec9a59f634b605adf2bcda5e6e61dbe254b9",
}


def sealed_sql_293():
    # Keep every 291 predicate and bind the exact 293 runner stamp with its seals.
    predecessor = sealed_sql_291()
    current = seal_profile_sql(291, SUCCESSOR_291)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(293, SUCCESSOR_293), 1)
        + " AND "
        + ledger_row_sql(*CROP_PLANTINGS_MIGRATION)
        + ")"
    )


CORTEX_PLANNER_MIGRATION = (
    294,
    "294-cortex-planner-shadow-storage.sql",
    "99e1feb22a465bafb7adf5852fec5def47357a5b9cd6bd15314c93c02697bb5d",
)
# #1350: seal-preserving. No object 294 adds carries an api, ingestor, MCP or PUBLIC privilege, so every
# seal keeps 293's value. Chained from 293; 294 and 296 ship in one PR, in that order.
SUCCESSOR_294 = dict(SUCCESSOR_293)


def sealed_sql_294():
    # Keep every 293 predicate and bind the exact 294 runner stamp.
    predecessor = sealed_sql_293()
    current = seal_profile_sql(293, SUCCESSOR_293)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(294, SUCCESSOR_294), 1)
        + " AND "
        + ledger_row_sql(*CORTEX_PLANNER_MIGRATION)
        + ")"
    )


KNOWLEDGE_EMBEDDINGS_MIGRATION = (
    296,
    "296-knowledge-embeddings-cortex.sql",
    "aec163e31534e28d7ffd2e5b4bc0402819c8096c70d1d5ab28a804dec795d40d",
)
# #1351: knowledge_embeddings (halfvec 2560, Cortex embed.main) and its search function, SELECT and
# EXECUTE for the MCP duty only. The MCP digest takes in both entries, so the MCP seal moves; neither
# object carries an api, ingestor or PUBLIC entry, so both ordinary seals stay 294's. Chained from 294.
# The rolled-back native qualification measured the MCP seal
# (tests/fixtures/296-knowledge-embeddings-native-qualification.json).
SUCCESSOR_296 = {**SUCCESSOR_294, "mcp": "984604e2c5e99ec801a752792373b53b1e817e5667b10ae35798da5c88eb0ef9"}


def sealed_sql_296():
    # Keep every 294 predicate and bind the exact 296 runner stamp with the MCP seal its grants move.
    predecessor = sealed_sql_294()
    current = seal_profile_sql(294, SUCCESSOR_294)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(296, SUCCESSOR_296), 1)
        + " AND "
        + ledger_row_sql(*KNOWLEDGE_EMBEDDINGS_MIGRATION)
        + ")"
    )


GREENHOUSE_BRIEF_MIGRATION = (
    301,
    "301-greenhouse-brief.sql",
    "24945842499e5d907d2c49ef4cbf4e57ada2f29b297c9744edd055421f7d0e1d",
)
# greenhouse_brief is written by the dashboard duty and read by the MCP duty; neither ordinary login reaches it,
# so the api and ingestor seals stay 296's and the MCP seal moves. Chained from 296 (#1388); the rolled-back
# native qualification measured the MCP seal (tests/fixtures/301-greenhouse-brief-native-qualification.json).
SUCCESSOR_301 = {**SUCCESSOR_296, "mcp": "af3f7ef2493b94b11e9e7c3f5ead6bf61c8542e477d26da117f3930b0530d873"}


def sealed_sql_301():
    # Keep every 296 predicate and bind the exact 301 runner stamp with the MCP seal its grant moves.
    predecessor = sealed_sql_296()
    current = seal_profile_sql(296, SUCCESSOR_296)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(301, SUCCESSOR_301), 1)
        + " AND "
        + ledger_row_sql(*GREENHOUSE_BRIEF_MIGRATION)
        + ")"
    )


PLANNER_SECTION_MIGRATION = (
    302,
    "302-planner-section-reads.sql",
    "a18aeb0588b5f4e56b7a78ce3dea7c592873b9fae54f0701c45f6d9bbbd8f3bf",
)
# The four Planner-section read views are verdify's, with SELECT for the dashboard duty only, which neither
# ordinary login nor an MCP role holds. All three seals stay 301's; the rolled-back native qualification
# confirmed it.
SUCCESSOR_302 = SUCCESSOR_301


def sealed_sql_302():
    # Keep every 301 predicate and bind the exact 302 runner stamp.
    predecessor = sealed_sql_301()
    current = seal_profile_sql(301, SUCCESSOR_301)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(302, SUCCESSOR_302), 1)
        + " AND "
        + ledger_row_sql(*PLANNER_SECTION_MIGRATION)
        + ")"
    )


SOIL_TARGETS_MIGRATION = (
    303,
    "303-soil-targets-configurable.sql",
    "fa983725905c01373290a490cd557fda86841a3c84cdee0f965513f924a3af97",
)
# Soil-moisture targets become configurable (#1345): new columns on a table 217 protects by name and the ingestor
# login reads, and SELECT plus threshold UPDATE for the dashboard and MCP duties, so all three seals move. The
# rolled-back native qualification measured them (tests/fixtures/303-soil-targets-native-qualification.json).
SUCCESSOR_303 = {
    **SUCCESSOR_302,
    "api": "42091d8bc52adfff17ef589ca7d71ac3a97fe37af4460b551a938f313635bb8c",
    "ingestor": "c7ad6a83e598b8f6da9ebd0086c008b13cfa2812647516ecdf8a7d1b32331940",
    "mcp": "2c6a1cbe628ea66fafe30784189f6c9ba7c33983792a46cd7383d7dae67dc245",
}


def sealed_sql_303():
    # Keep every 302 predicate and bind the exact 303 runner stamp with the three seals it moves.
    predecessor = sealed_sql_302()
    current = seal_profile_sql(302, SUCCESSOR_302)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(303, SUCCESSOR_303), 1)
        + " AND "
        + ledger_row_sql(*SOIL_TARGETS_MIGRATION)
        + ")"
    )


WATER_DAY_MIGRATION = (
    304,
    "304-water-day-local-end.sql",
    "2abfdb7625c5ee53a58255e94d47cdb4aa669ac07bafe171a709c103b63d1485",
)
# v_water_meter_daily keeps its columns, owner and ACL (the dashboard duty's SELECT only), which neither ordinary
# login nor an MCP role holds; v_water_daily, which the ingestor reads, keeps its text. All three seals stay 302's.
SUCCESSOR_304 = SUCCESSOR_302


def sealed_sql_304():
    # Keep every 302 predicate and bind the exact 304 runner stamp.
    predecessor = sealed_sql_302()
    current = seal_profile_sql(302, SUCCESSOR_302)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(304, SUCCESSOR_304), 1)
        + " AND "
        + ledger_row_sql(*WATER_DAY_MIGRATION)
        + ")"
    )


BAND_DRYOUT_MIGRATION = (
    305,
    "305-band-nightly-dryout.sql",
    "352a37ae666996e2055cccad70ae4c16cb7de310196ef802f0d806c0d8829ef5",
)
# daily_band_compliance keeps no ACL entry and fn_band_dryout_night is verdify's with PUBLIC's EXECUTE revoked,
# reached only through the refresh duty's entry point. All three seals stay 304's.
SUCCESSOR_305 = SUCCESSOR_304


def sealed_sql_305():
    # Keep every 304 predicate and bind the exact 305 runner stamp.
    predecessor = sealed_sql_304()
    current = seal_profile_sql(304, SUCCESSOR_304)
    assert predecessor.count(current) == 1
    return (
        "("
        + predecessor.replace(current, seal_profile_sql(305, SUCCESSOR_305), 1)
        + " AND "
        + ledger_row_sql(*BAND_DRYOUT_MIGRATION)
        + ")"
    )


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
