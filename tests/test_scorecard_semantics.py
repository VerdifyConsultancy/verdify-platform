"""#371 binary/graded separation, including a disposable PostgreSQL proof.

Set SCORECARD_TEST_PG_BIN to a local PostgreSQL bin directory for integration
coverage. This starts its OWN private-socket cluster; never uses DB_DSN/PGHOST
or production credentials. CI without server binaries still runs wire tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from verdify_schemas.mcp_responses import ScorecardResponse

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/241-scorecard-binary-semantics.sql"


@pytest.mark.parametrize("version", [None, 1, 3])
def test_unverified_contract_never_publishes_grade_as_binary(version):
    card = ScorecardResponse(
        scorecard_contract_version=version,
        compliance_pct=85.8,
        temp_compliance_pct=64.1,
        vpd_compliance_pct=58.4,
        total_stress_h=1.2,
    )
    evidence = card.climate_evidence()
    for key in ("both_axis_compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "stress_axis_hours"):
        assert evidence[key] is None
    assert all(v is None for v in evidence["stress_breakdown"].values())
    assert card.model_dump()["metric_semantics"]["binary_fields_verified"] is False


def test_binary_and_graded_are_distinct_and_zero_survives():
    card = ScorecardResponse.from_metric_rows(
        [
            ("scorecard_contract_version", 2),
            ("compliance_pct", 6.1),
            ("temp_compliance_pct", 34.3),
            ("vpd_compliance_pct", 30.8),
            ("compliance_v2_attributable_pct", 85.8),
            ("graded_temp_compliance_pct", 64.1),
            ("graded_vpd_compliance_pct", 58.4),
            ("heat_stress_h", 0),
        ]
    )
    evidence = card.climate_evidence()
    assert evidence["both_axis_compliance_pct"] == 6.1
    assert evidence["graded_compliance_attributable_pct"] == 85.8
    assert evidence["temp_compliance_pct"] == 34.3
    assert evidence["graded_temp_compliance_pct"] == 64.1
    assert evidence["vpd_compliance_pct"] == 30.8
    assert evidence["graded_vpd_compliance_pct"] == 58.4
    assert evidence["stress_breakdown"]["heat_h"] == 0
    assert evidence["stress_breakdown"]["cold_h"] is None
    semantics = json.loads(card.model_dump_json())["metric_semantics"]
    assert semantics["coverage_status"] == "unverified"
    for key in ("fixed_sensor_panel", "center_probe_measured", "duration_weighted", "crop_outcome_eligible"):
        assert semantics[key] is False


@pytest.mark.parametrize("version", [None, 2])
def test_publisher_withholds_unversioned_binary_claims(version):
    spec = importlib.util.spec_from_file_location("evidence_publisher", ROOT / "scripts/update-evidence-snapshots.py")
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    rendered = publisher.planning_block(
        {
            "planning_quality": {
                "metric_semantics": {"contract_version": version},
                "both_axis_compliance_pct": 6.1,
                "graded_compliance_attributable_pct": 85.8,
            },
        }
    )
    assert ("6.1%" in rendered) is (version == 2)
    assert "85.8%" in rendered
    assert "not binary compliance" in rendered
    assert "Coverage is unverified" in rendered


def test_public_consumers_use_shared_projection():
    source = (ROOT / "api/main.py").read_text()
    assert source.count(").climate_evidence()") == 2
    assert source.count("**climate_evidence,") == 2
    assert 'compliance_pct_today=climate_evidence["both_axis_compliance_pct"]' in source
    assert '"both_axis_compliance_pct": scorecard.get(' not in source


def test_standalone_planner_keeps_grade_separate_and_missing_absent():
    from planner_graph.clients.db import scorecard_context

    assert "compliance_pct" not in scorecard_context({"compliance_pct": 85.8})
    context = scorecard_context(
        {
            "scorecard_contract_version": 2,
            "compliance_pct": 0,
            "compliance_v2_attributable_pct": 85.8,
            "vpd_compliance_pct": None,
        }
    )
    assert context["compliance_pct"] == 0
    assert context["compliance_v2_attributable_pct"] == 85.8
    assert "vpd_compliance_pct" not in context
    assert "Coverage unverified" in context["measurement_basis"]
    assert "Never widen" in context["score_basis"]


@pytest.fixture
def isolated_pg(tmp_path):
    bin_dir = os.environ.get("SCORECARD_TEST_PG_BIN")
    if not bin_dir:
        pytest.skip("set SCORECARD_TEST_PG_BIN to run isolated PostgreSQL proof")
    # Socket path must fit the Unix 108-byte limit; pytest tmp_path can be long.
    import tempfile

    cluster = Path(tempfile.mkdtemp(prefix="scorecard-pg-"))
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
                "scorecard_fixture",
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
                    "scorecard_fixture",
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


def _baseline_sql():
    old_view = (ROOT / "db/migrations/206-water-est-completed-day-guard.sql").read_text()
    old_function = (ROOT / "db/migrations/203-materialized-daily-kpi-scorecard.sql").read_text()
    old_function = old_function[old_function.index("\nCREATE OR REPLACE FUNCTION") :]
    old_performance = (ROOT / "db/migrations/194-scope-aware-resource-accounting.sql").read_text()
    old_performance = old_performance[
        old_performance.index("CREATE OR REPLACE VIEW public.v_planner_performance AS") : old_performance.index(
            "CREATE OR REPLACE VIEW public.v_daily_kpi AS"
        )
    ]
    columns = sorted(set(re.findall(r"\bd\.([a-z0-9_]+)", old_view)))
    types = {"date": "date PRIMARY KEY", "greenhouse_id": "text", "notes": "text", "captured_at": "timestamptz"}
    declarations = ", ".join(f"{c} {types.get(c, 'double precision')}" for c in columns)
    return f"""
