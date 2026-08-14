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
fn_record_device_snapshot readback -> exposure opens via fn_open_exposure
ONLY when the device echoes the exact schema/generation/assignment/
activation identity (contract v2, #586: the aggregated policy_identity
sensor carries the FULL activation hash; content identity is bound inside
it per audit §8.9). Mismatch or readback timeout closes any open exposure
(bounded close reasons), aborts the device transaction, and requeues the
outbox row with a bounded error class + capped exponential backoff.
"""

import os

import asyncpg
import shared
from esp32_push import device_writes_enabled
from policy_transport import (
    ERROR_CLASS_GENERATION_CONFLICT,
    ERROR_CLASS_HASH_MISMATCH,
    ERROR_CLASS_INTERNAL,
    ERROR_CLASS_SCHEMA_MISMATCH,
    ERROR_CLASS_TIMEOUT,
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

_LEASE_SECONDS = 120
_MAX_ATTEMPTS = 10
_BACKOFF_BASE_S = 30
_BACKOFF_CAP_S = 1800
_READBACK_TIMEOUT_S = 20.0
_READBACK_POLL_S = 1.0

_transport_factory = Esp32PolicyTransport
_services_unavailable_logged = False

_LEASE_SQL = f"""
UPDATE policy_delivery_outbox
   SET state = 'leased',
       lease_owner = $1,
       lease_expires_at = now() + interval '{_LEASE_SECONDS} seconds',
       attempt_count = attempt_count + 1,
       updated_at = now()
 WHERE outbox_id = (
       SELECT outbox_id
         FROM policy_delivery_outbox
        WHERE (state IN ('queued', 'failed')
               AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
           OR (state IN ('leased', 'staging', 'staged', 'activating')
               AND lease_expires_at IS NOT NULL AND lease_expires_at < now())
        ORDER BY created_at
        LIMIT 1
          FOR UPDATE SKIP LOCKED
 )
 RETURNING outbox_id::text AS outbox_id, device_id, vector_id::text AS vector_id, attempt_count
"""


def _stage_name(raw: str) -> str:
    """Normalize a transport stage/service name onto the bounded attempt enum."""
    stage = raw.removeprefix("policy_")
    return stage if stage in ("begin", "chunk", "validate", "commit", "activate", "abort") else "activate"


def _lease_owner() -> str:
    return f"policy_delivery/{os.environ.get('HOSTNAME', 'ingestor')}"


async def _record_attempt(conn, outbox_id: str, attempt_no: int, stage: str, ok: bool, error_class: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO policy_delivery_attempts (outbox_id, attempt_no, stage, finished_at, ok, error_class)
        VALUES ($1::uuid, $2, $3, now(), $4, $5)
        ON CONFLICT (outbox_id, attempt_no, stage) DO NOTHING
        """,
        outbox_id,
        attempt_no,
        stage,
        ok,
        error_class,
    )


async def _set_outbox_state(conn, outbox_id: str, state: str, error_class: str | None = None) -> None:
    await conn.execute(
        f"""
        UPDATE policy_delivery_outbox
           SET state = $2,
               last_error_class = COALESCE($3, last_error_class),
               staged_at = CASE WHEN $2 = 'staged' THEN now() ELSE staged_at END,
               activated_at = CASE WHEN $2 = 'activated' THEN now() ELSE activated_at END,
               next_attempt_at = CASE WHEN $2 = 'failed'
                                      THEN now() + make_interval(secs => least({_BACKOFF_CAP_S},
                                           {_BACKOFF_BASE_S} * power(2, greatest(0, attempt_count - 1))))
                                      ELSE next_attempt_at END,
               updated_at = now()
         WHERE outbox_id = $1::uuid
        """,
        outbox_id,
        state,
        error_class,
    )


async def _requeue(conn, lease, error_class: str) -> None:
    """Failed/abandoned disposition with bounded error class + capped backoff."""
    if int(lease["attempt_count"]) >= _MAX_ATTEMPTS:
        await _set_outbox_state(conn, lease["outbox_id"], "abandoned", error_class)
        log.error(
            "policy_delivery: outbox %s abandoned after %d attempts (%s)",
            lease["outbox_id"],
            lease["attempt_count"],
            error_class,
        )
        return
    await _set_outbox_state(conn, lease["outbox_id"], "failed", error_class)
    log.warning(
        "policy_delivery: outbox %s requeued (attempt %d, %s)",
        lease["outbox_id"],
        lease["attempt_count"],
        error_class,
    )


async def _close_open_exposures(conn, device_id: str, reason: str, snapshot_id: int | None = None) -> int:
    rows = await conn.fetch(
        "SELECT exposure_id FROM policy_exposures WHERE device_id = $1 AND ended_at IS NULL",
        device_id,
    )
    for row in rows:
        await conn.fetchval(
            "SELECT fn_close_exposure($1::uuid, $2, $3)",
            row["exposure_id"],
            reason,
            snapshot_id,
        )
    return len(rows)


