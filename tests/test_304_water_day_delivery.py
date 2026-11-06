"""Exact304 delivery: ordered admission, the local day end, unchanged seals, native evidence.

#1283: v_water_meter_daily measured a day's end as ``day + interval '1 day'``, 24 hours in production's UTC sessions,
so the 25-hour fall-back day could read complete with its last hour missing and the 23-hour day never read complete.
304 replaces the view with the same columns, owner and ACL and ends each day at the next America/Denver midnight.
Nothing a runtime login, an MCP role or PUBLIC reaches changes, so every seal keeps its predecessor's value and no
receipt is re-sealed. tests/test_dashboard_db_water_day_304.py proves the fix on a private database (a 25-hour day
with its last hour missing, a complete 25-hour day, a complete 23-hour day). test_native_qualification_is_recorded is
the merge gate for the rolled-back production run that measures the seals and runs
db/qualification/304-water-day-local-end-fixture.sql.
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
PREDECESSOR = 303
# Merged predecessors not yet applied in production when 304 is qualified (each one --apply-first).
CHAIN = (303,)
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("water_day304_delivery", "c0-migration-delivery.py")
six = load("water_day304_six", "bootstrap-six-runtime-roles.py")
maintenance = load("water_day304_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("water_day304_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_304
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/304-water-day-local-end-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/304-water-day-local-end-native-qualification.json"
NUMBERS = [*range(255, 292), 293, 294, 296, 301, 302, *CHAIN, 304]


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
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_304_SHA256 == six.WATER_DAY_MIGRATION[2]
    assert six.WATER_DAY_MIGRATION[:2] == (304, c0.SUCCESSOR_304) == (304, "304-water-day-local-end.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="304 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_304: "0" * 64})
    rows = later()
    for invalid in (rows + ["399-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["304-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_304 == predecessor == registered(PREDECESSOR) == registered(304)
    assert c0.SUCCESSOR_304_DIGESTS == {API: six.SUCCESSOR_304["api"], INGESTOR: six.SUCCESSOR_304["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(304, six.SUCCESSOR_304), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.WATER_DAY_MIGRATION) + ")"
    assert six.sealed_sql_304() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=304" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.WATER_DAY_MIGRATION)
    # 304 follows its predecessor in both ORs.
    assert six.sealed_sql().count(" OR " + getattr(six, f"sealed_sql_{PREDECESSOR}")() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_304()) == 1
    assert '"304-water-day-local-end.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_304_water_day_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    for digest in registered(304).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_304
    assert "'304 refuses an unqualified predecessor'" in SOURCE and "'304 changed a runtime boundary'" in SOURCE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


# ── The body: one view, the local day end ───────────────────────────────────


def test_the_body_only_replaces_the_view_and_grants_nothing():
    assert re.findall(r"CREATE OR REPLACE VIEW (\S+) AS", BODY) == ["public.v_water_meter_daily"]
    for statement in ("GRANT", "REVOKE", "CREATE TABLE", "CREATE FUNCTION", "ALTER ", "INSERT", "UPDATE ", "DELETE"):
        assert statement not in BODY, statement


def test_the_day_ends_at_the_next_local_midnight_and_never_at_a_fixed_day():
    view = BODY.split("CREATE OR REPLACE VIEW public.v_water_meter_daily AS", 1)[1].split("COMMENT ON", 1)[0]
    assert "interval '1 day'" not in view
    assert (
        "((c.day AT TIME ZONE 'America/Denver')::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end" in view
    )
    assert view.count("c.day_end - interval '10 minutes'") == 3  # is_complete_day, quality, available_for_scoring
    # The local date is read in America/Denver too, never through the session's time zone.
    assert "c.day::date" not in view
    assert view.count("c.local_day < (now() AT TIME ZONE 'America/Denver')::date") == 2
    assert "WHEN c.local_day = (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'" in view


def test_the_view_keeps_its_columns_in_order():
    view = BODY.split("CREATE OR REPLACE VIEW public.v_water_meter_daily AS", 1)[1].split("COMMENT ON", 1)[0]
    final = view.rsplit("\n)\nSELECT", 1)[1].split("\nFROM bounded c", 1)[0]
    names = re.findall(r"(?:AS (\w+),?$)|(?:^\s+c\.(\w+),?$)", final, re.M)
    assert [a or b for a, b in names] == [
        "day",
        "greenhouse_id",
        "meter_id",
        "used_gal",
        "delta_events",
        "reset_events",
        "phantom_zero_events",
        "quality_events",
        "gap_events",
        "raw_sample_count",
        "first_raw_ts",
        "last_raw_ts",
        "max_raw_gap_seconds",
        "missing_interval_events",
        "is_complete_day",
        "quality",
        "available_for_scoring",
        "materialized_through_ts",
        "ledger_covers_day",
    ]


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_reads_in_two_time_zones_and_as_the_dashboard_login():
    text = NATIVE.read_text()
    qualifier.native_checks(NATIVE)
    assert "SET LOCAL TimeZone = 'UTC';" in text and "SET LOCAL TimeZone = 'Pacific/Kiritimati';" in text
    assert "SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;" in text
    assert text.count("RESET SESSION AUTHORIZATION;") == 1
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")
    assert "(DATE '2026-11-01', true, 'discontinuous')" in text


def assert_recorded(evidence):
    """What the recorded native qualification must show before 304 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "304 native qualification pending: with production at its predecessor (or --apply-first each merged one it "
        "lacks), run scripts/qualify-migration-successor.py db/migrations/304-water-day-local-end.sql --native-fixture "
        "db/qualification/304-water-day-local-end-fixture.sql --fixture "
        "tests/fixtures/304-water-day-local-end-native-qualification.json on production (every session rolls back) "
        "and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_304
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_304_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_304 == registered(304) == registered(PREDECESSOR)
    if CHAIN:  # the merged predecessors production lacked were applied first, in the same sessions
        assert "applied_first" in evidence
    assert measured_against(evidence, CHAIN) == registered(PREDECESSOR)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"dashboard": True}


def fake_qualification(monkeypatch, *, inside=None, native=None):
    """Replay the qualifier's three sessions for 304, without a database, on production at its predecessor."""
    seals = registered(304)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"dashboard": True}}
    replies = {
        "probe": {"before": state(LIVE), "successor": state(PREDECESSOR)},
        "final": {"inside": state(304) if inside is None else inside, "native_fixture": native or fixture},
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


def test_qualifier_records_304_and_the_gate_accepts_it(monkeypatch, tmp_path):
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
    assert evidence["migration"] == c0.SUCCESSOR_304
    assert evidence["projected_successor"] == six.SUCCESSOR_304 == qualifier.successor_literals(SOURCE)
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
    """Merge gate: the rolled-back production qualification of exact304 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
