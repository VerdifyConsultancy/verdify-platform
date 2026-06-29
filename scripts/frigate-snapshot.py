#!/usr/bin/env python3
"""
frigate-snapshot.py — Capture greenhouse camera snapshots from Frigate.

Saves to Obsidian vault for crop health tracking. 4x daily via cron.

Usage:
    frigate-snapshot.py              # capture now
    frigate-snapshot.py --camera greenhouse_2   # specific camera
"""

import logging
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [snapshot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

FRIGATE_URL = "http://192.168.30.142:5000"  # Frigate detect-frame fallback.
GO2RTC_URL = os.environ.get("VERDIFY_GO2RTC_PUBLIC_BASE_URL", "http://192.168.30.142:1984")
CAMERAS = ["greenhouse_1", "greenhouse_2"]
VAULT_DIR = Path("/mnt/iris/verdify-vault/snapshots")
DENVER = ZoneInfo("America/Denver")


def capture_snapshot(camera: str) -> bool:
    """Capture latest snapshot from Frigate camera. Returns True on success."""
    now = datetime.now(DENVER)
    date_dir = VAULT_DIR / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{camera}_{now.strftime('%H%M')}.jpg"
    filepath = date_dir / filename

    urls = [
        f"{GO2RTC_URL.rstrip('/')}/api/frame.jpeg?{urllib.parse.urlencode({'src': camera, 'h': 1080})}",
        f"{FRIGATE_URL.rstrip('/')}/api/{camera}/latest.jpg?h=720&quality=100",
    ]

    for url in urls:
        req = urllib.request.Request(url, headers={"User-Agent": "verdify-snapshot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                if len(data) < 1000:
                    log.warning("%s: response too small (%d bytes) — camera may be offline", camera, len(data))
                    continue
                filepath.write_bytes(data)
                log.info("%s: saved %s (%d KB)", camera, filepath, len(data) // 1024)
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning("%s: camera not found (404) — may be offline", camera)
            else:
                log.warning("%s: HTTP %d — %s", camera, e.code, e.reason)
        except Exception as e:
            log.warning("%s: %s", camera, e)
    log.error("%s: no snapshot endpoint succeeded", camera)
    return False


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
                log.warning("Analysis failed: %s", result.stderr[:200] if result.stderr else "no output")
        except Exception as e:
            log.warning("Analysis trigger error: %s", e)


if __name__ == "__main__":
    main()
