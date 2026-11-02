"""Problem details shared by the operator API and MCP (OpenAPI ``Problem``, architecture §5.3).

A service raises ``ServiceError``; the REST adapter renders its ``Problem`` as
``application/problem+json`` and the MCP adapter returns the same JSON with ``isError``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ProblemCode(StrEnum):
    """The OpenAPI ``ProblemCode`` enum, verbatim."""

    INVALID_REQUEST = "invalid_request"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    ORIGIN_MISMATCH = "origin_mismatch"
    NOT_FOUND = "not_found"
    WRITES_DISABLED = "writes_disabled"
    QUERY_TIMEOUT = "query_timeout"
    UNAVAILABLE = "unavailable"
    WINDOW_TOO_LARGE = "window_too_large"
    UNKNOWN_TARGET = "unknown_target"
    READ_ONLY = "read_only"
    OUT_OF_RANGE = "out_of_range"
    MANUAL = "manual"
    REQUEST_ID_REUSED = "request_id_reused"
    CHECK_REQUIRED = "check_required"
    TOO_CLOSE = "too_close"
    TOO_MANY_SCHEDULES = "too_many_schedules"
    LINE_DISABLED = "line_disabled"
    LINE_PAUSED = "line_paused"
    LINE_FAULTED = "line_faulted"
    NOT_SCHEDULE_MEMBER = "not_schedule_member"
    LINE_BUSY = "line_busy"
    MANIFOLD_BUSY = "manifold_busy"
    CAP_REACHED = "cap_reached"


_STATUS = {
    ProblemCode.INVALID_REQUEST: 400,
    ProblemCode.UNAUTHENTICATED: 401,
    ProblemCode.FORBIDDEN: 403,
    ProblemCode.ORIGIN_MISMATCH: 403,
    ProblemCode.NOT_FOUND: 404,
    ProblemCode.WRITES_DISABLED: 503,
    ProblemCode.QUERY_TIMEOUT: 503,
    ProblemCode.UNAVAILABLE: 503,
}

_TITLE = {
    ProblemCode.INVALID_REQUEST: "Invalid request",
    ProblemCode.UNAUTHENTICATED: "Not signed in",
    ProblemCode.FORBIDDEN: "Not allowed",
    ProblemCode.ORIGIN_MISMATCH: "Origin mismatch",
    ProblemCode.NOT_FOUND: "Not found",
    ProblemCode.WRITES_DISABLED: "Changes are switched off",
    ProblemCode.QUERY_TIMEOUT: "Query took too long",
    ProblemCode.UNAVAILABLE: "Unavailable",
    ProblemCode.WINDOW_TOO_LARGE: "Window too large",
    ProblemCode.UNKNOWN_TARGET: "Unknown target",
    ProblemCode.READ_ONLY: "Read only",
    ProblemCode.OUT_OF_RANGE: "Out of range",
    ProblemCode.MANUAL: "Manual setting",
    ProblemCode.REQUEST_ID_REUSED: "Request id reused",
    ProblemCode.CHECK_REQUIRED: "Check required",
    ProblemCode.TOO_CLOSE: "Too close",
    ProblemCode.TOO_MANY_SCHEDULES: "Too many schedules",
    ProblemCode.LINE_DISABLED: "Line disabled",
    ProblemCode.LINE_PAUSED: "Line paused",
    ProblemCode.LINE_FAULTED: "Line faulted",
    ProblemCode.NOT_SCHEDULE_MEMBER: "Not a schedule member",
    ProblemCode.LINE_BUSY: "Line busy",
    ProblemCode.MANIFOLD_BUSY: "Manifold busy",
    ProblemCode.CAP_REACHED: "Cap reached",
}

_RETRYABLE = frozenset(
    {ProblemCode.LINE_BUSY, ProblemCode.MANIFOLD_BUSY, ProblemCode.QUERY_TIMEOUT, ProblemCode.UNAVAILABLE}
)


class Problem(BaseModel):
    """RFC 9457 problem details, field for field the OpenAPI ``Problem`` schema."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    code: ProblemCode
    retryable: bool
    field: str | None = None
    min: float | None = None
    max: float | None = None
    current: float | str | bool | None = None
    retry_after_s: int | None = None
    result: Literal["ha_failed", "not_confirmed", "timeout"] | None = None
    request_id: str | None = None


class ServiceError(Exception):
    """A refusal or failure that carries exactly one ``Problem``."""

    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.detail or problem.title)
        self.problem = problem


def problem(
    code: ProblemCode | str,
    detail: str,
    *,
    field: str | None = None,
    min: float | None = None,  # noqa: A002 - the OpenAPI field name
    max: float | None = None,  # noqa: A002 - the OpenAPI field name
    current: float | str | bool | None = None,
    retry_after_s: int | None = None,
    result: Literal["ha_failed", "not_confirmed", "timeout"] | None = None,
    retryable: bool | None = None,
) -> ServiceError:
    """Build the ``ServiceError`` for ``code``: its words as the title and its HTTP status."""
    code = ProblemCode(code)
    return ServiceError(
        Problem(
            title=_TITLE[code],
            status=_STATUS.get(code, 422),
            detail=detail,
            code=code,
            retryable=code in _RETRYABLE if retryable is None else retryable,
            field=field,
            min=min,
            max=max,
            current=current,
            retry_after_s=retry_after_s,
            result=result,
        )
    )
