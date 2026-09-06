"""Owning-runner C0 delivery: exact contract, atomic transition, readback.

The normal runner delegates before bootstrap when its inventory includes any
C0 migration. There is no per-file fallback and no contract-generation mode.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c0_boundary_transition", ROOT / "scripts/c0-boundary-transition.py")
transition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition)
CORE_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT jsonb_build_object(
    'timescale', EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname='timescaledb'),
    'timescale_version', (SELECT extversion FROM pg_catalog.pg_extension WHERE extname='timescaledb'),
    'core', to_regclass('public.climate') IS NOT NULL
        AND to_regclass('public.setpoint_changes') IS NOT NULL
        AND to_regclass('public.equipment_state') IS NOT NULL);
COMMIT;"""
LEDGER_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT coalesce(jsonb_agg(jsonb_build_object('source',source,'filename',filename,
    'seq',seq,'sha256',sha256,'stamp_method',stamp_method) ORDER BY source,filename),'[]')
FROM public.schema_migrations;
COMMIT;"""


class DeliveryError(ValueError):
    """A locally authored, value-free diagnostic safe for the job log."""


def require(ok, message):
    if not ok:
        raise DeliveryError(message)


def inventory(directory):
    require(directory.is_dir(), "migration directory unavailable")
    files = {}
    for path in sorted(directory.glob("*.sql")):
        require(path.is_file() and not path.is_symlink(), "migration must be a regular file")
        require(re.fullmatch(r"[0-9][a-zA-Z0-9._-]*\.sql", path.name), "invalid migration filename")
        raw = path.read_bytes()
        installed = ROOT / "db/migrations" / path.name
        require(installed.is_file() and raw == installed.read_bytes(), "inventory differs from image source")
        files[path.name] = hashlib.sha256(raw).hexdigest()
    cohort = {name: sha for name, sha in files.items() if re.match(r"24[1-7]", name)}
    require(cohort == transition.MIGRATIONS, "complete exact C0 inventory required")
    transition.checked_sources()
    return files


def load_contract(environment, *, plan):
    filename = environment.get("VERDIFY_C0_BOUNDARY_CONTRACT", "")
    pin = environment.get("VERDIFY_C0_BOUNDARY_CONTRACT_SHA256", "")
    if plan and not filename and not pin:
        return None, None
    require(filename and pin, "C0 requires a reviewed contract and independent hash pin")
    require(transition.is_hash(pin), "invalid contract hash pin")
    contract, actual = transition.boundary.read_snapshot(Path(filename))
    require(actual == pin, "contract does not match reviewed hash pin")
    transition.validate(contract)
    return contract, actual


def psql(sql, environment):
    for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"):
        require(environment.get(key), "required database binding missing")
    env = dict(environment)
    env["PGPASSWORD"] = env["DB_PASS"]
    env["PGOPTIONS"] = (
        "-c statement_timeout=15min -c lock_timeout=30s "
        "-c idle_in_transaction_session_timeout=5min -c search_path=pg_catalog,public,pg_temp"
    )
    command = [
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        "VERBOSITY=verbose",
        "-h",
        env["DB_HOST"],
        "-p",
        env.get("DB_PORT", "5432"),
        "-U",
        env["DB_USER"],
        "-d",
        env["DB_NAME"],
    ]
    result = subprocess.run(command, input=sql, env=env, text=True, capture_output=True, timeout=1200)
    if result.returncode:
        # Server errors can contain row values, SQL context or credentials.
        # Preserve only a syntactically bounded SQLSTATE, never raw stdout/stderr.
        state = re.search(r"(?:ERROR|FATAL):\s+([A-Z0-9]{5}):", result.stderr)
        raise DeliveryError("database command refused" + (f" (SQLSTATE {state.group(1)})" if state else ""))
    return result.stdout.strip()


def ledger_rows(environment):
    raw = psql(LEDGER_SQL, environment)
    try:
        rows = json.loads(raw)
    except (ValueError, RecursionError):
        raise DeliveryError("invalid ledger readback") from None
    require(isinstance(rows, list), "invalid ledger readback")
    result = {}
    for row in rows:
        require(
            isinstance(row, dict) and set(row) == {"source", "filename", "seq", "sha256", "stamp_method"},
            "invalid ledger row",
        )
        require(isinstance(row["source"], str) and isinstance(row["filename"], str), "invalid ledger identity")
        key = (row["source"], row["filename"])
        require(key not in result, "duplicate ledger identity")
        result[key] = row
    return result


def verify_inventory_ledger(files, rows, *, after=False, version=transition.VERSION):
    migrations = transition.release_migrations(version)
    pending = 0
    for name, sha in files.items():
        row = rows.get(("db/migrations", "db/migrations/" + name))
        if name in migrations:
            if row is None:
                pending += 1
                require(not after, "C0 stamp missing after transaction")
            else:
                require(
                    row["sha256"] == sha and row["seq"] == int(name[:3]) and row["stamp_method"] == "runner",
                    "C0 stamp is not exact",
                )
        else:
            require(row is not None, "pending migration outside the qualified C0 bundle")
            require(
                row["sha256"] == sha or (row["sha256"] is None and row["stamp_method"] == "baseline"),
                "prior image/ledger source mismatch",
            )
    require(pending in (0, len(migrations)), "partial C0 release must not resume per-file")
    return pending


def successor_probe(contract):
    migrations = transition.release_migrations(contract["version"])
    member = (
        "source='db/migrations' AND filename IN ("
        + ",".join(transition.literal("db/migrations/" + name) for name in migrations)
        + ")"
    )
    return f"""BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog, pg_temp;
