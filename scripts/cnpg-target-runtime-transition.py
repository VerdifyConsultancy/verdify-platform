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
VERSION = "cnpg-native-runtime-transition-v2"
DATABASE = "verdify_rehearsal"
SERVER = 160013
TABLE = "public.cnpg_qualified_runtime_receipts"
PHYSICAL_TABLE = "public.cnpg_physical_runtime_receipts"
PHYSICAL_TARGETS = ("verdify-cnpg-pitr-a", "verdify-cnpg-pitr-b")


def profile(physical_target=None):
    c0.require(physical_target is None or physical_target in PHYSICAL_TARGETS, "unsupported physical target")
    return (physical_target, PHYSICAL_TABLE) if physical_target else (operator.CLUSTER, TABLE)


LOGINS = (*c0.boundary.LOGINS, "verdify_mcp_runtime_login")
MCP_ATTEST_SOURCE = ROOT / "db/migrations/259-mcp-ordinary-runtime-boundary.sql"
MCP_ATTEST_SHA = "5eb45e7264eb28d05aa30acab76bd571d7a5e7466f51b9a877fcea0beab13054"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def literal(value):
    return transaction.literal(value)


def target_receipt_shape(physical_target=None):
    _, table = profile(physical_target)
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
      FROM pg_class c WHERE c.oid=to_regclass('{table}')),false)
      AND NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('{table}'))
      AND NOT EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=to_regclass('{table}'))
      AND NOT EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=to_regclass('{table}'))
      AND NOT EXISTS(SELECT 1 FROM pg_inherits WHERE inhrelid=to_regclass('{table}') OR inhparent=to_regclass('{table}'))
      AND NOT EXISTS(SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid,
        LATERAL aclexplode(a.attacl) x WHERE c.oid=to_regclass('{table}'))
      AND NOT EXISTS(SELECT 1 FROM pg_class c,
        LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.oid=to_regclass('{table}') AND x.grantee<>c.relowner)
      AND (SELECT array_agg(attname||':'||format_type(atttypid,atttypmod)||':'||attnotnull::text ORDER BY attnum)
        FROM pg_attribute WHERE attrelid=to_regclass('{table}') AND attnum>0 AND NOT attisdropped)
        =ARRAY['login_name:text:true','boundary_sha256:bytea:true','qualification_sha256:text:true']::text[]
      AND NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('{table}')
        AND attnum>0 AND (attisdropped OR attgenerated<>'' OR attidentity<>''))
      AND NOT EXISTS(SELECT 1 FROM pg_attrdef WHERE adrelid=to_regclass('{table}'))
      AND (SELECT array_agg(pg_get_constraintdef(oid) ORDER BY conname) FROM pg_constraint
         WHERE conrelid=to_regclass('{table}'))={expected_checks}
      AND NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass('{table}')
        AND (NOT convalidated OR condeferrable OR condeferred))
      AND (SELECT count(*)=1 AND bool_and(indisprimary AND indisunique AND indisvalid AND indisready
        AND indexprs IS NULL AND indpred IS NULL AND indnatts=1 AND indnkeyatts=1)
        FROM pg_index WHERE indrelid=to_regclass('{table}'))"""


def target_body(original, mcp=False, bootstrap_grantor_profile=False, *, physical_target=None):
    cluster, table = profile(physical_target)
    bootstrap_guard = (
        " OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE oid=10 AND rolname='postgres' AND rolsuper)"
        if bootstrap_grantor_profile
        else ""
    )
    guard = f"""\n    IF current_database()<>'{DATABASE}' OR current_setting('server_version_num')::int<>{SERVER}
       OR current_setting('cluster_name')<>'{cluster}' OR pg_is_in_recovery()
       OR current_user<>'verdify' OR NOT coalesce(({target_receipt_shape(physical_target)}),false){bootstrap_guard} THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM {table})<>3
       OR (SELECT array_agg(login_name ORDER BY login_name) FROM {table})
          IS DISTINCT FROM ARRAY['verdify_api_runtime_login','verdify_ingestor_runtime_login','verdify_mcp_runtime_login']::text[]
       OR NOT (SELECT count(DISTINCT qualification_sha256)=1
          AND bool_and(qualification_sha256 ~ '^[0-9a-f]{{64}}$') FROM {table}) THEN
       RETURN false;
    END IF;\n"""
    body = original.replace("\nBEGIN\n", "\nBEGIN\n" + guard, 1)
    if mcp:
        body = body.replace(
            "public.mcp_runtime_boundary_receipt r WHERE r.singleton", f"{table} r WHERE r.login_name=session_user"
        )
    else:
        body = body.replace("public.runtime_ordinary_login_attestation_receipts receipt", f"{table} receipt")
    c0.require(body != original and body.count(table) >= 2, "unexpected attester source shape")
    return body


def classify_ddl(sql):
    stripped = rollback_safety.strip_sql_noise(sql)
    reasons = [name for name, pattern in rollback_safety._COMMIT_FORCING if pattern.search(stripped)]
    c0.require(not reasons, "nontransactional target DDL refused before rollback qualification")
    return {"self_committing": False, "reasons": []}


def ddl(bootstrap_grantor_profile=False, *, physical_target=None):
    _, receipt_table = profile(physical_target)
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
        body = target_body(original, is_mcp, bootstrap_grantor_profile, physical_target=physical_target)
        bodies[name] = body
        statements.append("CREATE OR REPLACE FUNCTION public." + name + "()" + suffix.replace(original, body, 1))
    table = f"""CREATE TABLE {receipt_table} (
      login_name text PRIMARY KEY CHECK(login_name IN ('{LOGINS[0]}','{LOGINS[1]}','{LOGINS[2]}')),
      boundary_sha256 bytea NOT NULL CHECK(octet_length(boundary_sha256)=32),
      qualification_sha256 text NOT NULL CHECK(qualification_sha256 ~ '^[0-9a-f]{{64}}$')
    );
    ALTER TABLE {receipt_table} OWNER TO verdify;
    REVOKE ALL ON TABLE {receipt_table} FROM PUBLIC;\n"""
    result = table + "\n".join(statements)
    # Reuse the owning rollback-safety classifier before any outer transaction.
    classify_ddl(result)
    return result, bodies


def witness_select(target=None, *, physical_target=None):
    cluster, _ = profile(physical_target)
    c0.require(c0.VERSION == "cnpg-c0-logical-recovery-witness-v3", "typed full identity v3 prerequisite missing")
    # Use the same independently source-pinned witness, including executable
    # digest guards; its attester definitions legitimately differ after DDL.
    sql = c0.emit_sql(
        target=True,
        bootstrap_grantor_profile=bool(target and target.get("bootstrap_grantor_profile")),
        cluster_name=operator.CLUSTER,
    )
    if physical_target:
        old_guard = "OR current_setting('cluster_name') <> '" + operator.CLUSTER + "'"
        c0.require(sql.count(old_guard) == 1, "unexpected exact witness identity guard")
        sql = sql.replace(old_guard, "OR current_setting('cluster_name') <> '" + cluster + "'", 1)
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
    c0.require(re.fullmatch(re.escape(operator.CLUSTER) + r"-[1-9]\d*", binding["pod"]), "wrong exact primary")
    c0.require(binding["operand_digest"] == operator.DIGEST, "unqualified target image")
    return result


def validate_post(before, after, *, physical_target=None):
    profile(physical_target)
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
    validate_raw_delta(before, after, physical_target=physical_target)
    for field in ["portable_catalog", "raw_portable_catalog_v2"]:
        validate_catalog_delta(before[field], after[field], physical_target=physical_target)


def validate_catalog_delta(before, after, *, physical_target=None):
    _, receipt_table = profile(physical_target)
    left = {(r[0], r[1]): r[2] for r in before}
    right = {(r[0], r[1]): r[2] for r in after}
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
    expected = {
        ("relation", receipt_table),
        ("index", receipt_table + "_pkey"),
        ("column", receipt_table + "_pkey.login_name"),
    }
    expected |= {("column", receipt_table + "." + c) for c in ["login_name", "boundary_sha256", "qualification_sha256"]}
    expected |= {
        ("constraint", receipt_table + "." + c)
        for c in [
            receipt_table.split(".")[1] + "_pkey",
            receipt_table.split(".")[1] + "_login_name_check",
            receipt_table.split(".")[1] + "_boundary_sha256_check",
            receipt_table.split(".")[1] + "_qualification_sha256_check",
        ]
    }
    c0.require(added == expected, "uncontrolled new target object")


def checked_qualification(before, record, *, retained_session=False):
    c0.require(
        isinstance(record, dict) and set(record) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness"},
        "unexpected qualification shape",
    )
    c0.require(
        record["version"] == ("cnpg-native-retained-session-transition-v1" if retained_session else VERSION)
        and record["mode"] == ("savepoint-rollback-qualification" if retained_session else "rollback-qualification"),
        "unqualified native result mode",
    )
    c0.require(
        record["ddl_sha256"] == digest(ddl(bool(before.get("bootstrap_grantor_profile")))[0].encode()),
        "changed source DDL qualification",
    )
    c0.require(record["before_witness"] == before, "qualification target predecessor mismatch")
    validate_post(before, record["post_witness"])
    return record["post_witness"]


def read_transition_record(path, *, version, mode):
    """Read only a closed native two-witness transition envelope.

    Real rollback/install output contains two roughly 35 MB witnesses. Keep the
    existing individual-witness limit unchanged; this separate record reader
    permits exactly two independently bounded witnesses and fixed metadata.
    Reading does not replace the caller's native qualification/install guards.
    """
    c0.require(version in {VERSION, "cnpg-physical-runtime-transition-v1"}, "unsupported transition record version")
    c0.require(mode in {"rollback-qualification", "install"}, "unsupported transition record mode")
    c0.require(path.is_file() and not path.is_symlink(), "regular transition record required")
    maximum = 2 * c0.WITNESS_MAX_BYTES + 1024
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    c0.require(len(raw) <= maximum, "transition record exceeds two-witness bound")
    record = json.loads(raw, object_pairs_hook=c0.boundary._pairs)
    c0.require(
        isinstance(record, dict) and set(record) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness"},
        "unexpected transition record shape",
    )
    c0.require(record["version"] == version and record["mode"] == mode, "transition record version/mode mismatch")
    c0.require(transaction.is_hash(record["ddl_sha256"]), "invalid transition record DDL hash")
    for key in ("before_witness", "post_witness"):
        witness = record[key]
        c0.require(isinstance(witness, dict), "transition record witness must be an object")
        size = len(json.dumps(witness, ensure_ascii=False, separators=(",", ":")).encode())
        c0.require(size <= c0.WITNESS_MAX_BYTES, "transition record individual witness exceeds bound")
    return record, digest(raw)


def raw_additions(*, physical_target=None):
    _, receipt_table = profile(physical_target)
    table_name = receipt_table.split(".")[1]
    return {
        "relations": [receipt_table],
        "indexes": [receipt_table + "_pkey"],
        "constraints": [
            receipt_table + "." + n
            for n in [
                table_name + "_pkey",
                table_name + "_login_name_check",
                table_name + "_boundary_sha256_check",
                table_name + "_qualification_sha256_check",
            ]
        ],
        "triggers": [],
    }


def validate_raw_delta(before, after, *, physical_target=None):
    for field, allowed in raw_additions(physical_target=physical_target).items():
        key = "raw_identity" if field == "triggers" else "identity"
        old = {f[key]: f for f in before["portability_native_facts"][field]}
        new = {f[key]: f for f in after["portability_native_facts"][field]}
        c0.require(
            len(old) == len(before["portability_native_facts"][field])
            and len(new) == len(after["portability_native_facts"][field]),
            "ambiguous raw native objects",
        )
        c0.require(
            not (set(old) & set(allowed)) and set(new) - set(old) == set(allowed), "uncontrolled raw native additions"
        )
        c0.require(all(new.get(k) == v for k, v in old.items()), "pre-existing raw native facts changed")


def validate_installed_post(before, qualified, installed, *, physical_target=None):
    """Compare reviewed rollback and real install; only enumerated new OIDs differ."""
    validate_post(before, installed, physical_target=physical_target)
    c0.require(
        {k: v for k, v in qualified.items() if k != "portability_native_facts"}
        == {k: v for k, v in installed.items() if k != "portability_native_facts"},
        "installed semantic/native witness differs from reviewed qualification",
    )
    additions = raw_additions(physical_target=physical_target)
    slots = {
        "relations": {"oid", "relfilenode", "reltype", "reltoastrelid", "relfrozenxid"},
        "indexes": {"oid", "relfilenode"},
        "constraints": {"oid", "conrelid", "conindid"},
        "triggers": set(),
    }
    for field, allowed in additions.items():

        def selected(witness):
            values = []
            for fact in witness["portability_native_facts"][field]:
                key = "raw_identity" if field == "triggers" else "identity"
                if fact[key] not in allowed:
                    continue
                value = dict(fact)
                value["native"] = {k: v for k, v in fact["native"].items() if k not in slots[field]}
                if field == "indexes":
                    value["index"] = {k: v for k, v in fact["index"].items() if k not in ("indexrelid", "indrelid")}
                values.append(value)
            return sorted(values, key=lambda f: f["identity"])

        c0.require(selected(qualified) == selected(installed), "installed new raw object shape differs from review")
    _, receipt_table = profile(physical_target)
    facts = installed["portability_native_facts"]
    relation = next(f["native"] for f in facts["relations"] if f["identity"] == receipt_table)
    index = next(f for f in facts["indexes"] if f["identity"] == receipt_table + "_pkey")
    c0.require(
        relation["relfilenode"] == relation["oid"]
        and int(relation["reltype"]) > 0
        and int(relation["reltoastrelid"]) > 0
        and index["native"]["relfilenode"] == index["native"]["oid"]
        and index["index"]["indexrelid"] == index["native"]["oid"]
        and index["index"]["indrelid"] == relation["oid"],
        "installed new relation/index OID binding drift",
    )
    for fact in facts["constraints"]:
        if fact["identity"] in additions["constraints"]:
            native = fact["native"]
            c0.require(
                native["conrelid"] == relation["oid"]
                and native["conindid"] == (index["native"]["oid"] if native["contype"] == "p" else "0"),
                "installed new constraint OID binding drift",
            )
    return installed


def raw_facts_guard_sql(reviewed, *, physical_target=None):
    _, receipt_table = profile(physical_target)
    table_name = receipt_table.split(".")[1]
    # Executed in the same DDL transaction, before any COMMIT. Existing facts
    # remain byte-exact. Only source-enumerated new receipt objects may appear.
    result = ""
    for field, allowed in raw_additions(physical_target=physical_target).items():
        key = "raw_identity" if field == "triggers" else "identity"
        identities = "ARRAY[" + ",".join(literal(n) for n in allowed) + "]::text[]"

        def selected(epoch, new):
            op = "=" if new else "<>"
            quantifier = "ANY" if new else "ALL"
            return f"""(SELECT coalesce(jsonb_agg(e ORDER BY e->>{literal(key)}),'[]'::jsonb)
              FROM jsonb_array_elements({epoch}->'portability_native_facts'->{literal(field)}) e
              WHERE e->>{literal(key)} {op} {quantifier}({identities}))"""

        result += f"""
 IF {selected("v_post", False)} IS DISTINCT FROM {selected("v_before", False)}
  OR (SELECT coalesce(jsonb_agg(e->>{literal(key)} ORDER BY e->>{literal(key)}),'[]'::jsonb)
       FROM jsonb_array_elements({selected("v_post", True)}) e)
       IS DISTINCT FROM {literal(json.dumps(sorted(allowed)))}::jsonb THEN
   RAISE EXCEPTION 'CNPG native transition refuses raw existing/addition custody drift';
 END IF;
