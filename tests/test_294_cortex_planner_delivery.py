"""Exact294 delivery: ordered admission, the planner storage, seal preservation, the pending native gate.

#1350/#1366 (Jason, 2026-11-05): the Cortex planner's packs and attempts, a retained weather_forecast
archive with its daily job, the planner duty's views and owner-run functions, and the heater roles. Nothing
294 adds carries an api, ingestor, MCP or PUBLIC privilege, so all three seals keep exact293's values and no
receipt is re-sealed (the 288 pattern). 294 chains from 293 (292 is a gap) and ships with 296 in one PR; until
the rolled-back native qualification measures them, the seals are 293's stand-ins. It is natively qualified
before it merges. test_native_qualification_is_recorded is that merge gate: it is a
strict xfail while the fixture says the qualification is pending, and a hard gate once it is recorded.
"""

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEX = "([0-9a-f]{64})"
PREDECESSOR = 293
# 292 is a gap: 293 follows 291 in the reviewed order.
NUMBERS = (*range(255, 292), 293)
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
PLANNER = "verdify_planner_runtime"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("cortex294_delivery", "c0-migration-delivery.py")
six = load("cortex294_six", "bootstrap-six-runtime-roles.py")
maintenance = load("cortex294_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("cortex294_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_294
SQL = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/294-cortex-planner-shadow-storage-native-qualification.json"
EVIDENCE = json.loads(FIXTURE.read_text())


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]


def later(with_294=True):
    rows = [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in NUMBERS]
    return rows + [c0.SUCCESSOR_294] if with_294 else rows


def files(with_294=True):
    found = {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in NUMBERS}
    if with_294:
        found[c0.SUCCESSOR_294] = c0.SUCCESSOR_294_SHA256
    return found


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == c0.SUCCESSOR_294_SHA256
    assert six.CORTEX_PLANNER_MIGRATION == (294, c0.SUCCESSOR_294, c0.SUCCESSOR_294_SHA256)
    assert c0.SUCCESSOR_294 == "294-cortex-planner-shadow-storage.sql"
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(False), files(False))
    with pytest.raises(c0.DeliveryError, match="reviewed 294 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_294: "0" * 64})
    rows = later()
    for invalid in (rows + ["295-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["294-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr
    for name, sql in qualifier.sessions(SQL).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper(), name


def test_predecessor_binds_exact293_with_its_registered_seals():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_293, c0.SUCCESSOR_293_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "to_regclass('public.planner_pack') IS NOT NULL" in PREDECESSOR_BLOCK
    assert "(SELECT count(*) FROM public.planner_inference_runs)<>0" in PREDECESSOR_BLOCK
    assert "'294 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_293[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal


def test_successor_keeps_every_seal_and_reseals_no_receipt():
    literals = qualifier.successor_literals(SQL)
    assert literals == registered(PREDECESSOR) == registered(294) == six.SUCCESSOR_294
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in SQL
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in SQL
    assert "'294 changed a runtime boundary'" in SUCCESSOR_BLOCK
    assert SUCCESSOR_BLOCK.count("notes LIKE '%(Jason, 2026-11-05)%')<>2") == 1


def test_body_grants_only_the_planner_duty_and_never_a_runtime_role():
    grants = re.findall(r"\bGRANT\s+(.*?)\s+TO\s+(\w+)\s*;", BODY, re.S)
    assert grants and {grantee for _, grantee in grants} == {PLANNER}
    assert not re.search(r"\bTO PUBLIC\b|DEFAULT PRIVILEGES|\bOWNER TO\b|\bDROP\b|\bTRUNCATE\b|\bDELETE\b", BODY)
    for role in ("verdify_api_runtime", "verdify_ingestor_runtime", "verdify_mcp"):
        assert role not in BODY, role
    # Every function and procedure is closed to PUBLIC; table writes are INSERT only, never UPDATE.
    created = re.findall(r"CREATE (?:FUNCTION|PROCEDURE) ([\w.]+)\(", BODY)
    assert len(created) == 5
    for name in created:
        assert re.search(rf"REVOKE ALL ON (?:FUNCTION|PROCEDURE) {re.escape(name)}\(.*?\) FROM PUBLIC;", BODY), name
    table_grants = [privileges for privileges, _ in grants if not privileges.startswith("EXECUTE")]
    assert all("UPDATE" not in privileges for privileges in table_grants)
    # Row updates: the planner_failed alert's dedup and resolve (owner-run functions) and the two heater notes.
    updates = re.findall(r"UPDATE public\.(\w+)", BODY)
    assert sorted(set(updates)) == ["alert_log", "equipment_assets"] and updates.count("equipment_assets") == 2
    assert "heat1" in BODY and "PRIMARY" in BODY and "heat2" in BODY and "BACKUP" in BODY


def test_shadow_rows_cannot_claim_dispatch():
    assert "planner_inference_runs_shadow_not_dispatched" in BODY
    assert "CHECK (mode IS DISTINCT FROM 'shadow' OR NOT dispatched)" in BODY


def test_archive_is_daily_and_covers_the_week_ahead():
    assert "add_job('public.archive_weather_forecast_job', interval '1 day'" in BODY
    assert "interval '7 days'" in BODY
    assert "SELECT public.fn_archive_weather_forecast();" in BODY  # seeded once, now


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_294_chains_from_293_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_293)
    expected = "(" + six.sealed_sql_293().replace(old, six.seal_profile_sql(294, six.SUCCESSOR_294), 1)
    expected += " AND " + six.ledger_row_sql(*six.CORTEX_PLANNER_MIGRATION) + ")"
    assert six.sealed_sql_294() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=294" in expected
    assert six.sealed_sql().count(" OR " + six.sealed_sql_293() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_294()) == 1
    assert (
        '"294-cortex-planner-shadow-storage.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    )
    assert "tests/test_294_cortex_planner_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, seals, with_294=True):
    ordinary, mcp_state = states(seals)
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(with_294)
    )


def test_post_249_readback_expects_293s_seals_with_294_stamped(monkeypatch):
    verify(monkeypatch, registered(294))
    verify(monkeypatch, registered(PREDECESSOR), with_294=False)
    for seal in SEALS:
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, {**registered(294), seal: "f" * 64})


# ── The native qualification ────────────────────────────────────────────────


def test_the_projection_is_the_files_literals():
    assert EVIDENCE["migration"] == c0.SUCCESSOR_294
    assert EVIDENCE["exact_final_migration_sha256"] == c0.SUCCESSOR_294_SHA256
    assert EVIDENCE["projected_successor"] == six.SUCCESSOR_294 == qualifier.successor_literals(SQL)
    assert EVIDENCE["before"] == registered(PREDECESSOR) == EVIDENCE["projected_successor"]


@pytest.mark.xfail(
    EVIDENCE.get("final_migration_qualification_pending", True),
    reason="DO NOT MERGE: 294 must be natively qualified on production (after 293) first",
    strict=True,
)
def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact294 with every seal unchanged."""
    assert EVIDENCE["final_migration_qualification_pending"] is False, "294 native qualification pending"
    assert EVIDENCE["qualified_successor"] == six.SUCCESSOR_294 == registered(294)
    assert not set(EVIDENCE) & set(qualifier.OPTION_EVIDENCE)
    assert EVIDENCE["before"] == EVIDENCE["after_rollback"] == registered(PREDECESSOR)
    assert EVIDENCE["final_migration_rollback_verified"] is True
    assert EVIDENCE["inside_transaction_receipts_match"] is True
