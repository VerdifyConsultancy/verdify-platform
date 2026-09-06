"""Native Timescale source/interval/consumer checks; synthetic, not commissioning."""

import ast
import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_c0_migration_delivery import owning as owning
from test_c0_release_rehearsal import attestation_probe, install_actual_attestation_probe
from test_scorecard_semantics import isolated_pg as plain_pg

from ingestor import partial_energy
from ingestor import shelly_energy as energy

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/248-shelly-source-interval-accounting.sql"
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def raw(value, at, index=0):
    return {
        "entity_id": energy.POWER_ENTITIES[index],
        "state": str(value),
        "attributes": {"unit_of_measurement": "W"},
        "last_updated": at.isoformat() if at else None,
    }


def sample(at=NOW, a=600, b=400, age=0):
    return energy.build_sample(
        {
            entity: raw(value, at - timedelta(seconds=age), index)
            for index, (entity, value) in enumerate(zip(energy.POWER_ENTITIES, (a, b), strict=True))
        },
        at,
    )


@pytest.mark.parametrize(
    "value,age,quality",
    [
        ("unavailable", 0, "unavailable"),
        ("NaN", 0, "nonfinite"),
        ("Infinity", 0, "nonfinite"),
        (0, 300, "stale"),
        (0, 301, "stale"),
        (1, -1, "future"),
        (0, 0, "ok"),
        (-40, 0, "ok"),
    ],
)
def test_channel_missingness_and_clock(value, age, quality):
    result = energy.build_sample({energy.POWER_ENTITIES[0]: raw(value, NOW - timedelta(seconds=age))}, NOW)
    assert result.ch0_quality == quality and result.ch1_quality == "missing"
    assert result.watts_total is None and result.kwh_today is None
    assert result.watts_heat is result.watts_other is result.watts_fans is None


def test_signed_zero_identity_units_and_missing_timestamp():
    assert sample(a=-600, b=400).watts_total == -200
    assert sample(a=0, b=0).watts_total == 0
    data = raw(0, None)
    assert energy.build_sample({energy.POWER_ENTITIES[0]: data}, NOW).ch0_quality == "unknown_time"
    data["attributes"]["unit_of_measurement"] = "kW"
    assert energy.build_sample({energy.POWER_ENTITIES[0]: data}, NOW).ch0_quality == "invalid"
    data = raw(2, NOW, 1)
    assert energy.build_sample({energy.POWER_ENTITIES[0]: data}, NOW).ch0_quality == "invalid"
    assert energy.build_sample({}, NOW).watts_total is None


@pytest.fixture
def isolated_pg(tmp_path):
    yield from plain_pg.__wrapped__(tmp_path, preload="timescaledb")


@pytest.fixture
def predecessor(isolated_pg):
    q = isolated_pg
    q("CREATE EXTENSION timescaledb")
    setup = (
        (ROOT / "db/migrations/tests/test-194-scope-aware-resource-accounting.sql")
        .read_text()
        .split("\\i db/migrations/194-scope-aware-resource-accounting.sql")[0]
    )
    setup = "\n".join(line for line in setup.splitlines() if not line.startswith("\\"))
    q(setup + "\n" + (ROOT / "db/migrations/194-scope-aware-resource-accounting.sql").read_text() + "\nCOMMIT;")
    q("""SELECT create_hypertable('public.energy','ts',chunk_time_interval=>interval '1 day');
        ALTER TABLE public.energy SET (timescaledb.compress,timescaledb.compress_segmentby='greenhouse_id');
        CREATE ROLE verdify_ingestor_runtime NOLOGIN;
        CREATE ROLE verdify_api_runtime NOLOGIN;
        CREATE ROLE verdify_ingestor_runtime_login LOGIN;
        CREATE ROLE verdify_api_runtime_login LOGIN;
        CREATE VIEW public.v_runtime_energy_write WITH (security_barrier=true,security_invoker=false) AS
        SELECT ts,watts_total,watts_heat,watts_fans,watts_other,kwh_today FROM public.energy;
        GRANT INSERT(ts,watts_total,watts_heat,watts_fans,watts_other,kwh_today)
        ON public.v_runtime_energy_write TO verdify_ingestor_runtime;
        GRANT SELECT ON public.v_energy_daily,public.v_energy_estimate_reconciliation,public.v_energy_meter_health TO verdify_api_runtime;
        ALTER TABLE public.daily_summary ADD COLUMN peak_kw float8;
        ALTER TABLE public.daily_summary ADD COLUMN captured_at timestamptz;
    """)
    assert q("SELECT extversion FROM pg_extension WHERE extname='timescaledb'") == "2.25.2"
    return q


