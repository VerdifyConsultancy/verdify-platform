"""Graph reads: API-03 to API-06, the band preview API-19, the lighting days API-22 and the band editor's preview API-23
(architecture §7 and §8, #1334). No auth and no SQL here."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from verdify_services import band_profile, lighting
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
    "/lighting",
    operation_id="API-22",
    summary="The grow lights by day: on time, starts, the policy in force and qualified light minutes against target",
    response_model=lighting.LightingDays,
)
async def lighting_days(conn: Connection, from_: WindowStart, to: WindowEnd) -> lighting.LightingDays:
    return await lighting.lighting_days(conn, from_, to)


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
    achievable: Annotated[
        bool, Query(description="The house's achievable VPD band and its nightly grades (#1433); windows up to 35 days")
    ] = False,
) -> service.BandTimeline:
    return await service.band(conn, from_, to, zone, achievable=achievable)


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


def _field(description: str, minimum: float | None = None) -> object:
    return Query(description=description, ge=minimum)


@router.get(
    "/band/profile",
    operation_id="API-23",
    tags=["settings"],
    summary="The house band as stored and as drafted in the band editor, over one day, before Save",
    response_model=band_profile.BandProfilePreview,
)
async def band_profile_preview(
    conn: Connection,
    temp_day: Annotated[float | None, _field("Temperature target at solar noon, °F")] = None,
    temp_night: Annotated[float | None, _field("Temperature target at solar midnight, °F")] = None,
    temp_width_day: Annotated[float | None, _field("± °F around the day target", 0)] = None,
    temp_width_night: Annotated[float | None, _field("± °F around the night target", 0)] = None,
    vpd_day: Annotated[float | None, _field("VPD target at solar noon, kPa")] = None,
    vpd_night: Annotated[float | None, _field("VPD target at solar midnight, kPa")] = None,
    vpd_below_day: Annotated[float | None, _field("kPa below the day target", 0)] = None,
    vpd_above_day: Annotated[float | None, _field("kPa above the day target", 0)] = None,
    vpd_below_night: Annotated[float | None, _field("kPa below the night target", 0)] = None,
    vpd_above_night: Annotated[float | None, _field("kPa above the night target", 0)] = None,
    anchor: Annotated[list[str] | None, Query(description="Advanced: a raw house anchor as param:value")] = None,
    day: Annotated[date | None, Query(description="America/Denver day; default today")] = None,
) -> band_profile.BandProfilePreview:
    patch = band_profile.BandProfilePatch(
        temp_day=temp_day,
        temp_night=temp_night,
        temp_width_day=temp_width_day,
        temp_width_night=temp_width_night,
        vpd_day=vpd_day,
        vpd_night=vpd_night,
        vpd_below_day=vpd_below_day,
        vpd_above_day=vpd_above_day,
        vpd_below_night=vpd_below_night,
        vpd_above_night=vpd_above_night,
    )
    overrides = band_profile.parse_anchor_overrides(anchor or [])
    return await band_profile.preview(conn, patch, overrides, day)
