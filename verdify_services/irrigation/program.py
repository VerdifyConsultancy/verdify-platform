"""What the controller runs (VD-40, #1155): the engine flag, the applied program and its pushes.

``verdify_services.settings`` (the ``irrigation_site`` readback) and ``verdify_services.irrigation.reads`` both read
these, from relations both the dashboard and the MCP logins can read (``system_state``, migration 259;
``irrigation_push``, M3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from verdify_schemas.irrigation import (
    SITE_PARAM_FIELDS,
    ProgramId,
    ProgramRecordError,
    parse_irr_program_id,
    unpack_program,
)

GREENHOUSE_ID = "vallery"
ENGINE_ENTITY = "irr_engine"  # #1177 writes one row on every controller connect: true or false
PROGRAM_ID_ENTITY = "irr_program_id"  # each change, from the controller's text sensor (#1157)
# A long bound: both change rarely and system_state keeps every row.
NEWEST_STATE_WINDOW = timedelta(days=400)


def engine_flag(value: object) -> bool:
    """An ``irr_engine`` row's value: only ``true`` is the engine; a missing row or anything else is not."""
    return isinstance(value, str) and value.strip().lower() == "true"


def program_id_reading(value: object) -> ProgramId | None:
    """``parse_irr_program_id``, with text outside the grammar treated as no reading."""
    if not isinstance(value, str):
        return None
    try:
        return parse_irr_program_id(value)
    except ValueError:
        return None


def site_value(push: Mapping[str, Any], param: str) -> float | None:
    """One ``irrigation_site`` value as the pushed record holds it (the header's SAF-4, RH ceiling or climate yield)."""
    field = SITE_PARAM_FIELDS.get(param)
    if field is None:
        return None
    try:
        return float(getattr(unpack_program(push["bytes"]), field))
    except (ProgramRecordError, TypeError):
        return None


@dataclass(frozen=True)
class ControllerProgram:
    """The controller's program as the platform knows it.

    ``applied`` is the newest push whose ``sha256`` starts with the ``irr_program_id`` hash and ``confirmed`` the
    newest of those with ``confirmed_at``; a controller on ``src=rom`` has neither. Before the engine there is no
    program on the controller, so all of them are None whatever an old row says.
    """

    engine: bool
    engine_at: datetime | None = None
    program_id: ProgramId | None = None
    applied: Mapping[str, Any] | None = None
    confirmed: Mapping[str, Any] | None = None


_STATES_SQL = """
SELECT e.entity, s.value, s.ts
  FROM unnest($1::text[]) AS e(entity)
  LEFT JOIN LATERAL (SELECT value, ts FROM system_state
                      WHERE entity = e.entity AND coalesce(greenhouse_id, 'vallery') = $2
                        AND ts > now() - $3::interval
                      ORDER BY ts DESC
                      LIMIT 1) AS s ON true
"""

_PUSHES_OF_HASH_SQL = """
SELECT revision, sha256, bytes, site_desired_at, pushed_at, confirmed_at, reject_code
  FROM irrigation_push
 WHERE greenhouse_id = $1 AND left(sha256, 16) = $2
 ORDER BY revision DESC
"""

# For each (param, write time): the first push compiled at or after the write that went out.
_FIRST_PUSH_SQL = """
SELECT c.param, p.pushed_at
  FROM unnest($2::text[], $3::timestamptz[]) AS c(param, since)
 CROSS JOIN LATERAL (SELECT pushed_at FROM irrigation_push
                      WHERE greenhouse_id = $1 AND site_desired_at >= c.since AND pushed_at IS NOT NULL
                      ORDER BY revision
                      LIMIT 1) AS p
"""


async def controller_program(conn: asyncpg.Connection) -> ControllerProgram:
    """The newest ``irr_engine`` and ``irr_program_id`` rows and the pushes of the applied hash."""
    rows = {
        row["entity"]: row
        for row in await conn.fetch(_STATES_SQL, [ENGINE_ENTITY, PROGRAM_ID_ENTITY], GREENHOUSE_ID, NEWEST_STATE_WINDOW)
    }
    engine_row = rows.get(ENGINE_ENTITY)
    engine = engine_row is not None and engine_flag(engine_row["value"])
    engine_at = engine_row["ts"] if engine_row is not None else None
    if not engine:
        return ControllerProgram(engine=False, engine_at=engine_at)
    id_row = rows.get(PROGRAM_ID_ENTITY)
    program_id = program_id_reading(id_row["value"]) if id_row is not None else None
    if program_id is None:
        return ControllerProgram(engine=True, engine_at=engine_at)
    pushes = await conn.fetch(_PUSHES_OF_HASH_SQL, GREENHOUSE_ID, program_id.hash16)
    return ControllerProgram(
        engine=True,
        engine_at=engine_at,
        program_id=program_id,
        applied=pushes[0] if pushes else None,
        confirmed=next((push for push in pushes if push["confirmed_at"] is not None), None),
    )


async def first_pushes(conn: asyncpg.Connection, writes: Sequence[tuple[str, datetime]]) -> dict[str, datetime]:
    """For each (param, write time): the ``pushed_at`` of the first push compiled at or after the write."""
    if not writes:
        return {}
    rows = await conn.fetch(
        _FIRST_PUSH_SQL, GREENHOUSE_ID, [param for param, _ in writes], [since for _, since in writes]
    )
    return {row["param"]: row["pushed_at"] for row in rows}