"""
        if not reviewed or not allowed:
            continue
        # Only OID slots allocated by this exact new table DDL are excluded from
        # rollback-vs-install literal comparison. Definitions, owners, ACLs,
        # storage flags and every other native field remain exact.
        slots = {
            "relations": ["oid", "relfilenode", "reltype", "reltoastrelid", "relfrozenxid"],
            "indexes": ["oid", "relfilenode"],
            "constraints": ["oid", "conrelid", "conindid"],
        }[field]
        subtract = " - ARRAY[" + ",".join(literal(n) for n in slots) + "]::text[]"
        expr = "e || jsonb_build_object('native',(e->'native')" + subtract + ")"
        if field == "indexes":
            expr += " || jsonb_build_object('index',(e->'index') - ARRAY['indexrelid','indrelid']::text[])"

        def comparable(epoch):
            return f"(SELECT jsonb_agg({expr} ORDER BY e->>'identity') FROM jsonb_array_elements({selected(epoch, True)}) e)"

        result += f"""
 IF {comparable("v_post")} IS DISTINCT FROM {comparable("v_reviewed")} THEN
   RAISE EXCEPTION 'CNPG native transition refuses new raw object shape drift: {field}';
 END IF;
"""
    result += f"""
 IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_type rt ON rt.oid=c.reltype AND rt.typrelid=c.oid
    JOIN pg_class toast ON toast.oid=c.reltoastrelid AND toast.relkind='t'
    WHERE n.nspname='public' AND c.relname='{table_name}' AND c.relkind='r'
      AND c.relpersistence='p' AND c.relfilenode=c.oid AND pg_get_userbyid(c.relowner)='verdify'
      AND c.xmin::text::bigint=v_creation_xid
      AND c.relfrozenxid::text::bigint=v_creation_frozenxid)
  OR NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
     WHERE i.indrelid='{receipt_table}'::regclass AND i.indexrelid='{receipt_table}_pkey'::regclass
       AND i.indisprimary AND i.indisunique AND c.relfilenode=c.oid)
  OR EXISTS(SELECT 1 FROM pg_constraint x WHERE x.conrelid='{receipt_table}'::regclass
     AND (x.conname NOT IN ('{table_name}_pkey','{table_name}_login_name_check',
        '{table_name}_boundary_sha256_check','{table_name}_qualification_sha256_check')
       OR (x.contype='p' AND x.conindid<>'{receipt_table}_pkey'::regclass)
       OR (x.contype='c' AND x.conindid<>0))) THEN
   RAISE EXCEPTION 'CNPG native transition refuses new native OID binding drift';
 END IF;
