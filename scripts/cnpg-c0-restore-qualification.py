"""Read-only C0 witness for PG16.11 -> isolated CNPG16.13 logical recovery.

This emits/compares catalog evidence only. It neither installs a transition nor
changes the original sealed receipts. Ordinary clients remain fail-closed until
an independently qualified owning delivery transition admits the new cluster.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("boundary", ROOT / "scripts/ordinary-boundary-diff.py")
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)
VERSION = "cnpg-c0-logical-recovery-witness-v3"
RAW_VERSION = "cnpg-c0-logical-recovery-witness-v2"
FROZEN_SOURCE_V2_SHA256 = "ec9b3d5aff2bbfd5ced3d1a769551053e72dadd0cb811a5a843ec7b022110e1d"
FROZEN_SOURCE_V2_CATALOG_SHA256 = "f79f3c2d171097426f98a3aeb1eb52e71d7c04283b6e92821308099a5c090908"
HISTORICAL_PROFILE = "cnpg-120237-postdump-chunk1069-v1"
WITNESS_MAX_BYTES = 64 * 1024 * 1024
# Independently captured original-source READ ONLY metadata and native backup custody.
HISTORICAL_FRESH_SHA = "f2deda6beaedb6f01878f80e9ddc3a5c7f0acd8174dbdaa6fce2cf791be77c1f"
HISTORICAL_FRESH_CONTENT_SHA = "febca847211b5805ffec0ae205b718116a03bcc16b3c0f25fdc6489f55f012f5"
HISTORICAL_METADATA_SHA = "45166eea3e953c3736a7c932674bba58c7e49105436b2fd70332958c52ae89f2"
HISTORICAL_CAPTURE_SHA = "66cc8f0c634dfd994c69137cbc06f9d2a96f00dcf79f59dd60ade5aa9a5e2006"
HISTORICAL_BACKUP_SHA = "d052fbd0a5ec77392a3e4b7092d5c15255e532b393fa9d501957bb309c9e526b"
HISTORICAL_TOC_SHA = "cf4fe98314c9070ea11bc726323b7e1b549acf964354dd58a06c14f6801da37a"
HISTORICAL_DUMP_SHA = "e5c9ade10f29cbf026e85c260aa3374a18083899bc9107a0d5701e5cbb7f6dfc"
HISTORICAL_SOURCE_POD_UID = "b1fa2c0e-a995-481f-a29f-4cfaad4e697b"
HISTORICAL_CHUNK_ENTRIES = [
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk.details",
        "c203cd9f6f6216d8cd4c29d3c50fc4333c487eb91d94ae7d7cb48b49668c7e30",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk.greenhouse_id",
        "34eafa3e30bd0ab80a0e9a25934dffeec084dd08e21be2fbe53c8e4b019be84a",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk.mode",
        "29230b782971a5158662b12bb3b64e301674224d091f8520f899874146825f49",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk.override_type",
        "838adddfbe039f7510b64b2a799f6d0a161fcb067038d389b661b60949958116",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk.ts",
        "24f4b7dc166ff4b81e7bcbbdaf3c191b8a755fd6e7aa7c146408bf069c046a12",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk_idx_override_events_ts.ts",
        "b1ecdba7d6ede88038a8c4625e816f31fd17535aa4122152a74bf598a1e35460",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk_idx_override_events_type.override_type",
        "d37f80fb6818df7ad86f6a543afb9294a6ddd365b8c5c9ae6c2267347cba0f8c",
    ],
    [
        "column",
        "_timescaledb_internal._hyper_19_1069_chunk_idx_override_events_type.ts",
        "e4f15e04e7d8df3542297c5084d9944ca2e8bd7e9aca7fc81a6b22c0b2473d72",
    ],
    [
        "constraint",
        "_timescaledb_internal._hyper_19_1069_chunk.constraint_886",
        "7a74ebefbe8041c4c8cb927ddd8189bb3a1f8cdbf17589d8c67c580acdcd2427",
    ],
    [
        "index",
        "_timescaledb_internal._hyper_19_1069_chunk_idx_override_events_ts",
        "e4df15ad87f77de6ee2c4d699328b9a6e45ab7fde1c1235ac04bcc5f6e7e6a62",
    ],
    [
        "index",
        "_timescaledb_internal._hyper_19_1069_chunk_idx_override_events_type",
        "594a76d8be92b819ca8c1580b6c262d8201e78e85998e5f1c22e15394302f1cf",
    ],
    [
        "relation",
        "_timescaledb_internal._hyper_19_1069_chunk",
        "f585f8d7ba94ce613a259962adb114d2ae56fa0cbe98bdb214b2bc96afb80bec",
    ],
]

BOOTSTRAP_PROFILE = "cnpg-source-bootstrap-grantor-v1"
MCP_SOURCE = "db/migrations/263-mcp-timescale-chunk-boundary-digest.sql"
MCP_SHA = "9f5fa53cde76224b06865095bfd9a531aadae058f6ca13e50e74ecd95ad5770b"
MIGRATION_268_SHA = "aed9c4e562ff0420e315d14211224d1fff6469b9e0d2a541aedbc0bc562447e0"


def acl_sql(expression):
    return f"""(SELECT coalesce(jsonb_agg(jsonb_build_array(
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
        pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable)
        ORDER BY CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                 pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable),'[]'::jsonb)
        FROM aclexplode({expression}) a)"""


def raw_portable_catalog_sql():
    # Force text before UNION: PostgreSQL name otherwise truncates qualified
    # identities to 63 bytes and makes distinct columns collide.
    # Cover the six newly separated workload roles as well as the three
    # historical seals. No target role/OID mapping is installed in a function.
    return f"""WITH user_schemas AS (
      SELECT * FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema'
    ), objects(kind,identity,definition) AS (
      SELECT 'schema',n.nspname::text,jsonb_build_array(pg_get_userbyid(n.nspowner),{acl_sql("n.nspacl")})
       FROM user_schemas n
      UNION ALL
      SELECT 'relation',format('%I.%I',n.nspname,c.relname),
       jsonb_build_array(c.relkind,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,
        c.reloptions,{acl_sql("c.relacl")},CASE WHEN c.relkind IN('v','m') THEN pg_get_viewdef(c.oid,true) END)
       FROM pg_class c JOIN user_schemas n ON n.oid=c.relnamespace WHERE c.relkind IN('r','p','v','m','f','S')
      UNION ALL
      SELECT 'column',format('%I.%I.%I',n.nspname,c.relname,a.attname),
       jsonb_build_array(a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
                        pg_get_expr(d.adbin,d.adrelid),{acl_sql("a.attacl")})
       FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
       JOIN user_schemas n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
       WHERE a.attnum>0 AND NOT a.attisdropped
      UNION ALL
      SELECT 'function',p.oid::regprocedure::text,
       jsonb_build_array(pg_get_userbyid(p.proowner),pg_get_functiondef(p.oid),{acl_sql("p.proacl")})
       FROM pg_proc p JOIN user_schemas n ON n.oid=p.pronamespace WHERE p.prokind IN('f','p')
      UNION ALL
      SELECT 'constraint',format('%I.%I.%I',n.nspname,c.relname,x.conname),to_jsonb(pg_get_constraintdef(x.oid,true))
       FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN user_schemas n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'index',format('%I.%I',n.nspname,c.relname),to_jsonb(pg_get_indexdef(i.indexrelid))
       FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN user_schemas n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'trigger',format('%I.%I.%I',n.nspname,c.relname,t.tgname),
       jsonb_build_array(t.tgenabled,pg_get_triggerdef(t.oid,true))
       FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN user_schemas n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'rule',format('%I.%I.%I',n.nspname,c.relname,r.rulename),
       jsonb_build_array(r.ev_enabled,pg_get_ruledef(r.oid,true))
       FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN user_schemas n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'policy',format('%I.%I.%I',n.nspname,c.relname,p.polname),
       jsonb_build_array(p.polcmd,p.polpermissive,
        (SELECT jsonb_agg(CASE WHEN oid=0 THEN 'PUBLIC' ELSE pg_get_userbyid(oid) END
                         ORDER BY CASE WHEN oid=0 THEN 'PUBLIC' ELSE pg_get_userbyid(oid) END)
          FROM unnest(p.polroles) oid),pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid))
       FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN user_schemas n ON n.oid=c.relnamespace
      UNION ALL
      SELECT 'default-acl',format('%s|%s|%s',pg_get_userbyid(d.defaclrole),coalesce(n.nspname,'ALL_SCHEMAS'),d.defaclobjtype),
       {acl_sql("d.defaclacl")} FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
    ) SELECT jsonb_agg(jsonb_build_array(kind,identity,encode(public.digest(definition::text,'sha256'),'hex'))
                      ORDER BY kind,identity) FROM objects"""


RI_FUNCTIONS = {
    "RI_FKey_check_ins",
    "RI_FKey_check_upd",
    "RI_FKey_noaction_del",
    "RI_FKey_noaction_upd",
    "RI_FKey_restrict_del",
    "RI_FKey_restrict_upd",
    "RI_FKey_cascade_del",
    "RI_FKey_cascade_upd",
    "RI_FKey_setnull_del",
    "RI_FKey_setnull_upd",
    "RI_FKey_setdefault_del",
    "RI_FKey_setdefault_upd",
}


def native_fk_predicate(alias="t"):
    # A generated-looking user trigger is never normalized. Require the native
    # internal FK object, actual OID suffix and built-in RI implementation.
    return f"""{alias}.tgisinternal AND x.contype='f'
      AND {alias}.tgname ~ ('^RI_ConstraintTrigger_[ac]_'||{alias}.oid::text||'$')
      AND fn.pronamespace='pg_catalog'::regnamespace AND fn.oid<16384
      AND fn.pronargs=0 AND fn.prorettype='pg_catalog.trigger'::regtype
      AND lang.lanname='internal' AND fn.prosrc=fn.proname::text
      AND fn.proname IN ('RI_FKey_check_ins','RI_FKey_check_upd',
       'RI_FKey_noaction_del','RI_FKey_noaction_upd','RI_FKey_restrict_del',
       'RI_FKey_restrict_upd','RI_FKey_cascade_del','RI_FKey_cascade_upd',
       'RI_FKey_setnull_del','RI_FKey_setnull_upd','RI_FKey_setdefault_del','RI_FKey_setdefault_upd')"""


def typed_fk_predicate():
    parent = native_fk_predicate("p").replace("x.", "px.").replace("fn.", "pf.").replace("lang.", "pl.")
    return f"""({native_fk_predicate()}) AND coalesce((
      WITH RECURSIVE parents AS (
        SELECT pt.* FROM pg_trigger pt WHERE pt.oid=t.tgparentid
        UNION ALL SELECT pt.* FROM parents p JOIN pg_trigger pt ON pt.oid=p.tgparentid
      ) SELECT bool_and(coalesce(({parent}),false)) FROM parents p
        LEFT JOIN pg_constraint px ON px.oid=p.tgconstraint
        JOIN pg_proc pf ON pf.oid=p.tgfoid JOIN pg_language pl ON pl.oid=pf.prolang),true)"""


def trigger_joins():
    return """FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
       JOIN user_schemas n ON n.oid=c.relnamespace
       LEFT JOIN pg_constraint x ON x.oid=t.tgconstraint
       JOIN pg_proc fn ON fn.oid=t.tgfoid JOIN pg_language lang ON lang.oid=fn.prolang
       LEFT JOIN pg_class ref ON ref.oid=t.tgconstrrelid
       LEFT JOIN pg_namespace refn ON refn.oid=ref.relnamespace"""


def fk_definition_sql():
    # Parent identities use named relation/constraint/function/type facts, never
    # the new target's numeric OIDs. Every parent trigger has its own catalog row.
    return """jsonb_build_array(t.tgtype,t.tgenabled,t.tgisinternal,t.tgdeferrable,
       t.tginitdeferred,t.tgnargs,encode(t.tgargs,'hex'),t.tgattr::text,
       pg_get_expr(t.tgqual,t.tgrelid),t.tgoldtable,t.tgnewtable,
       pg_get_constraintdef(x.oid,true),refn.nspname,ref.relname,
       (WITH RECURSIVE parents AS (
         SELECT pt.*,1 AS depth FROM pg_trigger pt WHERE pt.oid=t.tgparentid
         UNION ALL SELECT pt.*,p.depth+1 FROM parents p JOIN pg_trigger pt ON pt.oid=p.tgparentid
       ) SELECT jsonb_agg(jsonb_build_array(pn.nspname,pc.relname,px.conname,
                  pf.oid::regprocedure::text,p.tgtype,p.tgenabled,p.tgisinternal,
                  p.tgdeferrable,p.tginitdeferred,p.tgnargs,encode(p.tgargs,'hex'),
                  p.tgattr::text,pg_get_expr(p.tgqual,p.tgrelid),p.tgoldtable,p.tgnewtable,
                  pg_get_constraintdef(px.oid,true),prn.nspname,pr.relname)
                  ORDER BY p.depth)
         FROM parents p JOIN pg_class pc ON pc.oid=p.tgrelid
         JOIN pg_namespace pn ON pn.oid=pc.relnamespace
         LEFT JOIN pg_constraint px ON px.oid=p.tgconstraint
         JOIN pg_proc pf ON pf.oid=p.tgfoid
         LEFT JOIN pg_class pr ON pr.oid=p.tgconstrrelid
         LEFT JOIN pg_namespace prn ON prn.oid=pr.relnamespace))"""


def portable_catalog_sql():
    raw = raw_portable_catalog_sql()
    raw = raw.replace(
        acl_sql("c.relacl"),
        acl_sql(
            "coalesce(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 's'::\"char\" "
            "ELSE 'r'::\"char\" END,c.relowner))"
        ),
    )
    old = """SELECT 'trigger',format('%I.%I.%I',n.nspname,c.relname,t.tgname),
       jsonb_build_array(t.tgenabled,pg_get_triggerdef(t.oid,true))
       FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN user_schemas n ON n.oid=c.relnamespace"""
    new = f"""SELECT CASE WHEN {typed_fk_predicate()} THEN 'foreign-key-trigger' ELSE 'trigger' END,
       CASE WHEN {typed_fk_predicate()}
        THEN jsonb_build_array(n.nspname,c.relname,x.conname,fn.proname)::text
        ELSE format('%I.%I.%I',n.nspname,c.relname,t.tgname) END,
       CASE WHEN {typed_fk_predicate()} THEN {fk_definition_sql()}
        ELSE jsonb_build_array(t.tgenabled,pg_get_triggerdef(t.oid,true)) END
       {trigger_joins()}"""
    require(raw.count(old) == 1, "raw trigger projection changed")
    return raw.replace(old, new)


def portability_native_facts_sql():
    # Keep OID names and complete native pg_class/pg_trigger/pg_constraint facts
    # as separate custody, not as semantic equality or permission to reseal.
    return f"""WITH user_schemas AS (
      SELECT * FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema'
    ) SELECT jsonb_build_object(
      'relations',(SELECT jsonb_agg(jsonb_build_object('identity',format('%I.%I',n.nspname,c.relname),
        'native',to_jsonb(c),'schema',n.nspname,
       'raw_definition_text',jsonb_build_array(c.relkind,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,
        c.reloptions,{acl_sql("c.relacl")},CASE WHEN c.relkind IN('v','m') THEN pg_get_viewdef(c.oid,true) END)::text)
        ORDER BY n.nspname,c.relname)
       FROM pg_class c JOIN user_schemas n ON n.oid=c.relnamespace WHERE c.relkind IN('r','p','v','m','f','S')),
       'indexes',(SELECT coalesce(jsonb_agg(jsonb_build_object(
       'identity',format('%I.%I',n.nspname,c.relname),'native',to_jsonb(c),
       'index',to_jsonb(i),'definition',pg_get_indexdef(i.indexrelid)) ORDER BY n.nspname,c.relname),'[]'::jsonb)
       FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN user_schemas n ON n.oid=c.relnamespace),
      'constraints',(SELECT coalesce(jsonb_agg(jsonb_build_object(
       'identity',format('%I.%I.%I',n.nspname,c.relname,x.conname),'native',to_jsonb(x),
       'definition',pg_get_constraintdef(x.oid,true)) ORDER BY n.nspname,c.relname,x.conname),'[]'::jsonb)
       FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN user_schemas n ON n.oid=c.relnamespace),
      'triggers',(SELECT coalesce(jsonb_agg(jsonb_build_object(
       'raw_identity',format('%I.%I.%I',n.nspname,c.relname,t.tgname),
        'native',to_jsonb(t),'constraint',to_jsonb(x),'function',to_jsonb(fn),
       'schema',n.nspname,'relation',c.relname,
       'constraint_definition',pg_get_constraintdef(x.oid,true),
       'qualification',CASE WHEN {typed_fk_predicate()} THEN pg_get_expr(t.tgqual,t.tgrelid)
                            ELSE t.tgqual::text END,
       'referenced_relation',jsonb_build_array(refn.nspname,ref.relname),
       'language',lang.lanname,'typed_fk',coalesce({typed_fk_predicate()},false),
       'definition',pg_get_triggerdef(t.oid,true)) ORDER BY n.nspname,c.relname,t.tgname),'[]'::jsonb)
       {trigger_joins()}))"""


def catalog_sha256(catalog):
    return hashlib.sha256(json.dumps(catalog, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_witness(path):
    # Complete native v3 custody is 35MB; retain it plus immutable historical evidence.
    require(path.is_file() and not path.is_symlink(), "regular witness required")
    with path.open("rb") as stream:
        raw = stream.read(WITNESS_MAX_BYTES + 1)
    require(len(raw) <= WITNESS_MAX_BYTES, "witness exceeds bound")
    return json.loads(raw, object_pairs_hook=boundary._pairs), hashlib.sha256(raw).hexdigest()


def mcp_projection():
    raw = (ROOT / MCP_SOURCE).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == MCP_SHA, "changed MCP projection source")
    body = raw.decode().split("AS $body$", 1)[1].split("$body$;", 1)[0]
    cte = body[: body.index("\nSELECT public.digest(")]
    return body, cte


def ordinary_projection(login, *, semantic=False):
    body, cte = boundary.source_projection(login)
    if semantic:
        # Only ACL grantee identities differ after logical restore. Preserve
        # every privilege, grant option, owner, body and membership check.
        identity = "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(acl.grantee) END"
        cte, count = re.subn(r"(format\('%s:%s:%s',\s*)acl.grantee", lambda m: m[1] + identity, cte)
        require(count == 8, "unexpected ACL projection shape")
        cte, count = re.subn(r"ORDER BY acl.grantee", "ORDER BY " + identity, cte)
        require(count == 8, "unexpected ACL ordering shape")
        cte, count = re.subn(r"database_row.datname,", "'verdify',", cte)
        require(count == 2, "unexpected database-name projection shape")
    return body, cte


def emit_sql(target=False, *, bootstrap_grantor_profile=False):
    require(not bootstrap_grantor_profile or target, "bootstrap translation is target-only")
    profile_sql = "'" + BOOTSTRAP_PROFILE + "'" if bootstrap_grantor_profile else "NULL"
    database, version = ("verdify_rehearsal", 160013) if target else ("verdify", 160011)
    guard = "OR current_setting('cluster_name') <> 'verdify-cnpg-rehearsal'" if target else ""
    queries = []
    implementation_guards = []
    for login in boundary.LOGINS:
        body, raw = ordinary_projection(login)
        _, semantic = ordinary_projection(login, semantic=True)
        implementation_guards.append(f"""NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
          WHERE p.oid='public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure
           AND encode(public.digest(p.prosrc,'sha256'),'hex')='{hashlib.sha256(body.encode()).hexdigest()}'
           AND p.prosecdef AND l.lanname='plpgsql'
           AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
           AND p.proowner=(SELECT datdba FROM pg_database WHERE datname=current_database()))""")
        queries.append(f"""'{login}', jsonb_build_object(
            'installed_body_sha256', (SELECT encode(public.digest(prosrc,'sha256'),'hex')
                FROM pg_proc WHERE oid='public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure),
            'expected_body_sha256', '{hashlib.sha256(body.encode()).hexdigest()}',
            'installed_shape_verified', (SELECT p.prosecdef AND l.lanname='plpgsql'
                AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
                AND p.proowner=(SELECT datdba FROM pg_database WHERE datname=current_database())
                FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
                WHERE p.oid='public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure),
            'native', encode(public.fn_runtime_ordinary_boundary_digest('{login}'),'hex'),
            'raw_entries', ({raw} SELECT jsonb_agg(entry ORDER BY entry) FROM security_entries),
            'semantic_entries', ({semantic} SELECT jsonb_agg(entry ORDER BY entry) FROM security_entries))""")
    mcp_body, mcp_cte = mcp_projection()
    implementation_guards.append(f"""NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
      WHERE p.oid='public.fn_mcp_runtime_boundary_digest()'::regprocedure
       AND encode(public.digest(p.prosrc,'sha256'),'hex')='{hashlib.sha256(mcp_body.encode()).hexdigest()}'
       AND p.prosecdef AND l.lanname='sql'
       AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
       AND p.proowner=(SELECT datdba FROM pg_database WHERE datname=current_database()))""")
    queries.append(f"""'verdify_mcp_runtime_login', jsonb_build_object(
        'installed_body_sha256', (SELECT encode(public.digest(prosrc,'sha256'),'hex')
            FROM pg_proc WHERE oid='public.fn_mcp_runtime_boundary_digest()'::regprocedure),
        'expected_body_sha256', '{hashlib.sha256(mcp_body.encode()).hexdigest()}',
        'installed_shape_verified', (SELECT p.prosecdef AND l.lanname='sql'
            AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
            AND p.proowner=(SELECT datdba FROM pg_database WHERE datname=current_database())
            FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
            WHERE p.oid='public.fn_mcp_runtime_boundary_digest()'::regprocedure),
        'native', encode(public.fn_mcp_runtime_boundary_digest(),'hex'),
        'raw_entries', ({mcp_cte} SELECT jsonb_agg(entry ORDER BY entry)
            FROM (SELECT DISTINCT entry FROM entries) e))""")
    return f"""\\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path=pg_catalog,pg_temp;
SET LOCAL statement_timeout='60s';
SET LOCAL lock_timeout='2s';
DO $guard$ BEGIN
 IF current_database()<>'{database}' OR current_setting('server_version_num')::int<>{version}
    {guard}
    OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2'
    OR (SELECT extversion FROM pg_extension WHERE extname='vector') IS DISTINCT FROM '0.8.1'
    OR NOT EXISTS(SELECT 1 FROM public.schema_migrations
                  WHERE seq=268 AND source='db/migrations' AND stamp_method='runner') THEN
   RAISE EXCEPTION 'C0 logical recovery witness refuses wrong target/predecessor';
 END IF;
END $guard$;
-- Refuse changed executable digest implementations before invoking any of them.
DO $implementation_guard$ BEGIN
 IF {" OR ".join(implementation_guards)} THEN
   RAISE EXCEPTION 'C0 logical recovery witness refuses changed executable digest implementation';
 END IF;
END $implementation_guard$;
SELECT jsonb_build_object(
 'version','{VERSION}', 'database',current_database(), 'server',current_setting('server_version_num')::int,
 'roles',(SELECT jsonb_object_agg(oid::text,rolname) FROM pg_roles),
 'bootstrap_grantor_profile',{profile_sql},
 'bootstrap_identity',(SELECT jsonb_build_object('oid',oid::int,'name',rolname,'superuser',rolsuper) FROM pg_roles WHERE oid=10),
 'database_owner',(SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()),
 'database_acl',(SELECT jsonb_agg(jsonb_build_array(
      CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
      pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY a.grantee,a.privilege_type)
      FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a WHERE d.datname=current_database()),
 'namespaces',(SELECT jsonb_object_agg(oid::text,nspname) FROM pg_namespace),
 'portable_catalog',({portable_catalog_sql()}),
 'raw_portable_catalog_v2',({raw_portable_catalog_sql()}),
 'portability_native_facts',({portability_native_facts_sql()}),
 'ledger',(SELECT jsonb_agg(jsonb_build_array(source,filename,seq,sha256,stamp_method)
                         ORDER BY source,filename) FROM public.schema_migrations),
 'seals',jsonb_build_object(
   'ordinary',(SELECT jsonb_agg(jsonb_build_array(login_name,encode(boundary_sha256,'hex')) ORDER BY login_name)
                FROM public.runtime_ordinary_login_attestation_receipts),
   'mcp',(SELECT jsonb_agg(encode(boundary_sha256,'hex')) FROM public.mcp_runtime_boundary_receipt)),
 'boundaries',jsonb_build_object({",".join(queries)}));
COMMIT;
"""


def semantic_mcp(entry, roles, namespaces):
    parts = entry.split("|")
    # Fixed typed slots in the pinned 263 projection, never a digit/text
    # substitution over SQL bodies, defaults or view expressions.
    slots = {
        "member": (1, 2, 3),
        "database": (1,),
        "schema": (1,),
        "relation": (4,),
        "relation-acl": (2, 3),
        "column-acl": (3, 4),
        "function": (2,),
        "function-acl": (2, 3),
        "default-acl": (1, 4),
    }
    require(parts[0] in {"role", *slots}, "unknown MCP projection entry")
    for slot in slots.get(parts[0], ()):
        oid = parts[slot]
        require(oid == "0" or oid in roles, "unmapped MCP role OID")
        parts[slot] = "PUBLIC" if oid == "0" else roles[oid]
    if parts[0] == "default-acl":
        oid = parts[2]
        require(oid == "0" or oid in namespaces, "unmapped MCP namespace OID")
        parts[2] = "ALL_SCHEMAS" if oid == "0" else namespaces[oid]
    return "|".join(parts)


def checked_catalog(catalog, *, raw=False):
    require(isinstance(catalog, list) and catalog, "empty portable catalog")
    identities = set()
    for entry in catalog:
        require(
            isinstance(entry, list)
            and len(entry) == 3
            and all(isinstance(value, str) and value and "\x00" not in value for value in entry)
            and entry[0]
            in {
                "schema",
                "relation",
                "column",
                "function",
                "constraint",
                "index",
                "trigger",
                *(set() if raw else {"foreign-key-trigger"}),
                "rule",
                "policy",
                "default-acl",
            }
            and re.fullmatch(r"[0-9a-f]{64}", entry[2]) is not None,
            "malformed portable catalog entry",
        )
        identity = (entry[0], entry[1])
        require(identity not in identities, "ambiguous portable catalog identity")
        identities.add(identity)
    return identities


def jsonb_array_text(value):
    # These projections contain arrays/scalars only, no JSON object ordering.
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def projected_hash(value):
    return hashlib.sha256(jsonb_array_text(value).encode()).hexdigest()


def fk_fields(fact):
    t = fact["native"]
    args = t["tgargs"]
    require(isinstance(args, str) and args.startswith("\\x"), "malformed native trigger arguments")
    attrs = " ".join(str(v) for v in t["tgattr"]) if isinstance(t["tgattr"], list) else t["tgattr"]
    return [
        t["tgtype"],
        t["tgenabled"],
        t["tgisinternal"],
        t["tgdeferrable"],
        t["tginitdeferred"],
        t["tgnargs"],
        args[2:],
        attrs,
        fact["qualification"],
        t["tgoldtable"],
        t["tgnewtable"],
        fact["constraint_definition"],
        *fact["referenced_relation"],
    ]


def validate_projection_facts(snapshot, trigger_by_oid):
    raw = {(r[0], r[1]): r[2] for r in snapshot["raw_portable_catalog_v2"]}
    semantic = {(r[0], r[1]): r[2] for r in snapshot["portable_catalog"]}
    for fact in snapshot["portability_native_facts"]["relations"]:
        n = fact["native"]
        definition = json.loads(fact["raw_definition_text"])
        owner = snapshot["roles"].get(str(n["relowner"]))
        require(
            definition[:4] == [n["relkind"], owner, n["relrowsecurity"], n["relforcerowsecurity"]]
            and definition[4] == n["reloptions"],
            "relation native/projection drift",
        )
        require(snapshot["namespaces"].get(str(n["relnamespace"])) == fact["schema"], "relation namespace drift")
        require(
            raw[("relation", fact["identity"])] == hashlib.sha256(fact["raw_definition_text"].encode()).hexdigest(),
            "raw relation definition mismatch",
        )
        if n["relacl"] is None:
            require(definition[5] == [], "NULL ACL raw projection drift")
            rights = (
                ["SELECT", "UPDATE", "USAGE"]
                if n["relkind"] == "S"
                else ["DELETE", "INSERT", "REFERENCES", "SELECT", "TRIGGER", "TRUNCATE", "UPDATE"]
            )
            definition[5] = [[owner, owner, right, False] for right in rights]
        require(
            semantic[("relation", fact["identity"])] == projected_hash(definition),
            "unproved relation ACL normalization",
        )
    for field, kind in [("indexes", "index"), ("constraints", "constraint")]:
        for fact in snapshot["portability_native_facts"][field]:
            require(
                raw[(kind, fact["identity"])] == projected_hash(fact["definition"]),
                "raw index/constraint definition mismatch",
            )
    for fact in snapshot["portability_native_facts"]["triggers"]:
        n = fact["native"]
        require(
            raw[("trigger", fact["raw_identity"])] == projected_hash([n["tgenabled"], fact["definition"]]),
            "raw trigger definition mismatch",
        )
        if not fact["typed_fk"]:
            require(
                semantic[("trigger", fact["raw_identity"])] == raw[("trigger", fact["raw_identity"])],
                "uncontrolled user trigger change",
            )
            continue
        identity = jsonb_array_text(
            [fact["schema"], fact["relation"], fact["constraint"]["conname"], fact["function"]["proname"]]
        )
        parents = []
        parent = str(n["tgparentid"])
        while parent != "0":
            f = trigger_by_oid[parent]
            parents.append(
                [
                    f["schema"],
                    f["relation"],
                    f["constraint"]["conname"],
                    '"' + f["function"]["proname"] + '"()',
                    *fk_fields(f),
                ]
            )
            parent = str(f["native"]["tgparentid"])
        definition = [*fk_fields(fact), parents or None]
        require(
            semantic.get(("foreign-key-trigger", identity)) == projected_hash(definition),
            "unproved typed FK definition/identity",
        )


def historical_catalogs(snapshot):
    """Prove one native postdump closure; retain full fresh fields, return explicit historical views."""
    proof = snapshot.get("historical_snapshot")
    if proof is None:
        return snapshot["raw_portable_catalog_v2"], snapshot["portable_catalog"]
    require(proof["profile"] == HISTORICAL_PROFILE, "unsupported historical snapshot profile")
    require(
        set(proof)
        == {
            "profile",
            "fresh_witness_sha256",
            "frozen_source_raw",
            "metadata_raw",
            "capture_raw",
            "backup_raw",
            "toc_raw",
            "dump_sha256",
        },
        "historical proof shape drift",
    )
    require(proof["fresh_witness_sha256"] == HISTORICAL_FRESH_SHA, "missing complete fresh custody")

    def pinned(field, sha):
        raw = proof[field]
        require(
            isinstance(raw, str) and hashlib.sha256(raw.encode()).hexdigest() == sha,
            "historical native custody drift: " + field,
        )
        return json.loads(raw, object_pairs_hook=boundary._pairs)

    frozen = pinned("frozen_source_raw", FROZEN_SOURCE_V2_SHA256)
    metadata = pinned("metadata_raw", HISTORICAL_METADATA_SHA)
    capture = pinned("capture_raw", HISTORICAL_CAPTURE_SHA)
    backup = pinned("backup_raw", HISTORICAL_BACKUP_SHA)
    require(
        hashlib.sha256(proof["toc_raw"].encode()).hexdigest() == HISTORICAL_TOC_SHA
        and proof["dump_sha256"] == HISTORICAL_DUMP_SHA,
        "historical dump custody drift",
    )
    require(
        "_hyper_19_1069_chunk" not in proof["toc_raw"] and "TABLE public override_events verdify" in proof["toc_raw"],
        "chunk was not absent from historical dump",
    )
    require(
        frozen["version"] == RAW_VERSION and frozen["database"] == "verdify" and frozen["server"] == 160011,
        "wrong historical source",
    )
    require(
        catalog_sha256(frozen["portable_catalog"]) == FROZEN_SOURCE_V2_CATALOG_SHA256,
        "immutable historical catalog drift",
    )
    for field in ["roles", "seals", "ledger", "boundaries", "namespaces", "database_acl", "database_owner"]:
        require(snapshot[field] == frozen[field], "historical original scope changed: " + field)
    require(
        snapshot.get("bootstrap_identity") == {"oid": 10, "name": "verdify", "superuser": True},
        "historical native source bootstrap changed",
    )
    raw = {(r[0], r[1]): r[2] for r in snapshot["raw_portable_catalog_v2"]}
    old = {(r[0], r[1]): r[2] for r in frozen["portable_catalog"]}
    added = {(r[0], r[1]): r[2] for r in HISTORICAL_CHUNK_ENTRIES}
    require(
        raw == old | added and not old.keys() & added.keys(),
        "historical change/removal/extra or chunk projection spoof",
    )
    identity = metadata["identity"]
    require(
        identity["database"] == "verdify"
        and identity["current_user"] == identity["session_user"] == "verdify"
        and identity["server_version_num"] == "160011"
        and identity["server_timezone"] == "UTC"
        and identity["transaction_read_only"] == "on"
        and identity["transaction_isolation"] == "repeatable read",
        "wrong native metadata source",
    )
    require(
        any(e["extname"] == "timescaledb" and e["extversion"] == "2.25.2" for e in identity["extensions"]),
        "native Timescale version drift",
    )
    require(
        capture["pod_uid"] == HISTORICAL_SOURCE_POD_UID
        and capture["context"] == "vallery"
        and capture["namespace"] == "verdify-prod"
        and capture["pod"] == "verdify-db-0"
        and capture["container"] == "postgres"
        and capture["exit_code"] == 0
        and capture["read_only"] is True
        and capture["hashes"]["capture.stdout"] == HISTORICAL_METADATA_SHA
        and capture["hashes"]["pod-before.json"] == capture["hashes"]["pod-after.json"],
        "native source Pod/capture custody drift",
    )

    def utc(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    require(
        utc(capture["started_at"]) <= utc(identity["server_now"]) <= utc(capture["finished_at"]),
        "native source clock outside capture bounds",
    )
    require(
        backup["pair_stem"] == "verdify-20261001T120237Z"
        and backup["native_dump_succeeded"] is True
        and backup["native_pair_published"] is True
        and backup["native_completed"] is True
        and backup["container_termination"]["exitCode"] == 0,
        "wrong native original backup",
    )
    chunk = metadata["chunk"]
    require(
        chunk["id"] == 1069
        and chunk["hypertable_id"] == 19
        and chunk["schema_name"] == "_timescaledb_internal"
        and chunk["table_name"] == "_hyper_19_1069_chunk"
        and chunk["dropped"] is False
        and chunk["osm_chunk"] is False
        and chunk["compressed_chunk_id"] is None
        and chunk["status"] == 0,
        "unproved native chunk lineage",
    )
    require(
        utc(backup["container_termination"]["finishedAt"]) < utc(chunk["creation_time"]) <= utc(identity["server_now"]),
        "chunk not proved created after historical dump",
    )
    require(
        [c for c in metadata["all_parent_chunks"] if c["id"] == 1069] == [chunk], "native parent chunk inventory drift"
    )
    ht, view = metadata["hypertable"], metadata["chunk_view"]
    require(
        ht["id"] == 19
        and ht["schema_name"] == "public"
        and ht["table_name"] == "override_events"
        and ht["num_dimensions"] == 1
        and ht["compression_state"] == 0
        and ht["status"] == 0
        and ht["compressed_hypertable_id"] is None,
        "wrong native hypertable",
    )
    require(
        view["hypertable_schema"] == "public"
        and view["hypertable_name"] == "override_events"
        and view["chunk_schema"] == chunk["schema_name"]
        and view["chunk_name"] == chunk["table_name"]
        and view["chunk_creation_time"] == chunk["creation_time"]
        and view["is_compressed"] is False
        and view["range_start"] == "2026-10-01T00:00:00+00:00"
        and view["range_end"] == "2026-10-08T00:00:00+00:00"
        and view["primary_dimension"] == "ts",
        "native range/creation projection drift",
    )
    require(
        metadata["dimensions"]
        == [
            {
                "id": 15,
                "aligned": True,
                "num_slices": None,
                "column_name": "ts",
                "column_type": "timestamp with time zone",
                "hypertable_id": 19,
                "interval_length": 604800000000,
                "integer_now_func": None,
                "partitioning_func": None,
                "integer_now_func_schema": None,
                "compress_interval_length": None,
                "partitioning_func_schema": None,
            }
        ],
        "native dimension drift",
    )
    require(
        metadata["dimension_slices"]
        == [{"id": 886, "range_end": 1791417600000000, "range_start": 1790812800000000, "dimension_id": 15}]
        and metadata["chunk_constraints"]
        == [
            {
                "chunk_id": 1069,
                "constraint_name": "constraint_886",
                "dimension_slice_id": 886,
                "hypertable_constraint_name": None,
            }
        ],
        "native slice/constraint lineage drift",
    )
    require(
        [i for i in metadata["inherits"] if str(i["inhrelid"]) == "1581647"]
        == [{"inhrelid": "1581647", "inhseqno": 1, "inhparent": "19713", "inhdetachpending": False}],
        "native inheritance lineage drift",
    )
    classes = {str(r["native"]["oid"]): r for r in metadata["classes"]}
    require(len(classes) == len(metadata["classes"]) == 7, "native class inventory drift")
    require(
        classes["19713"]["owner"] == classes["1581647"]["owner"] == "verdify"
        and classes["19713"]["effective_acl"] == classes["1581647"]["effective_acl"],
        "native chunk owner/ACL drift",
    )
    facts = snapshot["portability_native_facts"]
    for field in ["relations", "indexes"]:
        matching = [r for r in facts[field] if str(r["native"]["oid"]) in classes]
        require(len(matching) == (2 if field == "relations" else 5), "native metadata/fresh class inventory mismatch")
        for fact in matching:
            require(
                fact["native"] == classes[str(fact["native"]["oid"])]["native"],
                "native metadata/fresh class facts differ",
            )
    indexes = {str(r["native"]["indexrelid"]): r["native"] for r in metadata["indexes"]}
    require(len(indexes) == len(metadata["indexes"]) == 5, "native index inventory drift")
    for fact in facts["indexes"]:
        if str(fact["native"]["oid"]) in indexes:
            require(fact["index"] == indexes[str(fact["native"]["oid"])], "native metadata/fresh index facts differ")

    def signature(index):
        return json.dumps({k: v for k, v in index.items() if k not in {"indexrelid", "indrelid"}}, sort_keys=True)

    require(
        {signature(i) for i in indexes.values() if str(i["indrelid"]) == "19713"}
        == {signature(i) for i in indexes.values() if str(i["indrelid"]) == "1581647"},
        "native parent/chunk index correspondence drift",
    )
    columns = {
        rel: {
            r["native"]["attname"]: r
            for r in metadata["columns"]
            if str(r["native"]["attrelid"]) == rel and r["native"]["attnum"] > 0
        }
        for rel in ["19713", "1581647"]
    }
    require(
        set(columns["19713"]) == set(columns["1581647"]) == {"ts", "override_type", "mode", "details", "greenhouse_id"},
        "native column inventory drift",
    )
    for name, parent in columns["19713"].items():
        child = columns["1581647"][name]

        def inherited(row):
            return {k: v for k, v in row["native"].items() if k not in {"attrelid", "attislocal", "attinhcount"}}

        require(
            inherited(parent) == inherited(child)
            and parent["default"] == child["default"]
            and child["native"]["attislocal"] is False
            and child["native"]["attinhcount"] == 1,
            "native inherited column drift",
        )
    require(len(metadata["constraints"]) == 1, "native constraint inventory drift")
    native_constraint = metadata["constraints"][0]["native"]
    require(
        native_constraint["oid"] == "1581654"
        and native_constraint["conrelid"] == "1581647"
        and native_constraint["conname"] == "constraint_886"
        and native_constraint["contype"] == "c"
        and native_constraint["convalidated"] is True,
        "native check constraint drift",
    )
    require(
        [
            f["native"]
            for f in facts["constraints"]
            if f["identity"] == "_timescaledb_internal._hyper_19_1069_chunk.constraint_886"
        ]
        == [native_constraint],
        "native metadata/fresh constraint facts differ",
    )
    raw_view = [r for r in snapshot["raw_portable_catalog_v2"] if (r[0], r[1]) not in added]
    semantic_view = [r for r in snapshot["portable_catalog"] if (r[0], r[1]) not in added]
    require(len(raw_view) == len(semantic_view) == len(old), "historical projection multiplicity drift")
    require(
        catalog_sha256(raw_view) == FROZEN_SOURCE_V2_CATALOG_SHA256, "historical projection differs from immutable ec9"
    )
    complete_fresh = {k: v for k, v in snapshot.items() if k != "historical_snapshot"}
    require(
        hashlib.sha256(
            json.dumps(complete_fresh, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        == HISTORICAL_FRESH_CONTENT_SHA,
        "historical complete fresh custody drift",
    )
    return raw_view, semantic_view


def qualify_historical_source(source_path, frozen_path, metadata_path, capture_path, backup_path, toc_path, dump_path):
    source, source_sha = read_witness(source_path)
    require("historical_snapshot" not in source, "source already has historical qualification")

    def text(path):
        require(
            path.is_file() and not path.is_symlink() and path.stat().st_size <= WITNESS_MAX_BYTES,
            "regular bounded evidence required",
        )
        return path.read_text()

    require(dump_path.is_file() and not dump_path.is_symlink(), "regular historical dump required")
    digest = hashlib.sha256()
    with dump_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    source["historical_snapshot"] = {
        "profile": HISTORICAL_PROFILE,
        "fresh_witness_sha256": source_sha,
        "frozen_source_raw": text(frozen_path),
        "metadata_raw": text(metadata_path),
        "capture_raw": text(capture_path),
        "backup_raw": text(backup_path),
        "toc_raw": text(toc_path),
        "dump_sha256": digest.hexdigest(),
    }
    checked(source, target=False)
    return source


def checked(snapshot, *, target):
    require(snapshot["version"] == VERSION, "unsupported witness")
    require(not target or "historical_snapshot" not in snapshot, "source-only historical profile on target")
    require(snapshot["database"] == ("verdify_rehearsal" if target else "verdify"), "wrong database")
    require(snapshot["server"] == (160013 if target else 160011), "wrong server version")
    semantic_ids = checked_catalog(snapshot["portable_catalog"])
    raw_ids = checked_catalog(snapshot["raw_portable_catalog_v2"], raw=True)
    if not target:
        require(
            catalog_sha256(historical_catalogs(snapshot)[0]) == FROZEN_SOURCE_V2_CATALOG_SHA256,
            "source raw v2 catalog differs from frozen ec9 witness",
        )
    facts = snapshot["portability_native_facts"]
    require(
        isinstance(facts, dict) and set(facts) == {"relations", "indexes", "constraints", "triggers"},
        "missing native portability facts",
    )
    require(all(isinstance(v, list) for v in facts.values()), "malformed native facts")
    relation_ids = [("relation", f["identity"]) for f in facts["relations"]]
    trigger_ids = [("trigger", f["raw_identity"]) for f in facts["triggers"]]
    require(
        len(set(relation_ids)) == len(relation_ids) and set(relation_ids) == {i for i in raw_ids if i[0] == "relation"},
        "raw relation custody incomplete",
    )
    require(
        len(set(trigger_ids)) == len(trigger_ids) and set(trigger_ids) == {i for i in raw_ids if i[0] == "trigger"},
        "raw trigger custody incomplete",
    )
    for field, kind in [("indexes", "index"), ("constraints", "constraint")]:
        ids = [(kind, f["identity"]) for f in facts[field]]
        require(
            len(set(ids)) == len(ids) and set(ids) == {i for i in raw_ids if i[0] == kind},
            "raw index/constraint custody incomplete",
        )
    typed_count = 0
    for fact in facts["triggers"]:
        t, x, fn = fact["native"], fact["constraint"], fact["function"]
        native_fk = (
            t["tgisinternal"] is True
            and isinstance(x, dict)
            and x.get("contype") == "f"
            and re.fullmatch(r"RI_ConstraintTrigger_[ac]_" + str(t["oid"]), t["tgname"]) is not None
            and snapshot["namespaces"].get(str(fn["pronamespace"])) == "pg_catalog"
            and int(fn["oid"]) < 16384
            and fn["pronargs"] == 0
            and int(fn["prorettype"]) == 2279
            and fact["language"] == "internal"
            and fn["prosrc"] == fn["proname"]
            and fn["proname"] in RI_FUNCTIONS
            and str(t["tgconstraint"]) == str(x["oid"])
            and str(t["tgfoid"]) == str(fn["oid"])
        )
        require(fact["typed_fk"] is native_fk, "unproved generated FK normalization")
        typed_count += native_fk
    trigger_by_oid = {str(f["native"]["oid"]): f for f in facts["triggers"]}
    require(len(trigger_by_oid) == len(facts["triggers"]), "duplicate native trigger OID")
    for fact in facts["triggers"]:
        if not fact["typed_fk"]:
            continue
        seen = {str(fact["native"]["oid"])}
        parent = str(fact["native"]["tgparentid"])
        while parent != "0":
            require(parent not in seen and parent in trigger_by_oid, "missing/cyclic FK parent custody")
            seen.add(parent)
            ancestor = trigger_by_oid[parent]
            require(ancestor["typed_fk"] is True, "unproved native FK parent")
            parent = str(ancestor["native"]["tgparentid"])

    def untouched(rows):
        return {(r[0], r[1]): r[2] for r in rows if r[0] not in {"relation", "trigger", "foreign-key-trigger"}}

    require(
        untouched(snapshot["portable_catalog"]) == untouched(snapshot["raw_portable_catalog_v2"]),
        "uncontrolled semantic catalog drift",
    )
    require(typed_count == sum(i[0] == "foreign-key-trigger" for i in semantic_ids), "typed FK multiplicity drift")
    require(len(raw_ids) == len(semantic_ids), "portable projection lost multiplicity")
    validate_projection_facts(snapshot, trigger_by_oid)
    roles, namespaces = snapshot["roles"], snapshot["namespaces"]
    require(len(set(roles.values())) == len(roles), "ambiguous role map")
    require(len(set(namespaces.values())) == len(namespaces), "ambiguous namespace map")
    expected = {*boundary.LOGINS, "verdify_mcp_runtime_login"}
    require(set(snapshot["boundaries"]) == expected, "incomplete sealed boundary inventory")
    ordinary = dict(snapshot["seals"]["ordinary"])
    require(
        set(ordinary) == set(boundary.LOGINS) and len(snapshot["seals"]["ordinary"]) == 2, "unexpected ordinary seals"
    )
    require(len(snapshot["seals"]["mcp"]) == 1, "unexpected MCP seals")
    predecessor = [row for row in snapshot["ledger"] if row[0] == "db/migrations" and row[2] == 268]
    require(
        predecessor
        == [
            [
                "db/migrations",
                "db/migrations/268-six-runtime-workload-role-boundaries.sql",
                268,
                MIGRATION_268_SHA,
                "runner",
            ]
        ],
        "exact268 runner predecessor required",
    )
    semantic = {}
    for login, data in snapshot["boundaries"].items():
        body = ordinary_projection(login)[0] if login in ordinary else mcp_projection()[0]
        expected_body_sha = hashlib.sha256(body.encode()).hexdigest()
        require(
            data["installed_body_sha256"] == data["expected_body_sha256"] == expected_body_sha,
            "installed digest body drift",
        )
        require(data["installed_shape_verified"] is True, "digest implementation posture drift")
        require(data["raw_entries"] and all(isinstance(e, str) for e in data["raw_entries"]), "empty catalog")
        digest = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
        require(digest == data["native"], "independent projection/native mismatch")
        if not target:
            seal = ordinary[login] if login in ordinary else snapshot["seals"]["mcp"][0]
            require(seal == digest, "source receipt is stale")
        if login in ordinary:
            require(
                sorted(e for e in data["raw_entries"] if e.startswith("member|"))
                == sorted(e for e in data["semantic_entries"] if e.startswith("member|")),
                "semantic membership differs from native raw membership",
            )
        semantic[login] = sorted(
            data["semantic_entries"]
            if login in ordinary
            else [semantic_mcp(e, roles, namespaces) for e in data["raw_entries"]]
        )
    return semantic


def bootstrap_profile(source, target):
    marker = target.get("bootstrap_grantor_profile")
    if marker is None:
        return None
    require(marker == BOOTSTRAP_PROFILE, "unsupported target bootstrap grantor profile")
    require(source["roles"].get("10") == "verdify", "frozen source OID10 bootstrap fact required")
    require(
        target["roles"].get("10") == "postgres"
        and target.get("bootstrap_identity") == {"oid": 10, "name": "postgres", "superuser": True},
        "native target OID10 bootstrap fact required",
    )
    return {
        "profile": BOOTSTRAP_PROFILE,
        "source_oid": 10,
        "source_name": "verdify",
        "target_oid": 10,
        "target_name": "postgres",
    }


def translate_bootstrap_memberships(source, target, left, right):
    profile = bootstrap_profile(source, target)
    if profile is None:
        return right, []
    translated, changes = {}, []
    for login, entries in right.items():
        translated[login] = []
        ordinary = login in boundary.LOGINS
        for entry in entries:
            parts = entry.split("|")
            slot = 3
            marker = "grantor=postgres" if ordinary else "postgres"
            if parts[0] == "member" and parts[slot] == marker:
                require(len(parts) == 7, "unexpected member projection shape")
                if ordinary:
                    require(
                        entry in target["boundaries"][login]["raw_entries"],
                        "semantic member differs from native raw member",
                    )
                    parts[slot] = "grantor=verdify"
                else:
                    require(
                        any(
                            raw.split("|")[0] == "member"
                            and raw.split("|")[3] == "10"
                            and semantic_mcp(raw, target["roles"], target["namespaces"]) == entry
                            for raw in target["boundaries"][login]["raw_entries"]
                        ),
                        "member grantor not native bootstrap OID10",
                    )
                    parts[slot] = "verdify"
                counterpart = "|".join(parts)
                require(counterpart in left[login], "bootstrap change has no exact source membership counterpart")
                changes.append({"login": login, "source": counterpart, "target": entry})
                entry = counterpart
            translated[login].append(entry)
        translated[login].sort()
    require(changes, "bootstrap profile has no native grantor delta")
    return translated, changes


def raw_catalog_delta(source, target):
    left = {(r[0], r[1]): r[2] for r in source["raw_portable_catalog_v2"]}
    right = {(r[0], r[1]): r[2] for r in target["raw_portable_catalog_v2"]}
    return {
        "removed": [[*k, left[k]] for k in sorted(left.keys() - right.keys())],
        "added": [[*k, right[k]] for k in sorted(right.keys() - left.keys())],
        "changed": [
            {"identity": list(k), "source": left[k], "target": right[k]}
            for k in sorted(left.keys() & right.keys())
            if left[k] != right[k]
        ],
    }


def compare(source, target):
    left, right = checked(source, target=False), checked(target, target=True)
    require(source["ledger"] == target["ledger"], "original ledger identity changed")
    require(source["seals"] == target["seals"], "original C0 seals changed")
    require(
        source["portable_catalog"] and historical_catalogs(source)[1] == target["portable_catalog"],
        "full workload object/ACL/definition catalog drift",
    )
    physical_semantic_equal = left == right
    translated, changes = translate_bootstrap_memberships(source, target, left, right)
    require(left == translated, "semantic boundary drift beyond qualified typed target profile")
    return {
        "version": VERSION,
        "frozen_source_v2_sha256": FROZEN_SOURCE_V2_SHA256,
        "source_raw_v2_catalog_sha256": catalog_sha256(source["raw_portable_catalog_v2"]),
        "historical_source_profile": (source.get("historical_snapshot") or {}).get("profile"),
        "historical_source_catalog_sha256": catalog_sha256(historical_catalogs(source)[0]),
        "target_raw_v2_catalog_sha256": catalog_sha256(target["raw_portable_catalog_v2"]),
        "raw_catalogs_equal": source["raw_portable_catalog_v2"] == target["raw_portable_catalog_v2"],
        "raw_portable_catalog_differences": raw_catalog_delta(source, target),
        "semantic_boundaries_equal": True,
        "physical_semantic_boundaries_equal": physical_semantic_equal,
        "bootstrap_grantor_profile": bootstrap_profile(source, target),
        "raw_bootstrap_grantor_differences": changes,
        "original_ledger_and_seals_retained": True,
        "runtime_transition_installed": False,
        "target_native_digests": {k: v["native"] for k, v in target["boundaries"].items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="store_true")
    parser.add_argument("--bootstrap-grantor-profile", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--restored", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--qualify-historical-source", action="store_true")
    for field in ["frozen-source", "chunk-metadata", "chunk-capture", "backup-custody", "dump-toc", "historical-dump"]:
        parser.add_argument("--" + field, type=Path)
    args = parser.parse_args()
    require(
        args.qualify_historical_source
        or not any(
            [
                args.frozen_source,
                args.chunk_metadata,
                args.chunk_capture,
                args.backup_custody,
                args.dump_toc,
                args.historical_dump,
            ]
        ),
        "historical evidence flags require explicit qualification",
    )
    if args.qualify_historical_source:
        require(
            args.source and not args.restored and not args.target and not args.bootstrap_grantor_profile,
            "historical source qualification only",
        )
        paths = [
            args.frozen_source,
            args.chunk_metadata,
            args.chunk_capture,
            args.backup_custody,
            args.dump_toc,
            args.historical_dump,
        ]
        require(all(paths), "complete native historical evidence required")
        content = (
            json.dumps(qualify_historical_source(args.source, *paths), separators=(",", ":"), ensure_ascii=False) + "\n"
        )
        require(len(content.encode()) <= WITNESS_MAX_BYTES, "qualified historical witness exceeds bound")
    elif args.source or args.restored:
        require(args.source and args.restored and not args.target, "both comparison witnesses required")
        source, source_sha = read_witness(args.source)
        target, target_sha = read_witness(args.restored)
        result = compare(source, target) | {"source_witness_sha256": source_sha, "target_witness_sha256": target_sha}
        content = json.dumps(result, indent=2) + "\n"
    else:
        content = emit_sql(args.target, bootstrap_grantor_profile=args.bootstrap_grantor_profile)
    with args.output.open("x") as stream:
        stream.write(content)


if __name__ == "__main__":
    main()
