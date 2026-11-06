"""greenhouse_brief (migration 301, #1346): one row per greenhouse-snapshot attempt.

CronJob verdify-greenhouse-brief validates every insert through ``GreenhouseBriefRow``; its checks mirror the
table's CHECK constraints, so a row the database would refuse never reaches it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

BriefStatus = Literal["ok", "rejected", "error"]


class GreenhouseBriefRow(BaseModel):
    """The written columns of ``public.greenhouse_brief`` (``id`` is an identity, ``checked_at`` and
    ``outdated_at`` are set by later runs)."""

    model_config = ConfigDict(extra="forbid")

    generated_at: AwareDatetime
    valid_until: AwareDatetime
    model: str = Field(min_length=1, max_length=200)
    route: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=50)
    facts: dict[str, Any]
    facts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    latency_ms: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    status: BriefStatus
    error: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _outcome(self) -> GreenhouseBriefRow:
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be after generated_at")
        if self.status == "ok" and (self.text is None or self.error is not None):
            raise ValueError("an ok row has text and no error")
        if self.status != "ok" and self.error is None:
            raise ValueError("a rejected or error row names its error")
        return self
