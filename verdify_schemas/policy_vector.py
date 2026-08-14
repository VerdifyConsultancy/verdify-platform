"""Canonical policy-vector wire codec (Lane A of #582; audit §8.9).

One 48-field planner policy vector has exactly one byte encoding, one
content hash, and one activation hash — identical here, in the generated
firmware header (`firmware/lib/policy_vector_generated.h`), and in the twin.

Wire format, version 2 (all integers big-endian; v2 retired wire_id 6,
`direct_wet_stress_latest_hour`, per the #588 decision — retired ids are
never reused):

    header:  magic b"VPV1" | schema version u8 | first 8 bytes of the
             wire-manifest SHA-256 | field count u8 (48)
    records: ordered by ascending permanent wire_id —
             wire_id u16 | fixed-width raw value per wire_kind
             (u8/u16/u32 unsigned; i16 two's complement; bool one byte
             0x00/0x01)

Values are fixed-point: raw = value × wire_scale, value = raw / wire_scale.
Quantization is round-half-even and every in-bounds value on the wire grid
round-trips exactly. Decoding is a strict inverse: bad magic/version/digest/
count, unknown, duplicate, or out-of-order wire_ids, out-of-bounds raws, and
trailing bytes are all rejected.

Hash domains (audit §8.9):

    content_sha256    = SHA-256(b"verdify-policy-content-v1" || 0x00 ||
                        schema version byte || 32-byte manifest digest ||
                        canonical vector bytes || canonical JSON of the
                        policy revision ids)
    activation_sha256 = SHA-256(b"verdify-policy-activation-v1" || 0x00 ||
                        content hash || experiment UUID (16, RFC 4122
                        network order) || assignment UUID (16) ||
                        treatment length u8 || treatment octets ||
                        generation u64 || valid_from_us u64 || valid_to_us u64)

Treatment octets are validated exactly per §8.9: randomized 0x01||0x58/0x59;
qualification 0x02||op||source_template_uuid||target_template_uuid||regime
(op 0x01 analyzed / 0x02 positioning / 0x03 baseline recovery / 0x04 identity
hold; regime 0x00..0x03); A/A 0x03||lane (0x00/0x01). Off/shadow mode emits
no activation hash — empty or missing treatment bytes are rejected, never
encoded as a null sentinel.

The wire-manifest digest is SHA-256 over an RFC-8785-style canonical JSON
serialization of `wire_manifest()` (sorted object keys, minimal string
escapes, shortest number forms; implemented locally — the value domain here
needs no exponent notation). The generated C++ header embeds the digest as a
constant, so both languages pin the identical manifest.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Mapping
from fractions import Fraction

from .tunable_registry import (
    POLICY_WIRE_FIELD_COUNT,
    REGISTRY,
    WIRE_SCHEMA_VERSION,
    TunableDef,
    wire_value_bounds,
)

POLICY_VECTOR_MAGIC = b"VPV1"
CONTENT_DOMAIN_TAG = b"verdify-policy-content-v1"
ACTIVATION_DOMAIN_TAG = b"verdify-policy-activation-v1"

WIRE_KIND_WIDTH: dict[str, int] = {"u8": 1, "u16": 2, "i16": 2, "u32": 4, "bool": 1}

TREATMENT_TAG_RANDOMIZED = 0x01
TREATMENT_TAG_QUALIFICATION = 0x02
TREATMENT_TAG_AA = 0x03
RANDOMIZED_ARM_OCTETS = (0x58, 0x59)  # ASCII 'X' / 'Y'
QUALIFICATION_OPERATION_CODES: dict[str, int] = {
    "analyzed": 0x01,
    "positioning": 0x02,
    "baseline_recovery": 0x03,
    "identity_hold": 0x04,
}
QUALIFICATION_REGIME_CODES = (0x00, 0x01, 0x02, 0x03)  # locked §8.3 order
AA_LANE_OCTETS = (0x00, 0x01)
_QUALIFICATION_TREATMENT_LEN = 1 + 1 + 16 + 16 + 1

_U64_MAX = 2**64 - 1


def wire_fields() -> tuple[TunableDef, ...]:
    """The 48 wire-schema fields ordered by permanent ``wire_id``."""
    return _WIRE_FIELDS


_WIRE_FIELDS: tuple[TunableDef, ...] = tuple(
    sorted((d for d in REGISTRY.values() if d.wire_id is not None), key=lambda d: d.wire_id or 0)
)
assert len(_WIRE_FIELDS) == POLICY_WIRE_FIELD_COUNT

# Component index (migration 207 policy_*_components.component_index, 0..47
# under wire schema v2): a field's position in the canonical ascending-wire_id
# record order.
WIRE_COMPONENT_INDEXES: dict[str, int] = {defn.name: index for index, defn in enumerate(_WIRE_FIELDS)}


def wire_component_index(name: str) -> int:
    """The 0..47 component index of one wire field (ascending wire_id order)."""
    try:
        return WIRE_COMPONENT_INDEXES[name]
    except KeyError:
        raise ValueError(f"{name!r} is not part of the policy wire schema") from None


POLICY_VECTOR_HEADER_SIZE = len(POLICY_VECTOR_MAGIC) + 1 + 8 + 1
POLICY_VECTOR_SIZE = POLICY_VECTOR_HEADER_SIZE + sum(2 + WIRE_KIND_WIDTH[d.wire_kind or ""] for d in _WIRE_FIELDS)


def wire_raw_bounds(defn: TunableDef) -> tuple[int, int]:
    """Inclusive raw (scaled-integer) bounds for one wire field."""
    lo, hi = wire_value_bounds(defn.name)
    scale = defn.wire_scale or 1
    return round(lo * scale), round(hi * scale)


# ── RFC-8785-style canonical JSON ────────────────────────────────────────────

_JSON_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _canonical_json_str(value: str) -> str:
    out = ['"']
    for char in value:
        if char in _JSON_ESCAPES:
            out.append(_JSON_ESCAPES[char])
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _canonical_json_num(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {value!r} is not canonical-JSON serializable")
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _canonical_json(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _canonical_json_num(value)
    if isinstance(value, str):
        return _canonical_json_str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise ValueError("canonical JSON object keys must be strings")
        if len(set(keys)) != len(keys):
            raise ValueError("canonical JSON object keys must be unique")
        return "{" + ",".join(f"{_canonical_json_str(k)}:{_canonical_json(value[k])}" for k in sorted(keys)) + "}"
    raise ValueError(f"{type(value).__name__} is not canonical-JSON serializable")


def canonical_json_bytes(value: object) -> bytes:
    """Deterministic RFC-8785-style JSON serialization (UTF-8 bytes)."""
    return _canonical_json(value).encode("utf-8")


# ── Wire manifest ────────────────────────────────────────────────────────────


def wire_manifest() -> list[dict]:
    """Ordered wire-schema manifest: one dict per field, ascending wire_id."""
    entries: list[dict] = []
    for defn in wire_fields():
        lo, hi = wire_value_bounds(defn.name)
        entries.append(
            {
                "kind": defn.wire_kind,
                "max": hi,
                "min": lo,
                "name": defn.name,
                "scale": defn.wire_scale,
                "unit": defn.wire_unit,
                "wire_id": defn.wire_id,
            }
        )
    return entries


def wire_manifest_digest() -> bytes:
    """Full SHA-256 over the canonical JSON serialization of the manifest."""
    global _MANIFEST_DIGEST
    if _MANIFEST_DIGEST is None:
        _MANIFEST_DIGEST = hashlib.sha256(canonical_json_bytes(wire_manifest())).digest()
    return _MANIFEST_DIGEST


_MANIFEST_DIGEST: bytes | None = None


# ── Encode / decode ──────────────────────────────────────────────────────────


def _raw_from_value(defn: TunableDef, value: object) -> int:
    name = defn.name
    if defn.wire_kind == "bool":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)) and value in (0, 1):
            return int(value)
        raise ValueError(f"{name}={value!r} is not a valid boolean (expected True/False or exact 0/1)")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}={value!r} is not numeric")
    if not math.isfinite(value):
        raise ValueError(f"{name}={value!r} is not finite")
    # Fraction(float) is exact, so quantization is deterministic round-half-even
    # on the true binary value — no double-rounding through float multiply.
    raw = round(Fraction(value) * (defn.wire_scale or 1))
    raw_lo, raw_hi = wire_raw_bounds(defn)
    if not raw_lo <= raw <= raw_hi:
        scale = defn.wire_scale or 1
        raise ValueError(
            f"{name}={value!r} quantizes to raw {raw}, outside wire bounds "
            f"[{raw_lo / scale:g}, {raw_hi / scale:g}] (raw [{raw_lo}, {raw_hi}])"
        )
    return raw


def _raws_from_values(values: Mapping[str, float | bool]) -> list[int]:
    expected = {defn.name for defn in wire_fields()}
    provided = set(values)
    if provided != expected:
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        raise ValueError(
            f"policy vector requires exactly the {POLICY_WIRE_FIELD_COUNT} wire fields: missing={missing} extra={extra}"
        )
    return [_raw_from_value(defn, values[defn.name]) for defn in wire_fields()]


def quantize_policy_values(values: Mapping[str, float | bool]) -> dict[str, float | bool]:
    """Return the canonical (quantized) values the wire encoding will carry.

    Quantization is deterministic round-half-even onto each field's wire grid;
    missing/extra/nonfinite/out-of-bounds values raise ``ValueError``. Callers
    persist or display these canonical values so what is stored is exactly
    what the device decodes.
    """
    raws = _raws_from_values(values)
    out: dict[str, float | bool] = {}
    for defn, raw in zip(wire_fields(), raws, strict=True):
        out[defn.name] = bool(raw) if defn.wire_kind == "bool" else raw / (defn.wire_scale or 1)
    return out


def encode_policy_vector(values: Mapping[str, float | bool]) -> bytes:
    """Encode all 48 wire fields into the canonical versioned byte vector."""
    raws = _raws_from_values(values)
    out = bytearray(POLICY_VECTOR_MAGIC)
    out.append(WIRE_SCHEMA_VERSION)
    out += wire_manifest_digest()[:8]
    out.append(POLICY_WIRE_FIELD_COUNT)
    for defn, raw in zip(wire_fields(), raws, strict=True):
        out += (defn.wire_id or 0).to_bytes(2, "big")
        out += raw.to_bytes(WIRE_KIND_WIDTH[defn.wire_kind or ""], "big", signed=defn.wire_kind == "i16")
    assert len(out) == POLICY_VECTOR_SIZE
    return bytes(out)


def decode_policy_vector(data: bytes) -> dict[str, float | bool]:
    """Strict inverse of :func:`encode_policy_vector`.

    Rejects bad magic/version/digest/count, unknown, duplicate, or
    out-of-order wire_ids, out-of-bounds raw values, and trailing bytes.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("policy vector must be bytes")
    data = bytes(data)
    if len(data) < POLICY_VECTOR_HEADER_SIZE:
        raise ValueError(f"policy vector truncated: {len(data)} < header size {POLICY_VECTOR_HEADER_SIZE}")
    if data[:4] != POLICY_VECTOR_MAGIC:
        raise ValueError(f"bad policy vector magic {data[:4]!r}")
    if data[4] != WIRE_SCHEMA_VERSION:
        raise ValueError(f"unsupported wire schema version {data[4]} (expected {WIRE_SCHEMA_VERSION})")
    if data[5:13] != wire_manifest_digest()[:8]:
        raise ValueError("wire-manifest digest mismatch: vector was encoded against a different manifest")
    if data[13] != POLICY_WIRE_FIELD_COUNT:
        raise ValueError(f"bad field count {data[13]} (expected {POLICY_WIRE_FIELD_COUNT})")
    if len(data) != POLICY_VECTOR_SIZE:
        raise ValueError(f"bad policy vector size {len(data)} (expected {POLICY_VECTOR_SIZE})")

    known_ids = {defn.wire_id for defn in wire_fields()}
    out: dict[str, float | bool] = {}
    offset = POLICY_VECTOR_HEADER_SIZE
    for index, defn in enumerate(wire_fields()):
        wire_id = int.from_bytes(data[offset : offset + 2], "big")
        offset += 2
        if wire_id != defn.wire_id:
            if wire_id not in known_ids:
                raise ValueError(f"unknown wire_id {wire_id} at record {index}")
            raise ValueError(f"duplicate or out-of-order wire_id {wire_id} at record {index} (expected {defn.wire_id})")
        width = WIRE_KIND_WIDTH[defn.wire_kind or ""]
        raw = int.from_bytes(data[offset : offset + width], "big", signed=defn.wire_kind == "i16")
        offset += width
        raw_lo, raw_hi = wire_raw_bounds(defn)
        if not raw_lo <= raw <= raw_hi:
            raise ValueError(f"{defn.name} raw {raw} outside wire bounds [{raw_lo}, {raw_hi}]")
        out[defn.name] = bool(raw) if defn.wire_kind == "bool" else raw / (defn.wire_scale or 1)
    return out


