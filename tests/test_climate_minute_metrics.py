"""Hand-computed observed-minute diagnostics and real SQL/revision integration."""

import asyncio
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[1]
metrics_spec = importlib.util.spec_from_file_location(
    "climate_minute_metrics_under_test", ROOT / "ingestor/climate_minute_metrics.py"
)
metrics_module = importlib.util.module_from_spec(metrics_spec)
metrics_spec.loader.exec_module(metrics_module)
WINDOW_SQL = metrics_module.WINDOW_SQL
measure_observed_minutes = metrics_module.measure_observed_minutes
refresh_observed_minute_metrics = metrics_module.refresh_observed_minute_metrics
START = datetime(2026, 9, 4, 6, tzinfo=UTC)
spec = importlib.util.spec_from_file_location(
    "climate_revision_fixture", ROOT / "tests/test_daily_climate_metric_revisions.py"
)
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
isolated_pg = fixture_module.isolated_pg


def row(minute=0, *, temp=70, vpd=1, second=0, greenhouse="vallery"):
    return {
        "ts": START + timedelta(minutes=minute, seconds=second),
        "greenhouse_id": greenhouse,
        "temp_avg": temp,
        "vpd_avg": vpd,
    }


def bounds():
    return [
        {"ts": START - timedelta(minutes=1), "greenhouse_id": "vallery", "parameter": parameter, "value": value}
        for parameter, value in {"temp_low": 60, "temp_high": 80, "vpd_low": 0.5, "vpd_high": 1.5}.items()
    ]


def measure(rows, events=None, minutes=3):
    return measure_observed_minutes(
        rows, bounds() if events is None else events, start=START, end=START + timedelta(minutes=minutes)
    )


def test_missingness_independent_axes_zero_and_duplicate_minutes():
    empty = measure([])
    assert empty["axes"]["temp"]["in_band_pct"] is None
    assert empty["joint"]["in_band_pct"] is None
    assert empty["joint"]["longest_ineligible_run_minutes"] == 3
    partial = measure([row(vpd=None)])
    assert partial["axes"]["temp"]["in_band_pct"] == 100
    assert partial["axes"]["vpd"]["in_band_pct"] is None
    assert partial["joint"]["eligible_minutes"] == 0
    hot = measure([row(temp=90)] * 60)
    assert hot["axes"]["temp"]["eligible_minutes"] == 1
    assert hot["axes"]["temp"]["high_miss_observed_minutes"] == 1
    assert hot["axes"]["temp"]["in_band_pct"] == 0
    assert hot["axes"]["temp"]["mean_outside_distance"] == 10
    assert hot["joint"]["in_band_pct"] == 0
    assert not hot["crop_outcome_eligible"] and not hot["experiment_endpoint_eligible"]
    assert hot["worst_measured_zone"] is None


def test_unique_timestamps_then_equal_minute_weight_and_signed_severity():
    result = measure([row(temp=50), row(temp=50)] + [row(1, temp=90, second=s) for s in range(60)])
    temp = result["axes"]["temp"]
    assert temp["eligible_minutes"] == 2
    assert temp["mean_low_distance"] == 5
    assert temp["mean_high_distance"] == 5
    assert temp["mean_outside_distance"] == 10
    assert temp["coverage_fraction"] == pytest.approx(2 / 3)
    # Two different finite samples within one minute average once, not two slots.
    average = measure([row(temp=50), row(temp=90, second=30)])
    assert average["axes"]["temp"]["in_band_pct"] == 100


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "70"])
def test_bad_axis_values_do_not_invalidate_other_axis_or_become_failures(bad):
    result = measure([row(temp=bad)])
    assert result["axes"]["temp"]["eligible_minutes"] == 0
    assert result["axes"]["temp"]["in_band_pct"] is None
    assert result["axes"]["vpd"]["in_band_pct"] == 100
    json.dumps(result, allow_nan=False)


def test_same_timestamp_conflicts_invalidate_only_affected_axis_minute():
    result = measure([row(temp=70), row(temp=71)])
    assert result["axes"]["temp"]["ineligible_minutes"]["conflicting_timestamp"] == 1
    assert result["axes"]["temp"]["eligible_minutes"] == 0
    assert result["axes"]["vpd"]["eligible_minutes"] == 1
    assert result["joint"]["eligible_minutes"] == 0


def test_numeric_zero_equivalence_and_setpoint_origin_hash_binding():
    result = measure([row(vpd=0.0), row(vpd=-0.0)])
    assert result["axes"]["vpd"]["eligible_minutes"] == 1
    assert result["axes"]["vpd"]["ineligible_minutes"]["conflicting_timestamp"] == 0
    tagged = [dict(e, source="esp32") for e in bounds()]
    assert measure([row()])["input_sha256"] != measure([row()], tagged)["input_sha256"]
    assert "provenance_unqualified" in result["target_basis"]


