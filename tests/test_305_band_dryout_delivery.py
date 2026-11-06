"""Exact305 delivery: ordered admission, the nightly dry-out flag in the 290 rollup, unchanged seals, native evidence.

#1365 (Jason, 2026-11-05): the night target is a dry-out, at least 4 h between 00:00 and 06:00 local at or above the
floor, max(served vpd_low, 0.74 kPa), the floor the firmware's night dry-out uses. 305 adds night_dryout_graded_min,
night_dryout_min and the generated night_dried_out to daily_band_compliance, computes them with fn_band_dryout_night
from fn_band_compliance_5m, stores them from fn_band_compliance_store (so the matview-refresh CronJob keeps them) and
fills the rows already stored. The table keeps no ACL entry and the new function's PUBLIC EXECUTE is revoked, so every
seal keeps 304's value. tests/test_dashboard_db_band_dryout_305.py proves the minutes and the flag on a private
database. test_native_qualification_is_recorded is the merge gate for the rolled-back production run (304 applied
first) that measures the seals and runs db/qualification/305-band-nightly-dryout-fixture.sql.
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
PREDECESSOR = 304
# Merged predecessors not yet applied in production when 305 is qualified (each one --apply-first).
CHAIN = (303, 304)
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("band_dryout305_delivery", "c0-migration-delivery.py")
six = load("band_dryout305_six", "bootstrap-six-runtime-roles.py")
maintenance = load("band_dryout305_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("band_dryout305_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_305
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/305-band-nightly-dryout-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/305-band-nightly-dryout-native-qualification.json"
NUMBERS = [*range(255, 292), 293, 294, 296, 301, 302, *CHAIN, 305]


# Production's newest applied migration when the successor is qualified, and the files applied first.
LIVE = 302
FIRST = [ROOT / "db/migrations" / getattr(c0, f"SUCCESSOR_{n}") for n in CHAIN]


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
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_305_SHA256 == six.BAND_DRYOUT_MIGRATION[2]
    assert six.BAND_DRYOUT_MIGRATION[:2] == (305, c0.SUCCESSOR_305) == (305, "305-band-nightly-dryout.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="305 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_305: "0" * 64})
    rows = later()
    for invalid in (rows + ["399-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["305-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_305 == predecessor == registered(PREDECESSOR) == registered(305)
    assert c0.SUCCESSOR_305_DIGESTS == {API: six.SUCCESSOR_305["api"], INGESTOR: six.SUCCESSOR_305["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(305, six.SUCCESSOR_305), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.BAND_DRYOUT_MIGRATION) + ")"
    assert six.sealed_sql_305() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=305" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.BAND_DRYOUT_MIGRATION)
    # 305 follows its predecessor in both ORs.
    assert six.sealed_sql().count(" OR " + getattr(six, f"sealed_sql_{PREDECESSOR}")() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_305()) == 1
    assert '"305-band-nightly-dryout.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_305_band_dryout_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    for digest in registered(305).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_305
    assert "'305 refuses an unqualified predecessor'" in SOURCE and "'305 changed a runtime boundary'" in SOURCE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


# ── The body: three columns, one function, the store and the refill ──────────


NEW_COLUMNS = ("night_dryout_graded_min", "night_dryout_min", "night_dried_out")


def test_the_only_acl_change_is_revoking_public_from_the_new_function():
    assert "GRANT" not in BODY
    revokes = [" ".join(r.split()) for r in re.findall(r"\bREVOKE\b.*?;", BODY, re.S)]
    assert revokes == ["REVOKE ALL ON FUNCTION public.fn_band_dryout_night(date) FROM PUBLIC;"]
    for runtime in ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_mcp", "verdify_dashboard"):
        assert runtime not in BODY, runtime
    assert "SECURITY DEFINER" not in BODY
    assert BODY.count("SECURITY INVOKER") == 2  # the new function and the replaced store
    assert re.findall(r"CREATE (?:OR REPLACE )?FUNCTION (\S+)\(", BODY) == [
        "public.fn_band_dryout_night",
        "public.fn_band_compliance_store",
    ]
    for statement in (
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE OR REPLACE VIEW",
        "DROP ",
        "DELETE",
        "INSERT INTO public.daily_summary",
    ):
        assert statement not in BODY, statement


def test_the_window_is_00_to_06_local_and_the_floor_is_the_firmwares():
    fn = BODY.split("AS $dryout$", 1)[1].split("$dryout$;", 1)[0]
    assert "p_day::timestamp AT TIME ZONE 'America/Denver' AS opens" in fn
    assert "(p_day::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver' AS closes" in fn
    assert "public.fn_band_compliance_5m(w.opens, w.closes)" in fn
    assert "g.vpd_kpa >= greatest(g.vpd_low, 0.74::double precision)" in fn
    assert fn.count("CASE WHEN now() >= w.closes") == 2  # null until the window has closed
    firmware = (ROOT / "firmware/lib/greenhouse_logic.h").read_text()
    assert "NIGHT_DRYOUT_MIN_FLOOR_KPA = 0.74f" in firmware
    assert "std::max(sp.vpd_low, NIGHT_DRYOUT_MIN_FLOOR_KPA)" in firmware


def test_the_flag_is_four_hours_and_the_minutes_are_bounded():
    assert "ADD COLUMN night_dried_out boolean GENERATED ALWAYS AS (night_dryout_min >= 240) STORED" in BODY
    check = " ".join(
        BODY.split("ADD CONSTRAINT daily_band_compliance_dryout_minutes CHECK (", 1)[1].split(";", 1)[0].split()
    )
    assert "(night_dryout_graded_min IS NULL) = (night_dryout_min IS NULL)" in check
    assert "night_dryout_graded_min BETWEEN 0 AND 420" in check  # 00:00-06:00 is 7 h on the fall-back day
    assert "night_dryout_min BETWEEN 0 AND night_dryout_graded_min" in check


def test_the_store_keeps_290s_body_and_adds_the_window():
    old = (ROOT / "db/migrations/290-band-compliance.sql").read_text().split("AS $store$", 1)[1].split("$store$;", 1)[0]
    new = SOURCE.split("AS $store$", 1)[1].split("$store$;", 1)[0]
    assert "CROSS JOIN public.fn_band_dryout_night(p_day) n" in new
    assert "n.graded_min, n.floor_min, now()" in new
    assert (
        "night_dryout_graded_min = EXCLUDED.night_dryout_graded_min, night_dryout_min = EXCLUDED.night_dryout_min"
        in new
    )
    # Everything else, the daily_summary copy included, is 290's text.
    assert new.split("RETURNING t.* INTO stored;", 1)[1] == old.split("RETURNING t.* INTO stored;", 1)[1]
    assert new.split("INSERT INTO", 1)[0] == old.split("INSERT INTO", 1)[0]


def test_the_rows_already_stored_are_refilled_from_the_function():
    refill = " ".join(BODY.split("UPDATE public.daily_band_compliance b", 1)[1].split(";", 1)[0].split())
    assert refill == (
        "SET (night_dryout_graded_min, night_dryout_min) = "
        "(SELECT n.graded_min, n.floor_min FROM public.fn_band_dryout_night(b.date) n)"
    )


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_runs_the_cronjob_call_and_checks_the_dst_window():
    text = NATIVE.read_text()
    qualifier.native_checks(NATIVE)
    assert text.index("SET SESSION AUTHORIZATION verdify_matview_refresh_runtime_login;") < text.index(
        "verdify_matview_refresh_runtime.fn_refresh_band_compliance()"
    )
    assert text.count("RESET SESSION AUTHORIZATION;") == 1
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")
    assert "date = DATE '2026-11-01' AND night_dryout_graded_min = 420" in text


def assert_recorded(evidence):
    """What the recorded native qualification must show before 305 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "305 native qualification pending: with production at its predecessor (or --apply-first each merged one it "
        "lacks), run scripts/qualify-migration-successor.py db/migrations/305-band-nightly-dryout.sql --native-fixture "
        "db/qualification/305-band-nightly-dryout-fixture.sql --fixture "
        "tests/fixtures/305-band-nightly-dryout-native-qualification.json on production (every session rolls back) "
        "and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_305
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_305_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_305 == registered(305) == registered(PREDECESSOR)
    if CHAIN:  # the merged predecessors production lacked were applied first, in the same sessions
        assert "applied_first" in evidence
    assert measured_against(evidence, CHAIN) == registered(PREDECESSOR)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"matview_refresh": True}


