"""Device-write safety gate (#79).

The ingestor must NEVER drive the physical ESP32 unless
VERDIFY_DEVICE_WRITE_ENABLED == '1' (default-deny). This is the hard interlock
that lets a k3s STAGING ingestor exist without any risk to the live greenhouse:
staging leaves the env unset/0, so every device-write chokepoint
(push_to_esp32 / push_occupancy_to_esp32) is a no-op.

These tests mock the aioesphomeapi client and assert ZERO
number_command/switch_command calls when the gate is off, and pass-through when
it is on. PR-blocking quality: a regression that re-opens the gate fails here.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402


@pytest.fixture
def mock_client():
    """Install a mock ESP32 client + key map into shared.esp32, restore after."""
    saved_client = shared.esp32.get("client")
    saved_keys = shared.esp32.get("keys")
    saved_logged = esp32_push._DEVICE_WRITE_DISABLED_LOGGED

    client = MagicMock()
    # number_command / switch_command are sync in the live client path; the
    # push helper handles both sync and coroutine returns. Keep them sync here.
    client.number_command = MagicMock(return_value=None)
    client.switch_command = MagicMock(return_value=None)
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"mister_engage_kpa": 11, "greenhouse_occupied": 22}

    yield client

    shared.esp32["client"] = saved_client
    shared.esp32["keys"] = saved_keys
    esp32_push._DEVICE_WRITE_DISABLED_LOGGED = saved_logged


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED", raising=False)
    # Reset the once-logged latch so each test starts clean.
    esp32_push._DEVICE_WRITE_DISABLED_LOGGED = False
    yield


def _run(coro):
    return asyncio.run(coro)


# ── gate OFF: default-deny (env unset) ───────────────────────────────────────


def test_push_noop_when_env_unset(mock_client):
    """Env unset -> zero device writes, returns 0."""
    pushed = _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
    assert pushed == 0
    mock_client.number_command.assert_not_called()
    mock_client.switch_command.assert_not_called()


def test_push_noop_when_env_zero(mock_client, monkeypatch):
    """Env '0' -> zero device writes (only '1' enables)."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "0")
    pushed = _run(
        esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number"), ("greenhouse_occupied", 1.0, "switch")])
    )
    assert pushed == 0
    mock_client.number_command.assert_not_called()
    mock_client.switch_command.assert_not_called()


def test_push_noop_when_env_truthy_but_not_one(mock_client, monkeypatch):
    """Only the exact string '1' opens the gate; 'true'/'yes' do NOT."""
    for val in ("true", "yes", "TRUE", "2", "on"):
        monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", val)
        esp32_push._DEVICE_WRITE_DISABLED_LOGGED = False
        pushed = _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
        assert pushed == 0, f"value {val!r} must not open the gate"
    mock_client.number_command.assert_not_called()


def test_occupancy_noop_when_env_unset(mock_client):
    """Occupancy chokepoint is also gated (defense in depth)."""
    pushed = _run(esp32_push.push_occupancy_to_esp32(True, "test"))
    assert pushed == 0
    mock_client.switch_command.assert_not_called()


def test_gate_logs_once(mock_client, caplog):
    """The disabled-warning is emitted once, not per call."""
    import logging

    with caplog.at_level(logging.WARNING, logger="esp32_push"):
        _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
        _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.4, "number")]))
    disabled_warnings = [r for r in caplog.records if "Device writes DISABLED" in r.getMessage()]
    assert len(disabled_warnings) == 1


# ── gate ON: writes pass through ─────────────────────────────────────────────


def test_push_passes_through_when_enabled(mock_client, monkeypatch):
    """Env exactly '1' -> writes reach the client."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    pushed = _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
    assert pushed == 1
    mock_client.number_command.assert_called_once()
    key_arg, val_arg = mock_client.number_command.call_args.args
    assert key_arg == 11
    assert val_arg == 1.3


def test_switch_passes_through_when_enabled(mock_client, monkeypatch):
    """Switch writes pass through and coerce to bool when enabled."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    pushed = _run(esp32_push.push_to_esp32([("greenhouse_occupied", 1.0, "switch")]))
    assert pushed == 1
    mock_client.switch_command.assert_called_once()
    key_arg, val_arg = mock_client.switch_command.call_args.args
    assert key_arg == 22
    assert val_arg is True


