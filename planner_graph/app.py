"""Application bootstrap for the planner service.

This module assembles the runtime, storage backend, worker, and HTTP API into
one FastAPI application. It connects deployment startup to the long-running
planner execution loop by deciding which dependencies the service will use.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from planner_graph.api import include_api
from planner_graph.config import AppSettings
from planner_graph.runtime import ExecutionHooks, PlannerRuntime
from planner_graph.store import InMemoryRunStore, PostgresRunStore, RunStore
from planner_graph.worker import PlannerWorker


class PlannerService:
    def __init__(
        self,
        repository: RunStore | None = None,
        runtime: PlannerRuntime | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self.settings = settings or AppSettings.from_env()
        self.runtime = runtime or PlannerRuntime(settings=self.settings)
        self.repository = repository or self._default_repository()
        self.worker = PlannerWorker(self.repository, self.runtime)

    def _default_repository(self) -> RunStore:
        if self.settings.planner_store_backend == "postgres":
            if self.settings.planner_db_dsn is None:
                raise RuntimeError(
                    "PLANNER_STORE_BACKEND=postgres requires PLANNER_DB_DSN or DB_DSN"
                )
            return PostgresRunStore(self.settings.planner_db_dsn)
        if not self.settings.allows_in_memory_store:
            raise RuntimeError(
                "InMemoryRunStore is only allowed in development/test environments. "
                "Set APP_ENV=development for local work or configure "
                "PLANNER_STORE_BACKEND=postgres with PLANNER_DB_DSN for non-development runtime."
            )
        return InMemoryRunStore()


def create_app(
    service: PlannerService | None = None,
    hooks: ExecutionHooks | None = None,
) -> FastAPI:
    planner_service = service or PlannerService()
    if hooks is not None:
        planner_service.runtime.hooks = hooks

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        planner_service.repository.initialize()
        planner_service.runtime.memory.initialize()
        planner_service.worker.start()
        app.state.planner_service = planner_service
        yield
        planner_service.worker.stop()

    app = FastAPI(title="planner-graph", lifespan=lifespan)
    include_api(app)
    return app
