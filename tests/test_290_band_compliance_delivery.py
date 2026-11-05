"""Exact290 delivery: ordered admission, one definition of time in band, unchanged seals, native evidence.

#1358: every "time in band" number graded the house against setpoint_changes temp_*/vpd_* rows last written on
2026-09-06, and the day/night split read mv_zone_band_grade, which nothing refreshed after 2026-06-12 (#1314). 290
grades each 5-minute bucket against the band the controller served (fn_band_compliance_5m), persists each local
day (daily_band_compliance) from the matview-refresh CronJob, copies the whole-day shares into daily_summary's
legacy columns and points v_daily_scorecard at the rollup. Its objects are verdify's and its one EXECUTE goes to the
matview-refresh duty in that duty's own schema, so every runtime seal keeps 289's value and no receipt is re-sealed.
test_native_qualification_is_recorded is the merge gate for the rolled-back production run that measures that and
runs db/qualification/290-band-compliance-fixture.sql.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = 289
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
NEW_FUNCTIONS = (
    "public.fn_band_compliance_5m(timestamptz, timestamptz)",
    "public.fn_band_compliance_day(date)",
    "public.fn_band_compliance_store(date)",
    "verdify_matview_refresh_runtime.fn_refresh_band_compliance()",
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("band_compliance290_delivery", "c0-migration-delivery.py")
six = load("band_compliance290_six", "bootstrap-six-runtime-roles.py")
maintenance = load("band_compliance290_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("band_compliance290_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_290
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/290-band-compliance-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/290-band-compliance-native-qualification.json"
CRONJOB = ROOT / "deploy/k8s/components/grafana/band-curve-refresh-cronjob.yaml"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


BODY = uncommented(SOURCE.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 291)]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 291)}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_290_SHA256 == six.BAND_COMPLIANCE_MIGRATION[2]
    assert six.BAND_COMPLIANCE_MIGRATION[:2] == (290, c0.SUCCESSOR_290) == (290, "290-band-compliance.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="290 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_290: "0" * 64})
    rows = later()
    for invalid in (rows + ["291-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["290-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_290 == predecessor == registered(PREDECESSOR) == registered(290)
    assert c0.SUCCESSOR_290_DIGESTS == {API: six.SUCCESSOR_290["api"], INGESTOR: six.SUCCESSOR_290["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(290, six.SUCCESSOR_290), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.BAND_COMPLIANCE_MIGRATION) + ")"
    assert six.sealed_sql_290() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=290" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.BAND_COMPLIANCE_MIGRATION)
    # 290 follows 289 in both ORs.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_289() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_290()) == 1
    assert '"290-band-compliance.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_290_band_compliance_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    assert "to_regclass('public.daily_band_compliance') IS NOT NULL" in SOURCE
    # The predecessor block and the successor block each pin all three seals.
    for digest in registered(290).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_290
    assert "'290 refuses an unqualified predecessor'" in SOURCE and "'290 changed a runtime boundary'" in SOURCE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


# ── The body: one definition, no runtime reach ──────────────────────────────


def test_the_only_grant_is_the_refresh_duty_execute_and_public_is_revoked():
    grants = [" ".join(g.split()) for g in re.findall(r"\bGRANT\b.*?;", BODY, re.S)]
    assert grants == [
        "GRANT EXECUTE ON FUNCTION verdify_matview_refresh_runtime.fn_refresh_band_compliance() "
        "TO verdify_matview_refresh_runtime;"
    ]
    revokes = [" ".join(r.split()) for r in re.findall(r"\bREVOKE\b.*?;", BODY, re.S)]
    assert revokes == [f"REVOKE ALL ON FUNCTION {f.replace(', ', ', ')} FROM PUBLIC;" for f in NEW_FUNCTIONS]
    for runtime in ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_mcp_runtime", "verdify_dashboard"):
        assert runtime not in BODY, runtime
    # Only the duty's entry point runs with its owner's rights; the definition is invoker-only.
    assert BODY.count("SECURITY DEFINER") == 1
    assert BODY.count("SECURITY INVOKER") == 3
    refresh = BODY.split("CREATE FUNCTION verdify_matview_refresh_runtime.fn_refresh_band_compliance()", 1)[1]
    assert "SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp" in refresh
    assert "IF session_user <> 'verdify_matview_refresh_runtime_login' THEN" in refresh
    assert "DATE '2026-09-01'" in refresh and "LIMIT 31" in refresh
    assert "REFRESH MATERIALIZED VIEW" not in BODY


def test_the_definition_grades_against_the_served_band():
    grade = BODY.split("AS $grade$", 1)[1].split("$grade$;", 1)[0]
    for role in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
        assert f"'band_house_{role}'" in grade
    assert "s.ts >= r.bucket - interval '600 seconds'" in grade  # API-06's carry
    assert "s.ts < r.bucket + interval '5 minutes'" in grade
    assert "public.fn_band_setpoints(a.bucket)" in grade and "WHERE a.roles < 4" in grade
    assert "public.fn_solar_phase(b.bucket + interval '150 seconds') < 2" in grade
    bodies = [BODY.split(f"${tag}$", 2)[1] for tag in ("grade", "day", "store", "refresh")]
    view = BODY.split("CREATE OR REPLACE VIEW public.v_daily_scorecard AS", 1)[1].split(";\n", 1)[0]
    for code in (*bodies, view):
        assert "setpoint_changes" not in code and "crop_target_profiles" not in code


def test_the_scorecard_keeps_its_columns_and_reads_the_rollup():
    view = BODY.split("CREATE OR REPLACE VIEW public.v_daily_scorecard AS", 1)[1].split(";\n", 1)[0]
    columns = re.findall(r"(?:AS (\w+),?\n)|(?:^\s+[db]\.(\w+),?$)", view, re.M)
    names = [a or b for a, b in columns]
    old = [
        "date",
        "temp_in_band_pct",
        "vpd_in_band_pct",
        "time_in_band_day_pct",
        "time_in_band_night_pct",
        "band_reason",
        "water_gal",
        "water_reason",
        "iris_required_written",
        "iris_required_missed",
        "alerts_critical",
        "alerts_warning",
        "alerts_info",
        "captured_at",
    ]
    assert names[: len(old)] == old  # CREATE OR REPLACE keeps the existing columns in order
    assert "mv_zone_band_grade" not in view
    assert "LEFT JOIN public.daily_band_compliance b ON b.date = d.date" in view


def test_the_cronjob_runs_the_refresh_after_the_matviews():
    job = yaml.safe_load(CRONJOB.read_text())
    command = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    calls = re.findall(r"SELECT (verdify_matview_refresh_runtime\.fn_\w+)\(\)", command)
    assert calls == [
        "verdify_matview_refresh_runtime.fn_attest_login",
        "verdify_matview_refresh_runtime.fn_refresh_mv_band_curve",
        "verdify_matview_refresh_runtime.fn_refresh_mv_equipment_runtime_daily",
        "verdify_matview_refresh_runtime.fn_refresh_mv_daily_kpi",
        "verdify_matview_refresh_runtime.fn_refresh_band_compliance",
    ]
    assert job["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 300


def test_no_user_facing_number_reads_the_frozen_band():
    """#1358 acceptance: nothing grades time in band against setpoint_changes temp_*/vpd_* any more."""
    daily = (ROOT / "ingestor/tasks/daily.py").read_text()
    refresh = daily.split("async def _refresh_daily_summary_for_date", 1)[1].split("\nasync def ", 1)[0]
    assert "setpoint_changes" not in refresh and "HOUSE_BAND_PARAMS" not in daily
    assert "band_house_temp_high" in refresh
    for column in ("compliance_pct=", "temp_compliance_pct=", "vpd_compliance_pct=", "stress_hours_"):
        assert column not in refresh.split("UPDATE daily_summary SET", 1)[1].split('"""', 1)[0], column
    series = (ROOT / "verdify_services/series.py").read_text()
    assert "setpoint_changes" not in series


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_runs_the_cronjob_call_and_reads_as_the_dashboard_login():
    text = NATIVE.read_text()
    qualifier.native_checks(NATIVE)
    assert text.index("SET SESSION AUTHORIZATION verdify_matview_refresh_runtime_login;") < text.index(
        "verdify_matview_refresh_runtime.fn_refresh_band_compliance()"
    )
    assert "SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;" in text
    assert text.count("RESET SESSION AUTHORIZATION;") == 2
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")
    assert "DATE '2026-10-29' AND DATE '2026-11-04'" in text


