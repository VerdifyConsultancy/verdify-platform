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

Readback (contract v2, #586 decision): ONE aggregated identity text sensor
(heap-frugal — no per-field entities) ingested into `shared.policy_readback`
and persisted via fn_record_device_snapshot:

    policy_identity — "<schema_revision>|<generation>|<assignment_uuid>|
                       <activation_sha256 full 64 hex>|<apply_state>"

Field semantics (parse with :func:`parse_policy_identity` — strict field
count and per-field format validation, split on '|'):

    schema_revision   — decimal wire schema version the firmware compiled
    generation        — decimal active device_generation
    assignment_uuid   — canonical lowercase UUID, or "-" when none is bound
                        (ROM baseline / recovery)
    activation_sha256 — FULL 64-lowercase-hex activation hash, or "-" when no
                        activation is bound. The v1 truncated content/
                        activation prefixes are gone: content identity is
                        bound inside activation_sha256 (audit §8.9), so the
                        activation echo transitively confirms content.
    apply_state       — staged|active|rom_baseline|recovery|unknown

Exposure only opens when this echo matches the admitted vector identity
EXACTLY on schema/generation/assignment/activation (fn_open_exposure,
migrations 207/209).
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_TRANSPORT_CONTRACT_VERSION = 2

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

# ── Readback sensor object id (device-echoed aggregated identity) ────────────
# Contract v2 (#586): the six v1 per-field sensors are replaced by ONE
# aggregated text sensor; see parse_policy_identity below.
POLICY_IDENTITY_SENSOR = "policy_identity"

POLICY_READBACK_SENSORS: tuple[str, ...] = (POLICY_IDENTITY_SENSOR,)

POLICY_APPLY_STATES: tuple[str, ...] = ("staged", "active", "rom_baseline", "recovery", "unknown")

# "-" marks an unbound assignment/activation (ROM baseline / recovery).
POLICY_IDENTITY_UNBOUND = "-"

_POLICY_IDENTITY_FIELD_COUNT = 5
_UUID_GROUP_LENGTHS = (8, 4, 4, 4, 12)


@dataclass(frozen=True)
class PolicyIdentityEcho:
    """Parsed `policy_identity` payload (device-echoed active identity)."""

    schema_revision: int
    generation: int
    assignment_id: str | None
    activation_sha256: str | None
    apply_state: str


def _is_lower_hex(value: str) -> bool:
    return bool(value) and all(c in "0123456789abcdef" for c in value)


def _is_canonical_uuid(value: str) -> bool:
    groups = value.split("-")
    return len(groups) == len(_UUID_GROUP_LENGTHS) and all(
        len(group) == length and _is_lower_hex(group) for group, length in zip(groups, _UUID_GROUP_LENGTHS, strict=True)
    )


def parse_policy_identity(payload: str) -> PolicyIdentityEcho:
    """Parse one aggregated ``policy_identity`` echo; strict, raises ValueError.

    Format (contract v2): exactly five '|'-separated fields —
    ``schema_revision|generation|assignment_uuid|activation_sha256|apply_state``.
    ``assignment_uuid`` / ``activation_sha256`` may be "-" (unbound; ROM
    baseline or recovery). Anything malformed raises — the caller treats it as
    "no echo", never a partially-trusted identity.
    """
    if not isinstance(payload, str):
        raise ValueError("policy_identity payload must be a string")
    fields = payload.split("|")
    if len(fields) != _POLICY_IDENTITY_FIELD_COUNT:
        raise ValueError(
            f"policy_identity must have exactly {_POLICY_IDENTITY_FIELD_COUNT} '|'-separated fields (got {len(fields)})"
        )
    schema_raw, generation_raw, assignment_raw, activation_raw, apply_state = fields
    if not schema_raw.isdigit():
        raise ValueError(f"policy_identity schema_revision {schema_raw!r} is not a decimal integer")
    if not generation_raw.isdigit():
        raise ValueError(f"policy_identity generation {generation_raw!r} is not a decimal integer")
    assignment_id: str | None = None
    if assignment_raw != POLICY_IDENTITY_UNBOUND:
        if not _is_canonical_uuid(assignment_raw):
            raise ValueError(f"policy_identity assignment {assignment_raw!r} is not a canonical lowercase UUID")
        assignment_id = assignment_raw
    activation_sha256: str | None = None
    if activation_raw != POLICY_IDENTITY_UNBOUND:
        if len(activation_raw) != 64 or not _is_lower_hex(activation_raw):
            raise ValueError("policy_identity activation hash must be the FULL 64 lowercase hex characters")
        activation_sha256 = activation_raw
    if apply_state not in POLICY_APPLY_STATES:
        raise ValueError(f"policy_identity apply_state {apply_state!r} not in {POLICY_APPLY_STATES}")
    return PolicyIdentityEcho(
        schema_revision=int(schema_raw),
        generation=int(generation_raw),
        assignment_id=assignment_id,
        activation_sha256=activation_sha256,
        apply_state=apply_state,
    )


def format_policy_identity(echo: PolicyIdentityEcho) -> str:
    """Inverse of :func:`parse_policy_identity` (test/fake transports)."""
    return "|".join(
        (
            str(int(echo.schema_revision)),
            str(int(echo.generation)),
            echo.assignment_id or POLICY_IDENTITY_UNBOUND,
            echo.activation_sha256 or POLICY_IDENTITY_UNBOUND,
            echo.apply_state,
        )
    )


# Raw canonical-vector bytes per policy_chunk call. 96 raw bytes hex-encode to
# 192 characters — comfortably inside ESPHome string-arg limits and small
# enough that staging never allocates a large contiguous heap block (#428).
# The full version-2 vector (178 bytes) stages in two chunks.
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
