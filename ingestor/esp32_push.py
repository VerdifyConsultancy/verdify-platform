"""Truthful, bounded delivery through the sole ESPHome writer.

All device writes still pass through this module.  The public
``push_to_esp32`` wrapper preserves the historical integer return value, while
``push_to_esp32_detailed`` exposes per-command queue and physical-delivery
outcomes to the dispatcher.  A single bounded round-robin worker owns the API
socket so a long anchor batch cannot monopolize later occupancy or operator
requests.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import shared
from entity_map import SETPOINT_MAP

log = logging.getLogger("esp32_push")

# firmware-v2 anchors-mode: the single on-chip API service that writes any
# NVS-persisted band/zone anchor global by name.
_BAND_ANCHOR_SERVICE = "set_band_anchor"

# ── Device-write safety gate (#79) ──────────────────────────────────────────
_DEVICE_WRITE_DISABLED_LOGGED = False


def _device_writes_enabled() -> bool:
    """True only when VERDIFY_DEVICE_WRITE_ENABLED == '1' (default-deny)."""
    return os.environ.get("VERDIFY_DEVICE_WRITE_ENABLED", "") == "1"


def _log_device_writes_disabled_once(where: str) -> None:
    global _DEVICE_WRITE_DISABLED_LOGGED
    if not _DEVICE_WRITE_DISABLED_LOGGED:
        _DEVICE_WRITE_DISABLED_LOGGED = True
        log.warning(
            "Device writes DISABLED (VERDIFY_DEVICE_WRITE_ENABLED != '1'); "
            "%s and all subsequent ESP32 writes are no-ops",
            where,
        )


# Keep the established heap-safe pace, but make the pause request-local.  The
# worker round-robins after every quantum, so another request can run while a
# large batch observes its six-second cooling pause.  In particular, the old
# ``await asyncio.sleep(_BATCH_PAUSE_S)`` monopoly is represented by a per-
# request ``not_before`` deadline so another queued request can overtake it.
_BATCH_PAUSE_EVERY = 2
_BATCH_PAUSE_S = 6.0
_MIN_COMMAND_INTERVAL_S = 2.0
_MAX_PENDING_REQUESTS = 32
_MAX_BATCH_COMMANDS = 128
_FAIR_QUANTUM = 2
_COMMAND_TIMEOUT_S = 15.0
_LIFECYCLE_CALLBACK_ATTEMPTS = 3
_LIFECYCLE_CALLBACK_TIMEOUT_S = 5.0
_LIFECYCLE_RETRY_S = 0.25
_LAST_COMMAND_TS = 0.0
_PUSH_LOCK = asyncio.Lock()

DeliveryState = Literal["queued", "sent", "failed", "cancelled", "superseded"]


class LifecyclePersistenceError(RuntimeError):
    """The bounded durable-state callback could not record a milestone."""


@dataclass(frozen=True)
class DeviceCommandOutcome:
    """One command's durable-lifecycle input, emitted after each milestone."""

    index: int
    object_id: str
    value: float
    entity_type: str
    parameter: str
    status: DeliveryState
    reason: str
    attempt: int
    connection_generation: int
    logical_version: float = 0.0


@dataclass(frozen=True)
class PushBatchResult:
    """Terminal per-command outcomes for one caller batch."""

    outcomes: tuple[DeviceCommandOutcome, ...]
    fatal_error: str | None = None

    @property
    def sent_count(self) -> int:
        return sum(outcome.status == "sent" for outcome in self.outcomes)

    @property
    def failed_count(self) -> int:
        return sum(outcome.status == "failed" for outcome in self.outcomes)

    @property
    def cancelled_count(self) -> int:
        return sum(outcome.status == "cancelled" for outcome in self.outcomes)


StateCallback = Callable[[tuple[DeviceCommandOutcome, ...]], Awaitable[None]]


