"""Graph reads: API-03 to API-06, and the band preview API-19 (architecture §7 and §8). No auth and no SQL here."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from verdify_services import series as service

from .reads import connection

router = APIRouter(tags=["graphs"])

Connection = Annotated[asyncpg.Connection, Depends(connection)]
WindowStart = Annotated[AwareDatetime, Query(alias="from", description="Window start (inclusive)")]
WindowEnd = Annotated[AwareDatetime, Query(description="Window end (exclusive)")]


@router.get(
    "/metrics",
    operation_id="API-03",
    summary="Metric registry over every numeric climate column, controller vitals and the daily scorecard keys",
    response_model=service.MetricList,
)
async def metrics() -> service.MetricList:
    return service.metric_list()


@router.get(
    "/series",
    operation_id="API-04",
    summary="Downsampled series for uPlot",
    response_model=service.Series,
)
async def series(
    conn: Connection,
    from_: WindowStart,
    to: WindowEnd,
    metric: Annotated[
        list[str],
        Query(
            min_length=1, max_length=service.MAX_SERIES_KEYS, description="Metric key from API-03; repeat for up to 8"
        ),
    ],
    points: Annotated[int, Query(ge=service.POINTS_MIN, le=service.POINTS_MAX)] = service.POINTS_DEFAULT,
) -> service.Series:
    return await service.series(conn, metric, from_, to, points)


@router.get(
    "/states",
    operation_id="API-05",
    summary="Equipment on/off intervals for the lanes",
    response_model=service.StateLanes,
)
async def states(
    conn: Connection,
    from_: WindowStart,
    to: WindowEnd,
    equipment: Annotated[list[str] | None, Query(description="Only these lanes; default all")] = None,
) -> service.StateLanes:
    return await service.states(conn, from_, to, equipment)


@router.get(
    "/band",
    operation_id="API-06",
    summary="Band in force and the reconstructed crop band, for the house or one zone",
    response_model=service.BandTimeline,
)
async def band(
    conn: Connection,
    from_: WindowStart,
    to: WindowEnd,
    zone: Annotated[service.BandZone, Query(description="North has no zone band and is not offered")] = "house",
) -> service.BandTimeline:
    return await service.band(conn, from_, to, zone)


@router.get(
    "/band/preview",
    operation_id="API-19",
    tags=["settings"],
    summary="Preview a crop-band edit as the day's diurnal curve, before Save",
    response_model=service.BandPreview,
)
async def band_preview(
    conn: Connection,
    param: Annotated[str, Query(description="A crop-band anchor or width")],
    value: Annotated[float, Query(description="The draft value, in the setting's unit")],
    day: Annotated[date | None, Query(description="America/Denver day; default today")] = None,
) -> service.BandPreview:
    return await service.band_preview(conn, param, value, day)
