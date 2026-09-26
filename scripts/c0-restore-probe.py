"""Emit a rollback-only C0 catalog probe for an isolated production-dump restore.

The output is SQL for a disposable database named ``verdify_rehearsal``. It
never supplies a delivery contract or updates the migration ledger/receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boundary = load("ordinary_boundary_diff", "ordinary-boundary-diff.py")
transition = load("c0_boundary_transition", "c0-boundary-transition.py")


def projection(login: str) -> str:
    # Use the pinned migration-217 projection and its independent equality
    # check, exactly as the read-only ordinary-boundary inspector does.
    full = boundary.emit_sql(login)
    prefix = "SET LOCAL lock_timeout = '2s';\n"
    start = full.index(prefix) + len(prefix)
    end = full.rindex("\nCOMMIT;")
    return full[start:end]


def emit_sql() -> str:
    sources = transition.checked_sources(transition.RESOURCE_VERSION)
    transition.require(len(sources) == 8, "exact eight-file source required")
    sql = """-- C0 rollback-only projection; isolated restored database only.
\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '15min';
SET LOCAL lock_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '5min';
DO $c0_probe_target$
BEGIN
    IF current_database() <> 'verdify_rehearsal'
       OR current_setting('server_version_num')::integer < 160000
       OR current_setting('server_version_num')::integer >= 170000
       OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') <> '2.25.2'
       OR EXISTS (SELECT 1 FROM public.schema_migrations
                  WHERE source='db/migrations' AND seq BETWEEN 241 AND 248) THEN
        RAISE EXCEPTION 'C0 probe requires an isolated PG16/Timescale 2.25.2 predecessor';
    END IF;
END;
$c0_probe_target$;
"""
    for stage in ("BEFORE", "AFTER"):
        if stage == "AFTER":
            sql += "\nSET LOCAL search_path = pg_catalog, public, pg_temp;\n"
            for name, sha, source in sources:
                sql += f"\n-- BEGIN EXACT SOURCE {name} SHA256 {sha}\n{source}\n-- END EXACT SOURCE {name}\n"
        for login in boundary.LOGINS:
            sql += f"\n\\echo C0_PROBE_{stage}_{login}\n{projection(login)}\n"
    sql += "\nROLLBACK;\n\\echo C0_PROBE_ROLLED_BACK\n"
    return sql


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        content = emit_sql()
        with args.output.open("x") as stream:
            stream.write(content)
        print(f"C0 isolated probe SQL SHA256={hashlib.sha256(content.encode()).hexdigest()}; no database contacted.")
    except (OSError, ValueError, TypeError, OverflowError, AssertionError):
        print("C0 probe refused: invalid source or output; no SQL emitted.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
