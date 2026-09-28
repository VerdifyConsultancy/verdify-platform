#!/usr/bin/env python3
"""Emit a pinned, rollback-only live 255 -> 256 -> draft-257 catalog probe.

This generator does not connect to a database. The operator reviews its SQL,
then runs it with psql ON_ERROR_STOP=1 after the attended writer window. Both
source files must match the reviewed hashes; the draft-257 fail-closed barrier
is removed in memory only. No synthetic day or publication row is inserted.

  set -o pipefail
  python3 scripts/probe-257-rollback-only.py | \
    scripts/verdify-db.sh prod -X -qAt -v ON_ERROR_STOP=1

If psql stops early, its disconnected transaction rolls back. Run the
--verify-only proof separately even if the first invocation reported success.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_256 = "db/migrations/256-experiment-v2-end-study-recovery-completion.sql"
MIGRATION_257 = "db/migrations/257-route-only-crop-band-publication.sql"
SHA_256 = "b35ed01468ef505dc46b035b6b3c15ee57ba96d6600183ffd98b9975d4fffe0f"
SHA_257_DRAFT = "a3a35779b8898ebf987fae3079a729d5c60315c239117c0b8d881f7a7e341c31"
PREDECESSORS = {
    "verdify_api_runtime_login": "a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62",
    "verdify_ingestor_runtime_login": "5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d",
}
POST_256 = {
    "verdify_api_runtime_login": "7c8b0d3f8dcfa8552068ca8373e0f394aafb3c27aab1fda72c7eebd3083c904a",
    "verdify_ingestor_runtime_login": "7783f5d743751224ae157fe063941c76e00167a0208cd233150a9b68b06633fa",
}
BARRIER = (
    "DO $unsealed$\n"
    "BEGIN\n"
    "    RAISE EXCEPTION 'migration 257 requires exact live boundary qualification';\n"
    "END;\n"
    "$unsealed$;\n"
)


def pinned_source(path: Path, expected_sha: str) -> str:
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != expected_sha:
        raise SystemExit(f"{path.name} changed; review and repin the probe")
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--check", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode:
        raise SystemExit(f"{path.name} is not proven wrap-safe: {check.stderr or check.stdout}")
    return source.decode("utf-8")


def proof(predecessors: dict[str, str]) -> None:
    print("BEGIN READ ONLY;")
    print("SET LOCAL lock_timeout = '2s';")
    print("SET LOCAL statement_timeout = '15s';")
    print("DO $rollback_proof$")
    print("BEGIN")
    print("    IF EXISTS (SELECT 1 FROM public.schema_migrations WHERE source = 'db/migrations' AND seq >= 256)")
    print("       OR to_regclass('public.route_only_crop_band_publications') IS NOT NULL")
    print("       OR to_regprocedure('public.fn_route_only_crop_band_diagnostic(date,text)') IS NOT NULL")
    print("       OR to_regprocedure('public.fn_guard_route_only_crop_band_publication()') IS NOT NULL")
    print("       OR to_regprocedure('public.fn_experiment_v2_stamp_facility_safe_lease()') IS NOT NULL")
    print("       OR to_regprocedure('public.fn_experiment_v2_require_end_recovery_completion()') IS NOT NULL")
    print(
        "       OR EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'experiment_v2_facility_safe_closures' AND column_name = 'closed_lease_generation')"
    )
    print(
        "       OR EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgname IN ('trg_experiment_v2_stamp_facility_safe_lease', 'trg_experiment_v2_end_recovery_completion'))"
    )
    print("       OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2")
    for login, digest in predecessors.items():
        print(
            "       OR (SELECT encode(boundary_sha256, 'hex') "
            "FROM public.runtime_ordinary_login_attestation_receipts "
            f"WHERE login_name = '{login}') IS DISTINCT FROM '{digest}'"
        )
        print(
            f"       OR encode(public.fn_runtime_ordinary_boundary_digest('{login}'), 'hex') "
            f"IS DISTINCT FROM '{digest}'"
        )
    print("    THEN RAISE EXCEPTION 'migration-257 chained rollback proof failed'; END IF;")
    print("END;")
    print("$rollback_proof$;")
    print("SELECT 'ROLLBACK_PROVED';")
    print("ROLLBACK;")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--migration-256", type=Path, default=ROOT / MIGRATION_256)
    args = parser.parse_args()
    if not args.verify_only:
        source_256 = pinned_source(args.migration_256, SHA_256)
        source_257 = pinned_source(ROOT / MIGRATION_257, SHA_257_DRAFT)
        if source_257.count(BARRIER) != 1:
            raise SystemExit("draft-257 fail-closed barrier shape changed")
        source_257 = source_257.replace(BARRIER, "", 1)
        print("BEGIN;")
        print("SET LOCAL lock_timeout = '2s';")
        print("SET LOCAL statement_timeout = '30s';")
        print("SET LOCAL idle_in_transaction_session_timeout = '15s';")
        print(source_256)
        print(
            "INSERT INTO public.schema_migrations "
            "(filename, source, seq, sha256, stamp_method, applied_at, duration_ms, applied_by) "
            f"VALUES ('{MIGRATION_256}', 'db/migrations', 256, '{SHA_256}', "
            "'runner', clock_timestamp(), 0, current_user);"
        )
        print("DO $pre_257$")
        print("BEGIN")
        for index, (login, digest) in enumerate(POST_256.items()):
            prefix = "    IF" if index == 0 else "       OR"
            print(
                f"{prefix} encode(public.fn_runtime_ordinary_boundary_digest('{login}'), 'hex') IS DISTINCT FROM '{digest}'"
            )
            print(
                "       OR (SELECT encode(boundary_sha256, 'hex') "
                "FROM public.runtime_ordinary_login_attestation_receipts "
                f"WHERE login_name = '{login}') IS DISTINCT FROM '{digest}'"
            )
        print("    THEN RAISE EXCEPTION 'exact post-256 predecessor changed'; END IF;")
        print("END;")
        print("$pre_257$;")
        print(source_257)
        print(
            "SELECT 'PROBE_257_API', encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex');"
        )
        print(
            "SELECT 'PROBE_257_INGESTOR', encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex');"
        )
        print("SELECT 'PROBE_257_ROWS', count(*) FROM public.route_only_crop_band_publications;")
        print(
            "SELECT 'PROBE_257_MISSING', unavailable_reason FROM public.fn_route_only_crop_band_diagnostic('2026-11-02'::date, 'vallery');"
        )
        print("ROLLBACK;")
    proof(PREDECESSORS)


if __name__ == "__main__":
    main()
