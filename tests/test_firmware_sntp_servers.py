"""#1364: the controller's clock comes from local NTP only: the estate chrony (VM305) first, the Starlink dish
second, and no internet hostname (no pool.ntp.org) and not the VLAN 10 gateway, which serves no NTP."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_sntp_servers_are_local_only():
    text = (ROOT / "firmware" / "greenhouse.yaml").read_text()
    block = re.search(r"platform: sntp\n(?:.*\n)*?    servers:\n((?:      - .*\n)+)", text)
    assert block is not None
    servers = re.findall(r"      - (\S+)", block[1])
    assert servers == ["192.168.7.54", "192.168.100.1"]
    assert "pool.ntp.org" not in text.split("time:", 1)[1].split("on_time_sync", 1)[0]
