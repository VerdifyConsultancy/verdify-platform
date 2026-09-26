"""Typed wire/consumer and real private-PostgreSQL reader acceptance."""

import ast
import asyncio
import copy
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.observed_minute_reader import parse_observed_minute_row, read_observed_minute_evidence
from verdify_schemas.observed_minutes import ObservedMinuteEvidence

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 9, 4)
START = datetime(2026, 9, 4, 6, tzinfo=UTC)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calculator = load("ingestor/climate_minute_metrics.py", "minute_reader_calculator")
fixture = load("tests/test_daily_climate_metric_revisions.py", "minute_reader_pg_fixture")
publisher = load("scripts/update-evidence-snapshots.py", "minute_reader_publisher")
isolated_pg = fixture.isolated_pg
MIGRATION = (ROOT / "db/migrations/246-observed-minute-reader.sql").read_text()


def diagnostic(*, minutes=3, rows=None, start=START):
    events = [
        {"ts": start - timedelta(minutes=1), "greenhouse_id": "vallery", "parameter": key, "value": value}
        for key, value in {"temp_low": 60, "temp_high": 80, "vpd_low": 0.5, "vpd_high": 1.5}.items()
    ]
    if rows is None:
        rows = [{"ts": start, "greenhouse_id": "vallery", "temp_avg": 90, "vpd_avg": None}]
    return calculator.measure_observed_minutes(rows, events, start=start, end=start + timedelta(minutes=minutes))


def captured(payload=None):
    return {
        "day": DAY,
        "greenhouse_id": "vallery",
        "served_at": START + timedelta(days=1, hours=2),
        "revision_id": 3,
        "recorded_at": START + timedelta(days=1),
        "capture_schema": "daily-summary-capture-v2",
        "unavailable_reason": None,
        "diagnostic": diagnostic() if payload is None else payload,
    }


def test_valid_zero_missingness_and_independent_legacy_contract():
    evidence = parse_observed_minute_row(captured(), DAY)
    assert evidence.availability == "available"
    assert evidence.diagnostic.axes.temp.in_band_pct == 0
    assert evidence.diagnostic.axes.vpd.in_band_pct is None
    assert evidence.diagnostic.joint.in_band_pct is None
    assert evidence.diagnostic.axes.temp.mean_high_distance == 10
    assert "freshness_not_assessed" in evidence.currentness
    card = ScorecardResponse(observed_minute_evidence=evidence, compliance_pct=85.8)
    projected = card.climate_evidence()
    assert projected["both_axis_compliance_pct"] is None  # missing old contract marker
    assert projected["observed_minute_evidence"]["diagnostic"]["axes"]["temp"]["in_band_pct"] == 0
    assert "observed_minute_evidence" not in ScorecardResponse.metric_names()
    assert ScorecardResponse().observed_minute_evidence.availability == "unavailable"
    with pytest.raises(ValidationError):
        ScorecardResponse.from_metric_rows([("observed_minute_evidence", 1)])
    assert ObservedMinuteEvidence.model_validate_json(evidence.model_dump_json()) == evidence


@pytest.mark.parametrize(
    "path,value",
    [
        (("definition",), "other"),
        (("calculation_source_sha256",), "bad"),
        (("input_sha256",), "bad"),
        (("greenhouse_id",), "other"),
        (("expected_minutes",), 4),
        (("expected_minutes",), True),
        (("observed_minutes",), 2),
        (("input_rows",), 0),
        (("window_start",), "2026-09-04T06:00:00"),
        (("window_end",), "2026-09-04T06:03:01Z"),
        (("fixed_sensor_panel",), True),
        (("duration_weighted",), 0),
        (("physical_proof_eligible",), True),
        (("crop_outcome_eligible",), True),
        (("experiment_endpoint_eligible",), True),
        (("worst_measured_zone",), "center"),
        (("target_basis",), "confirmed_firmware"),
        (("axes", "temp", "unit"), "kPa"),
        (("axes", "temp", "in_band_pct"), 100),
        (("axes", "temp", "eligible_minutes"), True),
        (("axes", "temp", "coverage_fraction"), 1),
        (("axes", "temp", "mean_high_distance"), float("nan")),
        (("axes", "temp", "mean_outside_distance"), 9),
        (("axes", "vpd", "mean_low_distance"), 0),
        (("axes", "temp", "longest_ineligible_run_minutes"), 3),
        (("axes", "temp", "ineligible_minutes", "missing_value"), 0),
        (("joint", "in_band_minutes"), 1),
        (("extra_untrusted_value",), "do-not-echo-fixture"),
    ],
)
def test_tampered_payload_is_unavailable_and_never_echoed(path, value):
    payload = diagnostic()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    evidence = parse_observed_minute_row(captured(payload), DAY)
    assert evidence.availability == "unavailable"
    assert evidence.unavailable_reason == "invalid_diagnostic"
    assert evidence.diagnostic is None
    assert "do-not-echo-fixture" not in evidence.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_id", None),
        ("capture_schema", "daily-summary-capture-v1"),
        ("recorded_at", START),
        ("served_at", START),
        ("day", date(2026, 9, 3)),
        ("greenhouse_id", "other"),
        ("unavailable_reason", "revision_mismatch"),
    ],
)
def test_snapshot_scope_and_revision_integrity(field, value):
    row = captured()
    row[field] = value
    assert parse_observed_minute_row(row, DAY).availability == "unavailable"


