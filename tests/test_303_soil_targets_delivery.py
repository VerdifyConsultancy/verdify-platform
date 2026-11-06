"""Exact303 delivery: ordered admission, configurable soil targets, three moved seals, native evidence.

#1345: soil_moisture_targets gains the probe map the ingestor kept as a constant (probe_column, sensor_id,
occupancy_zone), attribution columns, an order CHECK (0 <= wilt < min < max < saturation <= 100) and one row per zone,
and the dashboard and MCP duties get SELECT plus UPDATE on the thresholds, the notes and the attribution. The backup
slots lose the "vanda-degraded" label. All three seals move (217 protects the table by name; the ingestor login reads
it; the MCP duty gets grants) and every receipt is re-sealed. test_native_qualification_is_recorded is the merge gate
for the rolled-back production run (production at exact302) and db/qualification/303-soil-targets-fixture.sql.
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
PREDECESSOR = 302
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("soil303_delivery", "c0-migration-delivery.py")
six = load("soil303_six", "bootstrap-six-runtime-roles.py")
maintenance = load("soil303_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("soil303_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_303
SOURCE = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/303-soil-targets-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/303-soil-targets-native-qualification.json"
NUMBERS = [*range(255, 292), 293, 294, 296, 301, 302, 303]


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


BODY = uncommented(SOURCE.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])


def later():
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in NUMBERS]


def files():
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in NUMBERS}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_303_SHA256 == six.SOIL_TARGETS_MIGRATION[2]
    assert six.SOIL_TARGETS_MIGRATION[:2] == (303, c0.SUCCESSOR_303) == (303, "303-soil-targets-configurable.sql")
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="303 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_303: "0" * 64})
    rows = later()
    for invalid in (rows + ["304-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["303-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_moves_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert predecessor == registered(PREDECESSOR)
    assert six.SUCCESSOR_303 == registered(303)
    for seal in SEALS:
        assert six.SUCCESSOR_303[seal] != predecessor[seal], seal
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + six.sealed_sql_302().replace(old, six.seal_profile_sql(303, six.SUCCESSOR_303), 1)
    expected += " AND " + six.ledger_row_sql(*six.SOIL_TARGETS_MIGRATION) + ")"
    assert six.sealed_sql_303() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=303" in expected
    assert six.sealed_sql().count(" OR " + six.sealed_sql_302() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_303()) == 1
    assert '"303-soil-targets-configurable.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_303_soil_targets_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def test_migration_pins_its_predecessor_and_reseals_every_receipt():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    for digest in registered(PREDECESSOR).values():
        assert SOURCE.count(f"'{digest}'") == 1
    assert qualifier.successor_literals(SOURCE) == six.SUCCESSOR_303
    successor = SOURCE.split("DO $successor$", 1)[1]
    for login in (API, INGESTOR):
        assert f"WHERE login_name='{login}';" in successor
    assert "UPDATE public.mcp_runtime_boundary_receipt" in successor


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SOURCE)
    code = uncommented(SOURCE).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    for name, sql in qualifier.sessions(SOURCE, (), qualifier.native_checks(NATIVE)).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1, name


def test_grants_are_select_and_threshold_update_for_dashboard_and_mcp_only():
    grants = [" ".join(g.split()) for g in re.findall(r"\bGRANT\b.*?;", BODY, re.S)]
    assert grants == [
        "GRANT SELECT ON public.soil_moisture_targets TO verdify_dashboard_runtime, verdify_mcp_runtime;",
        "GRANT UPDATE (min_pct, max_pct, saturation_pct, wilt_pct, notes, updated_at, updated_by, updated_source, "
        "reason) ON public.soil_moisture_targets TO verdify_dashboard_runtime, verdify_mcp_runtime;",
    ]
    for absent in ("probe_column,", "verdify_api_runtime", "verdify_ingestor_runtime", "PUBLIC", "DELETE", "INSERT"):
        assert absent not in BODY.split("GRANT", 1)[1], absent


def test_probe_map_equals_the_ingestor_seed_and_the_slots_lose_the_crop_label():
    sys.path.insert(0, str(ROOT / "ingestor"))
    from tasks._common import SOIL_DRYOUT_PROBES

    seeded = set(re.findall(r"\('(\w+)',\s*'(soil_moisture_\w+)',\s*'(soil\.\w+)',\s*'(\w+)'\)", BODY))
    assert seeded == {(target, col, sensor, zone) for col, sensor, zone, target in SOIL_DRYOUT_PROBES}
    assert "UPDATE public.irrigation_slot SET label = 'sensor-fault backup' WHERE label = 'vanda-degraded';" in BODY
    assert "0 <= wilt_pct AND wilt_pct < min_pct AND min_pct < max_pct AND max_pct < saturation_pct" in BODY


def assert_recorded(evidence):
    assert evidence["final_migration_qualification_pending"] is False
    assert evidence["migration"] == c0.SUCCESSOR_303
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_303_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_303 == registered(303)
    assert evidence["before"] == registered(PREDECESSOR) == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"dashboard": True, "mcp": True}


def fake_qualification(monkeypatch, *, native=None):
    """Replay the qualifier's three sessions for 303, without a database, on production at 302."""
    before, after = registered(PREDECESSOR), registered(303)

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": {"dashboard": True, "mcp": True}}
    replies = {
        "probe": {"before": state(before, PREDECESSOR), "successor": state(after, PREDECESSOR)},
        "final": {"inside": state(after, 303), "native_fixture": native or fixture},
        "after": {"after": state(before, PREDECESSOR)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_303_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["projected_successor"] == six.SUCCESSOR_303 == qualifier.successor_literals(SOURCE)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact303 and its measured seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