"""
    return result


def transaction_witness_inputs_sql(before, reviewed_post):
    """Keep complete immutable inputs outside the PL/pgSQL compiler body.

    The real v3 witness is about 35 MB. Embedding it as a constant inside DO
    makes PL/pgSQL retain/copy that source while compiling each nested query.
    Transaction-local GUCs carry the same complete bytes in this one UID-bound
    psql session; the atomic block still compares every JSONB field exactly.
    No persistent table, input file, digest-only comparison or catalog filter
    substitutes for the original full witness.
    """
    return "\n".join(
        f"SELECT set_config('verdify.cnpg_transition_expected_{name}',"
        f"{literal(json.dumps(value, separators=(',', ':')))},true) IS NOT NULL;"
        for name, value in [("before", before), ("post", reviewed_post)]
    )


def refresh_custody_sql():
    """Hold relation locks without reading rows or disabling native refresh jobs.

    PostgreSQL rejects LOCK TABLE for materialized views. Planning these fixed
    LIMIT 0 reads takes AccessShareLock, held until the transaction ends, which
    conflicts with the native nonconcurrent refresh's AccessExclusiveLock.
    The subsequent complete literal predecessor check remains mandatory.
    """
    return """DO $refresh_custody$ BEGIN
 IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relname IN ('v_relay_stuck','v_climate_merged')
       AND c.relkind='m') <> 2 THEN
   RAISE EXCEPTION 'CNPG native transition refuses refresh custody object shape';
 END IF;
