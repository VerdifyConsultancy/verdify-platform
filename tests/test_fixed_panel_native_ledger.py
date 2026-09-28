"""Prospective callback ledger keeps source order and fails closed on gaps."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import fixed_panel_source as source  # noqa: E402

import ingestor  # noqa: E402
from verdify_schemas.fixed_panel_native import FixedPanelNativeEvent  # noqa: E402

REVISION = "a" * 40
RUNTIME = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
AT = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def tracker(max_pending_events: int = 8192) -> source.NativeProbeTracker:
    return source.NativeProbeTracker(
        RUNTIME,
        collector_revision=REVISION,
        ledger_enabled=True,
        max_pending_events=max_pending_events,
    )


def test_callback_events_require_replay_exclusion_and_current_generation():
    evidence = tracker()
    north = source.ROUTES["north"]["temp"]
    evidence.mark_connected(7, AT)
    assert not evidence.observe(north, 75.0, AT + timedelta(seconds=1), 7, "fw-1")
    assert evidence.observe(north, 75.0, AT + timedelta(seconds=11), 7, "fw-1")
    assert not evidence.observe(north, 77.0, AT + timedelta(seconds=12), 6, "fw-1")
    evidence.mark_disconnected(AT + timedelta(seconds=20))
    events = evidence.peek_events()
    assert [(event.source_sequence, event.kind) for event in events] == [
        (1, "connected"),
        (2, "callback"),
        (3, "gap"),
    ]
    assert events[1].received_at == AT + timedelta(seconds=11)
    assert events[1].firmware_revision == "fw-1"
    assert events[2].reason == "transport_disconnected"
    assert events[1].physical_serial_verified is False
    assert events[1].modbus_poll_time_verified is False
    evidence.acknowledge_through(2)
    assert [event.source_sequence for event in evidence.peek_events()] == [3]


def test_retry_queue_overflow_has_a_sticky_gap_and_new_generation():
    evidence = tracker(max_pending_events=4)
    north = source.ROUTES["north"]["temp"]
    evidence.mark_connected(1, AT)
    evidence.observe(north, 75.0, AT + timedelta(seconds=1), 1, "fw-1")
    for second in range(2, 8):
        evidence.observe(north, 75.0, AT + timedelta(seconds=second), 1, "fw-1")
    assert any(event.kind == "gap" and event.reason == "buffer_overflow" for event in evidence.peek_events())
    previous_sequence = evidence.source_sequence
    evidence.mark_connected(2, AT + timedelta(seconds=10))
    assert evidence.peek_events()[-1].source_sequence > previous_sequence
    assert evidence.peek_events()[-1].kind == "connected"
    assert evidence.peek_events()[-1].transport_generation == 2


def test_schema_rejects_nonfinite_values_and_physical_claims():
    evidence = tracker()
    evidence.mark_connected(1, AT)
    north = source.ROUTES["north"]["temp"]
    evidence.observe(north, 75.0, AT, 1, "fw-1")
    evidence.observe(north, 76.0, AT + timedelta(seconds=1), 1, "fw-1")
    payload = evidence.peek_events()[-1].model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError):
        FixedPanelNativeEvent.model_validate({**payload, "physical_serial_verified": True})
    with pytest.raises(ValidationError):
        FixedPanelNativeEvent.model_validate({**payload, "value": float("inf")})
    with pytest.raises(ValidationError):
        FixedPanelNativeEvent.model_validate({**payload, "value": 181.0})
    with pytest.raises(ValidationError):
        FixedPanelNativeEvent.model_validate({**payload, "firmware_revision": ""})
    with pytest.raises(ValueError, match="source SHA"):
        source.NativeProbeTracker(RUNTIME, ledger_enabled=True)
    assert not evidence.observe(north, 181.0, AT + timedelta(seconds=2), 1, "fw-1")


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, fail: bool):
        self.fail = fail
        self.payloads: list[str] = []

    def transaction(self):
        return _Transaction()

    async def fetchval(self, query, payload):
        assert query == "SELECT public.fn_append_fixed_panel_native_event($1::jsonb)"
        self.payloads.append(payload)
        if self.fail:
            raise OSError("injected DB outage")
        return True


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, fail: bool):
        self.connection = _Connection(fail)

    def acquire(self):
        return _Acquire(self.connection)


def test_db_error_keeps_events_for_exact_retry(monkeypatch):
    evidence = tracker()
    evidence.mark_connected(1, AT)
    monkeypatch.setattr(ingestor.state, "fixed_panel_native", evidence)
    with pytest.raises(OSError, match="injected DB outage"):
        asyncio.run(ingestor.write_fixed_panel_native_events(_Pool(True)))
    assert len(evidence.peek_events()) == 1
    good = _Pool(False)
    asyncio.run(ingestor.write_fixed_panel_native_events(good))
    assert len(evidence.peek_events()) == 0
    assert '"kind": "connected"' in good.connection.payloads[0]


def test_261_is_inert_until_post_260_seal_and_keeps_257_publication_separate():
    migration = (ROOT / "db/migrations/261-fixed-panel-native-callback-ledger.sql").read_text()
    assert "__PIN_260_LEDGER_SHA256__" in migration
    assert "261 refuses unsealed post-260 ledger or ordinary boundary" in migration
    assert "add_retention_policy('public.fixed_panel_native_events', interval '90 days')" in migration
    assert "CREATE TABLE public.fixed_panel_native_sessions" in migration
    assert "CREATE TABLE public.fixed_panel_native_day_receipts" in migration
    assert "CREATE FUNCTION public.fn_freeze_fixed_panel_native_day" in migration
    assert "REVOKE ALL ON public.fixed_panel_native_day_receipts" in migration
    assert "physical_proof_eligible', false" in migration
    assert "CREATE FUNCTION public.fn_fixed_panel_native_day_projection" in migration
    assert "route_only_crop_band_publications" not in migration
