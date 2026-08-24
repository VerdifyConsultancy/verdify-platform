"""Long-running, signal-aware runtime for one selected non-device duty."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

import asyncpg

from .contracts import ContractError, OrchestratorMode
from .database import DatabaseContractError, PoolFactory, create_attested_pool
from .provider import SelectorProviderAdapter
from .readiness import ReadinessReporter
from .safe_logging import emit
from .service import (
    load_lifecycle_plan,
    run_lifecycle_cycle,
    run_outcome_cycle,
    run_selector_cycle,
)
from .settings import RuntimeSettings
from .stores import (
    LifecycleFunctionStore,
    OutcomeFunctionStore,
    SelectorFunctionStore,
    StoreContractError,
)


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def _update_readiness(
    reporter: ReadinessReporter,
    update: Callable[[], None],
    *,
    logger: logging.Logger,
    mode: str,
) -> None:
    """Keep the worker fail-closed if its bounded status file is unavailable."""

    try:
        update()
    except (OSError, ValueError):
        # The exec probe independently fails on a missing/malformed file. Never
        # log an exception whose text could contain a path or external value.
        emit(
            logger,
            event="orchestrator_readiness_failed",
            mode=mode,
            reason="status_unavailable",
            level=logging.ERROR,
        )


async def run_worker(
    settings: RuntimeSettings,
    *,
    logger: logging.Logger,
    once: bool = False,
    pool_factory: PoolFactory = asyncpg.create_pool,
    readiness: ReadinessReporter | None = None,
) -> int:
    mode = settings.mode.value
    reporter = readiness or ReadinessReporter(mode, settings.poll_interval_seconds)
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    if not settings.runnable:
        _update_readiness(
            reporter,
            lambda: reporter.inactive(settings.inactive_reason or "unconfigured"),
            logger=logger,
            mode=mode,
        )
        emit(
            logger,
            event="orchestrator_inactive",
            mode=mode,
            reason=settings.inactive_reason or "unconfigured",
        )
        if once:
            return 0
        while not stop.is_set():
            _update_readiness(
                reporter,
                lambda: reporter.inactive(settings.inactive_reason or "unconfigured"),
                logger=logger,
                mode=mode,
            )
            await _wait(stop, settings.poll_interval_seconds)
        _update_readiness(reporter, reporter.stopping, logger=logger, mode=mode)
        return 0

    assert settings.database is not None
    assert settings.active_experiment_id is not None
    _update_readiness(reporter, reporter.starting, logger=logger, mode=mode)
    pool = None
    while pool is None and not stop.is_set():
        try:
            pool = await create_attested_pool(
                settings.database,
                settings.mode,
                pool_factory=pool_factory,
            )
        except (DatabaseContractError, asyncpg.PostgresError, OSError):
            _update_readiness(reporter, reporter.attestation_failed, logger=logger, mode=mode)
            emit(
                logger,
                event="orchestrator_start_failed",
                mode=mode,
                reason="database_attestation_failed",
                level=logging.ERROR,
            )
            if once:
                raise
            await _wait(stop, settings.poll_interval_seconds)
    if pool is None:
        _update_readiness(reporter, reporter.stopping, logger=logger, mode=mode)
        return 0
    try:
        if settings.mode is OrchestratorMode.LIFECYCLE:
            store = LifecycleFunctionStore(pool)

            async def cycle() -> str:
                plan = load_lifecycle_plan(
                    settings.lifecycle_plan_path,
                    settings.lifecycle_plan_sha256,
                    settings.active_experiment_id or "",
                )
                return await run_lifecycle_cycle(
                    store,
                    experiment_id=settings.active_experiment_id or "",
                    plan=plan,
                )

        elif settings.mode is OrchestratorMode.SELECTOR:
            store = SelectorFunctionStore(pool)
            provider = SelectorProviderAdapter(settings.provider)

            async def cycle() -> str:
                return await run_selector_cycle(
                    store,
                    experiment_id=settings.active_experiment_id or "",
                    provider=provider,
                    identity_path=settings.selector_identity_path,
                )

        else:
            store = OutcomeFunctionStore(pool)

            async def cycle() -> str:
                return await run_outcome_cycle(
                    store,
                    experiment_id=settings.active_experiment_id or "",
                    identity_path=settings.outcome_identity_path,
                )

        while not stop.is_set():
            try:
                disposition = await cycle()
                emit(
                    logger,
                    event="orchestrator_cycle",
                    mode=mode,
                    disposition=disposition,
                )
                _update_readiness(reporter, reporter.cycle_succeeded, logger=logger, mode=mode)
            except (ContractError, StoreContractError):
                emit(
                    logger,
                    event="orchestrator_cycle_failed",
                    mode=mode,
                    reason="contract_invalid",
                    level=logging.ERROR,
                )
                _update_readiness(
                    reporter,
                    lambda: reporter.cycle_failed("contract_invalid"),
                    logger=logger,
                    mode=mode,
                )
            except (asyncpg.PostgresError, OSError):
                emit(
                    logger,
                    event="orchestrator_cycle_failed",
                    mode=mode,
                    reason="dependency_unavailable",
                    level=logging.ERROR,
                )
                _update_readiness(
                    reporter,
                    lambda: reporter.cycle_failed("dependency_unavailable"),
                    logger=logger,
                    mode=mode,
                )
            if once:
                break
            await _wait(stop, settings.poll_interval_seconds)
        return 0
    finally:
        if not once:
            _update_readiness(reporter, reporter.stopping, logger=logger, mode=mode)
        await pool.close()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
