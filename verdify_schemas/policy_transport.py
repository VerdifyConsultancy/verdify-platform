"""Device policy-transport contract (Lane C of #584; consumed by Lane E).

ONE shared naming contract for the staged whole-vector policy transaction
between the ingestor delivery worker (`ingestor/tasks/policy_delivery.py` via
`ingestor/policy_transport.py`) and the firmware native-API services Lane E
registers. Both sides import/emit exactly these names, so the firmware can be
built after the delivery worker without renegotiating the surface.

Native API services (ESPHome user services, heap-budgeted — the repo
deliberately runs ONE service today, `set_band_anchor`; these five follow the
same registration pattern):

    policy_begin(generation, total_size, chunk_count,
                 content_sha256, activation_sha256, assignment_id)
        Open one staging buffer. Rejects when a transaction is already open
        (device_busy) or generation is not strictly greater than the active
        generation (generation_conflict).
    policy_chunk(seq, data_hex)
        Append one bounded chunk (hex-encoded canonical vector bytes,
        <= POLICY_CHUNK_DATA_MAX_BYTES raw bytes). seq is 0-based and must
        arrive in order.
    policy_validate()
        Decode + bound-check the staged bytes and recompute the content hash;
        the result is published through the readback sensors (apply_state
        'staged' on success), never through a service return value.
    policy_commit(generation)
        Atomically swap the validated staged vector into the active slot and
        journal it (two-copy NVS journal, Lane E). Echoes the new identity
        through the readback sensors.
    policy_abort(reason)
        Discard the staging buffer; identity readback reverts to the active
        vector.

All service arguments are ESPHome-service scalars: int or string. Hashes and
UUIDs travel as lowercase hex / canonical UUID strings. `generation` is the
`effective_policy_vectors.device_generation` value and must fit u32 on this
transport.

Readback sensors (device-echoed identity; ingested into
`shared.policy_readback` and persisted via fn_record_device_snapshot):

    policy_schema_revision     — wire-manifest revision the firmware compiled
    policy_generation          — active device_generation (numeric sensor)
    policy_assignment_id       — active assignment UUID (text sensor)
    policy_content_sha256      — active content hash, lowercase hex (text)
    policy_activation_sha256   — active activation hash, lowercase hex (text)
    policy_apply_state         — staged|active|rom_baseline|recovery|unknown

Exposure only opens when this echo matches the admitted vector identity
EXACTLY (fn_open_exposure, migration 207).
"""

from __future__ import annotations

POLICY_TRANSPORT_CONTRACT_VERSION = 1

# ── Native API service ids (Lane E registers these exact names) ──────────────
POLICY_SERVICE_BEGIN = "policy_begin"
POLICY_SERVICE_CHUNK = "policy_chunk"
POLICY_SERVICE_VALIDATE = "policy_validate"
POLICY_SERVICE_COMMIT = "policy_commit"
POLICY_SERVICE_ABORT = "policy_abort"

POLICY_TRANSPORT_SERVICES: tuple[str, ...] = (
    POLICY_SERVICE_BEGIN,
    POLICY_SERVICE_CHUNK,
    POLICY_SERVICE_VALIDATE,
    POLICY_SERVICE_COMMIT,
    POLICY_SERVICE_ABORT,
)

# ── Readback sensor object ids (device-echoed identity) ──────────────────────
POLICY_READBACK_SCHEMA_REVISION = "policy_schema_revision"
POLICY_READBACK_GENERATION = "policy_generation"
POLICY_READBACK_ASSIGNMENT_ID = "policy_assignment_id"
POLICY_READBACK_CONTENT_SHA256 = "policy_content_sha256"
POLICY_READBACK_ACTIVATION_SHA256 = "policy_activation_sha256"
POLICY_READBACK_APPLY_STATE = "policy_apply_state"

POLICY_READBACK_SENSORS: tuple[str, ...] = (
    POLICY_READBACK_SCHEMA_REVISION,
    POLICY_READBACK_GENERATION,
    POLICY_READBACK_ASSIGNMENT_ID,
    POLICY_READBACK_CONTENT_SHA256,
    POLICY_READBACK_ACTIVATION_SHA256,
    POLICY_READBACK_APPLY_STATE,
)

# Raw canonical-vector bytes per policy_chunk call. 96 raw bytes hex-encode to
# 192 characters — comfortably inside ESPHome string-arg limits and small
# enough that staging never allocates a large contiguous heap block (#428).
# The full version-1 vector (~210 bytes) stages in three chunks.
POLICY_CHUNK_DATA_MAX_BYTES = 96

_U32_MAX = 2**32 - 1


def _require_u32(generation: int) -> int:
    if not isinstance(generation, int) or isinstance(generation, bool) or not 0 < generation <= _U32_MAX:
        raise ValueError(f"policy generation {generation!r} must be an int in (0, 2^32) on this transport")
    return generation


def _require_hex_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def policy_chunk_payloads(vector_bytes: bytes) -> list[dict[str, int | str]]:
    """Split canonical vector bytes into ordered ``policy_chunk`` payloads."""
    if not isinstance(vector_bytes, (bytes, bytearray, memoryview)) or not bytes(vector_bytes):
        raise ValueError("vector_bytes must be non-empty bytes")
    data = bytes(vector_bytes)
    return [
        {"seq": index, "data_hex": data[offset : offset + POLICY_CHUNK_DATA_MAX_BYTES].hex()}
        for index, offset in enumerate(range(0, len(data), POLICY_CHUNK_DATA_MAX_BYTES))
    ]


def policy_begin_payload(
    *,
    generation: int,
    vector_bytes: bytes,
    content_sha256_hex: str,
    activation_sha256_hex: str,
    assignment_id: str,
) -> dict[str, int | str]:
    """The exact argument set of one ``policy_begin`` service call."""
    data = bytes(vector_bytes)
    if not data:
        raise ValueError("vector_bytes must be non-empty bytes")
    return {
        "generation": _require_u32(generation),
        "total_size": len(data),
        "chunk_count": len(policy_chunk_payloads(data)),
        "content_sha256": _require_hex_sha256(content_sha256_hex, "content_sha256"),
        "activation_sha256": _require_hex_sha256(activation_sha256_hex, "activation_sha256"),
        "assignment_id": str(assignment_id),
    }


def policy_commit_payload(*, generation: int) -> dict[str, int]:
    return {"generation": _require_u32(generation)}


def policy_abort_payload(*, reason: str) -> dict[str, str]:
    return {"reason": str(reason)[:120]}