async def _await_identity_echo(transport, expected_generation: int):
    """Poll the device identity readback until it reflects the new generation."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _READBACK_TIMEOUT_S
    identity = transport.read_identity()
    while loop.time() < deadline:
        if identity is not None and identity.device_generation == expected_generation:
            return identity
        await asyncio.sleep(_READBACK_POLL_S)
        identity = transport.read_identity()
    return identity


async def policy_delivery_worker(pool: asyncpg.Pool) -> None:
    """Lease one outbox row and drive it through the staged device commit."""
    global _services_unavailable_logged
    if policy_vector_mode() != POLICY_VECTOR_MODE_LIVE or not active_experiment_id():
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
        lease = await conn.fetchrow(_LEASE_SQL, _lease_owner())
        if lease is None:
            return

        vector = await conn.fetchrow(
            """
            SELECT v.vector_id::text AS vector_id, v.assignment_id::text AS assignment_id,
                   v.experiment_id::text AS experiment_id, v.greenhouse_id,
                   v.device_generation, v.canonical_bytes, v.content_sha256,
                   v.activation_sha256, v.status, upper(v.validity) AS valid_to,
                   a.status AS assignment_status
              FROM effective_policy_vectors v
              JOIN control_assignments a ON a.assignment_id = v.assignment_id
             WHERE v.vector_id = $1::uuid
            """,
            lease["vector_id"],
        )
        expired = vector is None or vector["status"] not in ("ready", "delivering")
        stale = vector is not None and (vector["assignment_status"] != "active")
        if expired or stale:
            await _set_outbox_state(conn, lease["outbox_id"], "abandoned", ERROR_CLASS_INTERNAL)
            if vector is not None and vector["status"] in ("ready", "delivering"):
                await conn.execute(
                    "UPDATE effective_policy_vectors SET status = 'aborted', updated_at = now() WHERE vector_id = $1::uuid",
                    lease["vector_id"],
                )
            log.warning(
                "policy_delivery: outbox %s abandoned (vector %s no longer deliverable)",
                lease["outbox_id"],
                lease["vector_id"],
            )
            return

        attempt_no = int(lease["attempt_count"])
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

        await conn.execute(
            "UPDATE effective_policy_vectors SET status = 'delivering', updated_at = now() "
            "WHERE vector_id = $1::uuid AND status = 'ready'",
            lease["vector_id"],
        )
        await _set_outbox_state(conn, lease["outbox_id"], "staging")

        # ── Staged transaction over the transport ────────────────────────────
        try:
            await transport.begin(request)
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "begin", True, None)
            for chunk in policy_chunk_payloads(request.canonical_bytes):
                await transport.stage_chunk(int(chunk["seq"]), str(chunk["data_hex"]))
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "chunk", True, None)
            await transport.validate()
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "validate", True, None)
            await _set_outbox_state(conn, lease["outbox_id"], "staged")
            await transport.commit()
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "commit", True, None)
            await _set_outbox_state(conn, lease["outbox_id"], "activating")
        except PolicyTransportError as exc:
            stage = _stage_name(exc.stage)
            await _record_attempt(conn, lease["outbox_id"], attempt_no, stage, False, exc.error_class)
            if stage == "commit":
                # Post-commit device state is unknown: the confirmed exposure
                # interval (if any) can no longer be trusted.
                await _close_open_exposures(conn, device_id, "device_lost")
            try:
                await transport.abort(f"{stage}:{exc.error_class}")
            except PolicyTransportError:
                log.warning("policy_delivery: abort after %s failure did not reach the device", stage)
            await _requeue(conn, lease, exc.error_class)
            return

        # ── Exact-echo readback -> snapshot -> exposure ──────────────────────
        identity = await _await_identity_echo(transport, generation)
        if identity is None:
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "activate", False, ERROR_CLASS_TIMEOUT)
            await _close_open_exposures(conn, device_id, "device_lost")
            await _requeue(conn, lease, ERROR_CLASS_TIMEOUT)
            return

        snapshot_id = await conn.fetchval(
            "SELECT fn_record_device_snapshot($1, $2, $3, $4, $5::uuid, $6, $7, $8, $9)",
            device_id,
            vector["greenhouse_id"],
            str(identity.schema_revision) if identity.schema_revision is not None else None,
            identity.device_generation,
            identity.assignment_id,
            # Contract v2 (#586): the device echoes no separate content hash —
            # content identity is bound inside activation_sha256 (audit §8.9).
            None,
            identity.activation_sha256,
            identity.apply_state or "unknown",
            None,  # firmware_revision: Lane E readback
        )

        # Exact echo (contract v2): schema/generation/assignment/activation.
        echo_exact = (
            identity.schema_revision == WIRE_SCHEMA_VERSION
            and identity.assignment_id == vector["assignment_id"]
            and identity.device_generation == generation
            and identity.activation_sha256 == vector["activation_sha256"]
        )
        if not echo_exact:
            if identity.device_generation != generation:
                error_class = ERROR_CLASS_GENERATION_CONFLICT
            elif identity.schema_revision != WIRE_SCHEMA_VERSION:
                error_class = ERROR_CLASS_SCHEMA_MISMATCH
            else:
                error_class = ERROR_CLASS_HASH_MISMATCH
            await _record_attempt(conn, lease["outbox_id"], attempt_no, "activate", False, error_class)
            await _close_open_exposures(conn, device_id, "protocol_deviation", snapshot_id)
            try:
                await transport.abort(f"echo:{error_class}")
            except PolicyTransportError:
                log.warning("policy_delivery: abort after echo mismatch did not reach the device")
            await _requeue(conn, lease, error_class)
            return

        # A prior vector's confirmed interval ends exactly where the new
        # device-confirmed identity begins.
        superseded = await _close_open_exposures(conn, device_id, "superseded", snapshot_id)
        exposure_id = await conn.fetchval(
            "SELECT fn_open_exposure($1::uuid, $2, $3, $4)",
            lease["vector_id"],
            device_id,
            snapshot_id,
            "policy_delivery",
        )
        await _record_attempt(conn, lease["outbox_id"], attempt_no, "activate", True, None)
        await _set_outbox_state(conn, lease["outbox_id"], "activated")
        log.info(
            "policy_delivery: vector %s active on %s (generation %d, exposure %s, superseded %d)",
            lease["vector_id"],
            device_id,
            generation,
            exposure_id,
            superseded,
        )
