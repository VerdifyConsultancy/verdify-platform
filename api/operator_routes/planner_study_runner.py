"""The planner study's boundary runner (#1360): a sweep in the operator API, like ``light_timer``. No routes, no SQL.

Every ``SWEEP_S`` each operator API pod runs ``verdify_services.planner_study.locked_sweep`` for each defined study:
under the study's advisory lock it reads the run from its ``study.*`` activity rows, holds the 48 planner settings
manual at the baseline vector on a baseline day, releases its own holds otherwise (an AI day, a pause, a stop or the
end), and records the day's boundary once the settings read back, or a deviation after the confirm window. The run is
the rows, so a restart or a rollout loses nothing, and each write's request id is derived from the run, day and field,
so a repeated sweep replays instead of writing twice. A study with no run costs one small read per sweep.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from verdify_schemas.planner_study import STUDY_FILES
from verdify_services import planner_study

log = logging.getLogger(__name__)

SWEEP_S = 60.0  # a boundary, pause or stop takes effect within about this long


class Runner:
    """The sweeps in one operator API process; the last outcome logged per study, so a steady state logs once."""

    def __init__(self) -> None:
        self.logged: dict[str, tuple[object, ...]] = {}

    async def run(self, pool: asyncpg.Pool, interval_s: float = SWEEP_S) -> None:
        """The lifespan task: a sweep every ``interval_s``, the first one ``interval_s`` after the start. A failed sweep
        is logged and the next one goes on."""
        while True:
            await asyncio.sleep(interval_s)
            for study_id in STUDY_FILES:
                try:
                    await self.once(pool, study_id)
                except asyncio.CancelledError:
                    raise
                except Exception as failure:
                    log.warning(
                        "Planner study: the %s sweep failed (%s: %s)", study_id, type(failure).__name__, failure
                    )

    async def once(self, pool: asyncpg.Pool, study_id: str) -> planner_study.SweepResult | None:
        async with pool.acquire() as conn:
            result = await planner_study.locked_sweep(conn, study_id)
        if result is None:
            return None
        state = (result.arm, result.held, result.released, tuple(result.foreign), result.boundary)
        if result.held or result.released or result.boundary or self.logged.get(study_id) != state:
            log.info(
                "Planner study %s: arm=%s held=%d released=%d held_by_others=%s boundary=%s",
                study_id,
                result.arm,
                result.held,
                result.released,
                ",".join(result.foreign) or "-",
                result.boundary or "-",
            )
            self.logged[study_id] = state
        return result
