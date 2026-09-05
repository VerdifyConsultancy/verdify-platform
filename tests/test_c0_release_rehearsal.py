"""One private database, actual ledger runner, and the combined C0 contracts.

Synthetic baseline only: resource-view stand-ins, date_bin instead of Timescale,
synthetic crop anchors, and a legacy band-function dependency sentinel. This is
not a production restore, complete migration history, or physical qualification.
Set SCORECARD_TEST_PG_BIN to server/client binaries; no ambient DB is contacted.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import asyncpg
import pytest
from test_forecast_verification_contract import _baseline as forecast_baseline
from test_public_band_lineage import baseline as band_baseline
from test_scorecard_semantics import _baseline_sql as scorecard_baseline
from test_scorecard_semantics import isolated_pg as isolated_pg

from ingestor.climate_minute_metrics import refresh_observed_minute_metrics
from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.observed_minute_reader import read_observed_minute_evidence

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(sorted((ROOT / "db/migrations").glob("24[1-6]-*.sql")))
DAY = date(2026, 9, 4)


def replace_once(source, old, new):
    # Deliberate fixture composition, never silent rewriting of production SQL.
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def combined_baseline():
    band = replace_once(band_baseline(), "CREATE ROLE verdify_api_runtime;", "")
    forecast = replace_once(forecast_baseline(), "CREATE ROLE verdify_ingestor_runtime;", "")
    forecast = replace_once(
        forecast,
        "CREATE TABLE climate (ts timestamptz, outdoor_temp_f float8, outdoor_rh_pct float8,\n"
        "    solar_irradiance_w_m2 float8, vpd_avg float8);",
        "ALTER TABLE climate ADD COLUMN outdoor_temp_f float8, ADD COLUMN outdoor_rh_pct float8,\n"
        "    ADD COLUMN solar_irradiance_w_m2 float8;",
    )
    forecast = replace_once(
        forecast,
        "INSERT INTO climate SELECT",
        "INSERT INTO climate (ts, outdoor_temp_f, outdoor_rh_pct, solar_irradiance_w_m2, vpd_avg) SELECT",
    )
    return (
        scorecard_baseline()
        + band
        + forecast
        + """