END $refresh_custody$;
SELECT 1 FROM public.v_relay_stuck LIMIT 0;
SELECT 1 FROM public.v_climate_merged LIMIT 0;"""


def emit_sql(
    target,
    *,
    reviewed_post=None,
    qualification_sha256=None,
    physical_target=None,
    logical_receipts=None,
    retained_session=False,
):
    c0.require(not retained_session or physical_target is None, "retained session is logical-target-only")
    cluster, receipt_table = profile(physical_target)
    c0.require((physical_target is None) == (logical_receipts is None), "physical history custody required")
    c0.checked(target, target=True)
    c0.require(
        target.get("bootstrap_grantor_profile") in (None, c0.BOOTSTRAP_PROFILE), "unsupported target bootstrap profile"
    )
    bootstrap_profile = bool(target.get("bootstrap_grantor_profile"))
    payload, bodies = (
        ddl(bootstrap_profile, physical_target=physical_target) if physical_target else ddl(bootstrap_profile)
    )
    witness = witness_select(target, physical_target=physical_target) if physical_target else witness_select(target)
    select = witness[witness.index("SELECT jsonb_build_object(") :].rstrip().removesuffix(";")
    guards = witness[: witness.index("SELECT jsonb_build_object(")]
    history_guard = ""
    history_lock = ""
    if physical_target:
        history_lock = f"LOCK TABLE {TABLE} IN SHARE MODE;"
        expected = literal(json.dumps(logical_receipts, separators=(",", ":")))
        history_guard = f"""IF (SELECT jsonb_agg(jsonb_build_array(login_name,encode(boundary_sha256,'hex'),qualification_sha256)
            ORDER BY login_name) FROM {TABLE}) IS DISTINCT FROM {expected}::jsonb THEN
            RAISE EXCEPTION 'physical admission refuses copied logical receipt drift';
        END IF;"""
    sql = f"""\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=pg_catalog,pg_temp;
