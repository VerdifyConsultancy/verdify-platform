"""Pydantic schema for the Verdify floating-corridor backlog replan.

This is the "internally consistent data schema for any [planning] properties,
tied back to pydantic, validated on any new changes" required by the
2026-06-22 replan. `planning/backlog.yaml` is the single source of truth for
the lane/wave plan; this module validates it (structure + cross-field
invariants) so the plan cannot drift into an inconsistent state.

Run directly to validate:
    python planning/schema.py planning/backlog.yaml
Or via the test:  pytest planning/tests/test_backlog.py
Or via make:      make planning-validate

Invariants enforced (beyond field types):
  * every work item has non-empty what / why / how / >=1 acceptance check;
  * new_properties non-empty  =>  schema_touch is True (a new control/telemetry
    property MUST be a schema change so it gets a pydantic model + cfg_* readback
    + drift guard — the project's "schema changes land first" + "drift guards are
    the wire protocol" disciplines);
  * no existing issue_ref (a "#NNN") is claimed by two lanes (no duplicate
    ownership — the synth coverage_check resolves these);
  * every depends_on token is a well-formed issue ref ("#NNN"), story id
    ("S..."), wave ("W0".."W3"), or lane id ("L0-..".."L8-..");
  * preflight / effort / wave values are within their enums;
  * every wave referenced by a work item exists in the top-level waves list.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Preflight = Literal[
    "none",
    "firmware-preflight",
    "prod-migration-preflight",
    "prod-sync-preflight",
    "device-tunable-preflight",
    "hardware-preflight",
    "infra-preflight",
]
Effort = Literal["S", "M", "L"]
Wave = Literal["W0", "W1", "W2", "W3"]

_ISSUE_RE = re.compile(r"^#\d+$")
_DEP_RE = re.compile(r"^(#\d+|S[\w-]+|W[0-3]|L[0-8][\w-]*)$")


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_ref: str
    title: str
    what: str
    why: str
    how: str
    acceptance: list[str]
    depends_on: list[str]
    preflight: Preflight
    effort: Effort
    wave: Wave
    area_labels: list[str]
    schema_touch: bool
    new_properties: list[str]

    @field_validator("what", "why", "how", "title")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty (each item explains what/why/how)")
        return v

    @field_validator("acceptance")
    @classmethod
    def _has_acceptance(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("every work item needs >=1 verifiable acceptance check")
        return v

    @field_validator("issue_ref")
    @classmethod
    def _issue_ref_shape(cls, v: str) -> str:
        if v != "NEW" and not _ISSUE_RE.match(v):
            raise ValueError(f"issue_ref must be 'NEW' or '#NNN', got {v!r}")
        return v

    @field_validator("depends_on")
    @classmethod
    def _dep_shape(cls, v: list[str]) -> list[str]:
        for d in v:
            if d in ("none", "") or _DEP_RE.match(d):
                continue
            raise ValueError(f"depends_on token not a valid ref: {d!r}")
        return v

    @model_validator(mode="after")
    def _new_props_imply_schema(self) -> "WorkItem":
        if self.new_properties and not self.schema_touch:
            raise ValueError(
                f"{self.issue_ref} '{self.title}': new_properties "
                f"{self.new_properties} require schema_touch=true"
            )
        return self


class UserStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    as_a: str
    i_want: str
    so_that: str
    work_items: list[WorkItem]


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_id: str
    lane_name: str
    worktree_branch: str
    preflight: Preflight
    milestone: str
    depends_on_lanes: list[str]
    owns_paths: list[str]
    covers_issues: list[str]
    summary: str
    user_stories: list[UserStory]


class WaveDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wave: Wave
    goal: str
    preflight: str
    lanes: list[str]
    exit_criteria: str


class DagEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_: str
    to: str
    reason: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Backlog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: dict
    waves: list[WaveDef]
    dependency_dag: list[dict]
    lanes: list[Lane]

    @model_validator(mode="after")
    def _global_invariants(self) -> "Backlog":
        wave_ids = {w.wave for w in self.waves}
        owner: dict[str, str] = {}
        for lane in self.lanes:
            for story in lane.user_stories:
                for wi in story.work_items:
                    if wi.wave not in wave_ids:
                        raise ValueError(
                            f"{wi.issue_ref} references wave {wi.wave} not in waves[]"
                        )
                    if wi.issue_ref != "NEW":
                        if wi.issue_ref in owner and owner[wi.issue_ref] != lane.lane_id:
                            raise ValueError(
                                f"issue {wi.issue_ref} owned by two lanes: "
                                f"{owner[wi.issue_ref]} and {lane.lane_id}"
                            )
                        owner[wi.issue_ref] = lane.lane_id
        return self


def load(path: str | Path) -> Backlog:
    data = yaml.safe_load(Path(path).read_text())
    return Backlog.model_validate(data)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "planning/backlog.yaml"
    bl = load(path)
    n_items = sum(
        len(s.work_items) for l in bl.lanes for s in l.user_stories
    )
    n_new = sum(
        1
        for l in bl.lanes
        for s in l.user_stories
        for wi in s.work_items
        if wi.issue_ref == "NEW"
    )
    n_cov = len(
        {
            wi.issue_ref
            for l in bl.lanes
            for s in l.user_stories
            for wi in s.work_items
            if wi.issue_ref != "NEW"
        }
    )
    print(
        f"OK: {len(bl.lanes)} lanes, {len(bl.waves)} waves, "
        f"{n_items} work items ({n_new} new, {n_cov} existing issues), "
        f"{len(bl.dependency_dag)} dependency edges — all invariants pass."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
