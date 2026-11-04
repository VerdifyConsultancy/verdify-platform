"""The operator API's write routes: settings and alerts, API-10, API-11, API-15 and API-16 (#1150); the grow lights,
API-20 (#1170).

Each settings and alert route runs one ``verdify_services`` call, which commits the domain row and its ``activity_log``
row in one transaction: a ``desired_state`` row, plus the ``crop_band_anchors`` rows of a crop-band setting, or the
``alert_log`` updates on every repeat merged into the row. The ``desired_state`` insert trigger wakes the dispatcher, so
no route sends a NOTIFY. Bounds, snapping and every refusal live in the services; a ``ServiceError`` propagates and the
operator API renders it as ``application/problem+json``. API-20 calls ``verdify-setpoint-server`` between two service
calls, outside any database transaction (``setpoint_server.command``). An API-10 write of ``sw_gl_main_auto_mode`` or
``sw_gl_grow_auto_mode`` = 1 also starts a task that matches the light to the controller once the write confirms
(``light_resync``, #1300), so Back to automatic finishes when the page that pressed it has closed.

No auth and no SQL (``operator_routes/__init__.py`` has the rules). ``api/operator_api.py`` (#1148) includes ``router``
under ``/v1`` with the bearer, session and role checks, so a viewer's write is 403 before a route runs, and the session
check sets ``request.state.actor``, the only source of who writes: no body, query or header value names the actor. The
operator API never reads ``DASHBOARD_WRITES_ENABLED``, which gates only the BFF.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat

from operator_routes import light_resync, setpoint_server
from operator_routes.reads import Connection
from verdify_schemas.activity import REASON_MAX_LENGTH
from verdify_services import alerts, lights, settings
from verdify_services.activity import WriteResult
from verdify_services.problems import ServiceError, problem

router = APIRouter()

# The text form the BFF also requires. uuid.UUID alone would also take braces, a urn: prefix and bare hex.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


async def idempotency_key(
    request: Request, key: Annotated[str | None, Header(alias="Idempotency-Key")] = None
) -> UUID | None:
    """The write's ``request_id``: the ``Idempotency-Key`` header, a uuid; any other format is 400 ``invalid_request``.

    The service stores it on the ``activity_log`` row, and it becomes this request's id in the log line and in
    ``Problem.request_id``. A replay returns the stored object and the same key with another payload is 422
    ``request_id_reused``, both from the service. Without the header the write runs once with ``request_id`` null.
    """
    if key is None:
        return None
    if _UUID.fullmatch(key) is None:
        raise ServiceError(problem("invalid_request", "Idempotency-Key must be a UUID", field="Idempotency-Key"))
    parsed = UUID(key)
    request.state.request_id = str(parsed)
    return parsed


IdempotencyKey = Annotated[UUID | None, Depends(idempotency_key)]


class SettingWrite(BaseModel):
    """API-10's body (OpenAPI ``SettingWrite``). A number or a boolean only, never text that reads as one. NaN and
    infinity pass here for the service to refuse as ``out_of_range``, and any other key, a misspelt ``manual`` or an
    actor, is 400."""

    model_config = ConfigDict(extra="forbid")

    value: StrictFloat
    # The manual default lives here (Round 2 item 8): the service stores exactly the flag it receives.
    manual: StrictBool = True
    reason: str = Field(default=None, max_length=REASON_MAX_LENGTH)


class AlertAck(BaseModel):
    """API-15's optional body."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(default=None, max_length=REASON_MAX_LENGTH)


class AlertResolve(BaseModel):
    """API-16's optional body."""

    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(default=None, max_length=REASON_MAX_LENGTH)


