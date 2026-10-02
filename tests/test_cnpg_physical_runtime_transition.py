"""Closed physical profile tests; fabricated witnesses are not recovery proof."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from test_cnpg_restore_qualification import witnesses
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401 — reuse isolated socket fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("physical", ROOT / "scripts/cnpg-physical-runtime-transition.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
t = p.t


@pytest.mark.parametrize("profile", t.PHYSICAL_TARGETS)
def test_rollback_ddl_retains_original_history_and_native_digest_bodies(profile):
    _, before = witnesses()
    ddl, bodies = t.ddl(physical_target=profile)
    assert f"CREATE TABLE {t.PHYSICAL_TABLE}" in ddl
    assert f"CREATE TABLE {t.TABLE}" not in ddl
    assert profile in ddl and "cluster_name" in ddl
    assert "FUNCTION public.fn_runtime_ordinary_boundary_digest" not in ddl
    assert "FUNCTION public.fn_mcp_runtime_boundary_digest" not in ddl
    assert "ALTER ROLE" not in ddl and "schema_migrations" not in ddl
    assert set(bodies) == {"fn_runtime_attest_ordinary_login", "fn_mcp_runtime_attest_ordinary_login"}
    rows = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    sql = t.emit_sql(before, physical_target=profile, logical_receipts=rows)
    assert sql.rstrip().endswith("ROLLBACK;") and "\nCOMMIT;" not in sql
    assert f"LOCK TABLE {t.TABLE} IN SHARE MODE;" in sql
    assert sql.count("physical admission refuses copied logical receipt drift") == 2
    assert "IS DISTINCT FROM v_original" in sql
    assert "inet_client_addr() IS NOT NULL" in sql and "current_user<>session_user" in sql
    assert f"INSERT INTO {t.PHYSICAL_TABLE}" not in sql
    assert "cnpg-physical-runtime-transition-v1" in sql


@pytest.mark.parametrize("profile", ("verdify-prod", "verdify-db", "other", "verdify-cnpg-rehearsal"))
def test_only_two_new_source_owned_names_allowed(profile):
    with pytest.raises(ValueError, match="unsupported physical"):
        t.ddl(physical_target=profile)


def test_physical_profile_cannot_omit_inherited_history_or_touch_logical_public_mode():
    _, before = witnesses()
    with pytest.raises(ValueError, match="history custody"):
        t.emit_sql(before, physical_target=t.PHYSICAL_TARGETS[0])
    with pytest.raises(ValueError, match="history custody"):
        t.emit_sql(before, logical_receipts=[])
    old, _ = t.ddl()
    assert t.PHYSICAL_TABLE not in old and f"CREATE TABLE {t.TABLE}" in old
    assert "verdify-cnpg-pitr-" not in old


def test_dataset_parity_refuses_missing_time_count_or_compressed_ownership():
    data = {
        "schema": "cnpg-physical-data-parity-v1",
        "database": t.DATABASE,
        "relations": [{"relation": "public.example", "count": 3, "time_ranges": {"ts": ["start", "end"]}}],
        "timescale_owners": [
            {
                "parent": "public.example",
                "parent_owner": "verdify",
                "chunk": "internal.chunk",
                "chunk_owner": "verdify",
                "compressed": "internal.compressed",
                "compressed_owner": "verdify",
            }
        ],
    }
    p.validate_dataset(data, copy.deepcopy(data))
    wrong = copy.deepcopy(data)
    wrong["timescale_owners"][0]["compressed_owner"] = "postgres"
    with pytest.raises(ValueError, match="dataset/count/time/owner drift"):
        p.validate_dataset(data, wrong)
    wrong = copy.deepcopy(data)
    wrong["relations"][0]["time_ranges"] = {"ts": [None, "end"]}
    with pytest.raises(ValueError, match="timestamp endpoints"):
        p.validate_dataset(wrong, wrong)


def test_install_result_or_raw_history_is_not_rollback_qualification():
    _, before = witnesses()
    profile = t.PHYSICAL_TARGETS[0]
    record = {
        "version": p.VERSION,
        "mode": "install",
        "ddl_sha256": "a" * 64,
        "before_witness": before,
        "post_witness": before,
    }
    with pytest.raises(ValueError, match="rollback qualification"):
        p.checked_physical_qualification(before, record, profile)


@pytest.mark.parametrize(
    "bootstrap,sha",
    [
        (False, "a03bba9b6a2add17fc1602c535a8233b63cbfd823e52e552689a02d2c7b96004"),
        (True, "685b63cafa536e85953900f783475ff43a8d53ac44283194eef78bc3d7ba8fa7"),
    ],
)
def test_original_logical_ddl_remains_exact_882_source_bytes(bootstrap, sha):
    assert t.digest(t.ddl(bootstrap)[0].encode()) == sha


def test_native_dataset_full_public_inventory_cannot_be_sampled():
    data = {
        "schema": "cnpg-physical-data-parity-v1",
        "database": t.DATABASE,
        "relations": [{"relation": "public.example", "count": 3, "time_ranges": {}}],
        "timescale_owners": [
            {
                "parent": "public.example",
                "parent_owner": "verdify",
                "chunk": "internal.chunk",
                "chunk_owner": "verdify",
                "compressed": None,
                "compressed_owner": None,
            }
        ],
    }
    catalog = [["relation", "public.example", "a" * 64], ["relation", "public.omitted", "b" * 64]]
    with pytest.raises(ValueError, match="inventory omits"):
        p.validate_dataset(data, data, catalog)


def test_recovered_marker_claim_without_native_content_and_original_payload_is_refused():
    markers = {
        "markers": {
            key: {"marker_id": "marker-" + key.lower(), "payload_sha256": key.lower() * 64} for key in ("A", "B", "C")
        }
    }
    binding = {"cluster": t.PHYSICAL_TARGETS[0]}
    recovery = {"binding": binding, "marker_ids": ["marker-a"]}
    capture = {
        "binding": binding,
        "database": "rehearsal_bootstrap",
        "server_version_num": 160013,
        "cluster_name": t.PHYSICAL_TARGETS[0],
        "pg_is_in_recovery": False,
        "markers": [{"marker_id": "marker-a", "payload_sha256": "a" * 64}],
    }
    p.validate_sentinel_capture(capture, recovery, markers)
    capture["markers"].append({"marker_id": "marker-b", "payload_sha256": "b" * 64})
    with pytest.raises(ValueError, match="row or payload mismatch"):
        p.validate_sentinel_capture(capture, recovery, markers)


@pytest.mark.parametrize("profile", (p.pitr.SOURCE, *t.PHYSICAL_TARGETS))
def test_native_dataset_sql_is_read_only_full_inventory_and_fixed_identity(profile):
    sql = p.dataset_sql(profile)
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql
    assert f"current_setting('cluster_name')<>'{profile}'" in sql
    assert "current_user<>session_user" in sql and "inet_client_addr() IS NOT NULL" in sql
    assert "n.nspname='public'" in sql and "c.relkind IN ('r','p','v','m','S')" in sql
    assert "c.compressed_chunk_id" in sql and "WHERE h.schema_name='public' AND NOT c.dropped" in sql
    assert not any(
        word in sql for word in ("INSERT INTO", "UPDATE public", "ALTER TABLE", "CREATE TABLE", "DELETE FROM")
    )
    assert "min(%I)::text,max(%I)::text" in sql


def test_native_private_fixture_dataset_collector_handles_uncompressed_and_compressed(private_pg):  # noqa: F811
    # This PG16 fixture has no Timescale extension. Only the collector joins,
    # counts, ranges and READ ONLY behavior are tested; no actual recovery proof.
    q = private_pg
    q("""CREATE SCHEMA _timescaledb_catalog;
      CREATE TABLE _timescaledb_catalog.hypertable(id int,schema_name text,table_name text);
      CREATE TABLE _timescaledb_catalog.chunk(id int,hypertable_id int,schema_name text,table_name text,
        compressed_chunk_id int,dropped boolean);
      CREATE TABLE public.fixture_data(ts timestamptz);
      INSERT INTO public.fixture_data VALUES('2026-10-01T10:00:00Z'),('2026-10-01T10:01:00Z');
      CREATE TABLE _timescaledb_catalog.fixture_chunk(ts timestamptz);
      CREATE TABLE _timescaledb_catalog.fixture_compressed(ts timestamptz);
      INSERT INTO _timescaledb_catalog.hypertable VALUES(1,'public','fixture_data');
      INSERT INTO _timescaledb_catalog.chunk VALUES
        (1,1,'_timescaledb_catalog','fixture_chunk',NULL,false),
        (2,1,'_timescaledb_catalog','fixture_data_second',3,false),
        (3,9,'_timescaledb_catalog','fixture_compressed',NULL,false);
      CREATE TABLE _timescaledb_catalog.fixture_data_second(ts timestamptz);""")
    emitted = p.dataset_sql(p.pitr.SOURCE)
    native_version = q("SHOW server_version_num")
    fixture_sql = emitted.replace("::int<>160013", "::int<>" + native_version)
    fixture_sql = fixture_sql.replace(
        "OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2'", "OR false"
    )
    result = json.loads(q(fixture_sql))
    data = next(row for row in result["relations"] if row["relation"] == "public.fixture_data")
    assert data["count"] == 2 and data["time_ranges"]["ts"] == ["2026-10-01 10:00:00+00", "2026-10-01 10:01:00+00"]
    assert len(result["timescale_owners"]) == 2
    assert result["timescale_owners"][0]["compressed"] is None
    assert result["timescale_owners"][1]["compressed_owner"] == "verdify"
    bad = q(emitted, check=False)
    assert bad.returncode != 0 and "refuses target/session" in bad.stderr
