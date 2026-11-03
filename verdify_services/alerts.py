"""Alerts: the one open-alert list and count, and acknowledge and resolve (architecture §7, VD-67).

- ``list(conn, status, limit, cursor)``: API-14 and the MCP ``alerts`` adapter. Open rows are ``alert_log``
  under ``v_open_alerts``' own predicate (``resolved_at IS NULL AND disposition <> 'suppressed'``), because
  the MCP login can read ``alert_log`` (migration 259) but not the view; ``recent`` adds the rows resolved in
  the last 24 h.
- ``open_count(conn)``: the one open-alert count every surface shows (Round 2b C); it always equals the rows
  with status ``open`` that ``list`` returns, because it merges the same rows the same way.
- ``ack(...)`` and ``resolve(...)``: act on every repeat of a merged row in one ``activity.recorded_write``
  transaction with one ``activity_log`` row. Resolve never refuses; the MCP adapter keeps its
  ``planner_tool_dependency_failed`` retrieval check.

Repeats merge by (``alert_type``, ``sensor_id``), a NULL ``sensor_id`` grouping with NULL: the key the
ingestor's alert monitor refreshes rows on, so each probe keeps its own row (VD-72) and ``zone``, NULL on most
rows, never folds unrelated sensors together. Resolved rows merge among themselves, apart from unresolved ones.

This module defines ``list``, so it keeps annotations eager (no postponed evaluation), builds its models before
that name exists and never calls the builtin after it.
"""

import base64
import json
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from verdify_schemas.activity import REASON_MAX_LENGTH, ActivityLogRow
from verdify_services import activity
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError, problem

GREENHOUSE_ID = "vallery"
LIMIT_DEFAULT = 50
LIMIT_MAX = 200
RECENT = timedelta(hours=24)
_INT4_MAX = 2**31 - 1
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

AlertSeverity = Literal["critical", "warning", "info"]
AlertStatus = Literal["open", "acknowledged", "resolved", "false_positive", "suppressed"]
ListStatus = Literal["open", "recent"]
SEVERITY_RANK: Mapping[str, int] = {"critical": 0, "warning": 1, "info": 2}


class Alert(BaseModel):
    """One merged alert row (OpenAPI ``Alert``).

    ``id`` is the oldest unresolved repeat's (a resolved merged row: its oldest repeat's), so it stays put as
    repeats merge; the other fields are the newest repeat's. ``count`` sums each repeat's ``details.count``
    (1 where absent), ``first_seen_at`` is the oldest ``ts`` and ``last_seen_at`` the newest
    ``details.last_seen_at`` or ``ts``. ``status`` is ``open`` while any repeat is disposition ``open``, else
    ``acknowledged``; a resolved merged row reads ``resolved``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    alert_type: str
    category: str
    severity: AlertSeverity
    message: str
    zone: str | None = None
    metric_value: float | None = None
    threshold_value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    status: AlertStatus
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime | None = None
    count: int = Field(ge=1)
    acknowledged_at: AwareDatetime | None = None
    acknowledged_by: str | None = None
    notes: str | None = None
    resolved_at: AwareDatetime | None = None
    resolved_by: str | None = None
    resolution: str | None = None
    runbook: str | None = None


class AlertPage(BaseModel):
    """Merged rows by severity, then last seen (OpenAPI ``AlertPage``); ``next_cursor`` None on the last page."""

    model_config = ConfigDict(frozen=True)

    items: list[Alert]
    next_cursor: str | None = None


class AlertCounts(BaseModel):
    """The one open-alert count (OpenAPI ``Now.alerts``): merged rows with status ``open``, by severity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    open: int
    critical: int
    warning: int
    info: int


@dataclass(frozen=True)
class Merged:
    """A merged row and the ``alert_log`` ids it holds."""

    alert: Alert
    ids: tuple[int, ...]


# ── Pure rules ──────────────────────────────────────────────────────────────


