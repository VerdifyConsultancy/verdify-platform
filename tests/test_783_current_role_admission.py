"""Source composition tests do not open a DB session or qualify an experiment."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_783", ROOT / "scripts/prepare-783-current-role-admission.py")
packet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet)


def test_current_roles_are_readonly_probes_not_owner_stateful_fixture():
    manifest, queries = packet.compose(ROOT)
    assert manifest["native_queries_executed"] == 0 and manifest["integration_accepted"] is False
    assert {r["stage"]: r["function_count"] for r in manifest["role_stages"]} == {
        "lifecycle": 25,
        "scheduler": 5,
        "randomizer": 5,
        "executor": 21,
        "freezer": 6,
    }
    for query in queries.values():
        assert "READ ONLY" in query and "statement_timeout='2s'" in query
        assert "has_function_privilege" in query and "ROLLBACK;" in query
        assert "$1" not in query and "$2" not in query and "PASSWORD" not in query
    assert "'public.fn_freeze_fixed_panel_native_previous_day()'" in queries["freezer"]


def test_missing_attestation_source_does_not_emit_an_admission(tmp_path):
    path = tmp_path / packet.DATABASE
    path.parent.mkdir(parents=True)
    path.write_text("ROLE_ATTESTATION_SQL = 'SELECT true'")
    with pytest.raises(ValueError, match="contract changed"):
        packet.compose(tmp_path)