@pytest.mark.parametrize("failure", ["missing", "nan", "inverted", "conflict", "expired", "superseded", "future"])
def test_bound_integrity_and_asof_never_backfill_old_complete_target(failure):
    events = bounds()
    replacement = dict(events[1], ts=START, value=80)
    if failure == "missing":
        replacement["value"] = None
    elif failure == "nan":
        replacement["value"] = float("nan")
    elif failure == "inverted":
        replacement["value"] = 40
    elif failure == "conflict":
        events.append(dict(replacement, value=81))
    elif failure == "expired":
        replacement["expired_at"] = START
    elif failure == "superseded":
        replacement["superseded_by_ts"] = START
    elif failure == "future":
        events = [e for e in events if e["parameter"] != "temp_high"]
        replacement["ts"] = START + timedelta(seconds=10)
    events.append(replacement)
    result = measure([row()], events)
    assert result["axes"]["temp"]["eligible_minutes"] == 0
    assert result["axes"]["vpd"]["eligible_minutes"] == 1


def test_scope_halfopen_boundaries_gaps_and_input_hash_reproducibility():
    rows = [row(), row(6), row(0, greenhouse="other"), row(-1), row(7)]
    result = measure(rows, minutes=7)
    assert result["input_rows"] == 2 and result["ignored_rows"] == 3
    assert result["axes"]["temp"]["longest_ineligible_run_minutes"] == 5
    assert result["input_sha256"] == measure(list(reversed(rows)), list(reversed(bounds())), minutes=7)["input_sha256"]
    changed = [dict(e, value=81) if e["parameter"] == "temp_high" else e for e in bounds()]
    assert result["input_sha256"] != measure(rows, changed, minutes=7)["input_sha256"]
    assert measure([], minutes=0)["axes"]["temp"]["coverage_fraction"] is None


@pytest.mark.parametrize("change", ["naive", "partial_minute", "too_long", "backwards"])
def test_window_contract_rejects_ambiguous_input(change):
    start, end = START, START + timedelta(minutes=1)
    if change == "naive":
        start = start.replace(tzinfo=None)
    elif change == "partial_minute":
        start += timedelta(seconds=1)
    elif change == "too_long":
        end += timedelta(days=2)
    else:
        end = start - timedelta(minutes=1)
    with pytest.raises(ValueError):
        measure_observed_minutes([], [], start=start, end=end)


