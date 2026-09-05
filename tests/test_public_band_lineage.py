"""Public band-lineage SQL -> typed API proof on an isolated local PostgreSQL.

BAND_TRACE_TEST_PG_BIN selects server binaries, never an existing database.
No DB_DSN, provider, production socket or kubectl is used.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_api_public_output_policy import load_api

from verdify_schemas.api import PublicBandTraceLatest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/243-public-band-lineage.sql"
START = "2026-09-04T21:00:00Z"
END = "2026-09-04T21:04:00Z"
CALL = f"public.fn_public_band_trace_v2('{START}', '{END}', 'vallery')"


def sample():
    edges = {}
    for name, value in (("temp_low", 60), ("temp_high", 90), ("vpd_low", 0.5), ("vpd_high", 1.5)):
        edges[name] = dict(
            unit="°F" if name.startswith("temp") else "kPa",
            raw_slug=f"cfg___{name}___f_" if name.startswith("temp") else f"cfg___{name}__kpa_",
            desired_value=value,
            desired_recorded_at=START,
            desired_conflict=False,
            cfg_snapshot_value=value,
            cfg_snapshot_captured_at=START,
            cfg_snapshot_conflict=False,
            numeric_comparison="equal_numeric",
        )
    return dict(
        ts=START,
        greenhouse_id="vallery",
        lineage_contract_version=2,
        temp_avg=75,
        vpd_avg=1,
        reconstructed_both_in_band=True,
        desired_both_in_band=True,
        trace_quality_flag="unobservable_consumed_band",
        lineage=dict(
            edges=edges,
            band_source_snapshot="onchip_curve",
            diagnostic_captured_at=START,
            temp_target_snapshot=75,
            vpd_target_snapshot=1,
            target_snapshot_captured_at=START,
            raw_observation_freshness_verified=False,
            runtime_connection_identity_verified=False,
            consumed_band_verified=False,
            disposition="unobservable",
        ),
    )


def test_wire_never_promotes_matching_onchip_cfg_to_consumed_proof():
    result = PublicBandTraceLatest.model_validate(sample()).model_dump()
    assert result["fw_vpd_high"] is None
    assert result["readback_matches_fw_band"] is None
    assert result["lineage"]["disposition"] == "unobservable"
    bad = sample()
    bad["fw_vpd_high"] = 1.5
    with pytest.raises(ValidationError):
        PublicBandTraceLatest.model_validate(bad)
    bad = sample()
    bad["lineage"]["consumed_band_verified"] = True
    with pytest.raises(ValidationError):
        PublicBandTraceLatest.model_validate(bad)


@pytest.mark.parametrize(
    "change", ["missing_version", "old_version", "missing_edge", "nonfinite", "wrong_unit", "wrong_slug"]
)
def test_incomplete_or_invalid_wire_is_rejected(change):
    row = sample()
    if change == "missing_version":
        del row["lineage_contract_version"]
    elif change == "old_version":
        row["lineage_contract_version"] = 1
    elif change == "missing_edge":
        del row["lineage"]["edges"]["temp_low"]
    elif change == "wrong_unit":
        row["lineage"]["edges"]["vpd_low"]["unit"] = "°F"
    elif change == "wrong_slug":
        row["lineage"]["edges"]["vpd_high"]["raw_slug"] = "desired_vpd"
    else:
        row["lineage"]["edges"]["temp_low"]["desired_value"] = float("nan")
    with pytest.raises(ValidationError):
        PublicBandTraceLatest.model_validate(row)


def test_missing_migration_is_503_not_legacy_fallback(monkeypatch):
    api = load_api()

    async def now():
        return datetime.now(UTC)

    async def missing(*args):
        raise api.asyncpg.exceptions.UndefinedFunctionError("synthetic")

    monkeypatch.setattr(api, "_fetch_public_band_trace_generated_at", now)
    monkeypatch.setattr(api, "_fetch_public_band_trace_latest", missing)
    monkeypatch.setattr(api, "_fetch_public_band_trace_summary", missing)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.public_band_trace())
    assert exc.value.status_code == 503
    assert not api._PUBLIC_BAND_TRACE_CACHE


def test_unsupported_greenhouse_does_not_call_database():
    api = load_api()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.public_band_trace(greenhouse_id="unconfigured"))
    assert exc.value.status_code == 422


@pytest.fixture
def isolated_pg(tmp_path):
    bin_dir = os.environ.get("BAND_TRACE_TEST_PG_BIN")
    if not bin_dir:
        pytest.skip("set BAND_TRACE_TEST_PG_BIN to run isolated PostgreSQL proof")
    # Socket path must fit the Unix 108-byte limit; pytest tmp_path can be long.
    import tempfile

    cluster = Path(tempfile.mkdtemp(prefix="band_trace-pg-"))
    pg_bin = Path(bin_dir)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["LC_ALL"] = "C"

    def run(args, **kwargs):
        result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=60, **kwargs)
        assert result.returncode == 0, result.stderr  # only our synthetic local fixture
        return result

    started = False
    try:
        run(
            [
                str(pg_bin / "initdb"),
                "-D",
                str(cluster / "data"),
                "-U",
                "band_trace_fixture",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        run(
            [
                str(pg_bin / "pg_ctl"),
                "-D",
                str(cluster / "data"),
                "-l",
                str(cluster / "server.log"),
                "-o",
                f"-k {cluster} -c listen_addresses='' -p 55472",
                "-w",
                "start",
            ]
        )
        started = True

        def query(sql):
            result = run(
                [
                    str(pg_bin / "psql"),
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    str(cluster),
                    "-p",
                    "55472",
                    "-U",
                    "band_trace_fixture",
                    "-d",
                    "postgres",
                    "-qAt",
                ],
                input=sql,
            )
            return result.stdout.strip()

        yield query
    finally:
        if started:
            run([str(pg_bin / "pg_ctl"), "-D", str(cluster / "data"), "-m", "fast", "-w", "stop"])
        # Only this fixture's generated cluster, never an existing DB/workspace.
        shutil.rmtree(cluster)


def baseline():
    return """
