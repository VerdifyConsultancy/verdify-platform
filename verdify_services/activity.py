"""The activity log and the one write convention (architecture §5.3, §5.6, §6 and §9).

- ``append`` writes one ``activity_log`` row, validated through ``verdify_schemas.activity.ActivityLogRow``.
- ``list`` reads the log newest first, cursor-paged (API-17 and the MCP ``activity`` tool).
- ``recorded_write`` is how every write runs: the actor check, the request hash, the replay of a retried
  request, the experiment stamp, then the domain row and its activity row in one transaction.

verdify_services/README.md has the conventions on one page. This module defines ``list``, so it keeps
annotations eager (no postponed evaluation) and builds its models before that name exists.
"""

import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic_core import to_jsonable_python

from verdify_schemas.activity import ACTOR_SOURCE_PATTERN, REASON_MAX_LENGTH, ActivityLogRow
from verdify_services.actor import Actor, ActorRef
from verdify_services.problems import ServiceError, problem

GREENHOUSE_ID = "vallery"
LIMIT_DEFAULT = 50
LIMIT_MAX = 200
# UNIQUE (source, request_id) on activity_log (migration 280): a request id applies once per source.
REQUEST_KEY_CONSTRAINT = "activity_log_source_request_id_key"
_BIGINT_MAX = 2**63 - 1
ARMED_EXPERIMENT_SQL = "SELECT experiment_id FROM public.fn_mcp_armed_assignment_context()"
_COLUMNS = (
    "id, greenhouse_id, ts, source, actor, action, target, detail::text AS detail, reason, "
    "request_id, request_sha256, experiment_id"
)
_INSERT = (
    "INSERT INTO public.activity_log (greenhouse_id, source, actor, action, target, detail, reason, "
    "request_id, request_sha256, experiment_id) "
    "VALUES ($1, $2, $3, $4, $5, $6::text::jsonb, $7, $8, $9, $10) RETURNING id"
)
_SOURCE = re.compile(ACTOR_SOURCE_PATTERN)

# The OpenAPI ReadbackState (architecture §5.5).
ReadbackState = Literal["requested", "confirmed", "not_confirmed", "held", "no_readback", "overridden", "released"]


class ActivityEntry(BaseModel):
    """One ``activity_log`` row as API-17 and the MCP ``activity`` tool return it (OpenAPI ``ActivityEntry``).

    ``state`` and ``confirmed_at`` stay None here; the settings service fills them for setting writes.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    ts: AwareDatetime
    actor: ActorRef
    action: str
    target: str
    detail: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    state: ReadbackState | None = None
    confirmed_at: AwareDatetime | None = None
    experiment_id: UUID | None = None
    request_id: UUID | None = None


class ActivityPage(BaseModel):
    """Entries newest first (OpenAPI ``ActivityPage``); ``next_cursor`` is None on the last page."""

    model_config = ConfigDict(frozen=True)

    items: list[ActivityEntry]
    next_cursor: str | None = None


class WriteResult[T](BaseModel):
    """A write's answer: the stored object, and whether the request replayed an earlier one."""

    model_config = ConfigDict(frozen=True)

    value: T
    replayed: bool = False

    def rest_json(self) -> Any:
        """The REST response body: the stored object's JSON."""
        return self.value.model_dump(mode="json") if isinstance(self.value, BaseModel) else self.value

    def rest_headers(self) -> dict[str, str]:
        """``Idempotency-Replayed: true`` on a replay."""
        return {"Idempotency-Replayed": "true"} if self.replayed else {}

    def mcp_payload(self) -> Any:
        """The REST JSON, with a top-level ``replayed: true`` on a replay (MCP has no headers)."""
        body = self.rest_json()
        if not self.replayed:
            return body
        if not isinstance(body, dict):
            raise TypeError("a replayed write's REST JSON must be an object")
        return {**body, "replayed": True}


