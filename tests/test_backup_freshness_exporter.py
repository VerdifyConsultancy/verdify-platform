"""Execute the deployed inline exporter against bounded local filesystem fixtures."""

from __future__ import annotations

import ast
import io
import os
import threading
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(
    os.environ.get("BACKUP_EXPORTER_MANIFEST", ROOT / "deploy/k8s/components/db-backup/backup-freshness-exporter.yaml")
)


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    doc = next(doc for doc in yaml.safe_load_all(MANIFEST.read_text()) if doc["kind"] == "Deployment")
    source = doc["spec"]["template"]["spec"]["containers"][0]["command"][2]
    tree = ast.parse(source)
    # Keep the actual scanner/handler; omit only server startup for local tests.
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.With)
            or (isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'")
        )
    ]
    module = types.ModuleType("backup_exporter")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    exec(compile(tree, str(MANIFEST), "exec"), module.__dict__)  # noqa: S102 - execute tracked deployed source
    return module


def response(exporter):
    handler = exporter.H.__new__(exporter.H)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda code: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_GET()
    return {
        name: float(value)
        for line in handler.wfile.getvalue().decode().splitlines()
        if not line.startswith("#")
        for name, value in [line.split()]
    }


def test_blocked_scan_cannot_block_or_multiply_request_work(exporter, monkeypatch):
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def blocked_scan():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return (123.0, 42, 1, 1)

    monkeypatch.setattr(exporter, "scan", blocked_scan)
    # Old source scans in do_GET; new source has one background refresh owner.
    scanner = None
    if hasattr(exporter, "refresh"):
        scanner = threading.Thread(target=exporter.refresh)
        scanner.start()
        assert entered.wait(1)
    responses = []

    def requests():
        for _ in range(3):
            responses.append(response(exporter))
        done.set()

    requester = threading.Thread(target=requests)
    requester.start()
    try:
        assert done.wait(0.5), "HTTP response blocked behind filesystem scan"
        assert len(calls) == 1, "HTTP handlers initiated additional filesystem scans"
        assert all(row["verdify_db_backup_exporter_up"] == 0 for row in responses)
        assert all(row["verdify_db_backup_last_success_timestamp_seconds"] == 0 for row in responses)
    finally:
        release.set()
        requester.join(2)
        if scanner:
            scanner.join(2)


def test_cache_expiry_cannot_refresh_backup_timestamp(exporter, tmp_path, monkeypatch):
    dump = tmp_path / "verdify-2026-10-17.dump"
    dump.write_bytes(b"PGDMPfixture")
    os.utime(dump, (123.0, 123.0))
    clock = [1000.0]
    monkeypatch.setattr(exporter.time, "monotonic", lambda: clock[0])
    exporter.refresh()
    current = response(exporter)
    assert current["verdify_db_backup_last_success_timestamp_seconds"] == 123
    assert current["verdify_db_backup_dump_size_bytes"] == 12
    clock[0] += exporter.MAX_SCAN_AGE_SECONDS
    expired = response(exporter)
    assert expired["verdify_db_backup_exporter_up"] == 0
    assert expired["verdify_db_backup_last_success_timestamp_seconds"] == 0
    assert expired["verdify_db_backup_dump_size_bytes"] == 0


def test_failed_scan_cannot_retain_previous_success(exporter, tmp_path, monkeypatch):
    (tmp_path / "verdify-old.dump").write_bytes(b"old")
    exporter.refresh()
    assert response(exporter)["verdify_db_backup_exporter_up"] == 1

    def denied(*args):
        raise PermissionError("fixture")

    monkeypatch.setattr(exporter.glob, "glob", denied)
    exporter.refresh()
    failed = response(exporter)
    assert failed["verdify_db_backup_exporter_up"] == 0
    assert failed["verdify_db_backup_last_success_timestamp_seconds"] == 0


def test_empty_and_partial_dumps_never_report_backup_success(exporter, tmp_path):
    (tmp_path / "verdify-incomplete.dump.partial").write_bytes(b"partial")
    exporter.refresh()
    empty = response(exporter)
    assert empty["verdify_db_backup_exporter_up"] == 1
    assert empty["verdify_db_backup_last_success_timestamp_seconds"] == 0
    assert empty["verdify_db_backup_dump_count"] == 0


def test_startup_denies_unobserved_backup(exporter):
    initial = response(exporter)
    assert initial["verdify_db_backup_exporter_up"] == 0
    assert initial["verdify_db_backup_last_success_timestamp_seconds"] == 0
