"""Prepare an isolated CNPG native runtime transition.

Default mode writes local SQL only. Explicit --execute connects to the guarded
isolated target through its existing Pod; it does not connect to production.

Qualification emits transactional DDL followed by ROLLBACK. Installation requires
separately reviewed exact post-DDL witness bytes/digests. Historical receipts,
ledger and digest implementations are retained. No production profile ships here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("cnpg-c0-restore-qualification")
transaction = load("c0-boundary-transition")
operator = load("cnpg-paired-restore")
role_parity = load("cnpg-restore-role-parity")
rollback_safety = load("check_migration_rollback_safety")
VERSION = "cnpg-native-runtime-transition-v1"
DATABASE = "verdify_rehearsal"
SERVER = 160013
TABLE = "public.cnpg_qualified_runtime_receipts"
LOGINS = (*c0.boundary.LOGINS, "verdify_mcp_runtime_login")
MCP_ATTEST_SOURCE = ROOT / "db/migrations/259-mcp-ordinary-runtime-boundary.sql"
MCP_ATTEST_SHA = "5eb45e7264eb28d05aa30acab76bd571d7a5e7466f51b9a877fcea0beab13054"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def literal(value):
    return transaction.literal(value)


def target_receipt_shape():
    # Checked inside each definer before reading expected digest data. No view,
    # inherited table, executable defaults, policies or extra write grantee.
    checks = [
        "CHECK ((octet_length(boundary_sha256) = 32))",
        "CHECK ((login_name = ANY (ARRAY['verdify_api_runtime_login'::text, 'verdify_ingestor_runtime_login'::text, 'verdify_mcp_runtime_login'::text])))",
        "PRIMARY KEY (login_name)",
        "CHECK ((qualification_sha256 ~ '^[0-9a-f]{64}$'::text))",
    ]
    expected_checks = "ARRAY[" + ",".join(literal(value) for value in checks) + "]::text[]"
    return f"""coalesce((SELECT c.relkind='r' AND c.relpersistence='p'
      AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relispartition
      AND pg_get_userbyid(c.relowner)='verdify'
      FROM pg_class c WHERE c.oid=to_regclass('{TABLE}')),false)
      AND NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('{TABLE}'))
      AND NOT EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=to_regclass('{TABLE}'))
      AND NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('{TABLE}'))
      AND NOT EXISTS(SELECT 1 FROM pg_inherits WHERE inhrelid=to_regclass('{TABLE}') OR inhparent=to_regclass('{TABLE}'))
      AND NOT EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid,
        LATERAL aclexplode(a.attacl) x WHERE c.oid=to_regclass('{TABLE}'))
      AND NOT EXISTS(SELECT 1 FROM pg_class c,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid=to_regclass('{TABLE}') AND x.grantee<>c.relowner)
      AND (SELECT array_agg(attname||':'||format_type(atttypid,atttypmod)||':'||attnotnull::text ORDER BY attnum)
        FROM pg_attribute WHERE attrelid=to_regclass('{TABLE}') AND attnum>0 AND NOT attisdropped)
        =ARRAY['login_name:text:true','boundary_sha256:bytea:true','qualification_sha256:text:true']::text[]
      AND NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('{TABLE}')
        AND attnum>0 AND (attisdropped OR attgenerated<>'' OR attidentity<>''))
      AND NOT EXISTS(SELECT 1 FROM pg_attrdef WHERE adrelid=to_regclass('{TABLE}'))
      AND (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY conname) FROM pg_constraint
         WHERE conrelid=to_regclass('{TABLE}'))={expected_checks}
      AND NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('{TABLE}')
        AND (NOT convalidated OR condeferrable OR condeferred))
      AND (SELECT count(*)=1 AND bool_and(indisprimary AND indisunique AND indisvalid AND indisready
        AND indexprs IS NULL AND indpred IS NULL AND indnatts=1 AND indnkeyatts=1)
        FROM pg_index WHERE indrelid=to_regclass('{TABLE}'))"""


def target_body(original, mcp=False, bootstrap_grantor_profile=False):
    bootstrap_guard = (
        " OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE oid=10 AND rolname='postgres' AND rolsuper)"
        if bootstrap_grantor_profile
        else ""
    )
    guard = f"""\n    IF current_database()<>'{DATABASE}' OR current_setting('server_version_num')::int<>{SERVER}
       OR current_setting('cluster_name')<>'{operator.CLUSTER}' OR pg_is_in_recovery()
       OR current_user<>'verdify' OR NOT coalesce(({target_receipt_shape()}),false){bootstrap_guard} THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM {TABLE})<>3
       OR (SELECT array_agg(login_name ORDER BY login_name) FROM {TABLE})
          IS DISTINCT FROM ARRAY['verdify_api_runtime_login','verdify_ingestor_runtime_login','verdify_mcp_runtime_login']::text[]
       OR NOT (SELECT count(DISTINCT qualification_sha256)=1
          AND bool_and(qualification_sha256 ~ '^[0-9a-f]{{64}}$') FROM {TABLE}) THEN
       RETURN false;
    END IF;\n"""
    body = original.replace("\nBEGIN\n", "\nBEGIN\n" + guard, 1)
    if mcp:
        body = body.replace(
            "public.mcp_runtime_boundary_receipt r WHERE r.singleton", f"{TABLE} r WHERE r.login_name=session_user"
        )
    else:
        body = body.replace("public.runtime_ordinary_login_attestation_receipts receipt", f"{TABLE} receipt")
    c0.require(body != original and body.count(TABLE) >= 2, "unexpected attester source shape")
    return body


def classify_ddl(sql):
    stripped = rollback_safety.strip_sql_noise(sql)
    reasons = [name for name, pattern in rollback_safety._COMMIT_FORCING if pattern.search(stripped)]
    c0.require(not reasons, "nontransactional target DDL refused before rollback qualification")
    return {"self_committing": False, "reasons": []}


def ddl(bootstrap_grantor_profile=False):
    c0.require(digest(c0.boundary.SOURCE.read_bytes()) == c0.boundary.SOURCE_SHA256, "ordinary source drift")
    c0.require(digest(MCP_ATTEST_SOURCE.read_bytes()) == MCP_ATTEST_SHA, "MCP attester source drift")
    ordinary = (
        c0.boundary.SOURCE.read_text()
        .split("CREATE OR REPLACE FUNCTION public.fn_runtime_attest_ordinary_login()", 1)[1]
        .split("\nDO $attestation_objects$", 1)[0]
    )
    mcp = (
        MCP_ATTEST_SOURCE.read_text()
        .split("CREATE FUNCTION public.fn_mcp_runtime_attest_ordinary_login()", 1)[1]
        .split("REVOKE ALL ON FUNCTION public.fn_mcp_runtime_attest_ordinary_login()", 1)[0]
    )
    statements = []
    bodies = {}
    for name, suffix, is_mcp in [
        ("fn_runtime_attest_ordinary_login", ordinary, False),
        ("fn_mcp_runtime_attest_ordinary_login", mcp, True),
    ]:
        original = suffix.split("AS $body$", 1)[1].split("$body$;", 1)[0]
        body = target_body(original, is_mcp, bootstrap_grantor_profile)
        bodies[name] = body
        statements.append("CREATE OR REPLACE FUNCTION public." + name + "()" + suffix.replace(original, body, 1))
    table = f"""CREATE TABLE {TABLE} (
      login_name text PRIMARY KEY CHECK(login_name IN ('{LOGINS[0]}','{LOGINS[1]}','{LOGINS[2]}')),
      boundary_sha256 bytea NOT NULL CHECK(octet_length(boundary_sha256)=32),
      qualification_sha256 text NOT NULL CHECK(qualification_sha256 ~ '^[0-9a-f]{{64}}$')
    );
    ALTER TABLE {TABLE} OWNER TO verdify;
    REVOKE ALL ON TABLE {TABLE} FROM PUBLIC;\n"""
    result = table + "\n".join(statements)
    # Reuse the owning rollback-safety classifier before any outer transaction.
    classify_ddl(result)
    return result, bodies


def witness_select(target=None):
    c0.require(c0.VERSION == "cnpg-c0-logical-recovery-witness-v2", "full identity v2 prerequisite missing")
    # Use the same independently source-pinned witness, including executable
    # digest guards; its attester definitions legitimately differ after DDL.
    sql = c0.emit_sql(target=True, bootstrap_grantor_profile=bool(target and target.get("bootstrap_grantor_profile")))
    return sql[sql.index("DO $guard$") : sql.rindex("COMMIT;")].strip()


def original_facts_sql():
    return """jsonb_build_object(
      'ledger',(SELECT jsonb_agg(to_jsonb(r) ORDER BY source,filename) FROM public.schema_migrations r),
      'ordinary',(SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts r),
      'mcp',(SELECT jsonb_agg(to_jsonb(r)) FROM public.mcp_runtime_boundary_receipt r))"""


def validate_inputs(source, target, binding):
    result = c0.compare(source, target)
    c0.require(target["database"] == DATABASE and target["server"] == SERVER, "wrong exact target")
    c0.require(set(binding) == {"cluster_uid", "pod", "pod_uid", "operand_digest"}, "unexpected target binding")
    import re

    for key in ["cluster_uid", "pod_uid"]:
        c0.require(
            isinstance(binding[key], str)
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", binding[key]),
            "invalid target UID",
        )
    c0.require(re.fullmatch(r"verdify-cnpg-rehearsal-[1-9]\d*", binding["pod"]), "wrong exact primary")
    c0.require(binding["operand_digest"] == operator.DIGEST, "unqualified target image")
    return result


def validate_post(before, after):
    # The original qualifier checks executable native digest functions unchanged,
    # but does not claim post-DDL semantic equality with the old source catalog.
    c0.checked(after, target=True)
    for field in [
        "database",
        "server",
        "roles",
        "namespaces",
        "database_owner",
        "database_acl",
        "ledger",
        "seals",
        "bootstrap_grantor_profile",
        "bootstrap_identity",
    ]:
        c0.require(before.get(field) == after.get(field), "post-DDL historical/identity drift")
    for login in LOGINS:
        c0.require(
            sorted(e for e in before["boundaries"][login]["raw_entries"] if e.startswith("member|"))
            == sorted(e for e in after["boundaries"][login]["raw_entries"] if e.startswith("member|")),
            "post-DDL native membership drift",
        )
    left = {(r[0], r[1]): r[2] for r in before["portable_catalog"]}
    right = {(r[0], r[1]): r[2] for r in after["portable_catalog"]}
    changed = {k for k in left.keys() & right.keys() if left[k] != right[k]}
    removed = left.keys() - right.keys()
    added = right.keys() - left.keys()
    c0.require(
        not removed
        and changed
        == {
            ("function", "public.fn_runtime_attest_ordinary_login()"),
            ("function", "public.fn_mcp_runtime_attest_ordinary_login()"),
        },
        "uncontrolled replaced object",
    )
    expected = {("relation", TABLE), ("index", TABLE + "_pkey"), ("column", TABLE + "_pkey.login_name")}
    expected |= {("column", TABLE + "." + c) for c in ["login_name", "boundary_sha256", "qualification_sha256"]}
    expected |= {
        ("constraint", TABLE + "." + c)
        for c in [
            "cnpg_qualified_runtime_receipts_pkey",
            "cnpg_qualified_runtime_receipts_login_name_check",
            "cnpg_qualified_runtime_receipts_boundary_sha256_check",
            "cnpg_qualified_runtime_receipts_qualification_sha256_check",
        ]
    }
    c0.require(added == expected, "uncontrolled new target object")


def checked_qualification(before, record):
    c0.require(
        isinstance(record, dict) and set(record) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness"},
        "unexpected qualification shape",
    )
    c0.require(
        record["version"] == VERSION and record["mode"] == "rollback-qualification", "unqualified native result mode"
    )
    c0.require(
        record["ddl_sha256"] == digest(ddl(bool(before.get("bootstrap_grantor_profile")))[0].encode()),
        "changed source DDL qualification",
    )
    c0.require(record["before_witness"] == before, "qualification target predecessor mismatch")
    validate_post(before, record["post_witness"])
    return record["post_witness"]


def emit_sql(target, *, reviewed_post=None, qualification_sha256=None):
    c0.checked(target, target=True)
    c0.require(
        target.get("bootstrap_grantor_profile") in (None, c0.BOOTSTRAP_PROFILE), "unsupported target bootstrap profile"
    )
    payload, bodies = ddl(bool(target.get("bootstrap_grantor_profile")))
    before = json.dumps(target, separators=(",", ":"))
    witness = witness_select(target)
    select = witness[witness.index("SELECT jsonb_build_object(") :].rstrip().removesuffix(";")
    guards = witness[: witness.index("SELECT jsonb_build_object(")]
    sql = f"""\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=pg_catalog,pg_temp;
