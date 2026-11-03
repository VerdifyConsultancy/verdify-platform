"""Exact288 delivery: ordered admission, the one registry update, unchanged seals, native evidence.

The engine firmware removed four config switches, so their sensor_registry rows would open four false sensor_offline
alerts about 2026-11-16 (#1278). The Home Assistant trim (VD-48) ended two climate columns, whose rows hold open
sensor_offline alerts. 288 makes all six inactive and keeps them as history. Neither runtime digest hashes table rows,
so every runtime seal keeps its merged predecessor 287's exact value and no receipt is re-sealed.
test_native_qualification_is_recorded is the merge gate for the rolled-back production run that measures that.
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
PREDECESSOR = 287
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
RETIRED = (
    "equipment.irrigation_enabled",
    "equipment.irrigation_wall_enabled",
    "equipment.irrigation_center_enabled",
    "equipment.irrigation_weather_skip",
    "climate.wet_bulb_temp_f",
    "climate.feels_like_f",
)
RETIRE = (
    "UPDATE public.sensor_registry SET active = false WHERE sensor_id IN ("
    + ", ".join(f"'{sensor}'" for sensor in RETIRED)
    + ") AND active;"
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("irrigation_registry288_delivery", "c0-migration-delivery.py")
six = load("irrigation_registry288_six", "bootstrap-six-runtime-roles.py")
maintenance = load("irrigation_registry288_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("irrigation_registry288_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_288
SOURCE = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/288-retire-irrigation-registry-rows-native-qualification.json"


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 289)]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 289)}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_288_SHA256 == six.RETIRE_IRRIGATION_REGISTRY_MIGRATION[2]
    assert (
        six.RETIRE_IRRIGATION_REGISTRY_MIGRATION[:2]
        == (288, c0.SUCCESSOR_288)
        == (288, "288-retire-irrigation-registry-rows.sql")
    )
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="288 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_288: "0" * 64})
    rows = later()
    for invalid in (rows + ["289-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["288-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_288 == predecessor == registered(PREDECESSOR) == registered(288)
    assert c0.SUCCESSOR_288_DIGESTS == {API: six.SUCCESSOR_288["api"], INGESTOR: six.SUCCESSOR_288["ingestor"]}
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(288, six.SUCCESSOR_288), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.RETIRE_IRRIGATION_REGISTRY_MIGRATION) + ")"
    assert six.sealed_sql_288() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=288" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.RETIRE_IRRIGATION_REGISTRY_MIGRATION)
    # 288 follows 287 in both ORs; a later successor follows it.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_287() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_288()) == 1
    assert (
        '"288-retire-irrigation-registry-rows.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    )
    assert "tests/test_288_retire_irrigation_registry_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    # The predecessor block and the successor block each pin all three seals.
    for digest in registered(288).values():
        assert SOURCE.count(f"'{digest}'") == 2
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_288


def test_the_file_only_retires_the_six_registry_rows():
    # Its one write anywhere, the two blocks included, and the only statement between them.
    code = re.sub(r"--[^\n]*", "", SOURCE)
    writes = r"\b(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|COPY)\b"
    assert re.findall(writes, code, re.I) == ["UPDATE"]
    assert qualifier.split_successor(SOURCE)[0].split("END $predecessor$;")[1].strip() == RETIRE


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    for name, sql in qualifier.sessions(SOURCE).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name


def assert_recorded(evidence):
    """What the recorded native qualification must show before 288 merges."""
    assert evidence["migration"] == c0.SUCCESSOR_288
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_288_SHA256
    assert evidence["final_migration_qualification_pending"] is False
    assert evidence["qualified_successor"] == six.SUCCESSOR_288 == registered(288) == registered(PREDECESSOR)
    # Measured on production at exact287, which is live: nothing applied first and no seal moved.
    assert not set(evidence) & set(qualifier.OPTION_EVIDENCE)
    assert evidence["before"] == evidence["after_rollback"] == registered(PREDECESSOR)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_qualification(monkeypatch, *, inside=None):
    """Replay the qualifier's three sessions for 288, without a database, on production at 287."""
    seals = registered(288)

    def state(max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    replies = {
        "probe": {"before": state(PREDECESSOR), "successor": state(PREDECESSOR)},
        "final": {"inside": state(288) if inside is None else inside},
        "after": {"after": state(PREDECESSOR)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_288_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == report
    assert_recorded(written)
    # A receipt that does not equal its digest inside the transaction writes nothing.
    new = registered(288)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "api_receipt": "f" * 64, "max_seq": 288}
    seen = fake_qualification(monkeypatch, inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact288 and its unchanged seals."""
    assert FIXTURE.exists(), (
        "288 native qualification pending: run scripts/qualify-migration-successor.py "
        "db/migrations/288-retire-irrigation-registry-rows.sql "
        "--fixture tests/fixtures/288-retire-irrigation-registry-rows-native-qualification.json "
        "on production (every session rolls back) and commit the fixture it writes"
    )
    assert_recorded(json.loads(FIXTURE.read_text()))
