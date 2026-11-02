#!/usr/bin/env python3
"""Rolled-back native qualification of a receipt-resealing migration (VD-30 rule).

A migration that changes runtime grants carries the api, ingestor and MCP seal
digests its grants produce, and those hash production role OIDs and grants, so
only the production catalog can measure them. This runs three psql sessions on
that database, each one transaction that ends in ROLLBACK:

  probe   the file up to its ``DO $successor$`` block, then the live digests:
          the successor literals the file must carry.
  final   the exact file, then its receipts and digests read inside the
          transaction (``inside_transaction_receipts_match``).
  after   read only: the live digests and receipts again (``after_rollback``).

Nothing commits: the SQL holds no COMMIT, a self-committing file is refused
before any session starts, ON_ERROR_STOP ends psql on the first error and the
server rolls the open transaction back on disconnect. ``--probe-only`` stops
after the probe. Otherwise the fixture is written only when the file's
literals equal the probe, the final run's receipts match and every value read
after the rollback equals its value before it.

    python3 scripts/qualify-migration-successor.py \\
        db/migrations/280-desired-state-activity-log.sql \\
        --fixture tests/fixtures/280-desired-state-native-qualification.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PSQL = "bash scripts/verdify-db.sh prod"
PSQL_FLAGS = ["-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-f", "-"]
BOUNDS = "SET LOCAL statement_timeout = '120s';\nSET LOCAL lock_timeout = '10s';\n"
STATE = """SELECT 'QUALIFY {label} ' || pg_catalog.json_build_object(
    'api', encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex'),
    'ingestor', encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex'),
    'mcp', encode(public.fn_mcp_runtime_boundary_digest(), 'hex'),
    'api_receipt', (SELECT encode(boundary_sha256, 'hex') FROM public.runtime_ordinary_login_attestation_receipts
                     WHERE login_name = 'verdify_api_runtime_login'),
    'ingestor_receipt', (SELECT encode(boundary_sha256, 'hex') FROM public.runtime_ordinary_login_attestation_receipts
                          WHERE login_name = 'verdify_ingestor_runtime_login'),
    'mcp_receipt', (SELECT encode(boundary_sha256, 'hex') FROM public.mcp_runtime_boundary_receipt WHERE singleton),
    'max_seq', (SELECT max(seq) FROM public.schema_migrations WHERE source = 'db/migrations'))::text;
