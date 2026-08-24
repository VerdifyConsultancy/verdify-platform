"""Experiment lifecycle API route tests (#587, audit §8.7 — Lane F tranche 2).

Stubbed-DB route tests following the tests/test_api_db_timeouts.py pattern
(importlib-load api/main.py, fake asyncpg pool — no postgres). Priority is
the BLINDING contract: the status/export response models must be unable to
leak proposal source, component values, reusable content hashes, template
ids, or the X/Y->A/B arm resolution even when the underlying rows carry
them; unblind is a one-way completed-state transition gated on the frozen
export hash; transitions are idempotent and optimistic-concurrency guarded;
all routes fail closed (403) with no token configured.
"""

import asyncio
from importlib import util
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

_SPEC = util.spec_from_file_location("verdify_api_main_experiments", Path(__file__).parents[1] / "api" / "main.py")
assert _SPEC and _SPEC.loader
main = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)

EXP_ID = "11111111-2222-3333-4444-555555555555"
ASG_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FORBIDDEN_MARKERS = (
    # reusable content / activation hashes
    "c0ffee" * 10 + "c0ff",
    "deadbeef" * 8,
    # template id + proposal producer/source
    "99999999-8888-7777-6666-555555555555",
    "proposal",
    "producer",
    "template_id",
    "content_sha256",
    "activation_sha256",
    "physical_arm",
    "canonical_bytes",
    "normalized_value",
)


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def _experiment_row(kind="randomized", status="running", **extra):
    row = {
        "experiment_id": EXP_ID,
        "protocol_version": 1,
        "greenhouse_id": "vallery",
        "kind": kind,
        "status": status,
        "name": "exp-1",
        "timezone": "America/Denver",
        "protocol_ref": None,
        "protocol_sha256": None,
        "beacon_identity": None,
        "beacon_hash": None,
        "schedule_sha256": None,
        "mutable_fields": None,
        "permitted_producers": ["ai", "forecast", "baseline", "guardrail", "operator"],
        "created_at": "2026-08-14T00:00:00+00:00",
        # Forbidden lineage a careless raw-row spread WOULD leak:
        "baseline_content_sha256": FORBIDDEN_MARKERS[0],
        "mapping_commitment_sha256": FORBIDDEN_MARKERS[1],
    }
    row.update(extra)
    return row


def _assignment_row(arm_label="X"):
    return {
        "assignment_id": ASG_ID,
        "arm_label": arm_label,
        "operation_kind": "randomized_day",
        "valid_from": "2026-08-14T00:00:00+00:00",
        "valid_to": "2026-08-15T00:00:00+00:00",
        "status": "active",
        # forbidden extras present in the row on purpose
        "template_id": FORBIDDEN_MARKERS[2],
        "content_sha256": FORBIDDEN_MARKERS[0],
        "producer": "ai",
    }


def _export_record(arm_label="X"):
    return {
        "assignment_id": ASG_ID,
        "arm_label": arm_label,
        "operation_kind": "randomized_day",
        "pair_index": 1,
        "block_index": 2,
        "valid_from": "2026-08-14T00:00:00+00:00",
        "valid_to": "2026-08-15T00:00:00+00:00",
        "assignment_status": "closed",
        "exposure_count": 3,
        "confirmed_exposure_count": 3,
        "exposure_coverage": 0.981,
        "fallback_closures": 0,
        # forbidden extras present in the record on purpose
        "template_id": FORBIDDEN_MARKERS[2],
        "activation_sha256": FORBIDDEN_MARKERS[1],
        "producer": "ai",
    }