# ── Content / activation hashes ──────────────────────────────────────────────


def canonical_revision_ids_bytes(policy_revision_ids: Mapping[str, str]) -> bytes:
    """Canonical serialization of the policy revision-id mapping."""
    if not isinstance(policy_revision_ids, Mapping) or not policy_revision_ids:
        raise ValueError("policy_revision_ids must be a non-empty mapping")
    for key, value in policy_revision_ids.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"policy_revision_ids entries must be str -> str (got {key!r}: {value!r})")
    return canonical_json_bytes(dict(policy_revision_ids))


def content_sha256(vector_bytes: bytes, *, schema_version: int, policy_revision_ids: Mapping[str, str]) -> bytes:
    """Content identity of one canonical policy vector (audit §8.9)."""
    if not isinstance(vector_bytes, (bytes, bytearray, memoryview)):
        raise ValueError("vector_bytes must be bytes")
    vector_bytes = bytes(vector_bytes)
    decode_policy_vector(vector_bytes)  # must be a canonical, in-bounds vector
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or not 1 <= schema_version <= 0xFF:
        raise ValueError(f"schema_version {schema_version!r} must be an int in [1, 255]")
    if schema_version != vector_bytes[4]:
        raise ValueError(f"schema_version {schema_version} != vector header version {vector_bytes[4]}")
    digest = hashlib.sha256()
    digest.update(CONTENT_DOMAIN_TAG)
    digest.update(b"\x00")
    digest.update(bytes([schema_version]))
    digest.update(wire_manifest_digest())
    digest.update(vector_bytes)
    digest.update(canonical_revision_ids_bytes(policy_revision_ids))
    return digest.digest()


