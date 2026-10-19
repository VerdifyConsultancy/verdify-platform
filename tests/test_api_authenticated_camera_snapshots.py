"""Public camera recovery uses the existing scoped TLS provider, never admin ports."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import yaml
from test_api_public_output_policy import load_api

ROOT = Path(__file__).resolve().parents[1]
JPEG = b"\xff\xd8" + b"x" * 1100 + b"\xff\xd9"


@pytest.fixture
def api(monkeypatch, tmp_path):
    module = load_api()
    token = tmp_path / "token"
    token.write_text("offline-camera-token")
    monkeypatch.setenv("VERDIFY_FRIGATE_TOKEN_FILE", str(token))
    monkeypatch.setenv(module.FRIGATE_BASE_URL_ENV, "https://frigate.frigate.svc.cluster.local:8971")
    monkeypatch.setenv("VERDIFY_FRIGATE_TLS_SERVER_NAME", "cameras.vallery.net")
    response = Mock(status=200)
    response.read.return_value = JPEG
    response.getheader.return_value = "image/jpeg"
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module, "_CameraHTTPSConnection", factory)
    module.test_factory, module.test_connection, module.test_response = factory, connection, response
    return module


@pytest.mark.parametrize("camera", ["greenhouse_1", "greenhouse_2"])
@pytest.mark.parametrize("height", [120, 1080])
def test_fixed_camera_scope_and_legacy_height_compatibility(api, camera, height):
    assert api._fetch_camera_snapshot_sync(camera, height) == JPEG
    api.test_factory.assert_called_once_with(
        "frigate.frigate.svc.cluster.local", 8971, tls_server_name="cameras.vallery.net"
    )
    method, path = api.test_connection.request.call_args.args
    assert (method, path) == ("GET", f"/api/vision/{camera}/latest.jpg")
    headers = api.test_connection.request.call_args.kwargs["headers"]
    assert headers["Host"] == "cameras.vallery.net"
    assert headers["Authorization"] == "Bearer offline-camera-token"
    assert "Cookie" not in headers
    api.test_response.read.assert_called_once_with(api.CAMERA_MAX_JPEG_BYTES + 1)
    api.test_connection.close.assert_called_once()


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 500, 503])
def test_provider_denial_has_no_redirect_or_fallback(api, status):
    api.test_response.status = status
    with pytest.raises(ValueError, match="rejected"):
        api._fetch_camera_snapshot_sync("greenhouse_1", 1080)
    assert api.test_connection.request.call_count == 1
    api.test_connection.close.assert_called_once()


@pytest.mark.parametrize(
    "endpoint",
    ["http://frigate:5000", "https://u:p@frigate", "https://frigate/api", "https://frigate?q=1", "https://frigate#x"],
)
def test_invalid_provider_origin_fails_before_credential_transmission(api, monkeypatch, endpoint):
    monkeypatch.setenv(api.FRIGATE_BASE_URL_ENV, endpoint)
    with pytest.raises(ValueError, match="HTTPS origin"):
        api._fetch_camera_snapshot_sync("greenhouse_1", 1080)
    api.test_factory.assert_not_called()


@pytest.mark.parametrize("token", ["", "two tokens", "bad\r\nHeader: x"])
def test_invalid_token_never_reaches_network(api, monkeypatch, tmp_path, token):
    p = tmp_path / "invalid"
    p.write_text(token)
    monkeypatch.setenv("VERDIFY_FRIGATE_TOKEN_FILE", str(p))
    with pytest.raises(ValueError, match="credential"):
        api._fetch_camera_snapshot_sync("greenhouse_1", 1080)
    api.test_factory.assert_not_called()


@pytest.mark.parametrize("camera,height", [("../admin", 1080), ("other", 1080), ("greenhouse_1", 119)])
def test_invalid_camera_or_height_rejected_without_network(api, camera, height):
    with pytest.raises(ValueError):
        api._fetch_camera_snapshot_sync(camera, height)
    api.test_factory.assert_not_called()


@pytest.mark.parametrize(
    "data,kind",
    [
        (JPEG, "text/html"),
        (b"x" * 2000, "image/jpeg"),
        (JPEG[:-2], "image/jpeg"),
        (b"\xff\xd8\xff\xd9", "image/jpeg"),
        (b"\xff\xd8" + b"x" * (8 * 1024 * 1024) + b"\xff\xd9", "image/jpeg"),
    ],
)
def test_invalid_or_oversized_response_fails_closed(api, data, kind):
    api.test_response.read.return_value = data
    api.test_response.getheader.return_value = kind
    with pytest.raises(ValueError, match="invalid JPEG"):
        api._fetch_camera_snapshot_sync("greenhouse_1", 1080)
    api.test_connection.close.assert_called_once()


def test_provider_exception_remains_generic_at_public_boundary(api, monkeypatch):
    monkeypatch.setattr(api, "_fetch_camera_snapshot_sync", Mock(side_effect=RuntimeError("private-token-body")))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.get("/api/v1/public/cameras/greenhouse_1/latest.jpg")

    response = asyncio.run(request())
    assert response.status_code == 503
    assert response.json() == {"detail": "Camera snapshot unavailable"}
    assert "private-token-body" not in response.text


def test_tls_connection_verifies_provider_identity(monkeypatch):
    api = load_api()
    connection = api._CameraHTTPSConnection(
        "frigate.frigate.svc.cluster.local", 8971, tls_server_name="cameras.vallery.net"
    )
    assert connection._context.check_hostname
    assert connection._context.verify_mode == api.ssl.CERT_REQUIRED
    assert connection.timeout == 15
    sock, context = Mock(), Mock()
    connection.sock, connection._context = sock, context
    monkeypatch.setattr(api.http.client.HTTPConnection, "connect", Mock())
    connection.connect()
    context.wrap_socket.assert_called_once_with(sock, server_hostname="cameras.vallery.net")


def test_api_mounts_only_the_existing_scoped_camera_credential():
    objects = list(yaml.safe_load_all((ROOT / "deploy/k8s/base/api-deployment.yaml").read_text()))
    spec = next(o for o in objects if o["kind"] == "Deployment")["spec"]["template"]["spec"]
    api = next(c for c in spec["containers"] if c["name"] == "api")
    env = {e["name"]: e for e in api["env"]}
    assert env["VERDIFY_FRIGATE_PUBLIC_BASE_URL"]["value"] == "https://frigate.frigate.svc.cluster.local:8971"
    assert env["VERDIFY_FRIGATE_TOKEN_FILE"]["value"] == "/vfrigate/token"
    assert next(v for v in spec["volumes"] if v["name"] == "frigate-auth")["secret"] == {
        "secretName": "verdify-frigate-vision-auth",
        "defaultMode": 0o440,
        "items": [{"key": "token", "path": "token"}],
    }
    assert next(v for v in api["volumeMounts"] if v["name"] == "frigate-auth")["readOnly"]