"""
SUCCESSOR_LITERALS = {
    "api": r"fn_runtime_ordinary_boundary_digest\('verdify_api_runtime_login'\),'hex'\) IS DISTINCT FROM '([0-9a-f]{64})'",
    "ingestor": (
        r"fn_runtime_ordinary_boundary_digest\('verdify_ingestor_runtime_login'\),'hex'\)"
        r" IS DISTINCT FROM '([0-9a-f]{64})'"
    ),
    "mcp": r"fn_mcp_runtime_boundary_digest\(\),'hex'\) IS DISTINCT FROM '([0-9a-f]{64})'",
}
SEALS = ("api", "ingestor", "mcp")
TRANSACTION_CONTROL = {"ABORT", "BEGIN", "COMMIT", "END", "PREPARE", "RELEASE", "ROLLBACK", "SAVEPOINT", "START"}


class QualificationError(RuntimeError):
    pass


def _safety():
    name = "check_migration_rollback_safety"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/check_migration_rollback_safety.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # its dataclass resolves annotations through sys.modules
        spec.loader.exec_module(module)
    return sys.modules[name]


def split_successor(sql: str) -> tuple[str, str]:
    """Return (everything before the successor block, the successor block)."""
    if sql.count("END $predecessor$;") != 1 or sql.count("DO $successor$") != 1:
        raise QualificationError("the file needs exactly one $predecessor$ and one $successor$ block")
    head, tail = sql.split("DO $successor$")
    if "END $predecessor$;" not in head:
        raise QualificationError("the predecessor block must precede the successor block")
    return head, "DO $successor$" + tail


def successor_literals(sql: str) -> dict[str, str]:
    block = split_successor(sql)[1]
    found = {}
    for seal, pattern in SUCCESSOR_LITERALS.items():
        values = re.findall(pattern, block)
        if len(values) != 1:
            raise QualificationError(f"the successor block must assert one {seal} digest")
        found[seal] = values[0]
    return found


def transaction(body: str, *, read_only: bool = False) -> str:
    sql = ("BEGIN READ ONLY;\n" if read_only else "BEGIN;\n") + BOUNDS + body
    if not sql.endswith("\n"):
        sql += "\n"
    return sql + "ROLLBACK;\n"


def sessions(sql: str) -> dict[str, str]:
    head, _ = split_successor(sql)
    return {
        "probe": transaction(STATE.format(label="before") + head + "\n" + STATE.format(label="successor")),
        "final": transaction(sql + "\n" + STATE.format(label="inside")),
        "after": transaction(STATE.format(label="after"), read_only=True),
    }


def run(psql: list[str], sql: str) -> dict[str, dict]:
    result = subprocess.run(
        psql + PSQL_FLAGS, input=sql, text=True, capture_output=True, cwd=ROOT, timeout=300, check=False
    )
    if result.returncode != 0:
        # The database error names the failed statement; it carries no secret.
        raise QualificationError("psql session failed: " + result.stderr.strip()[-2000:])
    states = {}
    for line in result.stdout.splitlines():
        if line.startswith("QUALIFY "):
            _, label, payload = line.split(" ", 2)
            states[label] = json.loads(payload)
    return states


def seals(state: dict) -> dict[str, str]:
    return {seal: state[seal] for seal in SEALS}


def receipts_match(state: dict) -> bool:
    return all(state[seal] is not None and state[seal] == state[seal + "_receipt"] for seal in SEALS)


def refuse_transaction_control(sql: str) -> None:
    """Only the session may begin and end the transaction: no top-level control or psql meta-command."""
    if any(line.lstrip().startswith("\\") for line in sql.splitlines()):
        raise QualificationError("a psql meta-command cannot run inside the qualification session")
    for statement in _safety().strip_sql_noise(sql).split(";"):
        words = statement.split()
        if words and words[0].upper() in TRANSACTION_CONTROL:
            raise QualificationError(f"top-level {words[0].upper()} would end the rolled-back transaction")


def qualify(migration: Path, fixture: Path | None, psql: list[str], *, probe_only: bool = False) -> dict:
    if _safety().classify(migration).self_committing:
        raise QualificationError("a self-committing migration cannot be qualified inside a rolled-back transaction")
    raw = migration.read_bytes()
    sql = raw.decode()
    refuse_transaction_control(sql)
    plan = sessions(sql)
    probe = run(psql, plan["probe"])
    before, successor = probe["before"], probe["successor"]
    if not receipts_match(before):
        raise QualificationError("the live receipts do not equal the live digests before the probe")
    measured = seals(successor)
    print("probe successor digests: " + json.dumps(measured, sort_keys=True))
    literals = successor_literals(sql)
    if probe_only:
        return {"qualified_successor": measured, "file_literals": literals}
    if literals != measured:
        raise QualificationError(
            "the file's successor literals differ from the probe; set them (and the c0-migration-delivery.py and "
            "bootstrap-six-runtime-roles.py registrations) to the measured digests, then run again"
        )
    inside = run(psql, plan["final"])["inside"]
    after = run(psql, plan["after"])["after"]
    report = {
        "migration": migration.name,
        "measured_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "before": seals(before),
        "qualified_successor": measured,
        "after_rollback": seals(after),
        "exact_final_migration_sha256": hashlib.sha256(raw).hexdigest(),
        "final_migration_qualification_pending": False,
        "inside_transaction_receipts_match": receipts_match(inside) and seals(inside) == measured,
        "final_migration_rollback_verified": after == before,
        "native_probe_source_sha256": hashlib.sha256(plan["probe"].encode()).hexdigest(),
        "qualification_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if not (report["inside_transaction_receipts_match"] and report["final_migration_rollback_verified"]):
        raise QualificationError("native qualification failed: " + json.dumps(report, sort_keys=True))
    if fixture is not None:
        existing = json.loads(fixture.read_text()) if fixture.exists() else {}
        fixture.write_text(json.dumps({**existing, **report}, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("migration", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--psql", default=DEFAULT_PSQL, help=f"command prefix that runs psql (default: {DEFAULT_PSQL})")
    args = parser.parse_args(argv)
    try:
        report = qualify(args.migration, args.fixture, shlex.split(args.psql), probe_only=args.probe_only)
    except (OSError, ValueError, KeyError, QualificationError, subprocess.TimeoutExpired) as exc:
        print(f"qualification refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
