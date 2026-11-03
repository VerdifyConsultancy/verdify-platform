"""API-02, API-07 and API-13: the Now, plan and equipment reads (VD-54).

No auth and no SQL: ``api/operator_api.py`` (#1148) includes ``router`` under
``/v1`` with its bearer, session and role dependencies, and each route calls
one ``verdify_services.reads`` function. ``connection`` lends one connection
from the operator API's asyncpg pool, ``app.state.pool``; the operator API may
override it with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Request

from verdify_services import reads

router = APIRouter()


async def connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.pool.acquire() as conn:
        yield conn


Connection = Annotated[asyncpg.Connection, Depends(connection)]


@router.get("/now", operation_id="API-02", tags=["now"], response_model=reads.Now)
async def get_now(conn: Connection) -> reads.Now:
    """Now hero: climate against the band, mode, Iris, running equipment, the leak, alerts, freshness, writer."""
    return await reads.now(conn)


@router.get("/plan", operation_id="API-07", tags=["plan"], response_model=reads.Plan)
async def get_plan(conn: Connection, hours: reads.PlanHours = 24) -> reads.Plan:
    """Plan in force, one lane per planned setting over 24 h (or 72 h), manual settings and planner health."""
    return await reads.plan(conn, hours=hours)


@router.get("/equipment", operation_id="API-13", tags=["equipment"], response_model=reads.Equipment)
async def get_equipment(conn: Connection) -> reads.Equipment:
    """Equipment now: state and since, runtime and cycles today, the grow lights' Lutron state and intent."""
    return await reads.equipment(conn)
