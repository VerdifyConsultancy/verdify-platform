"""Confirmed-component v2 operator status API (#587).

All rows are fakes: the suite proves authorization, generic phase/work fields,
environment fail-closed behavior, and non-disclosure without a database or
device/network call.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from importlib import util
from pathlib import Path

import pytest
import yaml
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
API_AUTH = {"X-Verdify-Experiment-Token": "lifecycle-token"}
RESOLVED_AT = datetime(2026, 8, 23, 20, 30, tzinfo=UTC)


def _attestation(**overrides):
    row = {
        "current_user_name": main.EXPERIMENT_LIFECYCLE_DB_LOGIN,
        "session_user_name": main.EXPERIMENT_LIFECYCLE_DB_LOGIN,
        "session_user_matches": True,
        "duty_member": True,
        "duty_membership_non_admin": True,
        "login_role_safe": True,
        "is_superuser": False,
        "is_database_owner": False,
        "has_elevated_role_attributes": False,
        "duty_role_safe": True,
        "has_other_role_membership": False,
        "has_unexpected_duty_member": False,
        "has_managed_object_ownership": False,
        "schema_usage": True,
        "has_public_schema_create": False,
        "has_protected_relation_privilege": False,
        "has_protected_sequence_privilege": False,
        "has_unexpected_function_execute": False,
        "has_required_function_execute": True,
    }
    row.update(overrides)
    return row


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
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _Acquire(self.conn)


class _Transaction:
    def __init__(self, conn, kwargs):
        self.conn = conn
        self.kwargs = kwargs

    async def __aenter__(self):
        self.conn.transactions.append(self.kwargs)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _status(operation_kind="shadow_preview", **overrides):
    randomized = operation_kind == "randomized_assignment"
    row = {
        "experiment_id": EXP_ID,
        "protocol_version": 2,
        "experiment_kind": "randomized",
        "transport_kind": "legacy_components_v1",
        "lifecycle_status": "draft",
        "execution_phase": "shadow",
        "admission_state": "closed",
        "component_enabled": False,
        "lease_generation": 7,
        "revision_bundle_sha256": "11" * 32,
        "firmware_revision": "fw-1",
        "config_revision": "cfg-1",
        "registry_revision": "reg-1",
        "grid_revision": "grid-1",
        "design_lock_sha256": "44" * 32,
        "schedule_sha256": "55" * 32,
        "mapping_commitment_sha256": "66" * 32,
        "scoped_probe_approved": False,
        "combined_physical_approved": False,
        "randomized_day_1_approved": False,
        "work_id": WORK_ID,
        "assignment_id": ASSIGNMENT_ID if randomized else None,
        "work_operation_kind": operation_kind,
        "work_execution_phase": "randomized" if randomized else "shadow",
        "work_valid_range": main.asyncpg.Range(
            RESOLVED_AT - timedelta(minutes=30),
            RESOLVED_AT + timedelta(minutes=30),
            lower_inc=True,
            upper_inc=False,
        ),
        "work_expires_at": RESOLVED_AT + timedelta(minutes=30),
        "future_randomized_identity_masked": False,
        "current_work_receipt_ids": [],
        "current_work_policy_state_content_sha256": [],
        "current_work_receipt_sha256": [],
        "current_work_receipt_persisted_at": [],
        "open_exposure_count": 0,
        "resolved_at": RESOLVED_AT,
        # A careless raw-row spread would leak these fake forbidden fields.
        "randomization_secret": "forbidden-secret",
        "mapping": {"X": "B"},
        "target_profile": "aggressive",
    }
    row.update(overrides)
    return row


def _status_without_work(**overrides):
    return _status(
        work_id=None,
        assignment_id=None,
        work_operation_kind=None,
        work_execution_phase=None,
        work_valid_range=None,
        work_expires_at=None,
        **overrides,
    )


class _Conn:
    def __init__(self, status=None, *, control_result=EXP_ID, control_error=None):
        self.statuses = list(status) if isinstance(status, list) else None
        self.status = status if self.statuses is None else None
        self.control_result = control_result
        self.control_error = control_error
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.control_queries: list[tuple[str, tuple[object, ...]]] = []
        self.transactions: list[dict[str, object]] = []

    def transaction(self, **kwargs):
        return _Transaction(self, kwargs)

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if sql != main._EXPERIMENT_V2_API_STATUS_SQL:
            raise AssertionError(f"unexpected fetchrow: {sql}")
        if self.statuses is not None:
            return self.statuses.pop(0)
        return self.status

    async def fetchval(self, sql, *args):
        self.control_queries.append((sql, args))
        if sql not in main._EXPERIMENT_V2_CONTROL_SQL.values():
            raise AssertionError(f"unexpected fetchval: {sql}")
        if self.control_error is not None:
            raise self.control_error
        return self.control_result


def _install(monkeypatch, conn, *, ordinary_pool=None):
    candidate = _Pool(conn)
    monkeypatch.setattr(main, "experiment_lifecycle_pool", main.AttestedExperimentLifecyclePool(candidate))
    monkeypatch.setattr(main, "pool", ordinary_pool)
    return conn


def _assert_one_function_call(conn):
    assert conn.queries == [(main._EXPERIMENT_V2_API_STATUS_SQL, (EXP_ID,))]
    query = conn.queries[0][0]
    assert "fn_experiment_v2_api_status($1::uuid)" in query
    assert "SELECT *" not in query.upper()
    for relation in (
        "control_experiments",
        "control_assignments",
        "experiment_v2_work",
        "experiment_v2_approvals",
        "experiment_v2_observation_receipts",
        "experiment_v2_exposures",
    ):
        assert relation not in query


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", "operator-token")
    monkeypatch.setenv("VERDIFY_EXPERIMENT_API_TOKEN", "lifecycle-token")
    monkeypatch.delenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", raising=False)
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.setattr(main, "experiment_lifecycle_pool", None)
    return TestClient(main.app)


def test_component_status_fails_closed_before_db(monkeypatch):
    monkeypatch.delenv("VERDIFY_EXPERIMENT_OPERATOR_TOKEN", raising=False)
    conn = _install(monkeypatch, _Conn(_status()))
    response = TestClient(main.app).get(
        f"/api/v1/experiments/{EXP_ID}/component-status",
        headers=OP_AUTH,
    )
    assert response.status_code == 403
    assert conn.queries == []


def test_component_status_missing_dedicated_pool_is_endpoint_only_503_and_never_falls_back(monkeypatch, client):
    ordinary_conn = _Conn(_status())
    ordinary_pool = _Pool(ordinary_conn)
    monkeypatch.setattr(main, "pool", ordinary_pool)
    monkeypatch.setattr(main, "experiment_lifecycle_pool", None)
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 503
    assert ordinary_pool.acquire_count == 0
    assert ordinary_conn.queries == []
    assert client.get("/").status_code == 200


def test_component_status_rejects_pool_without_attestation_marker(monkeypatch, client):
    unattested_conn = _Conn(_status())
    unattested_pool = _Pool(unattested_conn)
    monkeypatch.setattr(main, "experiment_lifecycle_pool", unattested_pool)
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 503
    assert unattested_pool.acquire_count == 0
    assert unattested_conn.queries == []


@pytest.mark.asyncio
async def test_missing_incomplete_or_shared_lifecycle_credential_never_builds_pool(monkeypatch):
    calls = []

    async def create_pool(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("create_pool must not run for a rejected credential")

    monkeypatch.setattr(main.asyncpg, "create_pool", create_pool)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.delenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, raising=False)
    monkeypatch.delenv(main.EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV, raising=False)
    assert await main.create_experiment_lifecycle_pool() is None

    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, main.EXPERIMENT_LIFECYCLE_DB_LOGIN)
    assert await main.create_experiment_lifecycle_pool() is None

    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, "ordinary-owner")
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV, "redacted-test-value")
    assert await main.create_experiment_lifecycle_pool() is None

    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, "otherwise-clean-login")
    assert await main.create_experiment_lifecycle_pool() is None

    monkeypatch.setenv("DATABASE_URL", "postgresql://url-owner:redacted@db/verdify")
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, "url-owner")
    assert await main.create_experiment_lifecycle_pool() is None
    assert calls == []


@pytest.mark.asyncio
async def test_lifecycle_database_login_requires_exact_function_only_duty(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, main.EXPERIMENT_LIFECYCLE_DB_LOGIN)
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV, "redacted-test-value")

    class ProbeConnection:
        async def fetchrow(self, query):
            assert "verdify_experiment_lifecycle" in query
            assert "fn_experiment_v2_api_status" in query
            assert "pg_database" in query
            assert "has_table_privilege" in query
            assert "has_any_column_privilege" in query
            assert "has_sequence_privilege" in query
            assert "has_function_privilege" in query
            assert "to_regprocedure" in query
            assert "FROM allowed_functions required" in query
            assert "has_schema_privilege" in query
            assert "duty.rolcanlogin" in query
            assert "duty.rolinherit" in query
            assert "membership.admin_option" in query
            assert "has_unexpected_duty_member" in query
            assert "pg_has_role(candidate.oid, duty.oid, 'member')" in query
            assert "current_user::text AS current_user_name" in query
            assert "session_user::text AS session_user_name" in query
            assert "rolcanlogin AND rolinherit" in query
            assert "has_managed_object_ownership" in query
            assert "candidate_function.prosecdef" in query
            assert "fn_experiment_v2_configure(uuid,text,text,text,text,text,text,uuid,text,bigint,text)" in query
            assert (
                "fn_experiment_v2_lock_design(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)"
                in query
            )
            assert "protected.relname LIKE" not in query
            assert "protected.relkind IN ('r', 'p', 'v', 'm', 'f')" in query
            return _attestation()

    class ProbePool:
        def __init__(self):
            self.closed = False

        def acquire(self):
            return _Acquire(ProbeConnection())

        async def close(self):
            self.closed = True

    candidate = ProbePool()
    create_kwargs = {}

    async def create_pool(*args, **kwargs):
        assert args == ()
        create_kwargs.update(kwargs)
        return candidate

    monkeypatch.setattr(main.asyncpg, "create_pool", create_pool)
    dedicated = await main.create_experiment_lifecycle_pool()
    assert isinstance(dedicated, main.AttestedExperimentLifecyclePool)
    assert create_kwargs["user"] == main.EXPERIMENT_LIFECYCLE_DB_LOGIN
    assert create_kwargs["min_size"] == 1 and create_kwargs["max_size"] == 2
    assert create_kwargs["init"] is main._init_experiment_lifecycle_db_connection
    assert create_kwargs["setup"] is main._setup_experiment_lifecycle_connection
    await dedicated.close()
    assert candidate.closed is True


@pytest.mark.asyncio
async def test_lifecycle_pool_checkout_applies_exact_bounded_session_settings():
    class ProbeConnection:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)

    connection = ProbeConnection()
    await main._setup_experiment_lifecycle_connection(connection)
    assert connection.statements == [
        "SET application_name = 'verdify-api-experiment-lifecycle'",
        f"SET statement_timeout = '{main.EXPERIMENT_STATUS_DB_STATEMENT_TIMEOUT_MS}ms'",
    ]


@pytest.mark.asyncio
async def test_lifecycle_pool_init_stays_separate_during_ordinary_role_cutover(monkeypatch):
    class ProbeConnection:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)

    monkeypatch.setenv(main.API_RUNTIME_DB_ROLE_REQUIRED_ENV, "1")
    connection = ProbeConnection()
    await main._init_experiment_lifecycle_db_connection(connection)
    assert connection.statements == ["SET jit = off"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("current_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_matches", False),
        ("session_user_matches", 1),
        ("duty_member", False),
        ("duty_member", 1),
        ("duty_membership_non_admin", False),
        ("login_role_safe", False),
        ("is_superuser", True),
        ("is_superuser", 0),
        ("is_database_owner", True),
        ("has_elevated_role_attributes", True),
        ("duty_role_safe", False),
        ("has_other_role_membership", True),
        ("has_unexpected_duty_member", True),
        ("has_managed_object_ownership", True),
        ("schema_usage", False),
        ("has_public_schema_create", True),
        ("has_protected_relation_privilege", True),
        ("has_protected_sequence_privilege", True),
        ("has_unexpected_function_execute", True),
        ("has_required_function_execute", False),
    ],
)
async def test_lifecycle_database_login_rejects_every_privilege_escape(monkeypatch, field, unsafe_value):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_USER_ENV, main.EXPERIMENT_LIFECYCLE_DB_LOGIN)
    monkeypatch.setenv(main.EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV, "redacted-test-value")

    class ProbeConnection:
        async def fetchrow(self, _query):
            return _attestation(**{field: unsafe_value})

    class ProbePool:
        def __init__(self):
            self.closed = False

        def acquire(self):
            return _Acquire(ProbeConnection())

        async def close(self):
            self.closed = True

    candidate = ProbePool()

    async def create_pool(*_args, **_kwargs):
        return candidate

    monkeypatch.setattr(main.asyncpg, "create_pool", create_pool)
    assert await main.create_experiment_lifecycle_pool() is None
    assert candidate.closed is True


def test_component_status_reports_safe_default_off(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(_status()))
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["component_capability_mode"] == "off"
    assert body["environment_admissible"] is False
    assert body["environment_gate_reason"] == "component_capability_off"
    assert body["execution_phase"] == "shadow"
    assert body["admission_state"] == "closed"
    assert body["current_work"]["operation_kind"] == "shadow_preview"
    # Preview/readiness work cannot disclose randomized assignment identity.
    assert body["current_work"]["assignment_id"] is None
    assert body["current_work"]["blinded_label"] is None
    _assert_one_function_call(conn)


def test_enabled_component_requires_vector_exactly_off(monkeypatch, client):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "shadow")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXP_ID)
    conn = _install(monkeypatch, _Conn(_status(component_enabled=True)))
    body = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH).json()
    assert body["environment_admissible"] is False
    assert body["environment_gate_reason"] == "generalized_vector_mode_not_exactly_off"
    _assert_one_function_call(conn)


def test_randomized_assignment_exposes_only_function_provided_opaque_identity(monkeypatch, client):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXP_ID)
    conn = _install(
        monkeypatch,
        _Conn(
            _status(
                "randomized_assignment",
                lifecycle_status="running",
                execution_phase="randomized",
                admission_state="open",
                component_enabled=True,
                scoped_probe_approved=True,
                combined_physical_approved=True,
                randomized_day_1_approved=True,
                current_work_receipt_ids=["cccccccc-dddd-eeee-ffff-000000000000"],
                current_work_policy_state_content_sha256=["22" * 32],
                current_work_receipt_sha256=["33" * 32],
                current_work_receipt_persisted_at=[RESOLVED_AT],
                open_exposure_count=1,
            )
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["environment_admissible"] is True
    assert body["current_work"]["assignment_id"] == ASSIGNMENT_ID
    assert body["current_work"]["blinded_label"] is None
    assert body["approvals"] == {
        "scoped_probe": True,
        "combined_physical": True,
        "randomized_day_1": True,
    }
    assert body["state_identity"] == {
        "work_id": WORK_ID,
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
    _assert_one_function_call(conn)


def test_status_function_contract_mismatch_is_endpoint_only_503(monkeypatch, client):
    conn = _install(
        monkeypatch,
        _Conn(
            _status(
                current_work_receipt_ids=["cccccccc-dddd-eeee-ffff-000000000000"],
                current_work_policy_state_content_sha256=[],
            )
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 503
    _assert_one_function_call(conn)


def test_recovered_work_is_terminal_and_historical_receipt_is_not_displayed(monkeypatch, client):
    conn = _install(
        monkeypatch,
        _Conn(
            _status(
                work_id=None,
                assignment_id=None,
                work_operation_kind=None,
                work_execution_phase=None,
                work_valid_range=None,
                work_expires_at=None,
            )
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    assert response.json()["current_work"] is None
    assert response.json()["state_identity"]["work_id"] is None
    assert response.json()["state_identity"]["observation_receipt_sha256"] is None
    _assert_one_function_call(conn)


def test_unknown_or_malformed_id_is_not_a_component_status(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(None))
    assert client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH).status_code == 404
    query_count = len(conn.queries)
    assert client.get("/api/v1/experiments/not-a-uuid/component-status", headers=OP_AUTH).status_code == 404
    assert len(conn.queries) == query_count
    _assert_one_function_call(conn)


def test_status_function_cannot_substitute_another_experiment_identity(monkeypatch, client):
    conn = _install(
        monkeypatch,
        _Conn(_status(experiment_id="99999999-9999-4999-8999-999999999999")),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 503
    _assert_one_function_call(conn)


def test_pending_randomized_work_must_mask_every_future_identity(monkeypatch, client):
    future_start = RESOLVED_AT + timedelta(minutes=30)
    conn = _install(
        monkeypatch,
        _Conn(
            _status(
                "randomized_assignment",
                work_id=WORK_ID,
                assignment_id=None,
                work_valid_range=main.asyncpg.Range(
                    future_start,
                    future_start + timedelta(hours=1),
                    lower_inc=True,
                    upper_inc=False,
                ),
                work_expires_at=future_start + timedelta(hours=1),
                future_randomized_identity_masked=False,
            )
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 503
    _assert_one_function_call(conn)


def test_component_work_model_rejects_cross_phase_identity():
    with pytest.raises(ValidationError):
        main.ComponentExperimentWorkStatus(
            work_id=WORK_ID,
            execution_phase="commissioning",
            operation_kind="commissioning_probe",
            valid_from="2026-08-23T20:00:00+00:00",
            valid_to="2026-08-23T21:00:00+00:00",
            expires_at="2026-08-23T21:00:00+00:00",
            temporal_state="active",
            expired=False,
            assignment_id=ASSIGNMENT_ID,
            blinded_label="X",
        )


def test_pending_work_is_not_labeled_expired(monkeypatch, client):
    future_start = RESOLVED_AT + timedelta(minutes=30)
    conn = _install(
        monkeypatch,
        _Conn(
            _status(
                "randomized_assignment",
                execution_phase="randomized",
                work_id=None,
                assignment_id=None,
                work_valid_range=main.asyncpg.Range(
                    future_start,
                    future_start + timedelta(hours=1),
                    lower_inc=True,
                    upper_inc=False,
                ),
                work_expires_at=future_start + timedelta(hours=1),
                future_randomized_identity_masked=True,
            )
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXP_ID}/component-status", headers=OP_AUTH)
    assert response.status_code == 200
    assert response.json()["current_work"]["temporal_state"] == "pending"
    assert response.json()["current_work"]["expired"] is False
    assert response.json()["current_work"]["assignment_id"] is None
    assert response.json()["current_work"]["blinded_label"] is None
    assert response.json()["state_identity"]["work_id"] is None
    _assert_one_function_call(conn)


def _existing_control_payload(action, **overrides):
    payload = {
        "action": action,
        "audit_ref": f"issue-642-{action}",
        "expected_lifecycle_status": "draft",
        "expected_execution_phase": "shadow",
        "expected_admission_state": "closed",
        "expected_component_enabled": False,
        "expected_lease_generation": 7,
        "expected_revision_bundle_sha256": "11" * 32,
    }
    payload.update(overrides)
    return payload


def test_component_control_fails_auth_before_pool_or_database(monkeypatch):
    monkeypatch.delenv("VERDIFY_EXPERIMENT_API_TOKEN", raising=False)
    conn = _install(monkeypatch, _Conn([_status_without_work(), _status_without_work()]))
    response = TestClient(main.app).post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("transition", target_execution_phase="commissioning"),
    )
    assert response.status_code == 403
    assert conn.queries == []
    assert conn.control_queries == []
    assert conn.transactions == []


def test_component_control_never_falls_back_to_ordinary_owner_pool(monkeypatch, client):
    ordinary_conn = _Conn(_status_without_work())
    ordinary_pool = _Pool(ordinary_conn)
    monkeypatch.setattr(main, "pool", ordinary_pool)
    monkeypatch.setattr(main, "experiment_lifecycle_pool", None)
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("transition", target_execution_phase="commissioning"),
    )
    assert response.status_code == 503
    assert ordinary_pool.acquire_count == 0
    assert ordinary_conn.queries == []


def test_component_control_exact_function_allowlist_has_no_dynamic_sql():
    expected = {
        "configure": "fn_experiment_v2_configure",
        "lock_design": "fn_experiment_v2_lock_design",
        "register_state": "fn_experiment_v2_register_state",
        "record_approval": "fn_experiment_v2_record_approval",
        "transition": "fn_experiment_v2_transition",
        "set_admission": "fn_experiment_v2_set_admission",
        "record_facility_safe_closure": "fn_experiment_v2_record_facility_safe_closure",
        "create_work": "fn_experiment_v2_create_work",
        "request_recovery": "fn_experiment_v2_request_recovery",
        "complete": "fn_experiment_v2_complete",
    }
    assert set(main._EXPERIMENT_V2_CONTROL_SQL) == set(expected)
    for action, function_name in expected.items():
        query = main._EXPERIMENT_V2_CONTROL_SQL[action]
        assert query.count("fn_experiment_v2_") == 1
        assert function_name in query
        assert function_name in main._EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL
        assert ";" not in query
        for forbidden in (
            "control_experiments",
            "control_assignments",
            "experiment_v2_work ",
            "experiment_v2_approvals",
            "experiment_v2_randomization",
        ):
            assert forbidden not in query


def test_api_attestation_exactly_matches_the_migration_lifecycle_grant() -> None:
    migration = (Path(__file__).parents[1] / "db/migrations/214-confirmed-component-experiment-v2.sql").read_text()
    grant_statement = "GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle"
    grant_end = migration.index(grant_statement)
    grant_start = migration.rfind("FOREACH fn IN ARRAY ARRAY[", 0, grant_end)
    assert grant_start >= 0
    lifecycle_grant = migration[grant_start:grant_end]
    granted = set(re.findall(r"'(public\.fn_experiment_v2_[^']+)'::regprocedure", lifecycle_grant))
    expected = {
        "public.fn_experiment_v2_configure(uuid,text,text,text,text,text,text,uuid,text,bigint,text)",
        "public.fn_experiment_v2_lock_design(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)",
        "public.fn_experiment_v2_register_state(uuid,text,smallint,bytea,bytea,text)",
        "public.fn_experiment_v2_record_approval(uuid,text,text,integer,text,text,tstzrange,timestamptz,text,text,text)",
        "public.fn_experiment_v2_transition(uuid,text,text,text,text)",
        "public.fn_experiment_v2_set_admission(uuid,text,text,text)",
        "public.fn_experiment_v2_record_facility_safe_closure(uuid,text,text,text)",
        "public.fn_experiment_v2_create_work(uuid,text,text,tstzrange,timestamptz,text)",
        "public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)",
        "public.fn_experiment_v2_complete(uuid,text,text)",
        "public.fn_experiment_v2_api_status(uuid)",
    }
    assert granted == expected
    assert all(signature in main._EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL for signature in expected)


def test_component_transition_is_serializable_and_optimistically_checked(monkeypatch, client):
    before = _status_without_work()
    after = _status_without_work(execution_phase="commissioning", component_enabled=True, lease_generation=8)
    conn = _install(monkeypatch, _Conn([before, after], control_result=EXP_ID))
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload(
            "transition",
            target_execution_phase="commissioning",
            note="readiness evidence bound",
        ),
    )
    assert response.status_code == 200, response.text
    assert conn.transactions == [{"isolation": "serializable"}]
    assert conn.queries == [
        (main._EXPERIMENT_V2_API_STATUS_SQL, (EXP_ID,)),
        (main._EXPERIMENT_V2_API_STATUS_SQL, (EXP_ID,)),
    ]
    assert len(conn.control_queries) == 1
    query, args = conn.control_queries[0]
    assert query == main._EXPERIMENT_V2_CONTROL_SQL["transition"]
    assert args == (
        EXP_ID,
        None,
        "commissioning",
        "verdify-api:issue-642-transition",
        "readiness evidence bound",
    )
    body = response.json()
    assert body["previous_state"] == {
        "lifecycle_status": "draft",
        "execution_phase": "shadow",
        "admission_state": "closed",
        "component_enabled": False,
        "lease_generation": 7,
        "revision_bundle_sha256": "11" * 32,
    }
    assert body["state"]["execution_phase"] == "commissioning"
    assert body["state"]["lease_generation"] == 8
    assert "target_profile" not in response.text
    assert "mapping" not in response.text


def test_component_control_stale_state_conflicts_before_mutation(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(_status_without_work(lease_generation=8)))
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("set_admission", target_admission_state="open", reason="probe window"),
    )
    assert response.status_code == 409
    assert response.json()["detail"].endswith("lease_generation")
    assert conn.transactions == [{"isolation": "serializable"}]
    assert conn.control_queries == []


def test_component_control_serialization_race_is_retryable_conflict(monkeypatch, client):
    conn = _install(
        monkeypatch,
        _Conn(
            _status_without_work(),
            control_error=main.asyncpg.exceptions.SerializationError("concurrent update"),
        ),
    )
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("transition", target_execution_phase="commissioning"),
    )
    assert response.status_code == 409
    assert "refresh status and retry" in response.json()["detail"]
    assert conn.transactions == [{"isolation": "serializable"}]
    assert len(conn.control_queries) == 1


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [
        ("initial candidate configure expected binding is stale", 409),
        ("candidate replacement expected binding is stale", 409),
        ("superseded candidate revision cannot reactivate old readiness evidence", 409),
        ("candidate replacement requires exact draft/closed binding and terminal current work", 422),
    ],
)
def test_component_candidate_conflicts_are_distinct_from_readiness_gates(message, expected_status):
    error = main._component_control_sql_http_error(main.asyncpg.exceptions.RaiseError(message))
    assert error.status_code == expected_status
    assert error.detail == message


def test_component_configure_uses_atomic_fixed_precondition_and_returns_no_design_payload(monkeypatch, client):
    after = _status_without_work(lease_generation=0)
    conn = _install(monkeypatch, _Conn([None, after], control_result=EXP_ID))
    payload = {
        "action": "configure",
        "audit_ref": "issue-642-configure",
        "expected_protocol_version": 1,
        "expected_lifecycle_status": "draft",
        "expected_execution_phase": None,
        "expected_admission_state": "closed",
        "expected_component_enabled": False,
        "expected_lease_generation": 0,
        "expected_revision_bundle_sha256": None,
        "firmware_revision": "firmware-revision",
        "config_revision": "config-revision",
        "registry_revision": "registry-revision",
        "grid_revision": "grid-revision",
        "study_id": "verdify-switchback-v2-2027",
        "assignment_namespace_uuid": "aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb",
    }
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["previous_state"] is None
    assert response.json()["state"]["lease_generation"] == 0
    assert payload["study_id"] not in response.text
    query, args = conn.control_queries[0]
    assert query == main._EXPERIMENT_V2_CONTROL_SQL["configure"]
    assert args == (
        EXP_ID,
        "firmware-revision",
        "config-revision",
        "registry-revision",
        "grid-revision",
        "verdify-switchback-v2-2027",
        main.uuid.UUID("aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb"),
        None,
        0,
        "verdify-api:issue-642-configure",
    )


def test_component_configure_replaces_only_an_observed_unlocked_candidate(monkeypatch, client):
    before = _status_without_work(design_lock_sha256=None)
    after = _status_without_work(
        design_lock_sha256=None,
        revision_bundle_sha256="22" * 32,
        lease_generation=8,
    )
    conn = _install(monkeypatch, _Conn([before, after], control_result=EXP_ID))
    payload = {
        "action": "configure",
        "audit_ref": "candidate-revision-2",
        "expected_protocol_version": 2,
        "expected_lifecycle_status": "draft",
        "expected_execution_phase": "shadow",
        "expected_admission_state": "closed",
        "expected_component_enabled": False,
        "expected_lease_generation": 7,
        "expected_revision_bundle_sha256": "11" * 32,
        "firmware_revision": "firmware-revision-2",
        "config_revision": "config-revision-2",
        "registry_revision": "registry-revision-2",
        "grid_revision": "grid-revision-2",
        "study_id": "verdify-switchback-v2-2027",
        "assignment_namespace_uuid": "aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb",
    }
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert response.json()["previous_state"]["revision_bundle_sha256"] == "11" * 32
    assert response.json()["state"]["revision_bundle_sha256"] == "22" * 32
    assert conn.control_queries[0][1][-3:] == (
        "11" * 32,
        7,
        "verdify-api:candidate-revision-2",
    )


def test_component_configure_preserves_database_lost_response_replay(monkeypatch, client):
    current = _status_without_work(
        design_lock_sha256=None,
        revision_bundle_sha256="22" * 32,
        lease_generation=8,
    )
    conn = _install(monkeypatch, _Conn([current, current], control_result=EXP_ID))
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json={
            "action": "configure",
            "audit_ref": "candidate-revision-lost-response",
            "expected_protocol_version": 2,
            "expected_lifecycle_status": "draft",
            "expected_execution_phase": "aa_rehearsal",
            "expected_admission_state": "closed",
            "expected_component_enabled": True,
            "expected_lease_generation": 7,
            "expected_revision_bundle_sha256": "11" * 32,
            "firmware_revision": "firmware-revision-2",
            "config_revision": "config-revision-2",
            "registry_revision": "registry-revision-2",
            "grid_revision": "grid-revision-2",
            "study_id": "verdify-switchback-v2-2027",
            "assignment_namespace_uuid": "aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb",
        },
    )

    assert response.status_code == 200, response.text
    assert len(conn.control_queries) == 1
    assert conn.control_queries[0][1][-3:-1] == ("11" * 32, 7)
    assert response.json()["state"]["revision_bundle_sha256"] == "22" * 32


def test_component_configure_checks_fresh_candidate_authority_axes_before_db(monkeypatch, client):
    current = _status_without_work(design_lock_sha256=None)
    conn = _install(monkeypatch, _Conn(current, control_result=EXP_ID))
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json={
            "action": "configure",
            "audit_ref": "candidate-revision-stale-phase",
            "expected_protocol_version": 2,
            "expected_lifecycle_status": "draft",
            "expected_execution_phase": "commissioning",
            "expected_admission_state": "closed",
            "expected_component_enabled": False,
            "expected_lease_generation": 7,
            "expected_revision_bundle_sha256": "11" * 32,
            "firmware_revision": "firmware-revision-2",
            "config_revision": "config-revision-2",
            "registry_revision": "registry-revision-2",
            "grid_revision": "grid-revision-2",
            "study_id": "verdify-switchback-v2-2027",
            "assignment_namespace_uuid": "aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"].endswith("execution_phase")
    assert conn.control_queries == []


def test_component_lock_design_binds_every_pre_draw_artifact_atomically(monkeypatch, client):
    before = _status_without_work(execution_phase="randomized", design_lock_sha256=None)
    after = _status_without_work(
        lifecycle_status="locked",
        execution_phase="randomized",
        lease_generation=8,
        design_lock_sha256="33" * 32,
    )
    conn = _install(monkeypatch, _Conn([before, after], control_result=EXP_ID))
    artifact_hashes = {
        "design_lock_sha256": "33" * 32,
        "source_git_sha": "44" * 20,
        "schedule_schema_sha256": main._COMPONENT_EXPERIMENT_SCHEDULE_SCHEMA_SHA256,
        "selector_identity_sha256": "55" * 32,
        "selector_artifact_sha256": "66" * 32,
        "context_schema_sha256": "77" * 32,
        "endpoint_artifact_sha256": "88" * 32,
        "outcome_schema_sha256": "99" * 32,
        "analyzer_environment_sha256": "aa" * 32,
        "power_artifact_sha256": "bb" * 32,
    }
    payload = _existing_control_payload(
        "lock_design",
        expected_execution_phase="randomized",
        study_start_local_date="2027-01-10",
        randomized_pair_count=150,
        selector_context_cutoff_local="11:45:00",
        **artifact_hashes,
    )
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "lock_design"
    assert response.json()["state"]["lifecycle_status"] == "locked"
    assert response.json()["state"]["lease_generation"] == 8
    query, args = conn.control_queries[0]
    assert query == main._EXPERIMENT_V2_CONTROL_SQL["lock_design"]
    assert args == (
        EXP_ID,
        date(2027, 1, 10),
        150,
        time(11, 45),
        artifact_hashes["design_lock_sha256"],
        artifact_hashes["source_git_sha"],
        artifact_hashes["schedule_schema_sha256"],
        artifact_hashes["selector_identity_sha256"],
        artifact_hashes["selector_artifact_sha256"],
        artifact_hashes["context_schema_sha256"],
        artifact_hashes["endpoint_artifact_sha256"],
        artifact_hashes["outcome_schema_sha256"],
        artifact_hashes["analyzer_environment_sha256"],
        artifact_hashes["power_artifact_sha256"],
        "verdify-api:issue-642-lock_design",
    )
    for artifact_hash in artifact_hashes.values():
        assert artifact_hash not in response.text


def test_component_lock_design_preserves_exact_database_lost_response_replay(monkeypatch, client):
    locked = _status_without_work(
        lifecycle_status="locked",
        execution_phase="randomized",
        lease_generation=8,
        design_lock_sha256="33" * 32,
    )
    conn = _install(monkeypatch, _Conn([locked, locked], control_result=EXP_ID))
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload(
            "lock_design",
            expected_execution_phase="randomized",
            study_start_local_date="2027-01-10",
            randomized_pair_count=150,
            selector_context_cutoff_local="11:45:00",
            design_lock_sha256="33" * 32,
            source_git_sha="44" * 20,
            schedule_schema_sha256=main._COMPONENT_EXPERIMENT_SCHEDULE_SCHEMA_SHA256,
            selector_identity_sha256="55" * 32,
            selector_artifact_sha256="66" * 32,
            context_schema_sha256="77" * 32,
            endpoint_artifact_sha256="88" * 32,
            outcome_schema_sha256="99" * 32,
            analyzer_environment_sha256="aa" * 32,
            power_artifact_sha256="bb" * 32,
        ),
    )

    assert response.status_code == 200, response.text
    assert len(conn.control_queries) == 1
    assert response.json()["previous_state"]["lifecycle_status"] == "locked"
    assert response.json()["state"]["lifecycle_status"] == "locked"


def test_component_lock_design_requires_the_complete_artifact_set_before_db(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(_status_without_work()))
    incomplete = _existing_control_payload(
        "lock_design",
        study_start_local_date="2027-01-10",
        randomized_pair_count=150,
        design_lock_sha256="33" * 32,
        source_git_sha="44" * 20,
        schedule_schema_sha256=main._COMPONENT_EXPERIMENT_SCHEDULE_SCHEMA_SHA256,
    )
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=incomplete,
    )

    assert response.status_code == 422
    assert conn.queries == []
    assert conn.control_queries == []


@pytest.mark.parametrize(
    ("action", "specific", "result_id"),
    [
        (
            "register_state",
            {
                "profile": "baseline",
                "wire_schema_version": 1,
                "wire_manifest_digest_hex": "33" * 32,
                "wire_vector_hex": "44" * 178,
            },
            WORK_ID,
        ),
        (
            "record_approval",
            {
                "approval_kind": "combined_physical",
                "scope_name": "combined",
                "issue_number": 641,
                "approval_ref": "issue-641-comment",
                "artifact_sha256": "55" * 32,
            },
            WORK_ID,
        ),
        (
            "set_admission",
            {"target_admission_state": "emergency_hold", "reason": "facility rescue"},
            EXP_ID,
        ),
        (
            "record_facility_safe_closure",
            {"authorization_ref": "facility-event-1", "safe_state_artifact_sha256": "66" * 32},
            EXP_ID,
        ),
        (
            "create_work",
            {
                "operation_kind": "shadow_preview",
                "target_profile": "baseline",
                "valid_from": "2027-01-01T10:00:00Z",
                "valid_to": "2027-01-01T11:00:00Z",
                "expires_at": "2027-01-01T11:00:00Z",
            },
            WORK_ID,
        ),
        (
            "request_recovery",
            {
                "source_work_id": WORK_ID,
                "reason": "delivery uncertainty",
                "valid_from": "2027-01-01T10:00:00Z",
                "valid_to": "2027-01-01T11:00:00Z",
                "expires_at": "2027-01-01T11:00:00Z",
            },
            WORK_ID,
        ),
        ("complete", {"note": "frozen evidence complete"}, EXP_ID),
    ],
)
def test_every_component_control_action_is_typed_and_treatment_free(monkeypatch, client, action, specific, result_id):
    conn = _install(
        monkeypatch,
        _Conn([_status_without_work(), _status_without_work()], control_result=result_id),
    )
    payload = _existing_control_payload(action, **specific)
    response = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["action"] == action
    assert response.json()["result_id"] == result_id
    assert conn.control_queries[0][0] == main._EXPERIMENT_V2_CONTROL_SQL[action]
    assert conn.control_queries[0][1].count(f"verdify-api:issue-642-{action}") == 1
    for forbidden in ("wire_vector", "manifest_digest", "target_profile", "approval_ref", "reason"):
        assert forbidden not in response.text


def test_unknown_or_cross_typed_component_control_is_rejected_before_db(monkeypatch, client):
    conn = _install(monkeypatch, _Conn(_status_without_work()))
    unknown = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("arbitrary_sql", sql="DROP TABLE control_experiments"),
    )
    assert unknown.status_code == 422
    generic_lock = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload("transition", target_lifecycle_status="locked"),
    )
    assert generic_lock.status_code == 422
    bad_work = client.post(
        f"/api/v1/experiments/{EXP_ID}/component-control/commands",
        headers=API_AUTH,
        json=_existing_control_payload(
            "create_work",
            operation_kind="shadow_preview",
            target_profile="aggressive",
            valid_from="2027-01-01T10:00:00Z",
            valid_to="2027-01-01T11:00:00Z",
            expires_at="2027-01-01T11:00:00Z",
        ),
    )
    assert bad_work.status_code == 422
    assert conn.queries == []
    assert conn.control_queries == []


def test_api_deployment_uses_optional_named_experiment_secret_keys() -> None:
    documents = yaml.safe_load_all((Path(__file__).parents[1] / "deploy/k8s/base/api-deployment.yaml").read_text())
    manifest = next(document for document in documents if document.get("kind") == "Deployment")
    container = next(item for item in manifest["spec"]["template"]["spec"]["containers"] if item["name"] == "api")
    environment = {item["name"]: item for item in container["env"]}
    for name in (
        main.EXPERIMENT_LIFECYCLE_DB_USER_ENV,
        main.EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV,
        main.EXPERIMENT_API_TOKEN_ENV,
        main.EXPERIMENT_OPERATOR_TOKEN_ENV,
    ):
        secret_ref = environment[name]["valueFrom"]["secretKeyRef"]
        assert secret_ref == {
            "name": "verdify-app-secrets",
            "key": name,
            "optional": True,
        }


def test_rollout_sources_use_exact_component_mode_and_keep_vector_path_off() -> None:
    root = Path(__file__).parents[1]
    runbook = (root / "docs/runbooks/experiment-rollout.md").read_text()
    assert "VERDIFY_COMPONENT_EXPERIMENT_ENABLED=off|enabled" in runbook
    for stale in (
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED=false|true",
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED=true",
        "enabled `false`",
        "component enabled `false`",
        "component enabled false",
    ):
        assert stale not in runbook

    config = yaml.safe_load((root / "deploy/k8s/base/configmap.yaml").read_text())
    assert config["data"]["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert config["data"]["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert config["data"]["VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED"] == "1"
    config_source = (root / "deploy/k8s/base/configmap.yaml").read_text()
    assert "overlays flip these" not in config_source
    assert 'Flipped to "0"' not in config_source