def test_occupancy_passes_through_when_enabled(mock_client, monkeypatch):
    """Occupancy push reaches switch_command when the gate is open."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    pushed = _run(esp32_push.push_occupancy_to_esp32(True, "test"))
    assert pushed == 1
    mock_client.switch_command.assert_called_once()


@pytest.mark.parametrize(
    "broken", ["replicas", "rolling", "image_tag", "config_identity", "readiness", "secret", "lease"]
)
def test_current_k3s_contract_rejects_real_drift(broken):
    """Native CI runs this file; each negative also has a genuine-render control."""
    from test_01_infrastructure import production_documents, validate_delivery

    documents = production_documents()
    validate_delivery(documents)
    objects = {(x["kind"], x["metadata"]["name"]): x for x in documents}
    writer = objects[("Deployment", "verdify-ingestor")]
    if broken == "replicas":
        writer["spec"]["replicas"] = 2
    elif broken == "rolling":
        writer["spec"]["strategy"]["type"] = "RollingUpdate"
    elif broken == "image_tag":
        writer["spec"]["template"]["spec"]["containers"][0]["image"] = "registry.vallery.net/verdify-ingestor:latest"
    elif broken == "config_identity":
        writer["spec"]["template"]["metadata"]["annotations"]["verdify.io/config-revision"] = "unknown"
    elif broken == "readiness":
        objects[("Deployment", "verdify-mcp")]["spec"]["template"]["spec"]["containers"][0]["readinessProbe"][
            "httpGet"
        ]["path"] = "/wrong"
    elif broken == "secret":
        documents.append({"kind": "Secret", "metadata": {"name": "forbidden"}})
    elif broken == "lease":
        objects[("ConfigMap", "verdify-config")]["data"]["VERDIFY_WRITER_LEASE_ENABLED"] = "0"
    with pytest.raises(
        AssertionError,
        match={
            "replicas": "exactly one ingestor replica",
            "rolling": "writer uses Recreate",
            "image_tag": "immutable origin image",
            "config_identity": "explicit config identity",
            "readiness": "MCP authenticated readiness surface",
            "secret": "production must not render Secret values",
            "lease": "writer Lease fence enabled",
        }[broken],
    ):
        validate_delivery(documents)


@pytest.mark.parametrize("backend", [None, "kube", "invalid"])
def test_smoke_db_transport_does_not_select_destroyed_vm_from_installed_docker(monkeypatch, backend):
    from types import SimpleNamespace

    import conftest

    monkeypatch.delenv("VERDIFY_DB_QUERY_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_DB_BACKEND", raising=False)
    if backend is not None:
        monkeypatch.setenv("VERDIFY_DB_BACKEND", backend)
    monkeypatch.setattr(conftest.shutil, "which", lambda _name: "/fixture/installed")
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="1", stderr="")

    monkeypatch.setattr(conftest.subprocess, "run", record)
    if backend == "invalid":
        with pytest.raises(ValueError, match="unsupported VERDIFY_DB_BACKEND"):
            conftest.db_query("SELECT 1")
        assert not calls
    else:
        assert conftest.db_query("SELECT 1") == "1"
        assert calls[0][:2] == ["kubectl", "exec"]
        assert calls[0][calls[0].index("-c") + 1] == "postgres"


@pytest.mark.parametrize(
    "dsn",
    [
        None,
        "postgresql://fixture@verdify-db/verdify_test_probe",
        "postgresql://fixture@localhost/verdify",
        "postgresql://fixture@localhost/verdify_test_probe",
    ],
)
def test_mutation_fixture_never_discovers_retired_host_or_production_credentials(monkeypatch, dsn):
    from conftest import isolated_mutation_test_dsn

    monkeypatch.delenv("VERDIFY_ISOLATED_TEST_DSN", raising=False)
    if dsn is not None:
        monkeypatch.setenv("VERDIFY_ISOLATED_TEST_DSN", dsn)
    if dsn == "postgresql://fixture@localhost/verdify_test_probe":
        assert isolated_mutation_test_dsn() == dsn
    else:
        with pytest.raises(ValueError, match="mutation"):
            isolated_mutation_test_dsn()
