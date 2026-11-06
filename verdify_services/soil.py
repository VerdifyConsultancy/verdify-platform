"""Soil-moisture targets (#1345, migration 303): API-SOIL-01 and API-SOIL-02, and MCP ``soil_targets`` and
``soil_target_set``, through the one write convention (``activity.recorded_write``).

A target is per probe zone (``south_1``, ``south_2``, ``west``): four thresholds in percent, in the order the database
keeps (``verdify_schemas.soil.order_problem``), plus a note. ``wilt_pct`` is what the ingestor's ``soil_dryout`` rule
pages on, so an edit here changes that rule on its next cycle. The probe map (``probe_column``, ``sensor_id``,
``occupancy_zone``) is read-only here: moving a probe is a migration, not an operator write. No crop name is served.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.soil import THRESHOLDS, SoilTargetPatch, order_problem
from verdify_services import activity
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor, ActorRef
from verdify_services.problems import ServiceError, problem

GREENHOUSE_ID = "vallery"
_COLUMNS = (
    "zone, wilt_pct::float8 AS wilt_pct, min_pct::float8 AS min_pct, max_pct::float8 AS max_pct, "
    "saturation_pct::float8 AS saturation_pct, notes, probe_column, sensor_id, occupancy_zone, updated_at, "
    "updated_by, updated_source, reason"
)
_ALL = f"SELECT {_COLUMNS} FROM public.soil_moisture_targets WHERE greenhouse_id = $1 ORDER BY zone"
_ONE = f"SELECT {_COLUMNS} FROM public.soil_moisture_targets WHERE greenhouse_id = $1 AND zone = $2"
_LOCK_ONE = _ONE + " FOR UPDATE"
_UPDATE = (
    "UPDATE public.soil_moisture_targets SET wilt_pct = $3, min_pct = $4, max_pct = $5, saturation_pct = $6, "
    "notes = $7, updated_at = now(), updated_by = $8, updated_source = $9, reason = $10 "
    "WHERE greenhouse_id = $1 AND zone = $2"
)


class SoilTarget(BaseModel):
    """One zone's target as API-SOIL-01 and MCP serve it (OpenAPI ``SoilTarget``)."""

    model_config = ConfigDict(frozen=True)

    zone: str
    wilt_pct: float
    min_pct: float
    max_pct: float
    saturation_pct: float
    notes: str | None = None
    probe: str | None = None
    sensor_id: str | None = None
    occupancy_zone: str | None = None
    updated_at: AwareDatetime | None = None
    updated_by: ActorRef | None = None
    reason: str | None = None


class SoilTargets(BaseModel):
    """API-SOIL-01's answer: every zone's target, by zone."""

    model_config = ConfigDict(frozen=True)

    targets: list[SoilTarget]


def _target(row: Mapping[str, Any]) -> SoilTarget:
    by = (
        ActorRef(source=row["updated_source"], name=row["updated_by"])
        if row["updated_source"] and row["updated_by"]
        else None
    )
    return SoilTarget(
        zone=row["zone"],
        wilt_pct=row["wilt_pct"],
        min_pct=row["min_pct"],
        max_pct=row["max_pct"],
        saturation_pct=row["saturation_pct"],
        notes=row["notes"],
        probe=row["probe_column"],
        sensor_id=row["sensor_id"],
        occupancy_zone=row["occupancy_zone"],
        updated_at=row["updated_at"],
        updated_by=by,
        reason=row["reason"],
    )


async def targets(conn: asyncpg.Connection) -> SoilTargets:
    """Every zone's soil-moisture target."""
    return SoilTargets(targets=[_target(row) for row in await conn.fetch(_ALL, GREENHOUSE_ID)])


def _unknown(zone: str) -> ServiceError:
    return ServiceError(problem("not_found", f"no soil target for zone {zone!r}", field="zone"))


def _parsed(body: SoilTargetPatch | Mapping[str, Any]) -> SoilTargetPatch:
    if isinstance(body, SoilTargetPatch):
        return body
    try:
        return SoilTargetPatch.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ())) or None
        code = "out_of_range" if first.get("type", "").endswith(("_equal", "_than")) else "invalid_request"
        raise ServiceError(problem(code, first.get("msg", "invalid soil target"), field=field)) from exc


async def set_target(
    conn: asyncpg.Connection,
    actor: Actor | None,
    zone: str,
    body: SoilTargetPatch | Mapping[str, Any],
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> WriteResult[SoilTarget]:
    """Change one zone's thresholds or note (API-SOIL-02, MCP ``soil_target_set``): the merged row must keep
    ``0 <= wilt < min < max < saturation <= 100`` (``out_of_range`` otherwise). One ``soil_target.write`` activity row
    names the old and new values; the row carries who wrote it and why."""
    patch = _parsed(body)
    changes = patch.model_dump(exclude_none=True)
    request = {"zone": zone, **changes}

    async def perform(c: asyncpg.Connection) -> tuple[SoilTarget, dict[str, Any]]:
        row = await c.fetchrow(_LOCK_ONE, GREENHOUSE_ID, zone)
        if row is None:
            raise _unknown(zone)
        before = {name: row[name] for name in (*THRESHOLDS, "notes")}
        after = {**before, **changes}
        problem_text = order_problem({name: float(after[name]) for name in THRESHOLDS})
        if problem_text:
            raise ServiceError(problem("out_of_range", problem_text, field="wilt_pct"))
        assert actor is not None  # recorded_write refused a missing actor first
        await c.execute(
            _UPDATE,
            GREENHOUSE_ID,
            zone,
            after["wilt_pct"],
            after["min_pct"],
            after["max_pct"],
            after["saturation_pct"],
            after["notes"],
            actor.name,
            actor.source,
            reason,
        )
        stored = _target(await c.fetchrow(_ONE, GREENHOUSE_ID, zone))
        changed = {name: {"from": before[name], "to": after[name]} for name in changes if before[name] != after[name]}
        return stored, {"ref": {"table": "soil_moisture_targets", "zone": zone}, "zone": zone, "changed": changed}

    async def load(c: asyncpg.Connection, logged: ActivityLogRow) -> SoilTarget:
        row = await c.fetchrow(_ONE, GREENHOUSE_ID, logged.target)
        if row is None:
            raise _unknown(logged.target)
        return _target(row)

    return await activity.recorded_write(
        conn,
        actor,
        op="soil_target.write",
        action="soil_target.write",
        target=zone,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=load,
    )


__all__ = ["SoilTarget", "SoilTargets", "set_target", "targets"]
