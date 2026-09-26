"""Real PostgreSQL capture/ACL/rollback proof; not physical metric qualification."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/244-daily-climate-metric-revisions.sql"
spec = importlib.util.spec_from_file_location("scorecard_pg_fixture", ROOT / "tests/test_scorecard_semantics.py")
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
isolated_pg = fixture_module.isolated_pg

FIELDS = (
    "compliance_pct",
    "temp_compliance_pct",
    "vpd_compliance_pct",
    "stress_hours_heat",
    "stress_hours_cold",
    "stress_hours_vpd_high",
    "stress_hours_vpd_low",
    "compliance_v2_raw_pct",
    "compliance_v2_attributable_pct",
    "compliance_v2_unachievable_frac",
    "graded_temp_compliance_pct",
    "graded_vpd_compliance_pct",
    "graded_stress_hours_heat",
    "graded_stress_hours_cold",
    "graded_stress_hours_vpd_high",
    "graded_stress_hours_vpd_low",
)


def baseline(query):
    query("""
        CREATE ROLE verdify_api_runtime;
        CREATE ROLE verdify_ingestor_runtime;
        CREATE ROLE verdify_api_runtime_login;
        CREATE ROLE verdify_ingestor_runtime_login;
        GRANT USAGE ON SCHEMA public TO verdify_api_runtime, verdify_ingestor_runtime;
    """)
    query(
        "CREATE TABLE daily_summary (date date PRIMARY KEY, greenhouse_id text, captured_at timestamptz, "
        + ", ".join(f"{name} double precision" for name in FIELDS)
        + ", cost_total numeric);"
    )
    query("""
        GRANT SELECT, INSERT, UPDATE ON daily_summary TO verdify_ingestor_runtime;
        INSERT INTO daily_summary (date, greenhouse_id, compliance_pct, temp_compliance_pct,
                                   stress_hours_heat, compliance_v2_attributable_pct, captured_at)
        VALUES ('2026-09-04', 'vallery', 6.1, 0, NULL, 85.8, '2026-09-05T12:00:00Z');
    """)


def install(query):
    # Same outer transaction required of the normal migration runner.
    query("BEGIN;\n" + MIGRATION.read_text() + "\nCOMMIT;")


def ledger(query):
    return json.loads(query("SELECT jsonb_agg(to_jsonb(r) ORDER BY revision_id) FROM daily_climate_metric_revisions r"))


def test_capture_seed_updates_noops_key_changes_delete_and_missing_values(isolated_pg):
    query = isolated_pg
    baseline(query)
    before = query("SELECT row_to_json(d) FROM daily_summary d")
    install(query)
    assert query("SELECT row_to_json(d) FROM daily_summary d") == before
    seed = ledger(query)
    assert len(seed) == 1 and seed[0]["operation"] == "baseline"
    assert seed[0]["capture_schema"] == "daily-summary-capture-v1"
    assert seed[0]["metrics"]["binary"]["stress_hours_heat"] is None
    assert seed[0]["metrics"]["binary"]["temp_compliance_pct"] == 0
    assert seed[0]["metrics"]["graded"]["compliance_v2_attributable_pct"] == 85.8
    assert "cost_total" not in json.dumps(seed)
    query(
        "UPDATE daily_summary SET cost_total=7, captured_at=now(); UPDATE daily_summary SET compliance_pct=compliance_pct;"
    )
    assert ledger(query) == seed
    query("SET ROLE verdify_ingestor_runtime; UPDATE daily_summary SET compliance_pct=NULL; RESET ROLE;")
    rows = ledger(query)
    assert [r["operation"] for r in rows] == ["baseline", "before_update", "after_update"]
    assert rows[1]["metrics"]["binary"]["compliance_pct"] == 6.1
    assert rows[2]["metrics"]["binary"]["compliance_pct"] is None
    assert rows[1]["transaction_id"] == rows[2]["transaction_id"]
    query("UPDATE daily_summary SET greenhouse_id='second', date='2026-09-03', temp_compliance_pct='NaN';")
    rows = ledger(query)
    assert rows[-2]["greenhouse_id"] == "vallery" and rows[-2]["day"] == "2026-09-04"
    assert rows[-1]["greenhouse_id"] == "second" and rows[-1]["day"] == "2026-09-03"
    assert rows[-1]["metrics"]["binary"]["temp_compliance_pct"] == "NaN"  # preserve, never sanitize into zero
    query("DELETE FROM daily_summary;")
    assert ledger(query)[-1]["operation"] == "delete"
    assert query("SELECT count(*) FROM daily_summary") == "0"
    query("SET ROLE verdify_ingestor_runtime; INSERT INTO daily_summary(date) VALUES('2026-09-05'); RESET ROLE;")
    assert ledger(query)[-1]["greenhouse_id"] is None  # no invented default identity
    assert ledger(query)[-1]["operation"] == "insert"


def test_runtime_read_only_acl_definer_and_shadow_resistance(isolated_pg):
    query = isolated_pg
    baseline(query)
    install(query)
    assert (
        query("SET ROLE verdify_api_runtime; SELECT count(*) FROM daily_climate_metric_revisions; RESET ROLE;") == "1"
    )
    for role in (
        "verdify_api_runtime",
        "verdify_ingestor_runtime",
        "verdify_api_runtime_login",
        "verdify_ingestor_runtime_login",
    ):
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER"):
            assert (
                query(f"SELECT has_table_privilege('{role}', 'daily_climate_metric_revisions', '{privilege}')") == "f"
            )
        assert (
            query(f"SELECT has_sequence_privilege('{role}', 'daily_climate_metric_revisions_revision_id_seq', 'USAGE')")
            == "f"
        )
        assert (
            query(f"SELECT has_function_privilege('{role}', 'fn_capture_daily_climate_metric_revision()', 'EXECUTE')")
            == "f"
        )
    assert (
        query("SELECT prosecdef FROM pg_proc WHERE oid='fn_capture_daily_climate_metric_revision()'::regprocedure")
        == "t"
    )
    assert (
        query("SELECT proconfig[1] FROM pg_proc WHERE oid='fn_capture_daily_climate_metric_revision()'::regprocedure")
        == "search_path=pg_catalog, public, pg_temp"
    )
    assert (
        query(
            "SELECT proowner=(SELECT datdba FROM pg_database WHERE datname=current_database()) FROM pg_proc WHERE oid='fn_capture_daily_climate_metric_revision()'::regprocedure"
        )
        == "t"
    )
    query("""
        SET ROLE verdify_ingestor_runtime;
        CREATE TEMP TABLE daily_climate_metric_revisions (metrics jsonb);
        SET search_path=pg_temp, public;
        UPDATE public.daily_summary SET compliance_pct=10;
        RESET ROLE;
    """)
    assert query("SELECT count(*) FROM public.daily_climate_metric_revisions") == "3"


def test_ledger_rejects_even_owner_update_delete_truncate(isolated_pg):
    query = isolated_pg
    baseline(query)
    install(query)
    for statement in (
        "UPDATE daily_climate_metric_revisions SET metrics='{}'",
        "DELETE FROM daily_climate_metric_revisions",
        "TRUNCATE daily_climate_metric_revisions",
        "TRUNCATE daily_summary",
    ):
        query(f"""DO $$ BEGIN
            BEGIN {statement}; RAISE EXCEPTION 'mutation unexpectedly succeeded';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL; END;
        END $$;""")
    assert len(ledger(query)) == 1
    assert query("SELECT count(*) FROM daily_summary") == "1"


def test_failed_capture_rolls_back_source_and_partial_before_image(isolated_pg):
    query = isolated_pg
    baseline(query)
    install(query)
    query(
        "ALTER TABLE daily_climate_metric_revisions ADD CONSTRAINT fixture_fail CHECK(operation <> 'after_update') NOT VALID;"
    )
    query("""DO $$ BEGIN
        BEGIN UPDATE daily_summary SET compliance_pct=42;
              RAISE EXCEPTION 'source write unexpectedly succeeded';
        EXCEPTION WHEN check_violation THEN NULL; END;
    END $$;""")
    assert len(ledger(query)) == 1
    assert query("SELECT compliance_pct FROM daily_summary") == "6.1"


def test_outer_migration_rollback_preserves_daily_table_values_identity_and_acls(isolated_pg):
    query = isolated_pg
    baseline(query)
    old = query("SELECT oid::text || ':' || relacl::text FROM pg_class WHERE oid='daily_summary'::regclass")
    before = query("SELECT row_to_json(d) FROM daily_summary d")
    query("BEGIN;\n" + MIGRATION.read_text() + "\nUPDATE daily_summary SET compliance_pct=42; ROLLBACK;")
    assert query("SELECT to_regclass('public.daily_climate_metric_revisions') IS NULL") == "t"
    assert query("SELECT oid::text || ':' || relacl::text FROM pg_class WHERE oid='daily_summary'::regclass") == old
    assert query("SELECT row_to_json(d) FROM daily_summary d") == before
    assert query("SELECT count(*) FROM pg_trigger WHERE tgrelid='daily_summary'::regclass AND NOT tgisinternal") == "0"


def test_seed_and_trigger_install_hold_the_source_writer_lock(isolated_pg):
    query = isolated_pg
    baseline(query)
    result = query(
        "BEGIN;\n" + MIGRATION.read_text() + "\nSELECT count(*) FROM pg_locks "
        "WHERE pid=pg_backend_pid() AND relation='daily_summary'::regclass "
        "AND mode='ShareRowExclusiveLock' AND granted; ROLLBACK;"
    )
    assert result == "1"