def validate_treatment_bytes(treatment_bytes: bytes) -> None:
    """Validate §8.9 version-1 treatment octets; raise ``ValueError`` if bad.

    Off/shadow mode emits no activation hash at all, so empty or missing
    treatment bytes are invalid here — never a null sentinel.
    """
    if not isinstance(treatment_bytes, (bytes, bytearray, memoryview)):
        raise ValueError("treatment bytes must be bytes")
    treatment = bytes(treatment_bytes)
    if not treatment:
        raise ValueError("treatment bytes must be non-empty (off/shadow emits no activation hash)")
    tag = treatment[0]
    if tag == TREATMENT_TAG_RANDOMIZED:
        if len(treatment) != 2 or treatment[1] not in RANDOMIZED_ARM_OCTETS:
            raise ValueError("randomized treatment must be exactly 0x01 || 0x58 ('X') or 0x01 || 0x59 ('Y')")
    elif tag == TREATMENT_TAG_QUALIFICATION:
        if len(treatment) != _QUALIFICATION_TREATMENT_LEN:
            raise ValueError(
                "qualification treatment must be exactly 0x02 || op || source_template_uuid(16) || "
                "target_template_uuid(16) || regime"
            )
        if treatment[1] not in QUALIFICATION_OPERATION_CODES.values():
            raise ValueError(f"qualification operation code 0x{treatment[1]:02x} not in 0x01..0x04")
        if treatment[-1] not in QUALIFICATION_REGIME_CODES:
            raise ValueError(f"qualification regime code 0x{treatment[-1]:02x} not in 0x00..0x03")
    elif tag == TREATMENT_TAG_AA:
        if len(treatment) != 2 or treatment[1] not in AA_LANE_OCTETS:
            raise ValueError("A/A treatment must be exactly 0x03 || 0x00 (lane 0) or 0x03 || 0x01 (lane 1)")
    else:
        raise ValueError(f"unknown treatment tag 0x{tag:02x}")


