"""Bounded native API callback evidence for the declared fixed probe routes.

These are host receive times, not Modbus poll times or physical identities.
The first state per entity on each API subscription may be a cached replay;
it is excluded. A later callback is still only a native API receive event.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime

ROUTES = {
    "north": {"modbus_address": 2, "temp": "north_temp___f_", "rh": "north_rh____"},
    "west": {"modbus_address": 3, "temp": "west_temp___f_", "rh": "west_rh____"},
    "east": {"modbus_address": 5, "temp": "east_temp___f_", "rh": "east_rh____"},
}
ROUTE_SOURCE_SHA256 = hashlib.sha256(json.dumps(ROUTES, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
OBJECT_FIELDS = {
    object_id: (zone, metric)
    for zone, route in ROUTES.items()
    for metric in ("temp", "rh")
    for object_id in (route[metric],)
}


class NativeProbeTracker:
    """Keep only callbacks since the last report, fenced to one API generation."""

    def __init__(self, runtime_instance_id: str):
        self.runtime_instance_id = runtime_instance_id
        self.generation = 0
        self.first_seen: set[str] = set()
        self.pending: dict[str, dict] = {}
        self.replay_excluded = 0
        self.generation_changed = False

    def mark_connected(self, generation: int) -> None:
        if generation < 1:
            raise ValueError("native API generation must be positive")
        if generation == self.generation:
            return
        self.pending.clear()
        self.first_seen.clear()
        self.replay_excluded = 0
        self.generation = generation
        self.generation_changed = True

    def observe(self, object_id: str, value: object, received_at: datetime, generation: int) -> bool:
        if object_id not in OBJECT_FIELDS or generation != self.generation or generation < 1:
            return False
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            return False
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        zone, metric = OBJECT_FIELDS[object_id]
        if metric == "rh" and not 0 <= value <= 100:
            return False
        if object_id not in self.first_seen:
            self.first_seen.add(object_id)
            self.replay_excluded += 1
            return False
        previous = self.pending.get(object_id)
        timestamp = received_at.astimezone(UTC)
        if previous is not None and timestamp < datetime.fromisoformat(previous["received_at"]):
            return False
        self.pending[object_id] = {
            "zone": zone,
            "metric": metric,
            "object_id": object_id,
            "value": float(value),
            "received_at": timestamp.isoformat(),
            "callback_count": 1 if previous is None else previous["callback_count"] + 1,
        }
        return True

    def drain(self, reported_at: datetime) -> dict:
        if reported_at.tzinfo is None or reported_at.utcoffset() is None:
            raise ValueError("report time must be timezone-aware")
        report = {
            "schema": "fixed-panel-native-api-callback-report-v1",
            "basis": "ingestor_receive_time_after_initial_subscription_state",
            "declared_route_sha256": ROUTE_SOURCE_SHA256,
            "runtime_instance_id": self.runtime_instance_id,
            "transport_generation": self.generation,
            "reported_at": reported_at.astimezone(UTC).isoformat(),
            "generation_changed_since_previous_report": self.generation_changed,
            "initial_state_callbacks_excluded": self.replay_excluded,
            "physical_serial_verified": False,
            "modbus_poll_time_verified": False,
            "callbacks": [self.pending[key] for key in sorted(self.pending)],
        }
        self.pending.clear()
        self.replay_excluded = 0
        self.generation_changed = False
        return report