SET LOCAL statement_timeout='120s';
SET LOCAL jit=off;
SET LOCAL lock_timeout='2s';
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
DO $identity$ BEGIN
 IF current_database()<>'{DATABASE}' OR current_setting('server_version_num')::int<>{SERVER}
    OR current_setting('cluster_name')<>'{cluster}' OR inet_client_addr() IS NOT NULL
    OR pg_is_in_recovery() OR current_user<>session_user OR current_user<>'verdify'
    OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())<>'verdify' THEN
   RAISE EXCEPTION 'CNPG native transition refuses target/session';
 END IF;
END $identity$;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,public.mcp_runtime_boundary_receipt IN SHARE MODE;
{history_lock}
{refresh_custody_sql()}
{guards}
{transaction_witness_inputs_sql(target, reviewed_post)}
DO $native_transition$
DECLARE v_original jsonb; v_before jsonb; v_post jsonb;
 v_creation_xid bigint; v_creation_frozenxid bigint;
 v_expected_before jsonb := current_setting('verdify.cnpg_transition_expected_before')::jsonb;
 v_reviewed jsonb := current_setting('verdify.cnpg_transition_expected_post')::jsonb;
BEGIN
 SELECT {original_facts_sql()} INTO v_original;
 {select} INTO v_before;
 IF v_before IS DISTINCT FROM v_expected_before THEN
   RAISE EXCEPTION 'CNPG native transition refuses stale exact target witness';
 END IF;
 {history_guard}
 -- PG16 heap creation initializes relfrozenxid from RecentXmin, not our XID.
 -- Force our creating XID, then capture the active creation snapshot horizon.
 -- An intervening horizon change is refused, never accepted as a range.
 v_creation_xid := (pg_current_xact_id()::text::bigint % 4294967296);
 v_creation_frozenxid := (pg_snapshot_xmin(pg_current_snapshot())::text::bigint % 4294967296);
 {payload}