class _FakeConn:
    """Dispatches on SQL markers; every query is recorded for assertions."""

    def __init__(
        self,
        experiment=None,
        assignment=None,
        export_records=(),
        resolutions=(),
        unblind_recorded=False,
        transition_error=None,
        create_error=None,
        insert_returns=None,
        device_snapshot=None,
    ):
        self.experiment = experiment
        self.assignment = assignment
        self.export_records = list(export_records)
        self.resolutions = list(resolutions)
        self.unblind_recorded = unblind_recorded
        self.transition_error = transition_error
        self.create_error = create_error
        self.insert_returns = insert_returns
        self.device_snapshot = device_snapshot
        self.queries: list[str] = []
        self.transition_calls: list[tuple] = []
        self.unblind_calls: list[tuple] = []
        self.executed: list[tuple] = []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if "fn_runtime_v1_experiment_transition" in sql:
            if "'locked'" in sql:
                target, expected, actor, note = "locked", args[1], args[2], args[3]
            else:
                target, expected, actor, note = args[1], args[2], args[3], args[4]
            self.transition_calls.append((args[0], target, expected, actor, note))
            if self.transition_error is not None:
                raise self.transition_error
            current = self.experiment["status"]
            if expected is not None and expected != current:
                raise asyncpg.exceptions.SerializationError(f"experiment {args[0]} is {current}, expected {expected}")
            return {"previous_status": current, "status": target, "changed": current != target}
        if "fn_runtime_v1_create_experiment" in sql:
            if self.create_error is not None:
                raise self.create_error
            return self.insert_returns
        if "FROM control_experiments" in sql:
            return self.experiment
        if "FROM control_assignments" in sql and "LIMIT 1" in sql:
            return self.assignment
        if "FROM policy_exposures" in sql:
            return {
                "exposure_count": 3,
                "confirmed": 2,
                "open_count": 1,
                "unconfirmed": 1,
                "missing_coverage": 1,
                "fallback_closures": 1,
                "coverage": 0.876,
            }
        if "FROM policy_delivery_outbox" in sql:
            return {"pending": 2, "failed": 1, "lag_seconds": 42.5}
        if "FROM experiment_events" in sql:
            return {"deviations": 1, "critical": 0}
        if "FROM policy_device_snapshots" in sql:
            return self.device_snapshot
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if "FROM control_assignments a" in sql:
            return self.export_records
        if "fn_runtime_v1_arm_resolutions" in sql:
            return self.resolutions
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        if "fn_runtime_v1_record_unblind" in sql:
            self.unblind_calls.append(args)
            inserted = not self.unblind_recorded
            self.unblind_recorded = True
            return inserted
        if "detail->>'to' = 'unblinded'" in sql:
            return self.unblind_recorded
        if "NOT EXISTS (SELECT 1 FROM policy_exposures" in sql:
            return 2
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql, *args):
        self.queries.append(sql)
        self.executed.append((sql, args))
        if "INSERT INTO experiment_events" in sql:
            self.unblind_recorded = True


AUTH = {"X-Verdify-Experiment-Token": "analyst-token"}
OP_AUTH = {"X-Verdify-Operator-Token": "operator-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VERDIFY_EXPERIMENT_API_TOKEN", "analyst-token")
    monkeypatch.setenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", "operator-token")
    return TestClient(main.app)


def _install(monkeypatch, conn):
    monkeypatch.setattr(main, "pool", _FakePool(conn))
    return conn


def test_runtime_role_cutover_rejects_owner_identity_and_database_url_bypass(monkeypatch):
    monkeypatch.setenv(main.API_RUNTIME_DB_ROLE_REQUIRED_ENV, "1")
    monkeypatch.setenv("DB_USER", "verdify")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="exact API database login"):
        main.get_db_dsn()

    monkeypatch.setenv("DATABASE_URL", "postgresql://verdify:placeholder@db/verdify")
    with pytest.raises(RuntimeError, match="non-runtime login"):
        main.get_db_dsn()


def test_runtime_role_cutover_attests_every_new_api_connection(monkeypatch):
    class Connection:
        def __init__(self, attested):
            self.attested = attested
            self.calls = []

        async def fetchval(self, sql, *args):
            self.calls.append((sql, args))
            return self.attested

        async def execute(self, sql):
            self.calls.append((sql, ()))

    monkeypatch.setenv(main.API_RUNTIME_DB_ROLE_REQUIRED_ENV, "true")
    good = Connection(True)
    asyncio.run(main._init_db_connection(good))
    assert good.calls[0][1] == ()
    assert "current_user = session_user" in good.calls[0][0]
    assert "current_setting('search_path')" in good.calls[0][0]
    assert "fn_runtime_attest_ordinary_login()" in good.calls[0][0]
    assert good.calls[-1][0] == "SET jit = off"

    with pytest.raises(RuntimeError, match="role attestation failed"):
        asyncio.run(main._init_db_connection(Connection(False)))


