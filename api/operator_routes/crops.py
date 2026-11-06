"""The Crops reads and writes: API-CROP-01 to -11 (#1327, #1330).

No auth and no SQL: ``api/operator_api.py`` includes ``router`` under ``/v1`` with its bearer, session and role checks
(every method but GET and HEAD for operators only) and sets ``request.state.actor``. Each route calls one
``verdify_services.crops`` function, the same ones the MCP planting tools call (#1328). A write's ``Idempotency-Key``
header becomes ``request_id``, a refusal propagates as ``ServiceError`` (problem+json), and the answer is the stored
planting, with ``Idempotency-Replayed: true`` on a replay.

API-CROP-06 (``GET /crops/resolve``) is the preview: what a place text means and which plantings hold those cells,
writing nothing. It is a GET so a viewer can use it and DASHBOARD_WRITES_ENABLED never blocks it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response
from pydantic import AwareDatetime

from operator_routes.irrigation_writes import Caller, IdempotencyKey
from operator_routes.reads import Connection
from verdify_schemas.crops import PlantingCreate, PlantingEnd, PlantingHarvest, PlantingMove, PlantingPatch
from verdify_services import crops

router = APIRouter()

PlantingId = Annotated[int, Path(ge=1, le=2**31 - 1)]


@router.get("/crops/locations", operation_id="API-CROP-01", tags=["crops"], response_model=crops.Locations)
async def get_locations(
    conn: Connection, zone: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,39}$")] = None
) -> crops.Locations:
    """Every zone, shelf and cell with its layout and water lines, and what each holds now."""
    return await crops.locations(conn, zone=zone)


@router.get("/crops/plantings", operation_id="API-CROP-02", tags=["crops"], response_model=crops.PlantingList)
async def get_plantings(
    conn: Connection,
    active: bool | None = True,
    zone: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,39}$")] = None,
    since: date | None = None,
    limit: Annotated[int, Query(ge=1, le=crops.LIMIT_MAX)] = 200,
) -> crops.PlantingList:
    """Plantings, active first and newest first; active=false keeps the ended ones."""
    return await crops.plantings(conn, active=active, zone=zone, since=since, limit=limit)


@router.get(
    "/crops/plantings/{planting_id}", operation_id="API-CROP-03", tags=["crops"], response_model=crops.PlantingDetail
)
async def get_planting(planting_id: PlantingId, conn: Connection) -> crops.PlantingDetail:
    """One planting with every place it has been, its events and its harvests."""
    return await crops.planting(conn, planting_id)


@router.get(
    "/crops/positions/{label}/history",
    operation_id="API-CROP-04",
    tags=["crops"],
    response_model=crops.PositionHistory,
)
async def get_position_history(
    label: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9-]{0,39}$")], conn: Connection
) -> crops.PositionHistory:
    """Who has held one cell, current first."""
    return await crops.position_history(conn, label)


@router.get("/crops/catalog", operation_id="API-CROP-05", tags=["crops"], response_model=crops.Catalog)
async def get_catalog(conn: Connection) -> crops.Catalog:
    """The crop catalog with plain names."""
    return await crops.catalog(conn)


@router.get("/crops/resolve", operation_id="API-CROP-06", tags=["crops"], response_model=crops.Resolution)
async def get_resolution(
    conn: Connection,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    at: AwareDatetime | None = None,
    planting: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
) -> crops.Resolution:
    """What a place text means and which plantings hold those cells at ``at`` (default now), leaving out
    ``planting`` (a move's own cells). Ambiguous text is 422 ambiguous_position with the candidates. Writes nothing."""
    return await crops.resolve(conn, q, at=at, planting_id=planting)


@router.post(
    "/crops/plantings", operation_id="API-CROP-07", tags=["crops"], status_code=201, response_model=crops.Planting
)
async def create_planting(
    body: PlantingCreate, conn: Connection, actor: Caller, response: Response, idempotency_key: IdempotencyKey = None
) -> crops.Planting:
    """Record a planting across the places named; a held cell is 422 position_occupied unless replace."""
    result = await crops.plant(conn, actor, body, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/crops/plantings/{planting_id}/move", operation_id="API-CROP-08", tags=["crops"], response_model=crops.Planting
)
async def move_planting(
    planting_id: PlantingId,
    body: PlantingMove,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> crops.Planting:
    """Move a planting; the old and new places both stay in its history."""
    result = await crops.move(conn, actor, planting_id, body, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/crops/plantings/{planting_id}/harvest",
    operation_id="API-CROP-09",
    tags=["crops"],
    response_model=crops.Planting,
)
async def harvest_planting(
    planting_id: PlantingId,
    body: PlantingHarvest,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> crops.Planting:
    """Record a harvest; partial unless final, which ends the planting."""
    result = await crops.harvest(conn, actor, planting_id, body, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/crops/plantings/{planting_id}/remove",
    operation_id="API-CROP-10",
    tags=["crops"],
    response_model=crops.Planting,
)
async def remove_planting(
    planting_id: PlantingId,
    body: PlantingEnd,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> crops.Planting:
    """End a planting without a harvest (failed, removed or reconciled)."""
    result = await crops.end(conn, actor, planting_id, body, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value


@router.patch(
    "/crops/plantings/{planting_id}", operation_id="API-CROP-11", tags=["crops"], response_model=crops.Planting
)
async def update_planting(
    planting_id: PlantingId,
    body: PlantingPatch,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> crops.Planting:
    """Correct a planting's variety, count (null is unknown), stage, notes or expected harvest."""
    result = await crops.update(conn, actor, planting_id, body, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value
