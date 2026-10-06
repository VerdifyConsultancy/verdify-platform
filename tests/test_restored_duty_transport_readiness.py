"""Transient CNI readiness must not become a credential or permission retry."""

import asyncio
import errno
import importlib.util
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "restored_duty", Path(__file__).resolve().parents[1] / "scripts/qualify-restored-runtime-duties.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionRefusedError(errno.ECONNREFUSED, "transport unavailable"),
        socket.gaierror(socket.EAI_AGAIN, "resolver not ready"),
        ConnectionResetError(errno.ECONNRESET, "transport reset"),
        OSError(errno.EHOSTUNREACH, "route not ready"),
    ],
)
def test_readiness_retries_only_transport_without_sending_credentials(monkeypatch, failure):
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
            raise failure
        return object(), Writer()

    monkeypatch.setattr(probe.asyncio, "open_connection", connect)
    result = asyncio.run(probe.wait_target_transport("qualified-target", timeout=1))
    assert result == [type(failure).__name__, "connected"]
    assert calls == [("qualified-target", 5432), ("qualified-target", 5432), "close", "closed"]


def test_expired_transport_budget_refuses_before_authentication():
    with pytest.raises(TimeoutError):
        asyncio.run(probe.wait_target_transport("qualified-target", timeout=0))


def test_unreachable_transport_exhausts_budget_without_authentication(monkeypatch):
    calls = []

    async def connect(host, port):
        calls.append((host, port))
        raise OSError(errno.EHOSTUNREACH, "route unavailable")

    monkeypatch.setattr(probe.asyncio, "open_connection", connect)
    with pytest.raises(TimeoutError, match="bounded target transport readiness expired"):
        asyncio.run(probe.wait_target_transport("qualified-target", timeout=0.01))
    assert calls and all(call == ("qualified-target", 5432) for call in calls)