@pytest.mark.parametrize(
    "start,minutes",
    [
        (START, 0),
        (START, 1440),
        (datetime(2026, 3, 8, 7, tzinfo=UTC), 1380),
        (datetime(2026, 11, 1, 6, tzinfo=UTC), 1500),
    ],
)
def test_zero_and_dst_windows_keep_null_denominators(start, minutes):
    data = diagnostic(minutes=minutes, start=start, rows=[])
    row = captured(data)
    row.update(day=start.date(), served_at=start + timedelta(days=2), recorded_at=start + timedelta(days=2))
    evidence = parse_observed_minute_row(row, start.date())
    assert evidence.availability == "available"
    assert evidence.diagnostic.joint.in_band_pct is None
    assert evidence.diagnostic.joint.coverage_fraction == (0 if minutes else None)


def test_publisher_revalidates_snapshot_and_does_not_substitute_graded_credit():
    evidence = parse_observed_minute_row(captured(), DAY).model_dump(mode="json")
    rendered = publisher.observed_minute_block(evidence, DAY.isoformat())
    assert "0.0%" in rendered and "0/1 eligible" in rendered and "1/3 evaluated" in rendered
    assert "Not continuous exposure" in rendered and "revision 3" in rendered
    assert "freshness is not assessed" in rendered and "input SHA-256" in rendered
    for payload in (None, {}, {"availability": "available"}, copy.deepcopy(evidence)):
        if payload and "diagnostic" in payload:
            payload["diagnostic"]["crop_outcome_eligible"] = True
        output = publisher.observed_minute_block(payload)
        assert "Unavailable" in output and "0.0%" not in output
    assert "Unavailable" in publisher.observed_minute_block(evidence, "2026-09-03")