SET LOCAL statement_timeout='120s';
SET LOCAL lock_timeout='2s';
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
DO $identity$ BEGIN
 IF current_database()<>'{DATABASE}' OR current_setting('server_version_num')::int<>{SERVER}
    OR current_setting('cluster_name')<>'{operator.CLUSTER}' OR inet_client_addr() IS NOT NULL
    OR pg_is_in_recovery() OR current_user<>session_user OR current_user<>'verdify'
    OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())<>'verdify' THEN
   RAISE EXCEPTION 'CNPG native transition refuses target/session';
 END IF;
END $identity$;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,public.mcp_runtime_boundary_receipt IN SHARE MODE;
{guards}
DO $native_transition$
DECLARE v_original jsonb; v_before jsonb; v_post jsonb;
BEGIN
 SELECT {original_facts_sql()} INTO v_original;
 {select} INTO v_before;
 IF v_before IS DISTINCT FROM {literal(before)}::jsonb THEN
   RAISE EXCEPTION 'CNPG native transition refuses stale exact target witness';
 END IF;
 {payload}
"""
    if reviewed_post is not None:
        validate_post(target, reviewed_post)
        c0.require(transaction.is_hash(qualification_sha256), "reviewed qualification hash required")
        values = ",".join(
            f"({literal(login)},decode({literal(reviewed_post['boundaries'][login]['native'])},'hex'),{literal(qualification_sha256)})"
            for login in LOGINS
        )
        sql += f"INSERT INTO {TABLE} (login_name,boundary_sha256,qualification_sha256) VALUES {values};\n"
    sql += f"{select} INTO v_post;\n"
    for name, body in bodies.items():
        sql += f"""
 IF NOT coalesce((SELECT p.prosrc={literal(body)} AND p.prosecdef
    AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
    AND l.lanname='plpgsql' AND pg_get_userbyid(p.proowner)='verdify'
    FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid='public.{name}()'::regprocedure),false) THEN
   RAISE EXCEPTION 'CNPG native transition refuses generated attester drift';
 END IF;
