"""Byte-exact randomization and commitment primitives for the planner switchback.

Implements Section 8.3 of docs/research/planner-efficacy-current-firmware-2026-08-14.md
exactly: the blinded pair-order derivation from a public beacon, the committed
secret X/Y-to-A/B mapping, the domain-separated mapping commitment, the
assignment UUIDv5 construction over raw name bytes, the 30-day blinded schedule
with its RFC 8785 (JCS) canonical hash, and the Section 8.9 version-1
`assignment_treatment_bytes` octets.

Everything here is deterministic and stdlib-only. This module does not
generate the mapping secret: per Jason's 2026-08-15 protocol decision, the
restricted assignment service will perform the automated operating-system
CSPRNG draw. The CLI only reads an existing secret file to commit, verify, or
reveal.

RFC 8785 note: the canonicalizer below intentionally supports only `str`, `int`,
`list`, and `dict` values (no float, bool, or null). Every value in the blinded
assignment JSON is an int or a string, so the ECMA-262 number-serialization
rules of RFC 8785 never have to be exercised; any other type is rejected so a
non-canonical value cannot slip into a hashed artifact.

Treatment octets note: Lane A implements the same Section 8.9 octets inside
verdify_schemas/policy_vector.py. This research package deliberately does NOT
import verdify_schemas; a cross-check test will unify the two implementations
against shared golden fixtures once Lane A lands.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

# --- Domain-separation tags (ASCII, locked by Section 8.3) ---
ORDER_DOMAIN_TAG = b"verdify-switchback-order-v1"
ARM_MAP_DOMAIN_TAG = b"verdify-switchback-arm-map-v1"
MAP_COMMIT_DOMAIN_TAG = b"verdify-switchback-map-commit-v1"

BLINDED_SCHEDULE_SCHEMA = "verdify-switchback-blinded-schedule-v1"
RESOLVED_SCHEDULE_SCHEMA = "verdify-switchback-resolved-schedule-v1"

MAPPING_SECRET_LENGTH = 32
DEFAULT_TIMEZONE = "America/Denver"
DEFAULT_PAIRS = 15

_LOCAL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Historical v1 canonicalizer bound. Protocol v2 applies the stricter I-JSON
# profile in ``rfc8785_canonicalize_nfc_ijson`` without changing v1 bytes.
_JCS_MAX_INT = 2**53
_JCS_IJSON_MAX_INT = 2**53 - 1


def _utf8_nfc(study_id: str) -> bytes:
    """Encode a study id as Unicode NFC UTF-8, per Section 8.3."""
    return unicodedata.normalize("NFC", study_id).encode("utf-8")


def pair_order(beacon_bytes: bytes, study_id: str, j: int) -> Literal["XY", "YX"]:
    """Blinded order of labels for pair ``j`` from the public beacon.

    HMAC-SHA256(key=beacon_bytes,
                msg=ASCII("verdify-switchback-order-v1") || 0x00 ||
                    UTF8_NFC(study_id) || 0x00 || uint32_be(j));
    (digest[31] & 0x01) == 0 -> "XY", else "YX".
    """
    if not isinstance(beacon_bytes, (bytes, bytearray)) or len(beacon_bytes) == 0:
        raise ValueError("beacon_bytes must be non-empty bytes")
    if not 0 <= j <= 0xFFFFFFFF:
        raise ValueError(f"pair index j={j} outside uint32 range")
    message = ORDER_DOMAIN_TAG + b"\x00" + _utf8_nfc(study_id) + b"\x00" + struct.pack(">I", j)
    digest = hmac.new(bytes(beacon_bytes), message, hashlib.sha256).digest()
    return "XY" if (digest[31] & 0x01) == 0 else "YX"


def arm_mapping(mapping_secret: bytes, study_id: str) -> dict[str, str]:
    """Resolve blinded labels to physical arms from the committed secret.

    HMAC-SHA256(key=mapping_secret,
                msg=ASCII("verdify-switchback-arm-map-v1") || 0x00 ||
                    UTF8_NFC(study_id));
    bit = digest[31] & 0x01; bit 0 -> {X: A, Y: B}; bit 1 -> {X: B, Y: A}.
    """
    _require_mapping_secret(mapping_secret)
    message = ARM_MAP_DOMAIN_TAG + b"\x00" + _utf8_nfc(study_id)
    digest = hmac.new(bytes(mapping_secret), message, hashlib.sha256).digest()
    if (digest[31] & 0x01) == 0:
        return {"X": "A", "Y": "B"}
    return {"X": "B", "Y": "A"}


def mapping_commitment(study_id: str, mapping_secret: bytes) -> bytes:
    """Publishable commitment to the mapping secret.

    SHA256(ASCII("verdify-switchback-map-commit-v1") || 0x00 ||
           UTF8_NFC(study_id) || 0x00 || mapping_secret).
    """
    _require_mapping_secret(mapping_secret)
    preimage = MAP_COMMIT_DOMAIN_TAG + b"\x00" + _utf8_nfc(study_id) + b"\x00" + bytes(mapping_secret)
    return hashlib.sha256(preimage).digest()


def _require_mapping_secret(mapping_secret: bytes) -> None:
    if not isinstance(mapping_secret, (bytes, bytearray)):
        raise TypeError("mapping_secret must be bytes")
    if len(mapping_secret) != MAPPING_SECRET_LENGTH:
        raise ValueError(f"mapping_secret must be exactly {MAPPING_SECRET_LENGTH} bytes, got {len(mapping_secret)}")


def assignment_uuid(namespace_uuid: uuid.UUID, study_id: str, local_date: str) -> uuid.UUID:
    """UUIDv5 over raw name bytes ``UTF8_NFC(study_id) || 0x00 || ASCII(YYYY-MM-DD)``.

    Implemented directly over the byte string (RFC 4122 SHA-1 construction)
    because ``uuid.uuid5`` only accepts ``str`` names and Section 8.3 specifies
    the exact name bytes including the 0x00 separator.
    """
    if not _LOCAL_DATE_RE.match(local_date):
        raise ValueError(f"local_date {local_date!r} is not YYYY-MM-DD")
    name_bytes = _utf8_nfc(study_id) + b"\x00" + local_date.encode("ascii")
    # Non-cryptographic RFC 4122 name-based construction, not a security use.
    digest = hashlib.sha1(namespace_uuid.bytes + name_bytes, usedforsecurity=False).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50  # version 5
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(raw))


# --- RFC 8785 JSON Canonicalization Scheme (int/str-restricted profile) ---

_JCS_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _jcs_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch in _JCS_SHORT_ESCAPES:
            out.append(_JCS_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def rfc8785_canonicalize(value: Any) -> str:
    """RFC 8785 canonical JSON text for int/str/list/dict values.

    Object keys sort by UTF-16 code units; strings use minimal escapes; no
    insignificant whitespace. Floats, booleans, and None are rejected by design
    (see module docstring) so number-canonicalization edge cases cannot arise.
    """
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise TypeError(f"non-canonical value type for hashed artifact: {value!r}")
    if isinstance(value, int):
        if abs(value) > _JCS_MAX_INT:
            raise ValueError(f"integer {value} exceeds IEEE-754 exact range")
        return str(value)
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(rfc8785_canonicalize(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError(f"object key must be str, got {key!r}")
        ordered = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs_string(k)}:{rfc8785_canonicalize(value[k])}" for k in ordered) + "}"
    raise TypeError(f"unsupported type for canonical JSON: {type(value).__name__}")


def rfc8785_sha256(value: Any) -> bytes:
    """SHA256(UTF8(RFC8785(value)))."""
    return hashlib.sha256(rfc8785_canonicalize(value).encode("utf-8")).digest()


def _validate_nfc_ijson(value: Any) -> None:
    """Validate the stricter protocol-v2 receipt profile without mutating v1."""
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise TypeError(f"non-canonical value type for hashed artifact: {value!r}")
    if isinstance(value, int):
        if abs(value) > _JCS_IJSON_MAX_INT:
            raise ValueError(f"integer {value} exceeds I-JSON exact range")
        return
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("canonical JSON strings must already be Unicode NFC")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("canonical JSON strings must contain only valid Unicode scalar values") from exc
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_nfc_ijson(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"object key must be str, got {key!r}")
            _validate_nfc_ijson(key)
            _validate_nfc_ijson(item)
        return
    raise TypeError(f"unsupported type for canonical JSON: {type(value).__name__}")


def rfc8785_canonicalize_nfc_ijson(value: Any) -> str:
    """Canonicalize the protocol-v2 NFC/I-JSON receipt profile."""
    _validate_nfc_ijson(value)
    return rfc8785_canonicalize(value)


# --- Blinded schedule generation ---


def _rfc3339_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def blinded_schedule(
    study_id: str,
    start_local_date: str,
    *,
    beacon_bytes: bytes,
    namespace_uuid: uuid.UUID,
    timezone: str = DEFAULT_TIMEZONE,
    pairs: int = DEFAULT_PAIRS,
) -> dict[str, Any]:
    """Generate the 30-day blinded assignment schedule and its canonical hash.

    Returns ``{"blinded_assignment": <int/str-only dict>,
    "schedule_hash_sha256": <hex>}`` where the hash is
    SHA256(UTF8(RFC8785(blinded_assignment_json))). Raises if the local-day
    window crosses a UTC-offset (DST) transition, per Section 8.3: version 1
    requires 88 expected bins and a wall-clock washout to mean the same elapsed
    exposure on every day.
    """
    if pairs < 1:
        raise ValueError("pairs must be >= 1")
    if not _LOCAL_DATE_RE.match(start_local_date):
        raise ValueError(f"start_local_date {start_local_date!r} is not YYYY-MM-DD")
    tz = ZoneInfo(timezone)
    start = date.fromisoformat(start_local_date)
    total_days = pairs * 2

    # Local midnights for day 0 .. day N inclusive (N+1 boundaries).
    midnights = [
        datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=tz) for i in range(total_days + 1)
    ]
    offsets = {m.utcoffset() for m in midnights}
    if len(offsets) != 1:
        raise ValueError(
            f"window {start_local_date} + {total_days} local days in {timezone} crosses a "
            "UTC-offset transition; Section 8.3 version 1 forbids this — define a new "
            "protocol version with elapsed-time windows instead"
        )

    assignments: list[dict[str, Any]] = []
    for j in range(pairs):
        order = pair_order(beacon_bytes, study_id, j)
        for day_in_pair in (1, 2):
            day_index = 2 * j + (day_in_pair - 1)
            local_date = (start + timedelta(days=day_index)).isoformat()
            label = order[day_in_pair - 1]
            assignments.append(
                {
                    "assignment_uuid": str(assignment_uuid(namespace_uuid, study_id, local_date)),
                    "blinded_label": label,
                    "day_in_pair": day_in_pair,
                    "local_date": local_date,
                    "pair_id": j,
                    "utc_end": _rfc3339_utc(midnights[day_index + 1]),
                    "utc_start": _rfc3339_utc(midnights[day_index]),
                }
            )

    blinded_assignment: dict[str, Any] = {
        "assignments": assignments,
        "namespace_uuid": str(namespace_uuid),
        "pairs": pairs,
        "schema": BLINDED_SCHEDULE_SCHEMA,
        "start_local_date": start_local_date,
        "study_id": study_id,
        "timezone": timezone,
    }
    return {
        "blinded_assignment": blinded_assignment,
        "schedule_hash_sha256": rfc8785_sha256(blinded_assignment).hex(),
    }


def _extract_blinded(schedule: dict[str, Any]) -> dict[str, Any]:
    blinded = schedule.get("blinded_assignment", schedule)
    if blinded.get("schema") != BLINDED_SCHEDULE_SCHEMA:
        raise ValueError(f"not a {BLINDED_SCHEDULE_SCHEMA} document")
    return blinded


def resolve_schedule(schedule: dict[str, Any], mapping_secret: bytes) -> dict[str, Any]:
    """Resolve a blinded schedule to physical arms for the Section 8.3 reveal step.

    Returns the resolved schedule (physical arm A/B per day), the mapping, the
    regenerated mapping commitment hex, and the recomputed blinded schedule
    hash, so the published commitment and hash can be checked byte-for-byte.
    """
    blinded = _extract_blinded(schedule)
    mapping = arm_mapping(mapping_secret, blinded["study_id"])
    resolved_days = []
    for row in blinded["assignments"]:
        resolved = dict(row)
        resolved["physical_arm"] = mapping[row["blinded_label"]]
        resolved_days.append(resolved)
    return {
        "schema": RESOLVED_SCHEDULE_SCHEMA,
        "study_id": blinded["study_id"],
        "timezone": blinded["timezone"],
        "start_local_date": blinded["start_local_date"],
        "pairs": blinded["pairs"],
        "namespace_uuid": blinded["namespace_uuid"],
        "arm_mapping": mapping,
        "mapping_commitment_sha256": mapping_commitment(blinded["study_id"], mapping_secret).hex(),
        "blinded_schedule_hash_sha256": rfc8785_sha256(blinded).hex(),
        "assignments": resolved_days,
    }


# --- Section 8.9 version-1 assignment_treatment_bytes ---
# NOTE: Lane A implements these same octets in verdify_schemas/policy_vector.py.
# Keep this standalone (no verdify_schemas import); a cross-check test unifies
# the two implementations against shared golden fixtures once Lane A lands.

RANDOMIZED_TAG = 0x01
QUALIFICATION_TAG = 0x02
AA_TAG = 0x03

QUALIFICATION_OPERATIONS = {
    "analyzed": 0x01,
    "positioning": 0x02,
    "baseline_recovery": 0x03,
    "identity_hold": 0x04,
}

# Locked 0x00..0x03 regime order from Section 8.3.
REGIME_CODES = {
    "night": 0x00,
    "hot_bright_dry": 0x01,
    "hot_bright_humid": 0x02,
    "other_daylight": 0x03,
}


def randomized_treatment_bytes(blinded_label: str) -> bytes:
    """``0x01 || 0x58`` for X, ``0x01 || 0x59`` for Y."""
    if blinded_label not in ("X", "Y"):
        raise ValueError(f"blinded label must be 'X' or 'Y', got {blinded_label!r}")
    return bytes([RANDOMIZED_TAG]) + blinded_label.encode("ascii")


def qualification_treatment_bytes(
    operation: str,
    source_template_uuid: uuid.UUID,
    target_template_uuid: uuid.UUID,
    regime: str,
) -> bytes:
    """``0x02 || op || source_uuid16 || target_uuid16 || regime`` (Section 8.9)."""
    if operation not in QUALIFICATION_OPERATIONS:
        raise ValueError(f"unknown qualification operation {operation!r}")
    if regime not in REGIME_CODES:
        raise ValueError(f"unknown regime {regime!r}")
    return (
        bytes([QUALIFICATION_TAG, QUALIFICATION_OPERATIONS[operation]])
        + source_template_uuid.bytes
        + target_template_uuid.bytes
        + bytes([REGIME_CODES[regime]])
    )


def aa_treatment_bytes(lane: int) -> bytes:
    """``0x03 || lane`` for A/A lanes 0 and 1."""
    if lane not in (0, 1):
        raise ValueError(f"A/A lane must be 0 or 1, got {lane!r}")
    return bytes([AA_TAG, lane])
