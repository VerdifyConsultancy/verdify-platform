#!/usr/bin/env python3
"""Emit a read-only projection of the migration-217 ordinary-login catalog.

The verifier hashes sorted ``security_entries`` text, including numeric role
OIDs in ACL entries. Logical restores allocate different OIDs, so a clone hash
is not a production successor seal. This emits the same catalog assembly as
the installed verifier without creating or replacing a database object.

Example:
  python3 scripts/probe-ordinary-boundary-catalog.py verdify_api_runtime_login \
    | scripts/verdify-db.sh prod -X -q -v ON_ERROR_STOP=1 > /tmp/verdify-api-catalog.txt 2>&1

The output has one CATALOG_B64 notice. Decode it locally, normalize only
reviewed clone-versus-live role OIDs, and require the normalized predecessor
hash to equal the sealed production receipt before deriving a successor.
"""

from __future__ import annotations

import argparse
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "db/migrations/217-runtime-role-boundary.sql"
)
FUNCTION_HEAD = (
    "CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest("
)
RETURN_STMT = "RETURN public.digest(coalesce(v_catalog, ''), 'sha256');"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "login",
        choices=("verdify_api_runtime_login", "verdify_ingestor_runtime_login"),
    )
    args = parser.parse_args()
    source = SOURCE.read_text()
    function = source.split(FUNCTION_HEAD, 1)[1]
    body = function.split("AS $body$\n", 1)[1].split("\n$body$;", 1)[0]
    if not body.startswith("DECLARE\n") or body.count(RETURN_STMT) != 1:
        raise SystemExit("migration-217 catalog verifier source shape changed")
    body = body.replace("DECLARE\n", f"DECLARE\n    p_login_name text := '{args.login}';\n", 1)
    body = body.replace(
        RETURN_STMT,
        "RAISE NOTICE 'CATALOG_B64:%', "
        "replace(encode(convert_to(coalesce(v_catalog, ''), 'UTF8'), "
        "'base64'), E'\\n', '');",
        1,
    )
    print("BEGIN READ ONLY;")
    print("SET LOCAL search_path = pg_catalog, pg_temp;")
    print("SET LOCAL lock_timeout = '2s';")
    print("SET LOCAL statement_timeout = '30s';")
    print("DO $projection$")
    print(body)
    print("$projection$;")
    print("ROLLBACK;")


if __name__ == "__main__":
    main()
