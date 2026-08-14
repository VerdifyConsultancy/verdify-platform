"""policy_arbiter worker (#584 Lane C) — pure-logic tests.

Proves, without a live DB:
- feature-off inertness;
- SHADOW mode: proposals are compiled (content hash recorded on the proposal)
  but fn_admit_policy_vector is NEVER called — shadow never creates outbox rows;
- LIVE mode: the admitted vector carries the byte-exact Lane A canonical
  encoding plus content/activation hashes recomputed independently here,
  including the assignment's §8.9 treatment octets and the expected generation;
- rule violations reject the proposal with a bounded reason;
- the treatment-octet builder for every assignment operation kind.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tasks.policy_arbiter import (  # noqa: E402
    ArbiterReject,
    policy_arbiter_admissions,
    treatment_bytes_for_assignment,
)
from test_experiment_workers import FakeConn, FakePool, ForbiddenPool  # noqa: E402

from verdify_schemas.policy_vector import (  # noqa: E402
    activation_sha256,
    content_sha256,
    encode_policy_vector,
    quantize_policy_values,
    validate_treatment_bytes,
    wire_fields,
)
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION  # noqa: E402

EXPERIMENT_ID = str(uuid.uuid4())
ASSIGNMENT_ID = str(uuid.uuid4())
PROPOSAL_ID = str(uuid.uuid4())
TEMPLATE_ID = str(uuid.uuid4())

REVISIONS = {"schema": "s1", "manifest": "m1", "compiler": "c1", "registry": "r1"}


class FakeRange:
    def __init__(self, lower, upper):
        self.lower = lower
        self.upper = upper


def _run(coro):
    return asyncio.run(coro)


def _default_values() -> dict[str, float | bool]:
    return {d.name: (bool(d.default) if d.wire_kind == "bool" else float(d.default)) for d in wire_fields()}


def _template_rows():
    return [{"field_name": name, "normalized_value": float(value)} for name, value in _default_values().items()]


def _exp_row():
    return {
        "experiment_id": EXPERIMENT_ID,
        "status": "running",
        "kind": "randomized",
        "greenhouse_id": "vallery",
        "schema_revision": REVISIONS["schema"],
        "manifest_revision": REVISIONS["manifest"],
        "compiler_revision": REVISIONS["compiler"],
        "registry_revision": REVISIONS["registry"],
        "mutable_fields": None,
    }


def _assignment_row():
    start = datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
    return {
        "assignment_id": ASSIGNMENT_ID,
        "arm_label": "X",
        "operation_kind": "randomized_day",
        "frozen_strata": None,
        "valid_range": FakeRange(start, start + timedelta(days=1)),
    }


def _proposal_row():
    return {
        "proposal_id": PROPOSAL_ID,
        "producer": "ai",
        "proposed_template_id": TEMPLATE_ID,
        "validity": None,
        "trigger_ref": "receipt:test",
    }


def _conn(template_rows=None, extra=()):
    return FakeConn(
        [
            ("FROM control_experiments", _exp_row()),
            ("FROM control_assignments", _assignment_row()),
            ("FROM policy_proposals", [_proposal_row()]),
            ("FROM policy_proposal_components", []),
            ("FROM policy_template_components", template_rows if template_rows is not None else _template_rows()),
            ("COALESCE(max(device_generation)", 1),
            *extra,
        ]
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("VERDIFY_POLICY_DEVICE_ID", raising=False)


def _enable(monkeypatch, mode):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", mode)
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)


def _expected_artifacts():
    quantized = quantize_policy_values(_default_values())
    vector_bytes = encode_policy_vector(quantized)
    content = content_sha256(vector_bytes, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=REVISIONS)
    assignment = _assignment_row()
    activation = activation_sha256(
        content,
        experiment_id=EXPERIMENT_ID,
        assignment_id=ASSIGNMENT_ID,
        treatment_bytes=bytes([0x01, ord("X")]),
        generation=1,
        valid_from_us=int(assignment["valid_range"].lower.timestamp() * 1_000_000),
        valid_to_us=int(assignment["valid_range"].upper.timestamp() * 1_000_000),
    )
    return vector_bytes, content, activation


# ── Feature-off inertness ───────────────────────────────────────────────────


def test_feature_off_default_env_touches_nothing():
    _run(policy_arbiter_admissions(ForbiddenPool()))


def test_mode_off_with_experiment_id_still_inert(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    _run(policy_arbiter_admissions(ForbiddenPool()))


# ── Shadow: compiled, never an outbox row ───────────────────────────────────


def test_shadow_compiles_but_never_admits(monkeypatch):
    _enable(monkeypatch, "shadow")
    conn = _conn()
    _run(policy_arbiter_admissions(FakePool(conn)))

    assert conn.sql_calls("fn_admit_policy_vector") == [], "shadow must NEVER reach the outbox-creating admission"
    updates = conn.sql_calls("UPDATE policy_proposals")
    assert len(updates) == 1
    _kind, _sql, args = updates[0]
    assert args[1] == "shadow"
    _vector_bytes, content, _activation = _expected_artifacts()
    assert content.hex() in args[2], "shadow state_reason must carry the compiled content hash"
    # The proposal was completed to the full 49 components.
    assert len(conn.sql_calls("INSERT INTO policy_proposal_components")) == 49


# ── Live: byte-exact admission via the SQL function ─────────────────────────


def test_live_admits_with_canonical_bytes_hashes_and_generation(monkeypatch):
    _enable(monkeypatch, "live")
    conn = _conn(extra=[("fn_admit_policy_vector", str(uuid.uuid4()))])
    _run(policy_arbiter_admissions(FakePool(conn)))

    admits = conn.sql_calls("fn_admit_policy_vector")
    assert len(admits) == 1
    args = admits[0][2]
    vector_bytes, content, activation = _expected_artifacts()
    assert args[0] == PROPOSAL_ID
    assert args[1] == "esp32-vallery"  # policy_device_id default
    assert args[3] == "policy_arbiter"
    assert args[4] == vector_bytes
    assert args[5] == content.hex()
    assert args[6] == activation.hex()
    assert args[7] == 1  # expected generation bound into the activation hash
    # Admitted path leaves the proposal state to the SQL function (no reject).
    assert all(call[2][1] != "rejected" for call in conn.sql_calls("UPDATE policy_proposals"))


def test_incomplete_template_rejects_with_bounded_reason(monkeypatch):
    _enable(monkeypatch, "live")
    conn = _conn(template_rows=_template_rows()[:10])
    _run(policy_arbiter_admissions(FakePool(conn)))
    assert conn.sql_calls("fn_admit_policy_vector") == []
    updates = conn.sql_calls("UPDATE policy_proposals")
    assert len(updates) == 1
    assert updates[0][2][1] == "rejected"
    assert "incomplete" in updates[0][2][2]
    assert len(updates[0][2][2]) <= 200


def test_unfrozen_revisions_reject(monkeypatch):
    _enable(monkeypatch, "live")
    exp = _exp_row() | {"registry_revision": None}
    conn = FakeConn(
        [
            ("FROM control_experiments", exp),
            ("FROM control_assignments", _assignment_row()),
            ("FROM policy_proposals", [_proposal_row()]),
        ]
    )
    _run(policy_arbiter_admissions(FakePool(conn)))
    updates = conn.sql_calls("UPDATE policy_proposals")
    assert len(updates) == 1 and updates[0][2][1] == "rejected"
    assert "not frozen" in updates[0][2][2]


# ── Treatment octets (§8.9) ─────────────────────────────────────────────────


def test_randomized_treatment_octets():
    for label, octet in (("X", 0x58), ("Y", 0x59)):
        treatment = treatment_bytes_for_assignment(_assignment_row() | {"arm_label": label})
        assert treatment == bytes([0x01, octet])
        validate_treatment_bytes(treatment)
    with pytest.raises(ArbiterReject):
        treatment_bytes_for_assignment(_assignment_row() | {"arm_label": "A"})


def test_aa_treatment_octets():
    for label, lane in (("lane0", 0x00), ("lane1", 0x01), ("0", 0x00), ("1", 0x01)):
        row = _assignment_row() | {"operation_kind": "aa_lane", "arm_label": label}
        treatment = treatment_bytes_for_assignment(row)
        assert treatment == bytes([0x03, lane])
        validate_treatment_bytes(treatment)
    with pytest.raises(ArbiterReject):
        treatment_bytes_for_assignment(_assignment_row() | {"operation_kind": "aa_lane", "arm_label": "lane2"})


def test_qualification_treatment_octets():
    source, target = uuid.uuid4(), uuid.uuid4()
    row = _assignment_row() | {
        "operation_kind": "analyzed",
        "arm_label": "moderate",
        "frozen_strata": {
            "source_template_id": str(source),
            "target_template_id": str(target),
            "regime": 2,
        },
    }
    treatment = treatment_bytes_for_assignment(row)
    assert treatment == bytes([0x02, 0x01]) + source.bytes + target.bytes + bytes([0x02])
    validate_treatment_bytes(treatment)
    with pytest.raises(ArbiterReject):
        treatment_bytes_for_assignment(row | {"frozen_strata": {}})
