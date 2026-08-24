"""policy_delivery worker (#584 Lane C) — pure-logic tests.

Proves, without a live DB or device:
- feature-off inertness and the single-writer gating matrix (writer lease +
  #79 device-write gate) BEFORE any DB access;
- graceful no-op when the device lacks the Lane E policy services;
- happy path: staged begin/chunk/validate/commit through the transport, the
  device snapshot recorded, and the fenced finalizer called ONLY on an exact
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
from datetime import UTC, datetime, timedelta
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
LEASE_OWNER = "policy_delivery/test"

CONTENT_SHA = "a" * 64
ACTIVATION_SHA = "b" * 64


def _vector_bytes() -> bytes:
    return encode_policy_vector(
        {d.name: (bool(d.default) if d.wire_kind == "bool" else float(d.default)) for d in wire_fields()}
    )


def _run(coro):
    return asyncio.run(coro)


def _lease_row():
    return {
        "outbox_id": OUTBOX_ID,
        "device_id": DEVICE_ID,
        "vector_id": VECTOR_ID,
        "attempt_count": 1,
        "lease_expires_at": datetime.now(UTC) + timedelta(seconds=120),
    }


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
        "assignment_experiment_id": EXPERIMENT_ID,
    }


def _exact_identity():
    return PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION,
        device_generation=7,
        assignment_id=ASSIGNMENT_ID,
        activation_sha256=ACTIVATION_SHA,
        apply_state="active",
    )


def _conn(extra=(), *, vector=None):
    return FakeConn(
        [
            ("fn_runtime_v1_lease_delivery", _lease_row()),
            ("FROM effective_policy_vectors v", vector or _vector_row()),
            (
                "fn_runtime_v1_renew_delivery_lease",
                datetime.now(UTC) + timedelta(seconds=180),
            ),
            ("fn_runtime_v1_record_device_snapshot", 101),
            ("fn_runtime_v1_abandon_recovered_mismatch", 202),
            ("FROM policy_exposures", []),
            (
                "fn_runtime_v1_finalize_delivery",
                {"exposure_id": str(uuid.uuid4()), "superseded_count": 0},
            ),
            (
                "fn_runtime_v1_finalize_recovered_delivery",
                {"exposure_id": str(uuid.uuid4()), "superseded_count": 1},
            ),
            *extra,
        ]
    )


def _assert_atomic_failure(conn: FakeConn, error_class: str) -> None:
    failures = conn.sql_calls("fn_runtime_v1_fail_delivery")
    assert len(failures) == 1
    assert failures[0][2] == (OUTBOX_ID, LEASE_OWNER, 1, error_class)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED", raising=False)
    monkeypatch.setattr(policy_delivery, "_services_unavailable_logged", False)
    monkeypatch.setattr(policy_delivery, "_lease_owner", lambda: LEASE_OWNER)
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


def test_explicit_feature_off_with_active_experiment_touches_nothing(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
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


@pytest.mark.parametrize("lineage_field", ["experiment_id", "assignment_experiment_id"])
def test_mismatched_lineage_never_reaches_device_transport(monkeypatch, lineage_field):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    vector = _vector_row() | {lineage_field: str(uuid.uuid4())}
    conn = FakeConn(
        [
            ("fn_runtime_v1_lease_delivery", _lease_row()),
            ("FROM effective_policy_vectors v", vector),
        ]
    )
    _run(policy_delivery_worker(FakePool(conn)))
    assert not any(name == "begin" for name, _payload in transport.calls)
    assert conn.sql_calls("fn_runtime_v1_set_vector_state") == []
    abandoned = conn.sql_calls("fn_runtime_v1_abandon_delivery")
    assert len(abandoned) == 1
    assert abandoned[0][2] == (OUTBOX_ID, LEASE_OWNER, 1, "internal")


def test_locally_expired_lease_yields_before_first_device_mutation(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    expired = _lease_row() | {"lease_expires_at": datetime.now(UTC) + timedelta(seconds=4)}
    conn = FakeConn(
        [
            ("fn_runtime_v1_lease_delivery", expired),
            ("FROM effective_policy_vectors v", _vector_row()),
        ]
    )

    _run(policy_delivery_worker(FakePool(conn)))

    assert transport.calls == []


def test_assignment_closed_after_lease_atomically_aborts_and_abandons(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = FakeConn(
        [
            ("fn_runtime_v1_lease_delivery", _lease_row()),
            (
                "FROM effective_policy_vectors v",
                _vector_row() | {"assignment_status": "closed"},
            ),
        ]
    )

    _run(policy_delivery_worker(FakePool(conn)))

    assert transport.calls == []
    terminal = conn.sql_calls("fn_runtime_v1_abandon_delivery")
    assert len(terminal) == 1
    assert terminal[0][2] == (OUTBOX_ID, LEASE_OWNER, 1, "internal")
    assert conn.sql_calls("fn_runtime_v1_set_vector_state") == []
    assert conn.sql_calls("fn_runtime_v1_set_outbox_state") == []


def test_database_fence_loss_after_begin_yields_without_abort_or_more_device_io(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)

    def reject_obsolete_attempt(_args):
        raise policy_delivery.asyncpg.SerializationError("obsolete delivery lease")

    conn = _conn()
    conn.responders.insert(0, ("fn_runtime_v1_record_delivery_attempt", reject_obsolete_attempt))

    _run(policy_delivery_worker(FakePool(conn)))

    assert [name for name, _payload in transport.calls] == ["begin"]
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []


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

    snapshots = conn.sql_calls("fn_runtime_v1_record_device_snapshot")
    assert len(snapshots) == 1
    assert snapshots[0][2][:3] == (OUTBOX_ID, LEASE_OWNER, 1)
    finalizers = conn.sql_calls("fn_runtime_v1_finalize_delivery")
    assert len(finalizers) == 1
    assert finalizers[0][2] == (OUTBOX_ID, LEASE_OWNER, 1, 101)
    leases = conn.sql_calls("fn_runtime_v1_lease_delivery")
    assert len(leases) == 1
    assert leases[0][2] == (EXPERIMENT_ID, LEASE_OWNER)
    renewals = conn.sql_calls("fn_runtime_v1_renew_delivery_lease")
    assert len(renewals) == 1
    assert renewals[0][2] == (OUTBOX_ID, LEASE_OWNER, 1)

    # Every post-lease mutation is bound to the exact same owner/attempt token.
    mutation_calls = [
        call
        for call in conn.calls
        if any(
            function_name in call[1]
            for function_name in (
                "fn_runtime_v1_record_delivery_attempt",
                "fn_runtime_v1_set_outbox_state",
                "fn_runtime_v1_set_vector_state",
                "fn_runtime_v1_renew_delivery_lease",
                "fn_runtime_v1_record_device_snapshot",
                "fn_runtime_v1_finalize_delivery",
                "fn_runtime_v1_finalize_recovered_delivery",
            )
        )
    ]
    assert mutation_calls
    assert all(call[2][:3] == (OUTBOX_ID, LEASE_OWNER, 1) for call in mutation_calls)


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


def test_recovered_delivering_vector_skips_ready_transition(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    assert conn.sql_calls("fn_runtime_v1_set_vector_state") == []
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    recovered = conn.sql_calls("fn_runtime_v1_finalize_recovered_delivery")
    assert len(recovered) == 1
    assert recovered[0][2] == (OUTBOX_ID, LEASE_OWNER, 1, 101)
    assert [name for name, _payload in transport.calls] == ["read_identity"]


def test_recovered_delivering_vector_without_parseable_echo_requeues_device_dark(monkeypatch):
    transport = FakePolicyTransport(identity=None)
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    assert {name for name, _payload in transport.calls} == {"read_identity"}
    _assert_atomic_failure(conn, "timeout")
    assert conn.sql_calls("fn_runtime_v1_close_delivery_exposure") == []
    assert conn.sql_calls("fn_runtime_v1_record_device_snapshot") == []
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []


def test_recovered_same_generation_nonexact_active_echo_persists_and_abandons_device_dark(monkeypatch):
    identity = _exact_identity()
    wrong_identity = PolicyDeviceIdentity(
        schema_revision=identity.schema_revision,
        device_generation=identity.device_generation,
        assignment_id=identity.assignment_id,
        activation_sha256="c" * 64,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=wrong_identity)
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _payload in transport.calls]
    assert "begin" not in stages and "commit" not in stages and "abort" not in stages
    recovered = conn.sql_calls("fn_runtime_v1_abandon_recovered_mismatch")
    assert len(recovered) == 1
    assert recovered[0][2] == (
        OUTBOX_ID,
        LEASE_OWNER,
        1,
        "hash_mismatch",
        str(WIRE_SCHEMA_VERSION),
        7,
        ASSIGNMENT_ID,
        None,
        "c" * 64,
        "active",
        None,
    )
    assert conn.sql_calls("fn_runtime_v1_abandon_delivery") == []
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []


def test_recovered_different_assignment_identity_is_preserved_without_device_io(monkeypatch):
    observed_assignment_id = str(uuid.uuid4())
    wrong_identity = PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION,
        device_generation=7,
        assignment_id=observed_assignment_id,
        activation_sha256=ACTIVATION_SHA,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=wrong_identity)
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    assert [name for name, _payload in transport.calls] == ["read_identity"]
    recovered = conn.sql_calls("fn_runtime_v1_abandon_recovered_mismatch")
    assert len(recovered) == 1
    assert recovered[0][2][6] == observed_assignment_id


def test_recovered_mismatch_stale_fence_yields_without_compensating_device_io(monkeypatch):
    wrong_identity = PolicyDeviceIdentity(
        schema_revision=WIRE_SCHEMA_VERSION,
        device_generation=7,
        assignment_id=ASSIGNMENT_ID,
        activation_sha256="c" * 64,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=wrong_identity)
    _enable(monkeypatch, transport)

    def reject_stale_fence(_args):
        raise policy_delivery.asyncpg.SerializationError("obsolete delivery lease")

    conn = _conn(vector=_vector_row(status="delivering"))
    conn.responders = [
        (
            fragment,
            reject_stale_fence if fragment == "fn_runtime_v1_abandon_recovered_mismatch" else result,
        )
        for fragment, result in conn.responders
    ]

    _run(policy_delivery_worker(FakePool(conn)))

    assert [name for name, _payload in transport.calls] == ["read_identity"]
    assert conn.sql_calls("fn_runtime_v1_abandon_delivery") == []


def test_recovered_lower_active_generation_may_use_normal_staging(monkeypatch):
    identity = _exact_identity()
    prior_identity = PolicyDeviceIdentity(
        schema_revision=identity.schema_revision,
        device_generation=identity.device_generation - 1,
        assignment_id=identity.assignment_id,
        activation_sha256="c" * 64,
        apply_state="active",
    )
    transport = FakePolicyTransport(identity=prior_identity)
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _payload in transport.calls]
    assert "begin" in stages and "commit" in stages
    assert ("abort", "echo:generation_conflict") in transport.calls


def test_recovered_staged_echo_aborts_and_requeues_without_policy_begin(monkeypatch):
    identity = _exact_identity()
    staged_identity = PolicyDeviceIdentity(
        schema_revision=identity.schema_revision,
        device_generation=identity.device_generation,
        assignment_id=identity.assignment_id,
        activation_sha256=identity.activation_sha256,
        apply_state="staged",
    )
    transport = FakePolicyTransport(identity=staged_identity)
    _enable(monkeypatch, transport)
    conn = _conn(vector=_vector_row(status="delivering"))

    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _payload in transport.calls]
    assert "begin" not in stages and "commit" not in stages
    assert ("abort", "recovery:validation_reject") in transport.calls
    _assert_atomic_failure(conn, "validation_reject")


def test_stale_fence_cannot_renew_or_commit(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)

    def reject_stale_renewal(_args):
        raise policy_delivery.asyncpg.SerializationError("obsolete delivery lease")

    conn = _conn()
    conn.responders.insert(0, ("fn_runtime_v1_renew_delivery_lease", reject_stale_renewal))

    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _payload in transport.calls]
    assert "validate" in stages
    assert "commit" not in stages
    assert "abort" not in stages
    assert conn.sql_calls("fn_runtime_v1_record_device_snapshot") == []
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []


def test_short_renewal_horizon_refuses_commit(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn()
    conn.responders = [
        (
            fragment,
            datetime.now(UTC) + timedelta(seconds=164) if fragment == "fn_runtime_v1_renew_delivery_lease" else result,
        )
        for fragment, result in conn.responders
    ]

    _run(policy_delivery_worker(FakePool(conn)))

    stages = [name for name, _payload in transport.calls]
    assert "commit" not in stages
    assert "abort" not in stages


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

    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    # The stale open exposure closed with a bounded reason.
    closes = conn.sql_calls("fn_runtime_v1_close_delivery_exposure")
    assert len(closes) == 1 and closes[0][2][4] == "protocol_deviation"
    # The retry release is atomic with a final conservative coverage sweep.
    _assert_atomic_failure(conn, "hash_mismatch")
    exposure_reads = conn.sql_calls("FROM policy_exposures")
    assert exposure_reads and exposure_reads[0][2] == (DEVICE_ID, EXPERIMENT_ID)
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
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    _assert_atomic_failure(conn, "generation_conflict")


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
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    _assert_atomic_failure(conn, "schema_mismatch")


def test_staged_echo_never_opens_exposure(monkeypatch):
    identity = _exact_identity()
    staged_identity = PolicyDeviceIdentity(
        schema_revision=identity.schema_revision,
        device_generation=identity.device_generation,
        assignment_id=identity.assignment_id,
        activation_sha256=identity.activation_sha256,
        apply_state="staged",
    )
    transport = FakePolicyTransport(identity=staged_identity)
    _enable(monkeypatch, transport)
    conn = _conn()

    _run(policy_delivery_worker(FakePool(conn)))

    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    _assert_atomic_failure(conn, "validation_reject")
    assert ("abort", "echo:validation_reject") in transport.calls


def test_snapshot_records_no_content_hash_under_contract_v2(monkeypatch):
    """The device echoes no separate content hash (bound inside activation,
    §8.9): the runtime snapshot wrapper must receive NULL content."""
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    snapshots = conn.sql_calls("fn_runtime_v1_record_device_snapshot")
    assert len(snapshots) == 1
    args = snapshots[0][2]
    assert args[:3] == (OUTBOX_ID, LEASE_OWNER, 1)
    assert args[3] == str(WIRE_SCHEMA_VERSION)  # schema_revision echo
    assert args[6] is None  # p_content_sha256
    assert args[7] == ACTIVATION_SHA  # p_activation_sha256 (full hash)


def test_snapshot_protocol_rejection_aborts_and_requeues(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)

    def reject_snapshot(_args):
        raise policy_delivery.asyncpg.InsufficientPrivilegeError("protocol lineage rejected")

    conn = _conn()
    conn.responders = [
        (fragment, reject_snapshot if fragment == "fn_runtime_v1_record_device_snapshot" else result)
        for fragment, result in conn.responders
    ]
    _run(policy_delivery_worker(FakePool(conn)))

    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    _assert_atomic_failure(conn, "internal")
    assert ("abort", "snapshot:internal") in transport.calls


def test_stale_fence_snapshot_rejection_never_aborts_or_requeues(monkeypatch):
    transport = FakePolicyTransport(identity=_exact_identity())
    _enable(monkeypatch, transport)

    def reject_stale_snapshot(_args):
        raise policy_delivery.asyncpg.SerializationError("obsolete delivery lease")

    conn = _conn()
    conn.responders = [
        (fragment, reject_stale_snapshot if fragment == "fn_runtime_v1_record_device_snapshot" else result)
        for fragment, result in conn.responders
    ]

    _run(policy_delivery_worker(FakePool(conn)))

    assert "commit" in [name for name, _payload in transport.calls]
    assert "abort" not in [name for name, _payload in transport.calls]
    assert conn.sql_calls("fn_runtime_v1_close_delivery_exposure") == []
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    assert not any(call[2][4] == "failed" for call in conn.sql_calls("fn_runtime_v1_set_outbox_state"))


def test_readback_timeout_requeues_with_timeout_class(monkeypatch):
    transport = FakePolicyTransport(identity=None)  # device never echoes
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))
    assert conn.sql_calls("fn_runtime_v1_finalize_delivery") == []
    assert conn.sql_calls("fn_runtime_v1_record_device_snapshot") == []
    _assert_atomic_failure(conn, "timeout")


def test_stage_failure_records_attempt_aborts_and_requeues(monkeypatch):
    transport = FakePolicyTransport(fail_stage="validate", fail_error_class=ERROR_CLASS_CONNECTION)
    _enable(monkeypatch, transport)
    conn = _conn()
    _run(policy_delivery_worker(FakePool(conn)))

    attempts = conn.sql_calls("fn_runtime_v1_record_delivery_attempt")
    failed_stages = [(a[2][3], a[2][4], a[2][5]) for a in attempts if a[2][4] is False]
    assert failed_stages == [("validate", False, "connection")]
    assert any(name == "abort" for name, _ in transport.calls)
    assert "commit" not in [name for name, _ in transport.calls]
    failed = [c for c in conn.sql_calls("fn_runtime_v1_set_outbox_state") if c[2][4] == "failed"]
    assert len(failed) == 1 and failed[0][2][5] == "connection"


def test_commit_failure_closes_coverage_before_abort_then_atomically_requeues(monkeypatch):
    transport = FakePolicyTransport(fail_stage="commit", fail_error_class=ERROR_CLASS_CONNECTION)
    _enable(monkeypatch, transport)
    open_exposure = {"exposure_id": str(uuid.uuid4())}
    conn = _conn(extra=[])
    conn.responders = [
        ("FROM policy_exposures", [open_exposure]) if frag == "FROM policy_exposures" else (frag, res)
        for frag, res in conn.responders
    ]

    _run(policy_delivery_worker(FakePool(conn)))

    closes = conn.sql_calls("fn_runtime_v1_close_delivery_exposure")
    assert len(closes) == 1 and closes[0][2][4] == "device_lost"
    _assert_atomic_failure(conn, "connection")
    assert ("abort", "commit:connection") in transport.calls
    ordered_sql = [sql for _kind, sql, _args in conn.calls]
    assert ordered_sql.index(closes[0][1]) < ordered_sql.index(conn.sql_calls("fn_runtime_v1_fail_delivery")[0][1])


def test_exhausted_attempts_abandon(monkeypatch):
    transport = FakePolicyTransport(identity=None)
    _enable(monkeypatch, transport)
    lease = _lease_row() | {"attempt_count": 10}
    conn = FakeConn(
        [
            ("fn_runtime_v1_lease_delivery", lease),
            ("FROM effective_policy_vectors v", _vector_row()),
            (
                "fn_runtime_v1_renew_delivery_lease",
                datetime.now(UTC) + timedelta(seconds=180),
            ),
            ("FROM policy_exposures", []),
        ]
    )
    _run(policy_delivery_worker(FakePool(conn)))
    abandoned = conn.sql_calls("fn_runtime_v1_abandon_delivery")
    assert len(abandoned) == 1
    assert abandoned[0][2] == (OUTBOX_ID, LEASE_OWNER, 10, "timeout")
    assert all(call[2][5] != "aborted" for call in conn.sql_calls("fn_runtime_v1_set_vector_state"))
