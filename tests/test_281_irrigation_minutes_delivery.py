"""Exact281 (VD-39, M4) delivery: ordered admission, seals, grants and native evidence.

281 is measured against its merged predecessor 280 (VD-30). Production is still at
279, so the rolled-back native qualification applies 280 first in the same
sessions (``--apply-first``) and runs 281's native fixture at the end of the final
session (``--native-fixture``). Until that evidence is recorded the successor seals
are a read-only projection and test_native_qualification_is_recorded fails: the
merge gate.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/281-irrigation-minutes-native-qualification.json"
NATIVE = ROOT / "db/qualification/281-irrigation-minutes-fixture.sql"
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
SEALS = ("api", "ingestor", "mcp")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("minutes281_delivery", "c0-migration-delivery.py")
six = load("minutes281_six", "bootstrap-six-runtime-roles.py")
maintenance = load("minutes281_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("minutes281_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_281
PREDECESSOR = ROOT / "db/migrations" / c0.SUCCESSOR_280
SQL = MIGRATION.read_text()


def later(last=281):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def reviewed(last=281):
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, last + 1)}


def receipt():
    return json.loads(FIXTURE.read_text())


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_281_SHA256 == six.IRRIGATION_MINUTES_MIGRATION[2]
    assert six.IRRIGATION_MINUTES_MIGRATION[:2] == (281, c0.SUCCESSOR_281)
    c0.reviewed_post_254(later(), reviewed())
    c0.reviewed_post_254(later(280), reviewed(280))
    with pytest.raises(c0.DeliveryError, match="281 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_281: "0" * 64})
    for invalid in [later() + ["282-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["281-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"281 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 281 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_281: changed})


def test_file_is_additive_read_only_and_wrap_safe():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    body = SQL[SQL.index("END $predecessor$;") : SQL.index("DO $successor$")]
    statements = re.findall(
        r"^(CREATE|COMMENT|REVOKE|GRANT|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b[^;]*", body, re.M
    )
    assert sorted({s.split()[0] for s in statements}) == ["COMMENT", "CREATE", "GRANT", "REVOKE"]
    created = re.findall(r"^CREATE (\w+) public\.(\w+)", body, re.M)
    assert created == [
        ("VIEW", "v_irrigation_day_coverage"),
        ("VIEW", "v_irrigation_minutes_daily"),
        ("FUNCTION", "fn_irrigation_minutes_daily"),
    ]
    grants = [" ".join(g.split()) for g in re.findall(r"^GRANT [^;]*", body, re.M)]
    assert grants == [
        "GRANT SELECT ON public.v_irrigation_day_coverage, public.v_irrigation_minutes_daily "
        "TO verdify_mcp_runtime, verdify_ingestor_runtime",
        "GRANT EXECUTE ON FUNCTION public.fn_irrigation_minutes_daily(date, date) "
        "TO verdify_mcp_runtime, verdify_ingestor_runtime",
    ]
    assert "verdify_api_runtime" not in body and "verdify_dashboard_runtime" not in body
    for view in ("v_water_relay_runs", "v_water_run_accounting", "v_water_event_attribution"):
        target = r"(public\.)?" + view + r"\b"
        assert not re.search(r"(CREATE|ALTER|DROP)(\s+OR\s+REPLACE)?\s+VIEW\s+(IF\s+EXISTS\s+)?" + target, body), view
        assert not re.search(r"COMMENT\s+ON\s+VIEW\s+" + target, body), view
        assert not re.search(r"(GRANT|REVOKE)[^;]*\bON\b[^;]*" + target, body), view
    for forbidden in ("CREATE TABLE", "CREATE TRIGGER", "CREATE INDEX", "COMMIT", "CONCURRENTLY", "VACUUM"):
        assert forbidden not in SQL, forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr


def test_predecessor_and_successor_literals_bind_280_and_the_registration():
    head, successor = qualifier.split_successor(SQL)
    predecessor = head.split("END $predecessor$;")[0]
    assert f"seq=280 AND filename='db/migrations/{c0.SUCCESSOR_280}'" in predecessor
    assert f"sha256='{c0.SUCCESSOR_280_SHA256}'" in predecessor and "stamp_method='runner'" in predecessor
    assert "seq>=281" in predecessor and "'281 refuses an unqualified predecessor'" in predecessor
    for digest in registered(280).values():
        assert predecessor.count(digest) == 2  # the live digest and its receipt
    expected = registered(281)
    assert qualifier.successor_literals(SQL) == expected == six.SUCCESSOR_281
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{expected[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in successor
    assert f"mcp_runtime_boundary_receipt SET boundary_sha256=decode('{expected['mcp']}','hex') WHERE singleton" in (
        successor
    )
    assert successor.count("IF NOT FOUND THEN RAISE EXCEPTION") == 3
    assert "0" * 64 not in SQL and "PENDING" not in SQL


def test_registration_binds_the_recorded_successor_and_moves_all_three_seals():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_281
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_281_SHA256
    successor = evidence["qualified_successor"] or evidence["projected_successor"]
    assert successor == six.SUCCESSOR_281 == registered(281)
    assert all(successor[seal] != registered(280)[seal] for seal in SEALS)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()


def test_sealed_sql_281_chains_from_280_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(280, six.SUCCESSOR_280)
    expected = "(" + six.sealed_sql_280().replace(old, six.seal_profile_sql(281, six.SUCCESSOR_281), 1)
    expected += " AND " + six.ledger_row_sql(*six.IRRIGATION_MINUTES_MIGRATION) + ")"
    assert six.sealed_sql_281() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.IRRIGATION_MINUTES_MIGRATION)
    assert six.sealed_sql().endswith(" OR " + six.sealed_sql_280() + " OR " + expected + ")")
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_281() + ")") == 1
    assert '"281-irrigation-minutes-by-zone.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_281_irrigation_minutes_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def states(api, ingestor, mcp):
    ordinary = dict(
        api=api,
        api_receipt=api,
        ingestor=ingestor,
        ingestor_receipt=ingestor,
        receipt_count=2,
        column_update=True,
        table_update=False,
    )
    exact = dict.fromkeys(
        (
            "outcome_scorecard",
            "outcome_scorecard_exec",
            "outcome_band_exec",
            "outcome_season_exec",
            "outcome_zone_exec",
            "outcome_anchor_read",
            "outcome_profile_read",
        ),
        True,
    )
    return ordinary, dict(
        mcp=mcp, mcp_receipt=mcp, mcp_login=True, mcp_arm_read=False, mcp_experiment_read=False, **exact
    )


def verify(monkeypatch, ordinary, mcp_state, last=281):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_the_281_seals_once_281_is_stamped(monkeypatch):
    seals = six.SUCCESSOR_281
    verify(monkeypatch, *states(seals["api"], seals["ingestor"], seals["mcp"]))
    old = six.SUCCESSOR_280
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=280)
    for mixed in (
        states(old["api"], seals["ingestor"], seals["mcp"]),
        states(seals["api"], old["ingestor"], seals["mcp"]),
        states(seals["api"], seals["ingestor"], old["mcp"]),
    ):
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *mixed)


def plan_281():
    record, block = qualifier.applied_first(PREDECESSOR)
    return record, qualifier.sessions(SQL, [block], qualifier.native_checks(NATIVE))


def test_qualification_applies_280_first_as_the_runner_then_measures_281():
    record, plan = plan_281()
    assert record == {"migration": c0.SUCCESSOR_280, "seq": 280, "sha256": c0.SUCCESSOR_280_SHA256}
    predecessor = PREDECESSOR.read_text()
    stamp = (
        f"SELECT public.stamp_migration(\n    'db/migrations/{c0.SUCCESSOR_280}', 'db/migrations', 280, "
        f"'{c0.SUCCESSOR_280_SHA256}', 'runner',"
    )
    head, _ = qualifier.split_successor(SQL)
    for name, sql in plan.items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name
    assert plan["after"].startswith("BEGIN READ ONLY;") and predecessor not in plan["after"]
    probe, final = plan["probe"], plan["final"]
    # The probe: live state, 280 exactly as the runner applies it, its state, then 281 up to its successor block.
    order = [
        probe.index("QUALIFY before"),
        probe.index(qualifier.RUNNER_LOCK),
        probe.index(predecessor),
        probe.index(stamp),
        probe.index("QUALIFY applied"),
        probe.index(head),
        probe.index("QUALIFY successor"),
    ]
    assert order == sorted(order)
    assert "281 refuses an unqualified successor" not in probe and "QUALIFY native_fixture" not in probe
    # The final session: 280 again, the exact 281 file, its receipts, then the native fixture.
    order = [
        final.index(predecessor),
        final.index(stamp),
        final.index("QUALIFY applied"),
        final.index(SQL),
        final.index("QUALIFY inside"),
        final.index(NATIVE.read_text()),
    ]
    assert order == sorted(order)
    for sql in (probe, final):
        # The bounds open the session and return after 280, before 281 runs.
        assert sql.count(qualifier.BOUNDS) == 2
        assert sql.index(stamp) < sql.rindex(qualifier.BOUNDS) < sql.index(head)


def test_native_fixture_attests_every_login_and_reads_as_granted():
    """The rolled-back qualification calls each login's attestor after the re-seal."""
    text = NATIVE.read_text()
    blocks = re.findall(
        r"SET SESSION AUTHORIZATION (\w+);\nDO \$(\w+)\$\n(.*?)\nEND \$\2\$;\nRESET SESSION AUTHORIZATION;", text, re.S
    )
    attestors = {login: body for login, _, body in blocks}
    assert set(attestors) == {f"verdify_{duty}_runtime_login" for duty in SEALS}
    for duty, attestor in (
        ("api", "fn_runtime_attest_ordinary_login"),
        ("ingestor", "fn_runtime_attest_ordinary_login"),
        ("mcp", "fn_mcp_runtime_attest_ordinary_login"),
    ):
        assert f"public.{attestor}() IS NOT TRUE THEN" in attestors[f"verdify_{duty}_runtime_login"], duty
    api = attestors["verdify_api_runtime_login"]
    for target in ("v_irrigation_minutes_daily", "v_irrigation_day_coverage", "fn_irrigation_minutes_daily"):
        assert re.search(r"PERFORM 1 FROM public\." + target + r"\b.*?WHEN insufficient_privilege", api, re.S), target
    for duty in ("ingestor", "mcp"):
        reader = attestors[f"verdify_{duty}_runtime_login"]
        for target in ("fn_irrigation_minutes_daily", "v_irrigation_minutes_daily", "v_irrigation_day_coverage"):
            assert f"FROM public.{target}" in reader, (duty, target)
    # One result line for the qualifier, after the last login switch.
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")
    assert qualifier.native_checks(NATIVE) == text


