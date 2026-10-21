#!/usr/bin/env python3
"""Mirror one hash-committed public generation into a private per-pod cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

MAX_MANIFEST = 16 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_TREE = 1536 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def manifest(raw: bytes) -> dict[str, str]:
    if len(raw) > MAX_MANIFEST:
        raise ValueError("manifest too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("files"), dict):
        raise ValueError("invalid manifest")
    files = value["files"]
    if not files or len(files) > 100000 or "index.html" not in files:
        raise ValueError("invalid public generation")
    for name, digest in files.items():
        if not isinstance(name, str) or not name or len(name) > 4096 or "\\" in name or "\x00" in name:
            raise ValueError("unsafe path")
        path = PurePosixPath(name)
        if path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")) or path.as_posix() != name:
            raise ValueError("unsafe path")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("invalid hash")
    return files


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def fetch(base: str, name: str, limit: int) -> bytes:
    url = base.rstrip("/") + "/" + urllib.parse.quote(name, safe="/")
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(url, timeout=30) as response:
        if response.status != 200:
            raise ValueError("origin response")
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("origin size limit")
        return body


def copy_file(base: str, name: str, target: Path, expected: str) -> int:
    opener = urllib.request.build_opener(NoRedirect)
    h = hashlib.sha256()
    count = 0
    started = time.monotonic()
    with opener.open(base.rstrip("/") + "/public/" + urllib.parse.quote(name, safe="/"), timeout=30) as source:
        if source.status != 200:
            raise ValueError("origin response")
        with target.open("xb") as output:
            while part := source.read(1024 * 1024):
                if time.monotonic() - started > 120:
                    raise ValueError("file transfer deadline")
                count += len(part)
                if count > MAX_FILE:
                    raise ValueError("file size limit")
                output.write(part)
                h.update(part)
    if h.hexdigest() != expected:
        raise ValueError("generation hash mismatch")
    return count


def save_state(state: Path, name: str, body: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".serving-", dir=state)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publication_status(base: str, files: dict[str, str]) -> bytes:
    status = fetch(base, "publication-status.json", 65536)
    receipt = json.loads(status)
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
    )
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != 1
        or receipt.get("served_public_sha256") != expected
    ):
        raise ValueError("publication receipt does not match the committed tree")
    return status


def sync_once(root: Path, base: str) -> bool:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError("unsafe serving root")
    started = time.monotonic()
    for old_candidate in root.glob(".candidate-*"):
        if old_candidate.is_symlink() or not old_candidate.is_dir() or old_candidate.stat().st_uid != os.getuid():
            raise ValueError("unsafe interrupted candidate")
        shutil.rmtree(old_candidate)
    raw = fetch(base, "manifest.json", MAX_MANIFEST)
    files = manifest(raw)
    state = root / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    previous = state / "serving-manifest.json"
    live = root / "public"
    if previous.is_file() and live.is_dir() and previous.read_bytes() == raw:
        # Publication can succeed without changing bytes. Refresh its freshness
        # receipt even when the public generation does not need another scan.
        save_state(state, "publish-status.json", publication_status(base, files))
        return False
    candidate = Path(tempfile.mkdtemp(prefix=".candidate-", dir=root))
    try:
        total = 0
        for name, expected in sorted(files.items()):
            if time.monotonic() - started > 900:
                raise ValueError("transfer deadline")
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            old = live / name
            # The prior tree is private, read-only to nginx and created by this
            # client. Copy bytes, never hardlink a published/retired generation.
            if old.is_file() and not old.is_symlink() and digest(old) == expected:
                shutil.copyfile(old, target)
                total += target.stat().st_size
            else:
                total += copy_file(base, name, target, expected)
            if total > MAX_TREE:
                raise ValueError("tree size limit")
        if fetch(base, "manifest.json", MAX_MANIFEST) != raw:
            raise ValueError("generation changed during transfer")
        status = publication_status(base, files)
        # The established scanner holds the exact scanned descriptors through
        # atomic promotion. No weaker rename/symlink fallback is introduced.
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("check-public-output.py")),
                "--root",
                str(candidate),
                "--promote-to",
                str(live),
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            raise ValueError("public validation or atomic promotion failed")
        save_state(state, "publish-status.json", status)
        save_state(state, "serving-manifest.json", raw)
        return True
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/work/publisher"))
    parser.add_argument("--origin", default="http://verdify-lab-cache-origin:8080")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            changed = sync_once(args.root, args.origin)
            print("lab serving cache ready generation=" + ("updated" if changed else "unchanged"), flush=True)
            if args.once:
                return 0
        except (OSError, ValueError, subprocess.SubprocessError):
            print("lab serving cache refresh failed; prior validated generation retained", flush=True)
            if args.once:
                return 1
        time.sleep(120)


if __name__ == "__main__":
    raise SystemExit(main())