# ── Auth: fail closed / feature-off parity ────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/experiments"),
        ("post", f"/api/v1/experiments/{EXP_ID}/lock"),
        ("post", f"/api/v1/experiments/{EXP_ID}/unblind"),
        ("get", f"/api/v1/experiments/{EXP_ID}/status"),
        ("get", f"/api/v1/experiments/{EXP_ID}/export"),
        ("get", f"/api/v1/experiments/{EXP_ID}/device-policy"),
        ("post", f"/experiments/{EXP_ID}/lock"),
        ("get", f"/experiments/{EXP_ID}/status"),
    ],
)
def test_routes_fail_closed_with_no_token_configured(monkeypatch, method, path):
    """Feature-off parity: no env token -> 403, and the DB is never touched."""
    monkeypatch.delenv("VERDIFY_EXPERIMENT_API_TOKEN", raising=False)
    monkeypatch.delenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", raising=False)
    conn = _install(monkeypatch, _FakeConn())
    client = TestClient(main.app)
    if method == "post":
        resp = client.post(path, json={}, headers={**AUTH, **OP_AUTH})
    else:
        resp = client.get(path, headers={**AUTH, **OP_AUTH})
    assert resp.status_code == 403
    assert conn.queries == []


def test_wrong_token_is_rejected(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row()))
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/status", headers={"X-Verdify-Experiment-Token": "nope"})
    assert resp.status_code == 403
    assert conn.queries == []


def test_device_policy_rejects_the_analyst_token(monkeypatch, client):
    """Operator surface is SEPARATELY authorized — the experiment token is not enough."""
    _install(monkeypatch, _FakeConn(experiment=_experiment_row()))
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/device-policy", headers=AUTH)
    assert resp.status_code == 403


# ── Blinding: status ──────────────────────────────────────────────────


def test_status_is_blinded_even_when_rows_carry_forbidden_lineage(monkeypatch, client):
    _install(monkeypatch, _FakeConn(experiment=_experiment_row(), assignment=_assignment_row("X")))
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/status", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "experiment_id",
        "kind",
        "status",
        "current_assignment",
        "exposure_coverage_pct",
        "open_exposures",
        "confirmed_exposures",
        "delivery_lag_seconds",
        "pending_deliveries",
        "missing_data",
        "safety",
    }
    assert set(body["current_assignment"]) == {
        "assignment_id",
        "blinded_label",
        "operation_kind",
        "valid_from",
        "valid_to",
        "status",
    }
    assert body["current_assignment"]["blinded_label"] == "X"
    assert body["exposure_coverage_pct"] == pytest.approx(87.6)
    assert body["delivery_lag_seconds"] == pytest.approx(42.5)
    assert body["missing_data"] == {
        "assignments_without_exposure": 2,
        "unconfirmed_exposures": 1,
        "exposures_missing_coverage": 1,
    }
    assert body["safety"]["fallback_closures"] == 1
    lowered = resp.text.lower()
    for marker in FORBIDDEN_MARKERS:
        assert marker.lower() not in lowered, f"blinded status leaked {marker!r}"


def test_status_suppresses_non_randomized_arm_labels(monkeypatch, client):
    """Qualification arm labels ARE the treatment (template kind) — never surfaced."""
    conn = _FakeConn(experiment=_experiment_row(kind="qualification"), assignment=_assignment_row("moderate"))
    conn.assignment["operation_kind"] = "analyzed"
    _install(monkeypatch, conn)
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/status", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["current_assignment"]["blinded_label"] is None
    assert "moderate" not in resp.text


