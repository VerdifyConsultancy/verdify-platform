"""Exact daily-scorecard successor (VD-56, M6): ordered admission, the one grant, unchanged seals, native evidence.

285 adds one verdify-owned view and grants it to verdify_dashboard_runtime alone, so API-04's daily.* keys do not get
42501. Neither runtime digest takes in a relation only that duty can read, and the view's one helper, fn_solar_phase,
keeps PUBLIC's EXECUTE (217), so every runtime seal should keep its merged predecessor 284's exact value and no receipt
is re-sealed. The native qualification (the migration and its fixture inside rolled-back production transactions, with
--apply-first for whichever of 283 and 284 production lacks) measures that. Until then the file's successor block and
its registrations carry 284's seals as stand-ins, and test_native_qualification_is_recorded fails: the merge gate.
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
# The merged migration this successor is measured against.
PREDECESSOR = 284
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
DUTY, LOGIN = "verdify_dashboard_runtime", "verdify_dashboard_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("scorecard285_delivery", "c0-migration-delivery.py")
six = load("scorecard285_six", "bootstrap-six-runtime-roles.py")
maintenance = load("scorecard285_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("scorecard285_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_285
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/285-daily-scorecard-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/285-daily-scorecard-native-qualification.json"
# Final, and not yet applied in production when this was written: --apply-first takes them in this order.
UNAPPLIED = (ROOT / "db/migrations" / c0.SUCCESSOR_283, ROOT / "db/migrations" / c0.SUCCESSOR_284)


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 286)]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 286)}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def receipt():
    return json.loads(FIXTURE.read_text())


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_285_SHA256 == six.DAILY_SCORECARD_MIGRATION[2]
    assert six.DAILY_SCORECARD_MIGRATION[:2] == (285, c0.SUCCESSOR_285) == (285, "285-daily-scorecard-view.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="285 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_285: "0" * 64})
    rows = later()
    for invalid in (rows + ["286-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["285-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_285 == predecessor == registered(PREDECESSOR) == registered(285)
    assert c0.SUCCESSOR_285_DIGESTS == {API: six.SUCCESSOR_285["api"], INGESTOR: six.SUCCESSOR_285["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(285, six.SUCCESSOR_285), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.DAILY_SCORECARD_MIGRATION) + ")"
    assert six.sealed_sql_285() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=285" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DAILY_SCORECARD_MIGRATION)
    assert six.sealed_sql().endswith(" OR " + expected + ")")
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_285() + ")") == 1
    assert '"285-daily-scorecard-view.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_285_daily_scorecard_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    # The predecessor block and the successor block each pin all three seals.
    for digest in registered(285).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_285
    assert "UPDATE public" not in SOURCE  # no receipt is re-sealed


def test_top_level_is_one_view_and_its_one_grant_to_the_dashboard_duty():
    # Top level, outside comments, strings and DO bodies: no data change and nothing for another role.
    statements = safety.strip_sql_noise(SOURCE)
    assert not re.search(r"\b(REVOKE|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b", statements, re.IGNORECASE)
    assert re.findall(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(\w+)", statements, re.IGNORECASE) == ["VIEW"]
    grant = f"GRANT SELECT ON public.v_daily_scorecard TO {DUTY};"
    assert re.findall(r"\bGRANT\b[^;]*;", statements) == [grant]
    # The probe runs the file up to the successor block, so the digests it reads include the grant.
    assert grant in qualifier.split_successor(SOURCE)[0]


def test_file_and_fixture_are_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    first = [qualifier.applied_first(path)[1] for path in UNAPPLIED]
    for blocks in ((), first):
        for name, sql in qualifier.sessions(SOURCE, blocks, qualifier.native_checks(NATIVE)).items():
            assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
            assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


def test_native_fixture_reads_the_view_as_the_dashboard_login():
    text = NATIVE.read_text()
    login = re.search(rf"SET SESSION AUTHORIZATION {LOGIN};\n(.*?)\nRESET SESSION AUTHORIZATION;", text, re.S)[1]
    assert f"{DUTY}.fn_attest_login() IS NOT TRUE" in login
    assert re.search(r"PERFORM \* FROM public\.v_daily_scorecard", login)  # every column
    assert "PERFORM public.fn_solar_phase(" in login
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")


def assert_recorded(evidence):
    """What the recorded native qualification must show before 285 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "285 native qualification pending: run scripts/qualify-migration-successor.py "
        "db/migrations/285-daily-scorecard-view.sql --probe-only on production (rolled back; add --apply-first for "
        "db/migrations/283-irrigation-tables.sql and db/migrations/284-dashboard-runtime-grants.sql, in that order, "
        "for each production lacks). The digests must equal the file's stand-ins; a moved seal is a finding. Then run "
        "it again with --native-fixture db/qualification/285-daily-scorecard-fixture.sql --fixture "
        "tests/fixtures/285-daily-scorecard-native-qualification.json and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_285
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_285_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_285 == registered(285)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    if "applied_first" in evidence:
        first = evidence["applied_first"]
        assert first == [
            {"migration": getattr(c0, f"SUCCESSOR_{n}"), "seq": n, "sha256": getattr(c0, f"SUCCESSOR_{n}_SHA256")}
            for n in range(285 - len(first), 285)
        ]
        measured_against = evidence["applied_first_seals"]
    else:
        measured_against = evidence["before"]
    assert measured_against == registered(PREDECESSOR)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"dashboard": True}