@dataclass
class _WriteRequest:
    changes: tuple[tuple[str, float, str], ...]
    future: asyncio.Future[PushBatchResult]
    ready: asyncio.Event
    on_state: StateCallback | None
    attempt: int
    accepted_generation: int
    accepted_client: object | None
    command_versions: tuple[float, ...]
    next_index: int = 0
    outcomes: list[DeviceCommandOutcome] = field(default_factory=list)
    cancel_requested: bool = False
    not_before: float = 0.0
    superseded_indices: set[int] = field(default_factory=set)
    inflight_index: int | None = None


_REQUESTS: deque[_WriteRequest] = deque()
_QUEUE_LOOP: asyncio.AbstractEventLoop | None = None
_QUEUE_EVENT: asyncio.Event | None = None
_WORKER_TASK: asyncio.Task[None] | None = None
_PENDING_REQUESTS = 0
_ACTIVE_REQUEST: _WriteRequest | None = None
_LIFECYCLE_BLOCKED = False
_LOGICAL_SEQUENCE = 0
_LATEST_LOGICAL_VERSION: dict[str, float] = {}
_LOGICAL_TOKEN_VERSION: dict[tuple[str, float], float] = {}

_NON_RETRYABLE_DELIVERY_REASONS = frozenset(
    {
        "anchor_service_unavailable",
        "batch_limit_exceeded",
        "command_version_count_mismatch",
        "device_writes_disabled",
        "entity_key_unavailable",
        "lifecycle_persistence_unavailable",
        "shadow_mode",
        "transport_client_changed",
        "transport_generation_changed",
        "unsupported_entity_type",
        "writer_lease_not_held",
    }
)


def delivery_failure_retryable(outcome: DeviceCommandOutcome) -> bool:
    """Retry only failures known not to have an ambiguous physical outcome."""
    return (
        outcome.status == "failed"
        and outcome.reason not in _NON_RETRYABLE_DELIVERY_REASONS
        and not outcome.reason.endswith("_outcome_unknown")
    )


_DELIVERY_TRANSITION_PRIORS: dict[str, tuple[str, ...]] = {
    "queued": ("pending", "requested", "retrying", "queued"),
    "retrying": ("pending", "requested", "queued", "retrying"),
    "sent": ("pending", "requested", "queued", "retrying", "sent"),
    "failed": ("pending", "requested", "queued", "retrying", "failed"),
    "cancelled": ("pending", "requested", "queued", "retrying", "cancelled"),
    "superseded": ("pending", "requested", "queued", "retrying", "superseded"),
}


def delivery_transition_prior_statuses(next_status: str) -> tuple[str, ...]:
    """Return the only durable states that may transition to ``next_status``."""
    return _DELIVERY_TRANSITION_PRIORS[next_status]


def preserved_terminal_status(current_status: str | None, attempted_status: str) -> str | None:
    """Preserve logical supersession when an in-flight outcome lands afterward."""
    if current_status == "superseded" and attempted_status in {"sent", "failed", "cancelled", "superseded"}:
        return "superseded"
    return None


def _parameter_for(obj_id: str, entity_type: str) -> str:
    if entity_type == "service":
        return obj_id
    return SETPOINT_MAP.get(obj_id, obj_id)


def _outcome(
    request: _WriteRequest,
    index: int,
    status: DeliveryState,
    reason: str,
) -> DeviceCommandOutcome:
    obj_id, value, entity_type = request.changes[index]
    return DeviceCommandOutcome(
        index=index,
        object_id=obj_id,
        value=float(value),
        entity_type=entity_type,
        parameter=_parameter_for(obj_id, entity_type),
        status=status,
        reason=reason,
        attempt=request.attempt,
        connection_generation=request.accepted_generation,
        logical_version=request.command_versions[index],
    )


