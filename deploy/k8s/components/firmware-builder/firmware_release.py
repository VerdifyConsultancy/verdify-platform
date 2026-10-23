#!/usr/bin/env python3
"""Bound archived firmware to an exact source and reviewed signed manifest.

The flash Pod stages immutable private copies, verifies the manifest with cosign,
then runs this verifier again immediately before the optional OTA call.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

FILES = ("firmware.ota.bin", "firmware.bin", "firmware.elf", "firmware.map")
SCHEMA = "verdify.firmware-release/v1"
MAX_MANIFEST = 65536


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def exact_sha(value: str, length: int) -> str:
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        raise ValueError(f"expected {length}-character lowercase SHA")
    return value


def regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"regular file required: {path.name}")


def release_directory(root: Path, revision: str) -> Path:
    exact_sha(revision, 40)
    regular_root = root.resolve(strict=True)
    directory = root / revision
    if directory.is_symlink() or directory.resolve(strict=True).parent != regular_root:
        raise ValueError("release directory escapes artifact root")
    return directory


def write_manifest(root: Path, revision: str, firmware_version: str, toolchain: str) -> dict:
    directory = release_directory(root, revision)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", firmware_version):
        raise ValueError("invalid firmware version")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", toolchain):
        raise ValueError("immutable toolchain reference required")
    files = {}
    for name in FILES:
        path = directory / name
        regular(path)
        if path.stat().st_size == 0:
            raise ValueError("empty build output")
        files[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    result = {
        "schema": SCHEMA,
        "source_repository": "https://github.com/VerdifyConsultancy/verdify-platform",
        "source_revision": revision,
        "firmware_version": firmware_version,
        "device": "greenhouse",
        "toolchain": toolchain,
        "files": files,
    }
    with (directory / "release.json").open("x") as stream:
        json.dump(result, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    return result


def read_manifest(directory: Path, revision: str, expected: str) -> dict:
    exact_sha(revision, 40)
    exact_sha(expected, 64)
    path = directory / "release.json"
    regular(path)
    if path.stat().st_size > MAX_MANIFEST or sha256(path) != expected:
        raise ValueError("reviewed manifest digest mismatch")
    result = json.loads(path.read_text())
    if result.get("schema") != SCHEMA or result.get("source_revision") != revision:
        raise ValueError("release identity mismatch")
    if result.get("device") != "greenhouse":
        raise ValueError("wrong device")
    if result.get("source_repository") != "https://github.com/VerdifyConsultancy/verdify-platform":
        raise ValueError("wrong source repository")
    files = result.get("files")
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise ValueError("incomplete or unexpected artifact set")
    for name in FILES:
        entry = files[name]
        if not isinstance(entry, dict) or set(entry) != {"sha256", "bytes"}:
            raise ValueError("malformed artifact descriptor")
        exact_sha(entry["sha256"], 64)
        if type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= 128 * 1024 * 1024:
            raise ValueError("invalid artifact size")
        artifact = directory / name
        regular(artifact)
        if artifact.stat().st_size != entry["bytes"] or sha256(artifact) != entry["sha256"]:
            raise ValueError(f"artifact bytes differ: {name}")
    return result


def stage(root: Path, revision: str, expected: str, destination: Path) -> None:
    source = release_directory(root, revision)
    read_manifest(source, revision, expected)
    # Copy into the Pod's private emptyDir. The verifier and uploader never use
    # mutable PVC paths after this point, including the signature bundle.
    if any(destination.iterdir()):
        raise ValueError("staging directory must be empty")
    for name in (*FILES, "release.json", "release.sigstore.json"):
        path = source / name
        regular(path)
        if path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("artifact exceeds staging bound")
        shutil.copyfile(path, destination / name)
    read_manifest(destination, revision, expected)


def verify_signature(directory: Path, public_key: Path) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    bundle_path = directory / "release.sigstore.json"
    regular(bundle_path)
    if bundle_path.stat().st_size > MAX_MANIFEST:
        raise ValueError("signature bundle too large")
    bundle = json.loads(bundle_path.read_text())
    if bundle.get("mediaType") != "application/vnd.dev.sigstore.bundle.v0.3+json":
        raise ValueError("unsupported signature bundle")
    payload = (directory / "release.json").read_bytes()
    message = bundle["messageSignature"]
    digest = message["messageDigest"]
    if (
        digest["algorithm"] != "SHA2_256"
        or base64.b64decode(digest["digest"], validate=True) != hashlib.sha256(payload).digest()
    ):
        raise ValueError("signed payload digest mismatch")
    key = serialization.load_pem_public_key(public_key.read_bytes())
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("trusted key must be P-256")
    try:
        key.verify(base64.b64decode(message["signature"], validate=True), payload, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("firmware signature mismatch") from exc


def finish(directory: Path, revision: str, expected: str, public_key: Path, flash: bool) -> None:
    manifest = read_manifest(directory, revision, expected)
    verify_signature(directory, public_key)
    if flash:
        # cosign verification is a preceding mandatory initContainer. Device
        # transport is imported only after private-copy byte validation.
        from esphome import espota2

        password = os.environ.get("OTA_PASSWORD")
        if not password:
            raise ValueError("OTA credential absent")
        rc, _ = espota2.run_ota(
            remote_host="192.168.10.111",
            remote_port=3232,
            password=password,
            filename=directory / "firmware.ota.bin",
        )
        if rc:
            raise RuntimeError("OTA transport failed")
    print(
        json.dumps(
            {
                "source_revision": revision,
                "firmware_version": manifest["firmware_version"],
                "manifest_sha256": expected,
                "verified_artifact_bytes": True,
                "device_contact": flash,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("manifest", "stage", "verify", "flash"))
    parser.add_argument("--root", type=Path, default=Path("/artifacts"))
    parser.add_argument("--staged", type=Path, default=Path("/verified"))
    parser.add_argument("--revision", default=os.environ.get("FW_REVISION", ""))
    parser.add_argument("--expected", default=os.environ.get("EXPECTED_MANIFEST_SHA256", ""))
    parser.add_argument("--firmware-version", default=os.environ.get("FW_VERSION", ""))
    parser.add_argument("--toolchain", default=os.environ.get("FIRMWARE_TOOLCHAIN_IMAGE", ""))
    parser.add_argument("--public-key", type=Path, default=Path("/trust/cosign.pub"))
    args = parser.parse_args()
    try:
        if args.action == "manifest":
            write_manifest(args.root, args.revision, args.firmware_version, args.toolchain)
            print(
                json.dumps(
                    {
                        "source_revision": args.revision,
                        "manifest_sha256": sha256(args.root / args.revision / "release.json"),
                    }
                )
            )
        elif args.action == "stage":
            stage(args.root, args.revision, args.expected, args.staged)
        else:
            finish(args.staged, args.revision, args.expected, args.public_key, args.action == "flash")
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(f"firmware release refused: {exc}", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
