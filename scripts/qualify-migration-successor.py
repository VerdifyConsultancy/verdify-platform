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

A successor whose merged predecessors are not applied in production yet names
each of them with ``--apply-first``, in filename order. The probe and final
sessions then apply each one before the successor as db/apply-migrations.sh
applies a wrap-safe file (the ledger advisory lock, the start of its timer, the
not-yet-ledgered guard, the file, then ``stamp_migration(..., 'runner', ...)``)
and set the session bounds again after it. Each predecessor's own
``$predecessor$`` and ``$successor$`` blocks must pass, its receipts must equal
its digests, and both sessions must read the same state before the successor
runs (``applied_first_seals``). ``--native-fixture`` runs a check file after the
successor in the final session; it reports one ``QUALIFY native_fixture {json}``
line with ``"all_passed": true``.

Nothing commits. The successor, each predecessor and the native fixture are
refused before any session starts if they commit on their own or hold top-level
transaction control or a psql meta-command. Each session sets a 120 s statement
timeout and a 10 s lock timeout before any file runs and again after each
predecessor, ON_ERROR_STOP ends psql on the first error, and the server rolls
the open transaction back on disconnect. ``--probe-only`` stops after the probe.
Otherwise the fixture is written only when the file's literals equal the probe,
the final run's receipts match, the native fixture passed and every value read
after the rollback equals its value before it. A rewritten fixture keeps its
projection but drops ``applied_first`` and native-fixture evidence that this
run did not produce.

    python3 scripts/qualify-migration-successor.py \\
        db/migrations/280-desired-state-activity-log.sql \\
        --fixture tests/fixtures/280-desired-state-native-qualification.json

    python3 scripts/qualify-migration-successor.py \\
        db/migrations/281-irrigation-minutes-by-zone.sql \\
        --apply-first db/migrations/280-desired-state-activity-log.sql \\
        --native-fixture db/qualification/281-irrigation-minutes-fixture.sql \\
        --fixture tests/fixtures/281-irrigation-minutes-native-qualification.json
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
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db/migrations"
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
# db/apply-migrations.sh accepts these names only, so they quote safely in SQL.
MIGRATION_NAME = re.compile(r"[0-9][a-zA-Z0-9._-]*\.sql")
# The runner's per-file wrapper around a wrap-safe file, in its order: its
# advisory lock, the clock it times the file from, its not-yet-ledgered guard,
# the file, then the ledger stamp with the duration and the applying user.
RUNNER_LOCK = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('verdify-schema-migrations'));\n"
RUNNER_GUARD = """SELECT pg_catalog.set_config('verdify_qualify.t0', pg_catalog.clock_timestamp()::text, true);
DO $qualify_apply_first$
BEGIN
    IF EXISTS (SELECT 1 FROM public.schema_migrations
                WHERE source = 'db/migrations' AND filename = '{rel}') THEN
        RAISE EXCEPTION '{rel} is already applied: qualify without --apply-first';
    END IF;
END
$qualify_apply_first$;
"""
RUNNER_STAMP = """SELECT public.stamp_migration(
    '{rel}', 'db/migrations', {seq}, '{sha}', 'runner',
    (extract(epoch from pg_catalog.clock_timestamp()
        - pg_catalog.current_setting('verdify_qualify.t0')::timestamptz) * 1000)::int,
    current_user);
"""
NATIVE_FIXTURE_LINE = "'QUALIFY native_fixture '"
# Evidence only a run with --apply-first or --native-fixture records. A fixture
# keeps its projection across runs but never this evidence from an earlier one.
OPTION_EVIDENCE = ("applied_first", "applied_first_seals", "native_fixture", "native_fixture_sha256")


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


def seq_of(name: str) -> int | None:
    """The runner's ledger seq: the leading number (095a -> 95), or NULL."""
    match = re.match(r"([0-9]+)[a-zA-Z]?-", name)
    return int(match.group(1)) if match else None


def refuse_self_committing(path: Path) -> None:
    if _safety().classify(path).self_committing:
        raise QualificationError(f"{path.name} commits on its own and cannot run inside a rolled-back transaction")


def applied_first(path: Path) -> tuple[dict, str]:
    """A merged predecessor that production lacks, applied and stamped as the runner would, then the bounds again."""
    if (
        not MIGRATION_NAME.fullmatch(path.name)
        or path.is_symlink()
        or not path.is_file()
        or path.resolve().parent != MIGRATIONS.resolve()
    ):
        raise QualificationError(f"--apply-first takes a migration file in db/migrations, not {path}")
    refuse_self_committing(path)
    raw = path.read_bytes()
    sql = raw.decode()
    refuse_transaction_control(sql)
    rel, seq, sha = f"db/migrations/{path.name}", seq_of(path.name), hashlib.sha256(raw).hexdigest()
    block = (
        RUNNER_LOCK
        + RUNNER_GUARD.format(rel=rel)
        + sql
        + ("" if sql.endswith("\n") else "\n")
        + RUNNER_STAMP.format(rel=rel, seq="NULL" if seq is None else seq, sha=sha)
        + BOUNDS
    )
    return {"migration": path.name, "seq": seq, "sha256": sha}, block


