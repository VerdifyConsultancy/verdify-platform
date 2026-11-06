"""#1381: closed-valve flow while someone is in the greenhouse is hose or sink use, not a leak.

Replays the live leak_detected and occupancy rows of 2026-10-26, -27 and -29
(every leak_detected=true period of the 30 days to 2026-11-05) through the
owning leak read, envelope and _sync_monitor_alerts (alert_lifecycle_harness):
they raise no leak alert. The same flag with nobody seen still raises it (a real
leak, or #1161's closed-valve hose test run with nobody in view), and so does an
attended flow that runs past three hours (a hose left running).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from alert_lifecycle_harness import AlertLog, load_alert_lifecycle

KEY = ("leak_detected", "equipment.leak_detected")

# Read-only from production (equipment_state, UTC, 2026), "l" = leak_detected,
# "o" = occupancy. Every valve's last state over these windows was closed and
# the meter read 1-5 gal/min (170 gal on 10-26, 481 on 10-27, 30 on 10-29).
LIVE = (
    ("10-26 16:30:58", "o", True),
    ("10-26 16:46:00", "o", False),
    ("10-26 17:16:04", "o", True),
    ("10-26 17:17:58", "l", True),
    ("10-26 17:18:45", "l", False),
    ("10-26 17:23:58", "l", False),
    ("10-26 17:25:58", "l", True),
    ("10-26 17:31:12", "o", False),
    ("10-26 17:33:58", "l", False),
    ("10-26 17:36:58", "l", True),
    ("10-26 17:37:51", "l", False),
    ("10-26 17:45:54", "l", True),
    ("10-26 18:07:58", "l", False),
    ("10-26 18:08:01", "l", True),
    ("10-26 18:09:01", "l", False),
    ("10-26 18:09:58", "l", True),
    ("10-26 18:11:02", "l", False),
    ("10-26 18:11:58", "l", False),
    ("10-26 18:13:58", "l", True),
    ("10-26 18:14:03", "l", False),
    ("10-26 18:14:58", "l", False),
    ("10-26 18:16:58", "l", True),
    ("10-26 18:17:04", "l", False),
    ("10-26 18:20:58", "l", False),
    ("10-26 18:22:58", "l", True),
    ("10-26 18:23:06", "l", False),
    ("10-26 18:24:58", "l", False),
    ("10-26 18:46:14", "o", True),
    ("10-26 18:47:58", "l", True),
    ("10-26 18:48:13", "l", False),
    ("10-26 18:57:17", "l", True),
    ("10-26 19:01:16", "o", False),
    ("10-26 19:06:17", "o", True),
    ("10-26 19:19:58", "l", False),
    ("10-26 19:22:58", "l", True),
    ("10-26 19:23:24", "l", False),
    ("10-26 19:32:27", "l", True),
    ("10-26 19:33:58", "l", False),
    ("10-26 19:35:58", "l", True),
    ("10-26 19:36:28", "l", False),
    ("10-26 19:40:59", "l", False),
    ("10-26 21:56:35", "o", True),
    ("10-26 22:00:59", "l", True),
    ("10-26 22:01:13", "l", False),
    ("10-26 22:10:17", "l", True),
    ("10-26 22:11:37", "o", False),
    ("10-26 22:15:59", "l", False),
    ("10-26 22:16:19", "l", True),
    ("10-26 22:17:19", "l", False),
    ("10-26 22:17:59", "l", True),
    ("10-26 22:18:20", "l", False),
    ("10-26 22:20:59", "l", False),
    ("10-26 22:41:39", "o", True),
    ("10-26 22:43:59", "l", True),
    ("10-26 22:44:28", "l", False),
    ("10-26 22:45:59", "l", False),
    ("10-26 22:51:40", "o", False),
    ("10-27 15:57:27", "o", True),
    ("10-27 16:12:28", "o", False),
    ("10-27 16:21:59", "l", True),
    ("10-27 16:22:49", "l", False),
    ("10-27 16:24:59", "l", False),
    ("10-27 16:30:59", "l", True),
    ("10-27 16:31:51", "l", False),
    ("10-27 16:31:59", "l", False),
    ("10-27 16:43:59", "l", True),
    ("10-27 16:44:54", "l", False),
    ("10-27 16:47:29", "o", True),
    ("10-27 16:52:29", "o", False),
    ("10-27 16:52:56", "l", True),
    ("10-27 18:28:00", "l", False),
    ("10-27 18:28:21", "l", True),
    ("10-27 18:29:22", "l", False),
    ("10-28 05:11:41", "l", False),
    ("10-28 05:11:42", "o", False),
    ("10-29 15:40:15", "o", True),
    ("10-29 15:55:15", "o", False),
    ("10-29 16:21:34", "l", True),
    ("10-29 16:22:19", "l", False),
    ("10-29 16:28:22", "l", True),
    ("10-29 16:31:02", "l", False),
    ("10-29 16:31:23", "l", True),
    ("10-29 16:32:23", "l", False),
    ("10-29 16:33:02", "l", True),
    ("10-29 16:33:24", "l", False),
    ("10-29 16:36:02", "l", False),
    ("10-29 17:16:37", "l", False),
)
NAMES = {"l": "leak_detected", "o": "occupancy"}


def at(stamp: str) -> datetime:
    return datetime.strptime(f"2026-{stamp}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def replay(rows, start: datetime, end: datetime, *, step=timedelta(minutes=1)):
    """Run the leak section and the sync every ``step`` from start to end with rows appearing as they happened."""
    ns = load_alert_lifecycle()
    log = AlertLog(start)
    pending = sorted(((at(stamp), NAMES[kind], state) for stamp, kind, state in rows), key=lambda row: row[0])
    when = start
    while when <= end:
        while pending and pending[0][0] <= when:
            ts, equipment, state = pending.pop(0)
            log.equipment.append({"ts": ts, "equipment": equipment, "state": state})
        log.now = when
        leak, refresh_only = asyncio.run(ns["_leak_cycle_alert"](log, now=when))
        asyncio.run(
            ns["_sync_monitor_alerts"](
                log,
                [leak] if leak else [],
                component_status_available=True,
                component_closure_resolutions={},
                refresh_only_keys=refresh_only,
                now=when,
            )
        )
        when += step
    return ns, log


def test_the_three_live_hose_days_raise_no_leak_alert():
    for start, end in (
        ("10-26 16:00:00", "10-26 23:30:00"),
        ("10-27 15:30:00", "10-27 19:30:00"),
        ("10-29 15:30:00", "10-29 17:30:00"),
    ):
        ns, log = replay(LIVE, at(start), at(end))
        assert log.of(*KEY) == [], start


def test_the_same_flow_with_nobody_seen_still_raises():
    """A real leak, or #1161's closed-valve hose test with nobody in view."""
    unattended = [row for row in LIVE if row[1] == "l"]
    ns, log = replay(unattended, at("10-29 15:30:00"), at("10-29 17:30:00"))
    [row] = log.of(*KEY)
    assert row["severity"] == "critical" and row["resolved_at"] is None
    assert datetime.fromisoformat(row["details"]["since"]) == at("10-29 16:21:34")


