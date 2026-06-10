"""MQTT telemetry fan-out bus (#113 / #114).

The fan-out bus decouples telemetry *capture* (prod, the single env that talks
to the ESP32 + Home Assistant + Tempest/Shelly) from telemetry *consumption*
(dev/stage, which never touch any device). In the k3s topology:

* prod's ingestor runs in **publish-all** mode (#113): every row it flushes to
  its own DB is ALSO published as JSON to the fan-out broker
  (``VERDIFY_MQTT_PUBLISH_ALL=1``).
* dev/stage ingestors run in **subscribe** mode (#114):
  ``VERDIFY_INGEST_SOURCE=mqtt-subscribe``. They open NO ESP32 connection, no
  Home Assistant session, no occupancy bridge — they only subscribe to the
  prod-published topics and write the same rows into their OWN per-env DB.

Both modes are env-gated and default OFF, so today's VM-side single ingestor is
unaffected (publish_all_enabled() and subscribe_mode_enabled() both False ->
fan-out is inert).

This module is kept OUT of ingestor.py (mirrors esp32_push.py) so tests and
tasks.py can import the gating + topic logic without importing the service
entrypoint / spinning up the asyncio loops.

Topic layout (retained=False; telemetry is a stream, not state):
    {FANOUT_MQTT_TOPIC_ROOT}/{table}/{greenhouse_id}
e.g. ``verdify/fanout/climate/vallery``. Payload is a JSON object of the row's
columns (timestamps ISO-8601, UTC).

Safety invariants enforced here:
* subscribe mode NEVER enables device writes — it cannot, because it carries no
  ESP32 client and the #79 VERDIFY_DEVICE_WRITE_ENABLED gate is independent. We
  additionally assert the two modes are mutually exclusive (a single ingestor is
  either the publisher OR a subscriber, never both).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

log = logging.getLogger("mqtt_fanout")

# Tables whose flushed rows are eligible for fan-out. Kept explicit (allow-list)
# so a new write path does not silently start leaking onto the bus. dev/stage
# subscribers route each table back to the same INSERT on their own DB.
FANOUT_TABLES: tuple[str, ...] = (
    "climate",
    "equipment_state",
    "system_state",
    "setpoint_snapshot",
    "diagnostics",
)


# ── Mode gating (read env at call time so tests can toggle) ──────────────────
def publish_all_enabled() -> bool:
    """True only when VERDIFY_MQTT_PUBLISH_ALL == '1' (default-deny).

    This is the prod-only publish-all mode (#113). Exact-string match mirrors
    the #79 device-write gate so 'true'/'yes'/'2' do NOT accidentally enable it.
    """
    return os.environ.get("VERDIFY_MQTT_PUBLISH_ALL", "") == "1"


def subscribe_mode_enabled() -> bool:
    """True when VERDIFY_INGEST_SOURCE == 'mqtt-subscribe' (dev/stage, #114).

    In this mode the ingestor reads telemetry from the fan-out bus instead of
    capturing it from the device, and runs NO device/HA loops.
    """
    return os.environ.get("VERDIFY_INGEST_SOURCE", "").strip().lower() == "mqtt-subscribe"


def assert_modes_consistent() -> None:
    """Guard: an ingestor is the publisher OR a subscriber, never both.

    Subscribe mode means 'I do not capture telemetry myself'; publish-all means
    'I capture AND re-emit'. Running both in one process would re-publish what we
    just consumed (a fan-out loop). Fail loudly at startup rather than create a
    self-feeding topic storm.
    """
    if publish_all_enabled() and subscribe_mode_enabled():
        raise RuntimeError(
            "Conflicting fan-out config: VERDIFY_MQTT_PUBLISH_ALL=1 and "
            "VERDIFY_INGEST_SOURCE=mqtt-subscribe are mutually exclusive. "
            "An ingestor is either the prod publisher or a dev/stage subscriber."
        )


# ── Topic + payload helpers ──────────────────────────────────────────────────
def _topic_root() -> str:
    return os.environ.get("FANOUT_MQTT_TOPIC_ROOT", "verdify/fanout").rstrip("/")


def topic_for(table: str, greenhouse_id: str) -> str:
    """Topic a publisher writes / a subscriber listens on for one table."""
    return f"{_topic_root()}/{table}/{greenhouse_id}"


def subscribe_topic_filter() -> str:
    """Wildcard a subscriber uses to receive every table for every greenhouse."""
    return f"{_topic_root()}/+/+"


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def encode_row(table: str, greenhouse_id: str, row: dict[str, Any]) -> tuple[str, str]:
    """Serialize one row to (topic, json_payload). The envelope carries table +
    greenhouse so a subscriber can route without parsing the topic string."""
    envelope = {"table": table, "greenhouse_id": greenhouse_id, "row": row}
    return topic_for(table, greenhouse_id), json.dumps(envelope, default=_json_default)


def decode_payload(payload: str) -> tuple[str, str, dict[str, Any]]:
    """Inverse of encode_row. Returns (table, greenhouse_id, row).

    Raises ValueError on malformed payloads (unknown table, missing keys) so the
    subscriber can drop a bad message instead of writing garbage into its DB.
    """
    obj = json.loads(payload)
    table = obj.get("table")
    greenhouse_id = obj.get("greenhouse_id")
    row = obj.get("row")
    if table not in FANOUT_TABLES:
        raise ValueError(f"fan-out payload for non-allow-listed table: {table!r}")
    if not isinstance(row, dict) or not row:
        raise ValueError("fan-out payload missing/empty row")
    if not isinstance(greenhouse_id, str) or not greenhouse_id:
        raise ValueError("fan-out payload missing greenhouse_id")
    return table, greenhouse_id, row


# ── Publisher (#113) ─────────────────────────────────────────────────────────
class FanoutPublisher:
    """Thin wrapper over a paho client that publishes flushed rows to the bus.

    Constructed only when publish_all_enabled(). connect() is best-effort: a bus
    outage must NEVER block the local DB flush (telemetry capture is Track A),
    so publish() swallows transport errors and logs them.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str = "",
        password: str = "",
        client_id: str = "verdify-fanout-pub",
    ) -> None:
        self.host = host
        self.port = port
        self._user = user
        self._password = password
        self._client_id = client_id
        self._client: Any = None
        self._connected = False

    def connect(self) -> None:
        import paho.mqtt.client as paho_mqtt

        client = paho_mqtt.Client(client_id=self._client_id)
        if self._user:
            client.username_pw_set(self._user, self._password)
        client.connect(self.host, self.port, 60)
        client.loop_start()
        self._client = client
        self._connected = True
        log.info("fan-out publisher connected to %s:%d", self.host, self.port)

    def publish_row(self, table: str, greenhouse_id: str, row: dict[str, Any]) -> bool:
        """Publish one row. Returns True if handed to the client, False on drop.

        Telemetry only — QoS 0, retain False. A drop is logged but never raised:
        the local DB write already succeeded and is the source of truth.
        """
        if table not in FANOUT_TABLES:
            log.debug("fan-out: skip non-allow-listed table %r", table)
            return False
        if self._client is None:
            log.debug("fan-out: publish before connect; dropping %s row", table)
            return False
        try:
            topic, payload = encode_row(table, greenhouse_id, row)
            self._client.publish(topic, payload, qos=0, retain=False)
            return True
        except Exception as e:  # noqa: BLE001 — bus errors must not break flush
            log.warning("fan-out publish failed (%s): %s", table, e)
            return False

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
            self._connected = False
