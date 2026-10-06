from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        "restored_duties", ROOT / "scripts/qualify-restored-runtime-duties.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parser_preserves_denial_payload_quotes_and_semicolons():
    text = "SELECT pg_temp.expect_denied('INSERT INTO observations(notes) VALUES(''a;b'')');SELECT 1;"
    statements = module().split_statements(text)
    assert statements == ["SELECT pg_temp.expect_denied('INSERT INTO observations(notes) VALUES(''a;b'')')", "SELECT 1"]
    with pytest.raises(AssertionError):
        module().split_statements("SELECT 'unterminated")


def test_complete_source_fixture_reuses_each_duty_without_owner_session_auth_proxy():
    mod = module()
    fixture = (ROOT / "db/qualification/268-six-runtime-role-fixture.sql").read_text()
    for duty in mod.DUTIES:
        cases = mod.cases(fixture, duty)
        assert len(cases) >= 15
        assert any(c["expect"] == "42501" and c["sql"] == "SET ROLE verdify" for c in cases)
        assert all("SESSION AUTHORIZATION" not in c["sql"] for c in cases)
        assert all(not c["sql"].startswith("SET search_path") for c in cases)
        assert all("expect_denied" not in c["sql"] for c in cases)
    # Genuine write duties remain included; no substitution with empty queries.
    for duty in mod.DUTIES - {"grafana"}:
        assert any(c["expect"] == "allowed" and c["sql"].startswith("INSERT") for c in mod.cases(fixture, duty))


def test_native_binding_and_backend_refuse_wrong_owner_replica_or_address():
    mod = module()
    uid = "4f697776-df25-4e22-b930-0b76cf35496e"

    def meta(name):
        return {"name": name, "namespace": "verdify-db-rehearsal", "uid": uid}

    binding = {
        "cluster": {"metadata": meta("verdify-cnpg-s2"), "status": {"currentPrimary": "verdify-cnpg-s2-1"}},
        "pod": {
            "metadata": {**meta("verdify-cnpg-s2-1"), "labels": {"cnpg.io/cluster": "verdify-cnpg-s2"}},
            "status": {"phase": "Running", "podIP": "10.42.5.100", "conditions": [{"type": "Ready", "status": "True"}]},
        },
        "service": {
            "metadata": {**meta("verdify-cnpg-s2-rw"), "ownerReferences": [{"kind": "Cluster", "uid": uid}]},
            "spec": {"selector": {"cnpg.io/cluster": "verdify-cnpg-s2", "role": "primary"}, "ports": [{"port": 5432}]},
        },
    }
    address, digest = mod.target_binding(json.dumps(binding).encode(), uid)
    assert address == "10.42.5.100" and len(digest) == 64
    identity = {
        "server_addr": address,
        "database_oid": 16385,
        "cluster_name": "verdify-cnpg-s2",
        "replica": False,
        "server_version_num": "160013",
    }
    mod.check_target_identity(identity, address, 16385)
    for key, value in (
        ("server_addr", "10.42.5.101"),
        ("database_oid", 16384),
        ("replica", True),
        ("cluster_name", "verdify-cnpg-rehearsal"),
    ):
        with pytest.raises(AssertionError):
            mod.check_target_identity({**identity, key: value}, address, 16385)
    binding["service"]["metadata"]["ownerReferences"][0]["uid"] = "wrong-cluster"
    with pytest.raises(AssertionError):
        mod.target_binding(json.dumps(binding).encode(), uid)