def fake_qualification(monkeypatch, *, inside=None, native=None):
    """Replay the qualifier's three sessions for 305, without a database, on production at its predecessor."""
    seals = registered(305)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"matview_refresh": True}}
    replies = {
        "probe": {"before": state(LIVE), "successor": state(PREDECESSOR)},
        "final": {"inside": state(305) if inside is None else inside, "native_fixture": native or fixture},
        "after": {"after": state(LIVE)},
    }
    if CHAIN:
        replies["probe"]["applied"] = replies["final"]["applied"] = state(CHAIN[-1])
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_305_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=FIRST, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=FIRST, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_305
    assert evidence["projected_successor"] == six.SUCCESSOR_305 == qualifier.successor_literals(SOURCE)
    assert measured_against(evidence, CHAIN) == registered(PREDECESSOR)


def measured_against(evidence, chain):
    """The seals the successor ran on: its predecessor's after `chain` was applied in-session, or the live ones."""
    if "applied_first" in evidence:
        assert evidence["applied_first"][len(evidence["applied_first"]) - len(chain) :] == [
            {"migration": getattr(c0, f"SUCCESSOR_{n}"), "seq": n, "sha256": getattr(c0, f"SUCCESSOR_{n}_SHA256")}
            for n in chain
        ]
        return evidence["applied_first_seals"]
    return evidence["before"]


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact305 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