"""
    if reviewed_post is not None:
        validate_post(target, reviewed_post, physical_target=physical_target)
        c0.require(transaction.is_hash(qualification_sha256), "reviewed qualification hash required")
        values = ",".join(
            f"({literal(login)},decode({literal(reviewed_post['boundaries'][login]['native'])},'hex'),{literal(qualification_sha256)})"
            for login in LOGINS
        )
        sql += f"INSERT INTO {receipt_table} (login_name,boundary_sha256,qualification_sha256) VALUES {values};\n"
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
 {history_guard}
 IF {original_facts_sql()} IS DISTINCT FROM v_original THEN
   RAISE EXCEPTION 'CNPG native transition changed original historical facts';
 END IF;
"""
    sql += raw_facts_guard_sql(reviewed_post is not None, physical_target=physical_target)
    if reviewed_post is not None:
        sql += """
 IF (v_post - 'portability_native_facts') IS DISTINCT FROM (v_reviewed - 'portability_native_facts') THEN
   RAISE EXCEPTION 'CNPG native transition refuses unqualified post-DDL catalog';
 END IF;
"""
    # Transaction-local return channel only. No persistent fact, namespace or
    # receipt is created by rollback qualification; no backdated/native fiction.
    sql += f"""
 PERFORM set_config('verdify.cnpg_transition_result',jsonb_build_object(
   'version','{"cnpg-physical-runtime-transition-v1" if physical_target else VERSION}','mode','{"install" if reviewed_post else "rollback-qualification"}',
   'ddl_sha256','{digest(payload.encode())}','before_witness',v_before,'post_witness',v_post)::text,true);
END $native_transition$;
SELECT current_setting('verdify.cnpg_transition_result');
{"COMMIT;" if reviewed_post else "ROLLBACK;"}
"""
    if retained_session:
        sql = retained_session_sql(sql, payload, install=reviewed_post is not None)
    return sql


