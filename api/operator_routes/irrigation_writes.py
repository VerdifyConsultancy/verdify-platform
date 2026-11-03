"""The irrigation writes: run and stop, API-IRR-06 and API-IRR-07 (#1177), and the rest of API-IRR-03 to -13 (#1156).

``api/operator_api.py`` (#1148) includes ``router`` under ``/v1`` with the bearer, session and role checks (every
method but GET and HEAD for operators only) and sets ``request.state.actor`` to the signed-in person, so this module
carries no auth of its own (``operator_routes/__init__.py`` has the rules). Each route calls one
``verdify_services.irrigation.writes`` function: the ``Idempotency-Key`` header becomes ``request_id``, a refusal
propagates as ``ServiceError`` (the operator API answers it as problem+json), and the answer is 202 with the stored
``Command``, with ``Idempotency-Replayed: true`` on a replay. Nothing here reads DASHBOARD_WRITES_ENABLED: the BFF
alone enforces it, and never on Stop. ``connection`` lends one connection from the operator API's pool,
``app.state.pool``; the operator API may override it with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request, Response

from verdify_schemas.irrigation import Command, LineId
from verdify_services.actor import Actor
from verdify_services.irrigation import writes

router = APIRouter()

# API-IRR-07 without a body stops every line.
EVERY_LINE = writes.LineCommand()


async def connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.pool.acquire() as conn:
        yield conn


def caller(request: Request) -> Actor | None:
    """The signed-in person the operator API's session check set; a missing one is refused as forbidden."""
    return getattr(request.state, "actor", None)


Connection = Annotated[asyncpg.Connection, Depends(connection)]
Caller = Annotated[Actor | None, Depends(caller)]
IdempotencyKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]


@router.post(
    "/irrigation/lines/{line_id}/run",
    operation_id="API-IRR-06",
    tags=["irrigation"],
    status_code=202,
    response_model=Command,
)
async def run_line(
    line_id: LineId,
    body: writes.RunRequest,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> Command:
    """Run now. Before the engine firmware only L01 runs, through the btn_wall_clean bridge, for its configured
    irrig_wall_duration_min; any other line is 503 unavailable and nothing is stored."""
    result = await writes.run(
        conn,
        actor,
        line_id,
        minutes=body.minutes,
        reason=body.reason,
        request_id=idempotency_key,
        prewet_s=body.prewet_s,
        flush_s=body.flush_s,
        on_s=body.on_s,
        count=body.count,
        gap_s=body.gap_s,
    )
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/irrigation/stop", operation_id="API-IRR-07", tags=["irrigation"], status_code=202, response_model=Command
)
async def stop(
    conn: Connection,
    actor: Caller,
    response: Response,
    body: writes.LineCommand = EVERY_LINE,
    idempotency_key: IdempotencyKey = None,
) -> Command:
    """Stop one line, or every line when line_id is omitted. Never blocked or deferred."""
    result = await writes.stop(conn, actor, body.line_id, reason=body.reason, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value