def _jsonable(value: Any) -> Any:
    """What ``json`` cannot write, written as Pydantic's JSON mode writes it, so a request held in a dict
    and the model it validates into hash alike (a UTC time is ``...Z`` in both, never ``+00:00``)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime | date | time | UUID):
        return to_jsonable_python(value)
    raise TypeError(f"{type(value).__name__} is not JSON")


def _json(value: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, no NaN or infinity (ValueError)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_jsonable)


def canonical_request(op: str, target: str, request: Any) -> bytes:
    """The canonical JSON of ``{"op": op, "target": target, "request": request}``.

    ``op`` is the REST operation id; an MCP tool passes its REST twin's id and that operation's ``request``
    (every input ``target`` does not carry), so both surfaces hash one request the same way.
    """
    try:
        return _json({"op": op, "target": target, "request": request}).encode("ascii")
    except ValueError as exc:
        raise ServiceError(problem("invalid_request", "The request holds a number that is not finite")) from exc


def request_hash(op: str, target: str, request: Any) -> bytes:
    """``activity_log.request_sha256``: the SHA-256 of ``canonical_request``."""
    return hashlib.sha256(canonical_request(op, target, request)).digest()


def _require_writer(actor: Actor | None) -> None:
    if not isinstance(actor, Actor):
        raise ServiceError(
            problem("forbidden", "A write needs a signed-in operator, Iris, a named agent or the system")
        )
    if actor.role == "viewer":
        raise ServiceError(problem("forbidden", "A viewer cannot change anything"))


def _request_key(request_id: UUID | str | None) -> UUID | None:
    if request_id is None or isinstance(request_id, UUID):
        return request_id
    try:
        return UUID(str(request_id))
    except ValueError as exc:
        raise ServiceError(problem("invalid_request", "request_id must be a UUID", field="request_id")) from exc


def _row(record: Mapping[str, Any]) -> ActivityLogRow:
    return ActivityLogRow.model_validate({**dict(record), "detail": json.loads(record["detail"])})


def _entry(row: ActivityLogRow) -> ActivityEntry:
    return ActivityEntry(
        id=row.id,
        ts=row.ts,
        actor=ActorRef(source=row.source, name=row.actor),
        action=row.action,
        target=row.target,
        detail=row.detail,
        reason=row.reason,
        experiment_id=row.experiment_id,
        request_id=row.request_id,
    )


def _encode_cursor(ts: datetime, row_id: int) -> str:
    raw = json.dumps([ts.isoformat(), row_id], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts_text, row_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        ts = datetime.fromisoformat(ts_text)
        if (
            ts.utcoffset() is None
            or isinstance(row_id, bool)
            or not isinstance(row_id, int)
            or not 1 <= row_id <= _BIGINT_MAX  # activity_log.id: a forged id must not reach the bind
        ):
            raise ValueError("not a cursor")
    except (TypeError, ValueError) as exc:  # binascii.Error, JSONDecodeError and UnicodeDecodeError included
        raise ServiceError(
            problem("invalid_request", "cursor is not a next_cursor of this list", field="cursor")
        ) from exc
    return ts, row_id


async def append(
    conn: asyncpg.Connection,
    actor: Actor,
    *,
    action: str,
    target: str,
    detail: Mapping[str, Any] | None = None,
    reason: str | None = None,
    request_id: UUID | None = None,
    request_sha256: bytes | None = None,
    experiment_id: UUID | None = None,
) -> int:
    """Append one ``activity_log`` row for ``actor`` and return its id.

    Actions follow architecture §9: ``setting.write``, ``setting.release``, ``plan.write``, ``alert.ack``,
    ``alert.resolve``, ``irrigation.*``, ``light.on`` and ``light.off``. A write with a request id goes through
    ``recorded_write``, which stores the id and the request hash together.
    """
    _require_writer(actor)
    row = ActivityLogRow(
        greenhouse_id=GREENHOUSE_ID,
        source=actor.source,
        actor=actor.name,
        action=action,
        target=target,
        detail=dict(detail or {}),
        reason=reason,
        request_id=request_id,
        request_sha256=request_sha256,
        experiment_id=experiment_id,
    )
    return await conn.fetchval(
        _INSERT,
        row.greenhouse_id,
        row.source,
        row.actor,
        row.action,
        row.target,
        _json(row.detail),
        row.reason,
        row.request_id,
        row.request_sha256,
        row.experiment_id,
    )


async def recorded_write(
    conn: asyncpg.Connection,
    actor: Actor,
    *,
    op: str,
    action: str,
    target: str,
    request: Any,
    request_id: UUID | str | None,
    reason: str | None,
    perform: Callable[[asyncpg.Connection], Awaitable[tuple[Any, Mapping[str, Any]]]],
    load: Callable[[asyncpg.Connection, ActivityLogRow], Awaitable[Any]],
) -> WriteResult:
    """Run one write: the domain row from ``perform`` and its ``activity_log`` row commit together or not at all.

    1. A missing actor (or a viewer) is refused with ``forbidden``.
    2. The request hash is ``request_hash(op, target, request)``. ``request`` holds every input ``target``
       does not carry (the body, plus a path or query value such as API-20's ``{state}``), so two writes
       that differ only there never replay each other.
    3. In one transaction: when ``request_id`` is set and (``actor.source``, ``request_id``) already has a row,
       the same hash returns ``WriteResult(value=await load(conn, row), replayed=True)`` and appends nothing;
       another hash raises ``request_id_reused`` (422, not retryable).
    4. ``experiment_id`` comes from ``fn_mcp_armed_assignment_context()``: None when nothing is armed. Nothing
       is refused because an experiment is armed.
    5. ``value, detail = await perform(conn)`` writes the domain row; ``detail`` carries ``ref: {table, id}``
       so a replay can ``load`` it. Then ``append`` stores the activity row, and the transaction commits.
    6. A concurrent duplicate that hits UNIQUE (source, request_id) rolls back and runs step 3 again in a
       new transaction, where it replays (or is refused).

    Call it outside a transaction: it opens its own.
    """
    _require_writer(actor)
    if reason is not None and len(reason) > REASON_MAX_LENGTH:
        raise ServiceError(
            problem("invalid_request", f"reason is at most {REASON_MAX_LENGTH} characters", field="reason")
        )
    if reason is not None and "\x00" in reason:  # PostgreSQL text cannot hold it
        raise ServiceError(problem("invalid_request", "reason holds a NUL character", field="reason"))
    key = _request_key(request_id)
    digest = request_hash(op, target, request)
    if conn.is_in_transaction():
        raise RuntimeError("recorded_write opens its own transaction; call it outside one")
    retried = False
    while True:
        try:
            async with conn.transaction():
                if key is not None:
                    stored = await conn.fetchrow(
                        f"SELECT {_COLUMNS} FROM public.activity_log WHERE source = $1 AND request_id = $2",
                        actor.source,
                        key,
                    )
                    if stored is not None:
                        row = _row(stored)
                        if row.request_sha256 != digest:
                            raise ServiceError(
                                problem(
                                    "request_id_reused",
                                    "This request id was already used for a different request; send a new one",
                                    field="request_id",
                                )
                            )
                        return WriteResult(value=await load(conn, row), replayed=True)
                experiment_id = await conn.fetchval(ARMED_EXPERIMENT_SQL)
                value, detail = await perform(conn)
                await append(
                    conn,
                    actor,
                    action=action,
                    target=target,
                    detail=detail,
                    reason=reason,
                    request_id=key,
                    request_sha256=digest if key is not None else None,
                    experiment_id=experiment_id,
                )
            return WriteResult(value=value, replayed=False)
        except asyncpg.exceptions.UniqueViolationError as exc:
            if retried or key is None or exc.constraint_name != REQUEST_KEY_CONSTRAINT:
                raise
            retried = True


async def list(  # activity.list, as the operator API and MCP call it
    conn: asyncpg.Connection,
    *,
    source: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = LIMIT_DEFAULT,
    cursor: str | None = None,
) -> ActivityPage:
    """API-17: ``activity_log`` newest first by (``ts``, ``id``).

    ``source='agent'`` matches every ``agent:<name>``; ``action`` is a prefix match; ``target`` and ``actor``
    are exact; ``from_`` is inclusive and ``to`` exclusive; ``limit`` is 1 to 200; ``cursor`` is the previous
    page's ``next_cursor`` (an opaque base64url of its last ``ts`` and ``id``).
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIMIT_MAX:
        # invalid_request, as the operator API answers the same query (#1148: request validation is 400).
        raise ServiceError(
            problem(
                "invalid_request",
                f"limit must be 1-{LIMIT_MAX}",
                field="limit",
                min=1,
                max=LIMIT_MAX,
                current=limit if isinstance(limit, int) else None,
            )
        )
    if source is not None and source != "agent" and _SOURCE.fullmatch(source) is None:
        raise ServiceError(
            problem("invalid_request", "source is dashboard, iris, system, agent or agent:<name>", field="source")
        )
    for name, value in (("actor", actor), ("action", action), ("target", target)):
        if value and "\x00" in value:  # PostgreSQL text cannot hold it, so no row can match
            raise ServiceError(problem("invalid_request", f"{name} holds a NUL character", field=name))
    for name, value in (("from", from_), ("to", to)):
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise ServiceError(problem("invalid_request", f"{name} needs a time with its UTC offset", field=name))
    after = _decode_cursor(cursor) if cursor else None

    clauses = ["greenhouse_id = $1"]
    args = [GREENHOUSE_ID]

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if source == "agent":
        clauses.append("starts_with(source, 'agent:')")
    elif source:
        clauses.append(f"source = {bind(source)}")
    if actor:
        clauses.append(f"actor = {bind(actor)}")
    if action:
        clauses.append(f"starts_with(action, {bind(action)})")
    if target:
        clauses.append(f"target = {bind(target)}")
    if from_ is not None:
        clauses.append(f"ts >= {bind(from_)}")
    if to is not None:
        clauses.append(f"ts < {bind(to)}")
    if after is not None:  # the plain ts bound lets the (greenhouse_id, ts DESC) index start at the cursor
        at, row_id = bind(after[0]), bind(after[1])
        clauses.append(f"ts <= {at}::timestamptz AND (ts, id) < ({at}::timestamptz, {row_id}::bigint)")
    sql = (
        f"SELECT {_COLUMNS} FROM public.activity_log WHERE {' AND '.join(clauses)} "
        f"ORDER BY ts DESC, id DESC LIMIT {bind(limit + 1)}"
    )
    records = await conn.fetch(sql, *args)
    items = [_entry(_row(record)) for record in records[:limit]]
    more = len(records) > limit
    return ActivityPage(items=items, next_cursor=_encode_cursor(items[-1].ts, items[-1].id) if more else None)