@pytest.fixture
def database(predecessor):
    assert (
        predecessor(
            "SELECT regexp_replace(pg_get_viewdef('public.v_runtime_energy_write'::regclass,true),'\\s','','g')"
        )
        == "SELECTts,watts_total,watts_heat,watts_fans,watts_other,kwh_todayFROMenergy;"
    )
    predecessor("BEGIN;" + MIGRATION.read_text() + "COMMIT;")
    return predecessor


def insert_many(query, rows, duty=False):
    payload = json.dumps([row.model_dump(mode="json") for row in rows]).replace("'", "''")
    columns = ",".join(energy.WRITE_FIELDS)
    query(
        ("SET ROLE verdify_ingestor_runtime;" if duty else "")
        + f"INSERT INTO public.v_runtime_energy_write ({columns}) SELECT {columns} FROM "
        f"jsonb_populate_recordset(NULL::public.energy, '{payload}'::jsonb);RESET ROLE;"
    )


def daily(query):
    return json.loads(query("SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY date),'[]') FROM public.v_energy_daily d"))


def test_null_poll_breaks_interval_and_no_trailing_extrapolation(database):
    insert_many(
        database,
        [sample(), energy.build_sample({}, NOW + timedelta(seconds=120)), sample(NOW + timedelta(seconds=600))],
    )


def interval_sql(select):
    source = MIGRATION.read_text()
    calculation = source.split("WITH intervals AS (\n", 1)[1].split("\n), split AS (", 1)[0]
    health_calculation = source.split("WITH intervals AS (\n", 2)[2].split("\n), latest AS (", 1)[0]
    assert calculation == health_calculation
    assert "CREATE VIEW public.v_energy_observation_intervals" not in source
    return "WITH intervals AS (" + calculation + ") " + select
    (row,) = daily(database)
    assert row["measured_kwh"] == 0.033 and row["observed_hours"] == 0.033
    assert row["sample_count"] == 1 and row["available_for_scoring"] is False
    assert row["measured_scope"] == "partial_shelly_two_channels"


@pytest.mark.parametrize("age,seconds", [(0, 300), (240, 60), (299, 1)])
def test_hold_stops_at_earliest_channel_expiry(database, age, seconds):
    insert_many(database, [sample(age=age), sample(NOW + timedelta(seconds=900))])
    result = database(interval_sql("SELECT extract(epoch FROM(end_ts-ts)) FROM intervals ORDER BY ts LIMIT 1"))
    assert float(result) == seconds


def test_legacy_duplicate_and_nonfinite_rows_never_fill_coverage(database):
    database("INSERT INTO public.energy(ts,watts_total) VALUES ('2026-08-14 10:00Z',1000),('2026-08-14 11:00Z','NaN')")
    insert_many(database, [sample(), sample(), sample(NOW + timedelta(seconds=300))])
    (row,) = daily(database)
    assert row["measured_kwh"] is None and row["observed_hours"] == 0
    assert row["measured_quality"] == "unverified_source"


@pytest.mark.parametrize(
    "start,hours",
    [
        ("2026-03-08T07:00:00+00:00", 23),
        ("2026-11-01T06:00:00+00:00", 25),
    ],
)
def test_actual_dst_denominator_and_session_timezone(database, start, hours):
    start = datetime.fromisoformat(start)
    insert_many(database, [sample(start + timedelta(seconds=i)) for i in range(0, hours * 3600 + 1, 300)])
    outputs = []
    for zone in ("UTC", "America/Denver", "Asia/Tokyo"):
        database(f"ALTER DATABASE postgres SET timezone='{zone}'")
        outputs.append(daily(database))
    assert outputs[0] == outputs[1] == outputs[2]
    completed = outputs[0][0]
    assert completed["measured_kwh"] == hours and completed["observed_hours"] == hours
    assert completed["meter_coverage_pct"] == 100 and completed["available_for_scoring"] is False
    assert outputs[0][1]["measured_kwh"] is None


