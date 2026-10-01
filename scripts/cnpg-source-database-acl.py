"""Restore the witnessed database ACL onto the disposable CNPG database only.

No C0 receipt, function, role, OID or source migration row is modified. The
source witness must have passed the independent source-pinned/native digest
checks, and its exact bytes must match the coordinator's custody hash.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("c0_witness", ROOT / "scripts/cnpg-c0-restore-qualification.py")
c0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c0)


def identifier(name):
    c0.require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name), "unsupported role name")
    return '"' + name + '"'


def emit_sql(source):
    c0.checked(source, target=False)
    owner = source["database_owner"]
    c0.require(owner == "verdify", "unexpected source database owner")
    rows = source["database_acl"]
    c0.require(isinstance(rows, list) and rows, "explicit source database ACL required")
    c0.require(len({tuple(row) for row in rows}) == len(rows), "duplicate source database privileges")
    grants = []
    for grantee, grantor, privilege, grant_option in rows:
        c0.require(
            grantor == owner and privilege in {"CONNECT", "CREATE", "TEMPORARY"} and type(grant_option) is bool,
            "unsupported source database authority",
        )
        c0.require(grantee == "PUBLIC" or grantee in source["roles"].values(), "unknown source grantee")
        role = "PUBLIC" if grantee == "PUBLIC" else identifier(grantee)
        grants.append(
            f"GRANT {privilege} ON DATABASE verdify_rehearsal TO {role}"
            + (" WITH GRANT OPTION" if grant_option else "")
            + ";"
        )
    # Create the same ACL through supported DDL, under its original grantor.
    # Clearing the new DB defaults affects this disposable database alone.
    return (
        """\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='30s';
SET LOCAL lock_timeout='2s';
DO $guard$ BEGIN
 IF current_database()<>'verdify_rehearsal'
    OR current_setting('cluster_name')<>'verdify-cnpg-rehearsal'
    OR current_setting('server_version_num')::int<>160013
    OR inet_client_addr() IS NOT NULL OR pg_is_in_recovery()
    OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())<>'verdify' THEN
   RAISE EXCEPTION 'source ACL restoration refuses target identity';
 END IF;
END $guard$;
SET LOCAL ROLE verdify;
REVOKE ALL ON DATABASE verdify_rehearsal FROM PUBLIC;
REVOKE ALL ON DATABASE verdify_rehearsal FROM verdify;
"""
        + "\n".join(grants)
        + "\nCOMMIT;\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, sha = c0.read_witness(args.source)
    c0.require(sha == args.sha256, "source witness custody hash mismatch")
    with args.output.open("x") as stream:
        stream.write(emit_sql(source))


if __name__ == "__main__":
    main()
