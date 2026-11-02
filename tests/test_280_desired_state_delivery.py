"""Exact280 (VD-30) delivery: ordered admission, seals, grant text and native evidence.

The rolled-back native qualification on production proves the successor seals;
these tests bind the migration file, its registrations and that evidence.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/280-desired-state-native-qualification.json"
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
NEW_RELATIONS = ("public.desired_state", "public.v_desired_state_current", "public.activity_log")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("desired280_delivery", "c0-migration-delivery.py")
six = load("desired280_six", "bootstrap-six-runtime-roles.py")
maintenance = load("desired280_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("desired280_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_280
SQL = MIGRATION.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)


def later(last=280):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def reviewed(last=280):
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, last + 1)}


def receipt():
    return json.loads(FIXTURE.read_text())


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_280_SHA256 == six.DESIRED_STATE_MIGRATION[2]
    assert six.DESIRED_STATE_MIGRATION[:2] == (280, c0.SUCCESSOR_280)
    c0.reviewed_post_254(later(), reviewed())
    with pytest.raises(c0.DeliveryError, match="280 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_280: "0" * 64})
    for invalid in [later() + ["281-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["280-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"280 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 280 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_280: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)


def test_predecessor_and_successor_literals_bind_279_and_the_registration():
    head, successor = qualifier.split_successor(SQL)
    predecessor = head.split("END $predecessor$;")[0]
    assert f"seq=279 AND filename='db/migrations/{c0.SUCCESSOR_279}'" in predecessor
    assert f"sha256='{c0.SUCCESSOR_279_SHA256}'" in predecessor
    assert "seq>=280" in predecessor and "'280 refuses an unqualified predecessor'" in predecessor
    for digest in (*c0.SUCCESSOR_279_DIGESTS.values(), c0.SUCCESSOR_279_MCP_DIGEST):
        assert predecessor.count(digest) == 2  # the live digest and its receipt
    expected = {
        "api": c0.SUCCESSOR_280_DIGESTS[API],
        "ingestor": c0.SUCCESSOR_280_DIGESTS[INGESTOR],
        "mcp": c0.SUCCESSOR_280_MCP_DIGEST,
    }
    assert qualifier.successor_literals(SQL) == expected == six.SUCCESSOR_280
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{expected[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in successor
    assert f"mcp_runtime_boundary_receipt SET boundary_sha256=decode('{expected['mcp']}','hex') WHERE singleton" in (
        successor
    )
    assert successor.count("IF NOT FOUND THEN RAISE EXCEPTION") == 3


def grants():
    rows = set()
    for privileges, objects, grantee in re.findall(r"^GRANT (.+?) ON (?:SEQUENCE )?(.+?) TO (\w+);$", CODE, re.M):
        for relation in (name.strip() for name in objects.split(",")):
            if "(" in privileges:
                rows.add((grantee, relation, privileges))
            else:
                rows.update((grantee, relation, p.strip()) for p in privileges.split(","))
    return rows


def test_grants_are_exactly_the_append_only_allowlist():
    assert len(re.findall(r"\bGRANT\b", CODE)) == len(re.findall(r"^GRANT .+;$", CODE, re.M)) == 7
    ingestor, mcp = "verdify_ingestor_runtime", "verdify_mcp_runtime"
    assert grants() == {
        (ingestor, "public.desired_state", "SELECT"),
        (ingestor, "public.v_desired_state_current", "SELECT"),
        (ingestor, "public.activity_log", "INSERT"),
        (ingestor, "public.activity_log_id_seq", "USAGE"),
        (mcp, "public.desired_state", "SELECT"),
        (mcp, "public.desired_state", "INSERT"),
        (mcp, "public.activity_log", "SELECT"),
        (mcp, "public.activity_log", "INSERT"),
        (mcp, "public.v_desired_state_current", "SELECT"),
        (mcp, "public.crop_band_anchors", "UPDATE (value, width_below, width_above)"),
        (mcp, "public.desired_state_id_seq", "USAGE"),
        (mcp, "public.activity_log_id_seq", "USAGE"),
    }
    assert not any("setpoint_changes" in relation for _, relation, _ in grants())
    assert "setpoint_changes" not in CODE and "setpoint_plan" not in CODE
    for _, relation, privilege in grants():
        if relation in NEW_RELATIONS:
            assert privilege in {"SELECT", "INSERT"}, (relation, privilege)
    assert re.findall(r"^REVOKE .+;$", CODE, re.M) == [
        "REVOKE ALL ON FUNCTION public.fn_desired_state_notify() FROM PUBLIC;"
    ]
    assert not re.search(r"\b(PUBLIC|verdify_api_runtime\w*)\b", " ".join(g for g, _, _ in grants()))


def test_trigger_only_wakes_with_an_empty_payload_and_leaves_the_fast_path():
    assert (
        "CREATE TRIGGER trg_desired_state_notify AFTER INSERT ON public.desired_state\n"
        "    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_desired_state_notify();"
    ) in CODE
    assert re.findall(r"pg_notify\([^)]*\)", CODE) == ["pg_notify('desired_state_changed', '')"]
    assert "FOR EACH ROW" not in CODE and "RETURN NULL;" in CODE
    assert "trg_setpoint_notify" not in CODE and "notify_setpoint_change" not in CODE
    assert "SELECT DISTINCT ON (greenhouse_id, param) *" in CODE
    assert "ORDER BY greenhouse_id, param, created_at DESC, id DESC" in CODE
    assert (
        "UNIQUE (source, request_id)" in CODE and "request_id" not in CODE.split("CREATE TABLE public.activity_log")[0]
    )


def test_registration_binds_the_recorded_successor_and_moves_all_three_seals():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_280
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_280_SHA256
    assert evidence["before"] == {
        "api": c0.SUCCESSOR_279_DIGESTS[API],
        "ingestor": c0.SUCCESSOR_279_DIGESTS[INGESTOR],
        "mcp": c0.SUCCESSOR_279_MCP_DIGEST,
    }
    successor = evidence["qualified_successor"] or evidence["projected_successor"]
    assert successor == six.SUCCESSOR_280
    assert c0.SUCCESSOR_280_DIGESTS == {API: six.SUCCESSOR_280["api"], INGESTOR: six.SUCCESSOR_280["ingestor"]}
    assert c0.SUCCESSOR_280_MCP_DIGEST == six.SUCCESSOR_280["mcp"]
    assert all(successor[seal] != evidence["before"][seal] for seal in ("api", "ingestor", "mcp"))


def test_sealed_sql_280_chains_from_279_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(279, six.SUCCESSOR_279)
    expected = "(" + six.sealed_sql_279().replace(old, six.seal_profile_sql(280, six.SUCCESSOR_280), 1)
    expected += " AND " + six.ledger_row_sql(*six.DESIRED_STATE_MIGRATION) + ")"
    assert six.sealed_sql_280() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DESIRED_STATE_MIGRATION)
    # A later successor (285, VD-33) follows 280 in the chain.
    assert " OR " + six.sealed_sql_279() + " OR " + expected + " OR " in six.sealed_sql()
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_280() + " OR ") == 1


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


def verify(monkeypatch, ordinary, mcp_state, last=280):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_the_280_seals_once_280_is_stamped(monkeypatch):
    seals = six.SUCCESSOR_280
    verify(monkeypatch, *states(seals["api"], seals["ingestor"], seals["mcp"]))
    old = six.SUCCESSOR_279
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=279)
    for mixed in (
        states(old["api"], seals["ingestor"], seals["mcp"]),
        states(seals["api"], old["ingestor"], seals["mcp"]),
        states(seals["api"], seals["ingestor"], old["mcp"]),
    ):
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *mixed)


def test_qualification_sessions_end_in_rollback_and_probe_stops_before_the_successor():
    plan = qualifier.sessions(SQL)
    for name, sql in plan.items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper(), name
    assert plan["after"].startswith("BEGIN READ ONLY;")
    assert "DO $successor$" not in plan["probe"] and "END $predecessor$;" in plan["probe"]
    assert "CREATE TABLE public.desired_state" in plan["probe"] and SQL in plan["final"]
    assert plan["probe"].index("QUALIFY before") < plan["probe"].index("QUALIFY successor")


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification must be recorded first."""
    evidence = receipt()
    assert evidence["final_migration_qualification_pending"] is False, (
        "280 native qualification pending: run scripts/qualify-migration-successor.py on production "
        "(rolled back) and commit the fixture it writes before merging"
    )
    assert evidence["qualified_successor"] == six.SUCCESSOR_280
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_sessions(monkeypatch, *, probe=None, inside=None, after=None):
    """Replay the three psql sessions of the qualifier without a database."""
    old, new = six.SUCCESSOR_279, six.SUCCESSOR_280

    def state(seals, receipts, max_seq=279):
        return {
            **seals,
            **{seal + "_receipt": receipts[seal] for seal in ("api", "ingestor", "mcp")},
            "max_seq": max_seq,
        }

    replies = {
        "probe": {"before": state(old, old), "successor": state(probe or new, old)},
        "final": {"inside": inside or state(new, new)},
        "after": {"after": after or state(old, old)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen, state


def test_qualifier_writes_the_fixture_only_from_a_clean_rolled_back_run(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"projected_successor": six.SUCCESSOR_280, "note": "kept"}))
    seen, _ = fake_sessions(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written["note"] == "kept" and written["projected_successor"] == six.SUCCESSOR_280
    assert written == {**written, **report}
    assert written["qualified_successor"] == six.SUCCESSOR_280
    assert written["before"] == written["after_rollback"] == six.SUCCESSOR_279
    assert written["final_migration_qualification_pending"] is False
    assert written["inside_transaction_receipts_match"] and written["final_migration_rollback_verified"]
    assert written["exact_final_migration_sha256"] == c0.SUCCESSOR_280_SHA256


def test_qualifier_refuses_changed_literals_or_an_unclean_run(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    measured = {**six.SUCCESSOR_280, "mcp": "0" * 64}
    seen, _ = fake_sessions(monkeypatch, probe=measured)
    with pytest.raises(qualifier.QualificationError, match="literals differ from the probe"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe"] and not fixture.exists()
    assert qualifier.qualify(MIGRATION, None, ["psql"], probe_only=True)["qualified_successor"] == measured
    _, state = fake_sessions(monkeypatch)
    for inside, after in (
        (state(six.SUCCESSOR_280, six.SUCCESSOR_279), None),
        (None, state(six.SUCCESSOR_280, six.SUCCESSOR_280, 280)),
    ):
        fake_sessions(monkeypatch, inside=inside, after=after)
        with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
            qualifier.qualify(MIGRATION, fixture, ["psql"])
        assert not fixture.exists()