CREATE ROLE verdify_api_runtime;
CREATE ROLE verdify_ingestor_runtime;
CREATE TABLE public.daily_summary ({declarations});
CREATE TABLE public.v_water_attribution_daily (
    date date, greenhouse_id text, quality_filtered_meter_gal float8,
    climate_wetting_gal float8, available_for_scoring boolean);
CREATE TABLE public.v_runtime_energy_daily (
    date date, greenhouse_id text, modeled_kwh float8, available_for_scoring boolean);
CREATE TABLE public.v_dli_daily (
    date date, greenhouse_id text, crop_dli_mol_m2_day float8, availability text,
    unavailable_reason text, provenance text, validity_revision text,
    valid_from timestamptz, valid_to timestamptz);
INSERT INTO daily_summary (date, greenhouse_id, compliance_pct, temp_compliance_pct,
    vpd_compliance_pct, compliance_v2_attributable_pct, compliance_v2_raw_pct,
    graded_temp_compliance_pct, graded_vpd_compliance_pct, stress_hours_heat,
    stress_hours_cold, stress_hours_vpd_high, stress_hours_vpd_low, graded_stress_hours_heat,
    graded_stress_hours_cold, graded_stress_hours_vpd_high, graded_stress_hours_vpd_low, cost_total)
VALUES ('2026-09-04', 'vallery', 6.1, 34.3, 30.8, 85.8, 60, 64.1, 58.4, 4, 0, 5, 0, 1, 0, 2, 0, 10),
       ('2026-09-03', 'vallery', 10, 20, 30, 80, 60, 60, 60, 2, 0, 3, 0, 1, 0, 1, 0, 10),
       ('2026-09-02', 'vallery', 0, 0, 0, 70, 60, 50, 50, 1, 0, 1, 0, 0.5, 0, 0.5, 0, 10),
       ('2026-09-01', 'vallery', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
        NULL, NULL, NULL, NULL, NULL);
