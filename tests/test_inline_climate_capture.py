"""Forward repair of the private payload callee; never refresh startup receipts."""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from test_c0_release_rehearsal import (
    MIGRATIONS,
    assert_applied,
    attestation_probe,
    copy_migrations,
    install_actual_attestation_probe,
)
from test_c0_release_rehearsal import rehearsal as rehearsal
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / "db/migrations/247-inline-climate-capture-payload.sql"
ALL = (*MIGRATIONS, REPAIR)


@pytest.fixture
def capture_ready(rehearsal):
    query, directory, run = rehearsal
    copy_migrations(directory)
    result = run()
    assert result.returncode == 0, result.stderr
    # Explicit fixture schema-create boundary, not a full migration217 replay.
    query("""REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        GRANT verdify_api_runtime TO verdify_api_runtime_login;
        GRANT verdify_ingestor_runtime TO verdify_ingestor_runtime_login;""")
    return query, directory, run


def objects(query):
    return json.loads(
        query("""SELECT jsonb_agg(jsonb_build_object(
        'oid',p.oid,'name',p.proname,'owner',p.proowner,'acl',p.proacl,
        'source',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
        'definer',p.prosecdef,'config',p.proconfig) ORDER BY p.proname)
        FROM pg_proc p WHERE p.oid IN (
          to_regprocedure('public.fn_capture_daily_climate_metric_revision()'),
          to_regprocedure('public.fn_daily_climate_metric_payload(public.daily_summary)'))""")
    )


def rows(query):
    return query("""SELECT jsonb_build_object(
        'daily',(SELECT jsonb_agg(to_jsonb(d) ORDER BY date) FROM daily_summary d),
        'revisions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY revision_id) FROM daily_climate_metric_revisions r))""")


def repair(directory, run):
    shutil.copyfile(REPAIR, directory / REPAIR.name)
    result = run()
    assert result.returncode == 0, result.stderr


def test_forward_repair_preserves_rows_function_identity_acl_and_noop(capture_ready):
    query, directory, run = capture_ready
    before_objects = objects(query)
    before_rows = rows(query)
    original_capture = next(obj for obj in before_objects if obj["name"] == "fn_capture_daily_climate_metric_revision")
    repair(directory, run)
    assert_applied(query, ALL)
    assert rows(query) == before_rows
    actual = objects(query)
    assert len(actual) == 1
    assert actual[0]["source"] != original_capture["source"]
    assert {k: v for k, v in actual[0].items() if k != "source"} == {
        k: v for k, v in original_capture.items() if k != "source"
    }
    before_noop = objects(query), rows(query)
    assert run().returncode == 0
    assert (objects(query), rows(query)) == before_noop
    # Exact-state direct SQL rerun is also safe; no helper gets recreated.
    query("BEGIN;" + REPAIR.read_text() + "COMMIT;")
    assert (objects(query), rows(query)) == before_noop


