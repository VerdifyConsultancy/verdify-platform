"""Exact306 delivery: ordered admission, the SQL mirror of the achievable VPD envelope, unchanged seals, native evidence.

#1433 (Jason, 2026-11-07): build the SQL mirror of verdify_schemas.vpd_envelope now. 306 adds nine pure
fn_vpd_envelope_* functions (round, the Tetens pair, the outdoor dewpoint, is_night, the fit, the ribbon, the
achievable band and the grade). They are verdify's with PUBLIC's EXECUTE revoked and touch no table, view or existing
function, so every seal keeps 305's value. tests/test_dashboard_db_vpd_envelope_306.py proves the functions equal the
Python bit for bit on a private database. test_native_qualification_is_recorded is the merge gate for the rolled-back
production run, which fits production's own training rows; test_production_fit_equals_python then requires
production's database to return the Python's bits on those rows.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from verdify_schemas import vpd_envelope as ve
from verdify_services import vpd_envelope as service

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = 305
# Merged predecessors not yet applied in production when 306 is qualified (each one --apply-first).
CHAIN = ()
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("vpd_envelope306_delivery", "c0-migration-delivery.py")
six = load("vpd_envelope306_six", "bootstrap-six-runtime-roles.py")
maintenance = load("vpd_envelope306_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("vpd_envelope306_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_306
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/306-vpd-envelope-sql-mirror-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/306-vpd-envelope-sql-mirror-native-qualification.json"
NUMBERS = [*range(255, 292), 293, 294, 296, 301, 302, 303, 304, *CHAIN, 305, 306]
FUNCTIONS = [
    "public.fn_vpd_envelope_round",
    "public.fn_vpd_envelope_es_kpa",
    "public.fn_vpd_envelope_dewpoint_f",
    "public.fn_vpd_envelope_outdoor_dewpoint_f",
    "public.fn_vpd_envelope_is_night",
    "public.fn_vpd_envelope_fit",
    "public.fn_vpd_envelope_expected",
    "public.fn_vpd_envelope_achievable_band",
    "public.fn_vpd_envelope_grade",
]


# Production's newest applied migration when the successor is qualified, and the files applied first.
LIVE = 305
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
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_306_SHA256 == six.VPD_ENVELOPE_MIGRATION[2]
    assert six.VPD_ENVELOPE_MIGRATION[:2] == (306, c0.SUCCESSOR_306) == (306, "306-vpd-envelope-sql-mirror.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="306 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_306: "0" * 64})
    rows = later()
    for invalid in (rows + ["399-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["306-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_306 == predecessor == registered(PREDECESSOR) == registered(306)
    assert c0.SUCCESSOR_306_DIGESTS == {API: six.SUCCESSOR_306["api"], INGESTOR: six.SUCCESSOR_306["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(306, six.SUCCESSOR_306), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.VPD_ENVELOPE_MIGRATION) + ")"
    assert six.sealed_sql_306() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=306" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.VPD_ENVELOPE_MIGRATION)
    # 306 follows its predecessor in both ORs.
    assert six.sealed_sql().count(" OR " + getattr(six, f"sealed_sql_{PREDECESSOR}")() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_306()) == 1
    assert '"306-vpd-envelope-sql-mirror.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_306_vpd_envelope_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    for digest in registered(306).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_306
    assert "'306 refuses an unqualified predecessor'" in SOURCE and "'306 changed a runtime boundary'" in SOURCE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


# ── The body: nine new pure functions and nothing else ──────────────────────


def test_the_body_creates_nine_functions_and_only_revokes_public():
    assert re.findall(r"CREATE FUNCTION (\S+)\(", BODY) == FUNCTIONS
    assert "GRANT" not in BODY and "SECURITY DEFINER" not in BODY and "OR REPLACE" not in BODY
    revokes = [" ".join(r.split()) for r in re.findall(r"\bREVOKE\b.*?;", BODY, re.S)]
    assert [r.split("FUNCTION ", 1)[1].split("(", 1)[0] for r in revokes] == FUNCTIONS
    assert all(r.startswith("REVOKE ALL ON FUNCTION ") and r.endswith(" FROM PUBLIC;") for r in revokes)
    assert BODY.count("IMMUTABLE") == len(FUNCTIONS)
    for runtime in ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_mcp", "verdify_dashboard"):
        assert runtime not in BODY, runtime
    for statement in ("CREATE TABLE", "CREATE VIEW", "ALTER ", "DROP ", "DELETE", "INSERT INTO", "UPDATE public"):
        assert statement not in BODY, statement


def test_the_body_carries_the_python_constants():
    assert ve.ENVELOPE_MIN_HOURS == 24 and "IF n < 24 THEN" in BODY
    assert ve.P25_Z == 0.674 and "0.674::double precision * p_rmse_f" in BODY
    assert ve.NIGHT_PHASE == 2.0 and "p_phase >= 2::double precision" in BODY
    assert "abs(m[piv][col]) < 1e-9::double precision" in BODY  # _solve's singular threshold
    assert "ceil(0.1::double precision * n::double precision)" in BODY  # the p10 index
    for constant in ("0.6108::double precision", "17.27::double precision", "237.3::double precision"):
        assert constant in BODY, constant


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_selects_the_training_rows_as_api06_does():
    text = NATIVE.read_text()
    qualifier.native_checks(NATIVE)
    query = text.split("CREATE TEMP TABLE envelope_training ON COMMIT DROP AS\n", 1)[1].split(";\n", 1)[0]
    api = service.ENVELOPE_RANGE_SQL
    for param, literal in (
        ("$1::timestamptz", "TIMESTAMPTZ '2026-10-24 19:00:00+00'"),
        ("$2::timestamptz", "TIMESTAMPTZ '2026-11-07 19:00:00+00'"),
        ("$3::text[]", "ARRAY[" + ", ".join(f"'{name}'" for name in ve.MOISTURE_EQUIPMENT) + "]"),
        ("$4", "'vallery'"),
        ("$5", str(ve.DEEP_NIGHT_ALTITUDE_DEG)),
    ):
        api = api.replace(param, literal)

    def words(sql):
        return " ".join(re.sub(r"::double precision AS \w+", "", sql).split())

    # The same statement with the parameters bound. The fixture casts each average to float8 (the climate columns
    # already are) and leaves the order to the fit's array_agg(... ORDER BY b).
    assert words(api) == words(query) + " ORDER BY b"
    assert "fn_vpd_envelope_fit(t.t_in, t.td_in, t.t_out, t.rh_out)" in text
    assert "array_agg(t_in ORDER BY b)" in text and "SET SESSION AUTHORIZATION" not in text


def assert_recorded(evidence):
    """What the recorded native qualification must show before 306 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "306 native qualification pending: with production at its predecessor (or --apply-first each merged one it "
        "lacks), run scripts/qualify-migration-successor.py db/migrations/306-vpd-envelope-sql-mirror.sql "
        "--native-fixture db/qualification/306-vpd-envelope-sql-mirror-fixture.sql --fixture "
        "tests/fixtures/306-vpd-envelope-sql-mirror-native-qualification.json on production (every session rolls "
        "back) and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_306
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_306_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_306 == registered(306) == registered(PREDECESSOR)
    if CHAIN:  # the merged predecessors production lacked were applied first, in the same sessions
        assert "applied_first" in evidence
    assert measured_against(evidence, CHAIN) == registered(PREDECESSOR)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["fit"] is not None and len(native["training"]) == native["fit"]["hours"]
    assert len(native["points"]) == 12