def test_blinded_models_reject_raw_row_spread():
    """extra='forbid' makes a **raw_row construction fail instead of leaking."""
    with pytest.raises(ValidationError):
        main.ExperimentAssignmentBlinded(**_assignment_row("X"))
    with pytest.raises(ValidationError):
        main.ExperimentExportRow(**_export_record("X"))


def test_blinded_label_validator_refuses_physical_labels():
    with pytest.raises(ValidationError):
        main.ExperimentAssignmentBlinded(
            assignment_id=ASG_ID,
            blinded_label="A",
            operation_kind="randomized_day",
            valid_from="2026-08-14T00:00:00+00:00",
            valid_to="2026-08-15T00:00:00+00:00",
            status="active",
        )


# ── Blinding: export + one-way unblind ────────────────────────────────


def test_export_is_blinded_and_holds_back_resolution_until_unblind(monkeypatch, client):
    conn = _FakeConn(
        experiment=_experiment_row(status="running"),
        export_records=[_export_record("X"), _export_record("Y")],
        resolutions=[
            {
                "blinded_label": "X",
                "physical_arm": "A",
                "resolved_at": "2026-08-14T00:00:00+00:00",
                "resolution_source": "unblind-ceremony:1",
            }
        ],
    )
    _install(monkeypatch, conn)
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/export", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unblinded"] is False
    assert body["arm_resolutions"] is None
    assert [r["arm_label"] for r in body["rows"]] == ["X", "Y"]
    assert set(body["rows"][0]) == {
        "assignment_id",
        "arm_label",
        "operation_kind",
        "pair_index",
        "block_index",
        "valid_from",
        "valid_to",
        "assignment_status",
        "exposure_count",
        "confirmed_exposure_count",
        "exposure_coverage_pct",
        "fallback_closures",
    }
    lowered = resp.text.lower()
    for marker in FORBIDDEN_MARKERS:
        assert marker.lower() not in lowered, f"blinded export leaked {marker!r}"


def test_export_drops_non_blinded_labels_for_randomized(monkeypatch, client):
    conn = _FakeConn(experiment=_experiment_row(), export_records=[_export_record("X"), _export_record("A")])
    _install(monkeypatch, conn)
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/export", headers=AUTH)
    assert [r["arm_label"] for r in resp.json()["rows"]] == ["X"]


def test_export_reveals_resolution_only_after_completed_and_unblind(monkeypatch, client):
    conn = _FakeConn(
        experiment=_experiment_row(status="completed"),
        export_records=[_export_record("X")],
        resolutions=[
            {
                "blinded_label": "X",
                "physical_arm": "A",
                "resolved_at": "2026-08-14T00:00:00+00:00",
                "resolution_source": "unblind-ceremony:1",
            }
        ],
        unblind_recorded=True,
    )
    _install(monkeypatch, conn)
    body = client.get(f"/api/v1/experiments/{EXP_ID}/export", headers=AUTH).json()
    assert body["unblinded"] is True
    assert body["arm_resolutions"] == [
        {
            "blinded_label": "X",
            "physical_arm": "A",
            "resolved_at": "2026-08-14T00:00:00Z",
            "resolution_source": "unblind-ceremony:1",
        }
    ]


def test_unblind_rejected_before_completed(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="running")))
    resp = client.post(
        f"/api/v1/experiments/{EXP_ID}/unblind",
        json={"export_sha256": "0" * 64},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]
    assert conn.executed == []