def retained_session_sql(sql, payload, *, install):
    """Explicit savepoint mode; caller must hold the same outer backend/locks.

    Default ordinary/physical emitters retain their original transaction bytes.
    A newly allocated child XID is proved from this backend's granted locks,
    rather than accepting any observed tuple XID or a numeric range.
    """
    require = c0.require
    require(sql.count("BEGIN;\n") == 1, "unexpected transaction envelope")
    sql = sql.replace("BEGIN;\n", "SAVEPOINT cnpg_native_phase;\n", 1)
    sql = sql.replace("SET LOCAL statement_timeout='120s';", "SET LOCAL statement_timeout='180s';", 1)
    sql = sql.replace(
        " v_creation_xid bigint; v_creation_frozenxid bigint;",
        " v_creation_xid bigint; v_creation_frozenxid bigint;\n v_owned_xids_before xid[]; v_new_child_xids xid[];",
    )
    capture = " v_creation_frozenxid := (pg_snapshot_xmin(pg_current_snapshot())::text::bigint % 4294967296);"
    require(sql.count(capture) == 1, "unexpected exact creation cutoff")
    sql = sql.replace(
        capture,
        capture
        + """
 SELECT array_agg(transactionid ORDER BY transactionid::text) INTO v_owned_xids_before
 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='transactionid'
   AND mode='ExclusiveLock' AND granted;
 IF v_owned_xids_before IS NULL OR NOT v_creation_xid::text::xid=ANY(v_owned_xids_before) THEN
   RAISE EXCEPTION 'retained admission refuses missing creating top XID';
 END IF;""",
    )
    first_create = payload[: payload.index(");\n") + 3]
    require(sql.count(first_create) == 1, "unexpected exact first receipt CREATE")
    sql = sql.replace(
        first_create,
        first_create
        + """
 SELECT array_agg(transactionid ORDER BY transactionid::text) INTO v_new_child_xids
 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='transactionid'
   AND mode='ExclusiveLock' AND granted AND NOT transactionid=ANY(v_owned_xids_before);
 IF cardinality(v_new_child_xids) IS DISTINCT FROM 1
    OR v_new_child_xids[1]::text::bigint=v_creation_xid
    OR EXISTS(SELECT 1 FROM unnest(v_owned_xids_before) old
       WHERE NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
         AND locktype='transactionid' AND mode='ExclusiveLock' AND granted AND transactionid=old)) THEN
   RAISE EXCEPTION 'retained admission refuses ambiguous creating child XID';
 END IF;
 v_creation_xid := v_new_child_xids[1]::text::bigint;
""",
    )
    sql = sql.replace("'version','" + VERSION + "'", "'version','cnpg-native-retained-session-transition-v1'")
    if not install:
        require(sql.endswith("ROLLBACK;\n"), "unexpected qualification terminal")
        sql = (
            sql.removesuffix("ROLLBACK;\n")
            + "ROLLBACK TO SAVEPOINT cnpg_native_phase;\nRELEASE SAVEPOINT cnpg_native_phase;\n"
        )
        sql = sql.replace("'mode','rollback-qualification'", "'mode','savepoint-rollback-qualification'")
    else:
        require(sql.endswith("COMMIT;\n"), "unexpected installation terminal")
    return sql


def bootstrap_facts_sql():
    # Password-free native bootstrap posture; role/settings/membership parity is
    # independently exported before and after through pg_dumpall.
    return """(SELECT jsonb_build_object('oid',oid::int,'name',rolname,'superuser',rolsuper,
      'inherit',rolinherit,'createrole',rolcreaterole,'createdb',rolcreatedb,'login',rolcanlogin,
      'replication',rolreplication,'bypassrls',rolbypassrls,'connection_limit',rolconnlimit,
      'valid_until',rolvaliduntil) FROM pg_roles WHERE oid=10)"""


def bootstrap_owner_sql(sql, *, physical_target=None):
    """Native peer bootstrap to owner DDL; never ordinary password-auth proof."""
    cluster, _ = profile(physical_target)
    c0.require(
        sql.startswith("\\set ON_ERROR_STOP on\nBEGIN;\n") and sql.rstrip().endswith(("ROLLBACK;", "COMMIT;")),
        "guarded transactional owner SQL required",
    )
    prefix = f"""\\set ON_ERROR_STOP on
DO $bootstrap_peer$ BEGIN
 IF current_database()<>'{DATABASE}' OR current_setting('server_version_num')::int<>{SERVER}
    OR current_setting('cluster_name')<>'{cluster}' OR inet_client_addr() IS NOT NULL
    OR pg_is_in_recovery() OR current_user<>'postgres' OR session_user<>'postgres'
    OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE oid=10 AND rolname='postgres' AND rolsuper)
    OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())<>'verdify' THEN
   RAISE EXCEPTION 'CNPG native transition refuses privileged peer bootstrap';
 END IF;
 PERFORM set_config('verdify.cnpg_bootstrap_before',{bootstrap_facts_sql()}::text,false);
END $bootstrap_peer$;
SET SESSION AUTHORIZATION verdify;
"""
    # Check bootstrap posture inside the owner transaction, before COMMIT. A
    # failure disconnects this one psql session with ON_ERROR_STOP; no hotload or
    # alternate authentication path is attempted.
    final = "COMMIT;" if sql.rstrip().endswith("COMMIT;") else "ROLLBACK;"
    owner = (
        sql.rstrip().removesuffix(final)
        + f"""
DO $bootstrap_custody$ BEGIN
 IF current_user<>'verdify' OR session_user<>'verdify'
    OR {bootstrap_facts_sql()} IS DISTINCT FROM current_setting('verdify.cnpg_bootstrap_before')::jsonb THEN
   RAISE EXCEPTION 'CNPG native transition changed bootstrap/owner custody';
 END IF;
END $bootstrap_custody$;
{final}
RESET SESSION AUTHORIZATION;
DO $bootstrap_return$ BEGIN
 IF current_user<>'postgres' OR session_user<>'postgres'
    OR {bootstrap_facts_sql()} IS DISTINCT FROM current_setting('verdify.cnpg_bootstrap_before')::jsonb THEN
   RAISE EXCEPTION 'CNPG native transition failed privileged session return';
 END IF;
END $bootstrap_return$;
"""
    )
    return prefix + owner


