from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator import database  # noqa: E402
from experiment_orchestrator.contracts import OrchestratorMode  # noqa: E402
from experiment_orchestrator.settings import DatabaseSettings  # noqa: E402


def good_attestation(user: str = "scheduler-login") -> dict[str, object]:
    return {
        "current_user_name": user,
        "session_user_name": user,
        "session_user_matches": True,
        "duty_member": True,
        "duty_membership_non_admin": True,
        "login_role_safe": True,
        "is_superuser": False,
        "is_database_owner": False,
        "has_elevated_role_attributes": False,
        "duty_role_safe": True,
        "has_other_role_membership": False,
        "has_managed_object_ownership": False,
        "has_public_schema_create": False,
        "has_protected_relation_privilege": False,
        "has_protected_sequence_privilege": False,
        "has_unexpected_function_execute": False,
        "has_required_function_execute": True,
    }


class Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class Connection:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


class Pool:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.closed = False

    def acquire(self):
        return Acquire(self.connection)

    async def close(self):
        self.closed = True


def settings() -> DatabaseSettings:
    return DatabaseSettings(
        host="verdify-db",
        port=5432,
        database="verdify",
        user="scheduler-login",
        password="top-secret-value",  # noqa: S106 - synthetic test sentinel
        duty_role="verdify_experiment_shadow_scheduler",
    )


def test_attestation_rejects_each_privilege_expansion_and_wrong_login() -> None:
    assert database.role_attestation_passes(good_attestation(), "scheduler-login")
    for field in (
        "is_superuser",
        "is_database_owner",
        "has_elevated_role_attributes",
        "has_other_role_membership",
        "has_managed_object_ownership",
        "has_public_schema_create",
        "has_protected_relation_privilege",
        "has_protected_sequence_privilege",
        "has_unexpected_function_execute",
    ):
        row = good_attestation()
        row[field] = True
        assert not database.role_attestation_passes(row, "scheduler-login")
    assert not database.role_attestation_passes(good_attestation("other-login"), "scheduler-login")
    admin_edge = good_attestation()
    admin_edge["duty_membership_non_admin"] = False
    assert not database.role_attestation_passes(admin_edge, "scheduler-login")


@pytest.mark.asyncio
async def test_pool_uses_keyword_credentials_one_connection_and_exact_allowlist(monkeypatch) -> None:
    connection = Connection(good_attestation())
    pool = Pool(connection)
    factory_calls = []

    async def factory(**kwargs):
        factory_calls.append(kwargs)
        return pool

    monkeypatch.setitem(
        database.ROLE_FUNCTIONS,
        OrchestratorMode.LIFECYCLE,
        ("public.fn_experiment_v2_lifecycle_cycle(uuid,text)",),
    )
    attested = await database.create_attested_pool(
        settings(),
        OrchestratorMode.LIFECYCLE,
        pool_factory=factory,
    )
    assert attested.mode is OrchestratorMode.LIFECYCLE
    assert factory_calls[0]["password"] == "top-secret-value"
    assert factory_calls[0]["min_size"] == factory_calls[0]["max_size"] == 1
    query, args = connection.calls[0]
    assert "has_function_privilege" in query
    assert "has_protected_relation_privilege" in query
    assert "membership.admin_option" in query
    assert args == (
        "verdify_experiment_shadow_scheduler",
        ["public.fn_experiment_v2_lifecycle_cycle(uuid,text)"],
    )
    assert "top-secret-value" not in query


@pytest.mark.asyncio
async def test_failed_attestation_closes_pool_without_exposing_secret(monkeypatch) -> None:
    row = good_attestation()
    row["has_unexpected_function_execute"] = True
    pool = Pool(Connection(row))

    async def factory(**_kwargs):
        return pool

    monkeypatch.setitem(
        database.ROLE_FUNCTIONS,
        OrchestratorMode.LIFECYCLE,
        ("public.fn_experiment_v2_lifecycle_cycle(uuid,text)",),
    )
    with pytest.raises(database.DatabaseContractError) as caught:
        await database.create_attested_pool(settings(), OrchestratorMode.LIFECYCLE, pool_factory=factory)
    assert pool.closed
    assert "top-secret-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_mode_role_mismatch_never_opens_a_connection() -> None:
    called = False

    async def factory(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError

    wrong = settings()
    wrong = DatabaseSettings(
        host=wrong.host,
        port=wrong.port,
        database=wrong.database,
        user=wrong.user,
        password=wrong.password,
        duty_role="verdify_experiment_lifecycle",
    )
    with pytest.raises(database.DatabaseContractError, match="selected mode"):
        await database.create_attested_pool(wrong, OrchestratorMode.LIFECYCLE, pool_factory=factory)
    assert not called


def test_production_function_allowlists_are_source_locked_for_all_three_duties() -> None:
    assert database.ROLE_FUNCTIONS[OrchestratorMode.LIFECYCLE] == (
        "public.fn_experiment_v2_schedule_shadow_cycle(uuid,date,timestamp with time zone,text,text,text,text,text,text)",
        "public.fn_experiment_v2_due_shadow_cycle(uuid)",
        "public.fn_experiment_v2_due_assignment(uuid)",
        "public.fn_experiment_v2_boundary_cycle(uuid,text)",
        "public.fn_experiment_v2_direct_launch_cycle(uuid,text)",
    )
    assert database.ROLE_FUNCTIONS[OrchestratorMode.SELECTOR] == (
        "public.fn_experiment_v2_finalize_randomization(uuid,text)",
        "public.fn_experiment_v2_selector_cycle(uuid)",
        "public.fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)",
        "public.fn_experiment_v2_record_shadow_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)",
        "public.fn_experiment_v2_reveal(uuid,text)",
    )
    assert database.ROLE_FUNCTIONS[OrchestratorMode.FREEZER] == (
        "public.fn_experiment_v2_outcome_source_cycle(uuid)",
        "public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)",
        "public.fn_experiment_v2_freeze_day_evidence(uuid,uuid,jsonb,jsonb,jsonb,text)",
        "public.fn_experiment_v2_freeze_export(uuid,text,text)",
        "public.fn_experiment_v2_record_shadow_outcome_preview(uuid,uuid,jsonb,text)",
    )
    assert "equipment_direct_state_snapshots" in database.ROLE_ATTESTATION_SQL
    assert "equipment_state_source_receipts" in database.ROLE_ATTESTATION_SQL
    assert "candidate.prosecdef" in database.ROLE_ATTESTATION_SQL
    assert "policy/outbox/setpoint" in database.ROLE_ATTESTATION_SQL