def test_exact_legacy_payload_parity_and_all_metric_updates(capture_ready):
    query, directory, run = capture_ready
    expected = query("SELECT fn_daily_climate_metric_payload(d) FROM daily_summary d WHERE date='2026-09-04'")
    repair(directory, run)
    query("""SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
        INSERT INTO public.daily_summary SELECT (jsonb_populate_record(NULL::public.daily_summary,
          to_jsonb(d)||'{"date":"2026-09-05"}'::jsonb)).*
          FROM public.daily_summary d WHERE date='2026-09-04';
        RESET SESSION AUTHORIZATION;""")
    assert query("SELECT metrics FROM daily_climate_metric_revisions WHERE day='2026-09-05'") == expected
    binary = (
        "compliance_pct",
        "temp_compliance_pct",
        "vpd_compliance_pct",
        "stress_hours_heat",
        "stress_hours_cold",
        "stress_hours_vpd_high",
        "stress_hours_vpd_low",
    )
    graded = (
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
    for field in (*binary, *graded):
        count = int(query("SELECT count(*) FROM daily_climate_metric_revisions"))
        query(f"""SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
            UPDATE public.daily_summary SET {field}=coalesce({field},0)+0.125 WHERE date='2026-09-05';
            RESET SESSION AUTHORIZATION;""")
        assert int(query("SELECT count(*) FROM daily_climate_metric_revisions")) == count + 2
        latest = json.loads(
            query("SELECT metrics FROM daily_climate_metric_revisions ORDER BY revision_id DESC LIMIT 1")
        )
        assert latest["binary" if field in binary else "graded"][field] == float(
            query(f"SELECT {field} FROM daily_summary WHERE date='2026-09-05'")
        )
    diagnostic = {
        "definition": "house-average-observed-minute-v1",
        **{
            key: False
            for key in (
                "fixed_sensor_panel",
                "duration_weighted",
                "physical_proof_eligible",
                "crop_outcome_eligible",
                "experiment_endpoint_eligible",
            )
        },
    }
    query(
        f"UPDATE daily_summary SET climate_observed_minute_metrics='{json.dumps(diagnostic)}'::jsonb WHERE date='2026-09-05'"
    )
    latest = json.loads(query("SELECT metrics FROM daily_climate_metric_revisions ORDER BY revision_id DESC LIMIT 1"))
    assert latest["observed_minute_diagnostic"] == diagnostic
    before_noop = query("SELECT count(*) FROM daily_climate_metric_revisions")
    query("UPDATE daily_summary SET cost_total=coalesce(cost_total,0)+1 WHERE date='2026-09-05'")
    query("UPDATE daily_summary SET compliance_pct=compliance_pct WHERE date='2026-09-05'")
    assert query("SELECT count(*) FROM daily_climate_metric_revisions") == before_noop
    query("UPDATE daily_summary SET compliance_pct=NULL WHERE date='2026-09-05'")
    assert (
        query(
            "SELECT metrics->'binary'->'compliance_pct' FROM daily_climate_metric_revisions ORDER BY revision_id DESC LIMIT 1"
        )
        == "null"
    )
    query("UPDATE daily_summary SET date='2026-09-06' WHERE date='2026-09-05'")
    assert (
        query("SELECT operation||':'||day FROM daily_climate_metric_revisions ORDER BY revision_id DESC LIMIT 1")
        == "after_update:2026-09-06"
    )
    query("DELETE FROM daily_summary WHERE date='2026-09-06'")
    assert query("SELECT operation FROM daily_climate_metric_revisions ORDER BY revision_id DESC LIMIT 1") == "delete"


@pytest.mark.parametrize(
    "drift",
    [
        "DROP FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary)",
        "CREATE OR REPLACE FUNCTION public.fn_capture_daily_climate_metric_revision() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN RETURN NULL; END; $$",
        "GRANT EXECUTE ON FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) TO PUBLIC",
        "ALTER FUNCTION public.fn_capture_daily_climate_metric_revision() SECURITY INVOKER",
        "ALTER FUNCTION public.fn_capture_daily_climate_metric_revision() OWNER TO verdify_api_runtime",
        "GRANT EXECUTE ON FUNCTION public.fn_capture_daily_climate_metric_revision() TO verdify_api_runtime",
        "ALTER FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) SECURITY DEFINER",
        "GRANT EXECUTE ON FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) TO verdify_ingestor_runtime",
        "ALTER FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) SET search_path TO public,pg_catalog",
        "CREATE OR REPLACE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary) RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$ SELECT '{}'::jsonb $$",
    ],
)
def test_predecessor_body_and_privilege_drift_refused_without_normalizing(capture_ready, drift):
    query, directory, run = capture_ready
    query(drift)
    before = objects(query), rows(query)
    shutil.copyfile(REPAIR, directory / REPAIR.name)
    result = run()
    assert result.returncode != 0
    assert "inline capture refuses" in result.stderr
    assert (objects(query), rows(query)) == before
    assert query("SELECT count(*) FROM schema_migrations WHERE seq=247") == "0"


def test_unexpected_dependency_is_not_cascaded(capture_ready):
    query, directory, run = capture_ready
    query(
        "CREATE VIEW fixture_payload_dependency AS SELECT fn_daily_climate_metric_payload(d) AS payload FROM daily_summary d"
    )
    before = objects(query), rows(query)
    shutil.copyfile(REPAIR, directory / REPAIR.name)
    result = run()
    assert result.returncode != 0
    assert "other objects depend on it" in result.stderr
    assert (objects(query), rows(query)) == before
    assert query("SELECT count(*) FROM fixture_payload_dependency") == "4"
    assert query("SELECT count(*) FROM schema_migrations WHERE seq=247") == "0"


def test_other_string_body_caller_is_preserved(capture_ready):
    query, directory, run = capture_ready
    query("""CREATE FUNCTION public.fixture_other_payload_caller(d public.daily_summary)
        RETURNS jsonb LANGUAGE sql AS $$ SELECT public.fn_daily_climate_metric_payload(d) $$;""")
    before = objects(query), rows(query)
    shutil.copyfile(REPAIR, directory / REPAIR.name)
    result = run()
    assert result.returncode != 0
    assert "inline capture refuses other stored payload callers" in result.stderr
    assert (objects(query), rows(query)) == before
    assert query("SELECT count(*) FROM schema_migrations WHERE seq=247") == "0"
    assert (
        query("SELECT to_regprocedure('public.fixture_other_payload_caller(public.daily_summary)') IS NOT NULL") == "t"
    )


def test_reintroduced_private_name_is_no_longer_called(capture_ready):
    query, directory, run = capture_ready
    repair(directory, run)
    query("""CREATE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary)
        RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'obsolete helper executed'; END; $$;
        REVOKE ALL ON FUNCTION public.fn_daily_climate_metric_payload(public.daily_summary) FROM PUBLIC;""")
    before_count = int(query("SELECT count(*) FROM daily_climate_metric_revisions"))
    query("""SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
        UPDATE public.daily_summary SET compliance_pct=compliance_pct+1 WHERE date='2026-09-04';
        RESET SESSION AUTHORIZATION;""")
    assert int(query("SELECT count(*) FROM daily_climate_metric_revisions")) == before_count + 2


def test_fault_after_drop_rolls_back_and_normal_runner_resumes(capture_ready):
    query, directory, run = capture_ready
    before = objects(query), rows(query)
    (directory / REPAIR.name).write_text(REPAIR.read_text() + "\nSELECT 1/0;\n")
    assert run().returncode != 0
    assert (objects(query), rows(query)) == before
    assert query("SELECT count(*) FROM schema_migrations WHERE seq=247") == "0"
    repair(directory, run)
    assert_applied(query, ALL)
    assert rows(query) == before[1]


def test_existing_writer_connection_reloads_replaced_trigger(capture_ready):
    query, directory, run = capture_ready

    async def exercise():
        connection = await asyncpg.connect(
            host=query("SHOW unix_socket_directories"), port=55472, user="scorecard_fixture", database="postgres"
        )
        try:
            await connection.execute("SET SESSION AUTHORIZATION verdify_ingestor_runtime_login")
            await connection.execute(
                "UPDATE public.daily_summary SET compliance_pct=compliance_pct+1 WHERE date='2026-09-04'"
            )
            repair(directory, run)
            before_count = int(query("SELECT count(*) FROM daily_climate_metric_revisions"))
            await connection.execute(
                "UPDATE public.daily_summary SET compliance_pct=compliance_pct+1 WHERE date='2026-09-04'"
            )
            assert int(query("SELECT count(*) FROM daily_climate_metric_revisions")) == before_count + 2
        finally:
            await connection.close()

    asyncio.run(exercise())


def test_only_attested_trigger_body_controls_capture_after_repair(capture_ready):
    query, directory, run = capture_ready
    repair(directory, run)
    install_actual_attestation_probe(query)
    assert (
        query("SELECT to_regprocedure('public.fn_daily_climate_metric_payload(public.daily_summary)') IS NULL") == "t"
    )
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    # The only mutable payload code is now in the directly attested trigger.
    query("""CREATE OR REPLACE FUNCTION public.fn_capture_daily_climate_metric_revision()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN RETURN NULL; END; $$;""")
    assert attestation_probe(query)["ingestor"] == "f"


def test_repair_does_not_refresh_stored_startup_receipts(capture_ready):
    query, directory, run = capture_ready
    install_actual_attestation_probe(query)
    before = query(
        "SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r"
    )
    repair(directory, run)
    assert (
        query("SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r")
        == before
    )
    assert attestation_probe(query)["ingestor"] == "f"


def test_prior_migrations_remain_immutable_and_forward_sql_is_wrap_safe():
    assert (
        hashlib.sha256((ROOT / "db/migrations/217-runtime-role-boundary.sql").read_bytes()).hexdigest()
        == "15369af1c28692addc2d0d758dcbc4efb25be549aade3ce3690a0f29d565522f"
    )
    source = REPAIR.read_text()
    assert "DROP FUNCTION IF EXISTS public.fn_daily_climate_metric_payload(public.daily_summary) RESTRICT" in source
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in source
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(REPAIR)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    expected = (
        "501590af14b957658e3da8b11abd79023812daee296f2f7ee910fc20370142f0",
        "80de73d174fc69c1921a362392147e271142d6357481172c07b9b3a6dde226fc",
        "c7d747ab72b451cdfd1ad51502e149f80e97e8b0ab9902391db5146847d62620",
        "8b04febbd7c9b620c922a7e4b5be0061f74423f6eb7f9f2a77f9f1caabe914ad",
        "28f490631031e5e2424ff2425f8d344835d15d4b1bca79fc78c8c3559ce84e45",
        "9e45f1b075ec1670392a6075f83fee4beb18d75d032adf722ebf6969312873db",
    )
    assert tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in MIGRATIONS) == expected


