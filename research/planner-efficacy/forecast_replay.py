"""Frozen #780 SQL replay. No network/production connection or credential lookup.

emit-sql only prints a bounded, read-only export for an authorized DB collector.
replay starts its own private-socket PostgreSQL and executes repository SQL there.
Raw exports and per-decision outputs belong outside Git, never on the public lab.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/242-outdoor-forecast-verification.sql"
TABLES = {
    "climate": {
        "ts": "timestamptz",
        "outdoor_temp_f": "float8",
        "outdoor_rh_pct": "float8",
        "solar_irradiance_w_m2": "float8",
        "vpd_avg": "float8",
    },
    "weather_forecast": {
        "ts": "timestamptz",
        "fetched_at": "timestamptz",
        "temp_f": "float8",
        "rh_pct": "float8",
        "vpd_kpa": "float8",
        "solar_w_m2": "float8",
        "cloud_cover_pct": "float8",
    },
    # Preserve the old view's actual input, not an invented raw-climate proxy.
    "v_climate_merged": {
        "bucket": "timestamptz",
        "outdoor_temp_f": "float8",
        "vpd_avg": "float8",
        "solar_w_m2": "float8",
    },
}


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timestamps require an explicit UTC offset")
    return result.astimezone(UTC)


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def export_sql(decisions):
    """One MVCC snapshot, explicit column allowlist, no raw identifiers/credentials."""
    times = sorted(timestamp(value) for value in decisions)
    if not times or times[-1] - times[0] > timedelta(days=7):
        raise ValueError("provide decisions spanning at most seven days")
    start = times[0].replace(minute=0, second=0, microsecond=0) - timedelta(days=30, hours=1)
    end = times[-1] + timedelta(hours=24)
    fields = []
    for table, columns in TABLES.items():
        time_field = next(iter(columns))
        scope = "greenhouse_id = 'vallery' AND " if table != "v_climate_merged" else ""
        fields.append(
            f"'{table}', (SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), '[]') "
            f"FROM (SELECT {', '.join(columns)} FROM public.{table} WHERE {scope}"
            f"{time_field} >= {literal(start.isoformat())}::timestamptz "
            f"AND {time_field} < {literal(end.isoformat())}::timestamptz) r)"
        )
    # v_climate_merged is historically unscoped. Refuse multi-house input rather
    # than claim its old metrics belong to a single greenhouse.
    scope_checks = " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM public.{table} WHERE greenhouse_id IS DISTINCT FROM 'vallery' "
        f"AND ts >= {literal(start.isoformat())}::timestamptz AND ts < {literal(end.isoformat())}::timestamptz)"
        for table in ("climate", "weather_forecast")
    )
    return (
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        "SET LOCAL statement_timeout = '120s';\nSET LOCAL timezone = 'UTC';\n"
        "SELECT jsonb_build_object('export_contract', 1, 'greenhouse_id', 'vallery', "
        "'captured_at', statement_timestamp(), 'snapshot', txid_current_snapshot()::text, "
        f"'requested_decisions', {literal(json.dumps([t.isoformat() for t in times]))}::jsonb, "
        f"'window_start', {literal(start.isoformat())}, 'window_end', {literal(end.isoformat())}, "
        f"'single_house_inputs', {scope_checks}, " + ", ".join(fields) + ");\nCOMMIT;\n"
    )


def validate_bundle(bundle):
    if not isinstance(bundle, dict):
        raise TypeError("expected an export object")
    if bundle.get("export_contract") != 1 or bundle.get("greenhouse_id") != "vallery":
        raise ValueError("unsupported export contract or scope")
    if bundle.get("single_house_inputs") is not True:
        raise ValueError("legacy unscoped views require verified single-house inputs")
    captured = timestamp(bundle["captured_at"])
    decisions = sorted(timestamp(value) for value in bundle["requested_decisions"])
    if not decisions or decisions[-1] > captured or decisions[-1] - decisions[0] > timedelta(days=7):
        raise ValueError("invalid decision window or decision after capture")
    start, end = timestamp(bundle["window_start"]), timestamp(bundle["window_end"])
    required_start = decisions[0].replace(minute=0, second=0, microsecond=0) - timedelta(days=30, hours=1)
    if start != required_start or end != decisions[-1] + timedelta(hours=24):
        raise ValueError("export bounds do not cover the declared replay window")
    for table, columns in TABLES.items():
        if not isinstance(bundle[table], list):
            raise TypeError("expected arrays of allowlisted rows")
        for row in bundle[table]:
            if set(row) != set(columns):
                raise ValueError("unexpected or missing export columns")
            for name, kind in columns.items():
                value = row[name]
                if kind == "timestamptz":
                    parsed = timestamp(value)
                    if name != "fetched_at" and not start <= parsed < end:
                        raise ValueError("row outside export bounds")
                elif value is not None:
                    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                        raise ValueError("expected numeric observation")
                    number = float(value)
                    # PostgreSQL JSON represents nonfinite floats as strings.
                    row[name] = number if math.isfinite(number) else str(number)
    return decisions


@contextmanager
def private_postgres(bin_dir):
    """Never accepts a DSN or reuses an existing database, even under PG* env."""
    cluster = Path(tempfile.mkdtemp(prefix="forecast-pg-"))
    pg_bin = Path(bin_dir).resolve()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["LC_ALL"] = "C"

    def run(args, **kwargs):
        result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=180, check=False, **kwargs)
        if result.returncode:
            # PostgreSQL diagnostics can echo input rows. Do not disclose them.
            raise RuntimeError(f"private PostgreSQL command failed: {Path(args[0]).name}")
        return result.stdout.strip()

    started = False
    try:
        run(
            [
                str(pg_bin / "initdb"),
                "-D",
                str(cluster / "data"),
                "-U",
                "forecast_fixture",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        started = True  # A failed/timeout start must not trigger blind directory removal.
        run(
            [
                str(pg_bin / "pg_ctl"),
                "-D",
                str(cluster / "data"),
                "-l",
                str(cluster / "server.log"),
                "-o",
                f"-k {cluster} -c listen_addresses='' -p 55473",
                "-w",
                "start",
            ]
        )

        def query(sql):
            return run(
                [
                    str(pg_bin / "psql"),
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    str(cluster),
                    "-p",
                    "55473",
                    "-U",
                    "forecast_fixture",
                    "-d",
                    "postgres",
                    "-qAt",
                ],
                input="SET search_path=public,pg_catalog; SET timezone='UTC';\n" + sql,
            )

        yield query
    finally:
        if started:
            run([str(pg_bin / "pg_ctl"), "-D", str(cluster / "data"), "-m", "fast", "-w", "stop"])
        # Remove only the private cluster created above; preserve it if stop fails.
        shutil.rmtree(cluster)


def replay(query, bundle):
    decisions = validate_bundle(bundle)
    source = (ROOT / "db/migrations/101-data-trust-and-outcome-views.sql").read_text()
    baseline = source[source.index("CREATE OR REPLACE VIEW v_forecast_accuracy AS") : source.index("-- Iris context")]
    legacy = (ROOT / "db/migrations/049-operator-views.sql").read_text()
    legacy = legacy[legacy.index("CREATE OR REPLACE VIEW v_forecast_vs_actual AS") : legacy.index("-- 5. v_cost_today")]
    correction = (ROOT / "db/migrations/050-operator-improvements.sql").read_text()
    correction = correction[
        correction.index("CREATE OR REPLACE FUNCTION fn_forecast_correction") : correction.index("-- 2. v_active_plan")
    ]
    migration = MIGRATION.read_text()
    execute = query

    def query(sql):
        return execute("SET search_path=public,pg_catalog; SET timezone='UTC';\n" + sql)

    query("""
