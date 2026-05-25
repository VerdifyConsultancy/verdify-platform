"""Slack operations schemas shared by MCP, ingestor, and tests."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SlackRole = Literal["viewer", "grower", "operator", "coordinator"]
SlackCommandStatus = Literal[
    "received", "parsed", "needs_confirmation", "executed", "denied", "failed", "canceled", "expired"
]
SlackRouting = Literal["deterministic", "openclaw", "hermes"]


class SlackUserRoleRow(BaseModel):
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
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    channel_id: str
    channel_name: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    slack_team_id: str | None = None
    slack_user_id: str
    slack_user_name: str | None = None
    role: SlackRole = "viewer"
    command_text: str
    normalized_intent: str | None = None
    status: SlackCommandStatus = "received"
    requires_confirmation: bool = False
    confirmation_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    record_type: str | None = None
    record_id: str | None = None
    response_text: str | None = None
    error: str | None = None
    raw_event: dict[str, Any] | None = None
    model_routing: SlackRouting = "deterministic"
    handled_by: str = "python"


class SlackConfirmationRequestRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    created_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    canceled_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    slack_team_id: str | None = None
    slack_user_id: str
    channel_id: str
    message_ts: str | None = None
    thread_ts: str | None = None
    normalized_intent: str
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    command_audit_id: int | None = None
    confirmation_text: str


class SlackAlertActionRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    alert_id: int
    action: str
    slack_user_id: str
    slack_user_name: str | None = None
    channel_id: str
    message_ts: str | None = None
    thread_ts: str | None = None
    note: str | None = None
    snoozed_until: AwareDatetime | None = None
    assigned_to: str | None = None
    command_audit_id: int | None = None


class SlackNotificationEventRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    source: str
    event_type: str
    severity: str | None = None
    channel_id: str
    message_ts: str | None = None
    thread_ts: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    dedupe_key: str | None = None
    status: str = "posted"
    post_mode: str = "thread"
    payload: dict[str, Any] | None = None
    error: str | None = None


class CropTaskRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    task_type: str
    priority: str = "normal"
    status: str = "open"
    crop_id: int | None = None
    position_id: int | None = None
    zone_id: int | None = None
    due_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    completed_by: str | None = None
    source: str = "system"
    related_observation_id: int | None = None
    related_treatment_id: int | None = None
    related_harvest_id: int | None = None
    slack_channel_id: str | None = None
    slack_message_ts: str | None = None
    slack_thread_ts: str | None = None
    notes: str | None = None


class SlackCommandRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    slack_user_id: str
    slack_user_name: str | None = None
    slack_team_id: str | None = None
    channel_id: str
    channel_name: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    raw_event: dict[str, Any] | None = None


class SlackParsedIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    normalized_intent: str
    args: dict[str, Any] = Field(default_factory=dict)
    required_role: SlackRole = "viewer"
    requires_confirmation: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    blocked_reason: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class SlackCommandResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    text: str
    status: SlackCommandStatus = "executed"
    normalized_intent: str | None = None
    requires_confirmation: bool = False
    confirmation_id: UUID | None = None
    record_type: str | None = None
    record_id: str | None = None
    model_routing: SlackRouting = "deterministic"
