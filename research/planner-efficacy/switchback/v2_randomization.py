"""Restricted protocol-v2 randomization and two-step finalization.

The public production API has no secret or random-source parameter.  One
32-byte OS-CSPRNG secret is generated behind the finalizer boundary, retained
in restricted storage, and never appears in the publishable receipt.  The
Tests inject deterministic bytes by monkeypatching the stdlib CSPRNG at the
test boundary; no caller-secret test class or adapter exists in this runtime
module.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import secrets
import struct
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .randomization import assignment_uuid, rfc8785_canonicalize_nfc_ijson

PAIR_DOMAIN = b"verdify-switchback-v2/pair\x00"
MAPPING_DOMAIN = b"verdify-switchback-v2/mapping\x00"
COMMIT_DOMAIN = b"verdify-switchback-v2/commit\x00"
SCHEDULE_SCHEMA = "verdify-switchback-blinded-schedule-v2"
RECEIPT_SCHEMA = "verdify-switchback-randomization-receipt-v2"
REVEAL_SCHEMA = "verdify-switchback-randomization-reveal-v2"
ALGORITHM_REVISION = "hmac-sha256-rfc8785-v2"
SECRET_BYTES = 32
SCHEDULE_SCHEMA_CONTRACT: dict[str, Any] = {
    "assignment_fields": [
        "assignment_uuid",
        "blinded_label",
        "day_in_pair",
        "local_date",
        "pair_index",
        "utc_end",
        "utc_start",
    ],
    "schema": SCHEDULE_SCHEMA,
    "top_level_fields": [
        "assignments",
        "namespace_uuid",
        "pairs",
        "schema",
        "start_local_date",
        "study_id",
        "timezone",
    ],
}


def schedule_schema_contract_sha256() -> str:
    payload = json.dumps(SCHEDULE_SCHEMA_CONTRACT, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _nfc_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("study_id must be a nonempty string")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise ValueError("study_id must already be Unicode NFC")
    return value.encode("utf-8")


def _secret32(secret: bytes) -> bytes:
    if not isinstance(secret, bytes) or len(secret) != SECRET_BYTES:
        raise ValueError("restricted randomization secret must be exactly 32 bytes")
    return secret


def pair_labels(secret: bytes, study_id: str, pair_index: int) -> Literal["XY", "YX"]:
    """Exact v2 pair domain: DOMAIN || NFC(study_id) || uint32_be(index)."""
    _secret32(secret)
    if not isinstance(pair_index, int) or isinstance(pair_index, bool) or not 0 <= pair_index <= 0xFFFFFFFF:
        raise ValueError("pair_index must be uint32")
    digest = hmac.new(secret, PAIR_DOMAIN + _nfc_bytes(study_id) + struct.pack(">I", pair_index), hashlib.sha256)
    return "XY" if (digest.digest()[0] & 1) == 0 else "YX"


def hidden_mapping(secret: bytes, study_id: str) -> dict[str, str]:
    _secret32(secret)
    digest = hmac.new(secret, MAPPING_DOMAIN + _nfc_bytes(study_id), hashlib.sha256).digest()
    return {"X": "A", "Y": "B"} if (digest[0] & 1) == 0 else {"X": "B", "Y": "A"}


def full_entropy_commitment(study_id: str, schedule_hash: bytes, secret: bytes) -> bytes:
    _secret32(secret)
    if not isinstance(schedule_hash, bytes) or len(schedule_hash) != 32:
        raise ValueError("schedule_hash must be 32 bytes")
    preimage = COMMIT_DOMAIN + _nfc_bytes(study_id) + b"\x00" + schedule_hash + b"\x00" + secret
    return hashlib.sha256(preimage).digest()


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class DesignLock:
    study_id: str
    start_local_date: str
    timezone: str
    pairs: int
    assignment_namespace_uuid: uuid.UUID
    design_lock_sha256: str
    source_git_sha: str
    schedule_schema_sha256: str

    def __post_init__(self) -> None:
        _nfc_bytes(self.study_id)
        parsed = date.fromisoformat(self.start_local_date)
        if parsed.isoformat() != self.start_local_date:
            raise ValueError("start_local_date must be canonical YYYY-MM-DD")
        ZoneInfo(self.timezone)
        if self.pairs < 1:
            raise ValueError("pairs must be positive")
        for name in ("design_lock_sha256", "schedule_schema_sha256"):
            value = getattr(self, name)
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name} must be lowercase SHA-256 hex")
        if self.schedule_schema_sha256 != schedule_schema_contract_sha256():
            raise ValueError("schedule_schema_sha256 does not match the source-locked v2 field contract")
        if not self.source_git_sha:
            raise ValueError("source_git_sha must be frozen")


def blinded_schedule(design: DesignLock, secret: bytes) -> dict[str, Any]:
    """Build the source-locked X/Y schedule; reject any UTC-offset crossing."""
    _secret32(secret)
    tz = ZoneInfo(design.timezone)
    start = date.fromisoformat(design.start_local_date)
    total_days = design.pairs * 2
    midnights = [
        datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=tz) for i in range(total_days + 1)
    ]
    if len({moment.utcoffset() for moment in midnights}) != 1:
        raise ValueError("protocol v2 forbids a UTC-offset crossing in the locked local-day window")
    assignments: list[dict[str, str | int]] = []
    for pair_index in range(design.pairs):
        labels = pair_labels(secret, design.study_id, pair_index)
        for offset in (0, 1):
            day_index = pair_index * 2 + offset
            local_date = (start + timedelta(days=day_index)).isoformat()
            assignments.append(
                {
                    "assignment_uuid": str(
                        assignment_uuid(design.assignment_namespace_uuid, design.study_id, local_date)
                    ),
                    "blinded_label": labels[offset],
                    "day_in_pair": offset + 1,
                    "local_date": local_date,
                    "pair_index": pair_index,
                    "utc_end": _rfc3339(midnights[day_index + 1]),
                    "utc_start": _rfc3339(midnights[day_index]),
                }
            )
    return {
        "assignments": assignments,
        "namespace_uuid": str(design.assignment_namespace_uuid),
        "pairs": design.pairs,
        "schema": SCHEDULE_SCHEMA,
        "start_local_date": design.start_local_date,
        "study_id": design.study_id,
        "timezone": design.timezone,
    }


def canonical_schedule_bytes(schedule: dict[str, Any]) -> bytes:
    if schedule.get("schema") != SCHEDULE_SCHEMA:
        raise ValueError(f"schedule schema must be {SCHEDULE_SCHEMA}")
    if set(schedule) != set(SCHEDULE_SCHEMA_CONTRACT["top_level_fields"]):
        raise ValueError("schedule top-level fields differ from the source-locked contract")
    assignments = schedule.get("assignments")
    if not isinstance(assignments, list) or any(
        not isinstance(row, dict) or set(row) != set(SCHEDULE_SCHEMA_CONTRACT["assignment_fields"])
        for row in assignments
    ):
        raise ValueError("schedule assignment fields differ from the source-locked contract")
    return rfc8785_canonicalize_nfc_ijson(schedule).encode("utf-8")


@dataclass(frozen=True)
class FinalizationReceipt:
    schema: str
    study_id: str
    design_lock_sha256: str
    source_git_sha: str
    algorithm_revision: str
    schedule: dict[str, Any]
    schedule_hash_sha256: str
    mapping_commitment_sha256: str
    finalized_at: str
    no_redraw: bool
    receipt_sha256: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "algorithm_revision": self.algorithm_revision,
            "design_lock_sha256": self.design_lock_sha256,
            "finalized_at": self.finalized_at,
            "mapping_commitment_sha256": self.mapping_commitment_sha256,
            "no_redraw": 1 if self.no_redraw else 0,
            "receipt_sha256": self.receipt_sha256,
            "schedule": self.schedule,
            "schedule_hash_sha256": self.schedule_hash_sha256,
            "schema": self.schema,
            "source_git_sha": self.source_git_sha,
            "study_id": self.study_id,
        }


@dataclass(frozen=True)
class CompletionProof:
    study_id: str
    lifecycle_status: Literal["completed"]
    outcomes_export_sha256: str
    deviations_export_sha256: str
    confirmed_baseline_close_sha256: str

    def __post_init__(self) -> None:
        if self.lifecycle_status != "completed":
            raise ValueError("restricted reveal requires lifecycle_status=completed")
        for field in ("outcomes_export_sha256", "deviations_export_sha256", "confirmed_baseline_close_sha256"):
            value = getattr(self, field)
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{field} must be lowercase SHA-256 hex")


@dataclass(frozen=True)
class RevealReceipt:
    schema: str
    study_id: str
    secret: bytes
    mapping: dict[str, str]
    reproduced_schedule_hash_sha256: str
    reproduced_commitment_sha256: str
    outcomes_export_sha256: str
    deviations_export_sha256: str


@dataclass
class _RestrictedState:
    design: DesignLock
    secret: bytes
    receipt: FinalizationReceipt
    revealed: bool = False


class RestrictedFinalizationStore:
    """Opaque in-process reference store; not a durability/security claim.

    Runtime integration must implement the same insert-once/collision-audit
    behavior inside L3's restricted durable transaction and role boundary.
    Public methods here never return restricted state.
    """

    def __init__(self) -> None:
        self.__states: dict[str, _RestrictedState] = {}
        self.__lock = threading.Lock()
        self.__safe_audit_events: list[tuple[str, str]] = []

    def _state(self, study_id: str) -> _RestrictedState | None:
        return self.__states.get(study_id)

    def _insert_once(self, state: _RestrictedState) -> tuple[_RestrictedState, bool]:
        with self.__lock:
            existing = self.__states.get(state.design.study_id)
            if existing is not None:
                self.__safe_audit_events.append((state.design.study_id, "concurrent_candidate_lost"))
                return existing, False
            self.__states[state.design.study_id] = state
            self.__safe_audit_events.append((state.design.study_id, "secret_generated_and_receipt_accepted"))
            return state, True

    def safe_audit_event_kinds(self) -> tuple[tuple[str, str], ...]:
        """Safe metadata only: event kinds contain no secret or commitment preimage."""
        return tuple(self.__safe_audit_events)


class RandomizationFinalizer:
    """Executable reference finalizer with the production least-input API.

    The algorithm/API are integration-ready; the in-process store is not the
    real persistence or custody boundary.
    """

    def __init__(self, store: RestrictedFinalizationStore | None = None) -> None:
        self._store = store if store is not None else RestrictedFinalizationStore()

    def finalize(self, design: DesignLock) -> FinalizationReceipt:
        existing = self._store._state(design.study_id)
        if existing is not None:
            if existing.design != design:
                raise ValueError("study already finalized under a different immutable design; replacement forbidden")
            return existing.receipt
        tz = ZoneInfo(design.timezone)
        locked_start = datetime.combine(date.fromisoformat(design.start_local_date), datetime.min.time(), tzinfo=tz)
        now = datetime.now(UTC)  # capture exactly once for both the gate and receipt
        if now.astimezone(tz) >= locked_start:
            raise ValueError("locked start was missed; abort this study id/draw instead of shifting the schedule")
        secret = _secret32(secrets.token_bytes(SECRET_BYTES))
        schedule = blinded_schedule(design, secret)
        schedule_hash = hashlib.sha256(canonical_schedule_bytes(schedule)).digest()
        commitment = full_entropy_commitment(design.study_id, schedule_hash, secret)
        finalized_at = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        receipt_body = {
            "algorithm_revision": ALGORITHM_REVISION,
            "design_lock_sha256": design.design_lock_sha256,
            "finalized_at": finalized_at,
            "mapping_commitment_sha256": commitment.hex(),
            "no_redraw": 1,
            "schedule": schedule,
            "schedule_hash_sha256": schedule_hash.hex(),
            "schema": RECEIPT_SCHEMA,
            "source_git_sha": design.source_git_sha,
            "study_id": design.study_id,
        }
        receipt_hash = hashlib.sha256(rfc8785_canonicalize_nfc_ijson(receipt_body).encode("utf-8")).hexdigest()
        candidate = FinalizationReceipt(
            schema=RECEIPT_SCHEMA,
            study_id=design.study_id,
            design_lock_sha256=design.design_lock_sha256,
            source_git_sha=design.source_git_sha,
            algorithm_revision=ALGORITHM_REVISION,
            schedule=schedule,
            schedule_hash_sha256=schedule_hash.hex(),
            mapping_commitment_sha256=commitment.hex(),
            finalized_at=finalized_at,
            no_redraw=True,
            receipt_sha256=receipt_hash,
        )
        accepted, _won = self._store._insert_once(_RestrictedState(design, secret, candidate))
        if accepted.design != design:
            raise ValueError("concurrent finalization collision under a different design")
        return accepted.receipt

    def reveal_after_completion(self, proof: CompletionProof) -> RevealReceipt:
        state = self._store._state(proof.study_id)
        if state is None:
            raise ValueError("study has not been finalized")
        if state.revealed:
            raise ValueError("restricted secret reveal is one-way and may occur only once")
        schedule = blinded_schedule(state.design, state.secret)
        schedule_hash = hashlib.sha256(canonical_schedule_bytes(schedule)).hexdigest()
        commitment = full_entropy_commitment(proof.study_id, bytes.fromhex(schedule_hash), state.secret).hex()
        if schedule_hash != state.receipt.schedule_hash_sha256 or commitment != state.receipt.mapping_commitment_sha256:
            raise RuntimeError("restricted reveal failed schedule/commitment reproduction")
        state.revealed = True
        return RevealReceipt(
            schema=REVEAL_SCHEMA,
            study_id=proof.study_id,
            secret=state.secret,
            mapping=hidden_mapping(state.secret, proof.study_id),
            reproduced_schedule_hash_sha256=schedule_hash,
            reproduced_commitment_sha256=commitment,
            outcomes_export_sha256=proof.outcomes_export_sha256,
            deviations_export_sha256=proof.deviations_export_sha256,
        )


def assert_production_secret_custody_api() -> None:
    """Machine guard: production finalization cannot accept a secret/replacement."""
    parameters = set(inspect.signature(RandomizationFinalizer.finalize).parameters)
    forbidden = {"secret", "mapping_secret", "rng", "random_source", "replace", "redraw"}
    if parameters & forbidden or parameters != {"self", "design"}:
        raise AssertionError(f"production finalizer API expanded: {sorted(parameters)}")
    runtime_names = globals()
    if any(name.startswith("TestingRandomization") for name in runtime_names):
        raise AssertionError("deterministic caller-secret finalizer leaked into the runtime module")


FINALIZATION_ONLY_PATHS = frozenset(
    {
        "randomization.mapping_commitment_sha256",
        "randomization.finalization_receipt_sha256",
        "study.blinded_schedule_artifact",
        "study.blinded_schedule_hash_sha256",
    }
)


def changed_leaf_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    """Return leaf paths changed between design-lock and final protocol objects."""
    if isinstance(before, dict) and isinstance(after, dict):
        paths: set[str] = set()
        for key in set(before) | set(after):
            child = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                paths.add(child)
            else:
                paths |= changed_leaf_paths(before[key], after[key], child)
        return paths
    return set() if before == after else {prefix}


def assert_finalization_only_changes(design_protocol: dict[str, Any], final_protocol: dict[str, Any]) -> None:
    changed = changed_leaf_paths(design_protocol, final_protocol)
    illegal = changed - FINALIZATION_ONLY_PATHS
    if illegal:
        raise ValueError(f"final protocol changed non-finalization design paths: {sorted(illegal)}")
