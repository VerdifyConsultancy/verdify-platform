"""#780: real SQL regression for outdoor truth, vintage selection and priors."""

import json
from pathlib import Path

from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/242-outdoor-forecast-verification.sql"


def _baseline():
    source = (ROOT / "db/migrations/101-data-trust-and-outcome-views.sql").read_text()
    views = source[source.index("CREATE OR REPLACE VIEW v_forecast_accuracy AS") : source.index("-- Iris context")]
    source = (ROOT / "db/migrations/049-operator-views.sql").read_text()
    legacy = source[source.index("CREATE OR REPLACE VIEW v_forecast_vs_actual AS") : source.index("-- 5. v_cost_today")]
    source = (ROOT / "db/migrations/050-operator-improvements.sql").read_text()
    correction = source[
        source.index("CREATE OR REPLACE FUNCTION fn_forecast_correction") : source.index("-- 2. v_active_plan")
    ]
    return (
        """
CREATE ROLE verdify_ingestor_runtime;
CREATE TABLE climate (ts timestamptz, outdoor_temp_f float8, outdoor_rh_pct float8,
    solar_irradiance_w_m2 float8, vpd_avg float8);
CREATE TABLE weather_forecast (ts timestamptz, fetched_at timestamptz, temp_f float8,
    rh_pct float8, vpd_kpa float8, solar_w_m2 float8, cloud_cover_pct float8);
CREATE FUNCTION time_bucket(interval, timestamptz) RETURNS timestamptz
LANGUAGE sql IMMUTABLE AS $$ SELECT date_bin($1, $2, timestamptz '1970-01-01 UTC') $$;
CREATE VIEW v_climate_merged AS SELECT ts AS bucket, outdoor_temp_f,
    vpd_avg, solar_irradiance_w_m2 AS solar_w_m2 FROM climate;
CREATE TABLE fixture_hour AS SELECT date_bin(interval '1 hour', now(),
    timestamptz '1970-01-01 UTC') - interval '48 hours' AS hour;
-- Perfect outdoor VPD (100% RH -> zero), indoor VPD 8 kPa, four minutes.
INSERT INTO climate SELECT hour + n * interval '1 minute', 77, 100, 100, 8
    FROM fixture_hour CROSS JOIN generate_series(0, 3) n;
INSERT INTO weather_forecast SELECT hour, hour - interval '5 hours', 77, 100, 0, 100, 0 FROM fixture_hour;
INSERT INTO weather_forecast SELECT * FROM weather_forecast;
INSERT INTO weather_forecast SELECT hour, hour - interval '2 hours', 77, 100, 0, 100, 0 FROM fixture_hour;
-- This vintage was unavailable before the valid hour.
INSERT INTO weather_forecast SELECT hour, hour + interval '1 hour', 120, 10, 9, 500, 0 FROM fixture_hour;
"""
        + views
        + legacy
        + correction
        + """
REVOKE EXECUTE ON FUNCTION fn_forecast_correction(text,numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_forecast_correction(text,numeric) TO verdify_ingestor_runtime;
GRANT SELECT ON v_forecast_accuracy_daily, v_forecast_accuracy_lead_buckets,
    v_forecast_vs_actual TO verdify_ingestor_runtime;
"""
    )


def test_outdoor_sql_reference_duplicates_lead_and_rollback(isolated_pg):
    query = isolated_pg
    query(_baseline())
    assert query("SELECT DISTINCT vpd_error_kpa FROM v_forecast_accuracy;") == "-8.00"
    assert int(query("SELECT samples FROM v_forecast_accuracy_lead_buckets WHERE param='vpd_kpa';")) == 12
    identities = """SELECT jsonb_build_object('function', p.oid, 'owner', p.proowner,
        'acl', p.proacl::text, 'definer', p.prosecdef,
        'accuracy', 'v_forecast_accuracy'::regclass::oid,
        'daily', 'v_forecast_accuracy_daily'::regclass::oid,
        'buckets', 'v_forecast_accuracy_lead_buckets'::regclass::oid)
        FROM pg_proc p WHERE p.oid='fn_forecast_correction(text,numeric)'::regprocedure;"""
    before = query(identities)
    migration = MIGRATION.read_text()
    query("BEGIN;\n" + migration + "\nROLLBACK;")
    assert query(identities) == before
    assert query("SELECT to_regclass('v_forecast_outdoor_pairs') IS NULL;") == "t"
    assert query("SELECT DISTINCT vpd_error_kpa FROM v_forecast_accuracy;") == "-8.00"
    query("BEGIN;\n" + migration + "\nCOMMIT;")
    assert query(identities) == before
    row = json.loads(query("SELECT row_to_json(v) FROM v_forecast_accuracy v;"))
    assert row["vpd_error_kpa"] == 0
    assert row["actual_vpd"] == 0
    assert row["lead_hours"] == 2
    assert row["vpd_minutes"] == 1
    assert row["verification_contract_version"] == 2
    buckets = json.loads(
        query("""SET ROLE verdify_ingestor_runtime;
        SELECT row_to_json(v) FROM v_forecast_accuracy_lead_buckets v WHERE param='vpd_kpa';""")
    )
    assert buckets["samples"] == 1
    assert buckets["observed_minutes"] == 1
    assert buckets["bias"] == 0
    # Lead is forecast lead, not the observation's age (48h ago).
    assert query("SET ROLE verdify_ingestor_runtime; SELECT samples FROM fn_forecast_correction('temp_f', 3);") == "1"
    assert query("SELECT samples FROM fn_forecast_correction('temp_f', 1);") == "0"
    assert query("SELECT avg_error IS NULL FROM fn_forecast_correction('unsupported', 24);") == "t"
    assert (
        float(
            query("""SELECT lead_hours FROM v_forecast_outdoor_pairs
        WHERE fetched_at <= (SELECT hour - interval '3 hours' FROM fixture_hour)
        ORDER BY fetched_at DESC LIMIT 1;""")
        )
        == 5
    )
    assert query("SELECT has_table_privilege('verdify_ingestor_runtime', 'weather_forecast', 'SELECT');") == "f"


