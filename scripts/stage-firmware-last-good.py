#!/usr/bin/env python3
"""Stage an authenticated, baked rollback binary from the cluster PVC locally.

This is a read-only fetch. It never promotes a candidate or contacts the ESP32.
The expected version and OTA SHA-256 must come from an independent release
receipt; metadata stored beside the binary cannot authenticate itself.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_hex

NAMESPACE = "verdify-prod"
CLAIM = "verdify-firmware-artifacts"
BAKE_SECONDS = 48 * 60 * 60
MAX_TAR_BYTES = 32 * 1024 * 1024
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class StageError(RuntimeError):
    """The source is unavailable or fails rollback qualification."""


@dataclass(frozen=True)
class VerifiedRollback:
    version: str
    sha256: str
    source_sha: str
    deployed_at: datetime
    original_mtime: int
    binary: bytes
    metadata: bytes


def parse_metadata(raw: bytes) -> dict[str, str]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise StageError("rollback metadata is not UTF-8") from exc
    values: dict[str, str] = {}
    for line in lines:
        if not line or "=" not in line:
            raise StageError("rollback metadata has a malformed line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in values:
            raise StageError("rollback metadata has a duplicate or invalid key")
        values[key] = value
    return values


def validate_bundle(
    files: dict[str, bytes],
    mtimes: dict[str, int],
    *,
    expected_version: str,
    expected_sha256: str,
    now: float | None = None,
) -> VerifiedRollback:
    """Check independent identity, archive parity, and the real bake clock."""
    if not VERSION_RE.fullmatch(expected_version) or not SHA256_RE.fullmatch(expected_sha256):
        raise StageError("expected version or SHA-256 is malformed")
    archive = f"{expected_version}/"
    required = {
        "last-good.ota.bin",
        "last-good.version",
        "last-good.metadata.env",
        archive + "firmware.ota.bin",
        archive + "SHA256SUMS",
        archive + "metadata.env",
    }
    if set(files) != required or set(mtimes) != required:
        raise StageError("rollback bundle is incomplete or contains unexpected files")
    try:
        version = files["last-good.version"].decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise StageError("rollback version is not ASCII") from exc
    if version != expected_version:
        raise StageError("rollback version differs from the independent receipt")
    if files["last-good.metadata.env"] != files[archive + "metadata.env"]:
        raise StageError("last-good and archived metadata differ")
    metadata = parse_metadata(files["last-good.metadata.env"])
    if metadata.get("firmware_version") != expected_version:
        raise StageError("rollback metadata version differs")
    source_sha = metadata.get("source_sha", "")
    if not SOURCE_SHA_RE.fullmatch(source_sha) or metadata.get("source_dirty") != "0":
        raise StageError("rollback source provenance is incomplete or dirty")

    sums = files[archive + "SHA256SUMS"].decode("ascii", errors="strict").splitlines()
    archived_hashes = [line.split()[0] for line in sums if line.split()[1:] == ["firmware.ota.bin"]]
    if len(archived_hashes) != 1 or archived_hashes[0] != expected_sha256:
        raise StageError("archived OTA checksum differs from the independent receipt")
    binary = files["last-good.ota.bin"]
    if not binary or binary != files[archive + "firmware.ota.bin"]:
        raise StageError("last-good binary differs from the archived OTA")
    if hashlib.sha256(binary).hexdigest() != expected_sha256:
        raise StageError("rollback binary SHA-256 mismatch")

    deployed_text = metadata.get("deployed_at", "")
    try:
        deployed_at = datetime.fromisoformat(deployed_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StageError("rollback metadata lacks a valid deployed_at timestamp") from exc
    if deployed_at.tzinfo is None:
        raise StageError("rollback deployed_at timestamp lacks a timezone")
    deployed_epoch = deployed_at.timestamp()
    original_mtime = mtimes["last-good.ota.bin"]
    current = time.time() if now is None else now
    if deployed_epoch > current or original_mtime > current:
        raise StageError("rollback timestamps are in the future")
    if current - deployed_epoch < BAKE_SECONDS or current - original_mtime < BAKE_SECONDS:
        raise StageError("rollback binary has not completed the 48-hour bake")
    if abs(original_mtime - deployed_epoch) > 3600:
        raise StageError("rollback file mtime does not match original deployment time")
    return VerifiedRollback(
        version,
        expected_sha256,
        source_sha,
        deployed_at,
        original_mtime,
        binary,
        files["last-good.metadata.env"],
    )


def unpack_bundle(payload: bytes) -> tuple[dict[str, bytes], dict[str, int]]:
    if len(payload) > MAX_TAR_BYTES:
        raise StageError("rollback bundle exceeds the firmware size bound")
    files: dict[str, bytes] = {}
    mtimes: dict[str, int] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if not member.isfile() or name in files:
                    raise StageError("rollback bundle has a non-file or duplicate entry")
                handle = archive.extractfile(member)
                if handle is None:
                    raise StageError("rollback bundle has an unreadable entry")
                files[name] = handle.read()
                mtimes[name] = int(member.mtime)
    except (tarfile.TarError, OSError) as exc:
        raise StageError("rollback bundle is not a valid tar archive") from exc
    return files, mtimes


def run_kubectl(args: list[str], *, input_data: bytes | None = None) -> bytes:
    command = ["kubectl", "--context", "vallery", "-n", NAMESPACE, *args]
    process = subprocess.run(command, input=input_data, capture_output=True, check=False)
    if process.returncode:
        detail = process.stderr.decode("utf-8", errors="replace").strip()[:400]
        raise StageError(f"kubectl {args[0]} failed: {detail}")
    return process.stdout


def fetch_from_pvc(expected_version: str) -> tuple[dict[str, bytes], dict[str, int]]:
    pod = f"firmware-last-good-read-{token_hex(5)}"
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": pod, "namespace": NAMESPACE},
        "spec": {
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": 300,
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "reader",
                    "image": "python:3.12-alpine",
                    "command": ["sleep", "300"],
                    "resources": {
                        "requests": {"cpu": "25m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "128Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [{"name": "artifacts", "mountPath": "/artifacts", "readOnly": True}],
                }
            ],
            "volumes": [{"name": "artifacts", "persistentVolumeClaim": {"claimName": CLAIM, "readOnly": True}}],
        },
    }
    created = False
    try:
        run_kubectl(["create", "-f", "-"], input_data=json.dumps(manifest).encode())
        created = True
        run_kubectl(["wait", "--for=condition=Ready", "--timeout=120s", f"pod/{pod}"])
        version = (
            run_kubectl(["exec", pod, "-c", "reader", "--", "cat", "/artifacts/last-good.version"])
            .decode("ascii")
            .strip()
        )
        if version != expected_version or not VERSION_RE.fullmatch(version):
            raise StageError("PVC last-good version differs from the independent receipt")
        names = [
            "last-good.ota.bin",
            "last-good.version",
            "last-good.metadata.env",
            f"{version}/firmware.ota.bin",
            f"{version}/SHA256SUMS",
            f"{version}/metadata.env",
        ]
        payload = run_kubectl(["exec", pod, "-c", "reader", "--", "tar", "-cf", "-", "-C", "/artifacts", *names])
        return unpack_bundle(payload)
    finally:
        if created:
            try:
                run_kubectl(["delete", "pod", pod, "--wait=false", "--ignore-not-found"])
            except StageError as exc:
                print(f"Warning: remove temporary PVC reader {pod}: {exc}", file=sys.stderr)


def install_locally(verified: VerifiedRollback, destination: Path) -> None:
    if destination.is_symlink():
        raise StageError(f"local rollback destination is a symlink: {destination}")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    entries = {
        "last-good.ota.bin": verified.binary,
        "last-good.version": (verified.version + "\n").encode(),
        "last-good.metadata.env": verified.metadata,
    }
    for name, content in entries.items():
        current = destination / name
        if current.exists() and current.read_bytes() != content:
            raise StageError(f"existing local rollback differs; preserve it before staging: {current}")
    temporary = destination / f".last-good-stage-{token_hex(5)}"
    temporary.mkdir(mode=0o700)
    try:
        for name, content in entries.items():
            target = temporary / name
            target.write_bytes(content)
            target.chmod(0o600)
        os.utime(temporary / "last-good.ota.bin", (verified.original_mtime, verified.original_mtime))
        for name in ("last-good.version", "last-good.metadata.env", "last-good.ota.bin"):
            os.replace(temporary / name, destination / name)
    finally:
        shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-ota-sha256", required=True)
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "firmware/artifacts")
    args = parser.parse_args()
    try:
        if not VERSION_RE.fullmatch(args.expected_version) or not SHA256_RE.fullmatch(args.expected_ota_sha256):
            raise StageError("provide an exact version and lowercase OTA SHA-256 from an independent receipt")
        files, mtimes = fetch_from_pvc(args.expected_version)
        verified = validate_bundle(
            files, mtimes, expected_version=args.expected_version, expected_sha256=args.expected_ota_sha256
        )
        install_locally(verified, args.destination)
    except StageError as exc:
        print(f"Rollback staging refused: {exc}", file=sys.stderr)
        return 1
    print(
        f"Staged {verified.version} at {args.destination} "
        f"(OTA SHA-256 {verified.sha256}, source {verified.source_sha}, "
        f"deployed {verified.deployed_at.isoformat()}; original mtime preserved)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