CREATE ROLE verdify_ingestor_runtime;
CREATE TABLE replay_clock (decision_at timestamptz);
CREATE FUNCTION public.now() RETURNS timestamptz LANGUAGE sql STABLE
AS $$ SELECT decision_at FROM public.replay_clock $$;
CREATE FUNCTION time_bucket(interval, timestamptz) RETURNS timestamptz
LANGUAGE sql IMMUTABLE AS $$ SELECT date_bin($1, $2, timestamptz '1970-01-01 UTC') $$;
""")
    for table, columns in TABLES.items():
        declarations = ", ".join(f"{name} {kind}" for name, kind in columns.items())
        payload = literal(json.dumps(bundle[table], allow_nan=False))
        query(
            f"CREATE TABLE {table} ({declarations}); "
            f"INSERT INTO {table} SELECT * FROM jsonb_to_recordset({payload}::jsonb) AS r({declarations});"
        )
    query(baseline + legacy + correction)

    def read_rows(sql):
        return json.loads(
            query(f"SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), '[]') FROM ({sql}) r;")
        )

    def capture():
        results = []
        for decision in decisions:
            query(f"TRUNCATE replay_clock; INSERT INTO replay_clock VALUES ({literal(decision.isoformat())});")
            results.append(
                {
                    "decision_at": decision.isoformat(),
                    "lead_buckets": read_rows("SELECT * FROM v_forecast_accuracy_lead_buckets"),
                    "daily": read_rows("SELECT * FROM v_forecast_accuracy_daily"),
                    "corrections": read_rows(
                        "SELECT c.* FROM (VALUES ('temp_f'), ('vpd_kpa'), ('solar_w_m2')) p(param) "
                        "CROSS JOIN LATERAL fn_forecast_correction(p.param, 24) c"
                    ),
                }
            )
        return results

    before = capture()
    identities = query("""SELECT jsonb_agg(jsonb_build_array(c.relname, c.oid, c.relowner, c.relacl::text)
        ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='v';""")
    query("BEGIN;\n" + migration + "\nROLLBACK;")
    if capture() != before or query("SELECT to_regclass('public.v_forecast_outdoor_pairs') IS NULL;") != "t":
        raise RuntimeError("outer migration rollback did not restore baseline")
    restored_identities = query("""SELECT jsonb_agg(jsonb_build_array(c.relname, c.oid, c.relowner, c.relacl::text)
        ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='v';""")
    if restored_identities != identities:
        raise RuntimeError("outer migration rollback changed baseline view identities")
    query("BEGIN;\n" + migration + "\nCOMMIT;")
    after = capture()
    for result, decision in zip(after, decisions, strict=True):
        query(f"TRUNCATE replay_clock; INSERT INTO replay_clock VALUES ({literal(decision.isoformat())});")
        result["priors"] = read_rows("SELECT * FROM v_forecast_planning_priors")
    return {
        "replay_contract": 1,
        "verification_contract_version": 2,
        "postgres_version": query("SHOW server_version;"),
        "outer_rollback_restored_baseline": True,
        "export_metadata": {
            key: bundle[key]
            for key in ("captured_at", "snapshot", "greenhouse_id", "window_start", "window_end", "requested_decisions")
        },
        "source_sha256": {
            "migration_242": digest(migration.encode()),
            "baseline_101_slice": digest(baseline.encode()),
            "baseline_049_view": digest(legacy.encode()),
            "baseline_050_function": digest(correction.encode()),
        },
        "row_counts": {table: len(bundle[table]) for table in TABLES},
        "baseline": before,
        "corrected": after,
        "limitations": [
            "Offline snapshot replay, not deployed/live acceptance or a production restore.",
            "Unmodified SQL binds now() to a private replay clock; time_bucket uses UTC date_bin.",
            "Forecast availability uses fetched_at, not unrecorded provider issuance.",
            "Observation timestamps are not ingestion availability: retrospective corrections/late rows may leak.",
            "Snapshot export bounds are not proof of raw retention or sensor coverage.",
            "Legacy DISTINCT ON conflicting ties are unspecified by the old source SQL.",
            "Old correction's second argument means observation age; new one means forecast lead.",
            "Old and corrected denominators/windows differ; these are not interchangeable estimates.",
            "Diagnostic priors only; no control retuning or locked-study changes are authorized.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    emit = sub.add_parser("emit-sql")
    emit.add_argument("--decision", action="append", required=True)
    local = sub.add_parser("replay")
    local.add_argument("--input", type=Path, required=True)
    local.add_argument("--pg-bin", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "emit-sql":
            print(export_sql(args.decision), end="")
        else:
            raw = args.input.read_bytes()
            bundle = json.loads(raw)
            validate_bundle(bundle)
            with private_postgres(args.pg_bin) as query:
                report = replay(query, bundle)
            report["input_sha256"] = digest(raw)
            report["replay_source_sha256"] = digest(Path(__file__).read_bytes())
            print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired):
        # Malformed input/connection diagnostics must not echo raw operational data.
        parser.exit(2, "forecast replay failed; verify input contract and private PostgreSQL prerequisites\n")


if __name__ == "__main__":
    main()
