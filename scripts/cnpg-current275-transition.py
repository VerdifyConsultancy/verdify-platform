"""Closed additive275 bridge and native admission for S2/frozen B.

Emits SQL only. Production is never an admitted target. Migration qualification
always rolls back; installation must bind the complete reviewed post-witness.
Old274 admission receipts remain separate historical custody.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "current275_private_transition", ROOT / "scripts/cnpg-target-runtime-transition.py"
)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
TARGETS = (
    "verdify-cnpg-s2",
    "verdify-cnpg-s2-pitr-b-frozen",
    "verdify-cnpg-s2-pitr-a-frozen",
)
TABLE = "public.cnpg_s2_275_runtime_receipts"
MIGRATION = "db/migrations/275-lighting-minutes-policy-bounded-jit.sql"
MIGRATION_SHA = "f4f8da110236461680f41770bad766a7e28b1a6db1982ecc632e93930a375dc4"
CATALOG_ID = "public.fn_lighting_minutes_policy(timestamp with time zone,text)"
ENTRY_PREFIX = "function|public.fn_lighting_minutes_policy("
CANONICAL_BEFORE = (
    "67bb961f69e72f8d59f8d38dce96201682b1fb8fb40d423071c987b075cf994e",
    "126f95dc75242a126579331fadf711f0c1e8e408d20ec7cd94e3d1e27a0dcf75",
    "7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68",
)
CANONICAL_AFTER = (
    "5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba",
    "8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743",
    CANONICAL_BEFORE[2],
)
NATIVE_BEFORE = (
    "12994609161863146788f841ea7531037749466ae5ecc53ec05021f9bdbc89e7",
    "84d233747ceda403ec0e32790b24ed8213732cb89d58878fd7cdce01b963364d",
    "fc94af5932ea6150f9b326f29fe626499a10ceef2d28821d843838c143c8872b",
)
NATIVE_AFTER = (
    "872775b5806263e232c1fae9bf2b54db4fc1b468c727cf200780f63da88e8455",
    "3390c55017e1c35092bbd617ab067128ab25c33794ac6daeed231860479de03a",
    NATIVE_BEFORE[2],
)
LEDGER_ROW = ["db/migrations", MIGRATION, 275, MIGRATION_SHA, "runner"]
VERSION = "cnpg-current275-additive-transition-v1"
NATIVE274_WITNESS_SHA = "6d896a5d41ac0fb65e3a0115c6b71798a0115c5fd8311a474246f903b49f0d7e"
NATIVE275_BRIDGE_WITNESS_SHA = "7089e3bc3df63d583d4e95c6f350b96e6ce889122bafc1865bfc68c4e5d76d8b"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def remap_migration(raw):
    t.c0.require(sha(raw) == MIGRATION_SHA, "exact reviewed275 source bytes required")
    result = raw.decode()
    # Only the six computed comparisons change; canonical receipt row guards and
    # updates remain byte-for-byte production source, never clone/native seals.
    for native, canonical in [(NATIVE_BEFORE, CANONICAL_BEFORE), (NATIVE_AFTER, CANONICAL_AFTER)]:
        for index, login in enumerate(t.LOGINS):
            expression = (
                "encode(public.fn_mcp_runtime_boundary_digest(),'hex')"
                if index == 2
                else f"encode(public.fn_runtime_ordinary_boundary_digest('{login}'),'hex')"
            )
            pattern = re.escape(expression) + r"(\s+IS DISTINCT FROM )'" + canonical[index] + "'"
            result, count = re.subn(pattern, lambda m: expression + m[1] + "'" + native[index] + "'", result, count=1)
            t.c0.require(count == 1, "275 computed comparison shape changed")
    t.classify_ddl(result)
    return result


def configured(cluster):
    """Private module instance: historical emitter modules/outputs stay intact."""
    t.c0.require(cluster in TARGETS, "unsupported closed275 target")
    t.operator.CLUSTER = TARGETS[0]
    t.PHYSICAL_TARGETS = TARGETS
    t.PHYSICAL_TABLE = TABLE
    return t


def witness_parts(cluster):
    configured(cluster)
    source = t.witness_select({"bootstrap_grantor_profile": t.c0.BOOTSTRAP_PROFILE}, physical_target=cluster)
    split = source.index("SELECT jsonb_build_object(")
    return source[:split], source[split:].rstrip().removesuffix(";")


def checked_before(before):
    t.c0.require(
        sha((json.dumps(before, indent=2) + "\n").encode()) == NATIVE274_WITNESS_SHA,
        "only the complete sealed native274 witness is admitted",
    )
    t.c0.checked(before, target=True)
    t.c0.require(
        max(row[2] for row in before["ledger"] if row[0] == "db/migrations") == 274, "exact274 predecessor required"
    )
    t.c0.require(
        before["seals"]
        == {"ordinary": [[t.LOGINS[i], CANONICAL_BEFORE[i]] for i in range(2)], "mcp": [CANONICAL_BEFORE[2]]},
        "copied canonical274 seals changed",
    )
    for login, native in zip(t.LOGINS, NATIVE_BEFORE, strict=True):
        t.c0.require(before["boundaries"][login]["native"] == native, "unqualified native274 predecessor")


def validate_bridge(before, after):
    checked_before(before)
    t.c0.checked(after, target=True)
    fields = {"ledger", "seals", "portable_catalog", "raw_portable_catalog_v2", "boundaries"}
    t.c0.require(
        {k: v for k, v in before.items() if k not in fields} == {k: v for k, v in after.items() if k not in fields},
        "275 uncontrolled full witness facts",
    )
    t.c0.require(
        after["ledger"] == sorted([*before["ledger"], LEDGER_ROW], key=lambda r: (r[0], r[1])),
        "275 uncontrolled ledger",
    )
    t.c0.require(
        after["seals"]
        == {"ordinary": [[t.LOGINS[i], CANONICAL_AFTER[i]] for i in range(2)], "mcp": [CANONICAL_AFTER[2]]},
        "275 canonical successor seals changed",
    )
    for field in ("portable_catalog", "raw_portable_catalog_v2"):
        old = {(r[0], r[1]): r[2] for r in before[field]}
        new = {(r[0], r[1]): r[2] for r in after[field]}
        t.c0.require(
            old.keys() == new.keys() and {k for k in old if old[k] != new[k]} == {("function", CATALOG_ID)},
            "275 full catalog drift beyond lighting config",
        )
    for login, native in zip(t.LOGINS, NATIVE_AFTER, strict=True):
        old = before["boundaries"][login]
        new = after["boundaries"][login]
        t.c0.require(old.keys() == new.keys(), "275 boundary field shape changed")
        t.c0.require(new["native"] == native, "275 unqualified native successor")
        for field in old:
            if field in ("native", "raw_entries", "semantic_entries"):
                continue
            t.c0.require(old[field] == new[field], "275 native implementation posture drift")
        for field in ("raw_entries", "semantic_entries"):
            if field not in old:
                continue

            def unchanged(rows):
                return [v for v in rows if not v.startswith(ENTRY_PREFIX)]

            t.c0.require(unchanged(old[field]) == unchanged(new[field]), "275 other complete boundary entries changed")
            old_changed = [v for v in old[field] if v.startswith(ENTRY_PREFIX)]
            new_changed = [v for v in new[field] if v.startswith(ENTRY_PREFIX)]
            t.c0.require(
                len(old_changed) == len(new_changed) == (0 if login == t.LOGINS[2] else 1),
                "275 lighting boundary multiplicity drift",
            )


def emit_bridge(cluster, before, migration, *, reviewed_post=None, qualification_sha=None):
    checked_before(before)
    configured(cluster)
    if reviewed_post is not None:
        validate_bridge(before, reviewed_post)
        t.c0.require(t.transaction.is_hash(qualification_sha), "bound275 rollback qualification required")
    guards, select = witness_parts(cluster)
    lit = t.literal
    identity = f"""DO $identity$ BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'{cluster}'
 OR current_setting('server_version_num')::int<>160013 OR inet_client_addr() IS NOT NULL
 OR pg_is_in_recovery() OR current_user<>session_user OR current_user<>'verdify'
 OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447 THEN
 RAISE EXCEPTION '275 refuses target identity'; END IF; END $identity$;"""
    sql = f"""\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=pg_catalog,pg_temp;
