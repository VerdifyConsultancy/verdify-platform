"""Closed physical profile tests; fabricated witnesses are not recovery proof."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cnpg_restore_qualification import witnesses
from test_cnpg_target_runtime_transition import originals, private_pg  # noqa: F401 — reuse isolated socket fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("physical", ROOT / "scripts/cnpg-physical-runtime-transition.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
t = p.t
OBSERVATION = "2026-10-02T10:14:00.945246+00:00"


def setup_actual_trace_functions(q):
    fixture = ROOT / "tests/fixtures/cnpg_band_trace_time_projection"
    properties = json.loads((fixture / "source-native-properties.json").read_text())
    definitions = "\n".join(function["body"].rstrip() + ";" for function in properties["functions"])
    q(
        """CREATE TABLE climate(ts timestamptz,greenhouse_id text,temp_avg float8,vpd_avg float8,rh_avg float8,dew_point float8);
      CREATE TABLE setpoint_changes(ts timestamptz,greenhouse_id text,parameter text,value float8,expired_at timestamptz);
      CREATE TABLE setpoint_snapshot(ts timestamptz,greenhouse_id text,parameter text,value float8);
      CREATE TABLE crop_band_anchors(crop_type text,series text,greenhouse_id text,growth_stage text,season text,anchor text,value float8);
      CREATE TABLE crops(crop_catalog_id int,is_active boolean,greenhouse_id text);
      CREATE TABLE crop_target_profiles(crop_catalog_id int,greenhouse_id text,season text,hour_of_day int,temp_ideal_min float8,temp_ideal_max float8,vpd_ideal_min float8,vpd_ideal_max float8);
      SET check_function_bodies=off;"""
        + definitions
        + "SET check_function_bodies=on;"
    )
    for view, body in properties["views"].items():
        q("CREATE VIEW public." + view + " AS " + body)
    q("""INSERT INTO crop_band_anchors
        SELECT crop,series,'vallery','default','all',anchor,
               CASE series WHEN 'temp_low' THEN 60 WHEN 'temp_high' THEN 80
                           WHEN 'vpd_low' THEN 0.7 WHEN 'vpd_high' THEN 1.1 ELSE 0.9 END
        FROM unnest(ARRAY['house','cannabis','citrus','pepper','orchid']) crop
        CROSS JOIN unnest(ARRAY['temp_low','temp_high','temp_target','vpd_low','vpd_high','vpd_target']) series
        CROSS JOIN unnest(ARRAY['sr','sm','ss','mid']) anchor;
      INSERT INTO climate SELECT now()-age,'vallery',70,0.9,50,40
        FROM unnest(ARRAY[interval '15 days',interval '3 hours',interval '1 hour',interval '20 minutes']) age;
      INSERT INTO climate VALUES(now()-interval '10 minutes','vallery',70,NULL,50,40),
          (now()-interval '10 minutes','other',70,0.9,50,40);
      INSERT INTO setpoint_changes SELECT now()-age,'vallery',parameter,1,
          CASE WHEN age=interval '25 minutes' THEN now()-interval '5 minutes' ELSE NULL END
        FROM unnest(ARRAY[interval '4 hours',interval '25 minutes']) age
        CROSS JOIN unnest(ARRAY['temp_low','temp_high','vpd_low','vpd_high']) parameter;
      INSERT INTO setpoint_snapshot SELECT ts,greenhouse_id,parameter,value FROM setpoint_changes;""")
    trace = t.load("cnpg-band-trace-time-projection")
    trace.observation_at = json.loads(q("SET timezone='UTC'; SELECT to_jsonb(now())"))
    return trace


def trace_aggregate(query, trace):
    ranges = " || ".join(
        "jsonb_build_object('" + name + "',jsonb_build_array(min(" + name + ")::text,max(" + name + ")::text))"
        for name in trace.TIME_COLUMNS
    )
    return "SELECT jsonb_build_object('count',count(*),'time_ranges'," + ranges + ") FROM (" + query + ") AS trace"


def reference_trace(view, trace):
    instant = trace.observation_sql(trace.observation_at)
    period = "14 days" if view.endswith("recent") else "2 hours"
    query = "SELECT * FROM public.fn_band_trace(" + instant + "-interval '" + period + "'," + instant + ",'vallery')"
    if view.endswith("latest"):
        query += " ORDER BY ts DESC LIMIT 1"
    return query


@pytest.mark.parametrize(
    "scenario",
    [
        "ordinary",
        "revealed_old",
        "sparse_masked",
        "duplicates",
        "empty",
        "no_readings",
        "fallback",
        "expiry_boundary",
        "initial_snapshot_null",
        "sparse_snapshot",
        "finite_between_permanent",
        "finite_only",
        "permanent_duplicate_peer",
    ],
)
def test_native_interval_endpoints_equal_original_all_nine(private_pg, scenario):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    if scenario == "revealed_old":
        q(
            "TRUNCATE setpoint_changes; INSERT INTO setpoint_changes VALUES"
            "(now()-interval '8 hours','vallery','temp_low',1,NULL),"
            "(now()-interval '5 hours','vallery','temp_low',2,now()-interval '2 hours');"
        )
    elif scenario == "sparse_masked":
        q(
            "TRUNCATE setpoint_changes; INSERT INTO setpoint_changes VALUES"
            "(now()-interval '2 hours','vallery','temp_low',1,now()-interval '90 minutes'),"
            "(now()-interval '1 hour','other','temp_low',2,NULL);"
        )
    elif scenario == "duplicates":
        q(
            "INSERT INTO climate SELECT * FROM climate; INSERT INTO setpoint_changes SELECT * FROM setpoint_changes;"
            "INSERT INTO setpoint_changes SELECT ts,greenhouse_id,parameter,value,ts FROM setpoint_changes;"
        )
    elif scenario == "empty":
        q("TRUNCATE climate")
    elif scenario == "no_readings":
        q("TRUNCATE setpoint_changes,setpoint_snapshot")
    elif scenario == "fallback":
        q("DELETE FROM crop_band_anchors WHERE crop_type='house'")
    elif scenario == "expiry_boundary":
        q(
            "TRUNCATE setpoint_changes; INSERT INTO setpoint_changes "
            "SELECT min(ts)-interval '1 day','vallery','temp_low',1,NULL FROM climate;"
            "INSERT INTO setpoint_changes SELECT min(ts)-interval '1 hour','vallery','temp_low',2,min(ts) FROM climate;"
        )
    elif scenario == "sparse_snapshot":
        instant = trace.observation_sql(trace.observation_at)
        q(
            "TRUNCATE climate,setpoint_snapshot; INSERT INTO climate VALUES ("
            + instant
            + "-interval '10 minutes','vallery',70,0.9,50,40),("
            + instant
            + ",'vallery',70,0.9,50,40);"
            "INSERT INTO setpoint_snapshot SELECT " + instant + "-age,'vallery',parameter,1 "
            "FROM unnest(ARRAY[interval '5 minutes',interval '3 minutes']) age "
            "CROSS JOIN unnest(ARRAY['temp_low','temp_high','vpd_low','vpd_high']) parameter;"
        )
    elif scenario == "finite_between_permanent":
        instant = trace.observation_sql(trace.observation_at)
        q(
            "TRUNCATE setpoint_changes; INSERT INTO setpoint_changes SELECT "
            + instant
            + "-start_age,'vallery',parameter,1,"
            + instant
            + "-end_age "
            "FROM (VALUES(interval '8 hours',NULL::interval),(interval '2 hours',interval '30 minutes'),"
            "(interval '90 minutes',interval '80 minutes'),(interval '15 minutes',NULL::interval)) ages(start_age,end_age) "
            "CROSS JOIN unnest(ARRAY['temp_low','temp_high','vpd_low','vpd_high']) parameter;"
        )
    elif scenario == "finite_only":
        q("UPDATE setpoint_changes SET expired_at=ts+interval '2 hours'")
    elif scenario == "permanent_duplicate_peer":
        q(
            "INSERT INTO setpoint_changes SELECT ts,greenhouse_id,parameter,value,NULL FROM setpoint_changes;"
            "INSERT INTO setpoint_changes SELECT ts,greenhouse_id,parameter,value,ts+interval '10 minutes' FROM setpoint_changes;"
        )
    elif scenario == "initial_snapshot_null":
        q(
            "TRUNCATE setpoint_snapshot; INSERT INTO setpoint_snapshot "
            "SELECT now()-interval '1 hour','vallery',p,1 FROM "
            "unnest(ARRAY['temp_low','temp_high','vpd_low','vpd_high']) p;"
        )
    for view in sorted(trace.VIEWS):
        sql = trace.endpoint_aggregate(view, trace.observation_at)
        result = q(
            "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SET LOCAL timezone='UTC';"
            + trace.guard_sql(trace.observation_at)
            + trace_aggregate(reference_trace(view, trace), trace)
            + ";"
            + "SELECT jsonb_build_object('count',row_count,'time_ranges',ranges) FROM ("
            + sql
            + ") AS result(row_count,ranges);COMMIT;"
        )
        original, optimized = map(json.loads, result.splitlines())
        assert original == optimized


def test_complete_native_collector_dispatches_both_trace_endpoint_aggregates(private_pg):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    q(
        "CREATE SCHEMA _timescaledb_catalog;"
        "CREATE TABLE _timescaledb_catalog.hypertable(id int,schema_name text,table_name text);"
        "CREATE TABLE _timescaledb_catalog.chunk(id int,hypertable_id int,schema_name text,table_name text,"
        "compressed_chunk_id int,dropped boolean);"
    )
    sql = p.dataset_sql(p.pitr.SOURCE, trace.observation_at).replace(
        "::int<>160013", "::int<>" + q("SHOW server_version_num")
    )
    sql = sql.replace(
        "OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2'", "OR false"
    )
    result = json.loads(q(sql))
    for view in sorted(trace.VIEWS):
        row = next(x for x in result["relations"] if x["relation"] == "public." + view)
        expected = json.loads(q("SET timezone='UTC'; " + trace_aggregate(reference_trace(view, trace), trace)))
        assert row["count"] == expected["count"]
        assert row["time_ranges"] == expected["time_ranges"]
    assert result["observation_at"] == trace.observation_at


@pytest.mark.parametrize("fallback", [False, True])
def test_actual_source_trace_timestamp_projection_equals_both_complete_views(private_pg, fallback):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    if fallback:
        # Actual center165 preserves nonNULL orchid/default fallback even when
        # house crop SQL119 returns NULL. The prior crop-based proposal is wrong.
        q("DELETE FROM crop_band_anchors WHERE crop_type='house'")
    for view in sorted(trace.VIEWS):
        reference = trace_aggregate(reference_trace(view, trace), trace)
        projected = trace_aggregate(trace.projection(view, trace.observation_at), trace)
        raw = q(
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SET LOCAL timezone='UTC'; "
            + trace.guard_sql(trace.observation_at)
            + reference
            + ";"
            + projected
            + ";COMMIT;"
        )
        before, after = map(json.loads, raw.splitlines())
        assert before == after
        assert before["count"] == (3 if view.endswith("recent") else 1)
        assert set(before["time_ranges"]) == set(trace.TIME_COLUMNS)


def test_trace_projection_retains_center_null_cardinality_not_crop_values(private_pg):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    # A fixture substitution exercises the relational equivalence under center
    # zero admission. The production source guard MUST refuse this altered body.
    q("""CREATE OR REPLACE FUNCTION public.fn_center_band_setpoints(target_ts timestamptz)
      RETURNS TABLE(temp_low float8,temp_high float8,vpd_low float8,vpd_high float8)
      LANGUAGE sql STABLE AS $$ SELECT 60::float8,80::float8,NULL::float8,1.1::float8 $$;""")
    failure = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp;" + trace.guard_sql(trace.observation_at),
        check=False,
    )
    assert failure.returncode != 0 and "changed source/resolution" in failure.stderr
    reference = trace_aggregate(reference_trace("v_band_trace_recent", trace), trace)
    projected = trace_aggregate(trace.projection("v_band_trace_recent", trace.observation_at), trace)
    rows = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp;" + reference + ";" + projected + ";COMMIT;"
    )
    left, right = map(json.loads, rows.splitlines())
    assert left == right and left["count"] == 0


def test_trace_projection_preserves_boundaries_duplicates_expiry_and_common_clock(private_pg):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    instant = trace.observation_sql(trace.observation_at)
    q(
        "INSERT INTO climate SELECT "
        + instant
        + "-age,'vallery',70,0.9,50,40 FROM unnest(ARRAY[interval '14 days',interval '14 days 0.000001 seconds',interval '2 hours',interval '2 hours 0.000001 seconds',interval '0 seconds']) age;"
    )
    q(
        "INSERT INTO setpoint_changes VALUES("
        + instant
        + "-interval '30 minutes','vallery','temp_low',2,"
        + instant
        + "-interval '20 minutes'),("
        + instant
        + "-interval '30 minutes','vallery','temp_low',3,NULL);"
    )
    for view in sorted(trace.VIEWS):
        reference = trace_aggregate(reference_trace(view, trace), trace)
        projected = trace_aggregate(trace.projection(view, trace.observation_at), trace)
        result = q(
            "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SET LOCAL timezone='UTC';"
            + trace.guard_sql(trace.observation_at)
            + reference
            + ";"
            + projected
            + ";COMMIT;"
        )
        expected, actual = map(json.loads, result.splitlines())
        assert expected == actual
        assert actual["count"] == (7 if view.endswith("recent") else 1)
    # Distinct common clocks remain part of the witness; sequential native NOW
    # values must never be silently compared as one observation.
    left = {
        "schema": "cnpg-physical-data-parity-v2",
        "database": "verdify_rehearsal",
        "observation_at": trace.observation_at,
        "relations": [],
        "timescale_owners": [],
    }
    right = copy.deepcopy(left)
    right["observation_at"] = OBSERVATION
    with pytest.raises(ValueError, match="drift"):
        p.validate_dataset(left, right)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-10-02",
        "2026-10-02T10:14:00",
        "2026-10-02T10:14:00-06:00",
        "infinity",
        "2026-10-02T10:14:00+00:00';COMMIT;",
    ],
)
def test_trace_common_observation_refuses_invalid_or_unbound_clock(value):
    trace = t.load("cnpg-band-trace-time-projection")
    with pytest.raises(ValueError):
        trace.observation_sql(value)


def test_trace_projection_refuses_skipped_float_exception_domain(private_pg):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    q("UPDATE crop_band_anchors SET value=1e308 WHERE crop_type='cannabis' AND series='vpd_target'")
    original = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SELECT count(*) FROM v_band_trace_recent;",
        check=False,
    )
    assert original.returncode != 0 and "out of range" in original.stderr
    optimized = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp;" + trace.guard_sql(trace.observation_at),
        check=False,
    )
    assert optimized.returncode != 0 and "unproven skipped arithmetic domain" in optimized.stderr


@pytest.mark.parametrize("value", ["1e-323", "1e-310", "1e-200", "0"])
def test_trace_native_tiny_harmonic_domain_and_projection_refusal(private_pg, value):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    q(
        "UPDATE crop_band_anchors SET value=CASE anchor WHEN 'sr' THEN "
        + value
        + "::float8 ELSE 0::float8 END WHERE crop_type='cannabis' AND series='vpd_target'"
    )
    original = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SELECT count(*) FROM v_band_trace_recent;",
        check=False,
    )
    optimized = q(
        "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp;" + trace.guard_sql(trace.observation_at),
        check=False,
    )
    if value == "1e-323":
        assert original.returncode != 0 and "underflow" in original.stderr
    else:
        assert original.returncode == 0
    if value == "0":
        assert optimized.returncode == 0
    else:
        assert optimized.returncode != 0 and "unproven skipped arithmetic domain" in optimized.stderr


@pytest.mark.parametrize("negative", [False, True])
def test_trace_normal_lower_domain_cancellation_preserves_native_endpoints(private_pg, negative):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    sign = "-" if negative else ""
    q(
        "UPDATE crop_band_anchors SET value=CASE anchor WHEN 'sr' THEN 1e-100::float8 "
        "WHEN 'ss' THEN 1.0000000000000001e-100::float8 ELSE "
        + sign
        + "1e-100::float8 END WHERE crop_type='cannabis' AND series='vpd_target'"
    )
    for view in sorted(trace.VIEWS):
        result = q(
            "BEGIN READ ONLY; SET LOCAL search_path=pg_catalog,public,pg_temp; SET LOCAL timezone='UTC';"
            + trace.guard_sql(trace.observation_at)
            + trace_aggregate(reference_trace(view, trace), trace)
            + ";"
            + trace_aggregate(trace.projection(view, trace.observation_at), trace)
            + ";COMMIT;"
        )
        original, projected = map(json.loads, result.splitlines())
        assert original == projected


@pytest.mark.parametrize("change", ["zone_body", "view", "owner", "search_path", "nan_anchor", "overload"])
def test_trace_projection_source_domain_guards_fail_closed(private_pg, change):  # noqa: F811
    q = private_pg
    trace = setup_actual_trace_functions(q)
    if change == "zone_body":
        q("""CREATE OR REPLACE FUNCTION fn_zone_vpd_targets(target_ts timestamptz)
          RETURNS TABLE(vpd_target_south float8,vpd_target_west float8,vpd_target_east float8,vpd_target_center float8)
          LANGUAGE plpgsql STABLE AS $$ BEGIN RAISE EXCEPTION 'fixture domain error'; END $$;""")
    elif change == "overload":
        q("CREATE FUNCTION fn_crop_band_value(text,text,timestamptz) RETURNS float8 LANGUAGE sql AS 'SELECT 1::float8'")
    elif change == "view":
        q("ALTER VIEW v_band_trace_recent RENAME TO changed_trace")
    elif change == "owner":
        q("ALTER FUNCTION fn_current_season() OWNER TO verdify_api_runtime_login")
    elif change == "nan_anchor":
        q("UPDATE crop_band_anchors SET value='NaN' WHERE crop_type='orchid' AND series='vpd_target'")
    search = "pg_catalog,pg_temp" if change == "search_path" else "pg_catalog,public,pg_temp"
    failure = q(
        "BEGIN READ ONLY; SET LOCAL search_path=" + search + ";" + trace.guard_sql(trace.observation_at), check=False
    )
    assert failure.returncode != 0 and "projection refuses" in failure.stderr


def test_physical_inputs_dispatch_only_the_two_logical_records_to_composite_reader(monkeypatch):
    calls = []
    args = SimpleNamespace(captures=Path("capture"))
    for key in p.INPUT_KEYS:
        setattr(args, key, Path(key))
        setattr(args, key + "_sha256", "a" * 64)

    def single(path):
        calls.append((path.name, "single"))
        return {}, "a" * 64

    def composite(path, *, version, mode):
        calls.append((path.name, version, mode))
        return {}, "a" * 64

    class AfterInputDispatch(Exception):
        pass

    def stop_before_independent_capture_qualification(*args):
        raise AfterInputDispatch

    monkeypatch.setattr(p.t.c0, "read_witness", single)
    monkeypatch.setattr(p.t, "read_transition_record", composite)
    monkeypatch.setattr(p.pitr, "validate_captures", stop_before_independent_capture_qualification)
    with pytest.raises(AfterInputDispatch):
        p.qualified_inputs(args)
    assert {row for row in calls if len(row) == 3} == {
        ("logical_rollback", t.VERSION, "rollback-qualification"),
        ("logical_install", t.VERSION, "install"),
    }
    assert {row[0] for row in calls if len(row) == 2} == set(p.INPUT_KEYS) - {"logical_rollback", "logical_install"}


@pytest.mark.parametrize("install", [False, True])
def test_post270_retained_record_reader_preserves_actual_envelope_and_full_file_hash(tmp_path, install):
    import hashlib

    retained = p.successor_module().r
    value = {
        "version": retained.VERSION,
        "mode": "install" if install else "savepoint-rollback-qualification",
        "ddl_sha256": "a" * 64,
        "before_witness": {"raw": ["original"]},
        "post_witness": {"raw": ["actual"]},
    }
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    path = tmp_path / "actual.json"
    path.write_bytes(raw)
    result, sha = p.read_logical_record(path, successor={}, install=install)
    assert result == value and sha == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw
    with pytest.raises(ValueError, match="version"):
        p.read_logical_record(path, successor=None, install=install)


@pytest.mark.parametrize("tamper", ["mode", "version", "ddl_hash", "extra_field", "duplicate_field"])
def test_post270_retained_record_reader_refuses_unrecognized_envelopes(tmp_path, tamper):
    value = {
        "version": p.successor_module().r.VERSION,
        "mode": "savepoint-rollback-qualification",
        "ddl_sha256": "a" * 64,
        "before_witness": {},
        "post_witness": {},
    }
    if tamper == "mode":
        value["mode"] = "rollback-qualification"
    elif tamper == "version":
        value["version"] = "unknown"
    elif tamper == "ddl_hash":
        value["ddl_sha256"] = "invalid"
    elif tamper == "extra_field":
        value["extra"] = True
    raw = json.dumps(value)
    if tamper == "duplicate_field":
        raw = raw.replace('{"version":', '{"mode":"install","version":', 1)
    path = tmp_path / "record.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        p.read_logical_record(path, successor={}, install=False)


def test_post270_retained_reader_refuses_wrong_file_custody_before_capture_checks(tmp_path, monkeypatch):
    args = SimpleNamespace(post270_lineage=tmp_path / "lineage", post270_lineage_sha256="a" * 64)
    for key in p.INPUT_KEYS:
        setattr(args, key, tmp_path / key)
        setattr(args, key + "_sha256", "a" * 64)
    monkeypatch.setattr(p, "read_post270_lineage", lambda *args: {})
    monkeypatch.setattr(p.t.c0, "read_witness", lambda *args: ({}, "a" * 64))
    monkeypatch.setattr(p, "read_logical_record", lambda *args, **kwargs: ({}, "b" * 64))
    with pytest.raises(ValueError, match="physical input custody mismatch"):
        p.qualified_inputs(args)


def test_post270_retained_reader_rejects_symlink_and_oversize(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="regular retained"):
        p.read_logical_record(link, successor={}, install=False)
    monkeypatch.setattr(p.t.c0, "WITNESS_MAX_BYTES", 1)
    path.write_bytes(b"x" * 1027)
    with pytest.raises(ValueError, match="two-witness bound"):
        p.read_logical_record(path, successor={}, install=False)


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
        "schema": "cnpg-physical-data-parity-v2",
        "observation_at": OBSERVATION,
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
        "schema": "cnpg-physical-data-parity-v2",
        "observation_at": OBSERVATION,
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
    sql = p.dataset_sql(profile, OBSERVATION)
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql
    assert "SET LOCAL search_path=pg_catalog,public,pg_temp;" in sql
    assert f"current_setting('cluster_name')<>'{profile}'" in sql
    assert "current_user<>session_user" in sql and "inet_client_addr() IS NOT NULL" in sql
    assert "n.nspname='public'" in sql and "c.relkind IN ('r','p','v','m','S')" in sql
    assert "c.compressed_chunk_id" in sql and "WHERE h.schema_name='public' AND NOT c.dropped" in sql
    assert not any(
        word in sql for word in ("INSERT INTO", "UPDATE public", "ALTER TABLE", "CREATE TABLE", "DELETE FROM")
    )
    assert "min(%I)::text,max(%I)::text" in sql
    assert "\\gexec" in sql and sql.count("SELECT count(*)") == 3
    assert "GROUPS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING" in sql
    assert "tstzrange(ts,ts," in sql
    assert "finite_expiry AS MATERIALIZED" in sql
    assert "min(ts) FILTER (WHERE expired_at IS NULL) OVER" in sql
    assert "range_agg(valid) OVER" not in sql
    assert "statement_timeout='120s'" in sql
    assert "FOR relation IN" not in sql


def test_native_private_fixture_dataset_collector_handles_uncompressed_and_compressed(private_pg):  # noqa: F811
    # This PG16 fixture has no Timescale extension. Only the collector joins,
    # counts, ranges and READ ONLY behavior are tested; no actual recovery proof.
    q = private_pg
    q("""CREATE SCHEMA _timescaledb_catalog;
      CREATE TABLE _timescaledb_catalog.hypertable(id int,schema_name text,table_name text);
      CREATE TABLE _timescaledb_catalog.chunk(id int,hypertable_id int,schema_name text,table_name text,
        compressed_chunk_id int,dropped boolean);
      CREATE TABLE public.fixture_data(ts timestamptz, "other time" timestamp);
      INSERT INTO public.fixture_data VALUES('2026-10-01T10:00:00Z','2026-10-01T09:00:00'),('2026-10-01T10:01:00Z','2026-10-01T09:01:00');
      CREATE TABLE _timescaledb_catalog.fixture_chunk(ts timestamptz);
      CREATE TABLE _timescaledb_catalog.fixture_compressed(ts timestamptz);
      INSERT INTO _timescaledb_catalog.hypertable VALUES(1,'public','fixture_data');
      INSERT INTO _timescaledb_catalog.chunk VALUES
        (1,1,'_timescaledb_catalog','fixture_chunk',NULL,false),
        (2,1,'_timescaledb_catalog','fixture_data_second',3,false),
        (3,9,'_timescaledb_catalog','fixture_compressed',NULL,false);
      CREATE TABLE _timescaledb_catalog.fixture_data_second(ts timestamptz);""")
    # Real restored v_band_device_divergence calls a source-owned public
    # PL/pgSQL resolver whose nested public helper has no SET search_path.
    # Relation qualification alone cannot resolve that helper/default args.
    q("""CREATE FUNCTION public.fixture_nested_value(value int DEFAULT 7)
          RETURNS int LANGUAGE sql AS 'SELECT value';
      CREATE FUNCTION public.fixture_nested_resolver() RETURNS int LANGUAGE plpgsql AS
          'BEGIN RETURN fixture_nested_value(); END';
      CREATE VIEW public.fixture_nested_view AS SELECT public.fixture_nested_resolver() AS value;""")
    # jsonb_build_object has a native argument ceiling; retain all timestamp
    # columns instead of silently narrowing the complete relation inventory.
    q("CREATE TABLE public.fixture_many_times(" + ",".join(f"t{i} timestamptz" for i in range(55)) + ")")
    q("INSERT INTO public.fixture_many_times DEFAULT VALUES")
    emitted = p.dataset_sql(p.pitr.SOURCE, OBSERVATION)
    native_version = q("SHOW server_version_num")
    fixture_sql = emitted.replace("::int<>160013", "::int<>" + native_version)
    fixture_sql = fixture_sql.replace(
        "OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2'", "OR false"
    )
    old_path = fixture_sql.replace(
        "SET LOCAL search_path=pg_catalog,public,pg_temp;", "SET LOCAL search_path=pg_catalog,pg_temp;"
    )
    old_failure = q(old_path, check=False)
    assert old_failure.returncode != 0 and "fixture_nested_value() does not exist" in old_failure.stderr
    result = json.loads(q(fixture_sql))
    assert next(row for row in result["relations"] if row["relation"] == "public.fixture_nested_view")["count"] == 1
    data = next(row for row in result["relations"] if row["relation"] == "public.fixture_data")
    assert data["count"] == 2 and data["time_ranges"]["ts"] == ["2026-10-01 10:00:00+00", "2026-10-01 10:01:00+00"]
    assert data["time_ranges"]["other time"] == ["2026-10-01 09:00:00", "2026-10-01 09:01:00"]
    many = next(row for row in result["relations"] if row["relation"] == "public.fixture_many_times")
    assert many["count"] == 1 and many["time_ranges"] == {f"t{i}": [None, None] for i in range(55)}
    assert len(result["timescale_owners"]) == 2
    assert result["timescale_owners"][0]["compressed"] is None
    assert result["timescale_owners"][1]["compressed_owner"] == "verdify"
    bad = q(emitted, check=False)
    assert bad.returncode != 0 and "refuses target/session" in bad.stderr
    # Adding public resolution must never permit a view's function to write.
    q("""CREATE FUNCTION public.fixture_forbidden_write() RETURNS int LANGUAGE plpgsql AS
          'BEGIN INSERT INTO public.fixture_data(ts) VALUES(now()); RETURN 1; END';
      CREATE VIEW public.fixture_writing_view AS SELECT public.fixture_forbidden_write() AS value;""")
    writing_failure = q(fixture_sql, check=False)
    assert writing_failure.returncode != 0 and "read-only transaction" in writing_failure.stderr
    assert '"schema": "cnpg-physical-data-parity-v2"' not in writing_failure.stdout
    assert q("SELECT count(*) FROM public.fixture_data") == "2"


@pytest.mark.parametrize("profile", t.PHYSICAL_TARGETS)
def test_physical_atomic_raw_guards_and_copied_history(private_pg, monkeypatch, profile):  # noqa: F811
    q = private_pg
    monkeypatch.setattr(t, "SERVER", int(q("SHOW server_version_num")))
    # Synthetic digest implementation/witness stand-ins only. The default
    # emitter's fixed PG160013 and source-native digest guards are not relaxed.
    # Exercise its actual atomic SQL/CAS/historical retention with real DDL and
    # the selected217/259 attesters, then leave real target proof to ROOT.
    monkeypatch.setattr(t.c0, "checked", lambda *args, **kwargs: None)
    native_members = """(SELECT coalesce(jsonb_agg(format('member|%s|%s|%s|%s|%s|%s',roleid,member,grantor,admin_option,inherit_option,set_option) ORDER BY roleid,member,grantor),'[]'::jsonb) FROM pg_auth_members)"""
    selects = (
        """SELECT jsonb_build_object(
      'database',current_database(),'server',current_setting('server_version_num')::int,
      'roles',(SELECT jsonb_object_agg(oid::text,rolname) FROM pg_roles),
      'namespaces',(SELECT jsonb_object_agg(oid::text,nspname) FROM pg_namespace),
      'database_owner','verdify','database_acl','fixture',
      'ledger',(SELECT jsonb_agg(to_jsonb(r)) FROM public.schema_migrations r),
      'seals',jsonb_build_object('ordinary',(SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts r),'mcp',(SELECT jsonb_agg(to_jsonb(r)) FROM public.mcp_runtime_boundary_receipt r)),
      'boundaries',jsonb_build_object('verdify_api_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')),
          'verdify_ingestor_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')),
          'verdify_mcp_runtime_login',jsonb_build_object('raw_entries',__NATIVE_MEMBERS__,'native',encode(public.fn_mcp_runtime_boundary_digest(),'hex'))),
      'portable_catalog',("""
        + t.c0.portable_catalog_sql()
        + """),'raw_portable_catalog_v2',("""
        + t.c0.raw_portable_catalog_sql()
        + """),'portability_native_facts',("""
        + t.c0.portability_native_facts_sql()
        + """));"""
    )
    selects = selects.replace("__NATIVE_MEMBERS__", native_members)
    monkeypatch.setattr(t, "witness_select", lambda target=None, **kwargs: selects)
    q("""CREATE TABLE physical_when(value integer);
      CREATE FUNCTION physical_when_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
      CREATE TRIGGER compare_old_new BEFORE UPDATE ON physical_when FOR EACH ROW
        WHEN (OLD.value IS DISTINCT FROM NEW.value) EXECUTE FUNCTION physical_when_fn();""")
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects))
    logical_ddl, _ = t.ddl()
    q(logical_ddl)
    rows = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    q(
        "INSERT INTO "
        + t.TABLE
        + " VALUES "
        + ",".join(
            "(" + t.literal(login) + ",decode('" + boundary + "','hex'),'" + qualification + "')"
            for login, boundary, qualification in rows
        )
    )
    before = json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects))

    def emit(value, **kwargs):
        native_sql = t.emit_sql(value, physical_target=profile, logical_receipts=rows, **kwargs)
        # Fixture identity substitution only: source emits the fixed physical name,
        # while this reused socket fixture runs with original cluster_name.
        return native_sql.replace(profile, t.operator.CLUSTER)

    refused_identity = q(t.emit_sql(before, physical_target=profile, logical_receipts=rows), check=False)
    assert refused_identity.returncode != 0 and "refuses target/session" in refused_identity.stderr
    historical = originals(q)
    result = q(emit(before)).splitlines()
    record = json.loads(result[-1])
    assert record["mode"] == "rollback-qualification"
    after = p.checked_physical_qualification(before, record, profile)
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    bad = copy.deepcopy(after)
    bad["boundaries"][t.LOGINS[0]]["native"] = "0" * 64
    failure = q(emit(before, reviewed_post=bad, qualification_sha256="a" * 64), check=False)
    assert failure.returncode != 0 and "unqualified post-DDL catalog" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before and originals(q) == historical
    stale = copy.deepcopy(before)
    stale["database_acl"] = "stale"
    failure = q(emit(stale), check=False)
    assert failure.returncode != 0 and "stale exact target witness" in failure.stderr
    assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
    original_ddl = t.ddl
    for injected in [
        "GRANT SELECT ON public.control_experiments TO PUBLIC;",
        "CREATE TABLE public.unapproved_native_object(x integer);",
        f"ALTER TABLE {t.PHYSICAL_TABLE} SET UNLOGGED;",
        f"UPDATE {t.TABLE} SET qualification_sha256=repeat('c',64);",
        "ALTER TABLE public.physical_when DISABLE TRIGGER compare_old_new;",
    ]:
        with monkeypatch.context() as scope:

            def modified_ddl(bootstrap=False, **kwargs):
                payload, bodies = original_ddl(bootstrap, **kwargs)
                return payload + "\n" + injected, bodies

            scope.setattr(t, "ddl", modified_ddl)
            refused = q(emit(before, reviewed_post=after, qualification_sha256="a" * 64), check=False)
            assert refused.returncode != 0
            assert any(
                reason in refused.stderr
                for reason in (
                    "CNPG native transition refuses raw",
                    "new raw object shape",
                    "copied logical receipt drift",
                )
            )
        assert json.loads(q("SET search_path=pg_catalog,pg_temp; " + selects)) == before
        assert originals(q) == historical
    installed = json.loads(q(emit(before, reviewed_post=after, qualification_sha256="a" * 64)).splitlines()[-1])
    assert installed["mode"] == "install"
    assert {k: v for k, v in installed["post_witness"].items() if k != "portability_native_facts"} == {
        k: v for k, v in after.items() if k != "portability_native_facts"
    }
    t.validate_raw_delta(before, installed["post_witness"], physical_target=profile)
    t.validate_installed_post(before, after, installed["post_witness"], physical_target=profile)
    for field, key, changed in (
        ("relations", "relnamespace", 999999),
        ("relations", "oid", 999998),
        ("constraints", "conrelid", 999997),
    ):
        altered = copy.deepcopy(installed["post_witness"])
        allowed = t.raw_additions(physical_target=profile)[field]
        fact = next(f for f in altered["portability_native_facts"][field] if f["identity"] in allowed)
        fact["native"][key] = changed
        with pytest.raises(ValueError):
            t.validate_installed_post(before, after, altered, physical_target=profile)
    assert installed["post_witness"]["portability_native_facts"] != after["portability_native_facts"]
    assert originals(q) == historical


@pytest.mark.parametrize("profile", (p.pitr.SOURCE, *t.PHYSICAL_TARGETS))
def test_dataset_peer_bridge_preserves_exact_readonly_snapshot_and_profile(profile):
    emitted = p.dataset_peer_sql(profile, OBSERVATION)
    assert emitted.count("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;") == 1
    assert "\nBEGIN;\n" not in emitted
    assert "current_user<>'postgres' OR session_user<>'postgres'" in emitted
    assert "SET SESSION AUTHORIZATION verdify;" in emitted and "RESET SESSION AUTHORIZATION;" in emitted
    assert f"current_setting('cluster_name')<>'{profile}'" in emitted
    assert "bootstrap/owner custody" in emitted
    assert not any(
        word in emitted for word in ("INSERT INTO", "UPDATE public", "ALTER TABLE", "CREATE TABLE", "DELETE FROM")
    )


def post270_contract(monkeypatch):
    """Small orchestration fixture; existing native suites own DDL proof."""
    d = p.successor_module()
    monkeypatch.setattr(p, "successor_module", lambda: d)
    before = {"namespaces": {}, "portability_native_facts": {"fixture": "before"}}
    admitted = copy.deepcopy(before)
    post = copy.deepcopy(before)
    post["ledger"] = "270"
    post["boundaries"] = {login: {"native": str(i) * 64} for i, login in enumerate(sorted(t.LOGINS), 1)}
    original = {
        "ledger": [{"source": "fixture", "seq": i} for i in range(277)],
        "ordinary": [{"id": 1}, {"id": 2}],
        "mcp": [{"id": 3}],
    }
    prior = [[login, "a" * 64, "b" * 64] for login in sorted(t.LOGINS)]
    old_rows = [
        {
            "login_name": login,
            "boundary_sha256": "\\x" + h,
            "qualification_sha256": q,
            "qualified_at": "2026-10-01T00:00:00+00:00",
        }
        for login, h, q in prior
    ]
    new_rows = copy.deepcopy(old_rows)
    for row in new_rows:
        row["boundary_sha256"] = "\\x" + post["boundaries"][row["login_name"]]["native"]
        row["qualification_sha256"] = p.POST270_ROLLBACK_SHA
    new_original = copy.deepcopy(original)
    new_original["ledger"].append(copy.deepcopy(p.POST270_NATIVE_LEDGER_ROW))
    original_rollback = {
        "version": d.r.VERSION,
        "mode": "savepoint-rollback-qualification",
        "ddl_sha256": "d" * 64,
        "before_witness": before,
        "post_witness": admitted,
    }
    original_install = dict(original_rollback, mode="install")
    successor_rollback = {
        "version": d.VERSION,
        "mode": "rollback-qualification",
        "before_witness": admitted,
        "post_witness": post,
    }
    successor_install = dict(successor_rollback, mode="install")
    successor = {
        "rollback": successor_rollback,
        "install": successor_install,
        "prior_rows": [original, old_rows],
        "installed_rows": {"original": new_original, "qualified": new_rows},
        "namespace_custody": {},
        "source_roles": "source",
        "installed_roles": "installed",
    }
    calls = []
    monkeypatch.setattr(t.c0, "compare", lambda *a: calls.append("source-compare"))
    monkeypatch.setattr(
        t,
        "checked_qualification",
        lambda *a, **k: (
            calls.append(("original-qualification", k)),
            {"boundaries": {login: {"native": "a" * 64} for login in t.LOGINS}},
        )[1],
    )
    monkeypatch.setattr(t, "validate_installed_post", lambda *a: admitted)
    monkeypatch.setattr(d, "validate_lineage", lambda *a, **k: calls.append("full-original-lineage"))
    monkeypatch.setattr(d, "checked_record", lambda *a, **k: calls.append(("genuine-successor", k)))
    monkeypatch.setattr(d, "installation_projection", lambda *a, **k: (post, []))
    monkeypatch.setattr(t.c0, "bootstrap_profile", lambda *a: {})
    monkeypatch.setattr(t.role_parity, "verify", lambda *a, **k: calls.append("password-free-role-parity"))
    monkeypatch.setattr(t.c0, "checked", lambda *a, **k: calls.append("complete-inherited-witness"))
    rows = [[login, post["boundaries"][login]["native"], p.POST270_ROLLBACK_SHA] for login in sorted(t.LOGINS)]
    args = ({}, before, original_rollback, original_install, post, rows, "b" * 64, successor)
    return args, calls


def test_post270_explicit_chain_retains_native_validators_and_real_modes(monkeypatch):
    args, calls = post270_contract(monkeypatch)
    p.validate_post270_logical(*args)
    assert ("original-qualification", {"retained_session": True}) in calls
    assert ("genuine-successor", {"mode": "rollback-qualification"}) in calls
    assert ("genuine-successor", {"mode": "install"}) in calls
    assert calls.count("full-original-lineage") == 2
    assert "password-free-role-parity" in calls and "complete-inherited-witness" in calls


@pytest.mark.parametrize(
    "tamper",
    [
        "original_mode",
        "original_before",
        "old_row",
        "old_seal",
        "receipt_time",
        "qualification_sha",
        "stamp",
        "catalog",
        "roles",
        "namespace",
        "copied_receipt",
        "270_applied_at",
        "270_duration",
        "270_extra_field",
    ],
)
def test_post270_chain_refuses_truthful_custody_and_inherited_drift(monkeypatch, tamper):
    args, _ = post270_contract(monkeypatch)
    args = list(copy.deepcopy(args))
    successor = args[-1]
    if tamper == "original_mode":
        args[3]["mode"] = "rollback-qualification"
    elif tamper == "original_before":
        args[3]["before_witness"] = {}
    elif tamper == "old_row":
        successor["installed_rows"]["original"]["ledger"][0]["seq"] = 999
    elif tamper == "old_seal":
        successor["installed_rows"]["original"]["mcp"][0]["id"] = 999
    elif tamper == "receipt_time":
        successor["installed_rows"]["qualified"][0]["qualified_at"] = "changed"
    elif tamper == "qualification_sha":
        successor["installed_rows"]["qualified"][0]["qualification_sha256"] = "f" * 64
    elif tamper == "stamp":
        successor["installed_rows"]["original"]["ledger"][-1]["sha256"] = "f" * 64
    elif tamper == "270_applied_at":
        successor["installed_rows"]["original"]["ledger"][-1]["applied_at"] = "2026-10-02T07:00:00+00:00"
    elif tamper == "270_duration":
        successor["installed_rows"]["original"]["ledger"][-1]["duration_ms"] += 1
    elif tamper == "270_extra_field":
        successor["installed_rows"]["original"]["ledger"][-1]["extra"] = "invented"
    elif tamper in {"catalog", "roles"}:
        args[4][tamper] = "unexpected"
    elif tamper == "namespace":
        args[4]["namespaces"]["9"] = "unexpected"
    else:
        args[5][0][2] = "f" * 64
    with pytest.raises(ValueError):
        p.validate_post270_logical(*args)


def test_post270_manifest_hash_and_incomplete_bindings_fail_closed(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": p.POST270_VERSION, "inputs": {}}))
    import hashlib

    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="manifest custody"):
        p.read_post270_lineage(path, "f" * 64)
    with pytest.raises(ValueError, match="complete post270"):
        p.read_post270_lineage(path, sha)
