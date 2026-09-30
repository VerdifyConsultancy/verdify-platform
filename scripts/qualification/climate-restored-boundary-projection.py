"""Emit clone-only read-only projections; retain raw entries and role-map provenance.

Every role name and ACL OID must resolve. Caller must compare complete predecessor
entries before treating projected successors as qualified. This never stamps receipts.
"""

import argparse
import importlib.util
import json
import pathlib
import re

a = argparse.ArgumentParser(description="Read-only restored-catalog projection, never a production receipt refresh")
a.add_argument("--role-map", type=pathlib.Path, required=True)
a.add_argument("--output", type=pathlib.Path, required=True)
args = a.parse_args()
p = args.output
p.mkdir(parents=True, exist_ok=True)
s = importlib.util.spec_from_file_location("boundary", "scripts/ordinary-boundary-diff.py")
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
roles = next(json.loads(line) for line in args.role_map.read_text().splitlines() if line.startswith("{"))
roles = {n: int(o) for n, o in roles.items()}
if not roles or not all(isinstance(n, str) and n and o > 0 for n, o in roles.items()):
    raise ValueError("invalid source role map")
if len(set(roles.values())) != len(roles):
    raise ValueError("ambiguous source role OID")
values = ",".join("('{}',{})".format(name.replace("'", "''"), oid) for name, oid in sorted(roles.items()))
mapexpr = (
    "CASE WHEN acl.grantee=0 THEN 0 ELSE (SELECT source.oid FROM (VALUES "
    + values
    + ") source(name,oid) JOIN pg_roles restored ON restored.rolname=source.name WHERE restored.oid=acl.grantee) END"
)
for label, login in [("api", "verdify_api_runtime_login"), ("ingestor", "verdify_ingestor_runtime_login")]:
    _, cte = m.source_projection(login)
    for translated in (False, True):
        c = cte
        if translated:
            c, n = re.subn(r"(format\('%s:%s:%s',\s*)acl.grantee", lambda match: match.group(1) + mapexpr, c)
            if n != 8:
                raise ValueError("unexpected source ACL projection shape")
            c, n = re.subn(r"ORDER BY acl.grantee", lambda _: "ORDER BY " + mapexpr, c)
            if n != 8:
                raise ValueError("unexpected source ACL projection shape")
            c = c.replace(
                "database_row.datname,",
                "CASE WHEN database_row.datname=current_database() THEN 'verdify' ELSE database_row.datname END,",
            )
        guard = (
            "DO $guard$ BEGIN IF current_database() NOT IN ('verdify_rehearsal','climate_265_clone_b') OR inet_server_addr() IS NOT NULL OR current_setting('listen_addresses')<>'' THEN RAISE EXCEPTION 'disposable socket-only projection required'; END IF; IF EXISTS(SELECT 1 FROM pg_roles restored LEFT JOIN (VALUES "
            + values
            + ") source(name,oid) ON source.name=restored.rolname WHERE source.name IS NULL) OR EXISTS(SELECT 1 FROM (VALUES "
            + values
            + ") source(name,oid) LEFT JOIN pg_roles restored ON restored.rolname=source.name WHERE restored.oid IS NULL) THEN RAISE EXCEPTION 'unknown or missing role; projection refused'; END IF; IF EXISTS(SELECT 1 FROM (SELECT grantee FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a UNION ALL SELECT grantee FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a UNION ALL SELECT grantee FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a UNION ALL SELECT grantee FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) a UNION ALL SELECT grantee FROM pg_attribute t CROSS JOIN LATERAL aclexplode(t.attacl) a) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee WHERE acl.grantee<>0 AND r.oid IS NULL) THEN RAISE EXCEPTION 'unknown ACL grantee OID'; END IF; END $guard$; "
            if translated
            else ""
        )
        sql = (
            "BEGIN READ ONLY; "
            + guard
            + " SET LOCAL search_path=pg_catalog,pg_temp; SET LOCAL statement_timeout='30s'; "
            + c
            + " SELECT jsonb_build_object('entries',jsonb_agg(entry ORDER BY entry),'hash',encode(public.digest(string_agg(entry,E'\\n' ORDER BY entry),'sha256'),'hex')) FROM security_entries; COMMIT;"
        )
        (p / (f"{label}-" + ("mapped" if translated else "raw") + ".sql")).write_text(sql)
