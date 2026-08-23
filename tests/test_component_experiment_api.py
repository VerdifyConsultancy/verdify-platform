"""Confirmed-component v2 operator status API (#587).

All rows are fakes: the suite proves authorization, generic phase/work fields,
environment fail-closed behavior, and non-disclosure without a database or
device/network call.
"""

from importlib import util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

_SPEC = util.spec_from_file_location(
    "verdify_api_main_component_experiment",
    Path(__file__).parents[1] / "api" / "main.py",
)
assert _SPEC and _SPEC.loader
main = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)

EXP_ID = "11111111-2222-3333-4444-555555555555"
WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ASSIGNMENT_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
OP_AUTH = {"X-Verdify-Operator-Token": "operator-token"}


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def _experiment(**overrides):
    row = {
        "experiment_id": EXP_ID,
        "kind": "randomized",
        "status": "draft",
        "protocol_version": 2,
        "transport_kind": "confirmed_component",
        "execution_phase": "shadow",
        "admission_state": "closed",
        "component_enabled": False,
        "lease_generation": 7,
        "revision_bundle_sha256": "11" * 32,
        "firmware_revision": "fw-1",
        "config_revision": "cfg-1",
        "registry_revision": "reg-1",
        "grid_revision": "grid-1",
        # A careless raw-row spread would leak these.
        "randomization_secret": "forbidden-secret",
        "mapping": {"X": "B"},
        "target_profile": "aggressive",
    }
    row.update(overrides)
    return row


def _work(operation_kind="preview", **overrides):
    row = {
        "work_id": WORK_ID,
        "assignment_id": ASSIGNMENT_ID,
        "blinded_label": "X",
        "execution_phase": "shadow",
        "operation_kind": operation_kind,
        "valid_from": "2026-08-23T20:00:00+00:00",
        "valid_to": "2026-08-23T21:00:00+00:00",
        "expires_at": "2026-08-23T21:00:00+00:00",
        "expired": False,
        "target_profile": "aggressive",
        "target_state_content_sha256": "55" * 32,
    }
    row.update(overrides)
    return row


class _Conn:
    def __init__(self, *, experiment=None, work=None, approval=None, receipt=None, exposures=0):
        self.experiment = experiment
        self.work = work
        self.approval = approval
        self.receipt = receipt
        self.exposures = exposures
        self.queries: list[str] = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if "FROM control_experiments" in sql:
            return self.experiment
        if "FROM experiment_v2_work w" in sql:
            return self.work
        if "FROM experiment_v2_approvals" in sql:
            return self.approval
        if "FROM experiment_v2_observation_receipts" in sql:
            return self.receipt
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        if "FROM experiment_v2_exposures" in sql:
            return self.exposures
        raise AssertionError(f"unexpected fetchval: {sql}")


def _install(monkeypatch, conn):
    monkeypatch.setattr(main, "pool", _Pool(conn))
    return conn


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", "operator-token")
    monkeypatch.delenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", raising=False)
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    return TestClient(main.app)


def test_component_status_fails_closed_before_db(monkeypatch):
    monkeypatch.delenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", raising=False)
    conn = _install(monkeypatch, _Conn(experiment=_experiment()))
    response = TestClient(main.app).get(
        f"/api/v1/experiments/{EXP_ID}/component-status",
        headers=OP_AUTH,
    )
    assert response.status_code == 403
    assert conn.queries == []


def test_component_status_reports_safe_default_off(monkeypatch, client):
    _install(monkeypatch, _Conn(experiment=_experiment(), work=_work()))
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["component_capability_mode"] == "off"
    assert body["environment_admissible"] is False
    assert body["environment_gate_reason"] == "component_capability_off"
    assert body["execution_phase"] == "shadow"
    assert body["admission_state"] == "closed"
    assert body["current_work"]["operation_kind"] == "preview"
    # Preview/readiness work cannot disclose randomized assignment identity.
    assert body["current_work"]["assignment_id"] is None
    assert body["current_work"]["blinded_label"] is None


def test_enabled_component_requires_vector_exactly_off(monkeypatch, client):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "shadow")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXP_ID)
    _install(monkeypatch, _Conn(experiment=_experiment(component_enabled=True)))
    body = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH).json()
    assert body["environment_admissible"] is False
    assert body["environment_gate_reason"] == "generalized_vector_mode_not_exactly_off"


def test_randomized_assignment_exposes_only_opaque_id_and_xy(monkeypatch, client):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXP_ID)
    conn = _install(
        monkeypatch,
        _Conn(
            experiment=_experiment(
                status="running",
                execution_phase="randomized",
                admission_state="open",
                component_enabled=True,
            ),
            work=_work(operation_kind="assignment", execution_phase="randomized"),
            approval={"scoped_probe": True, "combined_physical": True, "randomized_day_1": True},
            receipt={
                "policy_state_content_sha256": "22" * 32,
                "observation_receipt_sha256": "33" * 32,
                "persisted_at": "2026-08-23T20:30:00+00:00",
            },
            exposures=1,
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["environment_admissible"] is True
    assert body["current_work"]["assignment_id"] == ASSIGNMENT_ID
    assert body["current_work"]["blinded_label"] == "X"
    assert body["approvals"] == {
        "scoped_probe": True,
        "combined_physical": True,
        "randomized_day_1": True,
    }
    assert body["state_identity"] == {
        "policy_state_content_sha256": "22" * 32,
        "observation_receipt_sha256": "33" * 32,
        "receipt_persisted_at": "2026-08-23T20:30:00Z",
        "identity_source": "server_derived",
        "device_echoed": False,
    }
    assert body["open_exposures"] == 1
    lowered = response.text.lower()
    for forbidden in ("forbidden-secret", "aggressive", '"mapping"', '"target_profile"'):
        assert forbidden not in lowered
    assert all("secret" not in query.lower() for query in conn.queries)
    assert all("randomization" not in query.lower() for query in conn.queries)
    assert all("outcome" not in query.lower() for query in conn.queries)


def test_v1_or_malformed_id_is_not_a_component_status(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(experiment=_experiment(protocol_version=1)))
    assert client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH).status_code == 404
    query_count = len(conn.queries)
    assert client.get("/api/v1/experiments/not-a-uuid/component-status", headers=OP_AUTH).status_code == 404
    assert len(conn.queries) == query_count


def test_component_work_model_rejects_cross_phase_identity():
    with pytest.raises(ValidationError):
        main.ComponentExperimentWorkStatus(
            work_id=WORK_ID,
            execution_phase="commissioning",
            operation_kind="commissioning_probe",
            valid_from="2026-08-23T20:00:00+00:00",
            valid_to="2026-08-23T21:00:00+00:00",
            expires_at="2026-08-23T21:00:00+00:00",
            expired=False,
            assignment_id=ASSIGNMENT_ID,
            blinded_label="X",
        )
