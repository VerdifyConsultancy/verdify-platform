"""Inherited Timescale chunks do not churn the MCP authority receipt."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/263-mcp-timescale-chunk-boundary-digest.sql"
RUNNER = ROOT / "scripts/c0-migration-delivery.py"
spec = importlib.util.spec_from_file_location("c0_delivery_263", RUNNER)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def test_generated_chunk_churn_and_static_acl_drift(isolated_pg):
    query = isolated_pg
    sql = MIGRATION.read_text()
    function = sql[
        sql.index("CREATE OR REPLACE FUNCTION public.fn_mcp_runtime_boundary_digest()") : sql.index(
            "\n-- A reviewed successor digest"
        )
    ]
    query("""
        CREATE EXTENSION pgcrypto;
        CREATE ROLE verdify_mcp_runtime NOLOGIN;
        CREATE ROLE verdify_mcp_runtime_login NOLOGIN;
        CREATE SCHEMA _timescaledb_internal;
        CREATE SCHEMA _timescaledb_catalog;
        CREATE TABLE _timescaledb_catalog.hypertable (
            id integer PRIMARY KEY, schema_name name, table_name name,
            compressed_hypertable_id integer);
        CREATE TABLE _timescaledb_catalog.chunk (
            id integer PRIMARY KEY, hypertable_id integer,
            schema_name name, table_name name, dropped boolean);
        CREATE TABLE public.weather_forecast (ts timestamptz NOT NULL);
        CREATE TABLE _timescaledb_internal._compressed_hypertable_14 (ts timestamptz);
        INSERT INTO _timescaledb_catalog.hypertable VALUES
            (7, 'public', 'weather_forecast', 14),
            (14, '_timescaledb_internal', '_compressed_hypertable_14', NULL);
        CREATE TABLE _timescaledb_internal._hyper_7_1_chunk ()
            INHERITS (public.weather_forecast);
        CREATE TABLE _timescaledb_internal.compress_hyper_14_1_chunk (ts timestamptz);
        INSERT INTO _timescaledb_catalog.chunk VALUES
            (1, 7, '_timescaledb_internal', '_hyper_7_1_chunk', false),
            (2, 14, '_timescaledb_internal', 'compress_hyper_14_1_chunk', false);
        GRANT SELECT ON public.weather_forecast,
            _timescaledb_internal._compressed_hypertable_14,
            _timescaledb_internal._hyper_7_1_chunk,
            _timescaledb_internal.compress_hyper_14_1_chunk TO verdify_mcp_runtime;
    """)
    query(function)

    def digest():
        return query("SELECT encode(public.fn_mcp_runtime_boundary_digest(), 'hex')")

    baseline = digest()
    query("""
        CREATE TABLE _timescaledb_internal._hyper_7_2_chunk ()
            INHERITS (public.weather_forecast);
        CREATE TABLE _timescaledb_internal.compress_hyper_14_2_chunk (ts timestamptz);
        INSERT INTO _timescaledb_catalog.chunk VALUES
            (3, 7, '_timescaledb_internal', '_hyper_7_2_chunk', false),
            (4, 14, '_timescaledb_internal', 'compress_hyper_14_2_chunk', false);
        GRANT SELECT ON _timescaledb_internal._hyper_7_2_chunk TO verdify_mcp_runtime;
        GRANT SELECT ON _timescaledb_internal.compress_hyper_14_2_chunk TO verdify_mcp_runtime;
    """)
    assert digest() == baseline

    query("REVOKE SELECT ON _timescaledb_internal._hyper_7_2_chunk FROM verdify_mcp_runtime")
    assert digest() != baseline
    query("GRANT SELECT ON _timescaledb_internal._hyper_7_2_chunk TO verdify_mcp_runtime")
    assert digest() == baseline

    query("REVOKE SELECT ON _timescaledb_internal.compress_hyper_14_2_chunk FROM verdify_mcp_runtime")
    assert digest() != baseline
    query("GRANT SELECT ON _timescaledb_internal.compress_hyper_14_2_chunk TO verdify_mcp_runtime")
    assert digest() == baseline

    query("GRANT UPDATE ON _timescaledb_internal._hyper_7_2_chunk TO verdify_mcp_runtime")
    assert digest() != baseline
    one_deviant = digest()
    query("ALTER TABLE _timescaledb_internal._hyper_7_2_chunk ENABLE ROW LEVEL SECURITY")
    assert digest() != one_deviant
    query("ALTER TABLE _timescaledb_internal._hyper_7_2_chunk DISABLE ROW LEVEL SECURITY")
    assert digest() == one_deviant
    query("GRANT UPDATE ON _timescaledb_internal._hyper_7_1_chunk TO verdify_mcp_runtime")
    assert digest() != one_deviant
    query("REVOKE UPDATE ON _timescaledb_internal._hyper_7_1_chunk FROM verdify_mcp_runtime")
    assert digest() == one_deviant
    query("REVOKE UPDATE ON _timescaledb_internal._hyper_7_2_chunk FROM verdify_mcp_runtime")
    assert digest() == baseline

    query("GRANT UPDATE ON _timescaledb_internal.compress_hyper_14_2_chunk TO verdify_mcp_runtime")
    assert digest() != baseline
    query("REVOKE UPDATE ON _timescaledb_internal.compress_hyper_14_2_chunk FROM verdify_mcp_runtime")
    assert digest() == baseline

    query("GRANT UPDATE ON public.weather_forecast TO verdify_mcp_runtime")
    assert digest() != baseline
    query("REVOKE UPDATE ON public.weather_forecast FROM verdify_mcp_runtime")
    assert digest() == baseline

    query("""
        CREATE TABLE _timescaledb_internal._hyper_7_3_chunk (ts timestamptz);
        GRANT SELECT ON _timescaledb_internal._hyper_7_3_chunk TO verdify_mcp_runtime;
    """)
    assert digest() != baseline  # A matching name without inheritance is static.


def test_migration_is_ledgered_and_fenced():
    sql = MIGRATION.read_text()
    assert "seq=262" in sql and "seq >= 263" in sql
    assert "filename='db/migrations/262-fixed-panel-native-callback-ledger.sql'" in sql
    assert "6b9ebfb1ee429fac2a31c07253014ccfbc1832e08307f7f0e0aad867fc46020e" in sql
    assert "c6e6952976cbc27f8342ae2304fb69ecdbec682c46ed1ddaeb8b7a00894d6bd7" in sql
    assert "81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45" in sql
    assert "COMMIT;" not in sql
    assert "DROP FUNCTION" not in sql
    assert delivery.SUCCESSOR_263_SHA256 == hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, 264)]
    pins = {
        name: getattr(delivery, f"SUCCESSOR_{seq}_SHA256")
        for seq, name in ((seq, getattr(delivery, f"SUCCESSOR_{seq}")) for seq in range(255, 264))
    }
    delivery.reviewed_post_254(later, pins)
    with pytest.raises(delivery.DeliveryError, match="reviewed 263 successor source drift"):
        delivery.reviewed_post_254(later, dict(pins, **{delivery.SUCCESSOR_263: "0" * 64}))


def test_runner_admits_only_exact_pending_263_hotfix_predecessor(monkeypatch):
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, 263)]
    ordinary = delivery.SUCCESSOR_262_DIGESTS
    hotfix = delivery.SUCCESSOR_263_HOTFIX_PREDECESSOR_MCP_DIGEST
    mcp = {
        "mcp": hotfix,
        "mcp_receipt": hotfix,
        "mcp_login": True,
        "mcp_arm_read": False,
        "mcp_experiment_read": False,
        "outcome_scorecard": True,
        "outcome_scorecard_exec": True,
        "outcome_band_exec": True,
        "outcome_season_exec": True,
        "outcome_zone_exec": True,
        "outcome_anchor_read": True,
        "outcome_profile_read": True,
    }

    def read(sql, _environment):
        if "'mcp_receipt'" in sql:
            return json.dumps(mcp)
        if "'api_receipt'" in sql:
            return json.dumps(
                {
                    "api": ordinary["verdify_api_runtime_login"],
                    "ingestor": ordinary["verdify_ingestor_runtime_login"],
                    "api_receipt": ordinary["verdify_api_runtime_login"],
                    "ingestor_receipt": ordinary["verdify_ingestor_runtime_login"],
                    "receipt_count": 2,
                    "column_update": True,
                    "table_update": False,
                }
            )
        return "fixture-ledger"

    monkeypatch.setattr(delivery, "psql", read)
    contract = {"version": delivery.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "fixture-ledger"}
    delivery.verify_post_249(contract, {}, later=later, hotfix_predecessor_263=True)
    with pytest.raises(delivery.DeliveryError, match="reviewed MCP ordinary boundary"):
        delivery.verify_post_249(contract, {}, later=later)
    with pytest.raises(delivery.DeliveryError, match="exact applied 262"):
        delivery.verify_post_249(contract, {}, later=later[:-1], hotfix_predecessor_263=True)
    mcp["mcp"] = delivery.SUCCESSOR_262_MCP_DIGEST
    with pytest.raises(delivery.DeliveryError, match="263 predecessor MCP boundary"):
        delivery.verify_post_249(contract, {}, later=later, hotfix_predecessor_263=True)
    mcp["mcp_receipt"] = delivery.SUCCESSOR_262_MCP_DIGEST
    delivery.verify_post_249(contract, {}, later=later, hotfix_predecessor_263=True)
    mcp["mcp"] = mcp["mcp_receipt"] = delivery.SUCCESSOR_263_MCP_DIGEST
    delivery.verify_post_249(contract, {}, later=later + [delivery.SUCCESSOR_263])
