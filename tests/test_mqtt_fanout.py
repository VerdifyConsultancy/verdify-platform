"""MQTT telemetry fan-out gating + round-trip tests (#113 / #114).

Mirrors the #79 device-write gate test discipline: the publish-all and
subscribe modes are env-gated (default-deny / default-off), exact-string match,
and mutually exclusive. A regression that flips a default on, weakens the match,
or lets a subscriber re-publish (fan-out loop) fails here.

Pure unit tests — no broker, no DB. The FanoutPublisher's paho client is mocked.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import mqtt_fanout  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_MQTT_PUBLISH_ALL", raising=False)
    monkeypatch.delenv("VERDIFY_INGEST_SOURCE", raising=False)
    monkeypatch.delenv("FANOUT_MQTT_TOPIC_ROOT", raising=False)
    yield


# ── publish-all gate (#113) ──────────────────────────────────────────────────
def test_publish_all_default_off():
    assert mqtt_fanout.publish_all_enabled() is False


def test_publish_all_only_exact_one(monkeypatch):
    for val in ("0", "true", "yes", "TRUE", "2", "on", ""):
        monkeypatch.setenv("VERDIFY_MQTT_PUBLISH_ALL", val)
        assert mqtt_fanout.publish_all_enabled() is False, f"{val!r} must not enable"
    monkeypatch.setenv("VERDIFY_MQTT_PUBLISH_ALL", "1")
    assert mqtt_fanout.publish_all_enabled() is True


# ── subscribe mode gate (#114) ───────────────────────────────────────────────
def test_subscribe_default_off():
    assert mqtt_fanout.subscribe_mode_enabled() is False


def test_subscribe_only_mqtt_subscribe(monkeypatch):
    for val in ("", "device", "esp32", "mqtt", "subscribe", "ha"):
        monkeypatch.setenv("VERDIFY_INGEST_SOURCE", val)
        assert mqtt_fanout.subscribe_mode_enabled() is False, f"{val!r} must not enable"
    for val in ("mqtt-subscribe", "MQTT-Subscribe", " mqtt-subscribe "):
        monkeypatch.setenv("VERDIFY_INGEST_SOURCE", val)
        assert mqtt_fanout.subscribe_mode_enabled() is True, f"{val!r} should enable"


# ── mutual exclusion (no self-feeding fan-out loop) ──────────────────────────
def test_modes_consistent_when_neither(monkeypatch):
    mqtt_fanout.assert_modes_consistent()  # no raise


def test_modes_consistent_when_only_publish(monkeypatch):
    monkeypatch.setenv("VERDIFY_MQTT_PUBLISH_ALL", "1")
    mqtt_fanout.assert_modes_consistent()


def test_modes_consistent_when_only_subscribe(monkeypatch):
    monkeypatch.setenv("VERDIFY_INGEST_SOURCE", "mqtt-subscribe")
    mqtt_fanout.assert_modes_consistent()


def test_modes_inconsistent_when_both(monkeypatch):
    monkeypatch.setenv("VERDIFY_MQTT_PUBLISH_ALL", "1")
    monkeypatch.setenv("VERDIFY_INGEST_SOURCE", "mqtt-subscribe")
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        mqtt_fanout.assert_modes_consistent()


# ── topic layout ─────────────────────────────────────────────────────────────
def test_topic_for_default_root():
    assert mqtt_fanout.topic_for("climate", "vallery") == "verdify/fanout/climate/vallery"


def test_topic_for_custom_root(monkeypatch):
    monkeypatch.setenv("FANOUT_MQTT_TOPIC_ROOT", "x/y/")
    assert mqtt_fanout.topic_for("diagnostics", "gh1") == "x/y/diagnostics/gh1"


def test_subscribe_topic_filter():
    assert mqtt_fanout.subscribe_topic_filter() == "verdify/fanout/+/+"


# ── encode / decode round-trip ───────────────────────────────────────────────
def test_encode_decode_round_trip():
    ts = datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC)
    row = {"ts": ts, "temp_f": 72.5, "rh_pct": 55.0}
    topic, payload = mqtt_fanout.encode_row("climate", "vallery", row)
    assert topic == "verdify/fanout/climate/vallery"
    table, ghid, decoded = mqtt_fanout.decode_payload(payload)
    assert table == "climate"
    assert ghid == "vallery"
    assert decoded["temp_f"] == 72.5
    # ts is serialized ISO-8601; subscriber re-parses to datetime downstream.
    assert decoded["ts"] == ts.isoformat()


def test_decode_rejects_unknown_table():
    payload = json.dumps({"table": "secrets", "greenhouse_id": "vallery", "row": {"x": 1}})
    with pytest.raises(ValueError, match="non-allow-listed"):
        mqtt_fanout.decode_payload(payload)


def test_decode_rejects_empty_row():
    payload = json.dumps({"table": "climate", "greenhouse_id": "vallery", "row": {}})
    with pytest.raises(ValueError, match="empty row"):
        mqtt_fanout.decode_payload(payload)


def test_decode_rejects_missing_greenhouse():
    payload = json.dumps({"table": "climate", "greenhouse_id": "", "row": {"x": 1}})
    with pytest.raises(ValueError, match="greenhouse_id"):
        mqtt_fanout.decode_payload(payload)


# ── publisher behaviour ──────────────────────────────────────────────────────
def test_publisher_drops_non_allow_listed_table():
    pub = mqtt_fanout.FanoutPublisher("h", 1883)
    pub._client = MagicMock()
    assert pub.publish_row("not_a_table", "vallery", {"x": 1}) is False
    pub._client.publish.assert_not_called()


def test_publisher_drops_before_connect():
    pub = mqtt_fanout.FanoutPublisher("h", 1883)
    # _client is None (never connected)
    assert pub.publish_row("climate", "vallery", {"ts": datetime.now(UTC)}) is False


def test_publisher_publishes_when_connected():
    pub = mqtt_fanout.FanoutPublisher("h", 1883)
    pub._client = MagicMock()
    ts = datetime(2026, 5, 31, tzinfo=UTC)
    ok = pub.publish_row("climate", "vallery", {"ts": ts, "temp_f": 70.0})
    assert ok is True
    pub._client.publish.assert_called_once()
    args, kwargs = pub._client.publish.call_args
    assert args[0] == "verdify/fanout/climate/vallery"
    body = json.loads(args[1])
    assert body["table"] == "climate"
    assert body["row"]["temp_f"] == 70.0
    assert kwargs.get("retain") is False
    assert kwargs.get("qos") == 0


def test_publisher_swallows_publish_errors():
    """A bus error must NOT propagate — local DB write is the source of truth."""
    pub = mqtt_fanout.FanoutPublisher("h", 1883)
    client = MagicMock()
    client.publish.side_effect = RuntimeError("broker gone")
    pub._client = client
    assert pub.publish_row("climate", "vallery", {"ts": datetime.now(UTC)}) is False
