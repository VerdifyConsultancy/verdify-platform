from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.readiness import (  # noqa: E402
    FAILURE_THRESHOLD,
    ReadinessReporter,
    readiness_passes,
)
from experiment_orchestrator.runtime import run_worker  # noqa: E402
from experiment_orchestrator.safe_logging import build_logger, emit  # noqa: E402
from experiment_orchestrator.settings import load_settings  # noqa: E402

EXPERIMENT = "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_inactive_once_never_constructs_database_or_network(capsys, tmp_path: Path) -> None:
    called = False

    async def factory(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError

    result = await run_worker(
        load_settings({}, mode_override="selector"),
        logger=build_logger(),
        once=True,
        pool_factory=factory,
        readiness=ReadinessReporter("selector", 15.0, tmp_path / "ready.json"),
    )
    assert result == 0 and not called
    assert readiness_passes(tmp_path / "ready.json")
    logged = capsys.readouterr().err
    assert '"reason":"capability_off"' in logged
    assert "password" not in logged and "api_key" not in logged


def test_structured_logger_rejects_unbounded_fields_and_never_formats_message(capsys) -> None:
    logger = build_logger()
    logger.info("raw-message-must-not-render", extra={"safe_fields": {"event": "safe_event"}})
    logged = capsys.readouterr().err
    assert "raw-message-must-not-render" not in logged
    with pytest.raises(ValueError, match="safe tokens"):
        emit(logger, event="unsafe event", reason="credential=value")


def test_package_has_executable_entrypoint_and_no_device_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "experiment_orchestrator"
    assert (package / "__main__.py").is_file()
    combined = "\n".join(path.read_text() for path in package.glob("*.py")).lower()
    for forbidden in ("aioesphomeapi", "subscribe_states_request", "setter", "mqtt", "device client"):
        assert forbidden not in combined
    assert (package / "requirements.txt").read_text().splitlines() == [
        "asyncpg==0.31.0",
        "httpx==0.28.1",
        "pyyaml==6.0.3",
        "tzdata==2026.3",
    ]
    dockerfile = (package / "Dockerfile").read_text()
    assert "COPY experiment_orchestrator/*.py /app/experiment_orchestrator/" in dockerfile
    assert (
        "COPY research/planner-efficacy/switchback/v2_outcomes.py /app/experiment_orchestrator/_frozen_v2_outcomes.py"
    ) in dockerfile
    assert "COPY experiment_orchestrator/ /app" not in dockerfile
    source_copy = dockerfile.index("COPY experiment_orchestrator/*.py")
    evaluator_copy = dockerfile.index("COPY research/planner-efficacy/switchback/v2_outcomes.py")
    mode_normalization = dockerfile.index("RUN find /app -type d -exec chmod 0555 {} +")
    non_root_runtime = dockerfile.index("USER 10001:10001")
    assert source_copy < mode_normalization
    assert evaluator_copy < mode_normalization < non_root_runtime
    assert "find /app -type f -exec chmod 0444 {} +" in dockerfile
    dockerignore = (package.parent / ".dockerignore").read_text()
    assert "__pycache__" in dockerignore and "*.pyc" in dockerignore
    evaluator = (package.parent / "research" / "planner-efficacy" / "switchback" / "v2_outcomes.py").read_text().lower()
    for forbidden in ("aioesphomeapi", "mqtt", "subscribe_states_request", "setpoint"):
        assert forbidden not in evaluator


def test_event_loop_smoke() -> None:
    # Importing the runtime must not start work or install a global event loop.
    assert asyncio.get_event_loop_policy() is not None


def test_readiness_is_off_safe_stale_bounded_and_three_failure_tolerant(tmp_path: Path) -> None:
    path = tmp_path / "ready.json"
    reporter = ReadinessReporter("selector", 15.0, path)

    reporter.inactive("capability_off")
    assert readiness_passes(path)
    reporter.inactive("database_unconfigured")
    assert not readiness_passes(path)

    reporter.starting()
    assert not readiness_passes(path)
    reporter.cycle_succeeded()
    assert readiness_passes(path)
    for expected_failures in range(1, FAILURE_THRESHOLD):
        reporter.cycle_failed("dependency_unavailable")
        assert reporter.consecutive_failures == expected_failures
        assert readiness_passes(path)
    reporter.cycle_failed("dependency_unavailable")
    assert reporter.consecutive_failures == FAILURE_THRESHOLD
    assert not readiness_passes(path)

    reporter.cycle_succeeded()
    payload = json.loads(path.read_text())
    assert set(payload) == {
        "schema",
        "ready",
        "mode",
        "reason",
        "consecutive_failures",
        "expires_monotonic_ns",
    }
    assert readiness_passes(path, monotonic_ns=payload["expires_monotonic_ns"])
    assert not readiness_passes(path, monotonic_ns=payload["expires_monotonic_ns"] + 1)
    assert all(forbidden not in path.read_text().lower() for forbidden in ("password", "api_key", "exception"))


@pytest.mark.asyncio
async def test_enabled_missing_credentials_is_healthy_process_but_unready(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": EXPERIMENT,
        },
        mode_override="selector",
    )
    path = tmp_path / "ready.json"
    result = await run_worker(
        settings,
        logger=build_logger(),
        once=True,
        readiness=ReadinessReporter("selector", 15.0, path),
    )
    assert result == 0
    assert not readiness_passes(path)
    assert json.loads(path.read_text())["reason"] == "database_unconfigured"


