#!/usr/bin/env python3
"""Clear known stale retained MQTT payloads for irrigation feedback diagnostics."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_DIR = REPO_ROOT / "ingestor"
if str(INGESTOR_DIR) not in sys.path:
    sys.path.insert(0, str(INGESTOR_DIR))

from entity_map import MQTT_FEEDBACK_CANDIDATES  # noqa: E402

from config import MQTT_HOST, MQTT_PASS, MQTT_PORT, MQTT_USER  # noqa: E402

STALE_NEAR_MISS_TOPICS = (
    "greenhouse/sensor/east_soil_moisture____/state",
    "greenhouse/sensor/south_2_soil_moisture____/state",
    "greenhouse/sensor/west_soil_moisture____/state",
)


def _publish_command(topic: str) -> list[str]:
    cmd = ["mosquitto_pub", "-h", MQTT_HOST, "-p", str(MQTT_PORT)]
    if MQTT_USER:
        cmd.extend(["-u", MQTT_USER])
    if MQTT_PASS:
        cmd.extend(["-P", MQTT_PASS])
    cmd.extend(["-t", topic, "-r", "-n"])
    return cmd


def _clear_topic(topic: str) -> tuple[bool, str]:
    result = subprocess.run(_publish_command(topic), capture_output=True, text=True, timeout=20, check=False)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"mosquitto_pub exited {result.returncode}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Required before retained MQTT payloads are cleared")
    parser.add_argument("--dry-run", action="store_true", help="Print target topics without clearing them")
    parser.add_argument(
        "--feedback-key",
        default="south_soil_probe_1",
        choices=sorted(MQTT_FEEDBACK_CANDIDATES),
        help="Feedback candidate group to clear",
    )
    parser.add_argument(
        "--near-miss",
        action="store_true",
        help="Clear known retained near-match soil topics that are not accepted feedback inputs",
    )
    args = parser.parse_args()

    topics = STALE_NEAR_MISS_TOPICS if args.near_miss else MQTT_FEEDBACK_CANDIDATES[args.feedback_key]
    if not args.dry_run and not args.confirm:
        print("Refusing to clear retained MQTT values without --confirm", file=sys.stderr)
        return 2
    if shutil.which("mosquitto_pub") is None:
        print("mosquitto_pub not found", file=sys.stderr)
        return 2

    failed: list[str] = []
    for topic in topics:
        if args.dry_run:
            print(f"would clear retained MQTT feedback topic: {topic}")
            continue
        print(f"clearing retained MQTT feedback topic: {topic}")
        ok, detail = _clear_topic(topic)
        if not ok:
            failed.append(f"{topic}: {detail}")

    if failed:
        print("Failed to clear one or more retained MQTT topics:", file=sys.stderr)
        for item in failed:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