def execute(sql, binding, receipt_dir, source_roles, bootstrap_mapping=None):
    """Same CNPG UID custody as paired import; never create a Pod or credential."""
    os.umask(0o077)
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    args = SimpleNamespace(cluster_uid=binding["cluster_uid"], pod=binding["pod"], pod_uid=binding["pod_uid"])
    before = operator.read_target(args)
    executed_sql = bootstrap_owner_sql(sql)
    receipt = {
        "version": VERSION,
        "binding": binding,
        "sql_sha256": digest(sql.encode()),
        "executed_sql_sha256": digest(executed_sql.encode()),
        "authentication_mode": "privileged-local-peer-bootstrap-owner-session",
        "ordinary_password_authentication": False,
        "before": before,
        "production_endpoint_changed": False,
    }
    (receipt_dir / "executed-owner-sql.sql").write_text(executed_sql)
    (receipt_dir / "custody-before.json").write_text(json.dumps(receipt, indent=2) + "\n")
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; unset PGOPTIONS PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE; exec "$@"'
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
        "postgres",
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
            "-l",
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

    def read_bootstrap(label):
        query = (
            "BEGIN READ ONLY; SELECT jsonb_build_object('session_user',session_user,'current_user',current_user,'bootstrap',"
            + bootstrap_facts_sql()
            + "); COMMIT;"
        )
        result = subprocess.run(command + ["-c", query], capture_output=True, timeout=60)
        (receipt_dir / (label + "-bootstrap.json")).write_bytes(result.stdout)
        (receipt_dir / (label + "-bootstrap.stderr")).write_bytes(result.stderr)
        c0.require(result.returncode == 0, "native peer bootstrap observation failed")
        facts = json.loads(result.stdout)
        c0.require(
            facts["current_user"] == facts["session_user"] == "postgres"
            and facts["bootstrap"]["oid"] == 10
            and facts["bootstrap"]["name"] == "postgres"
            and facts["bootstrap"]["superuser"] is True,
            "unqualified native peer bootstrap",
        )
        return facts

    bootstrap_before = read_bootstrap("before")
    roles_before = read_roles("before")
    with (receipt_dir / "native.stdout").open("wb") as out, (receipt_dir / "native.stderr").open("wb") as err:
        try:
            result = subprocess.run(command, input=executed_sql.encode(), stdout=out, stderr=err, timeout=180)
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
    bootstrap_after = read_bootstrap("after")
    c0.require(bootstrap_before == bootstrap_after, "native bootstrap metadata changed during transition")
    c0.require(roles_before == roles_after, "native role metadata changed during transition")
    (receipt_dir / "execution-result.json").write_text(
        json.dumps(
            {
                "exit_code": result.returncode,
                "after": after,
                "sql_sha256": receipt["sql_sha256"],
                "role_parity": roles_after,
                "bootstrap_before": bootstrap_before,
                "bootstrap_after": bootstrap_after,
                "authentication_mode": receipt["authentication_mode"],
                "ordinary_password_authentication": False,
                "executed_sql_sha256": receipt["executed_sql_sha256"],
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
        record, qualification_sha = read_transition_record(
            args.reviewed_qualification, version=VERSION, mode="rollback-qualification"
        )
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
