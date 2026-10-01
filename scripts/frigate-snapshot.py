#!/usr/bin/env python3
"""
frigate-snapshot.py — Capture greenhouse camera snapshots from Frigate.

Saves to Obsidian vault for crop health tracking. 4x daily via cron.

Usage:
    frigate-snapshot.py              # capture now
    frigate-snapshot.py --camera greenhouse_2   # specific camera
"""

import http.client
import logging
import os
import ssl
import subprocess
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [snapshot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Service8971 is the authenticated HTTPS proxy, not Frigate's admin API.
FRIGATE_URL = os.environ.get("VERDIFY_FRIGATE_URL", "https://frigate.frigate.svc.cluster.local:8971")
TLS_SERVER_NAME = os.environ.get("VERDIFY_FRIGATE_TLS_SERVER_NAME", "cameras.vallery.net")
TOKEN_FILE = Path(os.environ.get("VERDIFY_FRIGATE_TOKEN_FILE", "/vfrigate/token"))
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
CAMERAS = ["greenhouse_1", "greenhouse_2"]
# Snapshot sink: the Syncthing vault (/mnt/iris) is gone with the iris-VM; in
# k3s point this at an emptyDir/PVC shared with the analyze step.
VAULT_DIR = Path(os.environ.get("VERDIFY_SNAPSHOT_DIR", "/mnt/iris/verdify-vault/snapshots"))
DENVER = ZoneInfo("America/Denver")


class SnapshotHTTPSConnection(http.client.HTTPSConnection):
    """Connect through cluster DNS while verifying the provider's TLS identity."""

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=TLS_SERVER_NAME)


def capture_snapshot(camera: str) -> bool:
    """Fetch one allowed snapshot; never follow redirects or log credentials."""
    if camera not in CAMERAS:
        log.error("Unsupported greenhouse camera")
        return False
    connection = None
    try:
        endpoint = urllib.parse.urlsplit(FRIGATE_URL)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
            raise ValueError("HTTPS endpoint required")
        if endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment:
            raise ValueError("Endpoint must be an HTTPS origin")
        token = TOKEN_FILE.read_text().strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError("Invalid dedicated credential")
        connection = SnapshotHTTPSConnection(
            endpoint.hostname, endpoint.port or 443, timeout=15, context=ssl.create_default_context()
        )
        connection.request(
            "GET",
            f"/api/vision/{camera}/latest.jpg",
            headers={"Host": TLS_SERVER_NAME, "Authorization": f"Bearer {token}", "User-Agent": "verdify-snapshot/2.0"},
        )
        response = connection.getresponse()
        if response.status != 200:
            log.error("%s: snapshot HTTP %d", camera, response.status)
            return False
        data = response.read(MAX_SNAPSHOT_BYTES + 1)
        if (
            response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "image/jpeg"
            or not 1000 <= len(data) <= MAX_SNAPSHOT_BYTES
            or not data.startswith(b"\xff\xd8")
            or not data.endswith(b"\xff\xd9")
        ):
            log.error("%s: invalid or oversized JPEG response", camera)
            return False
        now = datetime.now(DENVER)
        date_dir = VAULT_DIR / now.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filepath = date_dir / f"{camera}_{now.strftime('%H%M')}.jpg"
        filepath.write_bytes(data)
        log.info("%s: saved %s (%d KB)", camera, filepath, len(data) // 1024)
        return True
    except Exception as error:
        # HTTP/provider exception text can contain auth headers or bodies.
        log.error("%s: snapshot failed (%s)", camera, type(error).__name__)
        return False
    finally:
        if connection is not None:
            connection.close()


def main():
    cameras = CAMERAS

    if "--camera" in sys.argv:
        idx = sys.argv.index("--camera")
        cameras = [sys.argv[idx + 1]]

    success = 0
    for cam in cameras:
        if capture_snapshot(cam):
            success += 1

    log.info("Done: %d/%d cameras captured", success, len(cameras))

    # Trigger Gemini Vision analysis on captured snapshots
    if success > 0 and "--no-analyze" not in sys.argv:
        log.info("Triggering crop health analysis...")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    os.environ.get(
                        "VERDIFY_ANALYZE_SNAPSHOT_SCRIPT", "/srv/verdify/scripts/analyze-greenhouse-snapshot.py"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                log.info("Analysis complete")
            else:
                log.error("Analysis failed (exit %d)", result.returncode)
                return 1
        except Exception as error:
            log.error("Analysis trigger failed (%s)", type(error).__name__)
            return 1

    return 0 if success == len(cameras) else 1


if __name__ == "__main__":
    sys.exit(main())