def test_an_attended_flow_past_three_hours_raises():
    rows = (("10-27 15:57:27", "o", True), ("10-27 16:12:28", "o", False), ("10-27 16:21:59", "l", True))
    ns, log = replay(rows, at("10-27 16:00:00"), at("10-27 19:30:00"), step=timedelta(minutes=5))
    [row] = log.of(*KEY)
    assert row["ts"] >= at("10-27 19:21:59")  # 3 h after the episode start, not before
    assert datetime.fromisoformat(row["details"]["since"]) == at("10-27 16:21:59")


def test_episode_start_bridges_short_gaps_and_occupancy_window():
    ns = load_alert_lifecycle()
    start = ns["_leak_episode_start"]
    rows = [{"ts": at(stamp), "state": state} for stamp, kind, state in LIVE if kind == "l" and stamp < "10-27 17:00"]
    assert start([r for r in rows if r["ts"] <= at("10-27 16:52:56")]) == at("10-27 16:21:59")
    assert start([r for r in rows if r["ts"] <= at("10-26 18:47:58")]) == at(
        "10-26 18:47:58"
    )  # 23 min off: new episode
    assert start([r for r in rows if r["ts"] <= at("10-26 18:24:58")]) is None  # flag off
    attended = ns["_leak_attended"]
    seen = [{"ts": at("10-29 15:40:15"), "state": True}, {"ts": at("10-29 15:55:15"), "state": False}]
    assert attended(at("10-29 16:21:34"), seen)
    assert not attended(at("10-29 17:00:00"), seen[1:])  # only a "left" row in the window
    assert attended(at("10-29 16:50:00"), seen)  # the 15:40 detection still holds at the window edge
    assert not attended(at("10-29 18:00:00"), seen)