INSERT INTO v_water_attribution_daily VALUES ('2026-09-03', 'vallery', 120, 20, true);
INSERT INTO v_runtime_energy_daily VALUES ('2026-09-03', 'vallery', 2, true);
CREATE MATERIALIZED VIEW public.mv_daily_kpi AS SELECT 1 AS placeholder;
{old_view}
{old_performance}
{old_function}
REVOKE EXECUTE ON FUNCTION fn_planner_scorecard(date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_planner_scorecard(date) TO verdify_api_runtime, verdify_ingestor_runtime;
GRANT SELECT ON mv_daily_kpi, v_planner_performance TO verdify_api_runtime, verdify_ingestor_runtime;
-- A dependent rowtype facade must survive: dropping/recreating the MV fails.
CREATE FUNCTION fixture_kpi_row() RETURNS SETOF mv_daily_kpi
LANGUAGE sql AS 'SELECT * FROM mv_daily_kpi';
"""


def test_migration_real_postgres_values_roles_oids_and_rollback(isolated_pg):
    query = isolated_pg
    query(_baseline_sql())
    card_query = "SELECT jsonb_object_agg(metric, value) FROM fn_planner_scorecard('2026-09-04');"
    before = json.loads(query(card_query))
    assert before["compliance_pct"] == 85.8  # reproduce the actual defect
    identity_sql = """
SELECT jsonb_build_object('view', 'v_daily_kpi'::regclass::oid,
    'mv', 'mv_daily_kpi'::regclass::oid, 'function', p.oid,
    'owner', p.proowner, 'acl', p.proacl::text, 'definer', p.prosecdef,
    'mv_acl', c.relacl::text, 'mv_owner', c.relowner)
FROM pg_proc p CROSS JOIN pg_class c
WHERE p.oid = 'fn_planner_scorecard(date)'::regprocedure
AND c.oid = 'mv_daily_kpi'::regclass;
"""
    identities = query(identity_sql)
    migration = MIGRATION.read_text()
    query("BEGIN;\n" + migration + "\nROLLBACK;")
    assert json.loads(query(card_query)) == before
    assert query(identity_sql) == identities
    assert query("SELECT to_regclass('v_scorecard_climate_diagnostics') IS NULL;") == "t"
    query("BEGIN;\n" + migration + "\nCOMMIT;")
    assert query(identity_sql) == identities
    after = json.loads(query("SET ROLE verdify_api_runtime; " + card_query))
    assert after["scorecard_contract_version"] == 2
    assert after["compliance_pct"] == 6.1
    assert after["temp_compliance_pct"] == 34.3
    assert after["vpd_compliance_pct"] == 30.8
    assert after["compliance_v2_attributable_pct"] == 85.8
    assert after["graded_temp_compliance_pct"] == 64.1
    assert after["graded_vpd_compliance_pct"] == 58.4
    assert after["total_stress_h"] == 9
    assert after["graded_heat_stress_h"] == 1
    assert after["7d_avg_compliance"] == 5  # excludes null, retains zero
    for metric in (
        "planner_score",
        "cost_total",
        "water_gal",
        "kwh",
        "7d_avg_score",
        "planner_score_resource_weight_pct",
        "resource_terms_available",
    ):
        assert after[metric] == before[metric]
    assert query("SELECT compliance_pct FROM v_planner_performance WHERE date='2026-09-04';") == "6.1"
    assert (
        query("SELECT compliance_pct IS NULL AND total_stress_h IS NULL FROM mv_daily_kpi WHERE date='2026-09-01';")
        == "t"
    )
    assert query("SELECT compliance_pct FROM mv_daily_kpi WHERE date='2026-09-02';") == "0.0"
    assert query("SELECT has_table_privilege('verdify_api_runtime', 'daily_summary', 'SELECT');") == "f"
    assert (
        query("SELECT has_table_privilege('verdify_api_runtime', 'v_scorecard_climate_diagnostics', 'INSERT');") == "f"
    )
    assert ScorecardResponse.from_metric_rows(list(after.items())).climate_evidence()["both_axis_compliance_pct"] == 6.1