def assert_recorded(evidence):
    """What the recorded native qualification must show before 290 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "290 native qualification pending: run scripts/qualify-migration-successor.py "
        "db/migrations/290-band-compliance.sql --native-fixture db/qualification/290-band-compliance-fixture.sql "
        "--fixture tests/fixtures/290-band-compliance-native-qualification.json on production (every session "
        "rolls back) and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_290
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_290_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_290 == registered(290) == registered(PREDECESSOR)
    # Measured on production at exact289, which is live: nothing applied first and no seal moved.
    assert "applied_first" not in evidence
    assert evidence["before"] == evidence["after_rollback"] == registered(PREDECESSOR)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"matview_refresh": True, "dashboard": True}


def fake_qualification(monkeypatch, *, inside=None, native=None):
    """Replay the qualifier's three sessions for 290, without a database, on production at 289."""
    seals = registered(290)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"matview_refresh": True, "dashboard": True}}
    replies = {
        "probe": {"before": state(PREDECESSOR), "successor": state(PREDECESSOR)},
        "final": {"inside": state(290) if inside is None else inside, "native_fixture": native or fixture},
        "after": {"after": state(PREDECESSOR)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_290_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    # A failed native fixture writes nothing.
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_290
    assert evidence["projected_successor"] == six.SUCCESSOR_290 == qualifier.successor_literals(SOURCE)
    assert evidence["before"] == registered(PREDECESSOR)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact290 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
