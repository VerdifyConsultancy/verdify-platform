"""tasks.policy_delivery — durable outbox -> staged device commit (#584 Lane C).

Runs every 30s. Feature-off (mode != live or no experiment id) it returns
immediately without touching the database. Device work additionally requires
the single-writer model to hold: the k8s writer lease
(shared.writer_lease_held()) AND the #79 device-write gate — otherwise the
worker leaves the outbox untouched for the pod that does hold the lease.

One leased outbox row per cycle is driven through the whole-vector staged
transaction (begin -> chunks -> validate -> commit) over the PolicyTransport
(ingestor/policy_transport.py). The real ESP32 transport flushes the staged
sequence atomically through esp32_push.push_policy_transaction; until Lane E
registers the firmware services the transport reports unavailable and this
worker no-ops gracefully (logged once, natural 30s backoff).

Evidence chain per attempt: policy_delivery_attempts stage rows ->
fn_runtime_v1_record_device_snapshot readback -> exposure opens via the
protocol-v1 runtime wrapper
ONLY when the device echoes the exact schema/generation/assignment/
activation identity (contract v2, #586: the aggregated policy_identity
sensor carries the FULL activation hash; content identity is bound inside
it per audit §8.9). Mismatch or readback timeout closes any open exposure
with a bounded reason and requeues with capped backoff. A recovered same/newer
active mismatch is different: physical identity evidence and terminal
abandonment are persisted atomically without another device call.
"""

import os
from datetime import UTC, datetime, timedelta

import asyncpg
import shared
from esp32_push import device_writes_enabled
from policy_transport import (
    ERROR_CLASS_GENERATION_CONFLICT,
    ERROR_CLASS_HASH_MISMATCH,
    ERROR_CLASS_INTERNAL,
    ERROR_CLASS_SCHEMA_MISMATCH,
    ERROR_CLASS_TIMEOUT,
    ERROR_CLASS_VALIDATION_REJECT,
    Esp32PolicyTransport,
    PolicyDeliveryRequest,
    PolicyTransportError,
)

from verdify_schemas.experiment_config import (
    POLICY_VECTOR_MODE_LIVE,
    active_experiment_id,
    policy_vector_mode,
)
from verdify_schemas.policy_transport import policy_chunk_payloads
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION

from ._common import asyncio, log

_MAX_ATTEMPTS = 10
_READBACK_TIMEOUT_S = 20.0
_READBACK_POLL_S = 1.0
# One native service call is bounded to 15s. A commit flush may consume the
# full 120s transaction budget and then the final 15s call; reserve another
# 20s for identity readback and 10s for durable snapshot/finalization. The
# migration renews a staged lease to 180s immediately before commit.
_SINGLE_DEVICE_CALL_AUTHORITY_HORIZON = timedelta(seconds=20)
_POLICY_COMMIT_AUTHORITY_HORIZON = timedelta(seconds=165)

_transport_factory = Esp32PolicyTransport
_services_unavailable_logged = False


class _DeliveryFenceLost(RuntimeError):
    """Stop an obsolete worker without making any compensating device call."""


def _raise_if_delivery_fence_lost(exc: asyncpg.PostgresError) -> None:
    # Migration 217 uses serialization_failure exclusively for a lease token
    # that is no longer the current, unexpired owner/attempt tuple. Treat an
    # actual serialization conflict the same way: yielding is always safer
    # than letting an uncertain worker continue to touch the device.
    if getattr(exc, "sqlstate", None) == "40001":
        raise _DeliveryFenceLost from exc


def _assert_local_delivery_authority(
    lease,
    required_horizon: timedelta = _SINGLE_DEVICE_CALL_AUTHORITY_HORIZON,
) -> None:
    """Reject an expired/local-writer lease before the next device mutation."""
    expires_at = lease.get("lease_expires_at")
    if (
        not shared.writer_lease_held()
        or not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at <= datetime.now(UTC) + required_horizon
    ):
        raise _DeliveryFenceLost