def test_actual_reader_sql_roles_no_fallback_shadowing_and_rollback(isolated_pg):
    query = isolated_pg
    fixture.baseline(query)
    fixture.install(query)
    query((ROOT / "db/migrations/245-observed-minute-diagnostics.sql").read_text())
    before = query("SELECT row_to_json(d) FROM daily_summary d")
    query("BEGIN;" + MIGRATION + "ROLLBACK;")
    assert query("SELECT to_regprocedure('fn_observed_minute_diagnostic(date,text)') IS NULL") == "t"
    assert query("SELECT row_to_json(d) FROM daily_summary d") == before
    query("BEGIN;" + MIGRATION + "COMMIT;")
    for role in ("verdify_ingestor_runtime", "verdify_api_runtime_login", "verdify_ingestor_runtime_login"):
        assert (
            query(f"SELECT has_function_privilege('{role}', 'fn_observed_minute_diagnostic(date,text)', 'EXECUTE')")
            == "f"
        )
    assert query("SELECT has_table_privilege('verdify_api_runtime','daily_summary','SELECT')") == "f"
    assert (
        query(
            "SELECT prosecdef AND proowner=(SELECT datdba FROM pg_database WHERE datname=current_database()) FROM pg_proc WHERE oid='fn_observed_minute_diagnostic(date,text)'::regprocedure"
        )
        == "t"
    )
    assert (
        query("SELECT proconfig[1] FROM pg_proc WHERE oid='fn_observed_minute_diagnostic(date,text)'::regprocedure")
        == "search_path=pg_catalog, public, pg_temp"
    )

    async def run():
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
            assert (
                await read_observed_minute_evidence(conn, date(2026, 9, 3))
            ).unavailable_reason == "daily_row_missing"
            assert (
                await read_observed_minute_evidence(conn, DAY, "other")
            ).unavailable_reason == "unsupported_greenhouse"
            with pytest.raises(asyncpg.InvalidParameterValueError):
                await conn.fetchrow("SELECT * FROM fn_observed_minute_diagnostic($1, 'other')", DAY)
            await conn.execute("RESET ROLE; SET ROLE verdify_ingestor_runtime")
            await conn.execute(
                "UPDATE daily_summary SET climate_observed_minute_metrics=$1::jsonb", json.dumps(diagnostic())
            )
            await conn.execute("RESET ROLE; SET ROLE verdify_api_runtime")
            result = await read_observed_minute_evidence(conn, DAY)
            assert result.availability == "available" and result.revision_id == 3
            await conn.execute("CREATE TEMP TABLE daily_summary (date date); SET search_path=pg_temp,public")
            shadow = await read_observed_minute_evidence(conn, DAY)
            assert shadow.availability == "available" and shadow.revision_id == 3
            await conn.execute("DROP TABLE pg_temp.daily_summary")
            await conn.execute("RESET ROLE; SET search_path=public; SET ROLE verdify_ingestor_runtime")
            invalid = diagnostic()
            invalid["axes"]["temp"]["in_band_pct"] = 100
            await conn.execute(
                "UPDATE daily_summary SET climate_observed_minute_metrics=$1::jsonb", json.dumps(invalid)
            )
            await conn.execute("RESET ROLE; SET ROLE verdify_api_runtime")
            assert (await read_observed_minute_evidence(conn, DAY)).unavailable_reason == "invalid_diagnostic"
            # Admin-only trigger bypass simulates damaged capture, never a runtime action.
            await conn.execute(
                "RESET ROLE; ALTER TABLE daily_summary DISABLE TRIGGER daily_summary_capture_climate_revision"
            )
            await conn.execute(
                "UPDATE daily_summary SET climate_observed_minute_metrics=$1::jsonb", json.dumps(diagnostic())
            )
            await conn.execute(
                "ALTER TABLE daily_summary ENABLE TRIGGER daily_summary_capture_climate_revision; SET ROLE verdify_api_runtime"
            )
            mismatch = await read_observed_minute_evidence(conn, DAY)
            assert mismatch.unavailable_reason == "revision_mismatch" and mismatch.diagnostic is None
            await conn.execute("RESET ROLE; DELETE FROM daily_summary; SET ROLE verdify_api_runtime")
            deleted = await read_observed_minute_evidence(conn, DAY)
            assert deleted.unavailable_reason == "daily_row_missing" and deleted.diagnostic is None
            await conn.execute(
                "RESET ROLE; DROP FUNCTION fn_observed_minute_diagnostic(date,text); SET ROLE verdify_api_runtime"
            )
            async with conn.transaction():
                assert (await read_observed_minute_evidence(conn, DAY)).unavailable_reason == "reader_unavailable"
                assert await conn.fetchval("SELECT 1") == 1  # nested failure did not poison caller transaction
            # Exercise the actual server timeout and savepoint cleanup, not just
            # the mocked exception. This fixture function exists only in this
            # private socket cluster and intentionally never produces evidence.
            await conn.execute("""
                RESET ROLE;
                CREATE FUNCTION public.fn_observed_minute_diagnostic(date,text)
                RETURNS TABLE(day date, greenhouse_id text, served_at timestamptz,
                    revision_id bigint, recorded_at timestamptz, capture_schema text,
                    unavailable_reason text, diagnostic jsonb)
                LANGUAGE plpgsql AS $fixture$
                BEGIN PERFORM pg_sleep(10); RETURN; END;
                $fixture$;
                GRANT EXECUTE ON FUNCTION public.fn_observed_minute_diagnostic(date,text) TO verdify_api_runtime;
                SET ROLE verdify_api_runtime;
                SET statement_timeout='15000ms';
            """)
            async with conn.transaction():
                timed_out = await read_observed_minute_evidence(conn, DAY)
                assert timed_out.unavailable_reason == "db_statement_timeout"
                assert await conn.fetchval("SELECT 1") == 1
                assert await conn.fetchval("SHOW statement_timeout") == "15s"
        finally:
            await conn.close()

    asyncio.run(run())


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.parametrize(
    "error,reason",
    [
        (asyncpg.QueryCanceledError, "db_statement_timeout"),
        (TimeoutError, "db_statement_timeout"),
        (asyncpg.UndefinedFunctionError, "reader_unavailable"),
        (asyncpg.InsufficientPrivilegeError, "reader_unavailable"),
    ],
)
def test_bounded_fail_closed_reader_errors(error, reason):
    calls = []

    async def execute(sql):
        calls.append(sql)

    async def fetchrow(sql, *args, **kwargs):
        assert args == (DAY, "vallery") and kwargs == {"timeout": 3.5}
        raise error("untrusted-error-detail-do-not-echo")

    conn = SimpleNamespace(transaction=Transaction, execute=execute, fetchrow=fetchrow)
    evidence = asyncio.run(read_observed_minute_evidence(conn, DAY))
    assert evidence.unavailable_reason == reason
    assert calls == ["SET LOCAL statement_timeout = '3000ms'"]
    assert "untrusted" not in evidence.model_dump_json()