def test_unblind_rejects_a_stale_export_hash(monkeypatch, client):
    conn = _FakeConn(experiment=_experiment_row(status="completed"), export_records=[_export_record("X")])
    _install(monkeypatch, conn)
    resp = client.post(
        f"/api/v1/experiments/{EXP_ID}/unblind",
        json={"export_sha256": "0" * 64},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert conn.executed == []


def test_unblind_happy_path_is_one_way_and_idempotent(monkeypatch, client):
    conn = _FakeConn(experiment=_experiment_row(status="completed"), export_records=[_export_record("X")])
    _install(monkeypatch, conn)
    frozen_export = client.get(f"/api/v1/experiments/{EXP_ID}/export", headers=AUTH).json()
    frozen = frozen_export["export_sha256"]

    first = client.post(
        f"/api/v1/experiments/{EXP_ID}/unblind",
        json={"export_sha256": frozen, "actor": "caller-controlled-label"},
        headers=AUTH,
    )
    assert first.status_code == 200
    assert first.json() == {
        "experiment_id": EXP_ID,
        "unblinded": True,
        "export_sha256": frozen,
        "idempotent": False,
    }
    assert len(conn.unblind_calls) == 1
    assert conn.unblind_calls[0][:3] == (EXP_ID, "api:experiment-unblind", frozen)
    assert conn.unblind_calls[0][3] == main._experiment_export_canonical_json(
        [main.ExperimentExportRow.model_validate(row) for row in frozen_export["rows"]]
    )

    replay = client.post(f"/api/v1/experiments/{EXP_ID}/unblind", json={"export_sha256": frozen}, headers=AUTH)
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert len(conn.unblind_calls) == 2  # wrapper proves the replay atomically


# ── Lifecycle transitions ─────────────────────────────────────────────


def test_transition_calls_the_sql_state_machine(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="draft")))
    resp = client.post(
        f"/api/v1/experiments/{EXP_ID}/lock",
        json={"expected_status": "draft", "actor": "caller-controlled-label"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "experiment_id": EXP_ID,
        "action": "lock",
        "previous_status": "draft",
        "status": "locked",
        "idempotent": False,
        "validated": None,
    }
    assert conn.transition_calls == [(EXP_ID, "locked", "draft", "api:experiment-lock", None)]


def test_transition_is_an_idempotent_noop_when_already_in_target_state(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="paused")))
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/pause", json={}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["idempotent"] is True
    assert conn.transition_calls == [(EXP_ID, "paused", None, "api:experiment-pause", None)]


def test_transition_expected_status_precondition_conflicts(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="running")))
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/abort", json={"expected_status": "paused"}, headers=AUTH)
    assert resp.status_code == 409
    assert conn.transition_calls == [(EXP_ID, "aborted", "paused", "api:experiment-abort", None)]


def test_sql_gate_failures_surface_as_422_with_sql_detail(monkeypatch, client):
    conn = _FakeConn(
        experiment=_experiment_row(status="draft"),
        transition_error=asyncpg.exceptions.RaiseError("lock gate: experiment has no complete baseline template"),
    )
    _install(monkeypatch, conn)
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/lock", json={}, headers=AUTH)
    assert resp.status_code == 422
    assert "lock gate" in resp.json()["detail"]


def test_illegal_sql_transitions_surface_as_409(monkeypatch, client):
    conn = _FakeConn(
        experiment=_experiment_row(status="completed"),
        transition_error=asyncpg.exceptions.RaiseError("illegal experiment transition completed -> armed"),
    )
    _install(monkeypatch, conn)
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/arm", json={}, headers=AUTH)
    assert resp.status_code == 409
    assert "illegal experiment transition" in resp.json()["detail"]


def test_validate_dry_runs_the_lock_gates_without_transitioning(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="draft")))
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/validate", json={}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["validated"] is True
    assert body["status"] == "draft"  # dry-run rolled back — still draft
    # 'locked' is a SQL literal in the dry-run statement; args are (id, actor, note)
    assert conn.transition_calls == [(EXP_ID, "locked", None, "api:experiment-lock", None)]
    assert any("fn_runtime_v1_experiment_transition" in q and "'locked'" in q for q in conn.queries)


def test_rollback_maps_to_the_sql_unlock_edge(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn(experiment=_experiment_row(status="locked")))
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/rollback", json={}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert conn.transition_calls == [(EXP_ID, "draft", None, "api:experiment-rollback", None)]


def test_unknown_action_404s(monkeypatch, client):
    _install(monkeypatch, _FakeConn(experiment=_experiment_row()))
    resp = client.post(f"/api/v1/experiments/{EXP_ID}/detonate", json={}, headers=AUTH)
    assert resp.status_code == 404


