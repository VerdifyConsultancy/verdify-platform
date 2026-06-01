"""Planner lessons schemas.

- PlannerLesson: full row shape (planner_lessons table).
- LessonAction: MCP `lessons_manage` tool input envelope (replaces free-form
  `action: str, data: str` pair).
- LessonState: the typed lifecycle a lesson moves through, plus the legal
  transition map and a guard. The state is *derived* from the existing
  `planner_lessons` columns (`is_active`, `superseded_by`, validation history)
  so the state machine needs no DB migration — the four states are a typed view
  over data the table already carries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

LessonConfidence = Literal["low", "medium", "high"]

# ── Lesson lifecycle state machine (G8) ──────────────────────────────────────
#
# A lesson starts `proposed` when Iris first records it, becomes `validated`
# once it accrues an independent validation, can be `superseded` by a newer
# lesson, or `retired` (deactivated) when it no longer applies. The state is
# derived from existing columns rather than stored, so no migration is needed:
#
#   superseded  ← superseded_by IS NOT NULL  (highest precedence; terminal)
#   retired     ← is_active = false          (terminal)
#   validated   ← active and validated at least once beyond creation
#   proposed    ← active, freshly created, not yet independently validated
LessonState = Literal["proposed", "validated", "superseded", "retired"]

# Legal forward transitions. A lesson may always idempotently "transition" to
# its own state (e.g. re-validating a validated lesson). Terminal states
# (superseded, retired) accept no onward transitions.
LESSON_TRANSITIONS: dict[LessonState, frozenset[LessonState]] = {
    "proposed": frozenset({"validated", "superseded", "retired"}),
    "validated": frozenset({"superseded", "retired"}),
    "superseded": frozenset(),
    "retired": frozenset(),
}


def derive_lesson_state(
    *,
    is_active: bool,
    superseded_by: int | None,
    times_validated: int = 1,
    has_independent_validation: bool = False,
) -> LessonState:
    """Compute a lesson's lifecycle state from its persisted columns.

    Precedence matches the SQL the MCP read paths already use
    (`is_active = true AND superseded_by IS NULL` selects the *live* set):

    - `superseded_by` set ⇒ `superseded` (terminal), regardless of `is_active`.
    - else not active ⇒ `retired` (terminal).
    - else active and validated beyond creation ⇒ `validated`.
    - else active and fresh ⇒ `proposed`.

    `has_independent_validation` (e.g. `last_validated > created_at`) takes
    precedence over the `times_validated` heuristic when the caller can supply
    it; otherwise `times_validated > 1` flags a post-creation validation.
    """
    if superseded_by is not None:
        return "superseded"
    if not is_active:
        return "retired"
    if has_independent_validation or times_validated > 1:
        return "validated"
    return "proposed"


def is_legal_lesson_transition(current: LessonState, target: LessonState) -> bool:
    """True if `current → target` is allowed.

    Idempotent self-transitions are legal only for *non-terminal* states (e.g.
    re-validating a `validated` lesson). Terminal states (`superseded`,
    `retired`) are absorbing: you cannot re-supersede or re-retire them, so a
    self-transition into a terminal state is illegal.
    """
    if current == target:
        # legal only if `current` still has at least one forward transition
        # (i.e. it is non-terminal)
        return bool(LESSON_TRANSITIONS.get(current, frozenset()))
    return target in LESSON_TRANSITIONS.get(current, frozenset())


class LessonCreate(BaseModel):
    """MCP lessons_manage.create data payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str = Field(..., min_length=1, max_length=100)
    condition: str = Field(..., min_length=1, max_length=500)
    lesson: str = Field(..., min_length=1, max_length=2000)
    confidence: LessonConfidence = "low"


class LessonUpdate(BaseModel):
    """MCP lessons_manage.update data payload — selective patch."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str | None = Field(default=None, max_length=100)
    condition: str | None = Field(default=None, max_length=500)
    lesson: str | None = Field(default=None, max_length=2000)
    confidence: LessonConfidence | None = None


class LessonValidate(BaseModel):
    """MCP lessons_manage.validate data payload — optional confidence upgrade."""

    model_config = ConfigDict(extra="forbid")

    confidence: LessonConfidence | None = None


class LessonSupersede(BaseModel):
    """MCP lessons_manage.supersede data payload.

    Marks an existing lesson (`lesson_id`) as superseded *by* `new_id`. The new
    lesson must already exist. Mirrors the `planner_lessons.superseded_by`
    self-reference. The old lesson moves proposed/validated → superseded.
    """

    model_config = ConfigDict(extra="forbid")

    new_id: int = Field(..., gt=0)


LessonActionKind = Literal["list", "create", "update", "deactivate", "validate", "supersede"]


class LessonAction(BaseModel):
    """MCP `lessons_manage` tool input envelope."""

    model_config = ConfigDict(extra="forbid")

    action: LessonActionKind
    lesson_id: int | None = None
    data: LessonCreate | LessonUpdate | LessonValidate | LessonSupersede | None = None


class PlannerLesson(BaseModel):
    """planner_lessons table row — full persisted shape."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    created_at: AwareDatetime | None = None
    category: str
    condition: str
    lesson: str
    confidence: LessonConfidence = "low"
    times_validated: int = Field(default=1, ge=0)
    last_validated: AwareDatetime | None = None
    source_plan_ids: list[str] | None = None
    superseded_by: int | None = None
    is_active: bool = True
    greenhouse_id: str = "vallery"
