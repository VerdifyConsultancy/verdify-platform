"""The containment qualifier resolves its pinned PGDG key without CI egress."""

import hashlib
import importlib.util
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA256 = "0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76"


def load():
    spec = importlib.util.spec_from_file_location(
        "qualify_component_containment", ROOT / "scripts/qualify-component-containment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit):
        return self.body[:limit]


def test_vendored_pgdg_key_matches_the_pinned_hash():
    m = load()
    assert m.PGDG_KEY_SHA256 == PINNED_SHA256
    assert m.PGDG_KEY_FILE == ROOT / "db/qualification/pgdg-ACCC4CF8.asc"
    assert hashlib.sha256(m.PGDG_KEY_FILE.read_bytes()).hexdigest() == PINNED_SHA256


def test_vendored_key_needs_no_network(monkeypatch):
    m = load()

    def no_network(*args, **kwargs):
        raise AssertionError("fetched the PGDG key although the vendored copy exists")

    monkeypatch.setattr(m.urllib.request, "urlopen", no_network)
    assert m.pgdg_signing_key() == m.PGDG_KEY_FILE.read_bytes()


@pytest.mark.parametrize("failures", [2, 3])
def test_missing_key_fetch_makes_three_backed_off_attempts(monkeypatch, tmp_path, failures):
    m = load()
    calls, sleeps = [], []

    def flaky(url, timeout):
        calls.append((url, timeout))
        if len(calls) <= failures:
            raise urllib.error.URLError(TimeoutError("_ssl.c:1015: The handshake operation timed out"))
        return Response(m.PGDG_KEY_FILE.read_bytes())

    monkeypatch.setattr(m.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(m.time, "sleep", sleeps.append)
    if failures < 3:
        assert m.pgdg_signing_key(tmp_path / "absent.asc") == m.PGDG_KEY_FILE.read_bytes()
    else:
        with pytest.raises(urllib.error.URLError):
            m.pgdg_signing_key(tmp_path / "absent.asc")
    assert calls == [(m.PGDG_KEY_URL, 30)] * 3
    assert sleeps == [5, 10]


def test_fetched_or_vendored_key_still_has_to_match_the_pin(monkeypatch, tmp_path):
    m = load()
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda url, timeout: Response(b"not the PGDG key"))
    with pytest.raises(RuntimeError, match="pinned authority"):
        m.pgdg_signing_key(tmp_path / "absent.asc")
    tampered = tmp_path / "tampered.asc"
    tampered.write_bytes(m.PGDG_KEY_FILE.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="pinned authority"):
        m.pgdg_signing_key(tampered)
