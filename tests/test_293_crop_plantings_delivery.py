"""Exact293 delivery: ordered admission, the planting model, its grants, the re-sealed seals, native evidence.

#1327 and #1328: plantings on the location tree. 293 adds layout columns to shelves and positions, the
shelf_water_lines and crop_occupancy tables, end_reason/source/actor on crops, the v_crop_planting_current view and
the dashboard, MCP and ingestor grants the planting service and its readers need. Those grants and columns move all
three seals. 293 chains from 291 (292 is a gap); the rolled-back native qualification on production at 291 measured
them, and test_native_qualification_is_recorded (the merge gate) checks its fixture.
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
PREDECESSOR = 291
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("crop_plantings293_delivery", "c0-migration-delivery.py")
six = load("crop_plantings293_six", "bootstrap-six-runtime-roles.py")
maintenance = load("crop_plantings293_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("crop_plantings293_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_293
SQL = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/293-crop-plantings-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
FLAT = " ".join(BODY.split())
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]


def later(last=293):
    # 292 is a gap: 293 follows 291 in the reviewed order.
    numbers = [n for n in (*range(255, 292), 293) if n <= last]
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in numbers]


def files(last=293):
    numbers = [n for n in (*range(255, 292), 293) if n <= last]
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in numbers}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_293_SHA256 == six.CROP_PLANTINGS_MIGRATION[2]
    assert six.CROP_PLANTINGS_MIGRATION[:2] == (293, c0.SUCCESSOR_293) == (293, "293-crop-plantings.sql")
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(PREDECESSOR), files(PREDECESSOR))
    with pytest.raises(c0.DeliveryError, match="reviewed 293 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_293: "0" * 64})
    rows = later()
    for invalid in (rows + ["294-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["293-other.sql"]):
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


def test_predecessor_binds_exact291_with_its_registered_seals():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_291, c0.SUCCESSOR_291_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "'293 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_291[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal
        assert PREDECESSOR_BLOCK.count(f"boundary_sha256=decode('{digest}','hex')") == 1, seal


def test_successor_reseals_all_three_receipts_with_the_registered_literals():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_293 == registered(293)
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{literals[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in SUCCESSOR_BLOCK
    assert (
        f"UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('{literals['mcp']}','hex') WHERE singleton"
    ) in SUCCESSOR_BLOCK
    assert SUCCESSOR_BLOCK.count("IF NOT FOUND THEN RAISE EXCEPTION") == 3
    assert "'293 refuses an unqualified successor'" in SUCCESSOR_BLOCK


# ── The model ───────────────────────────────────────────────────────────────


def test_occupancy_allows_many_plantings_per_zone_or_shelf_and_one_per_cell():
    table = re.search(r"CREATE TABLE public\.crop_occupancy \((.*?)\n\);", BODY, re.S)[1]
    flat = " ".join(table.split())
    assert "EXCLUDE USING gist (position_id WITH =, during WITH &&) WHERE (position_id IS NOT NULL)" in flat
    assert "FOREIGN KEY (shelf_id, zone_id) REFERENCES public.shelves (id, zone_id)" in flat
    assert "FOREIGN KEY (position_id, shelf_id) REFERENCES public.positions (id, shelf_id)" in flat
    assert "CREATE EXTENSION IF NOT EXISTS btree_gist" in FLAT
    # No uniqueness on a zone or a shelf: they hold any number of plantings.
    assert not re.search(r"UNIQUE[^;]*\b(zone_id|shelf_id)\b[^;]*during", flat)


def test_jasons_corrections_are_in_the_seed_and_backfill():
    # The south floor pots under the south shelves, on the wall drip.
    assert "('south_floor', 'L01'" in FLAT and "('south_floor', 'L02'" in FLAT
    # The wall drip covers the east, south and west walls.
    assert re.search(r"UPDATE public\.water_systems SET .*east, south and west", FLAT, re.I)
    # The Vandas: crop 5 stays, hangs in the center at shelf level, count unknown.
    assert re.search(r"UPDATE public\.crops SET count = NULL, position_id = NULL.*WHERE id = 5", FLAT)
    # 9, 14 and 15 were ended in production on 2026-11-05; only their end_reason is backfilled.
    assert re.search(r"end_reason = 'reconciled' WHERE id IN \(9, 14, 15\)", FLAT)
    assert not re.search(r"SET is_active\s*=", FLAT)


def test_no_control_function_is_created_or_replaced():
    assert not re.search(r"CREATE (OR REPLACE )?FUNCTION", BODY)
    assert not re.search(r"\b(crop_target_profiles|crop_band_anchors|desired_state|setpoint_changes)\b", BODY)


def test_api_stays_select_only_and_no_one_gets_delete():
    grants = re.findall(r"GRANT ([^;]*?) TO ([\w, ]+);", FLAT)
    assert grants
    for privileges, roles in grants:
        assert "verdify_api_runtime" not in roles, (privileges, roles)
        assert "DELETE" not in privileges and "TRUNCATE" not in privileges
    assert not re.search(r"\b(REVOKE|DROP)\b|\bTO PUBLIC\b|DEFAULT PRIVILEGES", BODY)


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_293_chains_from_291_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_291)
    expected = "(" + six.sealed_sql_291().replace(old, six.seal_profile_sql(293, six.SUCCESSOR_293), 1)
    expected += " AND " + six.ledger_row_sql(*six.CROP_PLANTINGS_MIGRATION) + ")"
    assert six.sealed_sql_293() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=293" in expected
    assert six.sealed_sql().count(" OR " + six.sealed_sql_291() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_293()) == 1
    assert '"293-crop-plantings.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_293_crop_plantings_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=293):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_293s_seals_once_293_is_stamped(monkeypatch):
    new = registered(293)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_293
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_293_SHA256
    assert evidence["projected_successor"] == six.SUCCESSOR_293 == qualifier.successor_literals(SQL)
    assert evidence["before"] == registered(PREDECESSOR)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact293 and its re-sealed seals."""
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["final_migration_qualification_pending"] is False, (
        "293 native qualification pending: run "
        "scripts/qualify-migration-successor.py db/migrations/293-crop-plantings.sql --fixture "
        "tests/fixtures/293-crop-plantings-native-qualification.json --prelock public.shelves on production at 291 (every session rolls back); set the "
        "measured seals in the file's successor block and the c0-migration-delivery.py and "
        "bootstrap-six-runtime-roles.py registrations, update the file's sha at both and in the fixture, and run it "
        "again"
    )
    assert evidence["qualified_successor"] == six.SUCCESSOR_293 == registered(293)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["after_rollback"] == evidence["before"] == registered(PREDECESSOR)
