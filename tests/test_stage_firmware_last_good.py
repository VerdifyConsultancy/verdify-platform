"""Safety checks for staging the historically baked firmware rollback binary."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/stage-firmware-last-good.py"
SPEC = importlib.util.spec_from_file_location("stage_firmware_last_good", SCRIPT)
assert SPEC and SPEC.loader
stage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage
SPEC.loader.exec_module(stage)

VERSION = "2026.7.10.1500.09ee886"
SOURCE_SHA = "a" * 40
NOW = datetime(2026, 9, 28, tzinfo=UTC)
BINARY = b"authentic OTA fixture"
HASH = hashlib.sha256(BINARY).hexdigest()


def bundle(*, deployed: datetime | None = None, mtime: int | None = None):
    deployed = deployed or NOW - timedelta(days=3)
    stamp = int(deployed.timestamp()) if mtime is None else mtime
    metadata = (
        f"firmware_version={VERSION}\nsource_sha={SOURCE_SHA}\nsource_dirty=0\ndeployed_at={deployed.isoformat()}\n"
    ).encode()
    prefix = f"{VERSION}/"
    files = {
        "last-good.ota.bin": BINARY,
        "last-good.version": (VERSION + "\n").encode(),
        "last-good.metadata.env": metadata,
        prefix + "firmware.ota.bin": BINARY,
        prefix + "SHA256SUMS": f"{HASH}  firmware.ota.bin\n".encode(),
        prefix + "metadata.env": metadata,
    }
    return files, {name: stamp for name in files}


class RollbackStageTests(unittest.TestCase):
    def validate(self, files, mtimes, *, expected_sha=HASH):
        return stage.validate_bundle(
            files,
            mtimes,
            expected_version=VERSION,
            expected_sha256=expected_sha,
            now=NOW.timestamp(),
        )

    def test_verified_bundle_preserves_original_mtime(self):
        files, mtimes = bundle()
        verified = self.validate(files, mtimes)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "artifacts"
            stage.install_locally(verified, destination)
            binary = destination / "last-good.ota.bin"
            self.assertEqual(binary.read_bytes(), BINARY)
            self.assertEqual(int(binary.stat().st_mtime), mtimes["last-good.ota.bin"])
            self.assertEqual(binary.stat().st_mode & 0o777, 0o600)

    def test_requires_independently_expected_binary_hash(self):
        files, mtimes = bundle()
        with self.assertRaisesRegex(stage.StageError, "independent receipt"):
            self.validate(files, mtimes, expected_sha="b" * 64)

    def test_rejects_young_deployment_or_copied_file_mtime(self):
        files, mtimes = bundle(deployed=NOW - timedelta(hours=47))
        with self.assertRaisesRegex(stage.StageError, "48-hour bake"):
            self.validate(files, mtimes)
        files, mtimes = bundle(mtime=int((NOW - timedelta(days=2, hours=1)).timestamp()))
        with self.assertRaisesRegex(stage.StageError, "does not match original deployment"):
            self.validate(files, mtimes)
        files, mtimes = bundle(mtime=int((NOW - timedelta(hours=1)).timestamp()))
        with self.assertRaisesRegex(stage.StageError, "48-hour bake"):
            self.validate(files, mtimes)

    def test_rejects_unqualified_metadata_and_version(self):
        files, mtimes = bundle()
        files["last-good.version"] = b"wrong-version\n"
        with self.assertRaisesRegex(stage.StageError, "version differs"):
            self.validate(files, mtimes)
        files, mtimes = bundle()
        files["last-good.metadata.env"] = files["last-good.metadata.env"].replace(b"source_dirty=0", b"source_dirty=1")
        files[f"{VERSION}/metadata.env"] = files["last-good.metadata.env"]
        with self.assertRaisesRegex(stage.StageError, "provenance"):
            self.validate(files, mtimes)

    def test_rejects_missing_deployment_proof_and_existing_local_different_binary(self):
        files, mtimes = bundle()
        files["last-good.metadata.env"] = files["last-good.metadata.env"].replace(
            b"deployed_at=2026-09-25T00:00:00+00:00\n", b""
        )
        files[f"{VERSION}/metadata.env"] = files["last-good.metadata.env"]
        with self.assertRaisesRegex(stage.StageError, "deployed_at"):
            self.validate(files, mtimes)
        verified = self.validate(*bundle())
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            (destination / "last-good.ota.bin").write_bytes(b"preserve old")
            with self.assertRaisesRegex(stage.StageError, "existing local rollback differs"):
                stage.install_locally(verified, destination)

    def test_tar_reader_preserves_archive_member_mtime(self):
        files, mtimes = bundle()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mtime = mtimes[name]
                archive.addfile(member, io.BytesIO(content))
        unpacked, timestamps = stage.unpack_bundle(stream.getvalue())
        self.assertEqual(unpacked, files)
        self.assertEqual(timestamps, mtimes)


if __name__ == "__main__":
    unittest.main()