"""
    sql += f"""
 IF {original_facts_sql()} IS DISTINCT FROM v_original THEN
   RAISE EXCEPTION 'CNPG native transition changed original historical facts';
 END IF;
"""
    if reviewed_post is not None:
        sql += f"""
 IF v_post IS DISTINCT FROM {literal(json.dumps(reviewed_post, separators=(",", ":")))}::jsonb THEN
   RAISE EXCEPTION 'CNPG native transition refuses unqualified post-DDL catalog';
 END IF;
"""
    # Transaction-local return channel only. No persistent fact, namespace or
    # receipt is created by rollback qualification; no backdated/native fiction.
    sql += f"""
 PERFORM set_config('verdify.cnpg_transition_result',jsonb_build_object(
   'version','{VERSION}','mode','{"install" if reviewed_post else "rollback-qualification"}',
   'ddl_sha256','{digest(payload.encode())}','before_witness',v_before,'post_witness',v_post)::text,true);
END $native_transition$;
SELECT current_setting('verdify.cnpg_transition_result');
{"COMMIT;" if reviewed_post else "ROLLBACK;"}
"""
    return sql


def execute(sql, binding, receipt_dir, source_roles, bootstrap_mapping=None):
    """Same CNPG UID custody as paired import; never create a Pod or credential."""
    os.umask(0o077)
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    args = SimpleNamespace(cluster_uid=binding["cluster_uid"], pod=binding["pod"], pod_uid=binding["pod_uid"])
    before = operator.read_target(args)
    receipt = {
        "version": VERSION,
        "binding": binding,
        "sql_sha256": digest(sql.encode()),
        "before": before,
        "production_endpoint_changed": False,
    }
    (receipt_dir / "custody-before.json").write_text(json.dumps(receipt, indent=2) + "\n")
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; exec "$@"'
    command = operator.kube(
        "exec",
        "-i",
        args.pod,
        "-c",
        "postgres",
        "--",
        "sh",
        "-c",
        guard,
        "uid-guard",
        args.pod_uid,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        "/controller/run",
        "-p",
        "5432",
        "-U",
        "verdify",
        "-d",
        DATABASE,
    )

    def read_roles(label):
        role_command = operator.kube(
            "exec",
            args.pod,
            "-c",
            "postgres",
            "--",
            "sh",
            "-c",
            guard,
            "uid-guard",
            args.pod_uid,
            "pg_dumpall",
            "-h",
            "/controller/run",
            "-p",
            "5432",
            "-U",
            "postgres",
            "--roles-only",
            "--no-role-passwords",
            "--no-comments",
            "--no-security-labels",
        )
        result = subprocess.run(role_command, capture_output=True, timeout=60)
        (receipt_dir / (label + "-roles-private.sql")).write_bytes(result.stdout)
        (receipt_dir / (label + "-roles-private.stderr")).write_bytes(result.stderr)
        c0.require(result.returncode == 0, "native role metadata export failed")
        return role_parity.verify(source_roles, result.stdout.decode(), mapping=bootstrap_mapping)

    roles_before = read_roles("before")
    with (receipt_dir / "native.stdout").open("wb") as out, (receipt_dir / "native.stderr").open("wb") as err:
        try:
            result = subprocess.run(command, input=sql.encode(), stdout=out, stderr=err, timeout=180)
        except subprocess.TimeoutExpired:
            (receipt_dir / "execution-result.json").write_text(
                json.dumps(
                    {
                        "exit_code": None,
                        "outcome": "timeout-unknown",
                        "sql_sha256": receipt["sql_sha256"],
                        "no_retry": True,
                        "rollback_not_inferred": True,
                    },
                    indent=2,
                )
                + "\n"
            )
            raise ValueError("native execution timed out; retained unknown outcome, no retry") from None
    after = operator.read_target(args)
    roles_after = read_roles("after")
    c0.require(roles_before == roles_after, "native role metadata changed during transition")
    (receipt_dir / "execution-result.json").write_text(
        json.dumps(
            {
                "exit_code": result.returncode,
                "after": after,
                "sql_sha256": receipt["sql_sha256"],
                "role_parity": roles_after,
            },
            indent=2,
        )
        + "\n"
    )
    c0.require(result.returncode == 0, "native transition failed; custody retained, no retry")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ["source", "target", "binding"]:
        parser.add_argument("--" + key, type=Path, required=True)
        parser.add_argument("--" + key + "-sha256", required=True)
    parser.add_argument("--reviewed-qualification", type=Path)
    parser.add_argument("--reviewed-qualification-sha256")
    parser.add_argument("--source-roles", type=Path, required=True)
    parser.add_argument("--source-roles-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="ROOT-only actual isolated target execution")
    parser.add_argument("--receipt-dir", type=Path)
    args = parser.parse_args()
    data = {}
    for key in ["source", "target", "binding"]:
        obj, sha = c0.read_witness(getattr(args, key))
        c0.require(sha == getattr(args, key + "_sha256"), "input custody mismatch")
        data[key] = obj
    validate_inputs(**data)
    c0.require(
        args.source_roles.is_file() and not args.source_roles.is_symlink(), "regular source role custody required"
    )
    role_raw = args.source_roles.read_bytes()
    c0.require(
        len(role_raw) <= 1_000_000 and digest(role_raw) == args.source_roles_sha256, "source role custody mismatch"
    )
    role_lines = role_parity.canonical(role_raw.decode())
    source_role_names = {role_parity.role(line) for line in role_lines if line.startswith("CREATE ROLE ")}
    c0.require(
        source_role_names == {name for name in data["source"]["roles"].values() if not name.startswith("pg_")},
        "role artifact/witness identity mismatch",
    )
    if c0.bootstrap_profile(data["source"], data["target"]):
        role_parity.bootstrap_mapping(data["source"], role_raw.decode(), data["target"]["bootstrap_identity"])
    post = None
    qualification_sha = None
    if args.reviewed_qualification:
        record, qualification_sha = c0.read_witness(args.reviewed_qualification)
        c0.require(qualification_sha == args.reviewed_qualification_sha256, "qualification custody mismatch")
        post = checked_qualification(data["target"], record)
    else:
        c0.require(not args.reviewed_qualification_sha256, "incomplete reviewed qualification")
    sql = emit_sql(data["target"], reviewed_post=post, qualification_sha256=qualification_sha)
    with args.output.open("x") as out:
        out.write(sql)
    if args.execute:
        c0.require(args.receipt_dir is not None, "private execution receipt directory required")
        execute(
            sql,
            data["binding"],
            args.receipt_dir,
            role_raw.decode(),
            bootstrap_mapping=c0.bootstrap_profile(data["source"], data["target"]),
        )
    else:
        c0.require(args.receipt_dir is None, "execution receipts require explicit execution")


if __name__ == "__main__":
    main()
