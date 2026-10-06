"""Transient CNI readiness must not become a credential or permission retry."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "restored_duty", Path(__file__).resolve().parents[1] / "scripts/qualify-restored-runtime-duties.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_readiness_retries_only_transport_without_sending_credentials(monkeypatch):
    calls = []

    class Writer:
        def close(self):
            calls.append("close")

        async def wait_closed(self):
            calls.append("closed")

        def write(self, _):
            raise AssertionError("readiness must never send authentication or SQL")

    async def connect(host, port):
        calls.append((host, port))
        if len(calls) == 1:
            raise ConnectionRefusedError
        return object(), Writer()

    monkeypatch.setattr(probe.asyncio, "open_connection", connect)
    result = asyncio.run(probe.wait_target_transport("qualified-target", timeout=1))
    assert result == ["ConnectionRefusedError", "connected"]
    assert calls == [("qualified-target", 5432), ("qualified-target", 5432), "close", "closed"]


def test_expired_transport_budget_refuses_before_authentication():
    with pytest.raises(TimeoutError):
        asyncio.run(probe.wait_target_transport("qualified-target", timeout=0))