def test_cross_midnight_signed_energy_conserved(database):
    at = datetime.fromisoformat("2026-08-15T05:59:00+00:00")
    insert_many(database, [sample(at, a=-600, b=-400), sample(at + timedelta(seconds=120), a=0, b=0)])
    assert [row["measured_kwh"] for row in daily(database)] == [-0.017, -0.017]
    assert float(database(interval_sql("SELECT sum(extract(epoch FROM(end_ts-ts))) FROM intervals"))) == 120


def test_real_duty_insert_and_api_read_not_base_write(database):
    insert_many(database, [sample(), sample(NOW + timedelta(seconds=300))], duty=True)
    assert database("SET ROLE verdify_api_runtime; SELECT measured_kwh FROM public.v_energy_daily") == "0.083"
    for role in ("verdify_api_runtime", "verdify_ingestor_runtime"):
        assert database(f"SELECT has_table_privilege('{role}','public.energy','INSERT')") == "f"
    with pytest.raises(AssertionError, match="permission denied"):
        database("SET ROLE verdify_api_runtime; INSERT INTO public.v_runtime_energy_write(ts) VALUES(now())")


def test_daily_consumer_clears_stale_values_and_keeps_other_house(database):
    database(
        "INSERT INTO public.daily_summary(date,greenhouse_id,kwh_total,peak_kw) VALUES ('2026-08-14','vallery',99,9),('2026-08-14','gap_house',88,8)"
    )

    class Connection:
        async def execute(self, sql, day, house):
            database(sql.replace("$1", f"'{day}'::date").replace("$2", f"'{house}'"))

    asyncio.run(partial_energy.refresh_partial_energy(Connection(), "2026-08-14", "vallery"))
    assert (
        database("SELECT kwh_total IS NULL AND peak_kw IS NULL FROM public.daily_summary WHERE greenhouse_id='vallery'")
        == "t"
    )
    assert database("SELECT kwh_total FROM public.daily_summary WHERE greenhouse_id='gap_house'") == "88"


def test_float_roundoff_is_not_missing_and_zero_is_observed(database):
    insert_many(
        database,
        [
            sample(a=0.1, b=0.2),
            sample(NOW + timedelta(seconds=60), a=0, b=0),
            sample(NOW + timedelta(seconds=120), a=0, b=0),
        ],
    )
    assert database(interval_sql("SELECT bool_and(qualified) FROM intervals")) == "t"
    assert daily(database)[0]["measured_kwh"] == 0
    assert daily(database)[0]["sample_count"] == 2


def test_future_or_stale_gateway_clock_does_not_refresh_health(database):
    at = datetime.fromisoformat(database("SELECT now()::text"))
    insert_many(database, [sample(at, age=600), sample(at + timedelta(seconds=1), age=-60)])
    row = json.loads(database("SELECT to_jsonb(h) FROM public.v_energy_meter_health h WHERE greenhouse_id='vallery'"))
    assert row["latest_ts"] is None and row["fresh_for_observation"] is False


def test_unknown_facade_refuses_before_schema_changes(predecessor):
    predecessor(
        "CREATE OR REPLACE VIEW public.v_runtime_energy_write AS SELECT ts,watts_total,watts_heat,watts_fans,watts_other,kwh_today FROM public.energy WHERE false"
    )
    with pytest.raises(AssertionError, match="unknown write facade"):
        predecessor("BEGIN;" + MIGRATION.read_text() + "COMMIT;")
    assert (
        predecessor(
            "SELECT count(*) FROM pg_attribute WHERE attrelid='public.energy'::regclass AND attname='measurement_revision'"
        )
        == "0"
    )