def extract_function(path, name, namespace):
    """Execute the actual bounded consumer body without service/env bootstrap."""
    node = next(
        n for n in ast.parse((ROOT / path).read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    )
    node.decorator_list = []
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), "exec"), namespace)  # noqa: S102 - exact repository source, no external input
    return namespace[name]


@pytest.mark.parametrize("consumer", ["api", "mcp"])
@pytest.mark.parametrize("use_today", [False, True])
def test_actual_scorecard_consumers_attach_separate_typed_snapshot(consumer, use_today):
    evidence = parse_observed_minute_row(captured(), DAY)
    calls = []

    async def read(conn, day):
        calls.append(day)
        return evidence

    async def fetch(*args):
        return [{"metric": "scorecard_contract_version", "value": 2}, {"metric": "compliance_pct", "value": 6.1}]

    async def fetchval(sql):
        assert "America/Denver" in sql
        calls.append("resolved_day_once")
        return DAY

    async def close():
        calls.append("closed")

    conn = SimpleNamespace(fetch=fetch, fetchval=fetchval, close=close)

    class Checkout(Transaction):
        async def __aenter__(self):
            return conn

    async def db():
        return conn

    namespace = dict(
        ScorecardResponse=ScorecardResponse,
        ValidationError=ValidationError,
        read_observed_minute_evidence=read,
        _fetch_planner_scorecard=fetch,
        pool=SimpleNamespace(acquire=Checkout),
        _db=db,
        datetime=datetime,
        asyncpg=asyncpg,
    )
    if consumer == "api":
        fn = extract_function("api/main.py", "planner_scorecard", namespace)
        result = asyncio.run(fn(None if use_today else DAY))
    else:
        fn = extract_function("mcp/server.py", "scorecard", namespace)
        wire = json.loads(asyncio.run(fn("" if use_today else DAY.isoformat())))
        assert wire.pop("metric_semantics")["crop_outcome_eligible"] is False
        result = ScorecardResponse.model_validate(wire)
    assert result.compliance_pct == 6.1 and result.observed_minute_evidence == evidence
    assert calls[:2] == ["resolved_day_once", DAY] if use_today else calls[0] == DAY
    if consumer == "mcp":
        assert calls[-1] == "closed"


def test_public_home_projection_retains_diagnostic_and_source_scopes_both_public_routes():
    from verdify_schemas.api import PublicHomeMetrics

    evidence = parse_observed_minute_row(captured(), DAY)
    home = PublicHomeMetrics(
        generated_at=START + timedelta(days=1),
        greenhouse_id="vallery",
        climate_rows=1,
        climate_days=1,
        active_crops=1,
        plan_count=0,
        lesson_count=0,
        open_critical_high_alerts=0,
        data_health_status="ok",
        observed_minute_evidence=evidence,
    )
    assert PublicHomeMetrics.model_validate_json(home.model_dump_json()).observed_minute_evidence == evidence
    source = (ROOT / "api/main.py").read_text()
    assert source.count('score_day = generated_at.astimezone(ZoneInfo("America/Denver")).date()') == 2
    assert source.count("observed = await read_observed_minute_evidence(conn, score_day, greenhouse_id)") == 2


def test_publisher_unrepresentable_day_is_unavailable_not_an_exception():
    payload = parse_observed_minute_row(captured(), DAY).model_dump(mode="json")
    payload["day"] = "9999-12-31"
    assert "Unavailable" in publisher.observed_minute_block(payload)
