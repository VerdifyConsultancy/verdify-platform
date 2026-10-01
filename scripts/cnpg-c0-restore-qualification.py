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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("boundary", ROOT / "scripts/ordinary-boundary-diff.py")
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)
VERSION = "cnpg-c0-logical-recovery-witness-v2"
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


def portable_catalog_sql():
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


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_witness(path):
    # The actual post268 catalog contains >44k objects (12.3MB formatted).
    # Keep a separate bounded reader; do not widen the existing C0 contracts.
    require(path.is_file() and not path.is_symlink(), "regular witness required")
    with path.open("rb") as stream:
        raw = stream.read(32_000_001)
    require(len(raw) <= 32_000_000, "witness exceeds bound")
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


def emit_sql(target=False):
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
 'database_owner',(SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()),
 'database_acl',(SELECT jsonb_agg(jsonb_build_array(
      CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
      pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable) ORDER BY a.grantee,a.privilege_type)
      FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a WHERE d.datname=current_database()),
 'namespaces',(SELECT jsonb_object_agg(oid::text,nspname) FROM pg_namespace),
 'portable_catalog',({portable_catalog_sql()}),
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


def checked(snapshot, *, target):
    require(snapshot["version"] == VERSION, "unsupported witness")
    require(snapshot["database"] == ("verdify_rehearsal" if target else "verdify"), "wrong database")
    require(snapshot["server"] == (160013 if target else 160011), "wrong server version")
    catalog = snapshot["portable_catalog"]
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
        semantic[login] = sorted(
            data["semantic_entries"]
            if login in ordinary
            else [semantic_mcp(e, roles, namespaces) for e in data["raw_entries"]]
        )
    return semantic


def compare(source, target):
    left, right = checked(source, target=False), checked(target, target=True)
    require(source["ledger"] == target["ledger"], "original ledger identity changed")
    require(source["seals"] == target["seals"], "original C0 seals changed")
    require(
        source["portable_catalog"] and source["portable_catalog"] == target["portable_catalog"],
        "full workload object/ACL/definition catalog drift",
    )
    require(left == right, "semantic boundary drift beyond role OIDs/database name")
    return {
        "version": VERSION,
        "semantic_boundaries_equal": True,
        "original_ledger_and_seals_retained": True,
        "runtime_transition_installed": False,
        "target_native_digests": {k: v["native"] for k, v in target["boundaries"].items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--restored", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.source or args.restored:
        require(args.source and args.restored and not args.target, "both comparison witnesses required")
        source, source_sha = read_witness(args.source)
        target, target_sha = read_witness(args.restored)
        result = compare(source, target) | {"source_witness_sha256": source_sha, "target_witness_sha256": target_sha}
        content = json.dumps(result, indent=2) + "\n"
    else:
        content = emit_sql(args.target)
    with args.output.open("x") as stream:
        stream.write(content)


if __name__ == "__main__":
    main()
