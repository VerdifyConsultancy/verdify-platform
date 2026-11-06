"""Exact301 delivery: ordered admission, the snapshot table, the moved MCP seal, the pending native gate.

#1346: CronJob verdify-greenhouse-brief writes the greenhouse snapshot to greenhouse_brief as the dashboard login,
the operator API serves it in API-02 and MCP's get_greenhouse_snapshot reads it. Neither ordinary login reaches the
table, so the api and ingestor seals keep 290's values; the MCP duty's SELECT moves the MCP seal and its receipt is
re-sealed (the 283 pattern). 301 is chained after 290 while 291-300 are open; it is re-chained onto 300 and natively
qualified before it merges. test_native_qualification_is_recorded is that merge gate: a strict xfail while the
fixture says the qualification is pending, and a hard gate once it is recorded.
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
HEX = "([0-9a-f]{64})"
PREDECESSOR = 290
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
DASHBOARD, MCP = "verdify_dashboard_runtime", "verdify_mcp_runtime"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("brief301_delivery", "c0-migration-delivery.py")
six = load("brief301_six", "bootstrap-six-runtime-roles.py")
maintenance = load("brief301_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("brief301_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_301
SQL = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/301-greenhouse-brief-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/301-greenhouse-brief-native-qualification.json"
EVIDENCE = json.loads(FIXTURE.read_text())


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]


def later(with_301=True):
    rows = [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 291)]
    return rows + [c0.SUCCESSOR_301] if with_301 else rows


def files(with_301=True):
    found = {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 291)}
    if with_301:
        found[c0.SUCCESSOR_301] = c0.SUCCESSOR_301_SHA256
    return found


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == c0.SUCCESSOR_301_SHA256
    assert six.GREENHOUSE_BRIEF_MIGRATION == (301, c0.SUCCESSOR_301, c0.SUCCESSOR_301_SHA256)
    assert c0.SUCCESSOR_301 == "301-greenhouse-brief.sql"
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(False), files(False))
    with pytest.raises(c0.DeliveryError, match="reviewed 301 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_301: "0" * 64})
    rows = later()
    for invalid in (rows + ["302-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["301-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SQL, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper(), name


def test_predecessor_binds_exact290_with_its_registered_seals():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_290, c0.SUCCESSOR_290_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "to_regclass('public.greenhouse_brief') IS NOT NULL" in PREDECESSOR_BLOCK
    assert "'301 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_290[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal


def test_successor_moves_only_the_mcp_seal_and_reseals_its_receipt():
    literals = qualifier.successor_literals(SQL)
    assert literals == registered(301) == six.SUCCESSOR_301
    before = registered(PREDECESSOR)
    assert literals["api"] == before["api"] and literals["ingestor"] == before["ingestor"]
    assert literals["mcp"] != before["mcp"]
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in SQL
    assert SUCCESSOR_BLOCK.count("UPDATE public.mcp_runtime_boundary_receipt") == 1
    assert f"decode('{literals['mcp']}','hex') WHERE singleton;" in SUCCESSOR_BLOCK
    assert "'301 refuses an unqualified successor'" in SUCCESSOR_BLOCK


def test_body_grants_the_dashboard_writer_and_the_mcp_reader_only():
    grants = [" ".join(g.split()) for g in re.findall(r"\bGRANT\b.*?;", BODY, re.S)]
    assert grants == [
        f"GRANT SELECT, INSERT, DELETE ON public.greenhouse_brief TO {DASHBOARD};",
        f"GRANT UPDATE (checked_at, outdated_at) ON public.greenhouse_brief TO {DASHBOARD};",
        f"GRANT SELECT ON public.greenhouse_brief TO {MCP};",
    ]
    assert not re.search(r"\bTO PUBLIC\b|DEFAULT PRIVILEGES|\bOWNER TO\b|\bDROP\b|\bTRUNCATE\b|\bREVOKE\b", BODY)
    for role in ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_planner_runtime"):
        assert role not in BODY, role
    assert "GENERATED ALWAYS AS IDENTITY" in BODY  # no sequence grant needed
    assert "CHECK (status IN ('ok', 'rejected', 'error'))" in BODY


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_301_chains_from_290_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_290)
    expected = "(" + six.sealed_sql_290().replace(old, six.seal_profile_sql(301, six.SUCCESSOR_301), 1)
    expected += " AND " + six.ledger_row_sql(*six.GREENHOUSE_BRIEF_MIGRATION) + ")"
    assert six.sealed_sql_301() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=301" in expected
    assert six.sealed_sql().count(" OR " + six.sealed_sql_290() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_301()) == 1
    assert '"301-greenhouse-brief.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_301_greenhouse_brief_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def states(seals):
    ordinary = dict(
        api=seals["api"],
        api_receipt=seals["api"],
        ingestor=seals["ingestor"],
        ingestor_receipt=seals["ingestor"],
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
        mcp=seals["mcp"],
        mcp_receipt=seals["mcp"],
        mcp_login=True,
        mcp_arm_read=False,
        mcp_experiment_read=False,
        **exact,
    )


def verify(monkeypatch, seals, with_301=True):
    ordinary, mcp_state = states(seals)
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(with_301)
    )


def test_post_249_readback_expects_the_moved_mcp_seal_with_301_stamped(monkeypatch):
    verify(monkeypatch, registered(301))
    verify(monkeypatch, registered(PREDECESSOR), with_301=False)
    with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
        verify(monkeypatch, registered(PREDECESSOR))  # 301 stamped but the MCP seal still 290's
    for seal in SEALS:
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, {**registered(301), seal: "f" * 64})


# ── The native qualification ────────────────────────────────────────────────


def test_native_fixture_exercises_the_writer_and_reports_once():
    checks = qualifier.native_checks(NATIVE)
    assert "SET SESSION AUTHORIZATION verdify_dashboard_runtime_login;" in checks
    assert "verdify_dashboard_runtime.fn_attest_login() IS NOT TRUE" in checks
    assert "WHEN insufficient_privilege" in checks
    assert checks.count("RESET SESSION AUTHORIZATION;") == 1
    assert "'all_passed', true" in checks


def fake_qualification(monkeypatch, native=None):
    seen = []
    before, after = registered(PREDECESSOR), registered(301)

    def state(seals):
        return {**seals, **{f"{seal}_receipt": value for seal, value in seals.items()}, "max_seq": 290}

    def run(psql, sql):
        label = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(label)
        if label == "probe":
            return {"before": state(before), "successor": state(after)}
        if label == "final":
            return {"inside": state(after), "native_fixture": native or {"all_passed": True}}
        return {"after": state(before)}

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_301_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == EVIDENCE["projection"]
    assert written["final_migration_qualification_pending"] is False
    assert written["qualified_successor"] == registered(301)
    assert written["before"] == written["after_rollback"] == registered(PREDECESSOR)


def test_the_projection_is_the_files_literals():
    assert EVIDENCE["migration"] == c0.SUCCESSOR_301
    assert EVIDENCE["exact_final_migration_sha256"] == c0.SUCCESSOR_301_SHA256
    assert EVIDENCE["projected_successor"] == six.SUCCESSOR_301 == qualifier.successor_literals(SQL)
    assert EVIDENCE["before"] == registered(PREDECESSOR)


@pytest.mark.xfail(
    EVIDENCE.get("final_migration_qualification_pending", True),
    reason="DO NOT MERGE: 301 must be re-chained onto 300 and natively qualified on production first",
    strict=True,
)
def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact301 and its moved MCP seal."""
    assert EVIDENCE["final_migration_qualification_pending"] is False, "301 native qualification pending"
    assert EVIDENCE["qualified_successor"] == six.SUCCESSOR_301 == registered(301)
    assert EVIDENCE["before"] == EVIDENCE["after_rollback"] == registered(PREDECESSOR)
    assert EVIDENCE["final_migration_rollback_verified"] is True
    assert EVIDENCE["inside_transaction_receipts_match"] is True
    assert EVIDENCE["native_fixture"]["all_passed"] is True