def test_unknown_experiment_404s(monkeypatch, client):
    _install(monkeypatch, _FakeConn(experiment=None))
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/status", headers=AUTH)
    assert resp.status_code == 404


def test_malformed_experiment_id_404s_without_touching_the_db(monkeypatch, client):
    conn = _install(monkeypatch, _FakeConn())
    resp = client.get("/api/v1/experiments/not-a-uuid/status", headers=AUTH)
    assert resp.status_code == 404
    assert conn.queries == []


@pytest.mark.parametrize(
    ("method", "path", "headers", "payload"),
    [
        (
            "post",
            f"/api/v1/experiments/{EXP_ID}/unblind",
            AUTH,
            {"export_sha256": main._experiment_export_hash([])},
        ),
        ("post", f"/api/v1/experiments/{EXP_ID}/abort", AUTH, {}),
        ("post", f"/api/v1/experiments/{EXP_ID}/complete", AUTH, {}),
        ("post", f"/api/v1/experiments/{EXP_ID}/validate", AUTH, {}),
        ("get", f"/api/v1/experiments/{EXP_ID}/status", AUTH, None),
        ("get", f"/api/v1/experiments/{EXP_ID}/export", AUTH, None),
        ("get", f"/api/v1/experiments/{EXP_ID}/device-policy", OP_AUTH, None),
    ],
)
def test_legacy_v1_surfaces_reject_protocol_v2_before_evidence_or_mutation(
    monkeypatch,
    client,
    method,
    path,
    headers,
    payload,
):
    """A v2 row can never fall through to the legacy state/export contract."""
    conn = _install(
        monkeypatch,
        _FakeConn(
            experiment=_experiment_row(protocol_version=2, status="completed"),
            export_records=[],
            unblind_recorded=False,
            device_snapshot={"snapshot_id": 7},
        ),
    )

    if method == "post":
        response = client.post(path, json=payload, headers=headers)
    else:
        response = client.get(path, headers=headers)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Protocol-v2 experiments are not available on the legacy v1 experiment surface"
    }
    assert len(conn.queries) == 1
    assert "protocol_version" in conn.queries[0]
    assert "FROM control_experiments WHERE experiment_id" in conn.queries[0]
    assert conn.transition_calls == []
    assert conn.executed == []


# ── Create (draft) ────────────────────────────────────────────────────


def test_create_validates_kind_specific_payload(client):
    resp = client.post(
        "/api/v1/experiments",
        json={"greenhouse_id": "vallery", "kind": "aa", "name": "aa-1", "beacon_hash": "a" * 64},
        headers=AUTH,
    )
    assert resp.status_code == 422
    assert "randomized-only" in resp.text


def test_create_rejects_oversized_mutable_allowlist(client):
    resp = client.post(
        "/api/v1/experiments",
        json={
            "greenhouse_id": "vallery",
            "kind": "randomized",
            "name": "r-1",
            "mutable_fields": [f"f{i}" for i in range(12)],
        },
        headers=AUTH,
    )
    assert resp.status_code == 422
    assert "11-field" in resp.text


