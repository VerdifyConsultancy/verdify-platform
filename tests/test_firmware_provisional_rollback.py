"""The provisional rollback path must verify bytes before any OTA attempt."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rollback(tmp_path: Path, *, expected_sha: str, ota_credential: str) -> subprocess.CompletedProcess[str]:
    candidate = tmp_path / "candidate.ota.bin"
    candidate.write_bytes(b"offline test candidate; never an ESP32 image")
    environment = os.environ.copy()
    environment.update(
        {
            "ESP32_HOST": "127.0.0.1",
            "FIRMWARE_ROLLBACK_LOG": str(tmp_path / "rollback.log"),
            "FIRMWARE_ROLLBACK_SHA256": expected_sha,
            "OTA_PW": ota_credential,
            "SECRETS_YAML": str(tmp_path / "absent-secrets.yaml"),
        }
    )
    return subprocess.run(
        ["bash", "scripts/firmware-rollback.sh", str(candidate)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_wrong_provisional_sha_refuses_ota(tmp_path: Path) -> None:
    result = _rollback(tmp_path, expected_sha="0" * 64, ota_credential="offline-placeholder")
    assert result.returncode != 0
    assert "SHA-256 mismatch; refusing OTA" in result.stdout
    assert "Flashing previous binary" not in result.stdout


def test_matching_provisional_sha_still_requires_ota_credential(tmp_path: Path) -> None:
    expected = hashlib.sha256(b"offline test candidate; never an ESP32 image").hexdigest()
    result = _rollback(tmp_path, expected_sha=expected, ota_credential="")
    assert result.returncode != 0
    assert "Rollback binary SHA-256 verified" in result.stdout
    assert "No OTA_PW env" in result.stdout
    assert "Flashing previous binary" not in result.stdout
