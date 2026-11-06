#!/usr/bin/env python3
"""Find grow-light boundary episodes and flag premature cycling or overrun (#386).

Read-only. For each light circuit (main, grow) it takes the controller's intent
edges (equipment_state ``grow_light_<c>_intent``, exact times), the decision
reasons (system_state ``gl_<c>_reason``, one per 60 s decision), the live
``gl_<c>_min_on_s`` and controller boots (diagnostics uptime), and classifies
every ON interval:

  boundary_ok   the window closed during minimum-on (``min_on_hold`` seen) and
                the light went OFF once minimum-on had run, within one decision
  overrun       OFF came later than minimum-on plus one decision after the first
                ``min_on_hold`` (a hold can never outlast minimum-on)
  premature     ON for less than minimum-on, without auto-disable or a reboot
  auto_disabled ended early by auto-disable, which stays dominant
  reset         a controller boot fell inside the interval
  ordinary      anything else (ended at or after minimum-on with no hold)

The Lutron readback (equipment_state ``grow_light_<c>``) lag after each intent
edge is reported beside it. Exit 1 when any premature or overrun interval is
found, 0 otherwise. A ``boundary_ok`` episode is the natural evidence #386 waits
for; the script never forces one.

Usage: scripts/growlight-boundary-episodes.py [--since '3 days']
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CIRCUITS = ("main", "grow")
DECISION_S = 60.0  # the lighting decision period
SLACK_S = 15.0  # telemetry and tick jitter
READBACK_WINDOW_S = 600.0

SQL = """
SELECT json_build_object(
  'edges', (SELECT coalesce(json_agg(json_build_object('ts', extract(epoch FROM ts), 'eq', equipment, 'on', state)
                                     ORDER BY ts), '[]')
              FROM equipment_state
             WHERE ts >= now() - interval '{since}' - interval '1 day'
               AND equipment IN ('grow_light_main_intent', 'grow_light_grow_intent',
                                 'grow_light_main', 'grow_light_grow')),
  'reasons', (SELECT coalesce(json_agg(json_build_object('ts', extract(epoch FROM ts), 'entity', entity,
                                                         'value', value) ORDER BY ts), '[]')
                FROM system_state
               WHERE ts >= now() - interval '{since}' - interval '1 day'
                 AND entity IN ('gl_main_reason', 'gl_grow_reason')),
  'min_on', (SELECT json_object_agg(parameter, value)
               FROM (SELECT DISTINCT ON (parameter) parameter, value FROM setpoint_snapshot
                      WHERE parameter IN ('gl_main_min_on_s', 'gl_grow_min_on_s')
                        AND ts > now() - interval '2 days'
                      ORDER BY parameter, ts DESC) s),
  'boots', (SELECT coalesce(json_agg(boot ORDER BY boot), '[]')
              FROM (SELECT extract(epoch FROM ts) - uptime_s AS boot
                      FROM (SELECT ts, uptime_s, lag(uptime_s) OVER (ORDER BY ts) AS prev
                              FROM diagnostics
                             WHERE ts >= now() - interval '{since}' - interval '1 day'
                               AND uptime_s IS NOT NULL) d
                     WHERE prev IS NULL OR uptime_s < prev) b),
  'since', extract(epoch FROM now() - interval '{since}')
)
"""


def iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def intervals(edges: list[dict], equipment: str) -> list[tuple[float, float | None]]:
    """ON intervals from one equipment's state rows (repeated rows are not edges)."""
    out: list[tuple[float, float | None]] = []
    on_since = None
    last = None
    for e in edges:
        if e["eq"] != equipment or e["on"] == last:
            continue
        last = e["on"]
        if e["on"]:
            on_since = e["ts"]
        elif on_since is not None:
            out.append((on_since, e["ts"]))
            on_since = None
    if on_since is not None:
        out.append((on_since, None))
    return out


def readback_lag(edges: list[dict], equipment: str, after: float, state: bool) -> float | None:
    for e in edges:
        if e["eq"] == equipment and e["on"] == state and after - SLACK_S <= e["ts"] <= after + READBACK_WINDOW_S:
            return round(e["ts"] - after, 1)
    return None


def classify(data: dict) -> list[dict]:
    episodes = []
    for circuit in CIRCUITS:
        min_on = float((data.get("min_on") or {}).get(f"gl_{circuit}_min_on_s", 120.0))
        reasons = [r for r in data["reasons"] if r["entity"] == f"gl_{circuit}_reason"]
        for on_ts, off_ts in intervals(data["edges"], f"grow_light_{circuit}_intent"):
            if off_ts is None or off_ts < data["since"]:
                continue
            during = [r for r in reasons if on_ts - SLACK_S <= r["ts"] <= off_ts + SLACK_S]
            holds = [r["ts"] for r in during if r["value"] == "min_on_hold"]
            stop = next(
                (r["value"] for r in reasons if off_ts - SLACK_S <= r["ts"] <= off_ts + DECISION_S + SLACK_S), None
            )
            duration = off_ts - on_ts
            # A boot zeroes the decision's last transition, so it matters only where minimum-on can
            # still decide: from ON (or minimum-on before OFF) to just after OFF.
            near = max(on_ts, off_ts - min_on - DECISION_S) - SLACK_S
            if any(near <= b <= off_ts + 2 * DECISION_S for b in data["boots"]):
                kind = "reset"
            elif stop == "auto_disabled":
                kind = "auto_disabled"
            elif duration < min_on - SLACK_S:
                kind = "premature"
            elif holds and off_ts - holds[0] > min_on + DECISION_S + SLACK_S:
                kind = "overrun"
            elif holds:
                kind = "boundary_ok"
            else:
                kind = "ordinary"
            episodes.append(
                {
                    "circuit": circuit,
                    "kind": kind,
                    "on": iso(on_ts),
                    "off": iso(off_ts),
                    "duration_s": round(duration, 1),
                    "min_on_s": min_on,
                    "first_min_on_hold": iso(holds[0]) if holds else None,
                    "stop_reason": stop,
                    "readback_on_lag_s": readback_lag(data["edges"], f"grow_light_{circuit}", on_ts, True),
                    "readback_off_lag_s": readback_lag(data["edges"], f"grow_light_{circuit}", off_ts, False),
                }
            )
    return episodes


def fetch(since: str) -> dict:
    if not re.fullmatch(r"\d{1,3} (minutes|hours|days)", since):
        raise SystemExit("--since must look like '3 days', '12 hours' or '90 minutes'")
    db = Path(__file__).resolve().parent / "verdify-db.sh"
    out = subprocess.check_output(
        ["bash", str(db), "prod", "-XAt", "-c", SQL.format(since=since)], text=True, timeout=120
    )
    return json.loads(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="3 days")
    args = parser.parse_args(argv)
    episodes = classify(fetch(args.since))
    counts: dict[str, int] = {}
    for e in episodes:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    print(json.dumps({"since": args.since, "counts": counts, "episodes": episodes}, indent=2))
    return 1 if counts.get("premature") or counts.get("overrun") else 0


if __name__ == "__main__":
    sys.exit(main())
