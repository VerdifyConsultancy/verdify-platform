"""Slack operations contracts for the greenhouse operator surface."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SlackRole = Literal["viewer", "operator", "grower", "coordinator"]
SlackCommandStatus = Literal[
    "received",
    "parsed",
    "denied",
    "needs_confirmation",
    "confirmed",
    "executed",
    "not_found",
    "ambiguous",
    "error",
    "unsupported",
    "unsafe_blocked",
]
SlackModelRouting = Literal["deterministic", "openclaw_ai", "hybrid"]
SlackConfirmationStatus = Literal["pending", "confirmed", "canceled", "expired"]
SlackAlertActionKind = Literal["acknowledge", "snooze", "assign", "note", "false_positive", "resolve"]
SlackNotificationStatus = Literal["planned", "posted", "suppressed", "digest", "failed", "deleted"]
SlackPostMode = Literal["immediate", "thread", "digest", "suppressed"]
CropTaskType = Literal[
    "scouting",
    "treatment_followup",
    "harvest_due",
    "harvest_overdue",
    "stage_check",
    "observation_followup",
]
CropTaskPriority = Literal["low", "normal", "high", "critical"]
CropTaskStatus = Literal["open", "snoozed", "completed", "canceled"]

SlackIntentName = Literal[
    "status.get",
    "brief.get",
    "zone.status.get",
    "position.status.get",
    "equipment.status.get",
    "sensor.status.get",
    "crop.map.get",
    "crop.empty_positions.get",
    "crop.harvest_due.get",
    "crop.create",
    "crop.clear",
    "crop.transplant",
    "crop.harvest",
    "crop.observe",
    "crop.photo_observe",
    "crop.scouting_due.get",
    "crop.treatment.record",
    "alert.ack",
    "alert.snooze",
    "alert.assign",
    "alert.note",
    "alert.false_positive",
    "alert.resolve",
    "plan.status.get",
    "plan.trigger",
    "plan.explain",
    "confirmation.confirm",
    "confirmation.cancel",
    "forecast.deviation.triage",
    "firmware.health.get",
    "unsafe.direct_relay_control",
    "unknown",
]


class SlackUserRoleRow(BaseModel):
    """slack_user_roles table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    slack_team_id: str | None = None
    slack_user_id: str
    display_name: str | None = None
    role: SlackRole = "viewer"
    is_active: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


class SlackCommandAuditRow(BaseModel):
    """slack_command_audit table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    channel_id: str | None = None
    channel_name: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    slack_team_id: str | None = None
    slack_user_id: str | None = None
    slack_user_name: str | None = None
    role: SlackRole | None = None
    command_text: str = Field(..., min_length=1)
    normalized_intent: SlackIntentName
    status: SlackCommandStatus = "received"
    requires_confirmation: bool = False
    confirmation_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    record_type: str | None = None
    record_id: str | None = None
    response_text: str | None = None
    error: str | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)
    model_routing: SlackModelRouting = "deterministic"
    handled_by: str = "slack_ops"


class SlackConfirmationRequestRow(BaseModel):
    """slack_confirmation_requests table row."""

    model_config = ConfigDict(extra="ignore")

    id: UUID | None = None
    created_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    canceled_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    slack_team_id: str | None = None
    slack_user_id: str
    channel_id: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    normalized_intent: SlackIntentName
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: SlackConfirmationStatus = "pending"
    command_audit_id: int | None = None
    confirmation_text: str


class SlackAlertActionRow(BaseModel):
    """slack_alert_actions table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    alert_id: int
    action: SlackAlertActionKind
    slack_user_id: str | None = None
    slack_user_name: str | None = None
    channel_id: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    note: str | None = None
    snoozed_until: AwareDatetime | None = None
    assigned_to: str | None = None
    command_audit_id: int | None = None


class SlackNotificationEventRow(BaseModel):
    """slack_notification_events table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    source: str
    event_type: str
    severity: str | None = None
    channel_id: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    dedupe_key: str | None = None
    status: SlackNotificationStatus = "posted"
    post_mode: SlackPostMode = "immediate"
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class CropTaskRow(BaseModel):
    """crop_tasks table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    task_type: CropTaskType
    priority: CropTaskPriority = "normal"
    status: CropTaskStatus = "open"
    crop_id: int | None = None
    position_id: int | None = None
    zone_id: int | None = None
    due_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    completed_by: str | None = None
    source: str = "slack_ops"
    related_observation_id: int | None = None
    related_treatment_id: int | None = None
    related_harvest_id: int | None = None
    slack_channel_id: str | None = None
    slack_message_ts: str | None = None
    slack_thread_ts: str | None = None
    notes: str | None = None


class SlackCommandRequest(BaseModel):
    """Input envelope for deterministic Slack command handling."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    command_text: str = Field(..., min_length=1)
    slack_user_id: str | None = None
    slack_user_name: str | None = None
    slack_team_id: str | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)
    execute: bool = False


class SlackParsedIntent(BaseModel):
    """Normalized deterministic intent extracted from a Slack message."""

    model_config = ConfigDict(extra="forbid")

    name: SlackIntentName
    args: dict[str, Any] = Field(default_factory=dict)
    target_type: str | None = None
    target_id: str | None = None
    required_role: SlackRole = "viewer"
    write: bool = False
    requires_confirmation: bool = False
    requires_ai: bool = False
    unsafe_blocked: bool = False
    reason: str | None = None


class SlackCommandResponse(BaseModel):
    """Output envelope returned to OpenClaw/MCP or CLI callers."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    handled: bool
    intent: SlackParsedIntent
    status: SlackCommandStatus
    role: SlackRole = "viewer"
    text: str
    audit_id: int | None = None
    confirmation_id: UUID | None = None
    record_type: str | None = None
    record_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
