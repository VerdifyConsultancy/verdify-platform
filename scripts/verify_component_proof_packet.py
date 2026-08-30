#!/usr/bin/env python3
"""Validate and seal an exported baseline/aggressive/baseline proof packet.

This tool is deliberately offline and non-actuating.  It consumes evidence
already exported from the immutable v2 ledgers, verifies the full-48 freshness,
lineage, generation, and final fail-closed invariants, then optionally writes a
canonical metadata packet using exclusive creation.  Its SHA is a packet seal,
not a replacement for the database-owned physical proof receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

SCHEMA = "verdify-component-physical-proof-packet-v1"
WIRE_IDS = frozenset((*range(1, 6), *range(7, 50)))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BOUNDARY_CONTRACT = (
    ("baseline_before", "baseline"),
    ("aggressive", "aggressive"),
    ("baseline_after", "baseline"),
)
FORBIDDEN_KEY_FRAGMENTS = (
    "secret",
    "mapping",
    "physical_arm",
    "blinded_label",
    "authorization_token",
)


class ProofPacketError(ValueError):
    """The supplied packet cannot prove the physical sequence."""


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _exact_keys(value: object, expected: set[str], label: str, optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProofPacketError(f"{label} must be an object")
    optional = optional or set()
    actual = set(value)
    missing = expected - actual
    extra = actual - expected - optional
    if missing or extra:
        raise ProofPacketError(f"{label} keys differ: missing={sorted(missing)} extra={sorted(extra)}")
    return value


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProofPacketError(f"{label} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ProofPacketError(f"{label} is not a UUID") from exc
    if str(parsed) != value.lower():
        raise ProofPacketError(f"{label} is not canonical UUID text")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ProofPacketError(f"{label} must be lowercase SHA-256 hex")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProofPacketError(f"{label} must be a positive integer")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProofPacketError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProofPacketError(f"{label} is not an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProofPacketError(f"{label} must carry a UTC offset")
    return parsed.astimezone(UTC)


def _reject_forbidden_keys(value: object, path: str = "packet") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ProofPacketError(f"{path}.{key} is forbidden in a blinded proof packet")
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def validate_packet(packet: object) -> None:
    top = _exact_keys(
        packet,
        {
            "schema",
            "experiment_id",
            "authorization_id",
            "attempt_number",
            "authorized_from",
            "authorized_to",
            "revision_bundle_sha256",
            "lease_generation",
            "writer_generation",
            "connection_generation",
            "proof_receipt_id",
            "proof_receipt_sha256",
            "proof_receipt_recorded_at",
            "boundaries",
            "final_status",
        },
        "packet",
        {"packet_sha256"},
    )
    _reject_forbidden_keys(top)
    if top["schema"] != SCHEMA:
        raise ProofPacketError("packet schema is not current")
    _uuid(top["experiment_id"], "experiment_id")
    _uuid(top["authorization_id"], "authorization_id")
    _positive_int(top["attempt_number"], "attempt_number")
    revision = _sha(top["revision_bundle_sha256"], "revision_bundle_sha256")
    lease = _positive_int(top["lease_generation"], "lease_generation")
    writer = _positive_int(top["writer_generation"], "writer_generation")
    connection = _positive_int(top["connection_generation"], "connection_generation")
    _uuid(top["proof_receipt_id"], "proof_receipt_id")
    _sha(top["proof_receipt_sha256"], "proof_receipt_sha256")
    authorized_from = _timestamp(top["authorized_from"], "authorized_from")
    authorized_to = _timestamp(top["authorized_to"], "authorized_to")
    proof_recorded = _timestamp(top["proof_receipt_recorded_at"], "proof_receipt_recorded_at")
    if authorized_from >= authorized_to:
        raise ProofPacketError("authorization range is empty or reversed")

    boundaries = top["boundaries"]
    if not isinstance(boundaries, list) or len(boundaries) != len(BOUNDARY_CONTRACT):
        raise ProofPacketError("packet must contain exactly three ordered boundaries")
    all_boundary_ids: set[str] = set()
    all_epoch_ids: set[str] = set()
    all_receipt_hashes: set[str] = set()
    boundary_completed_at: list[datetime] = []
    state_hashes: list[str] = []

    for boundary_index, ((expected_stage, expected_profile), raw_boundary) in enumerate(
        zip(BOUNDARY_CONTRACT, boundaries, strict=True)
    ):
        label = f"boundaries[{boundary_index}]"
        boundary = _exact_keys(
            raw_boundary,
            {
                "stage",
                "profile",
                "work_id",
                "bundle_id",
                "bundle_finished_at",
                "expected_state_content_sha256",
                "epochs",
            },
            label,
        )
        if (boundary["stage"], boundary["profile"]) != (expected_stage, expected_profile):
            raise ProofPacketError(f"{label} stage/profile is out of order")
        work_id = _uuid(boundary["work_id"], f"{label}.work_id")
        bundle_id = _uuid(boundary["bundle_id"], f"{label}.bundle_id")
        if work_id in all_boundary_ids or bundle_id in all_boundary_ids or work_id == bundle_id:
            raise ProofPacketError("boundary work and bundle identities must be distinct")
        all_boundary_ids.update((work_id, bundle_id))
        bundle_finished = _timestamp(boundary["bundle_finished_at"], f"{label}.bundle_finished_at")
        expected_hash = _sha(boundary["expected_state_content_sha256"], f"{label}.expected_state_content_sha256")
        state_hashes.append(expected_hash)
        epochs = boundary["epochs"]
        if not isinstance(epochs, list) or len(epochs) != 2:
            raise ProofPacketError(f"{label} must contain exactly two receipt-bound epochs")
        epoch_components: list[dict[int, datetime]] = []
        epoch_completed_at: list[datetime] = []

        for epoch_index, raw_epoch in enumerate(epochs):
            epoch_label = f"{label}.epochs[{epoch_index}]"
            epoch = _exact_keys(
                raw_epoch,
                {
                    "source_epoch_id",
                    "work_id",
                    "bundle_id",
                    "observation_receipt_sha256",
                    "policy_state_content_sha256",
                    "persisted_at",
                    "revision_bundle_sha256",
                    "lease_generation",
                    "writer_generation",
                    "connection_generation",
                    "components",
                },
                epoch_label,
            )
            epoch_id = _uuid(epoch["source_epoch_id"], f"{epoch_label}.source_epoch_id")
            receipt_hash = _sha(epoch["observation_receipt_sha256"], f"{epoch_label}.observation_receipt_sha256")
            if epoch_id in all_epoch_ids or receipt_hash in all_receipt_hashes:
                raise ProofPacketError("source epochs and observation receipts must be globally distinct")
            all_epoch_ids.add(epoch_id)
            all_receipt_hashes.add(receipt_hash)
            if epoch["work_id"] != work_id or epoch["bundle_id"] != bundle_id:
                raise ProofPacketError(f"{epoch_label} is not bound to its exact work/bundle")
            if (
                _sha(epoch["policy_state_content_sha256"], f"{epoch_label}.policy_state_content_sha256")
                != expected_hash
            ):
                raise ProofPacketError(f"{epoch_label} does not match its expected state")
            if _sha(epoch["revision_bundle_sha256"], f"{epoch_label}.revision_bundle_sha256") != revision:
                raise ProofPacketError(f"{epoch_label} revision changed")
            if (
                _positive_int(epoch["lease_generation"], f"{epoch_label}.lease_generation") != lease
                or _positive_int(epoch["writer_generation"], f"{epoch_label}.writer_generation") != writer
                or _positive_int(epoch["connection_generation"], f"{epoch_label}.connection_generation") != connection
            ):
                raise ProofPacketError(f"{epoch_label} generation changed")
            persisted_at = _timestamp(epoch["persisted_at"], f"{epoch_label}.persisted_at")
            components = epoch["components"]
            if not isinstance(components, list) or len(components) != 48:
                raise ProofPacketError(f"{epoch_label} must contain exactly 48 components")
            observed: dict[int, datetime] = {}
            ordered_wire_ids: list[int] = []
            for component_index, raw_component in enumerate(components):
                component_label = f"{epoch_label}.components[{component_index}]"
                component = _exact_keys(raw_component, {"wire_id", "observed_at"}, component_label)
                wire_id = component["wire_id"]
                if isinstance(wire_id, bool) or not isinstance(wire_id, int) or wire_id not in WIRE_IDS:
                    raise ProofPacketError(f"{component_label}.wire_id is outside the exact manifest")
                if wire_id in observed:
                    raise ProofPacketError(f"{epoch_label} repeats wire_id {wire_id}")
                observed[wire_id] = _timestamp(component["observed_at"], f"{component_label}.observed_at")
                ordered_wire_ids.append(wire_id)
            if frozenset(observed) != WIRE_IDS:
                raise ProofPacketError(f"{epoch_label} is not the exact 48-field manifest")
            if ordered_wire_ids != sorted(WIRE_IDS):
                raise ProofPacketError(f"{epoch_label} is not in canonical wire order")
            first_observed, last_observed = min(observed.values()), max(observed.values())
            if first_observed <= bundle_finished:
                raise ProofPacketError(f"{epoch_label} includes a pre-completion/cached observation")
            if (last_observed - first_observed).total_seconds() > 60:
                raise ProofPacketError(f"{epoch_label} exceeds bounded component skew")
            if persisted_at < last_observed or (persisted_at - last_observed).total_seconds() > 90:
                raise ProofPacketError(f"{epoch_label} is stale or persisted before observation")
            if (
                not authorized_from <= first_observed < authorized_to
                or not authorized_from < persisted_at <= authorized_to
            ):
                raise ProofPacketError(f"{epoch_label} falls outside the attended authorization")
            epoch_components.append(observed)
            epoch_completed_at.append(last_observed)

        if (epoch_completed_at[1] - epoch_completed_at[0]).total_seconds() < 30:
            raise ProofPacketError(f"{label} epochs are less than 30 seconds apart")
        if any(epoch_components[1][wire_id] <= epoch_components[0][wire_id] for wire_id in WIRE_IDS):
            raise ProofPacketError(f"{label} second epoch does not advance every component")
        boundary_completed_at.append(epoch_completed_at[1])

    if not boundary_completed_at[0] < boundary_completed_at[1] < boundary_completed_at[2]:
        raise ProofPacketError("proof boundaries are not strictly ordered")
    if state_hashes[0] != state_hashes[2] or state_hashes[1] == state_hashes[0]:
        raise ProofPacketError("proof states are not baseline/aggressive/baseline")
    if proof_recorded < boundary_completed_at[2] or proof_recorded > authorized_to:
        raise ProofPacketError("proof receipt was not sealed after recovery inside authorization")

    final_status = _exact_keys(
        top["final_status"],
        {
            "lifecycle_status",
            "execution_phase",
            "admission_state",
            "component_enabled",
            "open_exposure_count",
            "baseline_confirmed",
        },
        "final_status",
    )
    if final_status != {
        "lifecycle_status": "draft",
        "execution_phase": "shadow",
        "admission_state": "closed",
        "component_enabled": False,
        "open_exposure_count": 0,
        "baseline_confirmed": True,
    }:
        raise ProofPacketError("final status is not baseline-confirmed, shadow, closed, and exposure-free")

    if "packet_sha256" in top:
        supplied = _sha(top["packet_sha256"], "packet_sha256")
        unsigned = deepcopy(top)
        unsigned.pop("packet_sha256")
        actual = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        if supplied != actual:
            raise ProofPacketError("packet_sha256 does not match canonical packet bytes")


def seal_packet(packet: object) -> dict[str, Any]:
    validate_packet(packet)
    sealed = deepcopy(packet)
    assert isinstance(sealed, dict)
    sealed.pop("packet_sha256", None)
    sealed["packet_sha256"] = hashlib.sha256(canonical_json_bytes(sealed)).hexdigest()
    validate_packet(sealed)
    return sealed


def write_exclusive(packet: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.is_symlink():
        raise ProofPacketError("output path must not be a symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError as exc:
        raise ProofPacketError("output already exists; proof packets are immutable") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(canonical_json_bytes(packet))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="exported JSON evidence packet")
    parser.add_argument("--output", type=Path, help="exclusive-create path for the sealed canonical packet")
    args = parser.parse_args(argv)
    try:
        packet = json.loads(args.input.read_text())
        sealed = seal_packet(packet)
        if args.output is not None:
            write_exclusive(sealed, args.output)
        print(
            f"status=pass schema={SCHEMA} packet_sha256={sealed['packet_sha256']} "
            f"proof_receipt_id={sealed['proof_receipt_id']}"
        )
    except (OSError, json.JSONDecodeError, ProofPacketError) as exc:
        print(f"status=fail reason={type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
