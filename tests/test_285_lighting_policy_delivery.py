"""Exact285 (VD-33) delivery: ordered admission, the two override reads, seals and native evidence.

Migration M5 makes fn_lighting_circuit_policy and fn_lighting_minutes_policy
ignore setpoint_changes rows with source 'manual'. These tests bind the file,
its registrations and its function literals to the bodies of migrations 185
and 179, and gate the merge on the rolled-back native qualification. The
number and the predecessor are provisional until 281-284 merge: the predecessor
here is whichever registered successor comes last before 285.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/285-lighting-policy-ignores-manual-native-qualification.json"
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
CIRCUIT, MINUTES = (
    "public.fn_lighting_circuit_policy(timestamptz,text)",
    "public.fn_lighting_minutes_policy(timestamptz,text)",
)
CIRCUIT_READ = ("COALESCE(source, '') <> 'esp32'", "COALESCE(source, '') NOT IN ('esp32', 'manual')")
MINUTES_READ = ("COALESCE(sc.source, '') <> 'esp32'", "COALESCE(sc.source, '') NOT IN ('esp32', 'manual')")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("lighting285_delivery", "c0-migration-delivery.py")
six = load("lighting285_six", "bootstrap-six-runtime-roles.py")
maintenance = load("lighting285_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("lighting285_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_285
SQL = MIGRATION.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)
REGISTERED = [n for n in range(255, 285) if hasattr(c0, f"SUCCESSOR_{n}")]
PREDECESSOR = REGISTERED[-1]


def successor(n, suffix=""):
    return getattr(c0, f"SUCCESSOR_{n}{suffix}")


def predecessor_seals():
    digests = successor(PREDECESSOR, "_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": successor(PREDECESSOR, "_MCP_DIGEST")}


def later(last=285):
    return [c0.SUCCESSOR_254] + [successor(n) for n in [*REGISTERED, 285] if n <= last]


def reviewed(last=285):
    return {successor(n): successor(n, "_SHA256") for n in [*REGISTERED, 285] if n <= last}


def receipt():
    return json.loads(FIXTURE.read_text())


def function_body(filename: str, open_tag: str, close_tag: str) -> str:
    text = (ROOT / "db/migrations" / filename).read_text()
    return text.split(open_tag, 1)[1].rsplit(close_tag, 1)[0]


CIRCUIT_BODY = function_body("185-per-circuit-lighting-crop-policy.sql", "AS $$", "$$;")
MINUTES_BODY = function_body("179-lighting-minutes-policy-planner-over-device.sql", "AS $function$", "$function$;")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_285_SHA256 == six.LIGHTING_MANUAL_MIGRATION[2]
    assert six.LIGHTING_MANUAL_MIGRATION[:2] == (285, c0.SUCCESSOR_285)
    c0.reviewed_post_254(later(), reviewed())
    with pytest.raises(c0.DeliveryError, match="285 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_285: "0" * 64})
    for invalid in [later() + ["286-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["285-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"285 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 285 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_285: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)


def test_predecessor_and_successor_literals_bind_the_predecessor_and_the_registration():
    head, successor_block = qualifier.split_successor(SQL)
    predecessor = head.split("END $predecessor$;")[0]
    name = successor(PREDECESSOR)
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in predecessor
    assert f"sha256='{successor(PREDECESSOR, '_SHA256')}'" in predecessor
    assert "seq>=285" in predecessor and "'285 refuses an unqualified predecessor'" in predecessor
    for digest in predecessor_seals().values():
        assert predecessor.count(digest) == 2  # the live digest and its receipt
    expected = {
        "api": c0.SUCCESSOR_285_DIGESTS[API],
        "ingestor": c0.SUCCESSOR_285_DIGESTS[INGESTOR],
        "mcp": c0.SUCCESSOR_285_MCP_DIGEST,
    }
    assert qualifier.successor_literals(SQL) == expected == six.SUCCESSOR_285
    assert expected["mcp"] == predecessor_seals()["mcp"]  # MCP's seal is projected unchanged
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{expected[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in successor_block
    assert "mcp_runtime_boundary_receipt SET" not in successor_block
    assert successor_block.count("IF NOT FOUND THEN RAISE EXCEPTION") == 2


def manual_override_rows() -> list[tuple[str, ...]]:
    block = CODE.split("DO $manual_override$", 1)[1].split("END $manual_override$;", 1)[0]
    pattern = (
        r"\('(public\.[^']+)'::regprocedure,\s*'([0-9a-f]{64})',\s*'([0-9a-f]{64})',\s*(NULL::text\[\]|ARRAY\['jit=off'\]),"
        r"\s*\$read\$(.+?)\$read\$,\s*\$read\$(.+?)\$read\$\)"
    )
    return re.findall(pattern, block)


def test_only_the_two_override_reads_change():
    rows = manual_override_rows()
    assert [row[0] for row in rows] == [CIRCUIT, MINUTES]
    for (_name, before, after, config, old, new), body, read, expected_config in (
        (rows[0], CIRCUIT_BODY, CIRCUIT_READ, "NULL::text[]"),
        (rows[1], MINUTES_BODY, MINUTES_READ, "ARRAY['jit=off']"),
    ):
        assert (old, new) == read
        assert body.count(old) == 1  # one override read per function
        assert before == sha(body)  # the live prosrc (185 and 179, unchanged since)
        assert after == sha(body.replace(old, new))  # nothing else in the body moves
        assert config == expected_config  # 275's jit=off stays on the minutes policy
    # 275 checked the same minutes-policy predecessor before setting jit=off.
    assert rows[1][1] in (ROOT / "db/migrations/275-lighting-minutes-policy-bounded-jit.sql").read_text()


def test_the_replacement_comes_from_the_live_definition_and_refuses_other_catalog_changes():
    block = CODE.split("DO $manual_override$", 1)[1].split("END $manual_override$;", 1)[0]
    assert "pg_catalog.pg_get_functiondef(p.oid)" in block
    assert "EXECUTE replace(definition, old_read, new_read);" in block
    # Owner, ACL, ROWS, SET and the rest compared; the defaults deparsed, since
    # their stored node trees carry the creating statement's parse offsets.
    metadata = "(to_jsonb(p) - 'prosrc' - 'proargdefaults')"
    assert block.count(metadata) == 2
    assert block.count("jsonb_build_object('arguments', pg_catalog.pg_get_function_arguments(p.oid))") == 2
    assert "IS DISTINCT FROM expected_config" in block
    assert "/ length(old_read) <> 1" in block
    assert "'285 refuses an unexpected lighting policy predecessor'" in block
    assert "'285 changed a lighting policy beyond its manual-source read'" in block
    assert not re.search(r"\b(GRANT|REVOKE|ALTER|DROP|INSERT|DELETE|TRUNCATE)\b", CODE, re.I)
    assert "setpoint_plan" not in CODE and "desired_state" not in CODE


def test_registration_binds_the_fixture_and_moves_the_api_and_ingestor_seals():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_285
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_285_SHA256
    assert evidence["before"] == predecessor_seals()
    projected = evidence["qualified_successor"] or evidence["projected_successor"]
    assert projected == six.SUCCESSOR_285
    assert c0.SUCCESSOR_285_DIGESTS == {API: six.SUCCESSOR_285["api"], INGESTOR: six.SUCCESSOR_285["ingestor"]}
    assert c0.SUCCESSOR_285_MCP_DIGEST == six.SUCCESSOR_285["mcp"]
    assert all(projected[seal] != evidence["before"][seal] for seal in ("api", "ingestor"))


def test_sealed_sql_285_chains_from_the_predecessor_and_binds_the_exact_ledger_row():
    previous = getattr(six, f"sealed_sql_{PREDECESSOR}")
    old = six.seal_profile_sql(PREDECESSOR, getattr(six, f"SUCCESSOR_{PREDECESSOR}"))
    expected = "(" + previous().replace(old, six.seal_profile_sql(285, six.SUCCESSOR_285), 1)
    expected += " AND " + six.ledger_row_sql(*six.LIGHTING_MANUAL_MIGRATION) + ")"
    assert six.sealed_sql_285() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.LIGHTING_MANUAL_MIGRATION)
    assert " OR " + previous() + " OR " + expected + ")" in six.sealed_sql()  # last until a later successor
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_285() + ")") == 1


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


def verify(monkeypatch, ordinary, mcp_state, last=285):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_the_285_seals_once_285_is_stamped(monkeypatch):
    seals = six.SUCCESSOR_285
    verify(monkeypatch, *states(seals["api"], seals["ingestor"], seals["mcp"]))
    old = predecessor_seals()
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=PREDECESSOR)
    for mixed in (
        states(old["api"], seals["ingestor"], seals["mcp"]),
        states(seals["api"], old["ingestor"], old["mcp"]),
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
    assert "DO $manual_override$" in plan["probe"] and SQL in plan["final"]
    assert plan["probe"].index("QUALIFY before") < plan["probe"].index("QUALIFY successor")


def test_native_qualification_is_recorded():
    """Merge gate: rebase onto 284, then record the rolled-back production qualification first."""
    evidence = receipt()
    assert evidence["final_migration_qualification_pending"] is False, (
        "285 native qualification pending: after 281-284 merge and 284 is applied in production, bind this "
        "file's predecessor to 284, run scripts/qualify-migration-successor.py on production (rolled back), set "
        "the measured seals here and in the registrations, and commit the fixture it writes before merging"
    )
    assert evidence["qualified_successor"] == six.SUCCESSOR_285
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