SET LOCAL statement_timeout='120s';
SET LOCAL jit=off;
SET LOCAL lock_timeout='2s';
{identity}
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
{t.refresh_custody_sql()}
{guards}
{t.transaction_witness_inputs_sql(before, reviewed_post)}
DO $capture_before$ DECLARE v_before jsonb; BEGIN
 {select} INTO v_before;
 IF v_before IS DISTINCT FROM current_setting('verdify.cnpg_transition_expected_before')::jsonb THEN
 RAISE EXCEPTION '275 stale complete predecessor'; END IF;
 PERFORM set_config('verdify.cnpg275_before',v_before::text,true);
 PERFORM set_config('verdify.cnpg275_original',{t.original_facts_sql()}::text,true);
 PERFORM set_config('verdify.cnpg275_proc',(SELECT to_jsonb(p)::text FROM pg_proc p
 WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure),true);
END $capture_before$;
SELECT set_config('verdify.cnpg275_started',clock_timestamp()::text,true) IS NOT NULL;
{remap_migration(migration)}
SET LOCAL search_path=pg_catalog,pg_temp;
-- Explicit target-qualified runner artifact; source275 itself is unchanged.
INSERT INTO public.schema_migrations(filename,source,seq,sha256,stamp_method,applied_at,duration_ms,applied_by)
VALUES ('{MIGRATION}','db/migrations',275,'{MIGRATION_SHA}','runner',clock_timestamp(),
(extract(epoch FROM clock_timestamp()-current_setting('verdify.cnpg275_started')::timestamptz)*1000)::integer,current_user);
DO $capture_after$ DECLARE v_before jsonb; v_after jsonb; v_original jsonb; v_current jsonb; v_field text;
BEGIN
 v_before:=current_setting('verdify.cnpg275_before')::jsonb;
 v_original:=current_setting('verdify.cnpg275_original')::jsonb;
 {select} INTO v_after;
 IF (SELECT to_jsonb(p)-'proconfig' FROM pg_proc p
 WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure)
 IS DISTINCT FROM current_setting('verdify.cnpg275_proc')::jsonb-'proconfig'
 OR (SELECT to_jsonb(proconfig) FROM pg_proc WHERE oid='public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure)
 IS DISTINCT FROM to_jsonb(array_append(coalesce(ARRAY(SELECT jsonb_array_elements_text(
 nullif(current_setting('verdify.cnpg275_proc')::jsonb->'proconfig','null'::jsonb))),ARRAY[]::text[]),'jit=off')) THEN
 RAISE EXCEPTION '275 native pg_proc changed beyond exact config'; END IF;
 IF (v_after-ARRAY['ledger','seals','portable_catalog','raw_portable_catalog_v2','boundaries'])
 IS DISTINCT FROM (v_before-ARRAY['ledger','seals','portable_catalog','raw_portable_catalog_v2','boundaries']) THEN
 RAISE EXCEPTION '275 full native nonprojected facts changed'; END IF;
 IF (SELECT jsonb_agg(value ORDER BY value->>0,value->>1) FROM jsonb_array_elements(v_after->'ledger')
 WHERE value IS DISTINCT FROM {lit(json.dumps(LEDGER_ROW))}::jsonb)
 IS DISTINCT FROM v_before->'ledger' THEN RAISE EXCEPTION '275 historical ledger changed'; END IF;
 SELECT {t.original_facts_sql()} INTO v_current;
 IF v_current->'mcp' IS DISTINCT FROM v_original->'mcp'
 OR (SELECT jsonb_agg(value-'boundary_sha256'-'captured_at' ORDER BY value->>'login_name')
 FROM jsonb_array_elements(v_current->'ordinary')) IS DISTINCT FROM
 (SELECT jsonb_agg(value-'boundary_sha256'-'captured_at' ORDER BY value->>'login_name')
 FROM jsonb_array_elements(v_original->'ordinary'))
 OR (SELECT jsonb_agg(value ORDER BY value->>'source',value->>'filename') FROM jsonb_array_elements(v_current->'ledger')
 WHERE value->>'filename'<>'{MIGRATION}') IS DISTINCT FROM v_original->'ledger' THEN
 RAISE EXCEPTION '275 immutable original row fields changed'; END IF;
 FOR v_field IN SELECT unnest(ARRAY['portable_catalog','raw_portable_catalog_v2']) LOOP
 IF (SELECT count(*) FROM jsonb_array_elements(v_after->v_field) WHERE value->>0='function' AND value->>1='{CATALOG_ID}')<>1
 OR (SELECT jsonb_agg(value ORDER BY ord) FROM jsonb_array_elements(v_after->v_field) WITH ORDINALITY x(value,ord)
 WHERE NOT(value->>0='function' AND value->>1='{CATALOG_ID}')) IS DISTINCT FROM
 (SELECT jsonb_agg(value ORDER BY ord) FROM jsonb_array_elements(v_before->v_field) WITH ORDINALITY x(value,ord)
 WHERE NOT(value->>0='function' AND value->>1='{CATALOG_ID}')) THEN
 RAISE EXCEPTION '275 complete catalog changed beyond lighting config'; END IF; END LOOP;
 IF current_setting('verdify.cnpg_transition_expected_post')::jsonb<>'null'::jsonb AND
 v_after IS DISTINCT FROM current_setting('verdify.cnpg_transition_expected_post')::jsonb THEN
 RAISE EXCEPTION '275 install differs from exact reviewed post witness'; END IF;
 PERFORM set_config('verdify.cnpg275_result',jsonb_build_object('version','{VERSION}',
 'cluster','{cluster}','migration_sha256','{MIGRATION_SHA}','qualified_payload_sha256','{sha(remap_migration(migration).encode())}',
 'mode','{"install" if reviewed_post is not None else "rollback-qualification"}',
 'before_witness',v_before,'post_witness',v_after)::text,true);
END $capture_after$;
SELECT current_setting('verdify.cnpg275_result');
{"COMMIT;" if reviewed_post is not None else "ROLLBACK;"}
"""
    return t.bootstrap_owner_sql(sql, physical_target=cluster)


def emit_admission(cluster, before, *, reviewed_post=None, qualification_sha=None, copied_rows=None):
    configured(cluster)
    t.c0.require(
        sha((json.dumps(before, indent=2) + "\n").encode()) == NATIVE275_BRIDGE_WITNESS_SHA,
        "only the native-qualified275 bridge post witness is admitted",
    )
    t.c0.require(
        max(row[2] for row in before["ledger"] if row[0] == "db/migrations") == 275 and LEDGER_ROW in before["ledger"],
        "qualified275 ledger required",
    )
    t.c0.require(
        before["seals"]
        == {"ordinary": [[t.LOGINS[i], CANONICAL_AFTER[i]] for i in range(2)], "mcp": [CANONICAL_AFTER[2]]},
        "qualified canonical275 seals required",
    )
    for login, native in zip(t.LOGINS, NATIVE_AFTER, strict=True):
        t.c0.require(before["boundaries"][login]["native"] == native, "native275 predecessor required")
    sql = t.emit_sql(
        before,
        reviewed_post=reviewed_post,
        qualification_sha256=qualification_sha,
        physical_target=cluster,
        logical_receipts=copied_rows,
    )
    sql = sql.replace(
        "SET LOCAL statement_timeout='120s';", "SET LOCAL statement_timeout='120s';\nSET LOCAL jit=off;", 1
    )
    return t.bootstrap_owner_sql(sql, physical_target=cluster)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("bridge-qualify", "bridge-install", "admission-qualify", "admission-install"))
    p.add_argument("--cluster", choices=TARGETS, required=True)
    for name in ("before", "migration", "reviewed-post", "copied-rows"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--before-sha256", required=True)
    p.add_argument("--reviewed-post-sha256")
    p.add_argument("--qualification", type=Path)
    p.add_argument("--qualification-sha256")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    before, before_sha = t.c0.read_witness(a.before)
    t.c0.require(before_sha == a.before_sha256, "complete predecessor custody changed")
    post = None
    if a.reviewed_post:
        post, post_sha = t.c0.read_witness(a.reviewed_post)
        t.c0.require(post_sha == a.reviewed_post_sha256, "reviewed post custody changed")
    t.c0.require(
        a.mode.endswith("install") == (post is not None),
        "installation requires reviewed post; qualification must rollback",
    )
    if post is not None:
        t.c0.require(a.qualification is not None, "native rollback artifact required for installation")
        t.c0.require(
            a.qualification.is_file() and not a.qualification.is_symlink(), "regular native rollback artifact required"
        )
        with a.qualification.open("rb") as f:
            raw = f.read(2 * t.c0.WITNESS_MAX_BYTES + 1025)
        t.c0.require(
            len(raw) <= 2 * t.c0.WITNESS_MAX_BYTES + 1024, "native rollback artifact exceeds two-witness bound"
        )
        t.c0.require(sha(raw) == a.qualification_sha256, "native rollback artifact custody changed")
        record = json.loads(raw, object_pairs_hook=t.c0.boundary._pairs)
        t.c0.require(
            record["mode"] == "rollback-qualification"
            and record["before_witness"] == before
            and record["post_witness"] == post,
            "native rollback witnesses differ from install inputs",
        )
        if a.mode.startswith("bridge"):
            t.c0.require(
                record["version"] == VERSION
                and record["cluster"] == a.cluster
                and record["migration_sha256"] == MIGRATION_SHA
                and record["qualified_payload_sha256"] == sha(remap_migration(a.migration.read_bytes()).encode()),
                "native275 source/profile qualification changed",
            )
        else:
            configured(a.cluster)
            t.c0.require(
                record["version"] == "cnpg-physical-runtime-transition-v1"
                and record["ddl_sha256"] == sha(t.ddl(True, physical_target=a.cluster)[0].encode()),
                "native275 admission source qualification changed",
            )
            t.validate_post(before, post, physical_target=a.cluster)
    if a.mode.startswith("bridge"):
        sql = emit_bridge(
            a.cluster, before, a.migration.read_bytes(), reviewed_post=post, qualification_sha=a.qualification_sha256
        )
    else:
        sql = emit_admission(
            a.cluster,
            before,
            reviewed_post=post,
            qualification_sha=a.qualification_sha256,
            copied_rows=json.loads(a.copied_rows.read_bytes()),
        )
    with a.output.open("x") as f:
        f.write(sql)


if __name__ == "__main__":
    main()
