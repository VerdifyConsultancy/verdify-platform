"""Event-driven greenhouse occupancy from Frigate over MQTT (#1370).

Jason, 2026-11-05: "The system should monitor MQTT topics for occupancy and
deliver the change real-time for lights."

Frigate (jvallery/sentinel, cameras ``greenhouse_1`` and ``greenhouse_2``)
publishes the number of people it sees in ``greenhouse_zone`` on
``frigate/greenhouse_zone/person`` on the Home Assistant OS broker. This module
subscribes to that topic with the ingestor's ``verdify-mqtt`` login
(``MQTT_USER``/``MQTT_PASS``, read only) and feeds every sighting into the same
occupancy latch as the 300 s Home Assistant poll (``occupancy.sync_occupancy_state``),
which pushes the latched state to the controller at once. The poll stays as the
fallback when the broker or this subscriber is down.

The lights themselves stay with their one writer, the controller (#399): its
lighting policy turns a circuit on for occupancy when the outdoor lux is below
that circuit's ``gl_*_lux_threshold`` inside its photoperiod window
(``occupancy_lux_low``), and returns it to its automatic decision when the latch
clears, ``OCCUPANCY_LATCH_MIN`` (15 min) after the last sighting. This module only
makes the sighting reach the controller within seconds instead of up to 300 s.

Sightings:

- a count above zero is a sighting; the first one after a zero starts a visit,
  whose start caps the latch at ``OCCUPANCY_VISIT_MAX_MIN`` like the poll's ON edge;
- while the count stays above zero (Frigate publishes only changes), a sighting
  is re-recorded every ``HEARTBEAT_S`` so the latch keeps running past the last
  change, up to the visit cap;
- the drop to zero is the last sighting: the latch then runs 15 min from it;
- retained messages and payloads that are not a count are ignored, so a stale
  retained count after a broker or Frigate restart cannot start a visit.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from occupancy import OCCUPANCY_VISIT_MAX_MIN, sync_occupancy_state

log = logging.getLogger("occupancy_mqtt")

SOURCE = "frigate_mqtt"
CLIENT_ID = "verdify-ingestor-frigate-occupancy"
# Frigate's per-zone person count (topic_prefix frigate, zone greenhouse_zone on
# both greenhouse cameras). Comma-separated; any topic above zero is a sighting.
DEFAULT_TOPICS = "frigate/greenhouse_zone/person"
HEARTBEAT_S = 60.0


def _enabled() -> bool:
    return os.environ.get("OCCUPANCY_MQTT_ENABLED", "1") == "1"


def _topics() -> tuple[str, ...]:
    raw = os.environ.get("OCCUPANCY_MQTT_TOPICS", DEFAULT_TOPICS)
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def parse_person_count(payload: bytes | str) -> int | None:
    """Frigate's count payload (``"0"``, ``"2"``) as an int, or None for anything else."""
    text = payload.decode(errors="replace") if isinstance(payload, bytes) else payload
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


@dataclass(frozen=True)
class Sighting:
    """One occupied observation for the latch: when, and when its visit started."""

    observed_at: datetime
    visit_started_at: datetime
    edge: bool  # the first sighting of a visit


