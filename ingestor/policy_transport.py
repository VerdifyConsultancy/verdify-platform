"""Device transport for whole-vector policy delivery (#584 Lane C).

Defines the ``PolicyTransport`` protocol the delivery worker drives, the
real ESP32 implementation SKELETON that maps onto the native API services
Lane E registers (service ids + readback sensor names are pinned in
``verdify_schemas.policy_transport`` so both lanes implement the same
contract), and a ``FakePolicyTransport`` for tests.

The ESP32 transport buffers begin/chunk/validate/commit into ONE call
sequence and flushes it through ``esp32_push.push_policy_transaction`` at
``commit()`` — the whole staged transaction reaches the device atomically
w.r.t. the singleton writer (ordinary per-parameter pushes queue behind it,
bounded, never interleave). ``read_identity()`` parses the device-echoed
aggregated ``policy_identity`` text sensor (contract v2, #586: ONE sensor,
FULL 64-hex activation hash, no content prefix — content identity is bound
inside the activation hash per audit §8.9) from ``shared.policy_readback``
(populated by the esp32 ingest loop).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import shared
from esp32_push import (
    PolicyServiceCall,
    PolicyTransactionResult,
    push_policy_transaction,
)

from verdify_schemas.policy_transport import (
    POLICY_IDENTITY_SENSOR,
    POLICY_SERVICE_ABORT,
    POLICY_SERVICE_BEGIN,
    POLICY_SERVICE_CHUNK,
    POLICY_SERVICE_COMMIT,
    POLICY_SERVICE_VALIDATE,
    POLICY_TRANSPORT_SERVICES,
    parse_policy_identity,
    policy_abort_payload,
    policy_begin_payload,
    policy_commit_payload,
)

# Bounded outbox error classes (migration 207 policy_delivery_outbox CHECK).
ERROR_CLASS_TIMEOUT = "timeout"
ERROR_CLASS_CONNECTION = "connection"
ERROR_CLASS_DEVICE_BUSY = "device_busy"
ERROR_CLASS_HASH_MISMATCH = "hash_mismatch"
ERROR_CLASS_SCHEMA_MISMATCH = "schema_mismatch"
ERROR_CLASS_GENERATION_CONFLICT = "generation_conflict"
ERROR_CLASS_VALIDATION_REJECT = "validation_reject"
ERROR_CLASS_INTERNAL = "internal"


class PolicyTransportError(RuntimeError):
    """One staged-transaction stage failed, with a bounded error class."""

    def __init__(self, stage: str, error_class: str, detail: str = "") -> None:
        super().__init__(f"policy transport {stage} failed [{error_class}] {detail}".strip())
        self.stage = stage
        self.error_class = error_class
        self.detail = detail


@dataclass(frozen=True)
class PolicyDeliveryRequest:
    """Everything one whole-vector staged delivery needs to reach the device."""

    device_id: str
    vector_id: str
    assignment_id: str
    device_generation: int
    canonical_bytes: bytes
    content_sha256: str
    activation_sha256: str


@dataclass(frozen=True)
class PolicyDeviceIdentity:
    """Device-echoed policy identity (the exact-echo exposure precondition).

    Contract v2 (#586): parsed from the ONE aggregated ``policy_identity``
    sensor. There is deliberately NO content hash field — content identity is
    bound inside ``activation_sha256`` (audit §8.9), so the activation echo
    transitively confirms content.
    """

    schema_revision: int | None
    device_generation: int | None
    assignment_id: str | None
    activation_sha256: str | None
    apply_state: str | None


class PolicyTransport(Protocol):
    """Staged whole-vector transaction surface the delivery worker drives."""

    def available(self) -> bool: ...

    async def begin(self, request: PolicyDeliveryRequest) -> None: ...

    async def stage_chunk(self, seq: int, data_hex: str) -> None: ...

    async def validate(self) -> None: ...

    async def commit(self) -> None: ...

    async def abort(self, reason: str) -> None: ...

    def read_identity(self) -> PolicyDeviceIdentity | None: ...


def _reason_to_error_class(reason: str) -> str:
    if reason.startswith("command_timeout") or reason == "transaction_budget_exceeded":
        return ERROR_CLASS_TIMEOUT
    if reason in (
        "transport_disconnected",
        "transport_generation_changed",
        "transport_client_changed",
    ) or reason.startswith("command_error"):
        return ERROR_CLASS_CONNECTION
    if reason in ("policy_service_unavailable", "transaction_call_limit_exceeded"):
        return ERROR_CLASS_VALIDATION_REJECT
    return ERROR_CLASS_INTERNAL


class Esp32PolicyTransport:
    """Real device transport SKELETON over the Lane E native API services.

    Buffered staging: ``begin``/``stage_chunk``/``validate``/``commit`` queue
    the exact service calls; ``commit()`` flushes the whole ordered sequence
    through one non-interleavable ``push_policy_transaction``. Until Lane E
    ships the firmware services, ``available()`` is False and the delivery
    worker no-ops gracefully.
    """

    def __init__(self) -> None:
        self._calls: list[PolicyServiceCall] = []
        self._request: PolicyDeliveryRequest | None = None

    def available(self) -> bool:
        services = shared.esp32.get("services") or {}
        return all(name in services for name in POLICY_TRANSPORT_SERVICES)

    async def begin(self, request: PolicyDeliveryRequest) -> None:
        if self._request is not None:
            raise PolicyTransportError("begin", ERROR_CLASS_DEVICE_BUSY, "transaction already staged")
        self._request = request
        self._calls = [
            PolicyServiceCall(
                POLICY_SERVICE_BEGIN,
                policy_begin_payload(
                    generation=request.device_generation,
                    vector_bytes=request.canonical_bytes,
                    content_sha256_hex=request.content_sha256,
                    activation_sha256_hex=request.activation_sha256,
                    assignment_id=request.assignment_id,
                ),
            )
        ]

    async def stage_chunk(self, seq: int, data_hex: str) -> None:
        self._require_open("chunk")
        self._calls.append(PolicyServiceCall(POLICY_SERVICE_CHUNK, {"seq": int(seq), "data_hex": data_hex}))

    async def validate(self) -> None:
        self._require_open("validate")
        self._calls.append(PolicyServiceCall(POLICY_SERVICE_VALIDATE, {}))

    async def commit(self) -> None:
        request = self._require_open("commit")
        self._calls.append(
            PolicyServiceCall(POLICY_SERVICE_COMMIT, policy_commit_payload(generation=request.device_generation))
        )
        calls, self._calls, self._request = self._calls, [], None
        result: PolicyTransactionResult = await push_policy_transaction(calls)
        if not result.ok:
            failure = result.failure
            reason = failure.reason if failure else "unknown"
            stage = calls[failure.index].service if failure else "commit"
            raise PolicyTransportError(stage, _reason_to_error_class(reason), reason)

    async def abort(self, reason: str) -> None:
        self._calls = []
        self._request = None
        result = await push_policy_transaction(
            [PolicyServiceCall(POLICY_SERVICE_ABORT, policy_abort_payload(reason=reason))]
        )
        if not result.ok:
            failure = result.failure
            raise PolicyTransportError(
                "abort",
                _reason_to_error_class(failure.reason if failure else "unknown"),
                failure.reason if failure else "unknown",
            )

    def read_identity(self) -> PolicyDeviceIdentity | None:
        payload = shared.policy_readback.get(POLICY_IDENTITY_SENSOR)
        if not payload:
            return None
        try:
            echo = parse_policy_identity(payload)
        except ValueError:
            # Malformed echo is "no echo", never a partially-trusted identity.
            return None
        return PolicyDeviceIdentity(
            schema_revision=echo.schema_revision,
            device_generation=echo.generation,
            assignment_id=echo.assignment_id,
            activation_sha256=echo.activation_sha256,
            apply_state=echo.apply_state,
        )

    def _require_open(self, stage: str) -> PolicyDeliveryRequest:
        if self._request is None:
            raise PolicyTransportError(stage, ERROR_CLASS_INTERNAL, "no open transaction (begin first)")
        return self._request


@dataclass
class FakePolicyTransport:
    """Scriptable in-memory transport for the delivery-worker tests."""

    identity: PolicyDeviceIdentity | None = None
    fail_stage: str | None = None
    fail_error_class: str = ERROR_CLASS_CONNECTION
    is_available: bool = True
    calls: list[tuple[str, object]] = field(default_factory=list)

    def available(self) -> bool:
        return self.is_available

    async def begin(self, request: PolicyDeliveryRequest) -> None:
        self.calls.append(("begin", request))
        self._maybe_fail("begin")

    async def stage_chunk(self, seq: int, data_hex: str) -> None:
        self.calls.append(("chunk", (seq, data_hex)))
        self._maybe_fail("chunk")

    async def validate(self) -> None:
        self.calls.append(("validate", None))
        self._maybe_fail("validate")

    async def commit(self) -> None:
        self.calls.append(("commit", None))
        self._maybe_fail("commit")

    async def abort(self, reason: str) -> None:
        self.calls.append(("abort", reason))

    def read_identity(self) -> PolicyDeviceIdentity | None:
        self.calls.append(("read_identity", None))
        return self.identity

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_stage == stage:
            raise PolicyTransportError(stage, self.fail_error_class, "scripted failure")
