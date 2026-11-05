"""API-IRR-01, -02, -10 and -11: the irrigation reads (VD-40, #1155).

No auth and no SQL: ``api/operator_api.py`` (#1148) includes ``router`` under ``/v1`` with its bearer, session and
role checks, and each route calls one ``verdify_services.irrigation.reads`` function, the same ones the MCP read tools
call (#1173). ``request.state.actor`` is the signed-in person the session check set.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import AwareDatetime

from operator_routes.reads import Connection
from verdify_schemas.irrigation import IrrigationCause, RunOutcome, RunPurpose
from verdify_services.irrigation import reads as irrigation

router = APIRouter()

LineFilter = Annotated[str | None, Query(pattern=r"^L(0[1-9]|1[01])$")]


@router.get("/irrigation", operation_id="API-IRR-01", tags=["irrigation"], response_model=irrigation.IrrigationStatus)
async def get_status(conn: Connection, request: Request) -> irrigation.IrrigationStatus:
    """Lines, active runs, program sync and the engine flag, minutes today, limits, leak and occupancy."""
    return await irrigation.status(conn, request.state.actor)


@router.get(
    "/irrigation/schedules", operation_id="API-IRR-02", tags=["irrigation"], response_model=irrigation.ScheduleAgenda
)
async def get_agenda(
    conn: Connection,
    request: Request,
    days: Annotated[int, Query(ge=1, le=7)] = 7,
    past_days: Annotated[int, Query(ge=0, le=7)] = 0,
) -> irrigation.ScheduleAgenda:
    """Schedules with their sync, the week planned against actual, bounds per line kind and solar times."""
    return await irrigation.agenda(conn, request.state.actor, days=days, past_days=past_days)


@router.get(
    "/irrigation/minutes", operation_id="API-IRR-10", tags=["irrigation"], response_model=irrigation.MinutesReport
)
async def get_minutes(
    conn: Connection,
    request: Request,
    from_: Annotated[date, Query(alias="from")],
    to: date,
    group_by: irrigation.GroupBy = "zone",
    purpose: RunPurpose | None = None,
) -> irrigation.MinutesReport:
    """Minutes, pulses and gallons per day by zone, line or cause, up to 400 days; purpose keeps irrigation or climate
    (VPD and fallback misting, and fog) only."""
    return await irrigation.minutes(
        conn, request.state.actor, from_, to, group_by, purpose=None if purpose is None else purpose.value
    )


@router.get("/irrigation/runs", operation_id="API-IRR-11", tags=["irrigation"], response_model=irrigation.RunPage)
async def get_runs(
    conn: Connection,
    request: Request,
    from_: Annotated[AwareDatetime, Query(alias="from")],
    to: AwareDatetime,
    line: LineFilter = None,
    cause: IrrigationCause | None = None,
    outcome: RunOutcome | None = None,
    limit: Annotated[int, Query(ge=1, le=irrigation.LIMIT_MAX)] = irrigation.LIMIT_DEFAULT,
    cursor: str | None = None,
    purpose: RunPurpose | None = None,
) -> irrigation.RunPage:
    """The run ledger, newest first; windows up to 31 days. purpose keeps irrigation runs or climate runs (VPD and
    fallback misting, and fog) only; none keeps both."""
    return await irrigation.runs(
        conn,
        request.state.actor,
        from_,
        to,
        line=line,
        cause=None if cause is None else cause.value,
        outcome=None if outcome is None else outcome.value,
        limit=limit,
        cursor=cursor,
        purpose=None if purpose is None else purpose.value,
    )