def test_actual_attestation_refuses_changed_boundary_without_refresh(predecessor):
    q = predecessor
    install_actual_attestation_probe(q)
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    receipt_sql = (
        "SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM public.runtime_ordinary_login_attestation_receipts r"
    )
    before = q(receipt_sql)
    metadata_sql = """SELECT jsonb_agg(to_jsonb(r) ORDER BY relname) FROM (
        SELECT oid,relname,relowner,relacl FROM pg_class WHERE oid IN (
        'public.v_runtime_energy_write'::regclass,'public.v_energy_daily'::regclass,
        'public.v_energy_meter_health'::regclass,'public.v_energy_estimate_reconciliation'::regclass)) r"""
    metadata_before = q(metadata_sql)
    q("BEGIN;" + MIGRATION.read_text() + "COMMIT;")
    assert (
        q(
            "SELECT bool_and(boundary_sha256 <> public.fn_runtime_ordinary_boundary_digest(login_name)) FROM public.runtime_ordinary_login_attestation_receipts"
        )
        == "t"
    )
    assert attestation_probe(q) == {"api": "f", "ingestor": "f"}
    assert q(receipt_sql) == before
    assert q(metadata_sql) == metadata_before
    # Neither calculator may become an untracked private-view dependency.
    digest_sql = "SELECT jsonb_object_agg(login_name,encode(public.fn_runtime_ordinary_boundary_digest(login_name),'hex')) FROM public.runtime_ordinary_login_attestation_receipts;"
    current = json.loads(q(digest_sql))
    source = MIGRATION.read_text()
    for name, next_name in (
        ("v_energy_daily", "v_energy_meter_health"),
        ("v_energy_meter_health", "v_energy_estimate_reconciliation"),
    ):
        statement = source.split("CREATE OR REPLACE VIEW public." + name + " AS", 1)[1].split(
            "CREATE OR REPLACE VIEW public." + next_name + " AS", 1
        )[0]
        assert "300 seconds" in statement
        changed = "CREATE OR REPLACE VIEW public." + name + " AS" + statement.replace("300 seconds", "301 seconds")
        mutated = json.loads(q("BEGIN;" + changed + digest_sql + "ROLLBACK;"))
        assert all(mutated[login] != current[login] for login in current)
        assert json.loads(q(digest_sql)) == current and q(receipt_sql) == before


def test_seven_file_owning_runner_refuses_pending_248_without_mutation(owning):
    from test_c0_release_rehearsal import snapshot

    query, directory, _, _, run = owning
    shutil.copyfile(MIGRATION, directory / MIGRATION.name)
    before = snapshot(query)
    result = run()
    assert result.returncode != 0
    assert "pending migration outside the qualified C0 bundle" in result.stderr
    assert "no per-file fallback" in result.stderr
    assert snapshot(query) == before


def test_public_resource_projection_keeps_scopes_and_unqualified_flags(database):
    from test_api_public_output_policy import load_api

    insert_many(database, [sample(), sample(NOW + timedelta(seconds=300))])
    row = json.loads(
        database(
            "SELECT to_jsonb(e) FROM public.v_energy_estimate_reconciliation e WHERE greenhouse_id='vallery' AND date='2026-08-14'"
        )
    )
    output = load_api()._project_energy_resource(row)
    assert output["measured_kwh"] == 0.083
    assert output["estimate_delta_kwh"] is None
    assert output["measured_available_for_scoring"] is False
    assert output["measured_scope"] == "partial_shelly_two_channels"


def test_both_daily_paths_call_missingness_preserving_consumer():
    tree = ast.parse((ROOT / "ingestor/tasks/daily.py").read_text())
    for name in ("grow_light_daily", "_refresh_daily_summary_for_date"):
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "refresh_partial_energy"
        ]
        assert len(calls) == 1 and isinstance(calls[0].args[2], ast.Name) and calls[0].args[2].id == "GREENHOUSE_ID"


def test_compressed_legacy_rows_and_failed_migration_rollback(predecessor):
    q = predecessor
    q(
        "INSERT INTO public.energy(ts,watts_total) VALUES('2026-08-14 12:00Z',1000); SELECT compress_chunk(c) FROM show_chunks('public.energy') c"
    )
    before = q("SELECT jsonb_agg(to_jsonb(e)) FROM public.energy e")
    with pytest.raises(AssertionError, match="injected rollback"):
        q("BEGIN;" + MIGRATION.read_text() + "DO $$ BEGIN RAISE EXCEPTION 'injected rollback'; END $$; COMMIT;")
    assert q("SELECT jsonb_agg(to_jsonb(e)) FROM public.energy e") == before
    assert (
        q(
            "SELECT count(*) FROM pg_attribute WHERE attrelid='public.energy'::regclass AND attname='measurement_revision'"
        )
        == "0"
    )
    q("BEGIN;" + MIGRATION.read_text() + "COMMIT;")
    assert daily(q)[0]["measured_kwh"] is None
    assert (
        q("SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name='energy' AND is_compressed") == "1"
    )
