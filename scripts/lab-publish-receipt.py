#!/usr/bin/env python3
"""Atomic, redacted publication receipt on the shared Lab cache PVC."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

_CLASS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest_file_hashes(files: dict[str, str]) -> str:
    canonical = json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _manifest_hash(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data["files"]
    if (
        data["version"] != 1
        or not isinstance(files, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for key, value in files.items()
        )
    ):
        raise ValueError("invalid committed publication manifest")
    return _digest_file_hashes(files)


def _tree_hash(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        raise ValueError("invalid publication tree")
    files: dict[str, str] = {}
    for root, _dirs, names in os.walk(path, followlinks=False):
        for name in names:
            file = Path(root) / name
            if not file.is_file():
                continue
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            files[file.relative_to(path).as_posix()] = digest.hexdigest()
    return _digest_file_hashes(files)


def _load(path: Path) -> dict:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "schema_version": 1,
            "last_attempt": None,
            "last_success": None,
            "last_contention_at_utc": None,
            "served_public_sha256": None,
        }
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise ValueError("invalid publication receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("invalid publication receipt") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid publication receipt")
    return value


def _store(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".publish-status.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", choices=("start", "failure", "success", "contention"))
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--class", dest="failure_class")
    parser.add_argument("--public-dir", type=Path)
    parser.add_argument("--content-manifest", type=Path)
    parser.add_argument("--public-manifest", type=Path)
    parser.add_argument("--source-uri")
    parser.add_argument("--freshness-seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.event == "failure" and (not args.failure_class or not _CLASS.fullmatch(args.failure_class)):
        parser.error("failure requires a fixed class label")
    if args.event == "success" and (
        args.content_manifest is None
        or args.public_manifest is None
        or not args.source_uri
        or not 60 <= args.freshness_seconds <= 86400
    ):
        parser.error("success requires manifests, source identity and bounded freshness")

    state_dir = args.state_dir
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise ValueError("invalid publication state directory")
    status_path = state_dir / "publish-status.json"
    lock_path = state_dir / ".publish-status.lock"
    # A separate receipt lock lets a skipped contender append its classification
    # without interfering with the long-held cache/publication lock.
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_metadata = os.fstat(lock_fd)
    if not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_uid != os.getuid() or lock_metadata.st_nlink != 1:
        os.close(lock_fd)
        raise ValueError("invalid publication receipt lock")
    with os.fdopen(lock_fd, "r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = _load(status_path)
        now = _now()
        if args.event == "contention":
            value["last_contention_at_utc"] = _stamp(now)
        elif args.event == "start":
            value["last_attempt"] = {"at_utc": _stamp(now), "outcome": "running", "failure_class": None}
        elif args.event == "failure":
            if args.public_dir is not None and args.public_dir.is_dir() and not args.public_dir.is_symlink():
                value["served_public_sha256"] = _tree_hash(args.public_dir)
            value["last_attempt"] = {
                "at_utc": _stamp(now),
                "outcome": "failed",
                "failure_class": args.failure_class,
            }
        else:
            public_hash = _manifest_hash(args.public_manifest)
            value["last_success"] = {
                "at_utc": _stamp(now),
                "fresh_until_utc": _stamp(now + timedelta(seconds=args.freshness_seconds)),
                "content_sha256": _manifest_hash(args.content_manifest),
                "public_sha256": public_hash,
                "source": "s3_content_prefix",
                "source_identity_sha256": "sha256:" + hashlib.sha256(args.source_uri.encode()).hexdigest(),
            }
            value["served_public_sha256"] = public_hash
            value["last_attempt"] = {"at_utc": _stamp(now), "outcome": "succeeded", "failure_class": None}
        _store(status_path, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
