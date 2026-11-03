"""Exact287 delivery: ordered admission, the one rule update, unchanged seals, native evidence.

Jason retired the high_wind forecast rule on 2026-11-03. 287 disables the row 063 seeds and keeps it as history. Neither
runtime digest hashes table rows, so every runtime seal keeps its merged predecessor 286's exact value and no receipt is
re-sealed. The native qualification (the migration inside rolled-back production transactions) measured that;
test_native_qualification_is_recorded is the merge gate. Production's row was already disabled by hand, so the UPDATE
matches no row there.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The merged migration this successor is measured against.
PREDECESSOR = 286
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
DISABLE = (
    "UPDATE public.forecast_action_rules SET enabled = false, updated_at = now() WHERE name = 'high_wind' AND enabled;"
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("high_wind287_delivery", "c0-migration-delivery.py")
six = load("high_wind287_six", "bootstrap-six-runtime-roles.py")
maintenance = load("high_wind287_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("high_wind287_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_287
SOURCE = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/287-retire-high-wind-rule-native-qualification.json"


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 288)]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 288)}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_287_SHA256 == six.RETIRE_HIGH_WIND_MIGRATION[2]
    assert six.RETIRE_HIGH_WIND_MIGRATION[:2] == (287, c0.SUCCESSOR_287) == (287, "287-retire-high-wind-rule.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="287 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_287: "0" * 64})
    rows = later()
    for invalid in (rows + ["288-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["287-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_287 == predecessor == registered(PREDECESSOR) == registered(287)
    assert c0.SUCCESSOR_287_DIGESTS == {API: six.SUCCESSOR_287["api"], INGESTOR: six.SUCCESSOR_287["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(287, six.SUCCESSOR_287), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.RETIRE_HIGH_WIND_MIGRATION) + ")"
    assert six.sealed_sql_287() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=287" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.RETIRE_HIGH_WIND_MIGRATION)
    # 287 follows 286 in both ORs; a later successor follows it.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_286() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_287()) == 1
    assert '"287-retire-high-wind-rule.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_287_retire_high_wind_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    # The predecessor block and the successor block each pin all three seals.
    for digest in registered(287).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_287


def test_the_file_only_disables_the_high_wind_rule():
    # Its one write anywhere, the two blocks included, and the only statement between them.
    code = re.sub(r"--[^\n]*", "", SOURCE)
    writes = r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|COPY)\b"
    assert re.findall(writes, code, re.I) == ["UPDATE"]
    assert qualifier.split_successor(SOURCE)[0].split("END $predecessor$;")[1].strip() == DISABLE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    for name, sql in qualifier.sessions(SOURCE).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


def assert_recorded(evidence):
    """What the recorded native qualification must show before 287 merges."""
    assert evidence["migration"] == c0.SUCCESSOR_287
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_287_SHA256
    assert evidence["final_migration_qualification_pending"] is False
    assert evidence["qualified_successor"] == six.SUCCESSOR_287 == registered(287) == registered(PREDECESSOR)
    # Measured on production at exact286, which is live: nothing applied first and no seal moved.
    assert not set(evidence) & set(qualifier.OPTION_EVIDENCE)
    assert evidence["before"] == evidence["after_rollback"] == registered(PREDECESSOR)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_qualification(monkeypatch, *, inside=None):
    """Replay the qualifier's three sessions for 287, without a database, on production at 286."""
    seals = registered(287)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    replies = {
        "probe": {"before": state(PREDECESSOR), "successor": state(PREDECESSOR)},
        "final": {"inside": state(287) if inside is None else inside},
        "after": {"after": state(PREDECESSOR)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_287_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == report
    assert_recorded(written)
    # A receipt that does not equal its digest inside the transaction writes nothing.
    new = registered(287)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "api_receipt": "f" * 64, "max_seq": 287}
    seen = fake_qualification(monkeypatch, inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact287 and its unchanged seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