def fake_qualification(monkeypatch, *, inside=None, native=None):
    """Replay the qualifier's three sessions for 306, without a database, on production at its predecessor."""
    seals = registered(306)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    training = [[60.0 + i % 7, 45.0 + i % 5, 30.0 + i % 11, 40.0 + i % 23] for i in range(30)]
    fixture = {
        "all_passed": True,
        "training": training,
        "fit": {"coef": [1.0, 0.5, 0.1, 0.2], "rmse_f": 1.0, "hours": 30, "dewpoint_p10_f": 45.0},
        "points": [[58.0, 30.0, 20.0, 0.4, 0.5, 0.4, 1.17]] * 12,
    }
    replies = {
        "probe": {"before": state(LIVE), "successor": state(PREDECESSOR)},
        "final": {"inside": state(306) if inside is None else inside, "native_fixture": native or fixture},
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


def test_qualifier_records_306_and_the_gate_accepts_it(monkeypatch, tmp_path):
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
    assert evidence["migration"] == c0.SUCCESSOR_306
    assert evidence["projected_successor"] == six.SUCCESSOR_306 == qualifier.successor_literals(SOURCE)
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


def test_production_fit_equals_python():
    """Production's database (its own libm) returns the Python's bits on its own training rows."""
    native = json.loads(FIXTURE.read_text()).get("native_fixture")
    if native is None:
        pytest.skip("306 native qualification not recorded yet")
    rows = [dict(zip(("t_in", "td_in", "t_out", "rh_out"), row, strict=True)) for row in native["training"]]
    fit = ve.fit_dewpoint(rows)
    recorded = native["fit"]
    assert fit == ve.DewpointFit(
        tuple(recorded["coef"]), recorded["rmse_f"], recorded["hours"], recorded["dewpoint_p10_f"]
    )
    for temp_f, t_out, td_out, p25, p75, low, high in native["points"]:
        assert ve.expected_vpd(fit, temp_f, t_out, td_out) == (p25, p75)
        assert ve.achievable_band(0.74, 1.17, (p25, p75)) == (low, high)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact306 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
