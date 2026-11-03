"""The operator reads: API-02, API-07 and API-13 (VD-54); API-09, API-12, API-14 and API-17 (VD-55); API-21 (VD-57).

No auth and no SQL: ``api/operator_api.py`` (#1148) includes ``router`` under
``/v1`` with its bearer, session and role dependencies, and each route calls
one ``verdify_services.reads`` function, or ``alerts.list`` for API-14.
``connection`` lends one connection from the operator API's asyncpg pool,
``app.state.pool``; the operator API may override it with
``app.dependency_overrides``. Query values pass through unchanged: the services
validate them, and a ``ServiceError`` propagates for the operator API to render
as ``application/problem+json``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Query, Request

from verdify_services import activity, alerts, reads

router = APIRouter()


async def connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.pool.acquire() as conn:
        yield conn


Connection = Annotated[asyncpg.Connection, Depends(connection)]
# from is a Python keyword: the parameter is from_ and the query key is from.
From = Annotated[datetime, Query(alias="from")]
OptionalFrom = Annotated[datetime | None, Query(alias="from")]


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
    """Equipment now: state and since, runtime and cycles today, the grow lights' Lutron state and intent, and why."""
    return await reads.equipment(conn)


@router.get("/water/daily", operation_id="API-21", tags=["irrigation"], response_model=reads.WaterUse)
async def get_water_daily(conn: Connection, days: reads.WaterDays = 30) -> reads.WaterUse:
    """Daily water use from the site meter by line and cause, with the water that ran while no valve was open apart."""
    return await reads.water_daily(conn, days=days)


@router.get("/settings", operation_id="API-09", tags=["settings"], response_model=reads.SettingRows)
async def get_settings(conn: Connection) -> reads.SettingRows:
    """Every registry row with its device value, desired row, plan value and state, and the Band in force rows."""
    return await reads.settings_rows(conn)


@router.get(
    "/settings/{param}/history",
    operation_id="API-12",
    tags=["settings"],
    response_model=reads.SettingHistory,
)
async def get_setting_history(conn: Connection, param: str, from_: From, to: datetime) -> reads.SettingHistory:
    """Desired rows, plan values and readback steps over [from, to): the Graphs setting steps."""
    return await reads.setting_history(conn, param, from_, to)


@router.get("/alerts", operation_id="API-14", tags=["alerts"], response_model=alerts.AlertPage)
async def get_alerts(
    conn: Connection,
    status: alerts.ListStatus = "open",
    limit: int = alerts.LIMIT_DEFAULT,
    cursor: str | None = None,
) -> alerts.AlertPage:
    """Open alerts by severity then age, or recently resolved ones, repeats merged, each with its runbook."""
    return await alerts.list(conn, status, limit, cursor)


@router.get("/activity", operation_id="API-17", tags=["activity"], response_model=reads.ActivityPage)
async def get_activity(
    conn: Connection,
    source: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    from_: OptionalFrom = None,
    to: datetime | None = None,
    limit: int = activity.LIMIT_DEFAULT,
    cursor: str | None = None,
) -> reads.ActivityPage:
    """Every write by people, Iris, agents and the system, newest first, filtered and cursor-paged."""
    return await reads.activity_page(
        conn,
        source=source,
        actor=actor,
        action=action,
        target=target,
        from_=from_,
        to=to,
        limit=limit,
        cursor=cursor,
    )
