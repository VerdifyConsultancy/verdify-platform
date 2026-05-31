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
