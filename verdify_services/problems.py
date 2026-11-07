"""Refusals in one shape: the OpenAPI ``Problem`` (RFC 9457) and its ``ProblemCode`` list (architecture §5.3).

A service raises ``ServiceError(problem(code, detail, ...))``. REST adapters render ``problem.body()`` as
``application/problem+json`` with ``problem.status``; MCP adapters return the same JSON with ``isError``, so
people and agents read the same code, bounds and ``retryable`` (US-AGT-04).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The OpenAPI ProblemCode enum, verbatim and in its order.
ProblemCode = Literal[
    "invalid_request",
    "unauthenticated",
    "forbidden",
    "origin_mismatch",
    "not_found",
    "writes_disabled",
    "query_timeout",
    "unavailable",
    "window_too_large",
    "unknown_target",
    "read_only",
    "out_of_range",
    "manual",
    "request_id_reused",
    "check_required",
    "too_close",
    "too_many_schedules",
    "line_disabled",
    "line_paused",
    "line_faulted",
    "not_schedule_member",
    "line_busy",
    "manifold_busy",
    "cap_reached",
    "position_occupied",
    "ambiguous_position",
    "planting_ended",
    "study_state",
]
PROBLEM_CODES: tuple[str, ...] = get_args(ProblemCode)

# API-20 only, with unavailable: Home Assistant failed the call, Lutron did not reach the state, or no answer in 12 s.
LightResult = Literal["ha_failed", "not_confirmed", "timeout"]

# Every validation and refusal code is 422; these are the others.
_NOT_422 = {
    "invalid_request": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "origin_mismatch": 403,
    "not_found": 404,
    "writes_disabled": 503,
    "query_timeout": 503,
    "unavailable": 503,
}
STATUS: Mapping[str, int] = MappingProxyType({code: _NOT_422.get(code, 422) for code in PROBLEM_CODES})
# Waiting can help only here. A caller sets retryable false where it cannot (the pre-engine unavailable).
RETRYABLE_CODES = frozenset({"line_busy", "manifold_busy", "query_timeout", "unavailable"})
# The code in words, e.g. "Out of range" (the same words as verdify_schemas.irrigation's refusals).
TITLES: Mapping[str, str] = MappingProxyType({code: code.replace("_", " ").capitalize() for code in PROBLEM_CODES})


class Problem(BaseModel):
    """The OpenAPI ``Problem`` schema, field for field. ``status`` follows the code and ``retryable`` is
    true only for ``line_busy``, ``manifold_busy``, ``query_timeout`` and ``unavailable``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: ProblemCode
    retryable: bool
    field: str | None = None
    min: int | float | None = None
    max: int | float | None = None
    current: bool | int | float | str | None = None
    retry_after_s: int | None = Field(default=None, ge=0)
    result: LightResult | None = None
    request_id: str | None = None

    @model_validator(mode="after")
    def _follows_its_code(self) -> Problem:
        if self.status != STATUS[self.code]:
            raise ValueError(f"{self.code} is status {STATUS[self.code]}, not {self.status}")
        if self.retryable and self.code not in RETRYABLE_CODES:
            raise ValueError(f"{self.code} is never retryable")
        return self

    def body(self) -> dict[str, Any]:
        """The JSON both surfaces return: problem+json on REST, the ``isError`` content on MCP."""
        return self.model_dump(mode="json", exclude_none=True)


class ServiceError(Exception):
    """A refusal: carries one ``Problem``. Nothing was stored or sent."""

    def __init__(self, problem: Problem) -> None:
        super().__init__(f"{problem.code}: {problem.detail or problem.title}")
        self.problem = problem


def problem(
    code: ProblemCode,
    detail: str,
    *,
    field: str | None = None,
    min: int | float | None = None,
    max: int | float | None = None,
    current: bool | int | float | str | None = None,
    retry_after_s: int | None = None,
    result: LightResult | None = None,
    retryable: bool | None = None,
) -> Problem:
    """Build the ``Problem`` for ``code``: its title in words, its status and its default ``retryable``."""
    return Problem(  # an unknown code fails the model's ProblemCode check
        title=TITLES.get(code, code),
        status=STATUS.get(code, 0),
        detail=detail,
        code=code,
        retryable=code in RETRYABLE_CODES if retryable is None else retryable,
        field=field,
        min=min,
        max=max,
        current=current,
        retry_after_s=retry_after_s,
        result=result,
    )