def _details(raw: object) -> dict[str, Any]:
    if isinstance(raw, str | bytes):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def repeat_count(details: Mapping[str, Any]) -> int:
    """A row's ``details.count`` (VD-72 bumps it as a probe repeats), 1 where absent or not a count."""
    count = details.get("count")
    return count if type(count) is int and count >= 1 else 1


def _aware(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.utcoffset() is not None else None
    return None


def last_seen(row: Mapping[str, Any], details: Mapping[str, Any]) -> datetime:
    """When a row was last raised: ``details.last_seen_at``, else its ``ts``."""
    return _aware(details.get("last_seen_at")) or row["ts"]


def merge_key(row: Mapping[str, Any]) -> tuple[str, str | None, bool]:
    """(``alert_type``, ``sensor_id``, resolved): repeats of one alert merge; resolved rows apart."""
    return row["alert_type"], row["sensor_id"], row["resolved_at"] is not None


def _merge_group(rows: Sequence[Mapping[str, Any]]) -> Merged:
    order = sorted(rows, key=lambda row: (row["ts"], row["id"]))
    oldest, newest = order[0], order[-1]
    newest_details = _details(newest["details"])
    details = [_details(row["details"]) for row in order]
    resolved = newest["resolved_at"] is not None
    if resolved:
        status = "resolved"
    elif any(row["disposition"] == "open" for row in order):
        status = "open"
    else:
        status = "acknowledged"
    alert = Alert(
        id=oldest["id"],
        alert_type=newest["alert_type"],
        category=newest["category"],
        severity=newest["severity"],
        message=newest["message"],
        zone=newest["zone"],
        metric_value=newest["metric_value"],
        threshold_value=newest["threshold_value"],
        details=newest_details,
        status=status,
        first_seen_at=oldest["ts"],
        last_seen_at=max(last_seen(row, detail) for row, detail in zip(order, details, strict=True)),
        count=sum(repeat_count(detail) for detail in details),
        acknowledged_at=newest["acknowledged_at"],
        acknowledged_by=newest["acknowledged_by"],
        notes=newest["notes"],
        resolved_at=newest["resolved_at"],
        resolved_by=newest["resolved_by"],
        resolution=newest["resolution"],
    )
    return Merged(alert, tuple(sorted(row["id"] for row in order)))


def merge(rows: Iterable[Mapping[str, Any]]) -> tuple[Merged, ...]:
    """Merge ``alert_log`` rows into one row per (``alert_type``, ``sensor_id``), unresolved and resolved
    apart, ordered by severity, then last seen (newest first), then id."""
    groups: dict[tuple[str, str | None, bool], list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(merge_key(row), []).append(row)
    return tuple(sorted((_merge_group(group) for group in groups.values()), key=lambda item: _sort_key(item.alert)))


def _micros(at: datetime) -> int:
    return (at - _EPOCH) // timedelta(microseconds=1)


def _sort_key(alert: Alert) -> tuple[int, int, int]:
    seen = alert.last_seen_at or alert.first_seen_at
    return SEVERITY_RANK.get(alert.severity, len(SEVERITY_RANK)), -_micros(seen), -alert.id


def counts(merged: Iterable[Merged]) -> AlertCounts:
    """The open-alert count: merged rows with status ``open``, each under its newest repeat's severity."""
    tally = {severity: 0 for severity in SEVERITY_RANK}
    for item in merged:
        if item.alert.status == "open":
            tally[item.alert.severity] += 1
    return AlertCounts(open=sum(tally.values()), **tally)


def runbook_markdown(runbook: Mapping[str, Any] | None) -> str | None:
    """A ``slack_alert_runbooks`` row as Markdown: title, summary, numbered steps and the link."""
    if not runbook:
        return None
    lines = [f"**{runbook.get('title') or runbook.get('alert_type') or 'Runbook'}**"]
    if runbook.get("summary"):
        lines += ["", str(runbook["summary"])]
    steps = [str(step) for step in (runbook.get("steps") or []) if str(step).strip()]
    if steps:
        lines += [""] + [f"{index}. {step}" for index, step in enumerate(steps, start=1)]
    if runbook.get("runbook_url"):
        lines += ["", f"[Operator view]({runbook['runbook_url']})"]
    return "\n".join(lines)


def pick_runbook(runbooks: Iterable[Mapping[str, Any]], alert_type: str, severity: str) -> Mapping[str, Any] | None:
    """The active runbook for a type: one for its severity, else one for any severity, else any; lowest id
    first (the order ``slack_ops.runbooks.fetch_alert_runbook`` uses)."""

    def rank(runbook: Mapping[str, Any]) -> tuple[int, int]:
        level = runbook.get("severity")
        if level is not None and str(level).lower() == severity.lower():
            return 0, runbook["id"]
        return (1 if level is None else 2), runbook["id"]

    matching = [runbook for runbook in runbooks if str(runbook["alert_type"]).lower() == alert_type.lower()]
    return min(matching, key=rank) if matching else None


def encode_cursor(alert: Alert) -> str:
    seen = alert.last_seen_at or alert.first_seen_at
    raw = json.dumps([SEVERITY_RANK.get(alert.severity, 3), seen.isoformat(), alert.id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("ascii")).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[int, int, int]:
    """The sort key the previous page ended on."""
    try:
        rank, seen_text, alert_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        seen = datetime.fromisoformat(seen_text)
        if (
            seen.utcoffset() is None
            or isinstance(rank, bool)
            or not isinstance(rank, int)
            or isinstance(alert_id, bool)
            or not isinstance(alert_id, int)
        ):
            raise ValueError("not a cursor")
    except (TypeError, ValueError) as exc:  # binascii.Error, JSONDecodeError and UnicodeDecodeError included
        raise ServiceError(
            problem("invalid_request", "cursor is not a next_cursor of this list", field="cursor")
        ) from exc
    return rank, -_micros(seen), -alert_id


def acted_by(actor: Actor) -> str:
    """``acknowledged_by`` and ``resolved_by``: the username for a person, ``actor.source`` for the rest."""
    return actor.name if actor.source == "dashboard" else actor.source


# ── Database (parameters only, never text from a request) ───────────────────

_COLUMNS = (
    "id, ts, alert_type, severity, category, sensor_id, zone, message, details::text AS details, "
    "metric_value, threshold_value, disposition, acknowledged_at, acknowledged_by, notes, "
    "resolved_at, resolved_by, resolution"
)
# v_open_alerts' own predicate, read from alert_log (db/schema.sql v_open_alerts).
OPEN_PREDICATE = "resolved_at IS NULL AND disposition <> 'suppressed'"
_RECENT_PREDICATE = "resolved_at >= now() - $1::interval AND disposition <> 'suppressed'"
_OPEN_SQL = f"SELECT {_COLUMNS} FROM alert_log WHERE {OPEN_PREDICATE}"
_RECENT_SQL = f"SELECT {_COLUMNS} FROM alert_log WHERE ({OPEN_PREDICATE}) OR ({_RECENT_PREDICATE})"
_BY_ID_SQL = f"SELECT {_COLUMNS} FROM alert_log WHERE id = $1"
_KEY = "alert_type = $1 AND sensor_id IS NOT DISTINCT FROM $2"
_KEY_OPEN_SQL = f"SELECT {_COLUMNS} FROM alert_log WHERE {_KEY} AND {OPEN_PREDICATE}"
# A resolved row's merged row: its key's rows resolved in the last 24 h, and the row itself.
_KEY_RESOLVED_SQL = (
    f"SELECT {_COLUMNS} FROM alert_log WHERE {_KEY} AND resolved_at IS NOT NULL "
    "AND ((resolved_at >= now() - $4::interval AND disposition <> 'suppressed') OR id = $3)"
)
# Every unacknowledged repeat (disposition open) of an unresolved merged row.
_ACK_SQL = f"""
UPDATE alert_log
   SET disposition = 'acknowledged', acknowledged_at = now(), acknowledged_by = $3, notes = coalesce($4, notes)
 WHERE {_KEY} AND resolved_at IS NULL AND disposition = 'open'
RETURNING id
"""
# Every unresolved repeat of a merged row, as the MCP tool resolves one today.
_RESOLVE_SQL = f"""
UPDATE alert_log
   SET disposition = 'resolved', resolved_at = now(), resolved_by = $3, resolution = $4
 WHERE {_KEY} AND {OPEN_PREDICATE}
RETURNING id
"""
_RUNBOOKS_SQL = """
SELECT id, alert_type, severity, title, summary, runbook_url, steps
  FROM slack_alert_runbooks
 WHERE greenhouse_id = $1 AND is_active AND lower(alert_type) = ANY($2::text[])
"""


@asynccontextmanager
async def _snapshot(conn: asyncpg.Connection):
    """One read-only snapshot for a whole read, so every ``now()`` in it agrees."""
    if conn.is_in_transaction():
        yield
        return
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        yield


async def _with_runbooks(conn: asyncpg.Connection, merged: Sequence[Merged]) -> tuple[Alert, ...]:
    types = sorted({item.alert.alert_type.lower() for item in merged})
    runbooks = [dict(row) for row in await conn.fetch(_RUNBOOKS_SQL, GREENHOUSE_ID, types)] if types else []
    return tuple(
        item.alert.model_copy(
            update={"runbook": runbook_markdown(pick_runbook(runbooks, item.alert.alert_type, item.alert.severity))}
        )
        for item in merged
    )


def _alert_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServiceError(problem("invalid_request", "id must be an alert id", field="id"))
    return value


async def _row(conn: asyncpg.Connection, alert_id: int) -> Mapping[str, Any] | None:
    if not 1 <= alert_id <= _INT4_MAX:  # alert_log.id is an integer
        return None
    return await conn.fetchrow(_BY_ID_SQL, alert_id)


async def _merged_for(conn: asyncpg.Connection, row: Mapping[str, Any]) -> Merged:
    """The merged row that holds ``row`` now: its key's open rows, or its key's recently resolved rows."""
    if row["resolved_at"] is None and row["disposition"] != "suppressed":
        rows = await conn.fetch(_KEY_OPEN_SQL, row["alert_type"], row["sensor_id"])
    else:
        rows = await conn.fetch(_KEY_RESOLVED_SQL, row["alert_type"], row["sensor_id"], row["id"], RECENT)
    for item in merge([*rows, row] if all(other["id"] != row["id"] for other in rows) else rows):
        if row["id"] in item.ids:
            return item
    raise RuntimeError(f"alert {row['id']} is in no merged row")  # merge() always places every row


def _activity_reason(text: str | None) -> str | None:
    """The note or resolution as the activity row's reason, never refused for its length (resolve never
    refuses); ``alert_log`` keeps the whole text."""
    if text is None or len(text) <= REASON_MAX_LENGTH:
        return text
    return text[: REASON_MAX_LENGTH - 1] + "…"


async def _act(
    conn: asyncpg.Connection,
    actor: Actor,
    alert_id: object,
    text: str | None,
    request_id: Any,
    *,
    op: str,
    action: str,
    field: str,
    sql: str,
) -> WriteResult[Alert]:
    key = _alert_id(alert_id)
    if text is not None and "\x00" in text:  # PostgreSQL text cannot hold it, so no retry could store it
        raise ServiceError(problem("invalid_request", f"{field} holds a NUL character", field=field))

    async def perform(tx: asyncpg.Connection) -> tuple[Alert, dict[str, Any]]:
        row = await _row(tx, key)
        if row is None:
            raise ServiceError(problem("unknown_target", f"alert {key} does not exist", field="id"))
        ids: Sequence[int] = ()
        if row["resolved_at"] is None and row["disposition"] != "suppressed":
            updated = await tx.fetch(sql, row["alert_type"], row["sensor_id"], acted_by(actor), text)
            ids = sorted(item["id"] for item in updated)
        merged = await _merged_for(tx, await _row(tx, key) or row)
        [alert] = await _with_runbooks(tx, [merged])
        detail = {
            "ref": {"table": "alert_log", "id": key},
            "ids": [*ids],
            "alert_type": row["alert_type"],
            "sensor_id": row["sensor_id"],
        }
        return alert, detail

    async def load(tx: asyncpg.Connection, logged: ActivityLogRow) -> Alert:
        row = await _row(tx, (logged.detail.get("ref") or {}).get("id", key))
        if row is None:
            raise ServiceError(problem("not_found", f"alert {key} is gone", field="id"))
        [alert] = await _with_runbooks(tx, [await _merged_for(tx, row)])
        return alert

    return await activity.recorded_write(
        conn,
        actor,
        op=op,
        action=action,
        target=str(key),
        request={field: text},
        request_id=request_id,
        reason=_activity_reason(text),
        perform=perform,
        load=load,
    )


async def ack(conn: asyncpg.Connection, actor: Actor, id: int, note: str | None, request_id: Any) -> WriteResult[Alert]:
    """API-15: acknowledge every unacknowledged repeat of the merged row ``id`` belongs to.

    One ``recorded_write`` transaction sets ``disposition`` acknowledged, ``acknowledged_at``,
    ``acknowledged_by`` (the username for a person, ``actor.source`` otherwise) and, when given, ``notes``, and
    writes one ``alert.ack`` activity row (detail: the ids). Returns the merged row. An unknown id is
    ``unknown_target``; a resolved row acknowledges nothing and returns its resolved merged row. A note holding a
    NUL, which PostgreSQL text cannot store, is ``invalid_request``.
    """
    return await _act(conn, actor, id, note, request_id, op="API-15", action="alert.ack", field="note", sql=_ACK_SQL)


async def resolve(
    conn: asyncpg.Connection, actor: Actor, id: int, resolution: str | None, request_id: Any
) -> WriteResult[Alert]:
    """API-16: resolve every unresolved repeat of the merged row ``id`` belongs to, whatever its type.

    One ``recorded_write`` transaction sets ``disposition`` resolved, ``resolved_at``, ``resolved_by`` and
    ``resolution``, as the MCP tool does today, and writes one ``alert.resolve`` activity row (detail: the
    ids). Returns the merged row. An unknown id is ``unknown_target``, and a resolution holding a NUL, which
    PostgreSQL text cannot store, is ``invalid_request``; nothing else refuses it. The MCP adapter keeps its
    ``planner_tool_dependency_failed`` retrieval check; this service has none.
    """
    return await _act(
        conn,
        actor,
        id,
        resolution,
        request_id,
        op="API-16",
        action="alert.resolve",
        field="resolution",
        sql=_RESOLVE_SQL,
    )


async def open_count(conn: asyncpg.Connection) -> AlertCounts:
    """``{open, critical, warning, info}`` over the merged open rows with status ``open``: always the rows with
    status ``open`` that ``list`` returns (Round 2b C)."""
    return counts(merge(await conn.fetch(_OPEN_SQL)))


async def list(  # alerts.list, as the operator API and MCP call it
    conn: asyncpg.Connection,
    status: ListStatus = "open",
    limit: int = LIMIT_DEFAULT,
    cursor: str | None = None,
) -> AlertPage:
    """API-14: merged rows by severity, then last seen, each with its runbook, in keyset pages.

    ``open`` is every row under ``v_open_alerts``' predicate, acknowledged ones included; ``recent`` adds the
    rows resolved in the last 24 h (suppressed ones never show). ``limit`` is 1 to 200; ``cursor`` is the
    previous page's ``next_cursor``.
    """
    if status not in ("open", "recent"):
        raise ServiceError(problem("invalid_request", "status is open or recent", field="status"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIMIT_MAX:
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
    after = decode_cursor(cursor) if cursor else None
    async with _snapshot(conn):
        rows = await conn.fetch(_OPEN_SQL) if status == "open" else await conn.fetch(_RECENT_SQL, RECENT)
        merged = [item for item in merge(rows) if after is None or _sort_key(item.alert) > after]
        page = await _with_runbooks(conn, merged[:limit])
    more = len(merged) > limit
    return AlertPage(items=[*page], next_cursor=encode_cursor(page[-1]) if more else None)
