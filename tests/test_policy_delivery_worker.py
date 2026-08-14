"""policy_delivery worker (#584 Lane C) — pure-logic tests.

Proves, without a live DB or device:
- feature-off inertness and the single-writer gating matrix (writer lease +
  #79 device-write gate) BEFORE any DB access;
- graceful no-op when the device lacks the Lane E policy services;
- happy path: staged begin/chunk/validate/commit through the transport, the
  device snapshot recorded, and fn_open_exposure called ONLY on an exact
  schema/generation/assignment/activation echo (contract v2, #586: the
  aggregated policy_identity echo carries the FULL activation hash; content
  identity is bound inside it per audit §8.9);
- echo mismatch: exposure never opens, open exposures close with a bounded
  reason, the transaction aborts, and the outbox row requeues with a bounded
  error class + backoff;
- stage failure: attempt row recorded, abort sent, outbox requeued.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from policy_transport import (  # noqa: E402
    ERROR_CLASS_CONNECTION,
    FakePolicyTransport,
    PolicyDeviceIdentity,
)
from tasks import policy_delivery  # noqa: E402
from tasks.policy_delivery import policy_delivery_worker  # noqa: E402
from test_experiment_workers import FakeConn, FakePool, ForbiddenPool  # noqa: E402

from verdify_schemas.policy_vector import encode_policy_vector, wire_fields  # noqa: E402
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION  # noqa: E402

EXPERIMENT_ID = str(uuid.uuid4())
ASSIGNMENT_ID = str(uuid.uuid4())
VECTOR_ID = str(uuid.uuid4())
OUTBOX_ID = str(uuid.uuid4())
DEVICE_ID = "esp32-vallery"

CONTENT_SHA = "a" * 64
ACTIVATION_SHA = "b" * 64


def _vector_bytes() -> bytes:
    return encode_policy_vector(
        {d.name: (bool(d.default) if d.wire_kind == "bool" else float(d.default)) for d in wire_fields()}
    )


def _run(coro):
    return asyncio.run(coro)


def _lease_row():
    return {"outbox_id": OUTBOX_ID, "device_id": DEVICE_ID, "vector_id": VECTOR_ID, "attempt_count": 1}


def _vector_row(status="ready"):
    return {
        "vector_id": VECTOR_ID,
        "assignment_id": ASSIGNMENT_ID,
        "experiment_id": EXPERIMENT_ID,
        "greenhouse_id": "vallery",
        "device_generation": 7,
        "canonical_bytes": _vector_bytes(),
        "content_sha256": CONTENT_SHA,
        "activation_sha256": ACTIVATION_SHA,
        "status": status,
        "valid_to": None,
        "assignment_status": "active",
    }


def _exact_identity():
    return PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION,
        device_generation=7,
        assignment_id=ASSIGNMENT_ID,
        activation_sha256=ACTIVATION_SHA,
        apply_state="active",
    )


def _conn(extra=()):
    return FakeConn(
        [
            ("UPDATE policy_delivery_outbox\n   SET state = 'leased'", _lease_row()),
            ("FROM effective_policy_vectors v", _vector_row()),
            ("fn_record_device_snapshot", 101),
            ("FROM policy_exposures", []),
            ("fn_open_exposure", str(uuid.uuid4())),
            *extra,
        ]
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED", raising=False)
    monkeypatch.setattr(policy_delivery, "_services_unavailable_logged", False)
    monkeypatch.setattr(policy_delivery, "_READBACK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(policy_delivery, "_READBACK_POLL_S", 0.01)


def _enable(monkeypatch, transport):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(policy_delivery, "_transport_factory", lambda: transport)


# ── Gating matrix (all BEFORE any DB access) ────────────────────────────────


def test_feature_off_default_env_touches_nothing():
    _run(policy_delivery_worker(ForbiddenPool()))


def test_shadow_mode_never_delivers(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "shadow")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    _run(policy_delivery_worker(ForbiddenPool()))


def test_device_write_gate_closed_leaves_outbox_alone(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    # VERDIFY_DEVICE_WRITE_ENABLED unset => default-deny.
    _run(policy_delivery_worker(ForbiddenPool()))


def test_writer_lease_not_held_leaves_outbox_alone(monkeypatch):
    import shared

    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "writer_lease_held", lambda: False)
    _run(policy_delivery_worker(ForbiddenPool()))


def test_missing_device_services_noop_logs_once(monkeypatch, caplog):
    import logging

    transport = FakePolicyTransport(is_available=False)
    _enable(monkeypatch, transport)
    with caplog.at_level(logging.WARNING, logger="tasks"):
        _run(policy_delivery_worker(ForbiddenPool()))
        _run(policy_delivery_worker(ForbiddenPool()))
    warnings = [r for r in caplog.records if "policy transport services" in r.getMessage()]
    assert len(warnings) == 1, "unavailable-services warning must log once, not per cycle"


# ── Happy path: exact echo opens the exposure ───────────────────────────────


def test_exact_echo_opens_exposure_and_activates(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _ in transport.calls]
    assert stages[0] == "begin"
    assert stages.count("chunk") >= 2  # ~210-byte vector => 3 chunks of 96
    assert "validate" in stages and "commit" in stages
    assert stages.index("validate") < stages.index("commit")
    assert "abort" not in stages

    assert len(conn.sql_calls("fn_record_device_snapshot")) == 1
    opens = conn.sql_calls("fn_open_exposure")
    assert len(opens) == 1
    assert opens[0][2][:3] == (VECTOR_ID, DEVICE_ID, 101)
    activated = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "activated"]
    assert len(activated) == 1


def test_begin_payload_carries_the_exact_vector_identity(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    request = next(payload for name, payload in transport.calls if name == "begin")
    assert request.device_generation == 7
    assert request.content_sha256 == CONTENT_SHA
    assert request.activation_sha256 == ACTIVATION_SHA
    assert request.canonical_bytes == _vector_bytes()


# ── Mismatch / timeout: never an exposure; bounded requeue ──────────────────


def test_hash_mismatch_requeues_and_never_opens_exposure(monkeypatch):
    identity = PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION,
        device_generation=7,
        assignment_id=ASSIGNMENT_ID,
        activation_sha256="c" * 64,  # wrong activation (content identity is bound inside it, §8.9)
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=identity)
    _enable(monkeypatch, transport)
    open_exposure = {"exposure_id": str(uuid.uuid4())}
    conn = _conn(extra=[])
    conn.responders = [
        ("FROM policy_exposures", [open_exposure]) if frag == "FROM policy_exposures" else (frag, res)
        for frag, res in conn.responders
    ]
    _run(policy_delivery_worker(FakePool(conn)))

    assert conn.sql_calls("fn_open_exposure") == []
    # The stale open exposure closed with a bounded reason.
    closes = conn.sql_calls("fn_close_exposure")
    assert len(closes) == 1 and closes[0][2][1] == "protocol_deviation"
    # Requeued failed with hash_mismatch and a backoff.
    failed = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "failed"]
    assert len(failed) == 1 and failed[0][2][2] == "hash_mismatch"
    assert ("abort", "echo:hash_mismatch") in transport.calls


def test_generation_conflict_classification(monkeypatch):
    identity = _exact_identity()
    identity = PolicyDeviceIdentity(
        schema_revision=identity.schema_revision,
        device_generation=6,  # stale generation
        assignment_id=identity.assignment_id,
        activation_sha256=identity.activation_sha256,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=identity)
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    assert conn.sql_calls("fn_open_exposure") == []
    failed = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "failed"]
    assert len(failed) == 1 and failed[0][2][2] == "generation_conflict"


def test_schema_mismatch_classification(monkeypatch):
    identity = _exact_identity()
    identity = PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION + 1,  # firmware compiled a different wire schema
        device_generation=identity.device_generation,
        assignment_id=identity.assignment_id,
        activation_sha256=identity.activation_sha256,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=identity)
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    assert conn.sql_calls("fn_open_exposure") == []
    failed = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "failed"]
    assert len(failed) == 1 and failed[0][2][2] == "schema_mismatch"


def test_snapshot_records_no_content_hash_under_contract_v2(monkeypatch):
    """The device echoes no separate content hash (bound inside activation,
    §8.9): fn_record_device_snapshot must receive NULL content."""
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    snapshots = conn.sql_calls("fn_record_device_snapshot")
    assert len(snapshots) == 1
    args = snapshots[0][2]
    assert args[2] == str(WIRE_SCHEMA_VERSION)  # schema_revision echo
    assert args[5] is None  # p_content_sha256
    assert args[6] == ACTIVATION_SHA  # p_activation_sha256 (full hash)


def test_readback_timeout_requeues_with_timeout_class(monkeypatch):
    transport = FakePolicyTransport(identity=None)  # device never echoes
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    assert conn.sql_calls("fn_open_exposure") == []
    assert conn.sql_calls("fn_record_device_snapshot") == []
    failed = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "failed"]
    assert len(failed) == 1 and failed[0][2][2] == "timeout"


def test_stage_failure_records_attempt_aborts_and_requeues(monkeypatch):
    transport = FakePolicyTransport(fail_stage="validate", fail_error_class=ERROR_CLASS_CONNECTION)
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))

    attempts = conn.sql_calls("INSERT INTO policy_delivery_attempts")
    failed_stages = [(a[2][2], a[2][3], a[2][4]) for a in attempts if a[2][3] is False]
    assert failed_stages == [("validate", False, "connection")]
    assert any(name == "abort" for name, _ in transport.calls)
    assert "commit" not in [name for name, _ in transport.calls]
    failed = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "failed"]
    assert len(failed) == 1 and failed[0][2][2] == "connection"


def test_exhausted_attempts_abandon(monkeypatch):
    transport = FakePolicyTransport(identity=None)
    _enable(monkeypatch, transport)
    lease = _lease_row() | {"attempt_count": 10}
    conn = FakeConn(
        [
            ("UPDATE policy_delivery_outbox\n   SET state = 'leased'", lease),
            ("FROM effective_policy_vectors v", _vector_row()),
            ("FROM policy_exposures", []),
        ]
    )
    _run(policy_delivery_worker(FakePool(conn)))
    abandoned = [c for c in conn.sql_calls("UPDATE policy_delivery_outbox") if len(c[2]) > 1 and c[2][1] == "abandoned"]
    assert len(abandoned) == 1