def test_create_returns_created_draft(monkeypatch, client):
    conn = _FakeConn(insert_returns=_experiment_row(kind="qualification", status="draft", inserted=True))
    _install(monkeypatch, conn)
    resp = client.post(
        "/api/v1/experiments",
        json={"greenhouse_id": "vallery", "kind": "qualification", "name": "exp-1"},
        headers=AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft"
    assert set(body) == {"experiment_id", "greenhouse_id", "kind", "status", "name", "timezone", "created_at"}
    # even the create echo must not spread commitment-hash columns
    assert FORBIDDEN_MARKERS[0] not in resp.text


def test_create_idempotency_key_replays_the_existing_row(monkeypatch, client):
    conn = _FakeConn(
        insert_returns=_experiment_row(kind="qualification", status="draft", inserted=False),
        experiment=_experiment_row(kind="qualification", status="draft", mapping_commitment_sha256=None),
    )
    _install(monkeypatch, conn)
    resp = client.post(
        "/api/v1/experiments",
        json={"greenhouse_id": "vallery", "kind": "qualification", "name": "exp-1", "experiment_id": EXP_ID},
        headers=AUTH,
    )
    assert resp.status_code == 200  # replay, not a duplicate
    assert resp.json()["experiment_id"] == EXP_ID


def test_create_idempotency_preserves_an_explicit_empty_producer_allowlist(monkeypatch, client):
    conn = _FakeConn(
        insert_returns=_experiment_row(kind="qualification", status="draft", inserted=False),
        experiment=_experiment_row(
            kind="qualification",
            status="draft",
            mapping_commitment_sha256=None,
            permitted_producers=[],
        ),
    )
    _install(monkeypatch, conn)
    response = client.post(
        "/api/v1/experiments",
        json={
            "greenhouse_id": "vallery",
            "kind": "qualification",
            "name": "exp-1",
            "experiment_id": EXP_ID,
            "permitted_producers": [],
        },
        headers=AUTH,
    )
    assert response.status_code == 200


def test_create_idempotency_key_conflicts_on_different_content(monkeypatch, client):
    conn = _FakeConn(
        insert_returns=_experiment_row(kind="qualification", status="draft", inserted=False),
        experiment=_experiment_row(kind="qualification", status="draft", mapping_commitment_sha256=None),
    )
    _install(monkeypatch, conn)
    resp = client.post(
        "/api/v1/experiments",
        json={"greenhouse_id": "other", "kind": "aa", "name": "different", "experiment_id": EXP_ID},
        headers=AUTH,
    )
    assert resp.status_code == 409


def test_create_idempotency_replay_cannot_return_a_protocol_v2_row(monkeypatch, client):
    conn = _install(
        monkeypatch,
        _FakeConn(
            create_error=asyncpg.exceptions.InsufficientPrivilegeError(
                f"ordinary runtime rejects protocol 2 experiment {EXP_ID}"
            ),
            experiment=_experiment_row(protocol_version=2),
        ),
    )
    response = client.post(
        "/api/v1/experiments",
        json={
            "greenhouse_id": "vallery",
            "kind": "randomized",
            "name": "exp-1",
            "experiment_id": EXP_ID,
        },
        headers=AUTH,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Protocol-v2 experiments are not available on the legacy v1 experiment surface"
    }
    assert len(conn.queries) == 1
    assert "fn_runtime_v1_create_experiment" in conn.queries[0]
    assert conn.transition_calls == []
    assert conn.executed == []


# ── Operator device-policy surface ────────────────────────────────────


def test_device_policy_returns_device_confirmed_identity(monkeypatch, client):
    conn = _FakeConn(
        experiment=_experiment_row(),
        device_snapshot={
            "snapshot_id": 7,
            "device_id": "esp32-gh1",
            "greenhouse_id": "vallery",
            "reported_at": "2026-08-14T00:00:00+00:00",
            "schema_revision": "r12",
            "device_generation": 42,
            "assignment_id": ASG_ID,
            "content_sha256": "ab" * 32,
            "activation_sha256": "cd" * 32,
            "valid_from": "2026-08-14T00:00:00+00:00",
            "valid_to": "2026-08-15T00:00:00+00:00",
            "apply_state": "active",
            "firmware_revision": "fw-9",
        },
    )
    _install(monkeypatch, conn)
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/device-policy", headers=OP_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    # Operator/safety surface IS allowed the identity hashes (not blinded).
    assert body["content_sha256"] == "ab" * 32
    assert body["activation_sha256"] == "cd" * 32
    assert body["device_generation"] == 42


def test_device_policy_404s_without_a_snapshot(monkeypatch, client):
    _install(monkeypatch, _FakeConn(experiment=_experiment_row(), device_snapshot=None))
    resp = client.get(f"/api/v1/experiments/{EXP_ID}/device-policy", headers=OP_AUTH)
    assert resp.status_code == 404