class LightSwitch(BaseModel):
    """API-20's optional body."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default=None, max_length=REASON_MAX_LENGTH)


NO_NOTE = AlertAck()
NO_RESOLUTION = AlertResolve()
NO_REASON = LightSwitch()


def _answer(result: WriteResult) -> JSONResponse:
    """The stored object as the REST JSON, with ``Idempotency-Replayed: true`` on a replay."""
    return JSONResponse(result.rest_json(), headers=result.rest_headers())


@router.put("/settings/{param}", operation_id="API-10", tags=["settings"], response_model=settings.DesiredSetting)
async def write_setting(
    key: IdempotencyKey, conn: Connection, request: Request, param: str, body: SettingWrite
) -> JSONResponse:
    """Validate and snap ``value``, then store its ``desired_state`` row with the ``manual`` flag it carries. A grow
    light's automatic switch set to 1 also starts its Back to automatic resync (``light_resync``)."""
    actor = request.state.actor
    written = await settings.write(conn, actor, param, body.value, body.manual, body.reason, key)
    light_resync.start(request.app, actor, written)
    return _answer(written)


@router.delete("/settings/{param}", operation_id="API-11", tags=["settings"], response_model=settings.DesiredSetting)
async def release_setting(
    key: IdempotencyKey,
    conn: Connection,
    request: Request,
    param: str,
    reason: Annotated[str | None, Query(max_length=REASON_MAX_LENGTH)] = None,
) -> JSONResponse:
    """Release to auto: append ``value NULL, manual false``, so the plan, band or default applies again."""
    return _answer(await settings.release(conn, request.state.actor, param, reason, key))


@router.post("/alerts/{id}/ack", operation_id="API-15", tags=["alerts"], response_model=alerts.Alert)
async def acknowledge_alert(
    key: IdempotencyKey, conn: Connection, request: Request, id: int, body: AlertAck = NO_NOTE
) -> JSONResponse:
    """Acknowledge every unacknowledged repeat merged into the row, under the person's name; returns the merged row."""
    return _answer(await alerts.ack(conn, request.state.actor, id, body.note, key))


@router.post("/alerts/{id}/resolve", operation_id="API-16", tags=["alerts"], response_model=alerts.Alert)
async def resolve_alert(
    key: IdempotencyKey, conn: Connection, request: Request, id: int, body: AlertResolve = NO_RESOLUTION
) -> JSONResponse:
    """Resolve every unresolved repeat merged into the row, under the person's name; returns the merged row. Never
    refused for its type: MCP's adapter alone keeps the retrieval check for ``planner_tool_dependency_failed``."""
    return _answer(await alerts.resolve(conn, request.state.actor, id, body.resolution, key))


def _light_answer(request: Request, written: WriteResult[lights.LightRecord]) -> JSONResponse:
    """200 ``LightCommand`` when Lutron reached the state, else the 503 Problem with its ``result``; a replay of either
    carries ``Idempotency-Replayed: true``, which an exception handler could not add."""
    refusal = written.value.refusal()
    if refusal is None:
        return JSONResponse(written.value.command().model_dump(mode="json"), headers=written.rest_headers())
    stamped = refusal.model_copy(update={"request_id": getattr(request.state, "request_id", None)})
    return JSONResponse(
        stamped.body(),
        status_code=stamped.status,
        media_type="application/problem+json",
        headers=written.rest_headers(),
    )


@router.post(
    "/equipment/lights/{light}/{state}", operation_id="API-20", tags=["equipment"], response_model=lights.LightCommand
)
async def switch_light(
    key: IdempotencyKey,
    conn: Connection,
    request: Request,
    light: lights.Light,
    state: lights.LightState,
    body: LightSwitch = NO_REASON,
) -> JSONResponse:
    """Turn a grow light on or off through verdify-setpoint-server, with one ``activity_log`` row for the attempt. A
    replayed key returns the stored result, a 503 included, and calls nothing, so a retry after a 503 sends a new key."""
    url = request.app.state.settings.setpoint_server_url
    written = await setpoint_server.command(conn, request.state.actor, url, light, state, body.reason, key)
    return _light_answer(request, written)