CREATE ROLE verdify_api_runtime;
CREATE ROLE band_trace_denied;
CREATE TABLE public.climate (
    ts timestamptz, greenhouse_id text, temp_avg float8, vpd_avg float8,
    house_temp_target_f float8 DEFAULT 75, house_vpd_target float8 DEFAULT 1);
CREATE TABLE public.setpoint_changes (
    ts timestamptz, greenhouse_id text, parameter text, value float8, expired_at timestamptz);
CREATE TABLE public.setpoint_snapshot (
    ts timestamptz, greenhouse_id text, parameter text, value float8);
CREATE TABLE public.diagnostics (ts timestamptz, greenhouse_id text, band_source text);
CREATE FUNCTION public.fn_band_setpoints(timestamptz)
RETURNS TABLE(temp_low float8, temp_high float8, vpd_low float8, vpd_high float8)
LANGUAGE sql STABLE AS $$ SELECT 60::float8, 90::float8, 0.5::float8, 1.5::float8 $$;
CREATE FUNCTION public.fn_band_trace(timestamptz,timestamptz,text)
RETURNS integer LANGUAGE sql AS $$ SELECT 119 $$;
CREATE VIEW public.legacy_dependent AS SELECT public.fn_band_trace(now(),now(),'vallery') AS value;
INSERT INTO public.climate(ts,greenhouse_id,temp_avg,vpd_avg) VALUES
('2026-09-04T21:00Z','vallery',75,1),
('2026-09-04T21:01Z','vallery',95,NULL),
('2026-09-04T21:02Z','vallery',NULL,2),
('2026-09-04T21:03Z','vallery',NULL,NULL),
('2026-09-04T21:01Z','other',-999,99);
INSERT INTO public.setpoint_changes
SELECT '2026-09-04T20:59Z','vallery',p,v,NULL FROM
(VALUES ('temp_low',60),('temp_high',90),('vpd_low',0.5),('vpd_high',1.5)) x(p,v);
INSERT INTO public.setpoint_snapshot SELECT ts,greenhouse_id,parameter,value FROM public.setpoint_changes;
INSERT INTO public.diagnostics VALUES ('2026-09-04T20:59Z','vallery','onchip_curve');
"""


def as_json(query, sql):
    return json.loads(query(f"SELECT coalesce(jsonb_agg(to_jsonb(r)), '[]') FROM ({sql}) r"))


def test_real_sql_wire_acl_and_forward_rollback(isolated_pg, monkeypatch):
    q = isolated_pg
    q(baseline())
    legacy = q("SELECT 'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure::oid")
    # Whole-transaction rollback leaves the old function/dependent view untouched.
    q("BEGIN;\n" + MIGRATION.read_text() + "\nROLLBACK;")
    assert q("SELECT to_regprocedure('public.fn_public_band_trace_v2(timestamptz,timestamptz,text)') IS NULL") == "t"
    assert q("SELECT value FROM public.legacy_dependent") == "119"
    q(MIGRATION.read_text())
    installed = q("SELECT 'public.fn_public_band_trace_v2(timestamptz,timestamptz,text)'::regprocedure::oid")
    q(MIGRATION.read_text())
    assert q("SELECT 'public.fn_public_band_trace_v2(timestamptz,timestamptz,text)'::regprocedure::oid") == installed
    assert q("SELECT 'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure::oid") == legacy
    assert q("SELECT count(*) FROM public.climate") == "5"
    assert q("SELECT has_table_privilege('verdify_api_runtime','public.climate','SELECT')") == "f"
    assert (
        q(
            "SELECT has_function_privilege('band_trace_denied','public.fn_public_band_trace_v2(timestamptz,timestamptz,text)','EXECUTE')"
        )
        == "f"
    )
    assert q(f"SET ROLE verdify_api_runtime; SELECT count(*) FROM {CALL}; RESET ROLE") == "4"
    rows = as_json(q, f"SELECT * FROM {CALL}")
    assert len(rows) == 4
    assert [r["reconstructed_both_in_band"] for r in rows] == [True, None, None, None]
    assert [r["reconstructed_temp_in_band"] for r in rows] == [True, False, None, None]
    assert [r["reconstructed_vpd_in_band"] for r in rows] == [True, None, False, None]
    assert rows[0]["lineage"]["edges"]["vpd_high"]["numeric_comparison"] == "equal_numeric"
    assert rows[0]["lineage"]["band_source_snapshot"] == "onchip_curve"
    for row in rows:
        PublicBandTraceLatest.model_validate(row)

    # Capture the ACTUAL API summary query, not a parallel hand-written aggregate.
    api = load_api()

    class Connection:
        async def fetchrow(self, sql, *args):
            self.sql = sql

    connection = Connection()

    class Acquire:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *args):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    api.pool = Pool()
    asyncio.run(api._fetch_public_band_trace_summary(1, "vallery"))
    summary_sql = connection.sql.replace("now() - ($1::int * interval '1 hour')", f"'{START}'::timestamptz")
    summary_sql = summary_sql.replace("now()", f"'{END}'::timestamptz").replace("$2", "'vallery'")
    summary = as_json(q, summary_sql)[0]
    assert summary["sample_count"] == 4
    assert summary["reconstructed_temp_eligible_samples"] == 2
    assert summary["reconstructed_vpd_eligible_samples"] == 2
    assert summary["reconstructed_both_eligible_samples"] == 1
    assert summary["reconstructed_temp_compliance_pct"] == 50
    assert summary["reconstructed_vpd_compliance_pct"] == 50
    assert summary["reconstructed_both_compliance_pct"] == 100

    # Driver-default JSONB arrives as text. Verify SQL -> actual endpoint -> public policy.
    wire_row = copy.deepcopy(rows[-1])
    wire_row["lineage"] = json.dumps(wire_row["lineage"])

    async def latest(*args):
        return wire_row

    async def aggregate(*args):
        return summary

    async def now():
        return datetime.now(UTC)

    monkeypatch.setattr(api, "_fetch_public_band_trace_generated_at", now)
    monkeypatch.setattr(api, "_fetch_public_band_trace_latest", latest)
    monkeypatch.setattr(api, "_fetch_public_band_trace_summary", aggregate)
    wire = asyncio.run(api.public_band_trace(hours=1)).model_dump(mode="json")
    assert wire["summary"]["reconstructed_both_compliance_pct"] == 100
    assert wire["summary"]["readback_match_pct"] is None
    assert wire["summary"]["consumed_band_eligible_samples"] == 0
    assert wire["summary"]["crop_both_compliance_pct"] is None
    assert wire["latest"]["crop_temp_low"] is None
    assert wire["semantics"]["reconstructed_basis"] == "current_house_anchor_curve_not_versioned_crop_targets"
    assert wire["semantics"]["physical_proof_eligible"] is False
    assert wire["latest"]["temp_avg"] is None

    # Latest conflicting snapshot is not arbitrarily picked or skipped to an older value.
    q("""
