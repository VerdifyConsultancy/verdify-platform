"""Fresh full-48 physical-proof packet failure fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify_component_proof_packet.py"
SPEC = importlib.util.spec_from_file_location("verify_component_proof_packet", SCRIPT)
assert SPEC and SPEC.loader
proof = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proof
SPEC.loader.exec_module(proof)

START = datetime(2026, 8, 29, 18, 0, tzinfo=UTC)


def _uuid(value: int) -> str:
    return str(UUID(int=value))


def _ts(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _packet() -> dict[str, object]:
    boundaries = []
    stages = (
        ("baseline_before", "baseline", "1" * 64),
        ("aggressive", "aggressive", "2" * 64),
        ("baseline_after", "baseline", "1" * 64),
    )
    identity = 100
    for boundary_index, (stage, profile, state_hash) in enumerate(stages):
        bundle_finished = START + timedelta(seconds=boundary_index * 120)
        identity += 1
        work_id = _uuid(identity)
        identity += 1
        bundle_id = _uuid(identity)
        epochs = []
        for epoch_index in range(2):
            observed_at = bundle_finished + timedelta(seconds=5 + 31 * epoch_index)
            identity += 1
            epochs.append(
                {
                    "source_epoch_id": _uuid(identity),
                    "work_id": work_id,
                    "bundle_id": bundle_id,
                    "observation_receipt_sha256": f"{boundary_index + 3}{epoch_index}" * 32,
                    "policy_state_content_sha256": state_hash,
                    "persisted_at": _ts(observed_at + timedelta(seconds=2)),
                    "revision_bundle_sha256": "a" * 64,
                    "lease_generation": 4,
                    "writer_generation": 8,
                    "connection_generation": 12,
                    "components": [
                        {"wire_id": wire_id, "observed_at": _ts(observed_at)} for wire_id in sorted(proof.WIRE_IDS)
                    ],
                }
            )
        boundaries.append(
            {
                "stage": stage,
                "profile": profile,
                "work_id": work_id,
                "bundle_id": bundle_id,
                "bundle_finished_at": _ts(bundle_finished),
                "expected_state_content_sha256": state_hash,
                "epochs": epochs,
            }
        )
    return {
        "schema": proof.SCHEMA,
        "experiment_id": "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "authorization_id": _uuid(1),
        "attempt_number": 9,
        "authorized_from": _ts(START - timedelta(minutes=1)),
        "authorized_to": _ts(START + timedelta(minutes=6)),
        "revision_bundle_sha256": "a" * 64,
        "lease_generation": 4,
        "writer_generation": 8,
        "connection_generation": 12,
        "proof_receipt_id": _uuid(2),
        "proof_receipt_sha256": "f" * 64,
        "proof_receipt_recorded_at": _ts(START + timedelta(seconds=278)),
        "boundaries": boundaries,
        "final_status": {
            "lifecycle_status": "draft",
            "execution_phase": "shadow",
            "admission_state": "closed",
            "component_enabled": False,
            "open_exposure_count": 0,
            "baseline_confirmed": True,
        },
    }


def test_fresh_full48_packet_seals_deterministically_and_keeps_db_receipt_distinct() -> None:
    first = proof.seal_packet(_packet())
    second = proof.seal_packet(_packet())

    assert first == second
    assert first["packet_sha256"] == second["packet_sha256"]
    assert first["packet_sha256"] != first["proof_receipt_sha256"]
    unsigned = deepcopy(first)
    seal = unsigned.pop("packet_sha256")
    assert hashlib.sha256(proof.canonical_json_bytes(unsigned)).hexdigest() == seal


@pytest.mark.parametrize(
    ("boundary_index", "epoch_index", "wire_id"),
    [
        (boundary_index, epoch_index, wire_id)
        for boundary_index in range(3)
        for epoch_index in range(2)
        for wire_id in sorted(proof.WIRE_IDS)
    ],
)
def test_every_missing_full48_component_fails(boundary_index: int, epoch_index: int, wire_id: int) -> None:
    packet = _packet()
    epoch = packet["boundaries"][boundary_index]["epochs"][epoch_index]
    epoch["components"] = [component for component in epoch["components"] if component["wire_id"] != wire_id]

    with pytest.raises(proof.ProofPacketError, match="exactly 48 components"):
        proof.validate_packet(packet)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda packet: packet["boundaries"][0]["epochs"][0].update(persisted_at=_ts(START + timedelta(seconds=96))),
            "stale",
        ),
        (
            lambda packet: packet["boundaries"][1]["epochs"][0].update(connection_generation=13),
            "generation changed",
        ),
        (
            lambda packet: packet["boundaries"][2]["epochs"][1].update(
                source_epoch_id=packet["boundaries"][2]["epochs"][0]["source_epoch_id"]
            ),
            "globally distinct",
        ),
        (
            lambda packet: packet["boundaries"][0]["epochs"][1]["components"][0].update(
                observed_at=packet["boundaries"][0]["epochs"][0]["components"][0]["observed_at"]
            ),
            "does not advance every component",
        ),
        (
            lambda packet: (
                packet["boundaries"][1].update(expected_state_content_sha256="1" * 64),
                [epoch.update(policy_state_content_sha256="1" * 64) for epoch in packet["boundaries"][1]["epochs"]],
            ),
            "baseline/aggressive/baseline",
        ),
        (
            lambda packet: packet["final_status"].update(component_enabled=True),
            "final status",
        ),
    ],
)
def test_receipt_freshness_lineage_generation_and_final_state_fail_closed(mutate, message: str) -> None:
    packet = _packet()
    mutate(packet)
    with pytest.raises(proof.ProofPacketError, match=message):
        proof.validate_packet(packet)


def test_packet_rejects_secret_or_mapping_material() -> None:
    packet = _packet()
    packet["randomization_secret"] = "forbidden"
    with pytest.raises(proof.ProofPacketError, match="keys differ|forbidden"):
        proof.validate_packet(packet)


def test_packet_rejects_reordered_full48_manifest() -> None:
    packet = _packet()
    components = packet["boundaries"][0]["epochs"][0]["components"]
    components[0], components[1] = components[1], components[0]
    with pytest.raises(proof.ProofPacketError, match="canonical wire order"):
        proof.validate_packet(packet)


def test_exclusive_output_refuses_overwrite_and_round_trips(tmp_path: Path) -> None:
    output = tmp_path / "private" / "proof.json"
    sealed = proof.seal_packet(_packet())
    proof.write_exclusive(sealed, output)
    assert json.loads(output.read_text()) == sealed
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(proof.ProofPacketError, match="already exists"):
        proof.write_exclusive(sealed, output)
