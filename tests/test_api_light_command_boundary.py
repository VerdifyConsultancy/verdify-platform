"""Authenticated command requests cannot fabricate observed light runtime."""

import asyncio

import httpx
import pytest
from test_api_public_output_policy import load_api


class NoDatabaseAccess:
    calls = 0

    def acquire(self):
        self.calls += 1
        raise TimeoutError("disabled command must not contact a database")


@pytest.fixture
def api(monkeypatch):
    result = load_api()
    monkeypatch.setenv(result.WRITE_API_KEY_ENV, "test-only-operator-key")
    monkeypatch.delenv(result.ALLOW_UNAUTHENTICATED_WRITES_ENV, raising=False)
    result.pool = NoDatabaseAccess()
    return result


def post(api, path, key="test-only-operator-key"):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            headers = {"X-Verdify-API-Key": key} if key else {}
            return await client.post(path, headers=headers)

    return asyncio.run(request())


@pytest.mark.parametrize("greenhouse", ["vallery", "other-greenhouse"])
@pytest.mark.parametrize("circuit", ["main", "grow"])
@pytest.mark.parametrize("action", ["on", "off"])
def test_valid_authenticated_request_is_unavailable_without_any_observation(api, greenhouse, circuit, action):
    response = post(api, f"/api/v1/greenhouses/{greenhouse}/lights/{circuit}/{action}")
    assert response.status_code == 501
    assert response.json() == {
        "detail": {
            "code": "light_control_unavailable",
            "message": "Direct light control is unavailable. No command was accepted.",
        }
    }
    assert api.pool.calls == 0


@pytest.mark.parametrize("key", [None, "wrong-operator-key"])
def test_missing_or_wrong_auth_is_still_rejected_before_command_handling(api, key):
    response = post(api, "/api/v1/greenhouses/vallery/lights/main/on", key=key)
    assert response.status_code == 403
    assert api.pool.calls == 0


@pytest.mark.parametrize("suffix", ["unknown/on", "main/invalid", "grow/true"])
def test_invalid_circuit_or_action_remains_rejected_without_any_write(api, suffix):
    response = post(api, "/api/v1/greenhouses/vallery/lights/" + suffix)
    assert response.status_code == 400
    assert api.pool.calls == 0


def test_read_only_contract_discloses_unsupported_status(api):
    operation = api.app.openapi()["paths"]["/api/v1/greenhouses/{greenhouse_id}/lights/{circuit}/{action}"]["post"]
    assert "501" in operation["responses"]
    assert "200" not in operation["responses"]