INSERT INTO public.setpoint_snapshot VALUES
('2026-09-04T21:01Z','vallery','temp_low',60),
('2026-09-04T21:01Z','vallery','temp_low',61);
INSERT INTO public.setpoint_changes VALUES
('2026-09-04T21:01Z','vallery','vpd_high',1.5,NULL),
('2026-09-04T21:01Z','vallery','vpd_high',2,NULL);
""")
    rows = as_json(q, f"SELECT * FROM {CALL}")
    edge = rows[1]["lineage"]["edges"]["temp_low"]
    assert edge["cfg_snapshot_conflict"] is True
    assert edge["cfg_snapshot_value"] is None
    assert edge["numeric_comparison"] == "unavailable"
    assert rows[1]["lineage"]["edges"]["vpd_high"]["desired_value"] is None

    q("""
INSERT INTO public.climate(ts,greenhouse_id,temp_avg,vpd_avg)
VALUES ('2026-09-04T21:20Z','vallery',75,1);
INSERT INTO public.setpoint_snapshot VALUES ('2026-09-04T21:21Z','vallery','temp_low',99);
""")
    stale = as_json(
        q, "SELECT * FROM public.fn_public_band_trace_v2('2026-09-04T21:20Z','2026-09-04T21:21Z','vallery')"
    )[0]
    assert stale["lineage"]["edges"]["temp_low"]["cfg_snapshot_value"] is None
    assert stale["lineage"]["band_source_snapshot"] is None
    assert stale["lineage"]["edges"]["temp_low"]["desired_value"] == 60

    # The 900-second database lookback is inclusive, future snapshots are excluded.
    q("""