async def _emit_state(request: _WriteRequest, outcomes: Sequence[DeviceCommandOutcome]) -> bool:
    """Try to record one lifecycle milestone and report whether it stuck.

    The device command and the Postgres lifecycle row cannot share a transaction.
    We therefore retry transient callback failures with both count and time
    bounds, then fail-close and request a supervised process restart. This closes
    the much larger failure mode where a DB blip let an entire batch continue
    while every physical ``sent`` milestone was silently discarded.
    """
    if not outcomes or request.on_state is None:
        return True
    for callback_attempt in range(1, _LIFECYCLE_CALLBACK_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(
                request.on_state(tuple(outcomes)),
                timeout=_LIFECYCLE_CALLBACK_TIMEOUT_S,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # lifecycle evidence must not create a second writer path
            log.error(
                "writer_delivery lifecycle_callback_failed count=%d callback_attempt=%d/%d error=%s",
                len(outcomes),
                callback_attempt,
                _LIFECYCLE_CALLBACK_ATTEMPTS,
                type(exc).__name__,
            )
            if callback_attempt < _LIFECYCLE_CALLBACK_ATTEMPTS:
                await asyncio.sleep(_LIFECYCLE_RETRY_S)
    return False


async def _emit_state_until_recorded(
    request: _WriteRequest,
    outcomes: Sequence[DeviceCommandOutcome],
) -> None:
    """Fail closed after bounded retries if the milestone cannot be recorded.

    For queued state this prevents an unrecorded command from starting.  For a
    terminal state it prevents the sole worker from advancing after an API return
    whose durable ``sent``/``failed`` evidence has not yet been stored.
    """
    if await _emit_state(request, outcomes):
        return
    raise LifecyclePersistenceError(f"lifecycle callback failed after {_LIFECYCLE_CALLBACK_ATTEMPTS} attempts")


def _reset_queue_for_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Bind queue primitives to one event loop (also keeps asyncio.run tests safe)."""
    global _QUEUE_LOOP, _QUEUE_EVENT, _WORKER_TASK, _PENDING_REQUESTS, _ACTIVE_REQUEST
    global _REQUESTS, _PUSH_LOCK, _LAST_COMMAND_TS, _LIFECYCLE_BLOCKED
    global _LOGICAL_SEQUENCE, _LATEST_LOGICAL_VERSION, _LOGICAL_TOKEN_VERSION
    if _QUEUE_LOOP is loop:
        return
    _QUEUE_LOOP = loop
    _QUEUE_EVENT = asyncio.Event()
    _WORKER_TASK = None
    _PENDING_REQUESTS = 0
    _ACTIVE_REQUEST = None
    _REQUESTS = deque()
    _PUSH_LOCK = asyncio.Lock()
    _LAST_COMMAND_TS = 0.0
    _LIFECYCLE_BLOCKED = False
    _LOGICAL_SEQUENCE = 0
    _LATEST_LOGICAL_VERSION = {}
    _LOGICAL_TOKEN_VERSION = {}


async def _pace_command() -> None:
    global _LAST_COMMAND_TS
    now = time.monotonic()
    wait_s = _MIN_COMMAND_INTERVAL_S - (now - _LAST_COMMAND_TS)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    _LAST_COMMAND_TS = time.monotonic()


def _preflight_failure() -> str | None:
    if _LIFECYCLE_BLOCKED:
        return "lifecycle_persistence_unavailable"
    if shared.is_shadow_mode():
        return "shadow_mode"
    if not _device_writes_enabled():
        _log_device_writes_disabled_once("push_to_esp32")
        return "device_writes_disabled"
    if not shared.writer_lease_held():
        return "writer_lease_not_held"
    if shared.esp32.get("client") is None:
        return "transport_disconnected"
    return None


def _request_fence_failure(request: _WriteRequest) -> str | None:
    """Reject work accepted for a stale transport/client before physical send."""
    if failure := _preflight_failure():
        return failure
    if request.accepted_generation != int(shared.transport_generation):
        return "transport_generation_changed"
    if request.accepted_client is not shared.esp32.get("client"):
        return "transport_client_changed"
    return None


async def _execute_one(request: _WriteRequest, index: int) -> DeviceCommandOutcome:
    """Execute exactly one command and report success only after API return."""
    failure = _request_fence_failure(request)
    if failure is not None:
        return _outcome(request, index, "failed", failure)

    obj_id, value, entity_type = request.changes[index]
    client = shared.esp32["client"]
    keys = shared.esp32["keys"]
    try:
        # The worker is already singular, but retaining the lock makes the
        # physical chokepoint explicit and protects direct test/reload edges.
        async with _PUSH_LOCK:
            await _pace_command()
            # Fence again inside the physical chokepoint.  A lease loss or
            # reconnect during lock/pacing wait must never send through the
            # client captured before that wait.
            if failure := _request_fence_failure(request):
                return _outcome(request, index, "failed", failure)
            if entity_type == "service":
                service = (shared.esp32.get("services") or {}).get(_BAND_ANCHOR_SERVICE)
                if service is None:
                    return _outcome(request, index, "failed", "anchor_service_unavailable")
                result = client.execute_service(service, {"anchor_key": obj_id, "value": float(value)})
            else:
                key = keys.get(obj_id)
                if not key:
                    return _outcome(request, index, "failed", "entity_key_unavailable")
                if entity_type == "number":
                    result = client.number_command(key, value)
                elif entity_type == "switch":
                    result = client.switch_command(key, value > 0.5)
                else:
                    return _outcome(request, index, "failed", "unsupported_entity_type")
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=_COMMAND_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        # The transport did not acknowledge completion.  The command may have
        # reached the device before the timeout, so call this a terminal unknown
        # failure and never let a generic retry duplicate it.
        log.error(
            "writer_delivery phase=transport status=failed reason=command_timeout_outcome_unknown "
            "param=%s generation=%d attempt=%d",
            _parameter_for(obj_id, entity_type),
            shared.transport_generation,
            request.attempt,
        )
        return _outcome(request, index, "failed", "command_timeout_outcome_unknown")
    except Exception as exc:
        log.warning(
            "writer_delivery phase=transport status=failed reason=command_error "
            "param=%s generation=%d attempt=%d error=%s",
            _parameter_for(obj_id, entity_type),
            shared.transport_generation,
            request.attempt,
            type(exc).__name__,
        )
        return _outcome(request, index, "failed", f"command_error:{type(exc).__name__}")

    parameter = _parameter_for(obj_id, entity_type)
    shared.recently_pushed[parameter] = time.time()
    shared.recently_pushed_values[parameter] = float(value)
    outcome = _outcome(request, index, "sent", "api_command_returned")
    log.info(
        "writer_delivery phase=transport status=sent reason=api_command_returned param=%s generation=%d attempt=%d",
        parameter,
        outcome.connection_generation,
        request.attempt,
    )
    return outcome


def _pop_ready_request(now: float) -> tuple[_WriteRequest | None, float | None]:
    """Round-robin to a ready request; return the shortest cooling delay otherwise."""
    if not _REQUESTS:
        return None, None
    shortest_delay: float | None = None
    for _ in range(len(_REQUESTS)):
        request = _REQUESTS.popleft()
        if request.cancel_requested or request.not_before <= now:
            return request, 0.0
        shortest_delay = min(shortest_delay or float("inf"), request.not_before - now)
        _REQUESTS.append(request)
    return None, shortest_delay


def _abort_all_requests(reason: str) -> None:
    """Resolve every in-memory caller without issuing another physical write."""
    global _PENDING_REQUESTS, _ACTIVE_REQUEST, _LIFECYCLE_BLOCKED
    _LIFECYCLE_BLOCKED = True
    requests = list(_REQUESTS)
    _REQUESTS.clear()
    if _ACTIVE_REQUEST is not None and all(_ACTIVE_REQUEST is not request for request in requests):
        requests.insert(0, _ACTIVE_REQUEST)
    for request in requests:
        recorded_indices = {outcome.index for outcome in request.outcomes}
        request.outcomes.extend(
            _outcome(request, index, "failed", reason)
            for index in range(len(request.changes))
            if index not in recorded_indices
        )
        result = PushBatchResult(
            tuple(sorted(request.outcomes, key=lambda item: item.index)),
            fatal_error=reason,
        )
        if not request.future.done():
            request.future.set_result(result)
    _PENDING_REQUESTS = 0
    _ACTIVE_REQUEST = None
    shared.note_writer_fatal(reason)
    log.critical(
        "writer_delivery lifecycle_persistence_blocked reason=%s generation=%d action=writer_fail_closed",
        reason,
        shared.transport_generation,
    )


async def _finish_request(request: _WriteRequest, outcomes: Sequence[DeviceCommandOutcome]) -> bool:
    global _PENDING_REQUESTS
    if outcomes:
        request.outcomes.extend(outcomes)
        try:
            await _emit_state_until_recorded(request, outcomes)
        except LifecyclePersistenceError:
            _abort_all_requests("lifecycle_persistence_unavailable")
            return False
    result = PushBatchResult(tuple(sorted(request.outcomes, key=lambda item: item.index)))
    if not request.future.done():
        request.future.set_result(result)
    _PENDING_REQUESTS -= 1
    return True


async def _writer_worker() -> None:
    """Sole physical writer worker with request-level round-robin fairness."""
    global _ACTIVE_REQUEST
    assert _QUEUE_EVENT is not None
    while True:
        if not _REQUESTS:
            _QUEUE_EVENT.clear()
            await _QUEUE_EVENT.wait()

        request, delay = _pop_ready_request(time.monotonic())
        if request is None:
            _QUEUE_EVENT.clear()
            try:
                await asyncio.wait_for(_QUEUE_EVENT.wait(), timeout=max(0.001, delay or 0.001))
            except TimeoutError:
                pass
            continue

        _ACTIVE_REQUEST = request
        await request.ready.wait()
        if request.cancel_requested:
            cancelled = [
                _outcome(request, index, "cancelled", "caller_cancelled_before_send")
                for index in range(request.next_index, len(request.changes))
            ]
            request.next_index = len(request.changes)
            if not await _finish_request(request, cancelled):
                return
            _ACTIVE_REQUEST = None
            continue

        quantum_outcomes: list[DeviceCommandOutcome] = []
        stop = min(len(request.changes), request.next_index + _FAIR_QUANTUM)
        while request.next_index < stop:
            index = request.next_index
            if index in request.superseded_indices:
                outcome = _outcome(request, index, "superseded", "superseded_by_newer_request")
            else:
                request.inflight_index = index
                outcome = await _execute_one(request, index)
                request.inflight_index = None
            quantum_outcomes.append(outcome)
            request.next_index += 1
            if request.cancel_requested:
                break

        if quantum_outcomes:
            request.outcomes.extend(quantum_outcomes)
            try:
                await _emit_state_until_recorded(request, quantum_outcomes)
            except LifecyclePersistenceError:
                _abort_all_requests("lifecycle_persistence_unavailable")
                return

        if request.next_index >= len(request.changes):
            if not await _finish_request(request, ()):
                return
            _ACTIVE_REQUEST = None
            continue

        if request.cancel_requested:
            cancelled = [
                _outcome(request, index, "cancelled", "caller_cancelled_before_send")
                for index in range(request.next_index, len(request.changes))
            ]
            request.next_index = len(request.changes)
            if not await _finish_request(request, cancelled):
                return
            _ACTIVE_REQUEST = None
            continue

        # Yield the writer after every two commands.  The cooling deadline is
        # attached to this request, so newly queued work can run immediately.
        request.not_before = time.monotonic() + _BATCH_PAUSE_S
        _REQUESTS.append(request)
        _ACTIVE_REQUEST = None
        _QUEUE_EVENT.set()


def _ensure_worker(loop: asyncio.AbstractEventLoop) -> None:
    global _WORKER_TASK
    if _WORKER_TASK is None or _WORKER_TASK.done():
        _WORKER_TASK = loop.create_task(_writer_worker(), name="verdify-device-writer")


async def _immediate_result(
    changes: tuple[tuple[str, float, str], ...],
    status: Literal["failed", "cancelled"],
    reason: str,
    attempt: int,
    on_state: StateCallback | None,
) -> PushBatchResult:
    loop = asyncio.get_running_loop()
    placeholder = _WriteRequest(
        changes=changes,
        future=loop.create_future(),
        ready=asyncio.Event(),
        on_state=on_state,
        attempt=attempt,
        accepted_generation=int(shared.transport_generation),
        accepted_client=shared.esp32.get("client"),
        command_versions=tuple(0.0 for _ in changes),
    )
    outcomes = tuple(_outcome(placeholder, index, status, reason) for index in range(len(changes)))
    await _emit_state_until_recorded(placeholder, outcomes)
    return PushBatchResult(outcomes)


def _supersede_older_queued_commands(new_request: _WriteRequest) -> int:
    """Use immutable logical versions so an old retry can never win by arrival."""
    marked = 0
    candidates = list(_REQUESTS)
    if _ACTIVE_REQUEST is not None:
        candidates.append(_ACTIVE_REQUEST)
    for new_index, (obj_id, _value, entity_type) in enumerate(new_request.changes):
        parameter = _parameter_for(obj_id, entity_type)
        new_version = new_request.command_versions[new_index]
        latest_version = _LATEST_LOGICAL_VERSION.get(parameter)
        if latest_version is not None and new_version < latest_version:
            new_request.superseded_indices.add(new_index)
            marked += 1
            continue
        _LATEST_LOGICAL_VERSION[parameter] = new_version
        for request in candidates:
            for index in range(request.next_index, len(request.changes)):
                if index == request.inflight_index or index in request.superseded_indices:
                    continue
                old_obj_id, _old_value, old_entity_type = request.changes[index]
                if (
                    _parameter_for(old_obj_id, old_entity_type) == parameter
                    and request.command_versions[index] <= new_version
                ):
                    request.superseded_indices.add(index)
                    marked += 1
    return marked


def _ordered_command_versions(
    batch: tuple[tuple[str, float, str], ...],
    producer_tokens: Sequence[float] | None,
) -> tuple[float, ...] | None:
    """Map every producer token into one comparable local acceptance sequence.

    A DB timestamp is an immutable retry identity, not an ordering domain.  The
    first sight of any token receives the same local sequence used by default
    callers; a retry with that token reuses its original position.
    """
    global _LOGICAL_SEQUENCE
    if producer_tokens is not None and len(producer_tokens) != len(batch):
        return None
    versions: list[float] = []
    for index, (obj_id, _value, entity_type) in enumerate(batch):
        parameter = _parameter_for(obj_id, entity_type)
        if producer_tokens is None:
            _LOGICAL_SEQUENCE += 1
            versions.append(float(_LOGICAL_SEQUENCE))
            continue
        token = float(producer_tokens[index])
        key = (parameter, token)
        version = _LOGICAL_TOKEN_VERSION.get(key)
        if version is None:
            _LOGICAL_SEQUENCE += 1
            version = float(_LOGICAL_SEQUENCE)
            _LOGICAL_TOKEN_VERSION[key] = version
        versions.append(version)
    return tuple(versions)


async def push_to_esp32_detailed(
    changes: list[tuple[str, float, str]],
    *,
    attempt: int = 1,
    on_state: StateCallback | None = None,
    command_versions: Sequence[float] | None = None,
) -> PushBatchResult:
    """Queue a bounded batch and return a truthful terminal result per command.

    Caller cancellation does not cancel an in-flight physical command.  It marks
    the request cancelled; the worker reports the command already returned by
    the API as ``sent`` and every still-unsent command as ``cancelled``.
    """
    global _PENDING_REQUESTS, _LIFECYCLE_BLOCKED
    loop = asyncio.get_running_loop()
    _reset_queue_for_loop(loop)
    batch = tuple((obj_id, float(value), entity_type) for obj_id, value, entity_type in changes)
    if not batch:
        return PushBatchResult(())
    if len(batch) > _MAX_BATCH_COMMANDS:
        return await _immediate_result(batch, "failed", "batch_limit_exceeded", attempt, on_state)
    if failure := _preflight_failure():
        return await _immediate_result(batch, "failed", failure, attempt, on_state)

    versions = _ordered_command_versions(batch, command_versions)
    if versions is None:
        return await _immediate_result(batch, "failed", "command_version_count_mismatch", attempt, on_state)
    if _PENDING_REQUESTS >= _MAX_PENDING_REQUESTS:
        return await _immediate_result(batch, "failed", "queue_full", attempt, on_state)

    request = _WriteRequest(
        changes=batch,
        future=loop.create_future(),
        ready=asyncio.Event(),
        on_state=on_state,
        attempt=attempt,
        accepted_generation=int(shared.transport_generation),
        accepted_client=shared.esp32.get("client"),
        command_versions=versions,
    )
    superseded = _supersede_older_queued_commands(request)
    if superseded:
        log.info(
            "writer_delivery phase=queue status=superseded reason=newer_request count=%d generation=%d",
            superseded,
            shared.transport_generation,
        )
    _PENDING_REQUESTS += 1
    _REQUESTS.append(request)
    assert _QUEUE_EVENT is not None
    _QUEUE_EVENT.set()
    _ensure_worker(loop)

    try:
        queued = tuple(_outcome(request, index, "queued", "bounded_queue_accepted") for index in range(len(batch)))
        # Queue acceptance must be durable before the worker is released.  Keep
        # this inside the cancellation guard: a timeout while the lifecycle store
        # is unavailable must wake the worker to cancel, never strand it forever
        # on ``request.ready``.
        await _emit_state_until_recorded(request, queued)
        request.ready.set()
        return await asyncio.shield(request.future)
    except LifecyclePersistenceError:
        # No physical command was released because queued state did not become
        # durable. Fail the process so startup reconciliation terminalizes the
        # consumed LISTEN/dispatcher row instead of stranding it as requested.
        _LIFECYCLE_BLOCKED = True
        shared.note_writer_fatal("lifecycle_persistence_unavailable")
        request.cancel_requested = True
        request.on_state = None
        request.ready.set()
        _QUEUE_EVENT.set()
        log.critical(
            "writer_delivery lifecycle_persistence_blocked reason=queued_state_unavailable "
            "generation=%d action=process_restart_requested",
            shared.transport_generation,
        )
        raise
    except asyncio.CancelledError:
        request.cancel_requested = True
        request.ready.set()
        _QUEUE_EVENT.set()
        log.info(
            "writer_delivery phase=queue status=cancelled reason=caller_cancelled pending=%d generation=%d",
            len(batch) - request.next_index,
            shared.transport_generation,
        )
        raise


async def push_to_esp32(changes: list[tuple[str, float, str]]) -> int:
    """Compatibility wrapper returning only the count physically sent."""
    result = await push_to_esp32_detailed(changes)
    if result.fatal_error:
        raise LifecyclePersistenceError(result.fatal_error)
    return result.sent_count


async def push_occupancy_to_esp32(occupied: bool, source: str) -> int:
    """Push greenhouse occupancy through the same bounded sole-writer queue."""
    if not _device_writes_enabled():
        _log_device_writes_disabled_once("push_occupancy_to_esp32")
        return 0

    label = "occupied" if occupied else "empty"
    if "greenhouse_occupied" not in shared.esp32["keys"]:
        log.debug("Occupancy ESP32 push skipped via %s: greenhouse_occupied API switch unavailable", source)
        return 0
    try:
        pushed = await push_to_esp32([("greenhouse_occupied", 1.0 if occupied else 0.0, "switch")])
        if pushed:
            log.info("Occupancy: pushed %s to ESP32 via %s", label, source)
        return pushed
    except Exception as exc:
        log.debug("Occupancy ESP32 push skipped via %s: %s", source, exc)
        return 0