CREATE ROLE verdify_api_runtime_login;
CREATE ROLE verdify_ingestor_runtime_login;
ALTER TABLE setpoint_changes ADD COLUMN source text, ADD COLUMN superseded_by_ts timestamptz;
GRANT USAGE ON SCHEMA public TO verdify_api_runtime, verdify_ingestor_runtime;
GRANT SELECT, INSERT, UPDATE ON daily_summary TO verdify_ingestor_runtime;
GRANT SELECT ON climate, setpoint_changes TO verdify_ingestor_runtime;
"""
    )


def snapshot(query):
    """DDL identities/ACLs/definitions and stored rows, not nontransactional sequences."""
    result = {}
    selections = {
        "relations": "SELECT oid, relname, relkind, relowner, relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace",
        "columns": "SELECT a.* FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace='public'::regnamespace",
        "constraints": "SELECT oid, conname, conrelid, pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE connamespace='public'::regnamespace",
        "functions": "SELECT oid, proowner, proacl::text, pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='public'::regnamespace",
        "triggers": "SELECT t.oid, pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='public'::regnamespace",
        "views": "SELECT oid, pg_get_viewdef(oid) AS definition FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('v','m')",
        "materialized_rows": "SELECT * FROM mv_daily_kpi",
        "daily": "SELECT * FROM daily_summary",
        "climate": "SELECT * FROM climate",
        "forecast": "SELECT * FROM weather_forecast",
        "setpoints": "SELECT * FROM setpoint_changes",
        "migration_ledger": "SELECT * FROM schema_migrations",
    }
    if query("SELECT to_regclass('daily_climate_metric_revisions') IS NOT NULL") == "t":
        selections["revisions"] = "SELECT * FROM daily_climate_metric_revisions"
    for name, sql in selections.items():
        result[name] = query(f"SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), '[]') FROM ({sql}) r")
    return result


@pytest.fixture
def rehearsal(isolated_pg, tmp_path):
    query = isolated_pg
    baseline = combined_baseline()
    query(baseline)
    query((ROOT / "db/ledger/schema_migrations.sql").read_text())
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    # Never stamp the real audited baseline onto a synthetic database. This
    # explicitly named fixture record is the only pre-existing ledger entry.
    fixture_sha = hashlib.sha256(baseline.encode()).hexdigest()
    query(f"""SELECT stamp_migration('qualification/synthetic-c0-baseline.sql',
        'db/migrations', NULL, '{fixture_sha}', 'manual');""")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PG", "DB_", "POSTGRES_", "VERDIFY_MIGR", "VERDIFY_LEDGER", "VERDIFY_SAFETY"))
        and k != "DATABASE_URL"
    }
    env.update(
        PATH=f"{os.environ['SCORECARD_TEST_PG_BIN']}:{env['PATH']}",
        DB_HOST=query("SHOW unix_socket_directories"),
        DB_PORT="55472",
        DB_NAME="postgres",
        DB_USER="scorecard_fixture",
        DB_PASS="private-synthetic-fixture-only",  # noqa: S106 - private trust-auth socket, not a credential
        VERDIFY_MIGRATIONS_DIR=str(migrations),
        VERDIFY_LEDGER_DIR=str(ROOT / "db/ledger"),
        VERDIFY_SAFETY_CLASSIFIER=str(ROOT / "scripts/check_migration_rollback_safety.py"),
        VERDIFY_MIGRATE_STATEMENT_TIMEOUT="10s",
        VERDIFY_MIGRATE_LOCK_TIMEOUT="2s",
        VERDIFY_MIGRATE_IDLE_TX_TIMEOUT="10s",
    )

    def run(*args):
        return subprocess.run(
            ["sh", str(ROOT / "db/apply-migrations.sh"), *args],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    assert len(MIGRATIONS) == 6
    return query, migrations, run


def copy_migrations(directory, paths=MIGRATIONS):
    for path in paths:
        shutil.copyfile(path, directory / path.name)


def assert_applied(query, paths=MIGRATIONS):
    rows = json.loads(
        query("""SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY filename), '[]')
        FROM schema_migrations m WHERE stamp_method='runner'""")
    )
    assert len(rows) == len(paths)
    for row, path in zip(rows, paths, strict=True):
        assert row["filename"] == f"db/migrations/{path.name}"
        assert row["source"] == "db/migrations"
        assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert row["seq"] == int(path.name.split("-")[0])
        assert row["applied_by"] == "scorecard_fixture" and row["duration_ms"] >= 0


def test_combined_runner_plan_apply_roles_consumers_and_noop(rehearsal):
    query, directory, run = rehearsal
    copy_migrations(directory)
    before = snapshot(query)
    plan = run("--plan")
    assert plan.returncode == 0, plan.stderr
    assert "6 pending, 0 already ledgered" in plan.stdout
    assert snapshot(query) == before
    assert query("SELECT value FROM fn_planner_scorecard('2026-09-04') WHERE metric='compliance_pct'") == "85.8"
    assert query("SELECT DISTINCT vpd_error_kpa FROM v_forecast_accuracy") == "-8.00"
    result = run()
    assert result.returncode == 0, result.stderr
    assert_applied(query)
    assert query("SELECT value FROM legacy_dependent") == "119"
    assert query("SELECT count(*) FROM fixture_kpi_row()") == "4"
    assert query("SELECT count(*) FROM daily_climate_metric_revisions WHERE operation='baseline'") == "4"

    async def consumers():
        conn = await asyncpg.connect(
            host=query("SHOW unix_socket_directories"),
            port=55472,
            user="scorecard_fixture",
            database="postgres",
            password="",
            ssl=False,
        )
        try:
            await conn.execute("SET ROLE verdify_api_runtime")
            assert (await read_observed_minute_evidence(conn, DAY)).unavailable_reason == "not_computed"
            card = ScorecardResponse.from_metric_rows(await conn.fetch("SELECT * FROM fn_planner_scorecard($1)", DAY))
            assert card.climate_evidence()["both_axis_compliance_pct"] == 6.1
            assert card.climate_evidence()["graded_compliance_attributable_pct"] == 85.8
            band = await conn.fetch("""SELECT * FROM fn_public_band_trace_v2(
                '2026-09-04T21:00Z', '2026-09-04T21:04Z', 'vallery')""")
            assert len(band) == 4
            assert [r["reconstructed_temp_in_band"] for r in band] == [True, False, None, None]
            for table in ("climate", "daily_summary", "weather_forecast", "setpoint_changes"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetch(f"SELECT * FROM {table} LIMIT 1")
            await conn.execute("RESET ROLE; SET ROLE verdify_ingestor_runtime")
            forecast = await conn.fetchrow("SELECT * FROM v_forecast_accuracy_lead_buckets WHERE param='vpd_kpa'")
            assert forecast["samples"] == 1 and forecast["bias"] == 0
            assert await conn.fetchval("SELECT samples FROM fn_forecast_correction('temp_f',3)") == 1
            # The actual writer's nested transaction must also support an
            # outer reconciliation dry run without leaving metric/capture rows.
            rollback = conn.transaction(isolation="repeatable_read")
            await rollback.start()
            try:
                await refresh_observed_minute_metrics(conn, DAY)
                assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == 6
            finally:
                await rollback.rollback()
            assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == 4
            assert (
                await conn.fetchval("SELECT climate_observed_minute_metrics FROM daily_summary WHERE date=$1", DAY)
                is None
            )
            diagnostic = await refresh_observed_minute_metrics(conn, DAY)
            assert diagnostic["expected_minutes"] == 1440
            assert diagnostic["axes"]["temp"]["eligible_minutes"] == 2
            assert diagnostic["axes"]["vpd"]["eligible_minutes"] == 2
            assert diagnostic["joint"]["eligible_minutes"] == 1
            for role in ("verdify_ingestor_runtime", "verdify_api_runtime"):
                await conn.execute(f"RESET ROLE; SET ROLE {role}")
                for sql in (
                    "UPDATE daily_climate_metric_revisions SET metrics='{}'",
                    "DELETE FROM daily_climate_metric_revisions",
                    "TRUNCATE daily_climate_metric_revisions",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.execute(sql)
            evidence = await read_observed_minute_evidence(conn, DAY)
            assert evidence.availability == "available"
            assert evidence.capture_schema == "daily-summary-capture-v2"
            assert evidence.diagnostic.axes.temp.in_band_pct == 50
            assert evidence.diagnostic.axes.vpd.in_band_pct == 50
            assert evidence.diagnostic.joint.in_band_pct == 100
            assert not evidence.diagnostic.experiment_endpoint_eligible
            assert not evidence.diagnostic.physical_proof_eligible
            assert not evidence.diagnostic.crop_outcome_eligible
            assert evidence.diagnostic.worst_measured_zone is None
            card.observed_minute_evidence = evidence
            assert card.climate_evidence()["both_axis_compliance_pct"] == 6.1
            assert (
                card.climate_evidence()["observed_minute_evidence"]["diagnostic"]["axes"]["temp"]["in_band_pct"] == 50
            )
        finally:
            await conn.close()

    asyncio.run(consumers())
    stable = snapshot(query)
    result = run()
    assert result.returncode == 0, result.stderr
    assert "0 pending, 6 already ledgered" in result.stdout
    assert snapshot(query) == stable
    # Edited applied source must fail before applying/stamping anything else.
    with (directory / MIGRATIONS[0].name).open("a") as file:
        file.write("\n-- synthetic post-apply drift\n")
    drift = run()
    assert drift.returncode != 0 and "edited after it was applied" in drift.stderr
    assert snapshot(query) == stable


def test_real_populated_empty_ledger_refuses_without_inventing_history(rehearsal):
    query, directory, run = rehearsal
    copy_migrations(directory)
    query("DELETE FROM schema_migrations")  # disposable synthetic ledger only
    before = snapshot(query)
    result = run()
    assert result.returncode != 0 and "populated DB with EMPTY ledger" in result.stderr
    assert snapshot(query) == before
    plan = run("--plan")
    assert plan.returncode == 0 and "REQUIRES VERDIFY_MIGRATE_ALLOW_BASELINE=1" in plan.stdout
    assert snapshot(query) == before


@pytest.mark.parametrize("failed_index", range(6), ids=[p.name[:3] for p in MIGRATIONS])
def test_each_migration_failure_rolls_back_without_stamp_and_can_resume(rehearsal, failed_index):
    query, directory, run = rehearsal
    prefix = MIGRATIONS[:failed_index]
    if prefix:
        copy_migrations(directory, prefix)
        result = run()
        assert result.returncode == 0, result.stderr
    assert_applied(query, prefix)
    before = snapshot(query)
    copy_migrations(directory)
    # Fault only the scratch copy AFTER all migration statements have executed.
    # Later migrations are also pending: failure must stop before those apply.
    with (directory / MIGRATIONS[failed_index].name).open("a") as file:
        file.write("\nDO $$ BEGIN RAISE EXCEPTION 'c0 synthetic end-of-file failure'; END $$;\n")
    result = run()
    assert result.returncode != 0 and "c0 synthetic end-of-file failure" in result.stderr
    assert snapshot(query) == before
    assert_applied(query, prefix)
    copy_migrations(directory)
    resumed = run()
    assert resumed.returncode == 0, resumed.stderr
    assert_applied(query)
