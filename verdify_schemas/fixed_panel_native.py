"""Prospective, route-only native callback ledger input."""

from __future__ import annotations

import math
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EventKind = Literal["connected", "callback", "gap"]
GapReason = Literal["buffer_overflow", "transport_disconnected", "callback_clock_regression"]
ProbeObjectId = Literal[
    "north_temp___f_",
    "north_rh____",
    "east_temp___f_",
    "east_rh____",
    "west_temp___f_",
    "west_rh____",
]


class FixedPanelNativeEvent(BaseModel):
    """One fenced host-received event, without physical probe identity."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fixed-panel-native-event-v1"] = Field(
        default="fixed-panel-native-event-v1", alias="schema"
    )
    kind: EventKind
    runtime_instance_id: UUID
    source_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    transport_generation: int = Field(ge=1, le=9_007_199_254_740_991)
    received_at: AwareDatetime
    collector_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    declared_route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_id: ProbeObjectId | None = None
    value: float | None = None
    firmware_revision: str | None = Field(default=None, min_length=1, max_length=512)
    reason: GapReason | None = None
    physical_serial_verified: Literal[False] = False
    modbus_poll_time_verified: Literal[False] = False

    @model_validator(mode="after")
    def valid_shape(self) -> FixedPanelNativeEvent:
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("callback receive time requires an offset")
        if self.firmware_revision is not None and (
            "\x00" in self.firmware_revision
            or unicodedata.normalize("NFC", self.firmware_revision) != self.firmware_revision
        ):
            raise ValueError("firmware revision must be NFC text without NUL")
        if self.kind == "callback":
            if self.object_id is None or self.value is None or self.reason is not None:
                raise ValueError("callback requires only object and value")
            if not math.isfinite(self.value):
                raise ValueError("callback value must be finite")
            if "_rh_" in self.object_id and not 0 <= self.value <= 100:
                raise ValueError("RH callback outside physical range")
            if "_temp_" in self.object_id and not -80 <= self.value <= 180:
                raise ValueError("temperature callback outside supported range")
        elif self.kind == "gap":
            if self.reason is None or self.object_id is not None or self.value is not None:
                raise ValueError("gap requires only reason")
        elif self.reason is not None or self.object_id is not None or self.value is not None:
            raise ValueError("connection marker has no probe value")
        return self
