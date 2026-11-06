"""Exact302 delivery: ordered admission, four dashboard-only read views, unchanged seals, native evidence.

#1361: the dashboard's Planner section shows plan cost, the experiment list and a study's arm calendar. The dashboard
duty reads plan_journal and planner_trigger_ledger already (284) but none of the experiment tables nor
planner_inference_runs. 302 adds four security-barrier views, owned by verdify, with SELECT for the dashboard duty and
nobody else, so every runtime seal keeps 301's value and no receipt is re-sealed (302 follows 301 in one change). v_planner_inference_runs carries the
whole row as jsonb (`attempt`), so the Cortex attempt columns of #1350 show up there once migration 294 adds them.
test_native_qualification_is_recorded is the merge gate for the rolled-back production run that measures that and runs
db/qualification/302-planner-section-reads-fixture.sql: a strict xfail while the fixture says it is pending, as 301's.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = 301
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
VIEWS = (
    "public.v_planner_experiments",
    "public.v_planner_assignments",
    "public.v_planner_experiment_events",
    "public.v_planner_inference_runs",
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("planner_section302_delivery", "c0-migration-delivery.py")
six = load("planner_section302_six", "bootstrap-six-runtime-roles.py")
maintenance = load("planner_section302_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("planner_section302_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_302
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/302-planner-section-reads-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/302-planner-section-reads-native-qualification.json"
NUMBERS = [*range(255, 292), 301, 302]


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


BODY = uncommented(SOURCE.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in NUMBERS]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in NUMBERS}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_302_SHA256 == six.PLANNER_SECTION_MIGRATION[2]
    assert six.PLANNER_SECTION_MIGRATION[:2] == (302, c0.SUCCESSOR_302) == (302, "302-planner-section-reads.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="302 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_302: "0" * 64})
    rows = later()
    for invalid in (rows + ["303-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["302-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_302 == predecessor == registered(PREDECESSOR) == registered(302)
    assert c0.SUCCESSOR_302_DIGESTS == {API: six.SUCCESSOR_302["api"], INGESTOR: six.SUCCESSOR_302["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(302, six.SUCCESSOR_302), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.PLANNER_SECTION_MIGRATION) + ")"
    assert six.sealed_sql_302() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=302" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.PLANNER_SECTION_MIGRATION)
    # 302 follows 301 in both ORs.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_301() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_302()) == 1
    assert '"302-planner-section-reads.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_302_planner_section_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    assert "to_regclass('public.v_planner_experiments') IS NOT NULL" in SOURCE
    for digest in registered(302).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_302
    assert "'302 refuses an unqualified predecessor'" in SOURCE and "'302 changed a runtime boundary'" in SOURCE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


# ── The body: four views, the dashboard duty's SELECT only ──────────────────


def test_the_only_grant_is_the_dashboard_duty_select_and_public_is_revoked():
    grants = [" ".join(g.split()) for g in re.findall(r"\bGRANT\b.*?;", BODY, re.S)]
    assert grants == [f"GRANT SELECT ON {', '.join(VIEWS)} TO verdify_dashboard_runtime;"]
    revokes = [" ".join(r.split()) for r in re.findall(r"\bREVOKE\b.*?;", BODY, re.S)]
    assert revokes == [f"REVOKE ALL ON {', '.join(VIEWS)} FROM PUBLIC;"]
    for runtime in (
        "verdify_api_runtime",
        "verdify_ingestor_runtime",
        "verdify_mcp",
        "verdify_dashboard_runtime_login",
    ):
        assert runtime not in BODY, runtime
    created = re.findall(r"CREATE VIEW (\S+) WITH \(security_barrier=true\) AS", BODY)
    assert tuple(created) == VIEWS
    for statement in (
        "CREATE TABLE",
        "CREATE FUNCTION",
        "ALTER TABLE",
        "INSERT",
        "UPDATE ",
        "DELETE",
        "SECURITY DEFINER",
    ):
        assert statement not in BODY, statement


def test_views_expose_no_free_text_or_payload_and_take_in_later_attempt_columns():
    events = BODY.split("CREATE VIEW public.v_planner_experiment_events", 1)[1].split(";", 1)[0]
    assert "detail" not in events
    experiments = BODY.split("CREATE VIEW public.v_planner_experiments", 1)[1].split("COMMENT ON", 1)[0]
    # Only the closing event's machine reason, never its authorization refs or artifact hashes.
    assert "c.detail ->> 'v2_event'" in experiments and "c.detail ->> 'reason'" in experiments
    assert "authorization_ref" not in experiments
    runs = BODY.split("CREATE VIEW public.v_planner_inference_runs", 1)[1].split("COMMENT ON", 1)[0]
    assert "to_jsonb(r) - ARRAY['proposal', 'response_body', 'error', 'price_snapshot'" in runs
    assert "r.*" not in runs  # a whole-row reference, not a star: columns 294 adds appear in `attempt`


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_reads_as_the_dashboard_login_and_finds_the_closed_draft():
    text = NATIVE.read_text()
    qualifier.native_checks(NATIVE)
    assert "SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;" in text
    assert text.count("RESET SESSION AUTHORIZATION;") == 1
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")
    assert "45039c86-c1d9-52f6-a0a9-d94a17bc4b14" in text and "'facility_safe_closure'" in text
    for view in VIEWS:
        assert f"PERFORM * FROM {view}" in text, view


def assert_recorded(evidence):
    """What the recorded native qualification must show before 302 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "302 native qualification pending: with production at exact301 (or --apply-first each predecessor), run "
        "scripts/qualify-migration-successor.py db/migrations/302-planner-section-reads.sql --native-fixture "
        "db/qualification/302-planner-section-reads-fixture.sql --fixture "
        "tests/fixtures/302-planner-section-reads-native-qualification.json on production (every session rolls "
        "back) and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_302
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_302_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_302 == registered(302) == registered(PREDECESSOR)
    assert evidence["before"] == evidence["after_rollback"] == registered(PREDECESSOR)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"dashboard": True}


def fake_qualification(monkeypatch, *, inside=None, native=None):
    """Replay the qualifier's three sessions for 302, without a database, on production at 301."""
    seals = registered(302)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"dashboard": True}}
    replies = {
        "probe": {"before": state(PREDECESSOR), "successor": state(PREDECESSOR)},
        "final": {"inside": state(302) if inside is None else inside, "native_fixture": native or fixture},
        "after": {"after": state(PREDECESSOR)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_302_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_302
    assert evidence["projected_successor"] == six.SUCCESSOR_302 == qualifier.successor_literals(SOURCE)
    assert evidence["before"] == registered(PREDECESSOR)


@pytest.mark.xfail(
    json.loads(FIXTURE.read_text()).get("final_migration_qualification_pending", True),
    reason="DO NOT MERGE: 301 and 302 must be natively qualified on production first",
    strict=True,
)
def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact302 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