def _uuid_bytes(value: uuid.UUID | str, label: str) -> bytes:
    if isinstance(value, uuid.UUID):
        return value.bytes
    try:
        return uuid.UUID(str(value)).bytes
    except ValueError as exc:
        raise ValueError(f"{label} {value!r} is not a valid UUID") from exc


def _u64(value: int, label: str) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _U64_MAX:
        raise ValueError(f"{label} {value!r} must be an int in [0, 2^64)")
    return value.to_bytes(8, "big")


def activation_sha256(
    content_hash: bytes,
    *,
    experiment_id: uuid.UUID | str,
    assignment_id: uuid.UUID | str,
    treatment_bytes: bytes,
    generation: int,
    valid_from_us: int,
    valid_to_us: int,
) -> bytes:
    """Activation identity: content identity bound to one exposure (§8.9)."""
    if not isinstance(content_hash, (bytes, bytearray, memoryview)) or len(bytes(content_hash)) != 32:
        raise ValueError("content_hash must be exactly 32 bytes")
    validate_treatment_bytes(treatment_bytes)
    valid_from = _u64(valid_from_us, "valid_from_us")
    valid_to = _u64(valid_to_us, "valid_to_us")
    if valid_from_us >= valid_to_us:
        raise ValueError(f"valid_from_us {valid_from_us} must be < valid_to_us {valid_to_us}")
    digest = hashlib.sha256()
    digest.update(ACTIVATION_DOMAIN_TAG)
    digest.update(b"\x00")
    digest.update(bytes(content_hash))
    digest.update(_uuid_bytes(experiment_id, "experiment_id"))
    digest.update(_uuid_bytes(assignment_id, "assignment_id"))
    digest.update(bytes([len(bytes(treatment_bytes))]))
    digest.update(bytes(treatment_bytes))
    digest.update(_u64(generation, "generation"))
    digest.update(valid_from)
    digest.update(valid_to)
    return digest.digest()