def test_receipt_records_the_stand_ins_or_the_measurement_and_nothing_stale():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_285
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_285_SHA256
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    previous = registered(PREDECESSOR)
    if evidence["final_migration_qualification_pending"]:
        # Nothing is measured yet: the file and the registrations carry 284's literals.
        projected = evidence["projected_successor"]
        assert projected == qualifier.successor_literals(SOURCE) == six.SUCCESSOR_285 == registered(285) == previous
        assert "stand-in" in evidence["projection"]
        assert evidence["qualified_successor"] is None and evidence["native_fixture"] is None
    else:
        measured = evidence["qualified_successor"]
        assert measured == qualifier.successor_literals(SOURCE) == six.SUCCESSOR_285 == registered(285) == previous
    report_keys = {
        "migration",
        "measured_at",
        "before",
        "qualified_successor",
        "after_rollback",
        "exact_final_migration_sha256",
        "final_migration_qualification_pending",
        "inside_transaction_receipts_match",
        "final_migration_rollback_verified",
        "native_probe_source_sha256",
        "qualification_source_sha256",
        *qualifier.OPTION_EVIDENCE,
    }
    assert set(evidence) - report_keys == {"projected_successor", "projection"}


def fake_qualification(monkeypatch, *, applied_first, native=None):
    """Replay the qualifier's three sessions for 285, without a database, on production at 282 or 284."""
    predecessor, new = registered(PREDECESSOR), registered(285)
    live, live_seq = (registered(282), 282) if applied_first else (predecessor, PREDECESSOR)

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"dashboard": True}}
    probe = {"before": state(live, live_seq), "successor": {**state(predecessor, PREDECESSOR), **new}}
    final = {"inside": state(new, 285), "native_fixture": fixture if native is None else native}
    if applied_first:
        probe["applied"] = final["applied"] = state(predecessor, PREDECESSOR)
    replies = {"probe": probe, "final": final, "after": {"after": state(live, live_seq)}}
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


@pytest.mark.parametrize("applied_first", [(), UNAPPLIED], ids=["284-applied", "283-and-284-applied-first"])
def test_qualifier_records_285_and_the_gate_accepts_it(monkeypatch, tmp_path, applied_first):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first))
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == receipt()["projected_successor"]  # the projection stays beside it
    assert ("applied_first" in written) is bool(applied_first)
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first), native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact285 and its unchanged seals."""
    assert_recorded(receipt())