def test_runtime_cannot_recreate_private_helper_or_modify_capture_function(capture_ready):
    query, directory, run = capture_ready
    repair(directory, run)
    for login in ("verdify_api_runtime_login", "verdify_ingestor_runtime_login"):
        assert query(f"SELECT has_schema_privilege('{login}','public','CREATE')") == "f"
        assert (
            query(f"SELECT has_function_privilege('{login}','fn_capture_daily_climate_metric_revision()','EXECUTE')")
            == "f"
        )
        with pytest.raises(AssertionError, match="permission denied for schema public"):
            query(f"""SET SESSION AUTHORIZATION {login};
                CREATE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary)
                RETURNS jsonb LANGUAGE sql AS $$ SELECT '{{}}'::jsonb $$;""")
    assert (
        query("SELECT to_regprocedure('public.fn_daily_climate_metric_payload(public.daily_summary)') IS NULL") == "t"
    )


def test_repaired_capture_respects_outer_writer_rollback(capture_ready):
    query, directory, run = capture_ready
    repair(directory, run)
    before = rows(query)
    query("""BEGIN; SET LOCAL SESSION AUTHORIZATION verdify_ingestor_runtime_login;
        UPDATE public.daily_summary SET compliance_pct=compliance_pct+1 WHERE date='2026-09-04';
        ROLLBACK;""")
    assert rows(query) == before
