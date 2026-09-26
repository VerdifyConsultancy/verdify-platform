"""Emit a rollback-only C0 catalog probe for an isolated production restore.

The default output targets the disposable logical-dump database
``verdify_rehearsal``. ``--physical-clone`` targets only the named offline
snapshot clone on its private Unix socket. No mode supplies a delivery contract
or updates the migration ledger/receipts.
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


def emit_sql(*, physical_clone: bool = False) -> str:
    sources = transition.checked_sources(transition.RESOURCE_VERSION)
    transition.require(len(sources) == 9, "exact nine-file source required")
    target = "verdify" if physical_clone else "verdify_rehearsal"
    clone_guard = (
        """OR current_setting('cluster_name') <> 'verdify-c0-contract-clone-20260926'
       OR current_setting('port') <> '55433'
       OR current_setting('listen_addresses') <> ''
       """
        if physical_clone
        else ""
    )
    sql = f"""-- C0 rollback-only projection; isolated restored database only.
\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout = '15min';
SET LOCAL lock_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '5min';
DO $c0_probe_target$
BEGIN
    IF current_database() <> '{target}'
       {clone_guard}
       OR current_setting('server_version_num')::integer < 160000
       OR current_setting('server_version_num')::integer >= 170000
       OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') <> '2.25.2'
       OR EXISTS (SELECT 1 FROM public.schema_migrations
                  WHERE source='db/migrations' AND seq BETWEEN 240 AND 248) THEN
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
        sql += "\nSET LOCAL search_path = pg_catalog, pg_temp;\n"
        for login in boundary.LOGINS:
            sql += f"\n\\echo C0_PROBE_{stage}_{login}\n{projection(login)}\n"
    sql += "\nROLLBACK;\n\\echo C0_PROBE_ROLLED_BACK\n"
    return sql


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--physical-clone", action="store_true")
    args = parser.parse_args(argv)
    try:
        content = emit_sql(physical_clone=args.physical_clone)
        with args.output.open("x") as stream:
            stream.write(content)
        print(f"C0 isolated probe SQL SHA256={hashlib.sha256(content.encode()).hexdigest()}; no database contacted.")
    except (OSError, ValueError, TypeError, OverflowError, AssertionError):
        print("C0 probe refused: invalid source or output; no SQL emitted.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