def test_missing_conflicting_stale_and_future_forecasts(isolated_pg):
    query = isolated_pg
    query(_baseline())
    query("BEGIN;\n" + MIGRATION.read_text() + "\nCOMMIT;")
    query("UPDATE climate SET outdoor_rh_pct=NULL;")
    assert query("SELECT actual_vpd IS NULL AND vpd_error_kpa IS NULL FROM v_forecast_accuracy;") == "t"
    assert query("SELECT samples FROM v_forecast_accuracy_lead_buckets WHERE param='vpd_kpa';") == "0"
    query("""
UPDATE climate SET outdoor_rh_pct=100;
INSERT INTO weather_forecast SELECT date_bin(interval '1 hour', now(), timestamptz '1970-01-01 UTC')
    + interval '1 hour', now() - interval '30 minutes', 77, 100, 0, 100, 0;
INSERT INTO weather_forecast SELECT date_bin(interval '1 hour', now(), timestamptz '1970-01-01 UTC')
    + interval '1 hour', now() + interval '1 minute', 120, 0, 9, 900, 0;
""")
    prior_sql = "SELECT row_to_json(v) FROM v_forecast_planning_priors v WHERE param='vpd_kpa';"
    prior = json.loads(query("SET ROLE verdify_ingestor_runtime; " + prior_sql))
    assert prior["raw_forecast"] == 0
    assert prior["corrected_prior"] == 0
    assert prior["availability"] == "available_diagnostic"
    assert prior["calibration_paired_hours"] == 1
    assert prior["calibration_observed_minutes"] == 1
    assert 30 <= prior["fetch_age_minutes"] < 31
    query("UPDATE weather_forecast SET fetched_at=now()-interval '3 hours' WHERE ts>now() AND fetched_at<now();")
    prior = json.loads(query(prior_sql))
    assert prior["availability"] == "stale_forecast"
    assert prior["corrected_prior"] is None
    query("""INSERT INTO weather_forecast SELECT ts, fetched_at, temp_f, rh_pct, 4,
        solar_w_m2, cloud_cover_pct FROM weather_forecast WHERE ts>now() AND fetched_at<now();""")
    prior = json.loads(query(prior_sql))
    assert prior["availability"] == "conflicting_vintage"
    assert prior["raw_forecast"] is None
    assert prior["corrected_prior"] is None


def test_planner_context_requires_version_and_carries_eligibility():
    source = (ROOT / "scripts/gather-plan-context.sh").read_text()
    assert "verification_contract_version = 2" in source
    assert "FROM v_forecast_planning_priors" in source
    for field in (
        "decision_at",
        "available_at",
        "lead_hours",
        "fetch_age_minutes",
        "calibration_paired_hours",
        "calibration_observed_minutes",
        "availability",
    ):
        assert field in source
    assert "Use corrected_vpd_kpa as the planning prior" not in source
    assert "AVG(bias)" not in source


def test_instant_weather_and_preceding_hour_solar_are_not_shifted(isolated_pg):
    query = isolated_pg
    query(_baseline())
    query("""
-- Subsequent weather differs sharply, but does not define the instant at t.
UPDATE climate SET outdoor_temp_f=100, outdoor_rh_pct=10 WHERE ts > (SELECT hour FROM fixture_hour);
-- Solar t is the preceding hour, not the sunny hour after t.
INSERT INTO climate SELECT hour - interval '1 hour' + n * interval '1 minute',
    50, 40, 100, 9 FROM fixture_hour CROSS JOIN generate_series(0,59) n;
UPDATE climate SET solar_irradiance_w_m2=900 WHERE ts >= (SELECT hour FROM fixture_hour);
""")
    query("BEGIN;\n" + MIGRATION.read_text() + "\nCOMMIT;")
    row = json.loads(query("SELECT row_to_json(v) FROM v_forecast_accuracy v;"))
    assert row["actual_temp"] == 77
    assert row["actual_vpd"] == 0
    assert row["actual_solar"] == 100
    assert row["solar_error_w"] == 0
    assert row["solar_minutes"] == 60
    # A missing solar minute is not extrapolated into a complete hourly truth.
    query("DELETE FROM climate WHERE ts=(SELECT hour-interval '1 hour' FROM fixture_hour);")
    assert query("SELECT actual_solar IS NULL AND solar_error_w IS NULL FROM v_forecast_accuracy;") == "t"
    assert query("SELECT solar_minutes FROM v_forecast_accuracy;") == "59"


def test_daily_mae_does_not_cancel_opposite_signed_errors(isolated_pg):
    query = isolated_pg
    query(_baseline())
    query("""
UPDATE weather_forecast SET temp_f=79 WHERE fetched_at < ts;
INSERT INTO climate SELECT hour+interval '1 hour',77,100,100,8 FROM fixture_hour;
INSERT INTO weather_forecast SELECT hour+interval '1 hour', hour-interval '1 hour',
    75,100,0,100,0 FROM fixture_hour;
""")
    query("BEGIN;\n" + MIGRATION.read_text() + "\nCOMMIT;")
    assert (
        float(
            query("""SELECT sum(abs_error*samples)/sum(samples)
        FROM v_forecast_accuracy_daily WHERE param='temp_f';""")
        )
        == 2
    )
    assert (
        float(
            query("""SELECT sum(bias*samples)/sum(samples)
        FROM v_forecast_accuracy_daily WHERE param='temp_f';""")
        )
        == 0
    )
