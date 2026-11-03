"""The irrigation writes: API-IRR-03 to -08, -12 and -13 (#1177, #1156).

``api/operator_api.py`` (#1148) includes ``router`` under ``/v1`` with the bearer, session and role checks (every
method but GET and HEAD for operators only) and sets ``request.state.actor`` to the signed-in person, so this module
carries no auth of its own (``operator_routes/__init__.py`` has the rules). Each route calls one
``verdify_services.irrigation.writes`` function: the ``Idempotency-Key`` header becomes ``request_id``, a refusal
propagates as ``ServiceError`` (the operator API answers it as problem+json), and the answer is the stored object,
with ``Idempotency-Replayed: true`` on a replay. The one rule that lives here is REST's ``manual`` default: a create
or a definition edit that omits it stores true (R3); the services never default it. Nothing here reads
DASHBOARD_WRITES_ENABLED: the BFF alone enforces it, and never on Stop. ``connection`` lends one connection from the
operator API's pool, ``app.state.pool``; the operator API may override it with ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from verdify_schemas.activity import REASON_MAX_LENGTH
from verdify_schemas.irrigation import Command, LineId
from verdify_services.actor import Actor
from verdify_services.irrigation import writes
from verdify_services.irrigation.reads import Schedule

router = APIRouter()

# API-IRR-07 and -13 without a body act on every line.
EVERY_LINE = writes.LineCommand()
# Fields of a line update that never change manual on their own (architecture §5.6).
_NEVER_MANUAL = frozenset({"paused_until", "checked", "check_note"})


async def connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.pool.acquire() as conn:
        yield conn


def caller(request: Request) -> Actor | None:
    """The signed-in person the operator API's session check set; a missing one is refused as forbidden."""
    return getattr(request.state, "actor", None)


Connection = Annotated[asyncpg.Connection, Depends(connection)]
Caller = Annotated[Actor | None, Depends(caller)]
IdempotencyKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]
SlotId = Annotated[int, Path(ge=writes.SLOT_ID_RANGE[0], le=writes.SLOT_ID_RANGE[1])]


def create_manual(body: writes.ScheduleCreate) -> bool:
    """A REST create that omits ``manual`` stores true (R3)."""
    return body.manual if "manual" in body.model_fields_set else True


def schedule_edit_manual(body: writes.ScheduleInput) -> bool | None:
    """Any API-IRR-04 field but ``reason`` is a definition edit, which stores true when it omits ``manual``."""
    carried = body.model_fields_set
    if "manual" in carried:
        return body.manual
    return True if carried - {"reason"} else None


def line_edit_manual(line_id: str, body: writes.LineUpdate) -> bool | None:
    """API-IRR-08: a body with a definition field stores ``manual``, true when omitted. A pause, a resume, the check
    or a reason passes none, so the stored flag stays, even with ``manual`` beside them. ``all`` passes what it was
    sent, and the service refuses it: ``all`` takes only ``paused_until`` and ``reason``."""
    carried = body.model_fields_set
    if carried & writes.LINE_DEFINITION_FIELDS:
        return body.manual if "manual" in carried else True
    if "manual" in carried and (line_id == "all" or not carried & _NEVER_MANUAL):
        return body.manual
    return None


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
    """Run now, or be refused; nothing is queued. Before the engine firmware only L01 runs, through the
    btn_wall_clean bridge, for its configured irrig_wall_duration_min; any other line is 503 unavailable and nothing
    is stored."""
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


@router.post(
    "/irrigation/schedules", operation_id="API-IRR-03", tags=["irrigation"], status_code=201, response_model=Schedule
)
async def create_schedule(
    body: writes.ScheduleCreate,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> Schedule:
    """Create a schedule. Before the engine firmware it is stored for the first push."""
    result = await writes.schedule_create(
        conn, actor, body, manual=create_manual(body), reason=body.reason, request_id=idempotency_key
    )
    response.headers.update(result.rest_headers())
    return result.value


@router.put("/irrigation/schedules/{slot_id}", operation_id="API-IRR-04", tags=["irrigation"], response_model=Schedule)
async def update_schedule(
    slot_id: SlotId,
    body: writes.ScheduleInput,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> Schedule:
    """Edit, enable or disable a schedule, or set manual. Omitted fields keep their value."""
    result = await writes.schedule_update(
        conn, actor, slot_id, body, manual=schedule_edit_manual(body), reason=body.reason, request_id=idempotency_key
    )
    response.headers.update(result.rest_headers())
    return result.value


@router.delete(
    "/irrigation/schedules/{slot_id}",
    operation_id="API-IRR-05",
    tags=["irrigation"],
    status_code=204,
    response_model=None,
)
async def delete_schedule(
    slot_id: SlotId,
    conn: Connection,
    actor: Caller,
    response: Response,
    reason: Annotated[str | None, Query(max_length=REASON_MAX_LENGTH)] = None,
    idempotency_key: IdempotencyKey = None,
) -> None:
    """Delete a schedule; its slot_id is never reused."""
    result = await writes.schedule_delete(conn, actor, slot_id, reason=reason, request_id=idempotency_key)
    response.headers.update(result.rest_headers())


@router.put(
    "/irrigation/lines/{line_id}",
    operation_id="API-IRR-08",
    tags=["irrigation"],
    response_model=writes.LinesUpdated,
)
async def update_line(
    line_id: Annotated[str, Path(pattern=r"^(L(0[1-9]|1[01])|all)$")],
    body: writes.LineUpdate,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> writes.LinesUpdated:
    """Enable or disable a line with its check; VPD and Schedule flags on misters; caps; pause or resume; manual.
    line_id all takes only paused_until and reason."""
    result = await writes.line_update(
        conn,
        actor,
        line_id,
        body,
        manual=line_edit_manual(line_id, body),
        reason=body.reason,
        request_id=idempotency_key,
    )
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/equipment/override",
    operation_id="API-IRR-12",
    tags=["irrigation", "equipment"],
    status_code=202,
    response_model=Command,
)
async def equipment_override(
    body: writes.OverrideRequest,
    conn: Connection,
    actor: Caller,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> Command:
    """A burst, or clear or restart, through manual_override. Before the engine firmware: 503 unavailable."""
    result = await writes.override(
        conn, actor, body.kind, minutes=body.minutes, reason=body.reason, request_id=idempotency_key
    )
    response.headers.update(result.rest_headers())
    return result.value


@router.post(
    "/irrigation/clear-fault", operation_id="API-IRR-13", tags=["irrigation"], status_code=202, response_model=Command
)
async def clear_fault(
    conn: Connection,
    actor: Caller,
    response: Response,
    body: writes.LineCommand = EVERY_LINE,
    idempotency_key: IdempotencyKey = None,
) -> Command:
    """Clear a mister's watchdog latch, or every latch when line_id is omitted. Before the engine firmware: 503
    unavailable."""
    result = await writes.clear_fault(conn, actor, body.line_id, reason=body.reason, request_id=idempotency_key)
    response.headers.update(result.rest_headers())
    return result.value