class FrigatePersonTracker:
    """Turns Frigate count messages into latch observations. Pure: no I/O, the caller passes the time."""

    def __init__(self, topics: tuple[str, ...]):
        self.topics = frozenset(topics)
        self.counts: dict[str, int] = {}
        self.visit_started_at: datetime | None = None
        self.last_recorded_at: datetime | None = None

    @property
    def present(self) -> bool:
        return any(count > 0 for count in self.counts.values())

    def on_message(self, topic: str, payload: bytes | str, retain: bool, now: datetime) -> Sighting | None:
        if retain or topic not in self.topics:
            return None
        count = parse_person_count(payload)
        if count is None:
            log.warning("Occupancy MQTT: ignoring a non-count payload on %s", topic)
            return None
        was_present = self.present
        self.counts[topic] = count
        if self.present:
            edge = not was_present
            if edge:
                self.visit_started_at = now
            return self._record(now, edge)
        if was_present:
            # The count fell to zero: this is the last sighting of the visit.
            sighting = self._record(now, False)
            self.visit_started_at = None
            return sighting
        return None

    def heartbeat(self, now: datetime) -> Sighting | None:
        """Re-record a visit whose count has not changed, until the visit cap."""
        if not self.present or self.visit_started_at is None or self.last_recorded_at is None:
            return None
        if now - self.last_recorded_at < timedelta(seconds=HEARTBEAT_S):
            return None
        if now - self.visit_started_at >= timedelta(minutes=OCCUPANCY_VISIT_MAX_MIN):
            return None
        return self._record(now, False)

    def reset(self) -> None:
        """Forget the counts (a lost connection): only fresh messages count again."""
        self.counts.clear()
        self.visit_started_at = None
        self.last_recorded_at = None

    def _record(self, now: datetime, edge: bool) -> Sighting:
        assert self.visit_started_at is not None
        self.last_recorded_at = now
        return Sighting(observed_at=now, visit_started_at=self.visit_started_at, edge=edge)


async def record_sighting(pool: asyncpg.Pool, sighting: Sighting) -> None:
    if sighting.edge:
        log.info("Occupancy MQTT: person seen at %s", sighting.observed_at.isoformat())
    await sync_occupancy_state(
        pool,
        True,
        SOURCE,
        observed_at=sighting.observed_at,
        visit_started_at=sighting.visit_started_at,
    )


async def occupancy_mqtt_loop(pool: asyncpg.Pool) -> None:
    """Subscribe to Frigate's greenhouse person count and feed the occupancy latch. Never raises."""
    if not _enabled():
        log.info("Occupancy MQTT: disabled (OCCUPANCY_MQTT_ENABLED != 1); the Home Assistant poll is the only input")
        return
    host = os.environ.get("MQTT_HOST", "")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_USER", "")
    password = os.environ.get("MQTT_PASS", "")
    topics = _topics()
    if not host or not user or not topics:
        log.warning("Occupancy MQTT: no broker, login or topic configured; the Home Assistant poll is the only input")
        return

    import paho.mqtt.client as paho_mqtt

    event_loop = asyncio.get_running_loop()
    tracker = FrigatePersonTracker(topics)
    lock = asyncio.Lock()

    async def _handle(topic: str, payload: bytes, retain: bool) -> None:
        async with lock:
            sighting = tracker.on_message(topic, payload, retain, datetime.now(UTC))
            if sighting is not None:
                await record_sighting(pool, sighting)

    def _done(future: Any) -> None:
        exc = future.exception()
        if exc is not None:
            log.error("Occupancy MQTT: recording a sighting failed: %s", exc)

    def on_connect(client, _userdata, _flags, rc):
        if rc == 0:
            client.subscribe([(topic, 0) for topic in topics])
            log.info("Occupancy MQTT: connected to %s:%d, subscribed to %s", host, port, ", ".join(topics))
        else:
            log.error("Occupancy MQTT: connect refused rc=%d", rc)

    def on_disconnect(_client, _userdata, rc):
        log.warning("Occupancy MQTT: disconnected rc=%d; paho reconnects", rc)
        event_loop.call_soon_threadsafe(tracker.reset)

    def on_message(_client, _userdata, msg):
        future = asyncio.run_coroutine_threadsafe(_handle(msg.topic, msg.payload, bool(msg.retain)), event_loop)
        future.add_done_callback(_done)

    client = paho_mqtt.Client(client_id=CLIENT_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.username_pw_set(user, password)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect_async(host, port, 60)
        client.loop_start()
        while True:
            await asyncio.sleep(5)
            try:
                async with lock:
                    sighting = tracker.heartbeat(datetime.now(UTC))
                    if sighting is not None:
                        await record_sighting(pool, sighting)
            except Exception as exc:  # noqa: BLE001
                log.error("Occupancy MQTT: heartbeat failed: %s", exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("Occupancy MQTT: subscriber stopped: %s; the Home Assistant poll stays the input", exc)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
