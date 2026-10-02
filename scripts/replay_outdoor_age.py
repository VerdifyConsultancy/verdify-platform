"""Causal outdoor-age merge from original source events, never cached action snapshots."""

import datetime as dt
import json
import math


def instant(value):
    timestamp = dt.datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("outdoor source timestamp must be timezone-aware")
    return timestamp


class OutdoorAge:
    def __init__(self):
        self.seen = set()
        self.active = None
        self.native_started = False

    def observe(self, event):
        self.active = None
        if event["event_kind"] != "weather":
            return
        key = (event["source_runtime_instance_id"], event["source_connection_generation"])
        first = key not in self.seen
        self.seen.add(key)
        # Parse exactly the one original callback object; never merge partial rows.
        try:
            payload = json.loads(event["value"])
            # Old emitter history has no exact-age capability. Once observed,
            # every subsequent missing/malformed event stays stale without proxy fallback.
            if "outdoor_data_age_s" in payload:
                self.native_started = True
            if first or int(key[1]) < 1:
                return
            age = payload["outdoor_data_age_s"]
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age):
                return
            # 99999 is the device's explicit missing-input stale sentinel.
            if not 0 <= age <= 99999 or not isinstance(payload.get("outdoor_fresh"), bool):
                return
            self.active = (dict(event), float(age), payload["outdoor_fresh"])
        except (ValueError, TypeError, KeyError):
            return

    def project(self, climate):
        if not self.native_started:
            return {
                "outdoor_data_age_s": climate.get("outdoor_data_age_s", ""),
                "outdoor_observation_ts": climate.get("outdoor_observation_ts", ""),
                "outdoor_freshness_basis": "conservative_change_observation",
            }
        result = {
            "outdoor_data_age_s": "",
            "outdoor_observation_ts": "",
            "outdoor_freshness_basis": "native_unavailable_stale",
        }
        if self.active is None:
            return result
        event, reported, fresh = self.active
        elapsed = (instant(climate["ts"]) - instant(event["ts"])).total_seconds()
        if elapsed < 0:
            raise ValueError("future outdoor source event")
        # Ceiling never makes a fractional elapsed age younger on uint replay conversion.
        age = min(99999, math.ceil(reported + elapsed)) if fresh else 99999
        return {
            "outdoor_data_age_s": age,
            "outdoor_observation_ts": event["ts"],
            "outdoor_freshness_basis": "device_reported_original_callback",
            "outdoor_device_reported_age_s": reported,
            "outdoor_device_event_id": event["event_id"],
            "outdoor_device_event_sha256": event["event_sha256"],
            "outdoor_device_runtime_id": event["source_runtime_instance_id"],
            "outdoor_device_generation": event["source_connection_generation"],
        }
