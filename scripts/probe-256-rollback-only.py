#!/usr/bin/env python3
"""Emit the pinned migration-256 live digest probe; never apply a migration.

The emitted SQL wraps the unsealed migration in one transaction, reads the
candidate successor ordinary-login digests, rolls back, then proves the
predecessor catalog/ledger/receipts survived. Invoke only after the attended
writer window ends, with psql ON_ERROR_STOP=1. A failed psql exits and its
connection closes, which aborts the open transaction; rerun a separate
read-only rollback proof in that case.

  set -o pipefail
  python3 scripts/probe-256-rollback-only.py | \
    scripts/verdify-db.sh prod -X -qAt -v ON_ERROR_STOP=1
"""

from __future__ import annotations

import hashlib
import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/256-experiment-v2-end-study-recovery-completion.sql"
EXPECTED_SHA256 = "6abff30f4bceb8cb629afed38299c6a5d320c07e1201c2be80799d688033ff25"
PREDECESSORS = {
    "verdify_api_runtime_login": "a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62",
    "verdify_ingestor_runtime_login": "5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true",
                        help="emit only the read-only post-rollback proof")
    args = parser.parse_args()
    if not args.verify_only:
        source = MIGRATION.read_bytes()
        if hashlib.sha256(source).hexdigest() != EXPECTED_SHA256:
            raise SystemExit("migration-256 source changed; review and repin the probe")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"),
             "--check", str(MIGRATION)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise SystemExit(f"migration is not proven wrap-safe: {result.stderr or result.stdout}")
        if "UPDATE PUBLIC.RUNTIME_ORDINARY_LOGIN_ATTESTATION_RECEIPTS" in source.decode().upper():
            raise SystemExit("probe source already contains successor receipt writes")

        print("BEGIN;")
        print("SET LOCAL lock_timeout = '2s';")
        print("SET LOCAL statement_timeout = '30s';")
        print("SET LOCAL idle_in_transaction_session_timeout = '15s';")
        print(source.decode())
        print("SELECT 'PROBE_API' AS label, encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex') AS digest;")
        print("SELECT 'PROBE_INGESTOR' AS label, encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex') AS digest;")
        print("ROLLBACK;")
    print("BEGIN READ ONLY;")
    print("SET LOCAL statement_timeout = '15s';")
    print("DO $rollback_proof$")
    print("BEGIN")
    print("    IF EXISTS (SELECT 1 FROM public.schema_migrations WHERE source = 'db/migrations' AND seq >= 256)")
    print("       OR to_regprocedure('public.fn_experiment_v2_stamp_facility_safe_lease()') IS NOT NULL")
    print("       OR to_regprocedure('public.fn_experiment_v2_require_end_recovery_completion()') IS NOT NULL")
    print("       OR EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'experiment_v2_facility_safe_closures' AND column_name = 'closed_lease_generation')")
    print("       OR EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgname IN ('trg_experiment_v2_stamp_facility_safe_lease', 'trg_experiment_v2_end_recovery_completion'))")
    print("       OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2")
    for login, digest in PREDECESSORS.items():
        print(f"       OR (SELECT encode(boundary_sha256, 'hex') FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name = '{login}') IS DISTINCT FROM '{digest}'")
        print(f"       OR encode(public.fn_runtime_ordinary_boundary_digest('{login}'), 'hex') IS DISTINCT FROM '{digest}'")
    print("    THEN RAISE EXCEPTION 'migration-256 rollback proof failed'; END IF;")
    print("END;")
    print("$rollback_proof$;")
    print("SELECT 'ROLLBACK_PROVED';")
    print("ROLLBACK;")


if __name__ == "__main__":
    main()