_LEASE_SQL = """
SELECT outbox_id::text AS outbox_id,
       device_id,
       vector_id::text AS vector_id,
       attempt_count,
       lease_expires_at
  FROM public.fn_runtime_v1_lease_delivery($1::uuid, $2)
"""


def _stage_name(raw: str) -> str:
    """Normalize a transport stage/service name onto the bounded attempt enum."""
    stage = raw.removeprefix("policy_")
    return stage if stage in ("begin", "chunk", "validate", "commit", "activate", "abort") else "activate"


def _lease_owner() -> str:
    return f"policy_delivery/{os.environ.get('HOSTNAME', 'ingestor')}"


async def _record_attempt(conn, lease, lease_owner: str, stage: str, ok: bool, error_class: str | None) -> None:
    try:
        await conn.execute(
            """
            SELECT public.fn_runtime_v1_record_delivery_attempt(
                $1::uuid, $2, $3, $4, $5, $6
            )
            """,
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            stage,
            ok,
            error_class,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _set_outbox_state(
    conn,
    lease,
    lease_owner: str,
    expected_state: str,
    target_state: str,
    error_class: str | None = None,
) -> None:
    try:
        await conn.execute(
            "SELECT public.fn_runtime_v1_set_outbox_state($1::uuid, $2, $3, $4, $5, $6)",
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            expected_state,
            target_state,
            error_class,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _set_vector_state(
    conn,
    lease,
    lease_owner: str,
    expected_state: str,
    target_state: str,
) -> None:
    try:
        await conn.execute(
            "SELECT public.fn_runtime_v1_set_vector_state($1::uuid, $2, $3, $4::uuid, $5, $6)",
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            lease["vector_id"],
            expected_state,
            target_state,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _renew_delivery_lease(conn, lease, lease_owner: str):
    """Extend a staged exact-token lease across commit/readback/finalization."""
    try:
        expires_at = await conn.fetchval(
            "SELECT public.fn_runtime_v1_renew_delivery_lease($1::uuid, $2, $3)",
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise _DeliveryFenceLost
    renewed = dict(lease)
    renewed["lease_expires_at"] = expires_at
    return renewed


async def _abandon_delivery(conn, lease, lease_owner: str, error_class: str) -> None:
    """Atomically abort the vector and terminalize its exact fenced outbox."""
    try:
        await conn.execute(
            "SELECT public.fn_runtime_v1_abandon_delivery($1::uuid, $2, $3, $4)",
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            error_class,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _abandon_recovered_mismatch(
    conn,
    lease,
    lease_owner: str,
    error_class: str,
    identity,
) -> int:
    """Persist recovered physical truth and terminalize under one DB fence."""
    try:
        return await conn.fetchval(
            """
            SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
                $1::uuid, $2, $3, $4, $5, $6, $7::uuid, $8, $9, $10, $11
            )
            """,
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            error_class,
            str(identity.schema_revision) if identity.schema_revision is not None else None,
            identity.device_generation,
            identity.assignment_id,
            # Contract v2 binds content identity inside activation_sha256.
            None,
            identity.activation_sha256,
            identity.apply_state or "unknown",
            None,  # firmware_revision: Lane E readback
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _fail_delivery(conn, lease, lease_owner: str, error_class: str) -> None:
    """Atomically close uncertain coverage and release an exact fenced retry."""
    try:
        await conn.execute(
            "SELECT public.fn_runtime_v1_fail_delivery($1::uuid, $2, $3, $4)",
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            error_class,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _requeue(
    conn,
    lease,
    lease_owner: str,
    expected_state: str,
    error_class: str,
    *,
    close_uncertain_coverage: bool = False,
) -> None:
    """Failed/abandoned disposition with bounded error class + capped backoff."""
    if int(lease["attempt_count"]) >= _MAX_ATTEMPTS:
        await _abandon_delivery(conn, lease, lease_owner, error_class)
        log.error(
            "policy_delivery: outbox %s abandoned after %d attempts (%s)",
            lease["outbox_id"],
            lease["attempt_count"],
            error_class,
        )
        return
    if close_uncertain_coverage:
        await _fail_delivery(conn, lease, lease_owner, error_class)
    else:
        await _set_outbox_state(conn, lease, lease_owner, expected_state, "failed", error_class)
    log.warning(
        "policy_delivery: outbox %s requeued (attempt %d, %s)",
        lease["outbox_id"],
        lease["attempt_count"],
        error_class,
    )


async def _close_open_exposures(
    conn,
    lease,
    lease_owner: str,
    device_id: str,
    expected_experiment_id: str,
    reason: str,
    snapshot_id: int | None = None,
) -> int:
    rows = await conn.fetch(
        """
        SELECT exposure_id
          FROM policy_exposures
         WHERE device_id = $1
           AND experiment_id = $2::uuid
           AND ended_at IS NULL
        """,
        device_id,
        expected_experiment_id,
    )
    for row in rows:
        try:
            await conn.fetchval(
                """
                SELECT public.fn_runtime_v1_close_delivery_exposure(
                    $1::uuid, $2, $3, $4::uuid, $5, $6, 'policy_delivery'
                )
                """,
                lease["outbox_id"],
                lease_owner,
                int(lease["attempt_count"]),
                row["exposure_id"],
                reason,
                snapshot_id,
            )
        except asyncpg.PostgresError as exc:
            _raise_if_delivery_fence_lost(exc)
            raise
    return len(rows)


async def _await_identity_echo(transport, expected_generation: int):
    """Poll the device identity readback until it reflects the new generation."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _READBACK_TIMEOUT_S
    identity = transport.read_identity()
    while loop.time() < deadline:
        if (
            identity is not None
            and identity.device_generation == expected_generation
            and identity.apply_state == "active"
        ):
            return identity
        await asyncio.sleep(_READBACK_POLL_S)
        identity = transport.read_identity()
    return identity


def _identity_is_exact_active(identity, vector, generation: int) -> bool:
    return bool(
        identity is not None
        and identity.schema_revision == WIRE_SCHEMA_VERSION
        and identity.assignment_id == vector["assignment_id"]
        and identity.device_generation == generation
        and identity.activation_sha256 == vector["activation_sha256"]
        and identity.apply_state == "active"
    )


async def _record_device_snapshot(conn, lease, lease_owner: str, identity) -> int:
    try:
        return await conn.fetchval(
            """
            SELECT public.fn_runtime_v1_record_device_snapshot(
                $1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8, $9, $10
            )
            """,
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            str(identity.schema_revision) if identity.schema_revision is not None else None,
            identity.device_generation,
            identity.assignment_id,
            # Contract v2 (#586): content is bound inside activation_sha256.
            None,
            identity.activation_sha256,
            identity.apply_state or "unknown",
            None,  # firmware_revision: Lane E readback
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _finalize_delivery(conn, lease, lease_owner: str, snapshot_id: int):
    try:
        return await conn.fetchrow(
            """
            SELECT exposure_id::text AS exposure_id, superseded_count
              FROM public.fn_runtime_v1_finalize_delivery(
                  $1::uuid, $2, $3, $4, 'policy_delivery'
              )
            """,
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            snapshot_id,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def _finalize_recovered_delivery(conn, lease, lease_owner: str, snapshot_id: int):
    """Finalize an exact recovered echo while preserving its uncertainty gap."""
    try:
        return await conn.fetchrow(
            """
            SELECT exposure_id::text AS exposure_id, superseded_count
              FROM public.fn_runtime_v1_finalize_recovered_delivery(
                  $1::uuid, $2, $3, $4, 'policy_delivery'
              )
            """,
            lease["outbox_id"],
            lease_owner,
            int(lease["attempt_count"]),
            snapshot_id,
        )
    except asyncpg.PostgresError as exc:
        _raise_if_delivery_fence_lost(exc)
        raise


async def policy_delivery_worker(pool: asyncpg.Pool) -> None:
    """Run one delivery, yielding cleanly if its database fence is obsolete."""
    try:
        await _policy_delivery_worker(pool)
    except _DeliveryFenceLost:
        log.warning(
            "policy_delivery: delivery authority expired or changed; "
            "obsolete worker yielded without another device mutation"
        )


async def _policy_delivery_worker(pool: asyncpg.Pool) -> None:
    """Lease one outbox row and drive it through the staged device commit."""
    global _services_unavailable_logged
    if policy_vector_mode() != POLICY_VECTOR_MODE_LIVE:
        return
    experiment_id = active_experiment_id()
    if not experiment_id:
        return
    # Single-writer model: only the lease-holding pod with device writes
    # enabled may touch the device; everyone else leaves the outbox alone.
    if not shared.writer_lease_held() or not device_writes_enabled():
        log.debug("policy_delivery: writer lease/device-write gate closed; leaving outbox for the writer pod")
        return

    transport = _transport_factory()
    if not transport.available():
        if not _services_unavailable_logged:
            _services_unavailable_logged = True
            log.warning(
                "policy_delivery: device lacks the policy transport services (Lane E not deployed); "
                "worker idle until they appear"
            )
        return
    _services_unavailable_logged = False

    async with pool.acquire() as conn:
        lease_owner = _lease_owner()
        lease = await conn.fetchrow(_LEASE_SQL, experiment_id, lease_owner)
        if lease is None:
            return

        vector = await conn.fetchrow(
            """
            SELECT v.vector_id::text AS vector_id, v.assignment_id::text AS assignment_id,
                   v.experiment_id::text AS experiment_id, v.greenhouse_id,
                   v.device_generation, v.canonical_bytes, v.content_sha256,
                   v.activation_sha256, v.status, upper(v.validity) AS valid_to,
                   a.status AS assignment_status,
                   a.experiment_id::text AS assignment_experiment_id
              FROM effective_policy_vectors v
              JOIN control_assignments a ON a.assignment_id = v.assignment_id
             WHERE v.vector_id = $1::uuid
            """,
            lease["vector_id"],
        )
        lineage_mismatch = vector is not None and (
            vector["experiment_id"] != experiment_id or vector["assignment_experiment_id"] != experiment_id
        )
        if lineage_mismatch:
            await _abandon_delivery(conn, lease, lease_owner, ERROR_CLASS_INTERNAL)
            log.error(
                "policy_delivery: leased vector %s failed active-experiment lineage checks; device untouched",
                lease["vector_id"],
            )
            return
        expired = vector is None or vector["status"] not in ("ready", "delivering")
        stale = vector is not None and (vector["assignment_status"] != "active")
        if expired or stale:
            await _abandon_delivery(conn, lease, lease_owner, ERROR_CLASS_INTERNAL)
            log.warning(
                "policy_delivery: outbox %s abandoned (vector %s no longer deliverable)",
                lease["outbox_id"],
                lease["vector_id"],
            )
            return

        device_id = lease["device_id"]
        generation = int(vector["device_generation"])
        request = PolicyDeliveryRequest(
            device_id=device_id,
            vector_id=vector["vector_id"],
            assignment_id=vector["assignment_id"],
            device_generation=generation,
            canonical_bytes=bytes(vector["canonical_bytes"]),
            content_sha256=vector["content_sha256"],
            activation_sha256=vector["activation_sha256"],
        )

        if vector["status"] == "delivering":
            # The prior worker may have committed the device transaction and
            # died before its durable snapshot/finalizer. Reconcile physical
            # truth before any new policy_begin: an exact active echo can be
            # finalized under this attempt's fresh fence with zero device I/O.
            recovered_identity = await _await_identity_echo(transport, generation)
            if recovered_identity is None:
                await _requeue(
                    conn,
                    lease,
                    lease_owner,
                    "leased",
                    ERROR_CLASS_TIMEOUT,
                    close_uncertain_coverage=True,
                )
                log.warning(
                    "policy_delivery: recovered vector %s has no parseable identity; "
                    "device untouched and outbox requeued",
                    lease["vector_id"],
                )
                return
            if _identity_is_exact_active(recovered_identity, vector, generation):
                await _set_outbox_state(conn, lease, lease_owner, "leased", "staging")
                await _set_outbox_state(conn, lease, lease_owner, "staging", "staged")
                lease = await _renew_delivery_lease(conn, lease, lease_owner)
                await _set_outbox_state(conn, lease, lease_owner, "staged", "activating")
                snapshot_id = await _record_device_snapshot(
                    conn,
                    lease,
                    lease_owner,
                    recovered_identity,
                )
                finalized = await _finalize_recovered_delivery(
                    conn,
                    lease,
                    lease_owner,
                    snapshot_id,
                )
                log.info(
                    "policy_delivery: reconciled committed vector %s on %s "
                    "without a device write (exposure %s, superseded %d)",
                    lease["vector_id"],
                    device_id,
                    finalized["exposure_id"],
                    finalized["superseded_count"],
                )
                return
            if recovered_identity.apply_state == "active":
                observed_generation = recovered_identity.device_generation
                if not isinstance(observed_generation, int) or observed_generation >= generation:
                    if observed_generation != generation:
                        error_class = ERROR_CLASS_GENERATION_CONFLICT
                    elif recovered_identity.schema_revision != WIRE_SCHEMA_VERSION:
                        error_class = ERROR_CLASS_SCHEMA_MISMATCH
                    else:
                        error_class = ERROR_CLASS_HASH_MISMATCH
                    snapshot_id = await _abandon_recovered_mismatch(
                        conn,
                        lease,
                        lease_owner,
                        error_class,
                        recovered_identity,
                    )
                    log.error(
                        "policy_delivery: recovered vector %s conflicts with active device identity; "
                        "device untouched and delivery abandoned (%s, evidence snapshot %s)",
                        lease["vector_id"],
                        error_class,
                        snapshot_id,
                    )
                    return
                # A lower active device generation never accepted this vector;
                # the normal staged transaction may safely continue below.
            else:
                await _set_outbox_state(conn, lease, lease_owner, "leased", "staging")
                await _close_open_exposures(
                    conn,
                    lease,
                    lease_owner,
                    device_id,
                    experiment_id,
                    "device_lost",
                )
                try:
                    _assert_local_delivery_authority(lease)
                    await transport.abort("recovery:validation_reject")
                except PolicyTransportError:
                    log.warning("policy_delivery: recovery abort did not reach the device")
                await _requeue(
                    conn,
                    lease,
                    lease_owner,
                    "staging",
                    ERROR_CLASS_VALIDATION_REJECT,
                    close_uncertain_coverage=True,
                )
                return

        if vector["status"] == "ready":
            await _set_vector_state(conn, lease, lease_owner, "ready", "delivering")
        await _set_outbox_state(conn, lease, lease_owner, "leased", "staging")

        # ── Staged transaction over the transport ────────────────────────────
        try:
            _assert_local_delivery_authority(lease)
            await transport.begin(request)
            await _record_attempt(conn, lease, lease_owner, "begin", True, None)
            for chunk in policy_chunk_payloads(request.canonical_bytes):
                _assert_local_delivery_authority(lease)
                await transport.stage_chunk(int(chunk["seq"]), str(chunk["data_hex"]))
            await _record_attempt(conn, lease, lease_owner, "chunk", True, None)
            _assert_local_delivery_authority(lease)
            await transport.validate()
            await _record_attempt(conn, lease, lease_owner, "validate", True, None)
            await _set_outbox_state(conn, lease, lease_owner, "staging", "staged")
            lease = await _renew_delivery_lease(conn, lease, lease_owner)
            _assert_local_delivery_authority(lease, _POLICY_COMMIT_AUTHORITY_HORIZON)
            await transport.commit()
            await _record_attempt(conn, lease, lease_owner, "commit", True, None)
            await _set_outbox_state(conn, lease, lease_owner, "staged", "activating")
        except PolicyTransportError as exc:
            stage = _stage_name(exc.stage)
            await _record_attempt(conn, lease, lease_owner, stage, False, exc.error_class)
            failed_state = "staged" if stage == "commit" else "staging"
            if stage == "commit":
                # Post-commit device state is unknown: the confirmed exposure
                # interval (if any) can no longer be trusted.
                await _close_open_exposures(conn, lease, lease_owner, device_id, experiment_id, "device_lost")
            try:
                _assert_local_delivery_authority(lease)
                await transport.abort(f"{stage}:{exc.error_class}")
            except PolicyTransportError:
                log.warning("policy_delivery: abort after %s failure did not reach the device", stage)
            await _requeue(
                conn,
                lease,
                lease_owner,
                failed_state,
                exc.error_class,
                close_uncertain_coverage=(stage == "commit"),
            )
            return

        # ── Exact-echo readback -> snapshot -> exposure ──────────────────────
        identity = await _await_identity_echo(transport, generation)
        if identity is None:
            await _record_attempt(conn, lease, lease_owner, "activate", False, ERROR_CLASS_TIMEOUT)
            await _requeue(
                conn,
                lease,
                lease_owner,
                "activating",
                ERROR_CLASS_TIMEOUT,
                close_uncertain_coverage=True,
            )
            return

        try:
            snapshot_id = await _record_device_snapshot(conn, lease, lease_owner, identity)
        except asyncpg.PostgresError as exc:
            _raise_if_delivery_fence_lost(exc)
            await _record_attempt(conn, lease, lease_owner, "activate", False, ERROR_CLASS_INTERNAL)
            await _close_open_exposures(conn, lease, lease_owner, device_id, experiment_id, "protocol_deviation")
            try:
                _assert_local_delivery_authority(lease)
                await transport.abort("snapshot:internal")
            except PolicyTransportError:
                log.warning("policy_delivery: abort after snapshot rejection did not reach the device")
            await _requeue(
                conn,
                lease,
                lease_owner,
                "activating",
                ERROR_CLASS_INTERNAL,
                close_uncertain_coverage=True,
            )
            log.warning(
                "policy_delivery: runtime snapshot rejected after device commit (%s)",
                type(exc).__name__,
            )
            return

        # Exact echo (contract v2): schema/generation/assignment/activation.
        echo_exact = _identity_is_exact_active(identity, vector, generation)
        if not echo_exact:
            if identity.device_generation != generation:
                error_class = ERROR_CLASS_GENERATION_CONFLICT
            elif identity.schema_revision != WIRE_SCHEMA_VERSION:
                error_class = ERROR_CLASS_SCHEMA_MISMATCH
            elif identity.apply_state != "active":
                error_class = ERROR_CLASS_VALIDATION_REJECT
            else:
                error_class = ERROR_CLASS_HASH_MISMATCH
            await _record_attempt(conn, lease, lease_owner, "activate", False, error_class)
            await _close_open_exposures(
                conn,
                lease,
                lease_owner,
                device_id,
                experiment_id,
                "protocol_deviation",
                snapshot_id,
            )
            try:
                _assert_local_delivery_authority(lease)
                await transport.abort(f"echo:{error_class}")
            except PolicyTransportError:
                log.warning("policy_delivery: abort after echo mismatch did not reach the device")
            await _requeue(
                conn,
                lease,
                lease_owner,
                "activating",
                error_class,
                close_uncertain_coverage=True,
            )
            return

        # Under one lease fence, close the prior confirmed interval, open this
        # delivering vector, record activate-success, and CAS the outbox.
        finalized = await _finalize_delivery(conn, lease, lease_owner, snapshot_id)
        log.info(
            "policy_delivery: vector %s active on %s (generation %d, exposure %s, superseded %d)",
            lease["vector_id"],
            device_id,
            generation,
            finalized["exposure_id"],
            finalized["superseded_count"],
        )
