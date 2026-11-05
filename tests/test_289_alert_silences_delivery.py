"""Exact289 delivery: ordered admission, the table and its seeds, the one grant, the re-sealed seals, native evidence.

Jason, 2026-11-05 ~02:10Z, on the soil alerts: "Yes, silence for a week." 289 adds alert_silence, seeds the
soil_dryout and soil_sensor_offline silences until 2026-11-12 03:00Z and grants the ingestor duty SELECT on it.
That ACL entry puts the table in both ordinary digests, so the api and ingestor seals move and both receipts are
re-sealed; the MCP seal stays 288's. The successor literals are a read-only projection on production at exact288
(the fixture's projection says how); test_native_qualification_is_recorded is the merge gate for the rolled-back
production run that measures them.
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
PREDECESSOR = 288
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
UNTIL = "2026-11-12 03:00:00+00"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("alert_silences289_delivery", "c0-migration-delivery.py")
six = load("alert_silences289_six", "bootstrap-six-runtime-roles.py")
maintenance = load("alert_silences289_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("alert_silences289_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_289
SQL = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/289-alert-silences-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]


def later(last=289):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def files(last=289):
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, last + 1)}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def statements(text):
    return [" ".join(s.split()) for s in re.findall(r"^(?:CREATE|COMMENT|INSERT|GRANT)\b.*?;\s*$", text, re.M | re.S)]


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_289_SHA256 == six.ALERT_SILENCE_MIGRATION[2]
    assert six.ALERT_SILENCE_MIGRATION[:2] == (289, c0.SUCCESSOR_289) == (289, "289-alert-silences.sql")
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(288), files(288))
    with pytest.raises(c0.DeliveryError, match="reviewed 289 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_289: "0" * 64})
    rows = later()
    for invalid in (rows + ["290-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["289-other.sql"]):
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


def test_predecessor_binds_exact288_with_its_registered_seals():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_288, c0.SUCCESSOR_288_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "to_regclass('public.alert_silence') IS NOT NULL" in PREDECESSOR_BLOCK
    assert "'289 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_288[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal  # the live digest
        assert PREDECESSOR_BLOCK.count(f"boundary_sha256=decode('{digest}','hex')") == 1, seal  # and its receipt


def test_successor_reseals_the_api_and_ingestor_receipts_and_keeps_the_mcp_seal():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_289 == registered(289)
    assert literals["mcp"] == registered(PREDECESSOR)["mcp"]
    assert literals["api"] != registered(PREDECESSOR)["api"]
    assert literals["ingestor"] != registered(PREDECESSOR)["ingestor"]
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{literals[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in SUCCESSOR_BLOCK
    # The MCP receipt is only read: it must still equal its digest.
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in SUCCESSOR_BLOCK
    assert "boundary_sha256=public.fn_mcp_runtime_boundary_digest())" in SUCCESSOR_BLOCK
    assert SUCCESSOR_BLOCK.count("IF NOT FOUND THEN RAISE EXCEPTION") == 2
    assert "'289 refuses an unqualified successor'" in SUCCESSOR_BLOCK


def test_body_creates_the_table_seeds_the_two_soil_silences_and_grants_the_ingestor_select():
    found = statements(BODY)
    assert [statement.split(" (")[0].split(" IS")[0] for statement in found] == [
        "CREATE TABLE public.alert_silence",
        "COMMENT ON TABLE public.alert_silence",
        "INSERT INTO public.alert_silence",
        "GRANT SELECT ON public.alert_silence TO verdify_ingestor_runtime;",
    ]
    assert re.sub(r"\s+", " ", BODY).count(";") == 4
    table = found[0]
    assert "alert_type text NOT NULL CHECK (alert_type <> 'leak_detected')" in table  # never silenced
    for column in ("id bigserial PRIMARY KEY", "sensor_pattern text,", "until timestamptz NOT NULL", "actor text"):
        assert column in table, column
    # The one grant: SELECT for the ingestor duty, nothing on the sequence, no one else.
    assert re.findall(r"\bGRANT\b", BODY) == ["GRANT"]
    assert not re.search(r"\b(REVOKE|ALTER|DROP|UPDATE|DELETE|TRUNCATE)\b|\bTO PUBLIC\b|DEFAULT PRIVILEGES", BODY)
    seeds = re.findall(r"\('(\w+)', NULL, '([^']+)',\s*'([^']+)',\s*'(\w+)'\)", found[2])
    assert [(alert_type, until, actor) for alert_type, until, _, actor in seeds] == [
        ("soil_dryout", UNTIL, "jason"),
        ("soil_sensor_offline", UNTIL, "jason"),
    ]
    for _, _, reason, _ in seeds:
        assert reason.startswith('Jason, 2026-11-05 ~02:10Z: "Yes, silence for a week."') and len(reason) <= 500
        assert "out of the soil" in reason and "reorganized" in reason


def test_the_monitor_reads_only_columns_the_table_has():
    alerts = (ROOT / "ingestor/tasks/alerts.py").read_text()
    read = re.search(r'"SELECT ([a-z_, ]+) FROM alert_silence "', alerts)
    table = re.search(r"CREATE TABLE public\.alert_silence \((.*?)\n\);", BODY, re.S)[1]
    columns = re.findall(r"^\s+(\w+)\s+(?:bigserial|text|timestamptz)\b", table, re.M)
    assert read and set(read[1].split(", ")) <= set(columns) == {
        "id",
        "alert_type",
        "sensor_pattern",
        "until",
        "reason",
        "actor",
        "created_at",
    }


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_289_chains_from_288_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_288)
    expected = "(" + six.sealed_sql_288().replace(old, six.seal_profile_sql(289, six.SUCCESSOR_289), 1)
    expected += " AND " + six.ledger_row_sql(*six.ALERT_SILENCE_MIGRATION) + ")"
    assert six.sealed_sql_289() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=289" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.ALERT_SILENCE_MIGRATION)
    # 289 follows 288 in both ORs.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_288() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_289()) == 1
    assert '"289-alert-silences.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_289_alert_silences_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=289):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_289s_seals_once_289_is_stamped(monkeypatch):
    new = registered(289)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(PREDECESSOR)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=PREDECESSOR)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def assert_recorded(evidence):
    """What the recorded native qualification must show before 289 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "289 native qualification pending: run scripts/qualify-migration-successor.py "
        "db/migrations/289-alert-silences.sql --fixture tests/fixtures/289-alert-silences-native-qualification.json "
        "on production (every session rolls back) and commit the fixture it writes. If its probe differs from the "
        "file's projected literals, set the measured api and ingestor digests in the file's successor block and in "
        "the c0-migration-delivery.py and bootstrap-six-runtime-roles.py registrations, update the file's sha at "
        "both and in the fixture, and run it again"
    )
    assert evidence["migration"] == c0.SUCCESSOR_289
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_289_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_289 == registered(289)
    # Measured on production at exact288, which is live: nothing applied first.
    assert not set(evidence) & set(qualifier.OPTION_EVIDENCE)
    assert evidence["before"] == evidence["after_rollback"] == registered(PREDECESSOR)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_qualification(monkeypatch, *, inside=None):
    """Replay the qualifier's three sessions for 289, without a database, on production at 288."""

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    before, after = state(registered(PREDECESSOR), PREDECESSOR), state(registered(289), 289)
    replies = {
        "probe": {"before": before, "successor": {**after, "max_seq": PREDECESSOR}},
        "final": {"inside": after if inside is None else inside},
        "after": {"after": before},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_289_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(FIXTURE.read_text())  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    # A receipt that does not equal its digest inside the transaction writes nothing.
    new = registered(289)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "api_receipt": "f" * 64, "max_seq": 289}
    seen = fake_qualification(monkeypatch, inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_289
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_289_SHA256
    assert evidence["projected_successor"] == six.SUCCESSOR_289 == qualifier.successor_literals(SQL)
    assert evidence["before"] == registered(PREDECESSOR)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact289 and its re-sealed seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
