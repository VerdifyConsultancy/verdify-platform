from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    LifecyclePlan,
    OrchestratorMode,
    canonical_json_bytes,
)
from experiment_orchestrator.database import AttestedPool  # noqa: E402
from experiment_orchestrator.service import (  # noqa: E402
    SelectorCandidate,
    SelectorDecision,
    run_lifecycle_cycle,
)
from experiment_orchestrator.stores import (  # noqa: E402
    LifecycleFunctionStore,
    SelectorFunctionStore,
)

EXPERIMENT = "11111111-1111-4111-8111-111111111111"
DIRECT_EXPERIMENT = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
SUBJECT = "22222222-2222-4222-8222-222222222222"


class Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class Connection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        result = self.rows.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Pool:
    def __init__(self, connection) -> None:
        self.connection = connection

    def acquire(self):
        return Acquire(self.connection)

    async def close(self):
        return None


def attested(connection: Connection, mode: OrchestratorMode) -> AttestedPool:
    return AttestedPool(Pool(connection), mode)


def lifecycle_plan(action: str = "shadow_schedule", experiment_id: str = EXPERIMENT) -> LifecyclePlan:
    payload = {
        "schema": "verdify-experiment-v2-lifecycle-plan-v1",
        "experiment_id": experiment_id,
        "action": action,
    }
    if action == "shadow_schedule":
        payload.update(
            {
                "local_date": "2026-08-26",
                "context_cutoff_at": "2026-08-25T23:45:00.000000Z",
                "context_schema_sha256": "1" * 64,
                "selector_identity_sha256": "2" * 64,
                "selector_artifact_sha256": "3" * 64,
                "endpoint_artifact_sha256": "4" * 64,
                "outcome_schema_sha256": "5" * 64,
            }
        )
    raw = canonical_json_bytes(payload, reject_forbidden_fields=False)
    return LifecyclePlan.parse(raw, hashlib.sha256(raw).hexdigest(), experiment_id)


@pytest.mark.asyncio
async def test_lifecycle_shadow_poll_makes_only_the_exact_schedule_call() -> None:
    connection = Connection([{"cycle_id": SUBJECT}])
    store = LifecycleFunctionStore(attested(connection, OrchestratorMode.LIFECYCLE))
    disposition = await run_lifecycle_cycle(
        store,
        experiment_id=EXPERIMENT,
        plan=lifecycle_plan(),
    )
    assert disposition == "shadow_scheduled"
    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    assert "fn_experiment_v2_schedule_shadow_cycle" in query
    assert "fn_experiment_v2_boundary_cycle" not in query
    assert args[0] == EXPERIMENT and args[-1] == "experiment-v2-orchestrator"


@pytest.mark.asyncio
async def test_lifecycle_boundary_poll_makes_only_one_server_clock_call() -> None:
    connection = Connection(
        [
            {
                "assignment_id": SUBJECT,
                "assigned_local_date": "2026-08-25",
                "assignment_status": "closed",
                "finalized": True,
                "resolved_at": datetime(2026, 8, 26, tzinfo=UTC),
            }
        ]
    )
    store = LifecycleFunctionStore(attested(connection, OrchestratorMode.LIFECYCLE))
    disposition = await run_lifecycle_cycle(
        store,
        experiment_id=DIRECT_EXPERIMENT,
        plan=lifecycle_plan("boundary", DIRECT_EXPERIMENT),
    )
    assert disposition == "boundary_finalized"
    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    assert "fn_experiment_v2_direct_launch_cycle" in query
    assert "schedule_shadow" not in query
    assert args == (DIRECT_EXPERIMENT, "experiment-v2-orchestrator")


@pytest.mark.asyncio
async def test_lifecycle_boundary_poll_preserves_ordinary_path_for_other_studies() -> None:
    other_experiment = "4a9a299c-03b2-43ec-bb37-0cbf21f5ac04"
    connection = Connection([None])
    store = LifecycleFunctionStore(attested(connection, OrchestratorMode.LIFECYCLE))
    disposition = await run_lifecycle_cycle(
        store,
        experiment_id=other_experiment,
        plan=lifecycle_plan("boundary", other_experiment),
    )
    assert disposition == "idle"
    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    assert "fn_experiment_v2_boundary_cycle" in query
    assert "fn_experiment_v2_direct_launch_cycle" not in query
    assert args == (other_experiment, "experiment-v2-orchestrator")


