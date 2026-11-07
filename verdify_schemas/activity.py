"""Activity log and desired-state rows: the two tables of migration 280 (VD-30).

- ActivityLogRow  → activity_log  (one row per write: who, what, why, the request id and the armed experiment)
- DesiredStateRow → desired_state (one row per setting write; the newest row per param wins)

``ACTOR_SOURCE_PATTERN`` is the R4 source vocabulary and the OpenAPI ``Actor.source`` pattern:
``dashboard``, ``iris``, ``system`` or ``agent:<name>``. Every writer validates its rows here: the
``verdify_services`` write path and the ingestor's own activity rows (the forecast engine, #1178), which
reach ``activity_log`` with SQL because the ingestor image carries no ``verdify_services``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

# R4: who wrote. The OpenAPI Actor.source pattern, verbatim.
ACTOR_SOURCE_PATTERN = r"^(dashboard|iris|system|agent:[a-z0-9][a-z0-9_-]*)$"
# Architecture §9: setting.write, setting.release, plan.write, alert.ack, alert.resolve, irrigation.*,
# light.on and light.off, band.write (the band editor's one Save, #1334) and planting.* (#1327: create, move, harvest,
# end, update, note), soil_target.write (#1345) and the planner study's study.* rows (#1360: start, pause, resume and
# stop by an operator, boundary and deviation by the study runner). Extend it here when a new kind of write appears.
ACTIVITY_ACTION_PATTERN = (
    r"^(setting\.(write|release)|band\.write|plan\.write|alert\.(ack|resolve)|light\.(on|off)"
    r"|irrigation(\.[a-z][a-z0-9_]*)+|planting\.(create|move|harvest|end|update|note)|soil_target\.write"
    r"|study\.(start|pause|resume|stop|boundary|deviation))$"
)
# A tunable_registry name (the OpenAPI Param pattern).
SETTING_PARAM_PATTERN = r"^[a-z][a-z0-9_]*$"
ACTOR_NAME_MAX_LENGTH = 64
REASON_MAX_LENGTH = 500


class ActivityLogRow(BaseModel):
    """activity_log row: one per write by a person, Iris, an agent or the system (US-ACT-01).

    ``request_id`` and ``request_sha256`` are the idempotency record (unique per source) and are set
    together; ``detail`` carries ``ref: {table, id}`` of the stored object so a replay can load it.
    ``reason`` has no length limit, like the column, so logging a write never refuses it (Iris's
    unchanged ``set_tunable`` takes a reason of any length, R5); the REST and MCP write inputs stop at
    500 characters in ``verdify_services.activity.recorded_write``.
    """

    model_config = ConfigDict(extra="ignore", ser_json_bytes="hex")

    id: int | None = None
    greenhouse_id: str = "vallery"
    ts: AwareDatetime | None = None
    source: str = Field(pattern=ACTOR_SOURCE_PATTERN)
    actor: str = Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)
    action: str = Field(pattern=ACTIVITY_ACTION_PATTERN)
    target: str = Field(min_length=1)
    detail: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    request_id: UUID | None = None
    request_sha256: bytes | None = Field(default=None, min_length=32, max_length=32)
    experiment_id: UUID | None = None

    @model_validator(mode="after")
    def _request_id_and_hash_together(self) -> ActivityLogRow:
        if (self.request_id is None) != (self.request_sha256 is None):
            raise ValueError("request_id and request_sha256 are set together (the idempotency record)")
        return self


class DesiredStateRow(BaseModel):
    """desired_state row: one setting write. ``value`` None releases the setting to plan, band or default."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    param: str = Field(pattern=SETTING_PARAM_PATTERN)
    value: float | None = Field(default=None, allow_inf_nan=False)
    manual: bool = False
    source: str = Field(pattern=ACTOR_SOURCE_PATTERN)
    actor: str = Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)
    reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH)
    created_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _release_is_never_manual(self) -> DesiredStateRow:
        if self.value is None and self.manual:
            raise ValueError("a release (value None) cannot be manual")
        return self
