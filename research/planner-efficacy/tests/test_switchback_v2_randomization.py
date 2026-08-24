from __future__ import annotations

import copy
import hashlib
import hmac
import inspect
import json
import struct
import sys
import threading
import uuid
from pathlib import Path

import pytest
import yaml

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR))

from switchback import v2_randomization as randomization

SECRET = bytes(range(32))
STUDY_ID = "verdify-v2-golden"
NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _design(*, study_id: str = STUDY_ID, start: str = "2099-09-01", pairs: int = 2) -> randomization.DesignLock:
    return randomization.DesignLock(
        study_id=study_id,
        start_local_date=start,
        timezone="America/Denver",
        pairs=pairs,
        assignment_namespace_uuid=NAMESPACE,
        design_lock_sha256="11" * 32,
        source_git_sha="8f9e011b8e186c3b4e735130d837eefe9a079b12",
        schedule_schema_sha256=randomization.schedule_schema_contract_sha256(),
    )


def test_v2_domains_are_byte_exact_and_have_no_public_beacon() -> None:
    expected = []
    for index in range(3):
        digest = hmac.new(
            SECRET,
            b"verdify-switchback-v2/pair\0" + STUDY_ID.encode() + struct.pack(">I", index),
            hashlib.sha256,
        ).digest()
        expected.append("XY" if digest[0] & 1 == 0 else "YX")
    assert expected == ["XY", "XY", "YX"]
    assert [randomization.pair_labels(SECRET, STUDY_ID, index) for index in range(3)] == expected
    assert randomization.hidden_mapping(SECRET, STUDY_ID) == {"X": "B", "Y": "A"}
    assert "beacon" not in inspect.signature(randomization.blinded_schedule).parameters
    assert "beacon" not in inspect.signature(randomization.RandomizationFinalizer.finalize).parameters


def test_source_locked_canonical_schedule_golden() -> None:
    design = _design(start="2026-09-01")
    schedule = randomization.blinded_schedule(design, SECRET)
    canonical = randomization.canonical_schedule_bytes(schedule)
    assert hashlib.sha256(canonical).hexdigest() == "d17085c263610b74028a1bab6c653173055b1f05923e2bd515ab34d2bdd87bf7"
    assert randomization.full_entropy_commitment(STUDY_ID, hashlib.sha256(canonical).digest(), SECRET).hex() == (
        "253182212ee42483a7658b8f8a12fd5056f2c4244e72a7d576f7fe49ac8a673e"
    )
    assert schedule["assignments"][0] == {
        "assignment_uuid": "386e5c50-b864-5021-b829-e918ac5148be",
        "blinded_label": "X",
        "day_in_pair": 1,
        "local_date": "2026-09-01",
        "pair_index": 0,
        "utc_end": "2026-09-02T06:00:00Z",
        "utc_start": "2026-09-01T06:00:00Z",
    }
    assert set(schedule) == set(randomization.SCHEDULE_SCHEMA_CONTRACT["top_level_fields"])
    assert set(schedule["assignments"][0]) == set(randomization.SCHEDULE_SCHEMA_CONTRACT["assignment_fields"])
    with pytest.raises(ValueError, match="source-locked"):
        randomization.DesignLock(**{**design.__dict__, "schedule_schema_sha256": "22" * 32})


@pytest.mark.parametrize("bad_sha", ["8f9e011", "8F9E011B8E186C3B4E735130D837EEFE9A079B12", "a" * 39, "a" * 41])
def test_design_lock_requires_exact_lowercase_40_hex_source_sha(bad_sha: str) -> None:
    design = _design()
    with pytest.raises(ValueError, match="lowercase 40-hex"):
        randomization.DesignLock(**{**design.__dict__, "source_git_sha": bad_sha})


@pytest.mark.parametrize("bad_pairs", [True, 1.5, 0, -1])
def test_design_lock_rejects_noninteger_or_nonpositive_pairs_before_draw(bad_pairs: object) -> None:
    design = _design()
    with pytest.raises(ValueError, match="positive exact integer"):
        randomization.DesignLock(**{**design.__dict__, "pairs": bad_pairs})  # type: ignore[arg-type]