def candidate(kind: str) -> SelectorCandidate:
    return SelectorCandidate(
        cycle_kind=kind,  # type: ignore[arg-type]
        subject_id=SUBJECT,
        assignment_id=SUBJECT if kind == "randomized" else None,
        work_id=None if kind == "randomized" else SUBJECT,
        study_id="study-v2",
        local_date="2026-08-25",
        invocation_key=SUBJECT,
        context_status="frozen",
        context_payload={},
        context_canonical_bytes=b"{}",
        context_sha256=hashlib.sha256(b"{}").hexdigest(),
        source_bundle_sha256="1" * 64,
        context_schema_sha256="2" * 64,
        selector_identity_sha256="3" * 64,
        selector_artifact_sha256="4" * 64,
        context_cutoff_at=datetime(2026, 8, 24, 23, 45, tzinfo=UTC),
        boundary_at=datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        resolved_at=datetime(2026, 8, 24, 23, 46, tzinfo=UTC),
        failure_reason=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["provider_unconfigured", "request_exceeds_context_budget"])
@pytest.mark.parametrize(
    ("kind", "function"),
    [
        ("shadow", "fn_experiment_v2_record_shadow_choice"),
        ("randomized", "fn_experiment_v2_record_selector_choice"),
    ],
)
async def test_selector_store_uses_only_kind_specific_function_and_blind_arguments(
    reason: str,
    kind: str,
    function: str,
) -> None:
    connection = Connection([{"choice_id": SUBJECT}])
    store = SelectorFunctionStore(attested(connection, OrchestratorMode.SELECTOR))
    decision = SelectorDecision(
        "baseline",
        reason,
        "5" * 64,
        None,
        ("6" * 64,),
    )
    await store.record_selector_choice(EXPERIMENT, candidate(kind), decision)
    assert len(connection.calls) == 1
    query, args = connection.calls[0]
    assert function in query
    assert "arm" not in query and "mapping" not in query and "secret" not in query
    assert args[:4] == (EXPERIMENT, SUBJECT, SUBJECT, SUBJECT)
    assert args[4:8] == ("baseline", reason, "5" * 64, None)
    assert args[-2:] == ("4" * 64, "experiment-v2-orchestrator")


@pytest.mark.asyncio
async def test_selector_store_retries_only_exact_boundary_sqlstate_as_safe_baseline() -> None:
    boundary = asyncpg.PostgresError("boundary")
    boundary.sqlstate = "V2B01"
    connection = Connection(
        [
            boundary,
            {
                "choice_id": SUBJECT,
                "selected_profile": "baseline",
                "fallback_reason": "boundary_elapsed_before_choice_persist",
            },
        ]
    )
    store = SelectorFunctionStore(attested(connection, OrchestratorMode.SELECTOR))
    selected = SelectorDecision("moderate", None, "5" * 64, "6" * 64, ("7" * 64,))
    persisted = await store.record_selector_choice(EXPERIMENT, candidate("randomized"), selected)
    assert persisted["fallback_reason"] == "boundary_elapsed_before_choice_persist"
    assert len(connection.calls) == 2
    _query, closure_args = connection.calls[1]
    assert closure_args[4:8] == (
        "baseline",
        "boundary_elapsed_before_choice_persist",
        candidate("randomized").context_sha256,
        None,
    )


@pytest.mark.asyncio
async def test_selector_store_does_not_retry_any_other_database_error() -> None:
    other = asyncpg.PostgresError("other")
    other.sqlstate = "P0001"
    connection = Connection([other])
    store = SelectorFunctionStore(attested(connection, OrchestratorMode.SELECTOR))
    selected = SelectorDecision("moderate", None, "5" * 64, "6" * 64, ("7" * 64,))
    with pytest.raises(asyncpg.PostgresError):
        await store.record_selector_choice(EXPERIMENT, candidate("randomized"), selected)
    assert len(connection.calls) == 1