def test_native_fixture_proves_the_five_minute_meter_bound():
    """Each run's meter scan stops at run_end + 5 min; the qualification proves no
    accepted delta is longer, so the gallons equal v_water_run_accounting's."""
    assert SQL.count("AND a.ts <= p.run_end + interval '5 minutes'") == 2  # the view and the reader
    assert "WHERE a.ts - a.prior_ts > interval '5 minutes')" in NATIVE.read_text()


def assert_recorded(evidence):
    """What the recorded native qualification must show before 281 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "281 native qualification pending: run scripts/qualify-migration-successor.py on production (rolled back) "
        "with --apply-first for 280 while production is at 279, and commit the fixture it writes before merging"
    )
    assert evidence["migration"] == c0.SUCCESSOR_281
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_281_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_281
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    if "applied_first" in evidence:
        assert evidence["applied_first"] == [
            {"migration": c0.SUCCESSOR_280, "seq": 280, "sha256": c0.SUCCESSOR_280_SHA256}
        ]
        measured_against = evidence["applied_first_seals"]
    else:
        measured_against = evidence["before"]
    assert measured_against == registered(280)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"api": True, "ingestor": True, "mcp": True}
    assert native["reader_400d_ms"] < 10_000


def fake_qualification(monkeypatch, *, native=None):
    """Replay the qualifier's three sessions for 281 with 280 applied first, without a database."""
    live, pred, new = six.SUCCESSOR_279, six.SUCCESSOR_280, six.SUCCESSOR_281

    def state(seals, receipts, max_seq):
        return {**seals, **{seal + "_receipt": receipts[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": dict.fromkeys(SEALS, True), "reader_400d_ms": 1234}
    replies = {
        "probe": {
            "before": state(live, live, 279),
            "applied": state(pred, pred, 280),
            "successor": state(new, pred, 280),
        },
        "final": {
            "applied": state(pred, pred, 280),
            "inside": state(new, new, 280),
            "native_fixture": fixture if native is None else native,
        },
        "after": {"after": state(live, live, 279)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_281_measured_against_applied_280(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=[PREDECESSOR], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == six.SUCCESSOR_281  # the projection stays beside the measurement
    assert written["before"] == six.SUCCESSOR_279
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=[PREDECESSOR], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification must be recorded first."""
    assert_recorded(receipt())