INSERT INTO public.climate(ts,greenhouse_id,temp_avg,vpd_avg)
VALUES ('2026-09-04T21:14Z','vallery',75,1), ('2026-09-04T21:15Z','vallery',75,1);
INSERT INTO public.setpoint_changes VALUES
('2026-09-04T21:13Z','vallery','temp_high',999,'2026-09-04T21:14Z');
""")
    cutoff = as_json(
        q, "SELECT * FROM public.fn_public_band_trace_v2('2026-09-04T21:14Z','2026-09-04T21:16Z','vallery')"
    )
    assert cutoff[0]["lineage"]["edges"]["temp_high"]["cfg_snapshot_value"] == 90
    assert cutoff[1]["lineage"]["edges"]["temp_high"]["cfg_snapshot_value"] is None
    assert cutoff[0]["lineage"]["edges"]["temp_high"]["desired_value"] == 90
    assert cutoff[0]["lineage"]["raw_observation_freshness_verified"] is False

    # Invalid telemetry and inverted/nonfinite bands stay unavailable, not passes.
    q("""
INSERT INTO public.climate(ts,greenhouse_id,temp_avg,vpd_avg)
VALUES ('2026-09-04T21:30Z','vallery','NaN','Infinity');
INSERT INTO public.setpoint_changes VALUES
('2026-09-04T21:29Z','vallery','temp_low',100,NULL),
('2026-09-04T21:29Z','vallery','vpd_high','NaN',NULL);
""")
    invalid = as_json(
        q, "SELECT * FROM public.fn_public_band_trace_v2('2026-09-04T21:30Z','2026-09-04T21:31Z','vallery')"
    )[0]
    assert invalid["temp_avg"] is None
    assert invalid["vpd_avg"] is None
    assert invalid["desired_both_in_band"] is None
    assert invalid["lineage"]["edges"]["vpd_high"]["desired_value"] is None

    # Check the actual function security configuration as well as its grants.
    assert (
        q(
            "SELECT prosecdef AND proconfig @> ARRAY['search_path=pg_catalog, public'] FROM pg_proc WHERE oid='public.fn_public_band_trace_v2(timestamptz,timestamptz,text)'::regprocedure"
        )
        == "t"
    )
    q(f"""
SET ROLE band_trace_denied;
DO $test$ BEGIN
    BEGIN PERFORM {CALL}; RAISE EXCEPTION 'unauthorized read succeeded';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $test$;
RESET ROLE;
""")

    # Empty resolver output must not erase climate rows. Missing is not a zero score.
    q(
        "CREATE OR REPLACE FUNCTION public.fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8,temp_high float8,vpd_low float8,vpd_high float8) LANGUAGE sql STABLE AS $$ SELECT 60::float8,90::float8,0.5::float8,1.5::float8 WHERE false $$"
    )
    rows = as_json(q, f"SELECT * FROM {CALL}")
    assert len(rows) == 4
    assert all(r["reconstructed_both_in_band"] is None for r in rows)
    summary = as_json(q, summary_sql)[0]
    assert summary["reconstructed_both_eligible_samples"] == 0
    assert summary["reconstructed_both_compliance_pct"] is None
    q("""
DO $test$ BEGIN
    BEGIN
        PERFORM public.fn_public_band_trace_v2('2026-09-01','2026-09-10','vallery');
        RAISE EXCEPTION 'unbounded window accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
    BEGIN
        PERFORM public.fn_public_band_trace_v2('2026-09-01','2026-09-02','other');
        RAISE EXCEPTION 'other greenhouse accepted';
    EXCEPTION WHEN invalid_parameter_value THEN NULL; END;
END $test$;
""")