def test_actual_sql_async_writer_revision_capture_and_rollback(isolated_pg):
    query = isolated_pg
    fixture_module.baseline(query)
    fixture_module.install(query)
    prior = query("SELECT metrics::text FROM daily_climate_metric_revisions")
    helper_identity = query(
        "SELECT oid::text || ':' || proowner::text || ':' || proacl::text FROM pg_proc WHERE oid='fn_daily_climate_metric_payload(daily_summary)'::regprocedure"
    )
    socket = query("SHOW unix_socket_directories")

    async def absent():
        conn = await asyncpg.connect(
            host=socket, port=55472, user="scorecard_fixture", database="postgres", password="", ssl=False
        )
        try:
            assert await refresh_observed_minute_metrics(conn, date(2026, 9, 4)) is None
        finally:
            await conn.close()

    asyncio.run(absent())
    migration = (ROOT / "db/migrations/245-observed-minute-diagnostics.sql").read_text()
    query("BEGIN;" + migration + "ROLLBACK;")
    assert query("SELECT metrics::text FROM daily_climate_metric_revisions") == prior
    assert (
        query(
            "SELECT count(*) FROM pg_attribute WHERE attrelid='daily_summary'::regclass AND attname='climate_observed_minute_metrics' AND NOT attisdropped"
        )
        == "0"
    )
    query("BEGIN;" + migration + "COMMIT;")
    assert (
        query(
            "SELECT oid::text || ':' || proowner::text || ':' || proacl::text FROM pg_proc WHERE oid='fn_daily_climate_metric_payload(daily_summary)'::regprocedure"
        )
        == helper_identity
    )
    query("""
        CREATE TABLE climate(ts timestamptz, greenhouse_id text, temp_avg double precision, vpd_avg double precision);
        CREATE TABLE setpoint_changes(ts timestamptz, greenhouse_id text, parameter text, value double precision, source text,
                                      expired_at timestamptz, superseded_by_ts timestamptz);
        GRANT SELECT ON climate, setpoint_changes TO verdify_ingestor_runtime;
        INSERT INTO climate VALUES ('2026-09-04T06:00Z','vallery',90,NULL),
                                   ('2026-09-04T06:00Z','vallery',90,NULL),
                                   ('2026-09-04T06:01Z','vallery',70,1),
                                   ('2026-09-04T06:00Z','other',0,99);
        INSERT INTO setpoint_changes(ts,greenhouse_id,parameter,value)
        VALUES ('2026-09-04T05:59Z','vallery','temp_low',60),
               ('2026-09-04T05:59Z','vallery','temp_high',80),
               ('2026-09-04T05:59Z','vallery','vpd_low',0.5),
               ('2026-09-04T05:59Z','vallery','vpd_high',1.5);
    """)

    async def run():
        conn = await asyncpg.connect(
            host=socket, port=55472, user="scorecard_fixture", database="postgres", password="", ssl=False
        )
        try:
            await conn.execute("SET ROLE verdify_ingestor_runtime")
            result = await refresh_observed_minute_metrics(conn, date(2026, 9, 4))
            assert result["axes"]["temp"]["eligible_minutes"] == 2
            assert result["axes"]["temp"]["in_band_pct"] == 50
            assert result["axes"]["vpd"]["in_band_pct"] == 100
            assert result["joint"]["eligible_minutes"] == 1
            assert result["expected_minutes"] == 1440
            assert await conn.fetchval("SELECT compliance_pct FROM daily_summary") == 6.1
            count = await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions")
            assert count == 3
            await refresh_observed_minute_metrics(conn, date(2026, 9, 4))
            assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == count
            saved = await conn.fetchval("SELECT climate_observed_minute_metrics::text FROM daily_summary")
            outer = conn.transaction(isolation="repeatable_read")
            await outer.start()
            try:
                await conn.execute("UPDATE daily_summary SET climate_observed_minute_metrics=NULL")
                await refresh_observed_minute_metrics(conn, date(2026, 9, 4))
                assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == count + 4
            finally:
                await outer.rollback()
            assert await conn.fetchval("SELECT climate_observed_minute_metrics::text FROM daily_summary") == saved
            assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == count
            await conn.execute(
                "RESET ROLE; ALTER TABLE daily_climate_metric_revisions ADD CONSTRAINT fixture_fail CHECK(operation <> 'after_update') NOT VALID"
            )
            await conn.execute("INSERT INTO climate VALUES ('2026-09-04T06:02Z','vallery',75,1)")
            await conn.execute("SET ROLE verdify_ingestor_runtime")
            with pytest.raises(asyncpg.CheckViolationError):
                await refresh_observed_minute_metrics(conn, date(2026, 9, 4))
            assert await conn.fetchval("SELECT climate_observed_minute_metrics::text FROM daily_summary") == saved
            assert await conn.fetchval("SELECT count(*) FROM daily_climate_metric_revisions") == count
        finally:
            await conn.close()

    asyncio.run(run())
    rows = fixture_module.ledger(query)
    assert rows[0]["capture_schema"] == "daily-summary-capture-v1"
    assert rows[-1]["capture_schema"] == "daily-summary-capture-v2"
    assert rows[-1]["metrics"]["observed_minute_diagnostic"]["definition"] == "house-average-observed-minute-v1"
    query("""DO $$ BEGIN
        BEGIN UPDATE daily_summary SET climate_observed_minute_metrics='{}';
              RAISE EXCEPTION 'invalid definition accepted';
        EXCEPTION WHEN check_violation THEN NULL; END;
    END $$;""")


def test_real_local_day_windows_handle_dst_and_exclude_unfinished_current_minutes(isolated_pg):
    query = isolated_pg
    for day, expected in (("2025-03-09", 1380), ("2025-11-02", 1500)):
        sql = WINDOW_SQL.replace("$1", f"'{day}'")
        assert float(query(f"SELECT extract(epoch FROM (w.end-w.start))/60 FROM ({sql}) w")) == expected
    sql = WINDOW_SQL.replace("$1", "(current_timestamp AT TIME ZONE 'America/Denver')::date")
    assert query(f"SELECT w.end=date_trunc('minute',current_timestamp) FROM ({sql}) w") == "t"


def test_historical_reconciliation_outer_transaction_matches_diagnostic_snapshot():
    import ast

    source = ast.parse((ROOT / "scripts/reconcile-derived-history.py").read_text())
    function = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "reconcile_day")
    transaction = next(
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "transaction"
    )
    assert any(k.arg == "isolation" and ast.literal_eval(k.value) == "repeatable_read" for k in transaction.keywords)