def test_design_lock_requires_exact_uuid_namespace() -> None:
    design = _design()
    with pytest.raises(TypeError, match="exact UUID"):
        randomization.DesignLock(  # type: ignore[arg-type]
            **{**design.__dict__, "assignment_namespace_uuid": str(design.assignment_namespace_uuid)}
        )


def test_checked_in_schema_golden_and_protocol_template_share_exact_contract() -> None:
    protocols = MODULE_DIR / "protocols"
    schema = json.loads((protocols / "blinded-schedule-v2.schema.json").read_text())
    design_schema = json.loads((protocols / "design-lock-v2.schema.json").read_text())
    golden = json.loads((protocols / "blinded-schedule-v2.golden.json").read_text())
    template = yaml.safe_load((protocols / "planner-switchback-v2.template.yaml").read_text())
    contract_hash = randomization.schedule_schema_contract_sha256()
    schema_projection = dict(schema)
    schema_projection.pop("x-verdify-field-contract-sha256")
    expected_hash = hashlib.sha256(
        json.dumps(schema_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert contract_hash == expected_hash
    assert schema["x-verdify-field-contract-sha256"] == contract_hash
    assert golden["schedule_schema_contract_sha256"] == contract_hash
    assert template["randomization"]["canonical_schedule_schema_sha256"] == contract_hash
    assert design_schema["properties"]["source_git_sha"]["pattern"] == "^[0-9a-f]{40}$"
    canonical = bytes.fromhex(golden["canonical_schedule_utf8_hex"])
    assert canonical == randomization.canonical_schedule_bytes(golden["schedule"])
    assert hashlib.sha256(canonical).hexdigest() == golden["canonical_schedule_sha256"]


def test_dst_crossing_and_missed_start_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="UTC-offset crossing"):
        randomization.blinded_schedule(_design(start="2026-10-15", pairs=15), SECRET)
    monkeypatch.setattr(randomization.secrets, "token_bytes", lambda length: SECRET)
    with pytest.raises(ValueError, match="missed"):
        randomization.RandomizationFinalizer().finalize(_design(start="2020-01-01"))


def test_production_api_has_no_caller_secret_or_deterministic_runtime_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    randomization.assert_production_secret_custody_api()
    assert set(inspect.signature(randomization.RandomizationFinalizer.finalize).parameters) == {"self", "design"}
    assert not any(name.startswith("TestingRandomization") for name in vars(randomization))
    finalizer = randomization.RandomizationFinalizer()
    with pytest.raises(TypeError):
        finalizer.finalize(_design(), secret=SECRET)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        randomization.RandomizationFinalizer(secret=SECRET)  # type: ignore[call-arg]
    monkeypatch.setattr(randomization.secrets, "token_bytes", lambda length: SECRET)
    receipt = finalizer.finalize(_design())
    rendered = repr(receipt.public_dict())
    assert SECRET.hex() not in rendered


def test_idempotent_restart_returns_existing_and_replacement_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(randomization.secrets, "token_bytes", lambda length: SECRET)
    store = randomization.RestrictedFinalizationStore()
    first = randomization.RandomizationFinalizer(store).finalize(_design())
    retry = randomization.RandomizationFinalizer(store).finalize(_design())
    assert retry is first
    changed = _design(pairs=3)
    with pytest.raises(ValueError, match="replacement forbidden"):
        randomization.RandomizationFinalizer(store).finalize(changed)


def test_receipt_schedule_is_defensive_and_retry_preserves_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(randomization.secrets, "token_bytes", lambda length: SECRET)
    store = randomization.RestrictedFinalizationStore()
    first = randomization.RandomizationFinalizer(store).finalize(_design())
    original = first.schedule
    original_hash = hashlib.sha256(randomization.canonical_schedule_bytes(original)).hexdigest()
    assert original_hash == first.schedule_hash_sha256

    first.schedule["assignments"][0]["blinded_label"] = "Y"
    first.public_dict()["schedule"]["assignments"][0]["blinded_label"] = "Y"
    retry = randomization.RandomizationFinalizer(store).finalize(_design())

    assert retry.schedule == original
    assert hashlib.sha256(randomization.canonical_schedule_bytes(retry.schedule)).hexdigest() == original_hash


def test_concurrent_candidate_loss_returns_winner_and_audits_safe_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2)

    def deterministic_test_rng(length: int) -> bytes:
        assert length == 32
        barrier.wait(timeout=5)
        return SECRET

    monkeypatch.setattr(randomization.secrets, "token_bytes", deterministic_test_rng)
    store = randomization.RestrictedFinalizationStore()
    receipts: list[randomization.FinalizationReceipt] = []

    def finalize() -> None:
        receipts.append(randomization.RandomizationFinalizer(store).finalize(_design()))

    threads = [threading.Thread(target=finalize) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(receipts) == 2 and receipts[0] == receipts[1]
    kinds = [kind for _study, kind in store.safe_audit_event_kinds()]
    assert kinds.count("secret_generated_and_receipt_accepted") == 1
    assert kinds.count("concurrent_candidate_lost") == 1


def test_reveal_only_after_completion_reproduces_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(randomization.secrets, "token_bytes", lambda length: SECRET)
    finalizer = randomization.RandomizationFinalizer()
    receipt = finalizer.finalize(_design())
    proof = randomization.CompletionProof(
        study_id=STUDY_ID,
        lifecycle_status="completed",
        outcomes_export_sha256="33" * 32,
        deviations_export_sha256="44" * 32,
        confirmed_baseline_close_sha256="55" * 32,
    )
    forged = randomization.CompletionProof(
        study_id=STUDY_ID,
        lifecycle_status="completed",
        outcomes_export_sha256="33" * 32,
        deviations_export_sha256="44" * 32,
        confirmed_baseline_close_sha256="55" * 32,
    )
    object.__setattr__(forged, "lifecycle_status", "running")
    with pytest.raises(ValueError, match="lifecycle_status=completed"):
        finalizer.reveal_after_completion(forged)
    reveal = finalizer.reveal_after_completion(proof)
    assert reveal.reproduced_schedule_hash_sha256 == receipt.schedule_hash_sha256
    assert reveal.reproduced_commitment_sha256 == receipt.mapping_commitment_sha256
    assert reveal.mapping == {"X": "B", "Y": "A"}
    with pytest.raises(ValueError, match="only once"):
        finalizer.reveal_after_completion(proof)
    with pytest.raises(ValueError, match="lifecycle_status=completed"):
        randomization.CompletionProof(
            study_id=STUDY_ID,
            lifecycle_status="running",  # type: ignore[arg-type]
            outcomes_export_sha256="33" * 32,
            deviations_export_sha256="44" * 32,
            confirmed_baseline_close_sha256="55" * 32,
        )


def test_design_lock_finalization_allowlist() -> None:
    protocol_path = MODULE_DIR / "protocols/planner-switchback-v2.template.yaml"
    before = yaml.safe_load(protocol_path.read_text())
    after = copy.deepcopy(before)
    after["study"]["blinded_schedule_artifact"] = "research/planner-efficacy/protocols/blinded-schedule-v2.json"
    after["study"]["blinded_schedule_hash_sha256"] = "22" * 32
    after["randomization"]["mapping_commitment_sha256"] = "33" * 32
    after["randomization"]["finalization_receipt_sha256"] = "44" * 32
    randomization.assert_finalization_only_changes(before, after)
    after["study"]["pairs_target"] += 1
    with pytest.raises(ValueError, match="non-finalization"):
        randomization.assert_finalization_only_changes(before, after)

    nested = copy.deepcopy(before)
    nested["study"]["blinded_schedule_artifact"] = {"secret": "forbidden", "mapping": {"X": "A"}}
    nested["study"]["blinded_schedule_hash_sha256"] = "22" * 32
    nested["randomization"]["mapping_commitment_sha256"] = "33" * 32
    nested["randomization"]["finalization_receipt_sha256"] = "44" * 32
    with pytest.raises(ValueError, match="safe committed JSON path"):
        randomization.assert_finalization_only_changes(before, nested)
