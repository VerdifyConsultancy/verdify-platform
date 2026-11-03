"""Exact286 (VD-33) delivery: ordered admission, the two override reads, seals and native evidence.

Migration M5 makes fn_lighting_circuit_policy and fn_lighting_minutes_policy
ignore setpoint_changes rows with source 'manual'. These tests bind the file,
its registrations and its function literals to the bodies of migrations 185
and 179, and gate the merge on the rolled-back native qualification, with
--apply-first for whichever of 283, 284 and 285 production lacks.

Its predecessor is 285 (VD-56). 285's own sha and seals are registered but not
measured yet, so the predecessor block carries them as stand-ins; a change to
them in c0-migration-delivery.py fails here until the file follows. Until 286
is measured, the file's successor block and its registrations carry those same
seals as stand-ins, which that block refuses because both replacements move the
api and ingestor digests, and test_native_qualification_is_recorded fails: the
merge gate.
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
FIXTURE = ROOT / "tests/fixtures/286-lighting-policy-ignores-manual-native-qualification.json"
SEALS = ("api", "ingestor", "mcp")
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


c0 = load("lighting286_delivery", "c0-migration-delivery.py")
six = load("lighting286_six", "bootstrap-six-runtime-roles.py")
maintenance = load("lighting286_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("lighting286_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_286
SQL = MIGRATION.read_text()
CODE = re.sub(r"--[^\n]*", "", SQL)
# The merged migration this successor is measured against.
PREDECESSOR = 285
REGISTERED = list(range(255, PREDECESSOR + 1))
# Not yet applied in production when this was written: --apply-first takes them in this order.
UNAPPLIED = tuple(ROOT / "db/migrations" / getattr(c0, f"SUCCESSOR_{n}") for n in (283, 284, 285))


def successor(n, suffix=""):
    return getattr(c0, f"SUCCESSOR_{n}{suffix}")


def registered(n):
    digests = successor(n, "_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": successor(n, "_MCP_DIGEST")}


def predecessor_seals():
    return registered(PREDECESSOR)


def later(last=286):
    return [c0.SUCCESSOR_254] + [successor(n) for n in [*REGISTERED, 286] if n <= last]


def reviewed(last=286):
    return {successor(n): successor(n, "_SHA256") for n in [*REGISTERED, 286] if n <= last}


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
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_286_SHA256 == six.LIGHTING_MANUAL_MIGRATION[2]
    assert six.LIGHTING_MANUAL_MIGRATION[:2] == (286, c0.SUCCESSOR_286)
    c0.reviewed_post_254(later(), reviewed())
    with pytest.raises(c0.DeliveryError, match="286 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_286: "0" * 64})
    for invalid in [later() + ["287-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["286-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"286 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 286 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_286: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)


def test_predecessor_and_successor_literals_bind_the_predecessor_and_the_registration():
    head, successor_block = qualifier.split_successor(SQL)
    predecessor = head.split("END $predecessor$;")[0]
    name = successor(PREDECESSOR)
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in predecessor
    assert f"sha256='{successor(PREDECESSOR, '_SHA256')}'" in predecessor
    assert "seq>=286" in predecessor and "'286 refuses an unqualified predecessor'" in predecessor
    for digest in predecessor_seals().values():
        assert predecessor.count(digest) == 2  # the live digest and its receipt
    expected = registered(286)
    assert qualifier.successor_literals(SQL) == expected == six.SUCCESSOR_286
    assert expected["mcp"] == predecessor_seals()["mcp"]  # MCP's seal is projected unchanged
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{expected[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in successor_block
    assert "mcp_runtime_boundary_receipt SET" not in successor_block
    assert successor_block.count("IF NOT FOUND THEN RAISE EXCEPTION") == 2
    assert "0" * 64 not in SQL


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
    assert "'286 refuses an unexpected lighting policy predecessor'" in block
    assert "'286 changed a lighting policy beyond its manual-source read'" in block
    assert not re.search(r"\b(GRANT|REVOKE|ALTER|DROP|INSERT|DELETE|TRUNCATE)\b", CODE, re.I)
    assert "setpoint_plan" not in CODE and "desired_state" not in CODE


def test_receipt_records_the_stand_ins_or_the_measurement_and_nothing_stale():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_286
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_286_SHA256
    assert c0.SUCCESSOR_286_DIGESTS == {API: six.SUCCESSOR_286["api"], INGESTOR: six.SUCCESSOR_286["ingestor"]}
    assert c0.SUCCESSOR_286_MCP_DIGEST == six.SUCCESSOR_286["mcp"]
    previous = predecessor_seals()
    if evidence["final_migration_qualification_pending"]:
        # Nothing is measured yet: the file and the registrations carry 285's literals.
        projected = evidence["projected_successor"]
        assert projected == qualifier.successor_literals(SQL) == six.SUCCESSOR_286 == registered(286) == previous
        assert "stand-ins" in evidence["projection"]
        assert evidence["before"] is None and evidence["qualified_successor"] is None
    else:
        measured = evidence["qualified_successor"]
        assert measured == qualifier.successor_literals(SQL) == six.SUCCESSOR_286 == registered(286)
        assert measured["api"] != previous["api"] and measured["ingestor"] != previous["ingestor"]
        assert measured["mcp"] == previous["mcp"]  # neither lighting policy is in the MCP digest
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


def test_sealed_sql_286_chains_from_the_predecessor_and_binds_the_exact_ledger_row():
    previous = getattr(six, f"sealed_sql_{PREDECESSOR}")
    old = six.seal_profile_sql(PREDECESSOR, getattr(six, f"SUCCESSOR_{PREDECESSOR}"))
    expected = "(" + previous().replace(old, six.seal_profile_sql(286, six.SUCCESSOR_286), 1)
    expected += " AND " + six.ledger_row_sql(*six.LIGHTING_MANUAL_MIGRATION) + ")"
    assert six.sealed_sql_286() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=286" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.LIGHTING_MANUAL_MIGRATION)
    # 286 follows 285 in both ORs; a later successor (287) follows it.
    assert six.sealed_sql().count(" OR " + previous() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_286()) == 1
    assert (
        '"286-lighting-policy-ignores-manual.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    )
    assert "tests/test_286_lighting_policy_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=286):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_the_286_seals_once_286_is_stamped(monkeypatch):
    new = registered(286)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = predecessor_seals()
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=PREDECESSOR)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


def test_qualification_sessions_end_in_rollback_and_probe_stops_before_the_successor():
    first = [qualifier.applied_first(path)[1] for path in UNAPPLIED]
    for blocks in ((), first):
        plan = qualifier.sessions(SQL, blocks)
        for name, sql in plan.items():
            assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
            assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name
        assert plan["after"].startswith("BEGIN READ ONLY;")
        head = qualifier.split_successor(SQL)[0]
        assert head in plan["probe"] and SQL in plan["final"]
        assert "286 refuses an unqualified successor" not in plan["probe"]
        assert "DO $manual_override$" in plan["probe"]
        assert (
            plan["probe"].index("QUALIFY before") < plan["probe"].index(head) < plan["probe"].index("QUALIFY successor")
        )
    # Applied first, each runs and stamps as the runner would, in order, before 286 in both write sessions.
    applied = qualifier.sessions(SQL, first)
    head = qualifier.split_successor(SQL)[0]
    for sql in (applied["probe"], applied["final"]):
        stamps = [
            sql.index(
                f"SELECT public.stamp_migration(\n    'db/migrations/{path.name}', 'db/migrations', {path.name[:3]},"
            )
            for path in UNAPPLIED
        ]
        assert stamps == sorted(stamps) and stamps[-1] < sql.index("QUALIFY applied") < sql.index(head)


def assert_recorded(evidence):
    """What the recorded native qualification must show before 286 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "286 native qualification pending: once 285's sha and seals are final, set them in this file's predecessor "
        "block, then run scripts/qualify-migration-successor.py db/migrations/286-lighting-policy-ignores-manual.sql "
        "--probe-only on production (rolled back; add --apply-first for db/migrations/283-irrigation-tables.sql, "
        "db/migrations/284-dashboard-runtime-grants.sql and db/migrations/285-daily-scorecard-view.sql, in that order, "
        "for each production lacks). Set the file's api and ingestor literals and the c0-migration-delivery.py and "
        "bootstrap-six-runtime-roles.py registrations to the measured digests (a moved MCP seal is a finding), then "
        "run it again with --fixture tests/fixtures/286-lighting-policy-ignores-manual-native-qualification.json and "
        "commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_286
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_286_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_286 == registered(286)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    if "applied_first" in evidence:
        first = evidence["applied_first"]
        assert first == [
            {"migration": successor(n), "seq": n, "sha256": successor(n, "_SHA256")}
            for n in range(286 - len(first), 286)
        ]
        measured_against = evidence["applied_first_seals"]
    else:
        measured_against = evidence["before"]
    assert measured_against == predecessor_seals()


def fake_qualification(monkeypatch, *, applied_first, inside=None):
    """Replay the qualifier's three sessions for 286, without a database, on production at 282 or 285."""
    predecessor, new = predecessor_seals(), registered(286)
    live, live_seq = (registered(282), 282) if applied_first else (predecessor, PREDECESSOR)

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    probe = {"before": state(live, live_seq), "successor": {**state(predecessor, PREDECESSOR), **new}}
    final = {"inside": state(new, 286) if inside is None else inside}
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


@pytest.mark.parametrize("applied_first", [(), UNAPPLIED], ids=["285-applied", "283-to-285-applied-first"])
def test_qualifier_records_286_and_the_gate_accepts_it(monkeypatch, tmp_path, applied_first):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first))
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == receipt()["projected_successor"]  # the projection stays beside it
    assert ("applied_first" in written) is bool(applied_first)
    assert_recorded(written)
    # A receipt that does not equal its digest inside the transaction writes nothing.
    new = registered(286)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "api_receipt": "f" * 64, "max_seq": 286}
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first), inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact286 and its moved api and ingestor seals."""
    assert_recorded(receipt())
