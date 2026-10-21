"""Per-pod cache boundary fixtures; no cluster, public service or large tree."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "serving_cache", Path(__file__).resolve().parents[1] / "scripts/sync-lab-serving-cache.py"
)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def body(files):
    return json.dumps({"version": 1, "files": files}).encode()


@pytest.mark.parametrize(
    "name", ["../secret", "/absolute", "x/../secret", "x//secret", "x/./secret", "x\\secret", "bad\x00name", ""]
)
def test_manifest_rejects_escape_and_ambiguous_paths(name):
    with pytest.raises(ValueError):
        cache.manifest(body({"index.html": "0" * 64, name: "1" * 64}))


@pytest.mark.parametrize(
    "raw",
    [b"{}", b'{"version":2,"files":{}}', body({"index.html": "not-a-hash"}), body({"missing-index.html": "0" * 64})],
)
def test_manifest_fails_closed(raw):
    with pytest.raises(ValueError):
        cache.manifest(raw)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "publisher"
    root.mkdir(mode=0o700)
    live = root / "public"
    live.mkdir()
    (live / "index.html").write_bytes(b"previous safe generation")
    data = b"new safe generation"
    files = {"index.html": hashlib.sha256(data).hexdigest()}
    raw = body(files)
    public_hash = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
    )
    status = json.dumps({"schema_version": 1, "served_public_sha256": public_hash}).encode()
    monkeypatch.setattr(cache, "fetch", lambda base, name, limit: raw if name == "manifest.json" else status)

    def transfer(base, name, target, expected):
        target.write_bytes(data)
        return len(data)

    monkeypatch.setattr(cache, "copy_file", transfer)
    return root, raw, status


def test_failed_transfer_preserves_the_previous_generation(prepared, monkeypatch):
    root, _, _ = prepared

    def fail(*args):
        raise ValueError("hash mismatch")

    monkeypatch.setattr(cache, "copy_file", fail)
    with pytest.raises(ValueError):
        cache.sync_once(root, "http://fixture")
    assert (root / "public/index.html").read_bytes() == b"previous safe generation"
    assert not list(root.glob(".candidate-*"))


def test_generation_change_preserves_previous_generation(prepared, monkeypatch):
    root, raw, status = prepared
    reads = 0

    def fetch(base, name, limit):
        nonlocal reads
        if name == "manifest.json":
            reads += 1
            return raw if reads == 1 else raw + b" "
        return status

    monkeypatch.setattr(cache, "fetch", fetch)
    with pytest.raises(ValueError, match="changed during transfer"):
        cache.sync_once(root, "http://fixture")
    assert (root / "public/index.html").read_bytes() == b"previous safe generation"


def test_public_guard_failure_preserves_previous_generation(prepared, monkeypatch):
    root, _, _ = prepared
    monkeypatch.setattr(cache.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    with pytest.raises(ValueError, match="public validation"):
        cache.sync_once(root, "http://fixture")
    assert (root / "public/index.html").read_bytes() == b"previous safe generation"


def test_receipt_must_match_the_committed_tree(prepared, monkeypatch):
    root, raw, _ = prepared
    monkeypatch.setattr(
        cache,
        "fetch",
        lambda base, name, limit: (
            raw if name == "manifest.json" else b'{"schema_version":1,"served_public_sha256":"wrong"}'
        ),
    )
    with pytest.raises(ValueError, match="receipt"):
        cache.sync_once(root, "http://fixture")
    assert (root / "public/index.html").read_bytes() == b"previous safe generation"


def test_unchanged_tree_refreshes_receipt_without_download_or_rescan(prepared, monkeypatch):
    root, raw, status = prepared
    state = root / "state"
    state.mkdir(mode=0o700)
    (state / "serving-manifest.json").write_bytes(raw)
    monkeypatch.setattr(cache, "copy_file", lambda *a: pytest.fail("unchanged tree downloaded"))
    monkeypatch.setattr(cache.subprocess, "run", lambda *a, **k: pytest.fail("unchanged tree rescanned"))
    assert cache.sync_once(root, "http://fixture") is False
    assert (state / "publish-status.json").read_bytes() == status


@pytest.mark.skipif(
    sys.platform != "linux", reason="descriptor-bound renameat2 promotion is qualified by native Linux CI"
)
def test_valid_generation_promotes_through_the_actual_public_guard(prepared):
    root, raw, status = prepared
    assert cache.sync_once(root, "http://fixture") is True
    assert (root / "public/index.html").read_bytes() == b"new safe generation"
    assert (root / "state/serving-manifest.json").read_bytes() == raw
    assert (root / "state/publish-status.json").read_bytes() == status
    assert not list(root.glob(".candidate-*"))