@pytest.mark.asyncio
async def test_one_shot_attestation_failure_is_unready_and_fail_closed(capsys, tmp_path: Path) -> None:
    calls = 0

    async def factory(**_kwargs):
        nonlocal calls
        calls += 1
        raise OSError("synthetic dependency failure")

    settings = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": EXPERIMENT,
            "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER": "verdify_experiment_v2_randomizer_login",
            "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD": "synthetic-test-password",
            "DB_HOST": "verdify-db",
            "DB_NAME": "verdify",
        },
        mode_override="selector",
    )
    path = tmp_path / "ready.json"
    with pytest.raises(OSError, match="synthetic dependency failure"):
        await run_worker(
            settings,
            logger=build_logger(),
            once=True,
            pool_factory=factory,
            readiness=ReadinessReporter("selector", 15.0, path),
        )
    assert calls == 1
    assert not readiness_passes(path)
    assert json.loads(path.read_text())["reason"] == "attestation_failed"
    assert "synthetic-test-password" not in capsys.readouterr().err


class Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class FreezerConnection:
    def __init__(self) -> None:
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if "pg_roles" in query:
            return {
                "current_user_name": "verdify_experiment_v2_outcome_freezer_login",
                "session_user_name": "verdify_experiment_v2_outcome_freezer_login",
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
        return None


class FreezerPool:
    def __init__(self) -> None:
        self.connection = FreezerConnection()
        self.closed = False

    def acquire(self):
        return Acquire(self.connection)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_freezer_once_attests_and_runs_one_server_cycle_without_provider(capsys, tmp_path: Path) -> None:
    pool = FreezerPool()

    async def factory(**_kwargs):
        return pool

    settings = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": EXPERIMENT,
            "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_USER": ("verdify_experiment_v2_outcome_freezer_login"),
            "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD": "synthetic-test-password",
            "DB_HOST": "verdify-db",
            "DB_NAME": "verdify",
        },
        mode_override="freezer",
    )
    result = await run_worker(
        settings,
        logger=build_logger(),
        once=True,
        pool_factory=factory,
        readiness=ReadinessReporter("freezer", 15.0, tmp_path / "ready.json"),
    )
    assert result == 0 and pool.closed
    assert readiness_passes(tmp_path / "ready.json")
    assert len(pool.connection.calls) == 2
    assert "fn_experiment_v2_outcome_source_cycle" in pool.connection.calls[1][0]
    logged = capsys.readouterr().err
    assert '"disposition":"idle"' in logged
    assert "synthetic-test-password" not in logged


@pytest.mark.asyncio
async def test_long_running_worker_retries_startup_db_outage_without_restart(capsys, tmp_path: Path) -> None:
    pool = FreezerPool()
    factory_calls = 0

    async def factory(**_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            raise OSError("synthetic dependency failure")
        return pool

    settings = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": EXPERIMENT,
            "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_USER": ("verdify_experiment_v2_outcome_freezer_login"),
            "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD": "synthetic-test-password",
            "VERDIFY_EXPERIMENT_V2_POLL_INTERVAL_SECONDS": "1",
            "DB_HOST": "verdify-db",
            "DB_NAME": "verdify",
        },
        mode_override="freezer",
    )
    path = tmp_path / "ready.json"
    task = asyncio.create_task(
        run_worker(
            settings,
            logger=build_logger(),
            pool_factory=factory,
            readiness=ReadinessReporter("freezer", 1.0, path),
        )
    )
    for _ in range(60):
        if factory_calls >= 2 and len(pool.connection.calls) >= 2 and readiness_passes(path):
            break
        await asyncio.sleep(0.05)
    assert not task.done()
    assert factory_calls >= 2
    assert len(pool.connection.calls) >= 2
    assert readiness_passes(path)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.closed
    logged = capsys.readouterr().err
    assert '"reason":"database_attestation_failed"' in logged
    assert "synthetic-test-password" not in logged
