"""Credential redaction and fail-closed invalid-password probe boundaries."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "component_probe", Path(__file__).resolve().parents[1] / "scripts/probe-component-runtime-login.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
REQUEST = {
    "role": "executor",
    "login": "verdify_experiment_v2_component_executor_login",
    "sql": "SELECT attestation",
    "duty": "executor",
    "functions": [],
}


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", REQUEST["login"])
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "private-test-sentinel")
    monkeypatch.setenv("DB_HOST", "verdify-db")


def test_rejects_endpoint_accepting_invalid_password(credentials, monkeypatch):
    class AcceptedInvalid:
        async def close(self, **kwargs):
            pass

    async def connect(**kwargs):
        return AcceptedInvalid()

    monkeypatch.setattr(probe.asyncpg, "connect", connect)
    result = asyncio.run(probe.probe(REQUEST))
    assert result["qualified"] is False
    assert result["failure_type"] == "ValueError"


def test_auth_failure_never_serializes_exception_or_secret(credentials, monkeypatch):
    async def connect(**kwargs):
        if kwargs["password"].startswith("deliberately-invalid"):
            raise probe.asyncpg.InvalidPasswordError()
        raise RuntimeError("private-test-sentinel: credential-bearing transport error")

    monkeypatch.setattr(probe.asyncpg, "connect", connect)
    result = asyncio.run(probe.probe(REQUEST))
    assert result["invalid_password_rejected"] is True
    assert result["failure_type"] == "RuntimeError" and result["qualified"] is False
    assert "private-test-sentinel" not in json.dumps(result)
    assert "credential-bearing" not in json.dumps(result)


def test_wrong_consumer_identity_fails_before_connection(credentials, monkeypatch):
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", "verdify")

    async def forbidden(**kwargs):
        pytest.fail("wrong identity attempted a connection")

    monkeypatch.setattr(probe.asyncpg, "connect", forbidden)
    result = asyncio.run(probe.probe(REQUEST))
    assert result["qualified"] is False and result["failure_type"] == "ValueError"