SET LOCAL statement_timeout = '30s';
DO $c0_delivery_readback$
DECLARE v_digest text;
BEGIN
    {transition.function_guard(contract["version"])}
    {transition.ledger_shape_guard()}
    IF ({transition.ledger_digest_sql("NOT (" + member + ")")})
        IS DISTINCT FROM '{contract["predecessor_ledger_sha256"]}'
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'C0 delivery readback refuses predecessor ledger or receipt count drift';
    END IF;
    {transition.digest_guard(contract["after"], contract["after"])}
END;
$c0_delivery_readback$;
COMMIT;"""


def deliver(directory, *, plan=False, environment=None):
    env = dict(os.environ if environment is None else environment)
    files = inventory(directory)
    contract, pin = load_contract(env, plan=plan)
    version = contract["version"] if contract else transition.VERSION
    migrations = transition.release_migrations(version)
    require(
        all(files.get(name) == sha for name, sha in migrations.items()),
        "complete exact selected release inventory required",
    )
    transition.checked_sources(version)
    # Preserve the entrypoint's existing core/Timescale guard after dispatch,
    # but never reach schema replay or repair when this prerequisite is missing.
    core = json.loads(psql(CORE_SQL, env))
    require(
        isinstance(core, dict) and core.get("timescale") is True and core.get("core") is True,
        "Timescale extension and existing core schema required",
    )
    if version == transition.RESOURCE_VERSION:
        require(core.get("timescale_version") == "2.25.2", "resource qualification requires TimescaleDB 2.25.2")
    rows = ledger_rows(env)
    pending = verify_inventory_ledger(files, rows, version=version)
    if plan:
        bundle = "241-248" if version == transition.RESOURCE_VERSION else "241-247"
        print(f"C0 PLAN: {pending} pending; atomic bundle={bundle}; contract_supplied={contract is not None}.")
        print("Read-only inventory check only; target fingerprints, execution and deployment remain unverified.")
        return
    sql = transition.emit_sql(contract, pin)
    sql_sha = hashlib.sha256(sql.encode()).hexdigest()
    print(f"C0 atomic delivery: contract_sha256={pin}; sql_sha256={sql_sha}", flush=True)
    psql(sql, env)
    verify_inventory_ledger(files, ledger_rows(env), after=True, version=version)
    psql(successor_probe(contract), env)
    count = "eight" if version == transition.RESOURCE_VERSION else "seven"
    print(f"C0 committed state verified: {count} exact stamps and both successor receipts/catalogs.")
    print("Ordinary application sessions, Argo health and live consumer adoption require separate verification.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrations-dir", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        deliver(args.migrations_dir, plan=args.plan)
    except (OSError, ValueError, TypeError, OverflowError, subprocess.TimeoutExpired) as exc:
        # Only locally authored diagnostics may be surfaced. Never echo paths,
        # subprocess arguments, contract values or raw DB errors from exceptions.
        suffix = " " + str(exc) + "." if isinstance(exc, DeliveryError) else ""
        print("C0 delivery refused or unverified; no per-file fallback." + suffix, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
