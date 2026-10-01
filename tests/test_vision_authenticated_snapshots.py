"""Offline regressions for scoped TLS snapshots and truthful Vision job exits."""

import importlib.util
import ssl
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
JPEG = b"\xff\xd8" + b"x" * 1100 + b"\xff\xd9"


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("vision_snapshot", ROOT / "scripts/frigate-snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = tmp_path / "token"
    token.write_text("offline-fixture-token")
    monkeypatch.setattr(module, "TOKEN_FILE", token)
    monkeypatch.setattr(module, "VAULT_DIR", tmp_path / "snap")
    monkeypatch.setattr(module, "FRIGATE_URL", "https://frigate.frigate.svc.cluster.local:8971")
    monkeypatch.setattr(module, "TLS_SERVER_NAME", "cameras.vallery.net")
    response = Mock(status=200)
    response.getheader.return_value = "image/jpeg"
    response.read.return_value = JPEG
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module, "SnapshotHTTPSConnection", factory)
    module.test_factory, module.test_connection, module.test_response = factory, connection, response
    return module


def test_authenticated_verified_single_route(snapshot):
    assert snapshot.capture_snapshot("greenhouse_1")
    args, kwargs = snapshot.test_factory.call_args
    assert args == ("frigate.frigate.svc.cluster.local", 8971)
    assert kwargs["context"].check_hostname
    assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
    method, path = snapshot.test_connection.request.call_args.args
    headers = snapshot.test_connection.request.call_args.kwargs["headers"]
    assert (method, path) == ("GET", "/api/vision/greenhouse_1/latest.jpg")
    assert headers["Host"] == "cameras.vallery.net"
    assert headers["Authorization"] == "Bearer offline-fixture-token"
    snapshot.test_connection.close.assert_called_once()
    assert next(snapshot.VAULT_DIR.rglob("*.jpg")).read_bytes() == JPEG


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 500])
def test_status_fails_without_redirect_or_auth_fallback(snapshot, status):
    snapshot.test_response.status = status
    assert not snapshot.capture_snapshot("greenhouse_2")
    assert snapshot.test_connection.request.call_count == 1
    assert not list(snapshot.VAULT_DIR.rglob("*.jpg"))
    snapshot.test_connection.close.assert_called_once()


@pytest.mark.parametrize(
    "data, content_type",
    [
        (JPEG, "text/html"),
        (b"x" * 2000, "image/jpeg"),
        (b"\xff\xd8\xff\xd9", "image/jpeg"),
        (JPEG[:-2], "image/jpeg"),
        (b"\xff\xd8" + b"x" * (8 * 1024 * 1024) + b"\xff\xd9", "image/jpeg"),
    ],
)
def test_invalid_response_never_becomes_snapshot(snapshot, data, content_type):
    snapshot.test_response.read.return_value = data
    snapshot.test_response.getheader.return_value = content_type
    assert not snapshot.capture_snapshot("greenhouse_1")
    assert not list(snapshot.VAULT_DIR.rglob("*.jpg"))


@pytest.mark.parametrize(
    "endpoint", ["http://frigate:5000", "https://u:p@frigate", "https://frigate/api", "https://frigate?q=1"]
)
def test_invalid_origin_fails_closed(snapshot, endpoint):
    snapshot.FRIGATE_URL = endpoint
    assert not snapshot.capture_snapshot("greenhouse_1")
    snapshot.test_factory.assert_not_called()


def test_arbitrary_camera_rejected_before_auth(snapshot):
    assert not snapshot.capture_snapshot("../other_camera")
    snapshot.test_factory.assert_not_called()


def test_exception_does_not_log_credentials(snapshot, caplog):
    snapshot.test_connection.getresponse.side_effect = RuntimeError("offline-fixture-token private body")
    assert not snapshot.capture_snapshot("greenhouse_1")
    assert "offline-fixture-token" not in caplog.text
    assert "private body" not in caplog.text
    snapshot.test_connection.close.assert_called_once()


@pytest.mark.parametrize(
    "captured, analysis_code, expected",
    [
        ([False, False], 0, 1),
        ([True, False], 0, 1),
        ([True, True], 1, 1),
        ([True, True], 0, 0),
    ],
)
def test_capture_and_analysis_determine_job_exit(snapshot, monkeypatch, captured, analysis_code, expected):
    capture = Mock(side_effect=captured)
    analysis = Mock(return_value=SimpleNamespace(returncode=analysis_code))
    monkeypatch.setattr(snapshot, "capture_snapshot", capture)
    monkeypatch.setattr(snapshot.subprocess, "run", analysis)
    monkeypatch.setattr(snapshot.sys, "argv", ["snapshot"])
    assert snapshot.main() == expected
    assert analysis.call_count == int(any(captured))


def test_analysis_exception_fails_job(snapshot, monkeypatch, caplog):
    monkeypatch.setattr(snapshot, "capture_snapshot", Mock(return_value=True))
    monkeypatch.setattr(snapshot.subprocess, "run", Mock(side_effect=RuntimeError("secret body")))
    monkeypatch.setattr(snapshot.sys, "argv", ["snapshot"])
    assert snapshot.main() == 1
    assert "secret body" not in caplog.text


def test_tls_sni_uses_provider_identity(monkeypatch):
    spec = importlib.util.spec_from_file_location("vision_tls", ROOT / "scripts/frigate-snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TLS_SERVER_NAME = "cameras.vallery.net"
    connection = module.SnapshotHTTPSConnection("frigate.frigate.svc.cluster.local", 8971)
    sock, context = Mock(), Mock()
    connection.sock, connection._context = sock, context
    monkeypatch.setattr(module.http.client.HTTPConnection, "connect", Mock())
    connection.connect()
    context.wrap_socket.assert_called_once_with(sock, server_hostname="cameras.vallery.net")


@pytest.mark.asyncio
@pytest.mark.parametrize("results, expected", [([None, None], 1), ([{}, None], 1), ([{}, {}], 0)])
async def test_analyzer_exit_requires_both_results(monkeypatch, tmp_path, results, expected):
    from unittest.mock import AsyncMock

    monkeypatch.setitem(__import__("sys").modules, "ai_config", SimpleNamespace(ai=Mock()))
    spec = importlib.util.spec_from_file_location("vision_analyzer", ROOT / "scripts/analyze-greenhouse-snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "argv", ["analyzer", "--date", "2026-10-01"])
    monkeypatch.setattr(module, "VAULT_DIR", tmp_path)
    folder = tmp_path / "2026-10-01"
    folder.mkdir()
    for camera in ("greenhouse_1", "greenhouse_2"):
        (folder / f"{camera}_1200.jpg").write_bytes(JPEG)
    connection = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(module.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(module, "get_db_url", lambda: "offline")
    monkeypatch.setattr(module, "analyze_image", AsyncMock(side_effect=results))
    assert await module.main() == expected
    connection.close.assert_awaited_once()