def native_checks(path: Path) -> str:
    """A check file for the final session, after the successor; it reports one QUALIFY native_fixture line."""
    refuse_self_committing(path)
    sql = path.read_text()
    refuse_transaction_control(sql)
    if sql.count(NATIVE_FIXTURE_LINE) != 1:
        raise QualificationError("the native fixture must report exactly one 'QUALIFY native_fixture {json}' line")
    return sql


def sessions(sql: str, first: Sequence[str] = (), checks: str | None = None) -> dict[str, str]:
    head, _ = split_successor(sql)
    applied = "".join(first) + (STATE.format(label="applied") if first else "")
    tail = "" if checks is None else checks + ("" if checks.endswith("\n") else "\n")
    return {
        "probe": transaction(STATE.format(label="before") + applied + head + "\n" + STATE.format(label="successor")),
        "final": transaction(applied + sql + "\n" + STATE.format(label="inside") + tail),
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
            if label in states:
                raise QualificationError(f"the session reported {label} twice")
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


def qualify(
    migration: Path,
    fixture: Path | None,
    psql: list[str],
    *,
    probe_only: bool = False,
    apply_first: Sequence[Path] = (),
    native_fixture: Path | None = None,
) -> dict:
    if _safety().classify(migration).self_committing:
        raise QualificationError("a self-committing migration cannot be qualified inside a rolled-back transaction")
    raw = migration.read_bytes()
    sql = raw.decode()
    refuse_transaction_control(sql)
    names = [path.name for path in apply_first]
    if names != sorted(set(names)) or any(name >= migration.name for name in names):
        raise QualificationError("--apply-first names each unapplied predecessor once, in filename order")
    first = [applied_first(path) for path in apply_first]
    checks = None if native_fixture is None else native_checks(native_fixture)
    plan = sessions(sql, [block for _, block in first], checks)
    probe = run(psql, plan["probe"])
    before, successor = probe["before"], probe["successor"]
    if not receipts_match(before):
        raise QualificationError("the live receipts do not equal the live digests before the probe")
    applied = probe["applied"] if first else None
    if first:
        last_seq = first[-1][0]["seq"]
        if not receipts_match(applied) or (last_seq is not None and applied["max_seq"] != last_seq):
            raise QualificationError("the predecessors applied first did not leave matching receipts and their stamp")
    measured = seals(successor)
    print("probe successor digests: " + json.dumps(measured, sort_keys=True))
    literals = successor_literals(sql)
    if probe_only:
        result = {"qualified_successor": measured, "file_literals": literals}
        if first:
            result["applied_first_seals"] = seals(applied)
        return result
    if literals != measured:
        raise QualificationError(
            "the file's successor literals differ from the probe; set them (and the c0-migration-delivery.py and "
            "bootstrap-six-runtime-roles.py registrations) to the measured digests, then run again"
        )
    final = run(psql, plan["final"])
    inside = final["inside"]
    if first and final.get("applied") != applied:
        raise QualificationError("the final session read a different state after the predecessors than the probe")
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
    if first:
        report["applied_first"] = [record for record, _ in first]
        report["applied_first_seals"] = seals(applied)
    passed = report["inside_transaction_receipts_match"] and report["final_migration_rollback_verified"]
    if native_fixture is not None:
        report["native_fixture"] = final.get("native_fixture")
        report["native_fixture_sha256"] = hashlib.sha256(native_fixture.read_bytes()).hexdigest()
        passed = passed and (report["native_fixture"] or {}).get("all_passed") is True
    if not passed:
        raise QualificationError("native qualification failed: " + json.dumps(report, sort_keys=True))
    if fixture is not None:
        existing = json.loads(fixture.read_text()) if fixture.exists() else {}
        kept = {key: value for key, value in existing.items() if key not in OPTION_EVIDENCE}
        fixture.write_text(json.dumps({**kept, **report}, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("migration", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument(
        "--apply-first",
        type=Path,
        action="append",
        default=[],
        metavar="MIGRATION",
        help="a merged predecessor not yet applied in production; repeat in filename order",
    )
    parser.add_argument("--native-fixture", type=Path, help="checks run after the successor in the final session")
    parser.add_argument("--psql", default=DEFAULT_PSQL, help=f"command prefix that runs psql (default: {DEFAULT_PSQL})")
    args = parser.parse_args(argv)
    try:
        report = qualify(
            args.migration,
            args.fixture,
            shlex.split(args.psql),
            probe_only=args.probe_only,
            apply_first=args.apply_first,
            native_fixture=args.native_fixture,
        )
    except (OSError, ValueError, KeyError, QualificationError, subprocess.TimeoutExpired) as exc:
        print(f"qualification refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
