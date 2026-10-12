"""Packet guard/cleanup regressions; mocks earn no actual-target credit."""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

SPEC = importlib.util.spec_from_file_location("v2_readonly_probe_test", Path(__file__).with_name("probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    for key, value in probe.OFF_ENV.items():
        monkeypatch.setenv(key, value)
    uid = "11111111-2222-4333-8444-555555555555"
    metadata = {"namespace": "verdify-db-rehearsal", "uid": uid}
    cluster = {"metadata": dict(metadata, name=probe.CLUSTER), "status": {"currentPrimary": "fixture-primary"}}
    pod = {
        "metadata": dict(metadata, name="fixture-primary", labels={"cnpg.io/cluster": probe.CLUSTER}),
        "status": {"phase": "Running", "podIP": "10.42.1.99", "conditions": [{"type": "Ready", "status": "True"}]},
    }
    service = {
        "metadata": dict(metadata, name=probe.CLUSTER + "-rw", ownerReferences=[{"kind": "Cluster", "uid": uid}]),
        "spec": {"selector": {"cnpg.io/cluster": probe.CLUSTER, "role": "primary"}, "ports": [{"port": 5432}]},
    }
    binding = tmp_path / "fixture-native-binding.json"
    binding.write_text(json.dumps({"cluster": cluster, "pod": pod, "service": service}))
    release = tmp_path / "fixture-owner-release.txt"
    release.write_text("unit fixture only; no actual authorization or target\n")
    return argparse.Namespace(
        duty="executor",
        native_binding=binding,
        cluster_uid=uid,
        database_oid=12345,
        system_identifier="1234567890123456789",
        admission_sha256="a" * 64,
        stage_release=release,
        device_id="fixture-device",
        experiment_id=None,
    )


@pytest.mark.asyncio
async def test_unreadable_owner_release_stops_before_import_or_credential(prepared, monkeypatch):
    prepared.stage_release.unlink()
    monkeypatch.delenv("QUALIFICATION_DB_PASSWORD", raising=False)
    monkeypatch.setattr(probe, "load_sources", lambda **kwargs: pytest.fail("consumer imported before release checked"))
    with pytest.raises(FileNotFoundError):
        await probe.actual_probe(prepared)


@pytest.mark.asyncio
async def test_enabled_mode_stops_before_import_or_credential(prepared, monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.delenv("QUALIFICATION_DB_PASSWORD", raising=False)
    monkeypatch.setattr(probe, "load_sources", lambda **kwargs: pytest.fail("consumer imported before OFF checked"))
    with pytest.raises(ValueError, match="boundary mismatch"):
        await probe.actual_probe(prepared)


@pytest.mark.asyncio
async def test_production_binding_stops_before_credential_or_connection(prepared, monkeypatch):
    binding = json.loads(prepared.native_binding.read_text())
    binding["cluster"]["metadata"]["name"] = "verdify-prod"
    prepared.native_binding.write_text(json.dumps(binding))
    monkeypatch.delenv("QUALIFICATION_DB_PASSWORD", raising=False)
    monkeypatch.setattr(asyncpg, "connect", lambda **kwargs: pytest.fail("connection attempted"))
    with pytest.raises(AssertionError):
        await probe.actual_probe(prepared)


@pytest.mark.asyncio
async def test_transaction_start_failure_always_closes_authenticated_connection(prepared, monkeypatch):
    calls = []

    class Transaction:
        async def start(self):
            raise RuntimeError("fixture transaction start failure")

        async def rollback(self):
            pytest.fail("rollback on transaction that never started")

    class Connection:
        def transaction(self, *, readonly):
            assert readonly is True
            return Transaction()

        async def close(self):
            calls.append("closed")

    async def connect(**kwargs):
        assert kwargs["host"] == probe.HOST
        assert kwargs["server_settings"]["default_transaction_read_only"] == "on"
        calls.append("fixture-connect")
        return Connection()

    monkeypatch.setenv("QUALIFICATION_DB_PASSWORD", "unit-fixture-never-sent")
    monkeypatch.setattr(asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="transaction start failure"):
        await probe.actual_probe(prepared)
    assert calls == ["fixture-connect", "closed"]


def test_exact_component_module_import_avoids_scheduler_and_config_package():
    code = """import importlib.util,sys
from pathlib import Path
p=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('packet_import_guard',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.source_guard();component,api,binding=m.load_sources(duty='executor')
assert component is not None and api is None
assert 'tasks' not in sys.modules and 'config' not in sys.modules
assert 'tasks._common' not in sys.modules
assert sys.dont_write_bytecode and sys.pycache_prefix==str(m.PACKET/'qualification-bytecode-absent')
assert not Path(sys.pycache_prefix).exists()
print('exact component module; no scheduler/config import')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(Path(__file__).with_name("probe.py"))],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "exact component module; no scheduler/config import"


@pytest.mark.parametrize("defect", ["missing", "conflicting"])
def test_image_dependency_guard_rejects_incomplete_or_conflicting_hashes(tmp_path, monkeypatch, defect):
    for name in ("source-hashes.json", "import-manifest.json"):
        (tmp_path / name).write_bytes((probe.PACKET / name).read_bytes())
    manifest_path = tmp_path / "source-hashes.json"
    manifest = json.loads(manifest_path.read_text())
    if defect == "missing":
        del manifest["files"]["ingestor/entity_map.py"]
    else:
        manifest["files"]["ingestor/entity_map.py"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(probe, "PACKET", tmp_path)
    monkeypatch.setattr(probe, "source_path", lambda *args: pytest.fail("source read before complete hashes checked"))
    with pytest.raises(ValueError, match="dependency hash"):
        probe.source_guard(layout="ingestor-image", duty="executor")
